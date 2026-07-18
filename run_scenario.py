"""
Run a SUMO scenario (baseline or road closure) and export flows for the web app.

Run after build_sumo_demand.py:
  python3 run_scenario.py                                    # baseline
  python3 run_scenario.py --close 60786979_3575001205_0      # whole-run closure
  python3 run_scenario.py --closure '{"edge_id":"60786979_3575001205_0","begin":"2025-09-16T08:00:00","end":"2025-09-16T10:00:00"}'

Method:
  - Simulates the calibrated demand (sumo/calibrated.rou.xml) N times with
    different random seeds (Monte Carlo).
  - --close <edgeId> adds a whole-run rerouter closure. --closure JSON adds
    one time-windowed closure without parallel CLI argument lists;
    SUMO reroutes vehicles around it by construction.
  - 15-min per-edge flows ("entered" vehicle counts) are averaged over seeds
    and written in the flows.json format with the SAME edge IDs as the map.

Per-edge confidence (0-1), written into the scenario file:
    confidence = spatial_prior × exp(-CV)
  where spatial_prior comes from network.geojson (distance to nearest sensor)
  and CV is the mean coefficient of variation across Monte Carlo seeds.
  Far from sensors AND unstable across seeds → low confidence.

Writes:
  web/data/scenarios/<name>.json   — flows + confidence for the web app
  web/data/scenarios/index.json    — manifest the web app lists scenarios from
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import heapq
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from traffic_sim.simulation import metrics as cm
from traffic_sim.simulation.sensor_fit import (assess_output_fit,
                                               summarize_pairs,
                                               summarize_rows)
from traffic_sim.simulation.runtime import sumo_home
from traffic_sim.simulation.metadata import load_metadata
from traffic_sim.core.fingerprint import sha256_file
from traffic_sim.core.contracts import ScenarioSpec, load_scenario_spec
from traffic_sim.simulation.multiday import parse_summary
from traffic_sim.simulation.trajectory_contract import (
    MULTIDAY_MAX_VEHICLES_PER_DAY,
    SAMPLING_METHOD as TRAJECTORY_SAMPLING_METHOD,
)
from traffic_sim.simulation.warm_state_cache import (
    load_state_arguments,
    save_state_arguments,
)

SUMO_DIR  = Path("sumo")
NET_PATH  = SUMO_DIR / "net.net.xml"
GEO_PATH  = Path("web/data/network.geojson")
OUT_DIR   = Path("web/data/scenarios")
FLOWS_PATH = Path("web/data/flows.json")
FLOWS_FORECAST_PATH = Path("web/data/flows_forecast.json")

# _tracked_main assigns the immutable run directory before main() starts.
# Direct library callers get a disposable fallback workspace.  New scenario
# intermediates never write to the shared sumo/ directory.
_ACTIVE_RUN_DIR: Path | None = None
_LAST_SCRATCH_DIR: Path | None = None
_LAST_SCRATCH_KEPT = False
_LAST_SCENARIO_OUTPUT: Path | None = None
_LAST_TRAJECTORY_OUTPUT: Path | None = None

# A whole-day meso run normally takes ~10-15 s (measured: 3-seed whole-day
# closure ~35 s total). This is a safety net, not a normal-path limit: without
# it, a hung sumo process has no bound and — if this script's own PARENT
# process (e.g. serve.py's outer subprocess.run) times out and kills THIS
# process first — becomes permanently orphaned, since a timeout can only ever
# kill its own direct child. Found in review 2026-07-07.
SUMO_TIMEOUT_S = 300


def demand_signature(meta: dict) -> str:
    """Stable fingerprint for the demand that produced a scenario.

    Scenario files are only meaningful for the calibrated demand currently
    represented by sumo/demand_meta.json. If someone runs build_sumo_demand.py
    manually, old closure scenarios from the previous date/window otherwise
    remain in index.json and look selectable even though they were generated
    from different routes. The signature keeps the manifest scoped to one
    demand build.
    """
    # New demand builds carry a content-addressed build ID.  It supersedes
    # the legacy date/window signature and invalidates scenarios when routes,
    # network, priors, code, or SUMO changes while the calendar window stays
    # the same.  Fixtures and older artifacts keep the backwards-compatible
    # metadata-only signature below.
    if meta.get("build_id"):
        return str(meta["build_id"])
    keys = ("date", "source", "begin", "end", "n_intervals", "epoch_sim", "n_variants")
    payload = {k: meta.get(k) for k in keys}
    # B1 metadata is additive for one-day demand. Preserve that established
    # signature exactly so current scenarios remain valid; multi-day demand
    # is distinguishable by its explicit range contract.
    if meta.get("days", 1) > 1:
        for key in ("start_date", "days", "end_date_exclusive",
                    "day_boundaries_s", "day_kinds"):
            payload[key] = meta.get(key)
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]


def variant_path(variants: list[Path], variant: str) -> Path:
    """Resolve a contract variant to the demand route artifact."""
    index = {"q50": 0, "edge_shares": 0, "q10": 1, "q90": 2}.get(variant)
    if index is None or index >= len(variants):
        raise ValueError(
            f"demand variant {variant!r} is unavailable in this build "
            f"({len(variants)} route variant(s))")
    return variants[index]


def seed_variant_plan(variants: list[Path], *, spec: ScenarioSpec | None = None,
                      seeds: int = 3) -> list[tuple[int, Path]]:
    """Return the exact seed-to-demand-variant plan for a study.

    Signal studies used to derive ``1000 + index`` and cycle through the
    available route files locally. That was harmless for the default three
    seeds, but it silently ignored a ScenarioSpec's explicit seed/variant
    mapping. Keep the legacy plan for callers without a spec and centralise
    the structured path so every study uses the same assignment contract.
    """
    if spec is not None:
        return [(seed, variant_path(variants, variant))
                for seed, variant in spec.demand_variant_mapping]
    if isinstance(seeds, bool) or not isinstance(seeds, int) or seeds < 1:
        raise ValueError("seeds must be a positive integer")
    if not variants:
        raise ValueError("at least one demand variant is required")
    return [(1000 + index, variants[index % len(variants)])
            for index in range(seeds)]


def validate_scenario_spec(spec: ScenarioSpec, *, meta: dict,
                           duration_s: int, network_path: Path) -> None:
    """Reject a spec that does not describe the loaded demand/network."""
    demand_id = str(meta.get("build_id") or demand_signature(meta))
    if spec.demand_build_id != demand_id:
        raise ValueError(
            f"ScenarioSpec demand_build_id {spec.demand_build_id!r} does not "
            f"match loaded demand build {demand_id!r}")
    network_id = sha256_file(network_path)
    if network_id is None or spec.network_build_id != network_id:
        raise ValueError(
            f"ScenarioSpec network_build_id does not match {network_path}")
    expected_start = pd.Timestamp(meta["epoch_sim"])
    expected_end = expected_start + pd.Timedelta(seconds=duration_s)
    if pd.Timestamp(spec.start_time) != expected_start or \
            pd.Timestamp(spec.end_time) != expected_end:
        raise ValueError(
            "ScenarioSpec start/end must exactly match the loaded demand window")
    if spec.simulation_mode not in {"meso", "micro"}:
        raise ValueError(f"unsupported ScenarioSpec simulation_mode: {spec.simulation_mode}")
    for closure in spec.closures:
        if closure.closure_type != "full":
            raise ValueError(
                "run_scenario currently supports only full closures from ScenarioSpec")
        if closure.permitted_access_exceptions:
            raise ValueError(
                "permitted access exceptions require a network-permission audit "
                "before they can be simulated")


def validate_variant_coverage(meta: dict, mapping: dict[int, str], *,
                              require_all: bool) -> None:
    """Require q50/q10/q90 participation when a release has three variants."""
    if not require_all or int(meta.get("n_variants", 1)) < 3:
        return
    present = {target_key_for_variant(str(variant)) for variant in mapping.values()}
    required = {"edge_shares", "edge_shares_q10", "edge_shares_q90"}
    missing = sorted(required - present)
    if missing:
        raise ValueError(
            "three-variant demand release must run q50, q10 and q90; "
            f"missing {', '.join(missing)}")


def want_trajectories(args: argparse.Namespace, n_intervals: int) -> bool:
    """Real vehicle playback is part of every scenario by default.

    Multi-day output is bounded later by a deterministic per-day sample;
    ``--no-trajectories`` remains an explicit batch/diagnostic escape hatch.
    """
    if args.no_trajectories:
        return False
    return True


def trajectory_sample_cap(n_intervals: int) -> int | None:
    """Return the per-day browser cap, or ``None`` for a full one-day run."""
    return (MULTIDAY_MAX_VEHICLES_PER_DAY
            if n_intervals > 96 else None)


def closure_integrity_status(active_closure_entries: int | None,
                             closures: list[dict]) -> str | None:
    """Classify the active-closure-edge-throughput measurement honestly.

    active_closure_entries is None when nothing was measurable, 0 when
    every seed measured zero leaked vehicles (a genuine, successful, fully-
    measured clean result), or a positive int when a leak was detected.
    Truthiness alone (`if active_closure_entries else ...`) would treat 0
    identically to None — a verified-clean closure reported the same way
    as "we never checked" — the exact "missing != zero" mistake
    ARCHITECTURE.md calls out explicitly. Found in review 2026-07-12.

    Uses closure_metrics.closure_edge_leaked() for the actual "is this a
    leak" threshold rather than reimplementing it — the same underlying
    measurement closure_metrics.disqualification_reasons() judges for the
    ranking tools (suggest_closure_time.py, signal_optimize.py,
    signal_closure_combine.py), so a future tolerance change can never
    silently apply to only one of the two."""
    if cm.closure_edge_leaked(active_closure_entries):
        return "failed_active_edge_flow"
    if active_closure_entries == 0:
        return "verified_clean"
    if closures:
        return "not_measurable"
    return None


def aggregate_active_closure_entries(entries_by_seed: list[int | None],
                                     closures: list[dict]) -> int | None:
    """Return a closure-throughput total only when every seed was measured.

    Summing the non-``None`` values would turn ``[0, None, 0]`` into a
    supposedly verified zero-flow result. The Monte Carlo claim requires
    evidence for every seed: one missing edgeData series makes the closure
    unmeasured, regardless of the remaining seeds' clean values.
    """
    if not closures:
        return None
    if not entries_by_seed or any(value is None for value in entries_by_seed):
        return None
    return sum(int(value) for value in entries_by_seed)


def baseline_output_fit_errors(meta: dict, audit: dict, *, n_intervals: int,
                               closures: list[dict]) -> list[str]:
    """Return final-output gate errors for a newly calibrated baseline.

    A road closure is intentionally expected to change sensor flow, so its
    audit remains explanatory rather than a calibration gate. A baseline
    backed by frozen demand targets, however, must prove raw SUMO edgeData
    fit before *any* publication path can write it. ``serve`` repeats this
    check when it swaps staged files into the live directory, protecting
    against stale/mixed artifacts.
    """
    if closures or not (meta.get("sensor_targets") or
                        meta.get("sensor_output_fit_required")):
        return []
    return assess_output_fit(
        audit, n_intervals=n_intervals,
        days=int(meta.get("days", 1) or 1))["errors"]


def demand_window_label(meta: dict) -> str:
    """Human-readable date range for scenario/index display.

    demand_metadata() (build_sumo_demand.py) only populates date/begin/end
    for single-day demand; multi-day demand carries start_date/
    end_date_exclusive/days instead. Reading meta['date'] unconditionally
    crashes with KeyError on multi-day demand.
    """
    if "date" in meta:
        return f"{meta['date']} {meta['begin']}–{meta['end']}"
    return (f"{meta['start_date']} → {meta['end_date_exclusive']} "
            f"({meta['days']} days)")


def index_for_current_demand(index: dict, signature: str) -> dict:
    """Drop manifest entries generated from another demand calibration."""
    scenarios = [
        s for s in index.get("scenarios", [])
        if s.get("demand_signature") == signature
    ]
    return {"demand_signature": signature, "scenarios": scenarios}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--close", nargs="*", default=[], metavar="EDGE_ID",
                   help="Edge(s) to close (must exist in network.geojson). "
                        "Omit for baseline.")
    p.add_argument("--closure", action="append", default=[], metavar="JSON",
                   help='Time-windowed closure, repeatable: '
                        "'{\"edge_id\":\"EDGE\",\"begin\":\"ISO\",\"end\":\"ISO\"}'. "
                        "--close remains the backwards-compatible whole-run form.")
    p.add_argument("--name",  default=None,
                   help="Scenario name (default: 'baseline' or 'close_<edge…>')")
    p.add_argument("--scenario-spec", default=None, metavar="PATH",
                   help="Load and validate a versioned ScenarioSpec JSON. "
                        "Legacy CLI flags remain supported when omitted.")
    p.add_argument("--seeds", type=int, default=3,
                   help="Monte Carlo runs (default 3)")
    p.add_argument("--seed-workers", type=int, default=1,
                   help="Concurrent independent SUMO seed processes "
                        "(default 1; benchmark before increasing).")
    p.add_argument("--micro", action="store_true",
                   help="Use microscopic simulation (default: mesoscopic — "
                        "~20x faster, adequate for 15-min edge flows)")
    p.add_argument("--no-trajectories", action="store_true",
                   help="Skip real-vehicle playback for a diagnostic/batch "
                        "run. Published UI baselines normally keep it.")
    p.add_argument("--trajectories", action="store_true",
                   help="Backward-compatible explicit request for trajectory "
                        "export. Export is already on by default; multi-day "
                        "output is deterministically capped per day.")
    p.add_argument("--out-dir", default=None, metavar="DIR",
                   help="Write scenario JSON + trajectories + index to DIR "
                        "instead of web/data/scenarios — the STAGING half "
                        "of serve.py's publish-after-validate recalibration "
                        "(IMPROVEMENT_PLAN.md E2): build the complete new set aside, "
                        "validate it, then atomically switch. Default: the "
                        "live directory, unchanged behaviour.")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Keep this run's isolated SUMO workspace for diagnosis "
                        "instead of deleting it after successful publication.")
    args = p.parse_args()
    if args.seeds < 1:
        p.error("--seeds must be >= 1")
    if args.seed_workers < 1:
        p.error("--seed-workers must be >= 1")
    if args.trajectories and args.no_trajectories:
        p.error("--trajectories and --no-trajectories are mutually exclusive")
    return args


def structured_closures(raw: list[str], whole_edges: list[str], epoch: str,
                        duration_s: int) -> list[dict]:
    """Parse CLI closures into the single internal time-window contract.

    The legacy --close form is deliberately represented as the old 0 through
    duration+flush interval, so all existing whole-day behaviour is retained.
    """
    if raw and whole_edges:
        raise ValueError("use either legacy --close or structured --closure, not both")
    closures = [
        {"edge_id": edge, "begin_s": 0, "end_s": duration_s + 3600}
        for edge in whole_edges
    ]
    epoch_ts = pd.Timestamp(epoch)
    for value in raw:
        try:
            item = json.loads(value)
            edge = item["edge_id"]
            begin = pd.Timestamp(item["begin"])
            end = pd.Timestamp(item["end"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("--closure must be JSON with edge_id, begin and end ISO values") from exc
        if begin.tzinfo is not None:
            begin = begin.tz_convert(None)
        if end.tzinfo is not None:
            end = end.tz_convert(None)
        begin_s = (begin - epoch_ts).total_seconds()
        end_s = (end - epoch_ts).total_seconds()
        # User-authored windows belong to the typed ScenarioSpec and must fit
        # its demand window. The extra simulation drain hour is an internal
        # legacy whole-run detail, not valid scenario-contract time.
        if not (0 <= begin_s < end_s <= duration_s):
            raise ValueError("--closure window must be within the simulated run and have begin < end")
        closures.append({"edge_id": edge, "begin_s": int(begin_s), "end_s": int(end_s)})
    return closures


def contract_closures(closures: list[dict], epoch: str,
                      duration_s: int) -> list[dict]:
    """Project internal second offsets into validated closure datetimes.

    Legacy ``--close`` keeps its historical rerouter active through the
    one-hour SUMO drain (``duration_s + 3600``). ScenarioSpec deliberately
    describes the demand/scenario window only, so its equivalent whole-run
    closure ends at ``duration_s``. Keeping this conversion at the CLI seam
    prevents internal ``begin_s``/``end_s`` dictionaries from being passed
    directly to ClosureSpec, which expects ISO datetimes.
    """
    epoch_ts = pd.Timestamp(epoch)
    result = []
    for closure in closures:
        begin_s = max(0, min(int(closure["begin_s"]), duration_s))
        end_s = max(0, min(int(closure["end_s"]), duration_s))
        if begin_s >= end_s:
            raise ValueError("closure window must overlap the scenario window")
        result.append({
            "edge_id": closure["edge_id"],
            "start_time": (epoch_ts + pd.Timedelta(seconds=begin_s)).isoformat(),
            "end_time": (epoch_ts + pd.Timedelta(seconds=end_s)).isoformat(),
        })
    return result


def edge_freeflow_times() -> dict[str, float]:
    """Free-flow seconds per net edge for the conservative window prefilter."""
    metadata = load_metadata(NET_PATH, SUMO_DIR / "network_metadata.json")
    if metadata is not None:
        return {edge_id: item["length_m"] / item["speed_m_s"]
                for edge_id, item in metadata["edges"].items()
                if item.get("length_m", 0) > 0 and item.get("speed_m_s", 0) > 0}
    times = {}
    for edge in ET.parse(NET_PATH).getroot().findall("edge"):
        # SUMO stores these on the first lane in normal net.net.xml files;
        # accept edge-level attributes too for small synthetic fixtures.
        lane = edge.find("lane")
        length = float(edge.get("length") or (lane.get("length") if lane is not None else 0) or 0)
        speed = float(edge.get("speed") or (lane.get("speed") if lane is not None else 0) or 0)
        if length > 0 and speed > 0:
            times[edge.get("id")] = length / speed
    return times


def valid_scenario_name(name: str) -> bool:
    """The scenario name is interpolated into route/additional/JSON/
    trajectory filenames under sumo/ and web/data/scenarios/. A strict slug
    keeps every generated path inside those directories (no separators, no
    "..") and SUMO-referenceable (no spaces). Auto-generated names already
    satisfy this by construction — they're built from validated edge IDs —
    so this only ever gates a hand-typed --name (IMPROVEMENT_REVIEW 13.7,
    narrow scope)."""
    return re.fullmatch(r"[A-Za-z0-9_+-]{1,80}", name) is not None


def atomic_write_json(path: Path, obj, **dump_kwargs) -> None:
    """Write JSON so a live browser polling this path never observes a
    partial file. serve.py's re-run/'Byt dag' flow overwrites these files
    in place while the web app may be mid-fetch (cache-busted, no-store) —
    a plain open(path, "w") is visible (and truncated) the instant it opens,
    so a request landing mid-write gets a truncated/invalid JSON parse
    error instead of either the old or the new scenario (found in review
    2026-07-10). Write to a same-directory temp file, then os.replace(),
    which POSIX guarantees is atomic — readers see either the fully old or
    fully new file, never a partial one."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                                    suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, **dump_kwargs)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def load_geojson_meta() -> tuple[dict[str, float], dict[str, str]]:
    """{edge_id: spatial confidence prior}, {edge_id: street name}."""
    with open(GEO_PATH) as f:
        geo = json.load(f)
    prior: dict[str, float] = {}
    names: dict[str, str] = {}
    for feat in geo["features"]:
        p = feat["properties"]
        # `or 0.5` was WRONG here: 0 is a valid — and on the inner-city
        # network the most common (6 569/7 147 edges) — confidence value,
        # meaning "far from every sensor, extrapolation only". Truthiness
        # coerced all of them to 0.5, so scenario exports displayed a flat
        # 50% confidence on exactly the edges the spatial prior most
        # deliberately marks unvalidated (IMPROVEMENT_REVIEW 13.1,
        # verified against the real network file 2026-07-10). The 0.5
        # fallback remains only for a genuinely absent property.
        conf = p.get("confidence")
        prior[p["id"]] = 0.5 if conf is None else float(conf)
        names[p["id"]] = p.get("name") or p["id"]
    return prior, names


def sensor_audit_edges() -> list[dict]:
    """Return the directed sensor edges that the UI must make auditable.

    A physical counter can contain one or two directed map edges.  Keep those
    edges separate: collapsing them back into a station total would hide the
    direction split the demand build explicitly models.
    """
    with open(GEO_PATH) as f:
        geo = json.load(f)
    out = []
    for feature in geo["features"]:
        props = feature.get("properties", {})
        if not props.get("sensor_id") or feature.get("geometry", {}).get("type") != "LineString":
            continue
        coords = feature["geometry"].get("coordinates", [])
        out.append({
            "sensor_id": str(props["sensor_id"]),
            "edge_id": props["id"],
            "name": props.get("name") or props["id"],
            "direction": compass_direction(coords),
            # A two-way Total is a physical-station count, not a directional
            # observation.  The browser needs this fact beside the number.
            "measurement": ("two_way_total" if props.get("level") == "Total"
                            else "directed"),
        })
    return sorted(out, key=lambda item: (item["sensor_id"], item["edge_id"]))


def compass_direction(coords: list[list[float]]) -> str | None:
    """Return the approximate compass direction of a WGS84 edge's travel.

    It is deliberately derived from the directed geometry rather than a
    station label.  New sensors therefore get a direction without a UI
    registry change, and the label means exactly the direction the simulated
    vehicle travels along that edge.
    """
    if len(coords) < 2:
        return None
    lon0, lat0 = coords[0]
    lon1, lat1 = coords[-1]
    dx = (lon1 - lon0) * math.cos(math.radians((lat0 + lat1) / 2))
    dy = lat1 - lat0
    if math.isclose(dx, 0.0, abs_tol=1e-12) and math.isclose(dy, 0.0, abs_tol=1e-12):
        return None
    bearing = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
    labels = ("N", "NO", "O", "SO", "S", "SV", "V", "NV")
    return labels[int((bearing + 22.5) // 45) % len(labels)]


def target_key_for_route_path(route_path: Path) -> str | None:
    """Map a calibrated route artifact to its persisted target variant.

    Closure runs copy routes to a private filename before SUMO starts.  The
    old exact-name lookup therefore lost q10/q90 provenance and compared all
    closure seeds against q50.  Keep this filename fallback for legacy callers,
    but prefer the explicit semantic variant carried by each seed result.
    """
    name = Path(route_path).name
    if name == "calibrated.rou.xml" or name.startswith("calibrated_close_"):
        return "edge_shares"
    if name == "calibrated_v1.rou.xml" or name.startswith("calibrated_v1_close_"):
        return "edge_shares_q10"
    if name == "calibrated_v2.rou.xml" or name.startswith("calibrated_v2_close_"):
        return "edge_shares_q90"
    return None


def target_key_for_variant(variant: str | None) -> str | None:
    """Translate the stable q50/q10/q90 contract to metadata keys."""
    return {
        "q50": "edge_shares",
        "edge_shares": "edge_shares",
        "q10": "edge_shares_q10",
        "edge_shares_q10": "edge_shares_q10",
        "q90": "edge_shares_q90",
        "edge_shares_q90": "edge_shares_q90",
    }.get(variant)


def sensor_audit_inputs(
    meta: dict,
) -> tuple[dict[str, dict[str, list]], dict[str, list], dict[str, str]]:
    """Load frozen demand audit inputs, with explicit legacy reconstruction.

    New demand metadata persists the exact arrays used by PFE.  Old demand
    builds lack that compact audit record; reconstructing from their current
    immutable source inputs keeps existing scenarios inspectable, but marks
    the provenance so it cannot silently masquerade as frozen build evidence.
    """
    stored = meta.get("sensor_targets")
    variants = stored.get("variants") if isinstance(stored, dict) else None
    observations = meta.get("sensor_observations")
    have_targets = isinstance(variants, dict) and bool(variants)
    have_observations = isinstance(observations, dict) and bool(observations)
    if have_targets and have_observations:
        return variants, observations, {
            "targets": "demand_metadata",
            "observations": "demand_metadata",
        }

    source_path = FLOWS_FORECAST_PATH if meta.get("source") == "forecast" else FLOWS_PATH
    try:
        from demand.intake import (build_targets, load_sensor_edges,
                                   observed_sensor_series, target_series)

        with open(source_path) as f:
            flows = json.load(f)["flows"]
        sensor_edges = load_sensor_edges()
        keys = ["edge_shares"]
        if int(meta.get("n_variants", 1)) >= 3:
            keys += ["edge_shares_q10", "edge_shares_q90"]
        rebuilt_targets = {
            key: target_series(build_targets(
                flows, sensor_edges, int(meta["qi_start"]),
                int(meta["n_intervals"]), split_key=key))
            for key in keys
        }
        rebuilt_observations = observed_sensor_series(
            flows, sensor_edges, int(meta["qi_start"]), int(meta["n_intervals"]))
        return (variants if have_targets else rebuilt_targets,
                observations if have_observations else rebuilt_observations,
                {
                    "targets": ("demand_metadata" if have_targets
                                else "reconstructed_current_inputs"),
                    "observations": ("demand_metadata" if have_observations
                                     else "reconstructed_current_inputs"),
                })
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"  WARNING sensor audit targets unavailable: {exc}")
        return (variants if have_targets else {},
                observations if have_observations else {},
                {
                    "targets": "demand_metadata" if have_targets else "unavailable",
                    "observations": ("demand_metadata" if have_observations
                                     else "unavailable"),
                })


def _series_value(series: dict[str, list], edge_id: str, qi: int) -> float | None:
    values = series.get(edge_id)
    if not isinstance(values, list) or qi >= len(values):
        return None
    value = values[qi]
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _series_or_missing(series: dict[str, list], edge_id: str,
                       n_intervals: int) -> list[float | None]:
    """Return a fixed-width source series without turning missing into zero."""
    return [_series_value(series, edge_id, qi) for qi in range(n_intervals)]


def _flow_series(flows: dict[str, np.ndarray], edge_id: str,
                 n_intervals: int) -> list[int]:
    values = flows.get(edge_id)
    if values is None:
        return [0] * n_intervals
    return [int(round(float(values[qi]))) if qi < len(values) else 0
            for qi in range(n_intervals)]


def _raw_mean_series(results: list[dict], edge_id: str,
                     n_intervals: int) -> list[float]:
    """Return the unrounded ensemble mean used for fit calculations."""
    if not results:
        return [0.0] * n_intervals
    values = []
    for qi in range(n_intervals):
        total = 0.0
        for result in results:
            series = result["flows"].get(edge_id)
            if series is not None and qi < len(series):
                total += float(series[qi])
        values.append(total / len(results))
    return values


def build_sensor_audit(meta: dict, results: list[dict],
                       flows_out: dict[str, list[int]], n_intervals: int,
                       *, calibration_comparison: bool,
                       raw_mean_flows: dict[str, list[float]] | None = None,
                       representative_seed: int | None = None) -> dict:
    """Build a compact, per-direction comparison for the scenario UI.

    ``flows_out`` is the displayed Monte Carlo mean, while the vehicle
    animation is only one representative seed — ``representative_seed`` when
    given (the trajectory seed), else the first result.  Publishing both
    values from the same SUMO edgeData files prevents the UI from inviting a
    false visual comparison between unlike quantities.
    """
    target_variants, observations, provenance = sensor_audit_inputs(meta)
    representative = results[0] if results else None
    if representative_seed is not None:
        representative = next(
            (result for result in results
             if result.get("seed") == representative_seed), representative)
    representative_key = (representative.get("target_key")
                          if representative else None)
    representative_key = (representative_key or
                          (target_key_for_route_path(representative["route_path"])
                           if representative else None))
    directions = []
    ensemble_pairs: list[tuple[float, float]] = []
    representative_pairs: list[tuple[float, float]] = []
    station_rows: dict[str, dict] = {}

    for info in sensor_audit_edges():
        edge_id = info["edge_id"]
        mean_targets: list[float | None] = []
        for qi in range(n_intervals):
            values = []
            for result in results:
                key = result.get("target_key") or target_key_for_route_path(result["route_path"])
                series = target_variants.get(key or "", {})
                value = _series_value(series, edge_id, qi)
                if value is not None:
                    values.append(value)
            mean_targets.append(sum(values) / len(values) if values else None)

        representative_targets = [
            _series_value(target_variants.get(representative_key or "", {}), edge_id, qi)
            for qi in range(n_intervals)
        ]
        representative_counts = (_flow_series(representative["flows"], edge_id, n_intervals)
                                 if representative else [0] * n_intervals)
        mean_counts = _flow_series(flows_out, edge_id, n_intervals)
        source_values = _series_or_missing(observations, edge_id, n_intervals)
        raw_counts = ((raw_mean_flows or {}).get(edge_id)
                      or [float(value) for value in mean_counts])
        for qi, target in enumerate(mean_targets):
            if target is not None:
                ensemble_pairs.append((raw_counts[qi], target))
        for qi, target in enumerate(representative_targets):
            if target is not None:
                representative_pairs.append((representative_counts[qi], target))
        directions.append({
            **info,
            "source_value": source_values,
            "target_mean": mean_targets,
            "target_representative": representative_targets,
            "simulated_mean": mean_counts,
            "simulated_mean_raw": raw_counts,
            "simulated_representative": representative_counts,
        })
        station = station_rows.setdefault(info["sensor_id"], {
            "sensor_id": info["sensor_id"],
            "measurement": info["measurement"],
            "edge_ids": [],
            "target_mean": [0.0] * n_intervals,
            "target_seen": [False] * n_intervals,
            "simulated_mean_raw": [0.0] * n_intervals,
        })
        station["edge_ids"].append(edge_id)
        for qi in range(n_intervals):
            if mean_targets[qi] is not None:
                station["target_mean"][qi] += mean_targets[qi]
                station["target_seen"][qi] = True
            station["simulated_mean_raw"][qi] += raw_counts[qi]

    # Two-way counters are one physical observation.  The directed rows are
    # retained above for transparency, but the station-level fit sums them
    # before comparison so a repeated total is never treated as two readings.
    stations_public = []
    for station in station_rows.values():
        station["target_mean"] = [round(value, 6) if seen else None
                                   for value, seen in zip(
                                       station["target_mean"],
                                       station["target_seen"])]
        station["simulated_mean_raw"] = [round(value, 6)
                                          for value in station["simulated_mean_raw"]]
        stations_public.append({key: value for key, value in station.items()
                                if key != "target_seen"})
    fit = {
        "ensemble": summarize_pairs(ensemble_pairs),
        "representative": summarize_pairs(representative_pairs),
    }
    daily_output_fit = []
    days = int(meta.get("days", 1) or 1)
    # PFE's documented GEH calibration metric is hourly. Keep raw 15-minute
    # arrays in the audit, but compare their aligned four-quarter sums so
    # ordinary travel time across a quarter boundary is not misclassified as
    # lost demand. Tiny synthetic/unit windows retain the legacy per-quarter
    # summary; production full-day windows are always divisible by four.
    output_fit_aggregation = (
        4 if n_intervals >= 4 and n_intervals % 4 == 0 else 1)
    output_ensemble = summarize_rows(
        directions, n_intervals=n_intervals,
        aggregation_quarters=output_fit_aggregation)
    output_station_ensemble = summarize_rows(
        stations_public, n_intervals=n_intervals,
        aggregation_quarters=output_fit_aggregation)
    if days > 1:
        if n_intervals != days * 96:
            raise ValueError(
                "multi-day sensor audit requires exactly 96 quarters per day")
        for day_index in range(days):
            start = day_index * 96
            end = start + 96
            daily_output_fit.append({
                "day": day_index + 1,
                "quarter_start": start,
                "quarter_end": end,
                "ensemble": summarize_rows(
                    [{
                        "target_mean": row["target_mean"][start:end],
                        "simulated_mean_raw":
                            row["simulated_mean_raw"][start:end],
                    } for row in directions],
                    n_intervals=96,
                    aggregation_quarters=output_fit_aggregation),
                "station_ensemble": summarize_rows(
                    [{
                        "target_mean": row["target_mean"][start:end],
                        "simulated_mean_raw":
                            row["simulated_mean_raw"][start:end],
                    } for row in stations_public],
                    n_intervals=96,
                    aggregation_quarters=output_fit_aggregation),
            })
    return {
        "schema_version": 1,
        "source": meta.get("source", "historical"),
        "provenance": provenance,
        "comparison": "calibration" if calibration_comparison else "scenario_effect",
        "representative": {
            "seed": representative.get("seed") if representative else None,
            "variant": representative_key,
        },
        "fit": fit,
        "output_fit": {
            "contract": "raw_edgeData_hourly_ensemble_vs_frozen_15min_targets",
            "uses_raw_ensemble_mean": raw_mean_flows is not None,
            "aggregation_quarters": output_fit_aggregation,
            "aggregation_minutes": output_fit_aggregation * 15,
            "ensemble": output_ensemble,
            "station_ensemble": output_station_ensemble,
            **({"per_day": daily_output_fit} if daily_output_fit else {}),
        },
        "stations": sorted(stations_public,
                            key=lambda item: item["sensor_id"]),
        "directions": directions,
    }


def write_edgedata_additional(path: Path, edgedata_file: Path,
                              duration_s: int, begin_s: int = 0) -> None:
    """duration_s/begin_s are absolute sim-time (same clock as --begin/--end),
    matching every other windowed-experiment convention in this project —
    begin_s=0 (default) preserves every existing caller's whole-run
    behaviour exactly. A windowed caller (e.g. signal_optimize.run_condition
    evaluating a bounded time-of-day window) must pass its own begin_s so
    the edgeData interval doesn't also count activity before the window."""
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <edgeData id="ed" file="{edgedata_file.name}" period="900" '
                f'begin="{begin_s}" end="{duration_s}" excludeEmpty="true"/>\n')
        f.write("</additional>\n")


REROUTER_RADIUS_M = 400


def edges_near(close_edges: list[str], radius_m: float) -> list[str]:
    """Edges whose midpoint lies within radius_m of a closed edge's midpoint.

    The rerouter is attached to THESE edges only. Attaching it to all 2 251
    edges makes every vehicle re-check its route on every edge entry —
    measured cost: 11.5 min per whole-day seed vs ~1 min with a local
    rerouter. Locality is also behaviourally reasonable: drivers divert when
    they encounter the closure area, not telepathically at departure.
    """
    import math
    metadata = load_metadata(NET_PATH, SUMO_DIR / "network_metadata.json")
    mids: dict[str, tuple[float, float]] = {}
    if metadata is not None:
        mids = {eid: tuple(item["midpoint"])
                for eid, item in metadata["edges"].items()
                if item.get("midpoint")}
    else:
        for e in ET.parse(SUMO_DIR / "plain.edg.xml").getroot().findall("edge"):
            pts = [tuple(map(float, p.split(","))) for p in e.get("shape").split()]
            mids[e.get("id")] = (sum(p[0] for p in pts) / len(pts),
                                 sum(p[1] for p in pts) / len(pts))
    centres = [mids[ce] for ce in close_edges if ce in mids]
    out = set(close_edges)
    for eid, (x, y) in mids.items():
        if any(math.hypot(x - cx, y - cy) <= radius_m for cx, cy in centres):
            out.add(eid)
    return sorted(out)


def grouped_closure_intervals(closures: list[dict]) -> list[dict]:
    """Validate closure windows and group simultaneous edges.

    SUMO accepts multiple ``closingReroute`` children in one interval.  The
    grouping keeps a recurring multi-edge worksite as one exact daily
    interval while still allowing the same edge to close again on a later
    non-overlapping day.
    """
    grouped: dict[tuple[int, int], list[str]] = {}
    by_edge: dict[str, list[tuple[int, int]]] = {}
    for closure in closures:
        edge = closure.get("edge_id")
        begin = closure.get("begin_s")
        end = closure.get("end_s")
        if (not isinstance(edge, str) or not edge
                or isinstance(begin, bool) or not isinstance(begin, int)
                or isinstance(end, bool) or not isinstance(end, int)
                or begin < 0 or end <= begin):
            raise ValueError(
                "closures require edge_id and non-negative increasing "
                "integer begin_s/end_s")
        grouped.setdefault((begin, end), []).append(edge)
        by_edge.setdefault(edge, []).append((begin, end))
    for edge, intervals in by_edge.items():
        intervals.sort()
        if any(current_begin < previous_end
               for (_, previous_end), (current_begin, _)
               in zip(intervals, intervals[1:])):
            raise ValueError(
                f"closure intervals for {edge} must not overlap")
    return [
        {"begin_s": begin, "end_s": end,
         "edge_ids": tuple(dict.fromkeys(grouped[(begin, end)]))}
        for begin, end in sorted(grouped)
    ]


def write_closure_additional(path: Path, closures: list[dict],
                             all_edges: list[str]) -> None:
    """One shared closure file per scenario. Vehicles whose remaining route
    uses a closed edge recompute when they enter a rerouter edge (the
    closure's neighbourhood)."""
    intervals = grouped_closure_intervals(closures)
    with open(path, "w") as f:
        f.write("<additional>\n")
        f.write(f'  <rerouter id="closure" edges="{" ".join(all_edges)}">\n')
        for interval in intervals:
            f.write(
                f'    <interval begin="{interval["begin_s"]}" '
                f'end="{interval["end_s"]}">\n')
            for edge_id in interval["edge_ids"]:
                f.write(
                    f'      <closingReroute id="{edge_id}" disallow="all"/>\n')
            f.write("    </interval>\n")
        f.write("  </rerouter>\n")
        f.write("</additional>\n")


def build_edge_graph(banned: set[str]) -> dict[str, list[str]]:
    """Directed edge->edge adjacency from net.net.xml's <connection> elements,
    with `banned` edges (the closure) removed from both ends — the same
    graph SUMO's own router uses to find a path, minus the closed edges."""
    metadata = load_metadata(NET_PATH, SUMO_DIR / "network_metadata.json")
    if metadata is not None:
        adj: dict[str, list[str]] = {}
        for frm, targets in metadata["successors"].items():
            if frm in banned:
                continue
            for to in targets:
                if to not in banned:
                    adj.setdefault(frm, []).append(to)
        return adj
    adj: dict[str, list[str]] = {}
    for c in ET.parse(NET_PATH).getroot().findall("connection"):
        frm, to = c.get("from"), c.get("to")
        if frm in banned or to in banned:
            continue
        adj.setdefault(frm, []).append(to)
    return adj


def reachable(adj: dict[str, list[str]], start: str, goal: str,
             banned: set[str]) -> bool:
    # start==goal is only trivially "reachable" if that edge itself isn't
    # the one being closed — a single-edge trip THROUGH the closed edge
    # (origin==destination==closed edge) has nowhere to go, not nowhere
    # to detour.
    if start in banned or goal in banned:
        return False
    if start == goal:
        return True
    seen = {start}
    stack = [start]
    while stack:
        for nxt in adj.get(stack.pop(), ()):
            if nxt == goal:
                return True
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def truncate_stranded_vehicles(route_path: Path, close_edges: list[str],
                               out_path: Path, adj: dict[str, list[str]],
                               closures: list[dict] | None = None,
                               edge_travel_s: dict[str, float] | None = None) -> tuple[int, int]:
    """Shorten (don't delete) vehicles whose route has no detour at all.

    FOUND 2026-07-09 (Gustav asked for the closure-leak finding from the
    demand rebuild's Codex review to actually get fixed): SUMO's runtime
    <rerouter>/closingReroute (write_closure_additional) reroutes ~99.4% of
    affected vehicles fine, but for an origin/destination pair with NO
    detour around the closure at all (confirmed directly: duarouter, even
    given the same closure additional file and even replanning the trip
    from scratch, still routes through the "closed" edge — rerouters are a
    RUNTIME sumo concept, invisible to the offline router — and a plain
    Dijkstra over net.net.xml's <connection> graph with the closed edges
    removed returns no path either, independent confirmation), the live
    rerouter can't find an alternative and the vehicle just sits stuck on
    the rerouter edge until sumo's end-of-run stuck-vehicle cleanup
    forcibly TELEPORTS it past the closure — which then shows up in the
    exported flows/trajectory as if it had legitimately driven the closed
    edge.

    FIRST FIX (same day) just deleted these vehicles outright — WRONG
    (Gustav, correctly: people whose actual destination is now unreachable
    by car still drive most of the way and park short of the closure,
    walking the rest — dropping the whole vehicle erases its real traffic
    contribution on every OTHER edge of its route too, not just the
    closed one, understating flow on the approach streets). Fixed:
    TRUNCATE the route at the last edge reachable before the closure
    (i.e. right before the first closed edge encountered) instead of
    removing the vehicle — it still drives and is counted on everything
    up to that point, it just "arrives" there instead of continuing.
    Only actually dropped if the closed edge is the very FIRST edge of
    the route (nothing to truncate to — no partial trip is possible).

    SECOND FIX (same day, Codex review): the reachability check used to
    test origin→destination, as a proxy for "will the live rerouter
    detour this one fine" — WRONG for two reasons Codex caught: (a) two
    vehicles with the same origin/destination can be on different
    candidate routes, so one can already be committed to a branch its
    ORIGIN could have avoided but IT no longer can; (b) with multiple
    `--close` edges, truncating at the first one encountered ignores
    whether a LATER closure on the same route is what actually kills the
    detour. Both are the same underlying bug: origin isn't where the live
    rerouter re-plans FROM — it re-plans from wherever the vehicle
    currently is when it hits the closure. Fixed: check reachability from
    the edge immediately BEFORE the first closed edge in THIS vehicle's
    own route (not from edges[0]) to the final destination — this is
    exactly what sumo's rerouter itself computes, and since `reachable()`
    does a full graph search (not just along the original candidate
    route) with ALL close_edges removed at once, it already correctly
    accounts for every other closure on the route too, not just the
    first.

    For a time window, only a vehicle estimated to arrive at its closed edge
    during that window is considered. Its arrival is depart plus free-flow
    travel time over the preceding route. A no-detour vehicle is retained if
    the remaining closure wait is safely below SUMO's 300 s teleport limit;
    otherwise it is truncated exactly as in the whole-run case.  This does
    not use periodic mode-8 routing: C1 showed that it changes route choice.

    `adj` is built ONCE by the caller (build_edge_graph(closed)) and
    reused across every demand variant file for this closure — it only
    depends on close_edges, not on which route file is being filtered.
    """
    closed = set(close_edges)
    # None deliberately selects the legacy branch below.  The eleven
    # whole-duration regression tests exercise that branch unchanged.
    windowed = closures is not None
    edge_travel_s = edge_travel_s or {}
    tree = ET.parse(route_path)
    root = tree.getroot()
    affected = [v for v in root.findall("vehicle")
                if closed & set(v.find("route").get("edges").split())]
    if not affected:
        tree.write(out_path, xml_declaration=True, encoding="UTF-8")
        return 0, 0
    cache: dict[tuple[str, str], bool] = {}
    n_truncated = 0
    n_dropped = 0
    for v in affected:
        route_el = v.find("route")
        edges = route_el.get("edges").split()
        candidates = [(idx, closure) for idx, edge in enumerate(edges)
                      for closure in (closures or []) if edge == closure["edge_id"]]
        if windowed:
            depart = float(v.get("depart") or 0)
            elapsed = 0.0
            active: list[tuple[int, dict, float]] = []
            for idx, edge in enumerate(edges):
                for ci, closure in candidates:
                    if ci == idx:
                        arrival = depart + elapsed
                        # Free-flow is an optimistic arrival estimate.  Only
                        # classify arrivals inside the stated window; this is
                        # intentionally conservative about the wait threshold.
                        if closure["begin_s"] <= arrival < closure["end_s"]:
                            active.append((idx, closure, arrival))
                elapsed += edge_travel_s.get(edge, 0.0)
            if not active:
                continue
            i, closure, arrival = min(active, key=lambda x: x[0])
            # C1 observed the default teleport at 301 s. Keep only waits
            # strictly below 300 s, leaving one second of headroom.
            if closure["end_s"] - arrival >= 300:
                pass
            else:
                continue
        else:
            i = next(idx for idx, e in enumerate(edges) if e in closed)
        if i == 0:
            root.remove(v)
            n_dropped += 1
            continue
        key = (edges[i - 1], edges[-1])
        ok = cache.get(key)
        if ok is None:
            ok = cache[key] = reachable(adj, *key, closed)
        if ok:
            continue   # the live rerouter will detour this one fine
        route_el.set("edges", " ".join(edges[:i]))
        n_truncated += 1
    tree.write(out_path, xml_declaration=True, encoding="UTF-8")
    return n_truncated, n_dropped


def demand_variants(meta: dict) -> list[Path]:
    """Calibrated route sets — q50 plus (if built) the q10/q90 direction-
    split variants. Monte Carlo seeds are spread over them so the seed
    spread — and the confidence — includes direction uncertainty.

    The metadata is the authoritative contract. Looking only for sibling
    files let a new q50-only demand build silently reuse stale q10/q90 files
    left by an older calibration.
    """
    paths = [SUMO_DIR / "calibrated.rou.xml"]
    n_variants = int(meta.get("n_variants", 1))
    if n_variants == 1:
        return paths
    if n_variants != 3:
        raise ValueError(f"unsupported demand metadata n_variants={n_variants}")
    for suffix in ("_v1", "_v2"):
        p = SUMO_DIR / f"calibrated{suffix}.rou.xml"
        if not p.exists():
            raise FileNotFoundError(
                f"demand metadata requires {p.name}, but it is missing")
        paths.append(p)
    return paths


def run_sumo(seed: int, route_path: Path, add_paths: list[Path],
             duration_s: int, home: Path, micro: bool = False,
             metrics: bool = False, begin_s: int = 0,
             net_path: Path | None = None,
             flush_s: int = 3600,
             vehroute_output: Path | None = None,
             vehroute_write_unfinished: bool = False,
             run_label: str | None = None,
             stats_path: Path | None = None,
             summary_output: Path | None = None,
             save_state_path: Path | None = None,
             save_state_time_s: int | None = None,
             load_state_path: Path | None = None,
             work_dir: Path | None = None) -> dict[str, Path] | None:
    # EdgeData's file attribute is relative to the SUMO cwd. A private
    # work_dir therefore isolates every generated XML while all input paths
    # remain absolute. The default keeps direct diagnostic callers working.
    run_cwd = Path(work_dir or SUMO_DIR)
    run_cwd.mkdir(parents=True, exist_ok=True)
    # Mesoscopic by default: our product is 15-min edge flows, which does not
    # need microscopic car-following — meso is ~20x faster (whole-day seed:
    # minutes → seconds), which is what makes interactive closures possible.
    cmd = [
        str(home / "bin" / "sumo"),
        # meso junction control: LIMITED mode only, not fully off. Full
        # control models signal delays from GUESSED (not real) timing plans
        # and measurably throttles below the flows reality (107-N delivery
        # 0.57 with plain junction-control, 0.92 without — both re-verified
        # 2026-07-06, along with --tls.default-type actuated and
        # --meso-tls-penalty, neither of which changed the result: SUMO's
        # mesoscopic engine doesn't model actuated programs at all, and the
        # penalty had no measurable effect either). --meso-junction-control
        # .limited only engages control at junctions actually approaching
        # saturation ("this prevents faulty traffic lights from hindering
        # flow in low-traffic situations" — SUMO docs) — re-measured
        # delivery identical to fully-off (0.822 mean either way) at today's
        # demand, so it costs nothing, while still being ready to model a
        # signal's real capacity limit if a scenario ever pushes a junction
        # into genuine saturation (e.g. a closure that concentrates flow).
        # Re-enable plain (unlimited) junction control when the city
        # provides real signal timings.
        *([] if micro else ["--mesosim", "true",
                            "--meso-junction-control", "true",
                            "--meso-junction-control.limited", "true"]),
        # net_path (default None -> the deployed NET_PATH, every existing
        # caller's behaviour is unchanged): signal_optimize.py (IMPROVEMENT_PLAN.md D2)
        # needs to compare against ALTERNATE networks whose ONLY difference
        # is TLS type ("actuated"/"delay_based" are baked in at netconvert
        # time, not a sumo runtime flag — verified: `sumo --tls.default-
        # type` does not exist, only `netconvert --tls.default-type` does).
        "-n", str((net_path or NET_PATH).resolve()),
        "-r", str(route_path.resolve()),
        # Every existing caller always has at least an edgeData additional,
        # but signal_lab.py (IMPROVEMENT_PLAN.md D1) genuinely has none — metrics=True
        # adds tripinfo/statistics/summary flags directly, independent of
        # -a. An empty "-a ''" is untested SUMO territory; omit the flag
        # entirely instead of risking it.
        *(["-a", ",".join(str(p.resolve()) for p in add_paths)] if add_paths else []),
        "--seed", str(seed),
        # begin_s shifts the simulation START (default 0, every existing
        # caller's behaviour is unchanged) — a vehicle with depart < begin_s
        # simply never departs, which is exactly what a BOUNDED time-of-day
        # window (signal_lab.py, IMPROVEMENT_PLAN.md D1) wants: only vehicles active in
        # the window are simulated at all, not the whole day filtered after
        # the fact. duration_s stays the far-end offset from t=0 regardless
        # of begin_s, matching every existing caller's mental model.
        "--begin", str(begin_s),
        # flush_s default 3600 (every existing whole-period caller's
        # behaviour is unchanged): generous flush for MESO's insertion-
        # queue delay (measured ~170 s avg backlog); a short flush silently
        # drops the tail. CRITICAL for a BOUNDED time-of-day window
        # (signal_lab.py/signal_optimize.py/signal_meso_screen.py, IMPROVEMENT_PLAN.md
        # D1-D3): --end does not just cap the simulation clock, it also
        # caps INSERTION — reusing the meso flush margin here let vehicles
        # scheduled to depart up to an hour AFTER the requested window
        # still be inserted and counted in the "window" total. Verified
        # empirically 2026-07-11 against real demand: a nominal 07:00-09:00
        # experiment with the meso flush actually simulated 07:00-10:00,
        # and 1 886 of 3 419 tripinfo entries (55%) had depart >= 09:00 —
        # more than half the "window" was contamination. These callers now
        # pass flush_s=0 (--end lands exactly on the window boundary); a
        # vehicle that departed in-window but hasn't finished by then is
        # honestly counted via the EXISTING unfinished_trips/
        # unfinished_waiting_trips guard metrics, not silently included as
        # if it had completed, and not padded with departures that were
        # never part of the requested window at all. Found in an external
        # review (NEW_CHANGES_REVIEW_2026-07-11.md section 1).
        "--end", str(duration_s + flush_s),
        "--no-step-log", "true",
        "--no-warnings", "true",
        # Vehicles whose destination IS the closed edge have no valid route —
        # drop them instead of aborting (standard for closure studies).
        "--ignore-route-errors", "true",
    ]
    metric_paths: dict[str, Path] = {}
    if (save_state_path is None) != (save_state_time_s is None):
        raise ValueError(
            "save_state_path and save_state_time_s must be supplied together")
    if save_state_path is not None:
        # SUMO's --end is exclusive: a state requested exactly at --end is
        # never written.  Require at least one following simulation second.
        if not begin_s < save_state_time_s < duration_s + flush_s:
            raise ValueError(
                "save_state_time_s must be after begin_s and before the "
                "exclusive simulation end")
        metric_paths["state"] = Path(save_state_path)
        cmd.extend(save_state_arguments(
            save_state_path, warmup_end_s=save_state_time_s))
    if load_state_path is not None:
        if begin_s <= 0:
            raise ValueError(
                "loading a warm state requires its positive saved time as begin_s")
        cmd.extend(load_state_arguments(load_state_path))
    if metrics:
        # Deliberately opt-in: interactive closures only need edgeData and
        # retain their current fast command path. The stem makes per-seed,
        # per-demand-variant output names deterministic for batch callers.
        # Batch experiments can run the same route/seed under different
        # signal conditions. Keep their evidence separate instead of letting
        # the later condition overwrite the earlier XML files. Existing
        # callers retain their historical filename shape when no label is
        # supplied.
        safe_label = "".join(c if c.isalnum() or c in "_-" else "_"
                             for c in run_label) if run_label else ""
        stem = f"metrics_{safe_label + '_' if safe_label else ''}{route_path.stem}_{seed}"
        metric_paths.update({
            "tripinfo": run_cwd / f"{stem}_tripinfo.xml",
            "statistics": run_cwd / f"{stem}_statistics.xml",
            "summary": run_cwd / f"{stem}_summary.xml",
        })
        cmd.extend([
            "--tripinfo-output", str(metric_paths["tripinfo"].resolve()),
            "--tripinfo-output.write-unfinished", "true",
            "--statistic-output", str(metric_paths["statistics"].resolve()),
            # Summary's waiting count is supporting diagnostics, not a queue
            # metric; SUMO's queue-output remains experimental.
            "--summary-output", str(metric_paths["summary"].resolve()),
            "--summary-output.period", "900",
        ])
    elif summary_output is not None:
        # Multi-day continuity evidence without the expensive per-vehicle
        # tripinfo/vehroute products.  End-of-run statistics remain separate
        # so the ordinary one-day path keeps its established fast command.
        metric_paths["summary"] = summary_output
        cmd.extend(["--summary-output", str(summary_output.resolve()),
                    "--summary-output.period", "900"])
    if stats_path is not None and "statistics" not in metric_paths:
        # E3 (IMPROVEMENT_PLAN.md): one tiny end-of-run XML with loaded/inserted/
        # running/waiting/teleports — the per-seed health record. Separate
        # from `metrics` because tripinfo is large and this must be cheap
        # enough for every interactive closure seed. metrics=True already
        # writes its own statistic-output; that one wins if both are asked.
        metric_paths["statistics"] = stats_path
        cmd.extend(["--statistic-output", str(stats_path.resolve())])
    if vehroute_output is not None:
        # IMPROVEMENT_PLAN.md D4: extracting the ACTUALLY-driven post-closure routes
        # (not the original pre-reroute demand) needs the runtime
        # rerouter's real decisions, only available from vehroute-output —
        # independent of `metrics` since D4 wants both from the SAME run
        # (the disruption metrics and the routes must come from the exact
        # same simulated scenario, not two separate seeds/runs).
        metric_paths["vehroute"] = vehroute_output
        cmd.extend(["--vehroute-output", str(vehroute_output.resolve()),
                   "--vehroute-output.exit-times", "true"])
        if vehroute_write_unfinished:
            cmd.extend(["--vehroute-output.write-unfinished", "true"])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(run_cwd), env={"SUMO_HOME": str(home)},
                             timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        sys.exit(f"sumo timed out after {SUMO_TIMEOUT_S}s (seed {seed})")
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit(f"sumo failed (seed {seed})")
    if save_state_path is not None and not Path(save_state_path).is_file():
        sys.exit(
            f"sumo did not write requested warm state (seed {seed}): "
            f"{save_state_path}")
    # `or None`: every existing caller's behaviour is unchanged when
    # neither metrics nor vehroute_output was requested (returns None, not
    # an empty dict) — only the SHAPE of what "requested output" means grew
    # to include vehroute alongside the pre-existing tripinfo/statistics/
    # summary trio.
    return metric_paths or None


# E3 health thresholds (IMPROVEMENT_PLAN.md; improvement plan 1.3-1.4: "thresholds from
# baselines, not visual taste"). Basis: SUMO's own vehicle accounting
# (loaded = read from route file; inserted = entered the network; waiting =
# still in the insertion queue at end; running = still driving at end;
# teleports = stuck-vehicle relocations, sumo docs "Simulation/Why Vehicles
# are teleporting" — the exact mechanism behind the 2026-07-09 closure-leak
# incident, where 39 teleported vehicles silently crossed a closed edge).
# The whole-day meso baseline runs with a full flush hour, so a healthy run
# drains: measured healthy values are ~0 unfinished and 0 teleports.
# UNFINISHED_MAX_SHARE at 2% and TELEPORT_MAX at max(10, 0.5% of inserted)
# leave room for legitimate closure-induced congestion while catching the
# failure classes actually seen (mass non-insertion, gridlock, teleport
# leaks). A seed that loads NOTHING is always fatal.
HEALTH_UNFINISHED_MAX_SHARE = 0.02
HEALTH_TELEPORT_MAX_ABS = 10
HEALTH_TELEPORT_MAX_SHARE = 0.005


def parse_seed_health(stats_path: Path, seed: int, variant: str) -> dict | None:
    """Parse SUMO's --statistic-output into one per-seed health record."""
    if not stats_path.exists():
        return None
    try:
        root = ET.parse(stats_path).getroot()
    except ET.ParseError:
        return None

    def num(tag: str, attr: str) -> int | None:
        el = root.find(tag)
        v = el.get(attr) if el is not None else None
        return int(float(v)) if v is not None else None

    return {
        "seed": seed,
        "variant": variant,
        "loaded": num("vehicles", "loaded"),
        "inserted": num("vehicles", "inserted"),
        "running_at_end": num("vehicles", "running"),
        "waiting_at_end": num("vehicles", "waiting"),
        "teleports": num("teleports", "total"),
        "collisions": num("safety", "collisions"),
    }


def seed_health_flags(seed_health: list[dict]) -> list[str]:
    """Evaluate every seed's record against the E3 gates → list of failures.

    Empty list == healthy. serve.py's publish gate refuses a staged
    baseline whose payload carries any flag; the CLI prints them as
    warnings (the artifact still exists for inspection — refusing to
    PUBLISH is the gate, not refusing to compute)."""
    flags = []
    for h in seed_health:
        if h is None:
            continue
        tag = f"seed {h['seed']}"
        loaded, inserted = h.get("loaded"), h.get("inserted")
        if loaded is not None and inserted is not None:
            if inserted == 0 and loaded > 0:
                flags.append(f"{tag}: 0 of {loaded} loaded vehicles inserted")
                continue
            unfinished = (h.get("running_at_end") or 0) + (h.get("waiting_at_end") or 0)
            if loaded > 0 and unfinished / loaded > HEALTH_UNFINISHED_MAX_SHARE:
                flags.append(
                    f"{tag}: {unfinished}/{loaded} vehicles unfinished at end "
                    f"({100 * unfinished / loaded:.1f}% > "
                    f"{100 * HEALTH_UNFINISHED_MAX_SHARE:.0f}%)")
        tele = h.get("teleports")
        if tele is not None and inserted:
            if tele > max(HEALTH_TELEPORT_MAX_ABS,
                          HEALTH_TELEPORT_MAX_SHARE * inserted):
                flags.append(f"{tag}: {tele} teleports (max "
                             f"{max(HEALTH_TELEPORT_MAX_ABS, int(HEALTH_TELEPORT_MAX_SHARE * inserted))})")
    return flags


def final_route(veh: ET.Element) -> ET.Element | None:
    """The route the vehicle ACTUALLY drove.

    SUMO wraps a vehicle whose route was changed at runtime (the closure
    rerouter) in <routeDistribution>, last <route> = final. Found in the
    2026-07-14 accuracy review and
    verified on the real closure artifact: 3182/21594 vehicles sat in
    routeDistribution and were silently DROPPED from the animation —
    exactly the rerouted vehicles that matter most after a closure. F1's
    displayed_share had mislabelled them as "drives outside the map".
    Same selection as signal_closure_combine.extract_final_routes."""
    dist = veh.find("routeDistribution")
    if dist is not None:
        routes = dist.findall("route")
        return routes[-1] if routes else None
    return veh.find("route")


def load_agent_endpoint_positions(route_path: Path) -> dict[str, dict]:
    """Load optional calibrated endpoint positions keyed by vehicle id.

    SUMO's vehroute output normally records edges and times but not the
    ``departPos``/``arrivalPos`` assigned in the calibrated route file.  The
    adjacent agent provenance is the authoritative compact sidecar, so use it
    to keep the browser animation spatially consistent with the simulation.
    Older route products simply return an empty mapping.
    """
    agent_path = route_path.with_name(
        route_path.name.replace(".rou.xml", ".agents.json"))
    try:
        doc = json.loads(agent_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    positions: dict[str, dict] = {}
    for agent in doc.get("agents", []):
        vehicle_id = agent.get("vehicle_id")
        if not isinstance(vehicle_id, str):
            continue
        record = {}
        for source, target in (("origin_position_m", "p"),
                               ("destination_position_m", "a")):
            try:
                value = float(agent[source])
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0:
                record[target] = round(value, 2)
        if record:
            positions[vehicle_id] = record
    return positions


def parse_vehroute_file(vr_file: Path, web_edges: set[str],
                        endpoint_positions: dict[str, dict] | None = None,
                        max_vehicles_per_day: int | None = None,
                        return_sampling: bool = False):
    """vehroute XML → compact vehicle timelines for the web animation.

    Returns (edge_index, vehicles, n_in_file, n_unfinished). F2: an
    UNFINISHED vehicle (still driving at end of run — requires
    --vehroute-output.write-unfinished) has exit times only for the edges
    it actually left; sumo emits -1 for the rest. The driven prefix is
    kept and the vehicle marked "u": 1 so the renderer parks it at its
    last known position instead of erasing the trip.

    ``iterparse`` is deliberate: a seven-day vehroute file can contain well
    over 100 000 large route elements. Loading the whole XML tree made peak
    memory scale with the study length. When ``max_vehicles_per_day`` is set,
    the lowest SHA-256 ranks per departure day are retained, making the sample
    stable across reruns and XML ordering while preserving every day.
    """
    if max_vehicles_per_day is not None and max_vehicles_per_day <= 0:
        raise ValueError("max_vehicles_per_day must be positive")

    selected: list[dict] = []
    day_heaps: dict[int, list[tuple[int, str, dict]]] = {}
    eligible_by_day: dict[int, int] = {}
    n_in_file = 0
    context = ET.iterparse(vr_file, events=("start", "end"))
    _event, root = next(context)
    for event, veh in context:
        if event != "end" or veh.tag != "vehicle":
            continue
        n_in_file += 1
        route = final_route(veh)
        if route is None or not route.get("exitTimes"):
            veh.clear()
            root.clear()
            continue
        edges = route.get("edges").split()
        exits: list[int] = []
        for t in route.get("exitTimes").split():
            v = int(float(t))
            if v < 0:
                break
            exits.append(v)
        unfinished = len(exits) < len(edges)
        if unfinished:
            if not exits:
                veh.clear()
                root.clear()
                continue   # never left its first edge — no drawable path
            edges = edges[:len(exits)]
        if not edges or any(edge not in web_edges for edge in edges):
            veh.clear()
            root.clear()
            continue
        vehicle_id = veh.get("id", "")
        depart = int(float(veh.get("depart")))
        day = max(0, depart // 86400)
        rec = {"_vehicle_id": vehicle_id, "_edges": edges,
               "_unfinished": unfinished, "d": depart, "x": exits}
        endpoint = (endpoint_positions or {}).get(vehicle_id, {})
        if "p" in endpoint:
            rec["p"] = endpoint["p"]
        if "a" in endpoint:
            rec["a"] = endpoint["a"]
        if unfinished:
            rec["u"] = 1
        eligible_by_day[day] = eligible_by_day.get(day, 0) + 1
        if max_vehicles_per_day is None:
            selected.append(rec)
        else:
            rank = int.from_bytes(
                hashlib.sha256(vehicle_id.encode("utf-8")).digest(), "big")
            entry = (-rank, vehicle_id, rec)
            heap = day_heaps.setdefault(day, [])
            if len(heap) < max_vehicles_per_day:
                heapq.heappush(heap, entry)
            elif rank < -heap[0][0]:
                heapq.heapreplace(heap, entry)
        veh.clear()
        root.clear()

    if max_vehicles_per_day is not None:
        selected = [entry[2] for day in sorted(day_heaps)
                    for entry in day_heaps[day]]
    selected.sort(key=lambda rec: (rec["d"], rec["_vehicle_id"]))

    edge_index: dict[str, int] = {}
    vehicles: list[dict] = []
    selected_by_day: dict[int, int] = {}
    n_unfinished = 0
    for rec in selected:
        day = max(0, rec["d"] // 86400)
        selected_by_day[day] = selected_by_day.get(day, 0) + 1
        idxs = []
        for edge in rec.pop("_edges"):
            if edge not in edge_index:
                edge_index[edge] = len(edge_index)
            idxs.append(edge_index[edge])
        rec["e"] = idxs
        rec.pop("_vehicle_id", None)
        if rec.pop("_unfinished"):
            n_unfinished += 1
        vehicles.append(rec)

    all_days = sorted(set(eligible_by_day) | set(selected_by_day))
    sampling = {
        "enabled": max_vehicles_per_day is not None,
        "method": (TRAJECTORY_SAMPLING_METHOD
                   if max_vehicles_per_day is not None else None),
        "max_vehicles_per_day": max_vehicles_per_day,
        "eligible_vehicles": sum(eligible_by_day.values()),
        "selected_vehicles": len(vehicles),
        "per_day": [{
            "day": day + 1,
            "eligible": eligible_by_day.get(day, 0),
            "selected": selected_by_day.get(day, 0),
        } for day in all_days],
    }
    result = (edge_index, vehicles, n_in_file, n_unfinished)
    return (*result, sampling) if return_sampling else result


def publish_trajectories_from_vehroute(
        name: str, route_path: Path, vr_file: Path, stats_path: Path,
        web_edges: set[str], seed: int = 1000,
        max_vehicles_per_day: int | None = None) -> str | None:
    """Publish trajectories from an already-completed SUMO run.

    The normal scenario path requests vehroute output during its representative
    seed's flow run and calls this function afterward. Keeping parsing and
    publication separate from process launch prevents a second simulation
    with the same seed from being mistaken for the representative run.
    """
    if not vr_file.exists():
        print(f"  trajectories: missing vehroute output {vr_file}")
        return None

    endpoint_positions = load_agent_endpoint_positions(route_path)
    edge_index, vehicles, n_in_file, n_unfinished, sampling = \
        parse_vehroute_file(
            vr_file, web_edges, endpoint_positions=endpoint_positions,
            max_vehicles_per_day=max_vehicles_per_day,
            return_sampling=True)
    vehicles.sort(key=lambda v: v["d"])
    inv = [None] * len(edge_index)
    for e, i in edge_index.items():
        inv[i] = e

    # Keep parse integrity separate from the deliberate off-map display
    # filter. A short vehroute file is withheld; an off-map route is disclosed.
    health = parse_seed_health(stats_path, seed=seed,
                               variant=route_path.name)
    inserted = health.get("inserted") if health else None
    if inserted:
        integrity = n_in_file / inserted
        if integrity < 0.98:
            print(f"  trajectories: FAILED reconciliation — vehroute file "
                  f"holds {n_in_file} of {inserted} inserted vehicles "
                  f"({100 * integrity:.1f}% < 98%); artifact withheld")
            return None

    traj_name = f"{name}_traj.json"
    atomic_write_json(OUT_DIR / traj_name,
                      {"seed": seed, "variant": route_path.name,
                       "n_vehicles": len(vehicles),
                       "n_unfinished": n_unfinished,
                       "inserted_in_run": inserted,
                       "sampling": sampling,
                       "displayed_share": (round(len(vehicles) / inserted, 4)
                                           if inserted else None),
                       "edges": inv, "vehicles": vehicles},
                      separators=(",", ":"))
    size_mb = (OUT_DIR / traj_name).stat().st_size / 1e6
    print(f"  trajectories: {len(vehicles)} of {inserted or '?'} vehicles "
          f"drawable → {traj_name} ({size_mb:.1f} MB; seed {seed}, "
          f"{route_path.name})")
    return traj_name


def export_trajectories(name: str, route_path: Path, closure_add: list[Path],
                        duration_s: int, home: Path,
                        web_edges: set[str], micro: bool = False) -> str | None:
    """Legacy standalone SUMO run with vehroute exit-times.

    The main scenario path no longer calls this function: it captures the
    same output during the existing seed-1000 flow run. This wrapper remains
    for diagnostic callers and backwards-compatible tests.

    One extra SUMO run with vehroute exit-times → a compact per-vehicle
    edge-timeline the web animates: EVERY DOT IS A REAL SIMULATED VEHICLE
    with its origin, destination, route and congestion-accurate timing.

    Format (indices into a shared edge list, times in whole seconds):
      {"edges": [...], "vehicles": [{"d": depart, "e": [i...], "x": [t...]}]}
    """
    vr_file = SUMO_DIR / f"vehroutes_{name}.xml"
    cmd = [
        str(home / "bin" / "sumo"),
        *([] if micro else ["--mesosim", "true",
                            "--meso-junction-control", "true",
                            "--meso-junction-control.limited", "true"]),
        "-n", str(NET_PATH.resolve()),
        "-r", str(route_path.resolve()),
        *(("-a", ",".join(str(p.resolve()) for p in closure_add))
          if closure_add else ()),
        "--vehroute-output", vr_file.name,
        "--vehroute-output.exit-times", "true",
        # F2 (IMPROVEMENT_PLAN.md): vehicles still driving/queued when the run ends
        # must appear in the artifact (drawn parked at their last known
        # position) instead of silently vanishing — the same honesty rule
        # as closure truncation. Without this flag sumo omits them from
        # vehroute-output entirely.
        "--vehroute-output.write-unfinished", "true",
        # F1 (IMPROVEMENT_PLAN.md): the trajectory run writes its own health record so
        # the artifact's vehicle count can be reconciled against what the
        # run actually loaded/inserted — a truncated vehroute parse must
        # not silently animate a subset as if it were everyone.
        "--statistic-output", f"stats_traj_{name}.xml",
        "--begin", "0", "--end", str(duration_s + 3600),
        "--no-step-log", "true", "--no-warnings", "true",
        "--ignore-route-errors", "true", "--seed", "1000",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=str(SUMO_DIR), env={"SUMO_HOME": str(home)},
                             timeout=SUMO_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        print(f"sumo (trajectory export) timed out after {SUMO_TIMEOUT_S}s")
        return None
    if res.returncode != 0:
        print(res.stderr[-800:])
        return None
    return publish_trajectories_from_vehroute(
        name, route_path, vr_file, SUMO_DIR / f"stats_traj_{name}.xml",
        web_edges, seed=1000)


def parse_edgedata(
    path: Path,
    n_intervals: int,
    measured_empty_edges: list[str] | tuple[str, ...] = (),
) -> dict[str, np.ndarray]:
    """Return edge ``entered`` counts, retaining required measured zeros.

    ``write_edgedata_additional`` uses ``excludeEmpty=true``: an edge absent
    from this output was still measured and carried zero vehicles. Most map
    aggregation already zero-fills absent edges. Closure integrity needs the
    same distinction explicitly for closed edges so a successful zero-flow
    closure is not mislabeled "not measurable".
    """
    flows: dict[str, np.ndarray] = {}
    root = ET.parse(path).getroot()
    for interval in root.findall("interval"):
        i = int(float(interval.get("begin")) // 900)
        if i >= n_intervals:
            continue
        for edge in interval.findall("edge"):
            eid = edge.get("id")
            entered = float(edge.get("entered") or 0)
            if eid not in flows:
                flows[eid] = np.zeros(n_intervals)
            flows[eid][i] = entered
    for edge_id in measured_empty_edges:
        flows.setdefault(edge_id, np.zeros(n_intervals))
    return flows


def aggregate_flows(
    per_seed: list[dict[str, np.ndarray]], web_edges: set[str],
    prior: dict[str, float], n_intervals: int,
) -> tuple[dict[str, list[int]], dict[str, float]]:
    """Mean flows + Monte Carlo confidence, for EVERY edge the map can draw.

    Iterates web_edges, not just the union of edges some seed's edgeData
    happened to report traffic on (found in a bug review 2026-07-10,
    independently verified, previously the whole set here): SUMO uses the
    same edge IDs as network.geojson for the whole simulated graph, so an
    edge absent from a seed's edgeData (excludeEmpty="true") genuinely
    carried zero vehicles that seed, not "wasn't simulated" — the
    ps.get(eid, np.zeros(...)) fallback below already treats it that way
    per-seed; restricting the OUTER loop to only observed edges silently
    turned every genuinely-zero edge into a rendered "no data" gap instead
    of a real, honest zero — the exact inversion of this project's own
    null-means-missing/zero-means-measured contract."""
    flows_out: dict[str, list[int]] = {}
    conf_out:  dict[str, float] = {}
    for eid in sorted(web_edges):
        stack = np.stack([ps.get(eid, np.zeros(n_intervals)) for ps in per_seed])
        mean  = stack.mean(axis=0)
        flows_out[eid] = [int(round(v)) for v in mean]

        busy = mean > 2           # CV is meaningless for near-zero flows
        cv   = float((stack.std(axis=0)[busy] / mean[busy]).mean()) if busy.any() else 0.0
        conf_out[eid] = round(prior[eid] * float(np.exp(-cv)), 3)
    return flows_out, conf_out


def create_scenario_workspace(name: str) -> Path:
    """Create the exclusive scratch directory for one scenario run.

    Tracked CLI runs use runs/<run-id>/scratch. Direct library callers use a
    temporary child of SUMO_DIR so tests and integrations retain a simple
    fallback without sharing files with another scenario.
    """
    global _LAST_SCRATCH_DIR, _LAST_SCRATCH_KEPT
    if _ACTIVE_RUN_DIR is not None:
        workspace = _ACTIVE_RUN_DIR / "scratch"
        workspace.mkdir(parents=True, exist_ok=False)
    else:
        SUMO_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:40]
        workspace = Path(tempfile.mkdtemp(
            prefix=f".scenario_{slug}_", dir=str(SUMO_DIR)))
    _LAST_SCRATCH_DIR = workspace
    _LAST_SCRATCH_KEPT = False
    return workspace


def cleanup_scenario_workspace(workspace: Path, keep: bool = False) -> None:
    """Remove disposable scratch only after a successful publication."""
    global _LAST_SCRATCH_KEPT
    _LAST_SCRATCH_KEPT = bool(keep)
    if keep:
        print(f"  kept scenario scratch: {workspace}")
        return
    shutil.rmtree(workspace, ignore_errors=False)


def run_seed_job(job: dict) -> dict:
    """Run and parse one independent SUMO seed.

    All paths in job are seed-specific. The function is intentionally free of
    publication or aggregation side effects so a bounded executor can run it
    concurrently while the parent retains deterministic seed ordering.
    """
    run_sumo(
        job["seed"], job["route_path"], job["add_paths"], job["duration_s"],
        job["home"], micro=job["micro"], stats_path=job["stats_file"],
        vehroute_output=job.get("vehroute_output"),
        vehroute_write_unfinished=job.get("vehroute_write_unfinished", False),
        summary_output=job.get("summary_file"),
        work_dir=job["work_dir"])
    flows = parse_edgedata(
        job["edge_file"], job["n_intervals"],
        measured_empty_edges=job.get("closure_edges", ()))
    health = parse_seed_health(job["stats_file"], job["seed"],
                               job["route_path"].name)
    daily = None
    if job.get("summary_file") is not None:
        daily = parse_summary(
            job["summary_file"],
            day_boundaries_s=job["day_boundaries_s"],
            days=job["days"],
        )
        if health is not None:
            health["multi_day"] = daily
    return {
        "seed": job["seed"],
        "route_path": job["route_path"],
        "demand_variant": job.get("demand_variant"),
        "target_key": target_key_for_variant(job.get("demand_variant")),
        "flows": flows,
        "health": health,
        "multi_day": daily,
    }


def main() -> None:
    global OUT_DIR, _LAST_SCENARIO_OUTPUT, _LAST_TRAJECTORY_OUTPUT
    _LAST_SCENARIO_OUTPUT = None
    _LAST_TRAJECTORY_OUTPUT = None
    args = parse_args()
    if args.out_dir:
        OUT_DIR = Path(args.out_dir)
    home = sumo_home()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    n_intervals = meta["n_intervals"]
    duration_s  = n_intervals * 900
    days = int(meta.get("days", 1))
    day_boundaries_s = [int(value) for value in
                        (meta.get("day_boundaries_s") or
                         [day * 86400 for day in range(days + 1)])]
    sig = demand_signature(meta)

    spec: ScenarioSpec | None = None
    if args.scenario_spec:
        try:
            spec = load_scenario_spec(Path(args.scenario_spec))
            validate_scenario_spec(spec, meta=meta, duration_s=duration_s,
                                   network_path=NET_PATH)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.exit(f"invalid ScenarioSpec: {exc}")
        if args.close or args.closure:
            sys.exit("--scenario-spec cannot be combined with --close/--closure")
        # The spec is authoritative for the simulation level. Legacy callers
        # continue to use --micro when no spec is supplied.
        args.micro = spec.simulation_mode == "micro"

    try:
        if spec is not None:
            raw_closures = [json.dumps({
                "edge_id": closure.edge_id,
                "begin": closure.start_time,
                "end": closure.end_time,
            }) for closure in spec.closures]
            closures = structured_closures(raw_closures, [],
                                           meta["epoch_sim"], duration_s)
        else:
            closures = structured_closures(args.closure, args.close,
                                           meta["epoch_sim"], duration_s)
    except ValueError as exc:
        sys.exit(str(exc))
    close_edges = list(dict.fromkeys(c["edge_id"] for c in closures))
    windowed_closure = bool(args.closure or spec is not None)

    prior, names = load_geojson_meta()
    for ce in close_edges:
        if ce not in prior:
            sys.exit(f"closure {ce}: not an edge in network.geojson")

    if args.name:
        if not valid_scenario_name(args.name):
            sys.exit(f"--name {args.name!r}: use only letters, digits, "
                     "_ + - (max 80 chars)")
        if spec is not None and args.name != spec.scenario_id:
            sys.exit("--name must match ScenarioSpec.scenario_id")
        name = args.name
    elif spec is not None:
        name = spec.scenario_id
        if not valid_scenario_name(name):
            sys.exit(f"ScenarioSpec.scenario_id {name!r} is not a valid scenario name")
    elif close_edges:
        name = "close_" + "+".join(close_edges)
        if windowed_closure:
            # Two windows on the same edge are distinct scenarios; unlike the
            # legacy whole-run name, include their structured identity.
            window_hash = hashlib.sha1(
                json.dumps(closures, sort_keys=True).encode()).hexdigest()[:8]
            name = f"{name}_{window_hash}"
        if len(name) > 80:   # many edges → keep the filename sane
            name = f"close_{len(close_edges)}edges_" + \
                   hashlib.sha1("+".join(close_edges).encode()).hexdigest()[:8]
    else:
        name = "baseline"

    if close_edges:
        streets = sorted({names[ce] for ce in close_edges})
        label = "Avstängning: " + ", ".join(streets)
    else:
        label = "Baslinje (ingen avstängning)"

    scratch_dir = create_scenario_workspace(name)

    variants = demand_variants(meta)
    if spec is not None:
        seed_values = list(spec.seed_set)
        variant_by_seed = dict(spec.demand_variant_mapping)
    else:
        seed_values = [1000 + s for s in range(args.seeds)]
        default_variants = ("q50", "q10", "q90")
        variant_by_seed = {
            seed: default_variants[i % min(len(variants), len(default_variants))]
            for i, seed in enumerate(seed_values)
        }
    seed_count = len(seed_values)
    try:
        validate_variant_coverage(meta, variant_by_seed,
                                  require_all=seed_count >= 3)
    except ValueError as exc:
        sys.exit(str(exc))
    if spec is None:
        # Legacy CLI runs still emit a validated contract in their result so
        # downstream tools can migrate without two different payload shapes.
        network_id = sha256_file(NET_PATH)
        if network_id is None:
            sys.exit(f"network artifact missing: {NET_PATH}")
        spec = ScenarioSpec.from_dict({
            "scenario_id": name,
            "demand_build_id": str(meta.get("build_id") or sig),
            "network_build_id": network_id,
            "start_time": meta["epoch_sim"],
            "end_time": (pd.Timestamp(meta["epoch_sim"])
                          + pd.Timedelta(seconds=duration_s)).isoformat(),
            "closures": contract_closures(
                closures, meta["epoch_sim"], duration_s),
            "simulation_mode": "micro" if args.micro else "meso",
            "seed_set": seed_values,
            "demand_variant_mapping": variant_by_seed,
        })
    print(f"Scenario '{name}'  ({label})  —  {seed_count} seeds × {n_intervals} × 15 min")

    if len(variants) > 1:
        print(f"  {len(variants)} demand variants (q50 + direction-split bounds)")

    closure_add: list[Path] = []
    n_truncated = n_dropped = 0   # stay 0 for a baseline (no-closure) run
    if close_edges:
        rerouter_edges = edges_near(close_edges, REROUTER_RADIUS_M)
        print(f"  rerouter on {len(rerouter_edges)} edges within "
              f"{REROUTER_RADIUS_M} m of the closure")
        cpath = scratch_dir / f"closure_{name}.add.xml"
        write_closure_additional(cpath, closures, rerouter_edges)
        closure_add = [cpath]

        # Vehicles with no detour around the closure at all can't be fixed
        # by the runtime rerouter above (see truncate_stranded_vehicles) —
        # shortened/dropped here so they never get simulated past the
        # closure, instead of relying on sumo's stuck-vehicle teleport to
        # hide them after the fact.
        adj = build_edge_graph(set(close_edges))
        freeflow = edge_freeflow_times()
        filtered_variants = []
        for vp in variants:
            fp = scratch_dir / f"{vp.stem}_{name}.rou.xml"
            t, d = truncate_stranded_vehicles(
                vp, close_edges, fp, adj,
                # Preserve the exact tested function path for legacy --close.
                closures=closures if windowed_closure else None,
                edge_travel_s=freeflow)
            n_truncated += t
            n_dropped += d
            filtered_variants.append(fp)
        if n_truncated:
            print(f"  truncated {n_truncated} vehicle(s) with no detour around "
                  f"the closure to end just short of it (parked there instead "
                  f"of continuing) rather than sitting stuck and getting "
                  f"teleported through it at end of run")
        if n_dropped:
            print(f"  dropped {n_dropped} vehicle(s) that couldn't even "
                  f"depart with the closure in place")
        variants = filtered_variants

    trajectory_enabled = want_trajectories(args, n_intervals)
    trajectory_stats_file = None
    trajectory_seed = seed_values[0]
    trajectory_route_path = None

    jobs = []
    for seed in seed_values:
        demand_variant = variant_by_seed[seed]
        route_path = variant_path(variants, demand_variant)
        # SUMO is an external process and writes relative edgeData files from
        # its cwd. Give every seed a private directory, even when the parent
        # schedules seeds concurrently. This makes the isolation contract
        # executable instead of relying on filename conventions alone.
        seed_dir = scratch_dir / f"seed-{seed}"
        seed_dir.mkdir(parents=True, exist_ok=False)
        ed_file  = seed_dir / f"edgedata_{name}_{seed}.xml"
        add_path = seed_dir / f"additional_{name}_{seed}.add.xml"
        stats_file = seed_dir / f"stats_{name}_{seed}.xml"
        summary_file = (seed_dir / f"summary_{name}_{seed}.xml"
                        if days > 1 else None)
        trajectory_vr_file = (seed_dir / f"vehroutes_{name}.xml"
                              if seed == trajectory_seed and trajectory_enabled else None)
        if seed == trajectory_seed and trajectory_enabled:
            trajectory_stats_file = stats_file
            trajectory_route_path = route_path
        write_edgedata_additional(add_path, ed_file, duration_s)
        jobs.append({
            "seed": seed,
            "route_path": route_path,
            "demand_variant": demand_variant,
            "add_paths": [add_path] + closure_add,
            "duration_s": duration_s,
            "home": home,
            "micro": args.micro,
            "stats_file": stats_file,
            "summary_file": summary_file,
            "days": days,
            "day_boundaries_s": day_boundaries_s,
            "edge_file": ed_file,
            "closure_edges": close_edges,
            "n_intervals": n_intervals,
            "vehroute_output": trajectory_vr_file,
            "vehroute_write_unfinished": (seed == trajectory_seed and trajectory_enabled),
            "work_dir": seed_dir,
        })

    if args.seed_workers == 1 or len(jobs) == 1:
        results = [run_seed_job(job) for job in jobs]
    else:
        executor = ThreadPoolExecutor(
            max_workers=min(args.seed_workers, len(jobs)),
            thread_name_prefix="sumo-seed")
        futures = [executor.submit(run_seed_job, job) for job in jobs]
        try:
            results = [future.result() for future in as_completed(futures)]
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    # Completion order is intentionally discarded. Confidence and JSON output
    # must use the same seed order for serial and parallel execution.
    results.sort(key=lambda item: item["seed"])
    per_seed: list[dict[str, np.ndarray]] = []
    seed_health: list[dict] = []
    for result in results:
        per_seed.append(result["flows"])
        health = result["health"]
        if health is not None:
            seed_health.append(health)
        h_note = ""
        if health is not None:
            h_note = (f", {health['inserted']}/{health['loaded']} inserted, "
                      f"{health['teleports']} teleports")
        print(f"  seed {result['seed']} ({result['route_path'].name}): "
              f"{len(per_seed[-1])} edges with traffic{h_note}")

    health_flags = seed_health_flags(seed_health)
    # FAIL CLOSED (2026-07-14 accuracy review §P0-3): a seed whose
    # statistics file is missing/corrupt is UNMEASURED, not healthy — it
    # previously produced no record, no flag, and published as clean.
    # Missing telemetry now flags exactly like a failed gate (E2's
    # publisher refuses any flagged baseline).
    if len(seed_health) < seed_count:
        health_flags.append(
            f"hälsotelemetri saknas för {seed_count - len(seed_health)} av "
            f"{seed_count} frön — omätt är inte friskt")
    for flag in health_flags:
        print(f"  WARNING seed health: {flag}")

    multi_day_validation = None
    if days > 1:
        from traffic_sim.simulation.multiday import aggregate as aggregate_multiday
        multi_day_validation = aggregate_multiday(
            [result.get("multi_day") for result in results],
            days=days, day_boundaries_s=day_boundaries_s)
        for reason in multi_day_validation.get("reasons", []):
            print(f"  WARNING multi-day continuity: {reason}")

    # ── Aggregate: mean flows + Monte Carlo confidence ─────────────────────────
    web_edges = set(prior)   # only edges the map can draw
    flows_out, conf_out = aggregate_flows(per_seed, web_edges, prior, n_intervals)
    # Only the audited sensor edges need the pre-rounding ensemble mean; the
    # map keeps its rounded display value.  Computing it citywide would be
    # ~7 000 edges × quarters × seeds of pure-Python work for values nothing
    # reads.
    raw_mean_flows = {
        info["edge_id"]: _raw_mean_series(results, info["edge_id"], n_intervals)
        for info in sensor_audit_edges()
    }
    # Sensor verification is a first-class scenario product, not a manual
    # visual inference from moving dots.  It keeps the three distinct values
    # together for every directed counter edge: PFE target, ensemble mean,
    # and the single q50 seed whose vehicles the browser animates.
    sensor_audit = build_sensor_audit(
        meta, results, flows_out, n_intervals,
        raw_mean_flows=raw_mean_flows,
        # The audit's "representative" must be the seed whose vehicles the
        # browser animates (the trajectory seed), not whichever seed sorts
        # first — a ScenarioSpec seed_set is not required to be ascending.
        representative_seed=trajectory_seed,
        # A closure is meant to differ from its baseline calibration target.
        # Keep values visible, but do not present that difference as a failed
        # demand-calibration gate.
        calibration_comparison=not bool(closures),
    )
    fit_errors = baseline_output_fit_errors(
        meta, sensor_audit, n_intervals=n_intervals, closures=closures)
    if fit_errors:
        raise RuntimeError(
            "baseline sensor output fit failed: " + "; ".join(fit_errors[:3])
            + "; no scenario was published")
    # Integrity is evaluated on each raw seed before the display mean is
    # rounded. A single seed with prohibited active-closure flow must not be
    # hidden by averaging it with zero-flow seeds.
    active_entries_by_seed = ([cm.active_closure_throughput(seed_flows, closures)
                               for seed_flows in per_seed] if closures else [])
    active_closure_entries = aggregate_active_closure_entries(
        active_entries_by_seed, closures)
    closure_integrity = closure_integrity_status(active_closure_entries, closures)
    if closures and closure_integrity != "verified_clean":
        # A scenario that either leaked vehicles through an active closure or
        # never measured the closed edge cannot be presented as a successful
        # closure. Fail before trajectory/scenario/manifest publication so a
        # UI consumer has no selectable "done" artifact with false closure
        # semantics; the preserved scratch/run registry remains the evidence
        # for diagnosis.
        raise RuntimeError(
            "closure integrity is not verified: "
            f"{closure_integrity} (active closure-edge entries: "
            f"{active_closure_entries!r}); no scenario was published")

    window_label = demand_window_label(meta)

    traj_name = None
    if trajectory_enabled and trajectory_stats_file is not None and trajectory_route_path is not None:
        traj_name = publish_trajectories_from_vehroute(
            name, trajectory_route_path,
            scratch_dir / f"seed-{trajectory_seed}" / f"vehroutes_{name}.xml",
            trajectory_stats_file,
            web_edges, seed=trajectory_seed,
            max_vehicles_per_day=trajectory_sample_cap(n_intervals))
        _LAST_TRAJECTORY_OUTPUT = OUT_DIR / traj_name

    payload = {
        "epoch":            meta["epoch_sim"],
        "interval_minutes": 15,
        "n_quarters":       n_intervals,
        "generated_at":     pd.Timestamp.now().isoformat(timespec="seconds"),
        "scenario_spec":    spec.to_dict(),
        "trajectories":     traj_name,
        "scenario": {
            "name": name, "scenario_id": spec.scenario_id, "label": label,
            "closed_edges": close_edges,
            "closures": closures,
            "window": window_label,
            "source": meta.get("source", "historical"),
            "agent_demand": meta.get("agent_demand"),
            # Disruption-quality signal, previously only printed to the
            # build log and lost — a closure with a high truncated/dropped
            # count is a lower-confidence scenario and the UI/API consumer
            # has no other way to see that (found in review 2026-07-10).
            "truncated_vehicles": n_truncated,
            "dropped_vehicles":   n_dropped,
            "active_closure_edge_entries": active_closure_entries,
            "active_closure_edge_entries_by_seed": active_entries_by_seed,
            "closure_integrity": closure_integrity,
            **({"date": meta["date"], "begin": meta["begin"], "end": meta["end"]}
               if "date" in meta else
               {"start_date": meta["start_date"],
                "end_date_exclusive": meta["end_date_exclusive"],
                "days": meta["days"]}),
            "seeds": seed_count,
            "seed_set": seed_values,
            "simulation_mode": spec.simulation_mode,
            "network_build_id": spec.network_build_id,
            "demand_signature": sig,
            "build_id": meta.get("build_id"),
            "demand_build_key": meta.get("demand_build_key"),
        },
        "seed_health":       seed_health,
        "seed_health_flags": health_flags,
        **({"multi_day_validation": multi_day_validation}
           if multi_day_validation is not None else {}),
        "sensor_audit":      sensor_audit,
        "flows":      flows_out,
        "confidence": conf_out,
    }
    out_path = OUT_DIR / f"{name}.json"
    atomic_write_json(out_path, payload, separators=(",", ":"))
    _LAST_SCENARIO_OUTPUT = out_path
    print(f"Wrote {out_path}  ({len(flows_out)} edges)")

    # ── Manifest ───────────────────────────────────────────────────────────────
    index_path = OUT_DIR / "index.json"
    index = json.load(open(index_path)) if index_path.exists() else {"scenarios": []}
    index = index_for_current_demand(index, sig)
    index["scenarios"] = [s for s in index["scenarios"] if s["name"] != name]
    src_tag = " · Prognos" if meta.get("source") == "forecast" else ""
    index["scenarios"].append({
        "name": name, "label": label, "file": f"{name}.json",
        "closed_edges": close_edges,
        "closures": closures,
        "closure_integrity": closure_integrity,
        "scenario_spec": spec.to_dict(),
        "demand_signature": sig,
        "build_id": meta.get("build_id"),
        "demand_build_key": meta.get("demand_build_key"),
        "window": f"{window_label}{src_tag}",
    })
    index["scenarios"].sort(key=lambda s: s["name"])
    atomic_write_json(index_path, index, indent=2)
    print(f"Updated {index_path}  ({len(index['scenarios'])} scenarios)")
    try:
        cleanup_scenario_workspace(scratch_dir, keep=args.keep_scratch)
    except OSError as exc:
        # Publication is complete. Keep the result visible but record cleanup
        # failure instead of turning a successful simulation into a false
        # failed run.
        print(f"  WARNING could not remove scratch {scratch_dir}: {exc}")


def _tracked_main() -> None:
    """Run main() under the E1 run registry (see build_sumo_demand's
    _tracked_main for the rationale). Scenario products stay on their
    legacy web/data/scenarios paths; the run directory archives the
    scenario JSON + index, and the manifest ties them to code + inputs."""
    import sys

    from traffic_sim.ops import runs

    global _ACTIVE_RUN_DIR
    run = runs.start_run("scenario", inputs={"argv": sys.argv[1:]})
    _ACTIVE_RUN_DIR = run.dir
    try:
        main()
    except BaseException as exc:
        if _LAST_SCRATCH_DIR is not None:
            run.record("scratch", {
                "path": str(_LAST_SCRATCH_DIR),
                "kept": True,
                "status": "preserved_after_failure",
            })
        run.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _ACTIVE_RUN_DIR = None
    if _LAST_SCRATCH_DIR is not None:
        run.record("scratch", {
            "path": str(_LAST_SCRATCH_DIR),
            "kept": _LAST_SCRATCH_KEPT,
            "status": "kept" if _LAST_SCRATCH_KEPT else "removed",
        })
    index_path = OUT_DIR / "index.json"
    run.add_output(index_path)
    if index_path.exists():
        with open(index_path) as f:
            index = json.load(f)
        run.record("scenarios", [s.get("file") for s in
                                 index.get("scenarios", [])])
    # Archive the exact scenario produced by this process.  Choosing the
    # newest JSON from the shared output directory is racy and can select an
    # unrelated closure when two files have the same timestamp or when an
    # older run is copied into the directory after this one.
    for product in scenario_run_products(_LAST_SCENARIO_OUTPUT,
                                         _LAST_TRAJECTORY_OUTPUT):
        run.add_output(product)
    # G3: the baseline's seed health feeds the assembled validation
    # report — refresh it after every LIVE scenario rebuild (a staged E2
    # build refreshes on publish instead: the report must describe what
    # the UI actually serves, not a staging candidate).
    if OUT_DIR == Path("web/data/scenarios"):
        try:
            import validation_report
            validation_report.write_report()
        except Exception as exc:
            print(f"validation report: {type(exc).__name__}: {exc}")
    run.finish("succeeded")


def scenario_run_products(scenario_output: Path | None,
                          trajectory_output: Path | None) -> list[Path]:
    """Return the exact scenario artifacts produced by one process."""
    return [path for path in (scenario_output, trajectory_output)
            if path is not None and Path(path).is_file()]


if __name__ == "__main__":
    _tracked_main()
