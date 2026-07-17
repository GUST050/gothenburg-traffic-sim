"""
signal_optimize.py — IMPROVEMENT_PLAN.md Phase D2: off-the-shelf signal-timing
optimizers, evaluated via the D1 harness (signal_lab.py's machinery).

Runs SUMO's own tools against the currently calibrated demand for one time
window, then evaluates several conditions with the SAME MICRO metric machinery
against the SAME baseline:

  baseline              — the deployed net.net.xml's untouched synthetic
                          90 s-cycle programs (netconvert --tls.guess).
  adapted               — tlsCycleAdaptation.py's per-intersection Webster
                          cycle/green-split recalculation for this window.
  adapted_coordinated   — the above, plus tlsCoordinator.py's green-wave
                          offset coordination on top.
  actuated              — SUMO's built-in gap-based actuated TLS type, as
                          a no-optimization-tool reference point.
  delay_based            — SUMO's built-in delay-based actuated TLS type,
                          same reference-point role.
  regulation_compliant  — a TSFS 2014:30-informed conservative rebuild of
                          the baseline phase structure (IMPROVEMENT_PLAN.md D6, partial).
                          It is not Gothenburg's real plan and does not prove
                          regulatory compliance for a physical junction;
                          see signal_regulation.py's assumptions.

actuated/delay_based are network-BUILD-time choices (verified: `sumo
--tls.default-type` does not exist, only `netconvert --tls.default-type`
does), so each needs its own network file — built from the SAME plain
nod/edg XML build_sumo_net.py uses, verified to produce byte-identical
edge IDs to the deployed network.

HONESTY (IMPROVEMENT_PLAN.md's own instruction for this step): every relative
improvement here is measured against a SYNTHETIC 90 s-cycle GUESS
(netconvert --tls.guess), not Gothenburg's real signal plans (IMPROVEMENT_PLAN.md D6,
not done). Expect possibly LARGE relative wins for exactly that reason —
absolute numbers are always reported alongside relative ones, and every
result row carries tls_provenance="synthetic" with an explicit caveat
string, matching signal_lab.py's (D1) same honesty field.

Usage:
  python3 signal_optimize.py [--window-start 07:00] [--window-end 09:00]
      [--seeds 3] [--out PATH]

Requires everything signal_lab.py requires, plus sumo/plain.nod.xml +
sumo/plain.edg.xml (written by build_sumo_net.py) for the actuated/
delay_based network variants.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import dataclasses
import hashlib
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime

from traffic_sim.simulation import metrics as cm
import run_scenario as rs
from signal_lab import (TLS_PROVENANCE, merge_route_files, net_fingerprint,
                        sumo_version, tls_plan_diff, tls_timing_schedule,
                        window_offsets_s)
from signal_regulation import (build_demand_weighted_tls,
                               build_regulation_compliant_tls,
                               enforce_timing_minimums, timing_certificate,
                               write_signal_plan_artifact)
from suggest_closure_time import aggregate_seed_metrics
from traffic_sim.core.contracts import load_scenario_spec

CAVEAT = ("Measured against a SYNTHETIC netconvert --tls.guess baseline "
         "(90 s uniform cycle), not Gothenburg's real signal plans (IMPROVEMENT_PLAN.md "
         "D6, not yet imported). A large relative improvement here reflects "
         "how naive the baseline is, not necessarily real-world quality — "
         "read the absolute numbers, not just the percentages.")

BUILTIN_TLS_TYPES = ["actuated", "delay_based"]
SIGNAL_CONDITION_NAMES = (
    "baseline", "adapted", "adapted_coordinated", "actuated", "delay_based",
    "regulation_compliant", "demand_weighted_coordinated",
)
SIGNAL_CONDITION_COUNT = len(SIGNAL_CONDITION_NAMES)

# This condition remains a synthetic program. It applies TSFS-informed
# conservative timing rules to incomplete SUMO geometry, but lacks the real
# junction conflict matrix, pedestrian/cycle phases, controller logic, and
# authority validation required to claim compliance for a physical junction.
REGULATION_PROVENANCE = "synthetic_tsfs_informed"

# Every condition whose add_paths trace back to signal_regulation.
# rebuild_phases() (via enforce_timing_minimums for "adapted"/
# "adapted_coordinated", build_demand_weighted_tls for
# "demand_weighted_coordinated", or build_regulation_compliant_tls for
# "regulation_compliant" itself) — found in review 2026-07-12:
# tls_provenance_by_condition only special-cased "regulation_compliant" by
# name, so these three conditions were silently reported under the same
# plain "synthetic" label as netconvert's raw arbitrary guess (baseline/
# actuated/delay_based), even though they share the same TSFS-grounded
# rebuild this project's own honesty discipline says must be labelled
# distinctly.
TSFS_INFORMED_CONDITIONS = frozenset({
    "adapted", "adapted_coordinated", "regulation_compliant", "demand_weighted_coordinated",
})


@dataclasses.dataclass(frozen=True)
class SignalRun:
    """One condition's result for one shared seed/demand-variant pair.

    Signal comparisons must match these records by ``pair_key`` rather than
    compare independently aggregated means. The explicit key makes the
    common-random-numbers design auditable in the result JSON.
    """

    seed: int
    demand_variant: str
    metrics: cm.DisruptionMetrics

    @property
    def pair_key(self) -> str:
        return f"{self.seed}:{self.demand_variant}"


def paired_comparison(baseline_runs: list[SignalRun],
                      candidate_runs: list[SignalRun]) -> dict:
    """Summarize candidate-minus-baseline deltas for matched simulations.

    A condition may only be ranked when every run has the same seed and
    demand variant as the baseline. This prevents a changed variant ordering
    or partial failed batch from silently becoming an unpaired comparison.
    """
    baseline_by_key = {run.pair_key: run for run in baseline_runs}
    candidate_by_key = {run.pair_key: run for run in candidate_runs}
    if len(baseline_by_key) != len(baseline_runs) or \
       len(candidate_by_key) != len(candidate_runs):
        raise ValueError("duplicate seed/demand-variant pair in signal results")
    if baseline_by_key.keys() != candidate_by_key.keys():
        raise ValueError("signal conditions do not share identical seed/demand pairs")

    keys = sorted(baseline_by_key)
    time_loss_deltas = [candidate_by_key[key].metrics.total_time_loss_s -
                        baseline_by_key[key].metrics.total_time_loss_s
                        for key in keys]
    teleport_deltas = [candidate_by_key[key].metrics.teleport_total -
                       baseline_by_key[key].metrics.teleport_total
                       for key in keys]
    unfinished_deltas = [candidate_by_key[key].metrics.unfinished_trips -
                         baseline_by_key[key].metrics.unfinished_trips
                         for key in keys]
    return {
        "pair_keys": keys,
        "n_pairs": len(keys),
        "time_loss_delta_s": time_loss_deltas,
        "mean_time_loss_delta_s": statistics.fmean(time_loss_deltas),
        "median_time_loss_delta_s": statistics.median(time_loss_deltas),
        "stddev_time_loss_delta_s": (
            statistics.stdev(time_loss_deltas) if len(time_loss_deltas) > 1 else 0.0),
        "min_time_loss_delta_s": min(time_loss_deltas),
        "max_time_loss_delta_s": max(time_loss_deltas),
        "teleport_delta": teleport_deltas,
        "unfinished_trip_delta": unfinished_deltas,
    }


def run_tls_cycle_adaptation(home: Path, route_path: Path, begin_s: int,
                             out_path: Path, program_id: str = "a") -> None:
    tool = home / "tools" / "tlsCycleAdaptation.py"
    cmd = [sys.executable, str(tool),
          "-n", str(rs.NET_PATH.resolve()), "-r", str(route_path.resolve()),
          "-b", str(begin_s), "-o", str(out_path.resolve()), "-p", program_id]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        print(res.stdout[-2000:], res.stderr[-2000:])
        sys.exit("tlsCycleAdaptation.py failed")


def run_tls_coordinator(home: Path, route_path: Path, adapted_path: Path,
                        out_path: Path) -> None:
    tool = home / "tools" / "tlsCoordinator.py"
    cmd = [sys.executable, str(tool),
          "-n", str(rs.NET_PATH.resolve()), "-r", str(route_path.resolve()),
          "-a", str(adapted_path.resolve()), "-o", str(out_path.resolve())]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if res.returncode != 0:
        print(res.stdout[-2000:], res.stderr[-2000:])
        sys.exit("tlsCoordinator.py failed")


def merge_demand_variants(variants: list[Path], out_path: Path) -> int:
    """Create a deterministic, equally weighted TLS-optimizer demand set.

    The direction-split q10/q50/q90 route files reuse vehicle IDs. Prefix
    them while merging so SUMO's timing tools see all uncertainty variants
    rather than silently treating q50 as the sole optimization target.
    """
    return merge_route_files(variants, out_path, prefix="variant", require_nonempty=True)


def build_alt_type_net(home: Path, tls_type: str, out_path: Path) -> None:
    """A network identical to the deployed one except every guessed TLS
    program uses `tls_type` instead of "static" — a netconvert-time choice
    (verified: no equivalent sumo runtime flag exists). Reuses the exact
    plain nod/edg inputs and flags build_sumo_net.py's own netconvert call
    uses (--tls.guess true, --geometry.remove false) so the only
    difference is --tls.default-type."""
    nod_path = rs.SUMO_DIR / "plain.nod.xml"
    edg_path = rs.SUMO_DIR / "plain.edg.xml"
    if not (nod_path.exists() and edg_path.exists()):
        sys.exit(f"{nod_path} / {edg_path} not found — run build_sumo_net.py first")
    cmd = [str(home / "bin" / "netconvert"),
          "-n", str(nod_path), "-e", str(edg_path), "-o", str(out_path.resolve()),
          "--tls.guess", "true", "--tls.default-type", tls_type,
          "--geometry.remove", "false", "--no-warnings", "true"]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         env={"SUMO_HOME": str(home), "PATH": "/usr/bin:/bin"},
                         timeout=300)
    if res.returncode != 0:
        print(res.stderr[-2000:])
        sys.exit(f"netconvert failed for --tls.default-type {tls_type}")


def non_tls_network_fingerprint(net_path: Path) -> str:
    """Hash every network element except TLS programs themselves.

    D2's alternate networks are permitted to change a ``<tlLogic>`` program
    type, but no topology, geometry, lane, connection, junction, or other
    network detail. Canonicalising the full XML tree excluding only tlLogic
    makes that contract executable instead of assuming matching edge IDs are
    enough.
    """
    def canonical(element: ET.Element):
        children = [canonical(child) for child in element]
        return (element.tag, tuple(sorted(element.attrib.items())),
                tuple(sorted(children, key=lambda value: json.dumps(value))))

    root = ET.parse(net_path).getroot()
    payload = [canonical(child) for child in root if child.tag != "tlLogic"]
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def scenario_spec_key(spec) -> str:
    """Short deterministic key for a structured signal-study identity."""
    if spec is None:
        return "legacy"
    payload = json.dumps(spec.to_dict(), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha1(payload).hexdigest()[:12]


def signal_artifact_label(window_start: str, window_end: str, demand_sig: str,
                          net_fp: str, study_key: str | None = None) -> str:
    """Content-addressed label for cached signal-timing artifacts
    (tls_adapted, tls_coordinated, alternate-type networks) — folds in
    demand_signature, net_fingerprint and (for structured jobs) the complete
    study key so a stale artifact built from a DIFFERENT demand, network or
    seed/variant plan can never be silently reused just because the window
    happens to match. Found in external review 2026-07-11
    (NEW_CHANGES_REVIEW_2026-07-11.md section 6.1): the label used to be
    the window alone, so recalibrating demand or rebuilding the network
    without changing --window-start/--window-end left a stale
    tls_adapted_<label>.add.xml on disk that a later run would silently
    reuse, evaluated against demand/geometry it was never actually built
    from."""
    window = f"{window_start.replace(':', '')}_{window_end.replace(':', '')}"
    suffix = f"_{study_key}" if study_key else ""
    return f"{window}_{demand_sig}_{net_fp}{suffix}"


def build_signal_conditions(home: Path, variants: list[Path], begin_s: int,
                            end_s: int, label: str) -> dict[str, dict]:
    """Build (or reuse, under the fingerprinted `label`) the
    tlsCycleAdaptation/tlsCoordinator outputs and the actuated/delay_based
    alternate networks, then return the 6-condition dict D2 and D3 both
    evaluate. Shared by signal_optimize.py and signal_meso_screen.py so
    they can never silently diverge on which conditions exist or how
    artifacts get cached — found in external review 2026-07-11 as
    duplicated, behaviorally-INCONSISTENT code: signal_optimize.py always
    rebuilt every artifact unconditionally while signal_meso_screen.py
    cached by bare filename existence with no freshness check at all (the
    actual bug fixed here). A single shared, correctly-fingerprinted
    implementation fixes both problems at once.

    regulation_compliant (IMPROVEMENT_PLAN.md D6, partial) is cached under
    net_fingerprint ALONE, not `label` — unlike adapted/coordinated/alt-
    nets, it depends only on network geometry (TSFS 2014:30's minimums and
    the separering-i-tid clearance formula, never on demand or window), so
    it is correctly reused across every window/demand combination sharing
    the same network rather than rebuilt per-label for no reason."""
    ensemble_path = rs.SUMO_DIR / f"tls_adaptation_demand_{label}.rou.xml"
    if not ensemble_path.exists():
        print(f"  merging {len(variants)} demand variant(s) for robust timing adaptation …")
        merge_demand_variants(variants, ensemble_path)
    adapted_raw_path = rs.SUMO_DIR / f"tls_adapted_ensemble_{label}.add.xml"
    if not adapted_raw_path.exists():
        print("  running tlsCycleAdaptation.py …")
        run_tls_cycle_adaptation(home, ensemble_path, begin_s, adapted_raw_path)
    adapted_path = rs.SUMO_DIR / f"tls_adapted_safe_{label}.add.xml"
    if not adapted_path.exists():
        print("  enforcing TSFS-informed timing minimums on adapted plan …")
        enforce_timing_minimums(rs.NET_PATH, adapted_raw_path, adapted_path)
    coordinated_path = rs.SUMO_DIR / f"tls_coordinated_safe_{label}.add.xml"
    if not coordinated_path.exists():
        print("  running tlsCoordinator.py …")
        run_tls_coordinator(home, ensemble_path, adapted_path, coordinated_path)
    demand_weighted_path = rs.SUMO_DIR / f"tls_demand_weighted_{label}.add.xml"
    if not demand_weighted_path.exists():
        print("  allocating green time jointly within each junction …")
        # begin_s/end_s (review 2026-07-12): without this, green-time
        # allocation reflected route_path's FULL demand file (typically a
        # whole day) rather than the actual analysis window being
        # optimized for and compared against.
        build_demand_weighted_tls(rs.NET_PATH, ensemble_path, demand_weighted_path,
                                  begin_s=begin_s, end_s=end_s)
    demand_weighted_coordinated_path = rs.SUMO_DIR / f"tls_demand_weighted_coordinated_{label}.add.xml"
    if not demand_weighted_coordinated_path.exists():
        print("  coordinating demand-weighted junction offsets …")
        run_tls_coordinator(home, ensemble_path, demand_weighted_path,
                            demand_weighted_coordinated_path)
    alt_nets: dict[str, Path] = {}
    for tls_type in BUILTIN_TLS_TYPES:
        net_path = rs.SUMO_DIR / f"net_{tls_type}_{label}.net.xml"
        if not net_path.exists():
            print(f"  building alternate network (--tls.default-type {tls_type}) …")
            build_alt_type_net(home, tls_type, net_path)
        alt_nets[tls_type] = net_path
    baseline_non_tls = non_tls_network_fingerprint(rs.NET_PATH)
    for tls_type, net_path in alt_nets.items():
        if non_tls_network_fingerprint(net_path) != baseline_non_tls:
            sys.exit(
                f"alternate {tls_type} network changed non-TLS structure; "
                "refusing an invalid signal comparison"
            )
    regulation_path = rs.SUMO_DIR / f"tls_regulation_{net_fingerprint(rs.NET_PATH)}.add.xml"
    if not regulation_path.exists():
        print("  building TSFS-informed synthetic TLS program …")
        build_regulation_compliant_tls(rs.NET_PATH, regulation_path)
    return {
        "baseline": {"net_path": rs.NET_PATH, "add_paths": []},
        "adapted": {"net_path": rs.NET_PATH, "add_paths": [adapted_path]},
        "adapted_coordinated": {"net_path": rs.NET_PATH,
                                "add_paths": [adapted_path, coordinated_path]},
        "actuated": {"net_path": alt_nets["actuated"], "add_paths": []},
        "delay_based": {"net_path": alt_nets["delay_based"], "add_paths": []},
        "regulation_compliant": {"net_path": rs.NET_PATH, "add_paths": [regulation_path]},
        "demand_weighted_coordinated": {
            "net_path": rs.NET_PATH,
            "add_paths": [demand_weighted_path, demand_weighted_coordinated_path],
        },
    }


def condition_net_fingerprints(conditions: dict[str, dict]) -> dict[str, str]:
    """net_fingerprint PER CONDITION, not just the baseline network — found
    in review 2026-07-11: actuated/delay_based run against their OWN
    rebuilt network files, so a single top-level net_fingerprint field
    (hashing only rs.NET_PATH) couldn't detect if one of those alternate
    networks changed between two runs while the baseline stayed the same."""
    seen: dict[Path, str] = {}
    out = {}
    for name, cfg in conditions.items():
        p = cfg["net_path"]
        if p not in seen:
            seen[p] = net_fingerprint(p)
        out[name] = seen[p]
    return out


def condition_non_tls_fingerprints(conditions: dict[str, dict]) -> dict[str, str]:
    """Non-TLS structural fingerprints recorded alongside full net hashes."""
    seen: dict[Path, str] = {}
    out = {}
    for name, cfg in conditions.items():
        path = cfg["net_path"]
        if path not in seen:
            seen[path] = non_tls_network_fingerprint(path)
        out[name] = seen[path]
    return out


def condition_tls_provenance(conditions: dict[str, dict]) -> dict[str, str]:
    """REGULATION_PROVENANCE for every TSFS-grounded condition, plain
    TLS_PROVENANCE otherwise — found in review 2026-07-12: this used to
    special-case only "regulation_compliant" by name, so "adapted",
    "adapted_coordinated", and "demand_weighted_coordinated" (which ALSO
    go through signal_regulation.rebuild_phases(), via
    enforce_timing_minimums/build_demand_weighted_tls) were silently
    reported under the same plain "synthetic" label as netconvert's raw
    arbitrary guess."""
    return {name: (REGULATION_PROVENANCE if name in TSFS_INFORMED_CONDITIONS else TLS_PROVENANCE)
            for name in conditions}


def run_condition(*, net_path: Path, add_paths: list[Path], variants: list[Path],
                  seeds: int, begin_s: int, end_s: int, home: Path,
                  micro: bool = True,
                  vehroute_output: Path | None = None,
                  vehroute_outputs: list[Path] | None = None,
                  seed_plan: list[tuple[int, Path]] | None = None,
                  run_label: str = "signal",
                  closed_edges: list[str] | None = None,
                  work_dir: Path | None = None,
                  seed_workers: int = 1,
                  ) -> tuple[cm.DisruptionMetrics, list[SignalRun]]:
    """micro=True (default, every existing caller's behaviour unchanged) for
    D2's own ground-truth comparisons; micro=False reruns the SAME five
    conditions in meso for D3's screening-feasibility question (IMPROVEMENT_PLAN.md).

    ``vehroute_outputs`` (IMPROVEMENT_PLAN.md D4) requests one vehroute artifact per
    seed, so a downstream optimizer can see every evaluated demand variant.
    ``vehroute_output`` is retained for callers that genuinely need only the
    first seed. The two forms are mutually exclusive.

    ``closed_edges`` (found in review 2026-07-12): when this condition's
    add_paths include an active closure (D4's signal_closure_combine.py is
    the only caller that ever closes real edges), pass the closed edge IDs
    so each seed's run also requests edgeData output and computes
    closed_edge_throughput for cm.build_metrics(). Without this, closure_
    metrics.disqualification_reasons()'s active-closure-edge-throughput
    leak check — added specifically to catch a vehicle driving through an
    edge that was supposed to be closed — stayed permanently None (and
    therefore permanently inert) for every run_condition-based caller,
    including the one that actually closes edges."""
    if vehroute_output is not None and vehroute_outputs is not None:
        raise ValueError("provide vehroute_output or vehroute_outputs, not both")
    if vehroute_outputs is not None and len(vehroute_outputs) != seeds:
        raise ValueError("vehroute_outputs must contain exactly one path per seed")
    if seed_workers < 1:
        raise ValueError("seed_workers must be >= 1")
    if seed_plan is None:
        seed_plan = rs.seed_variant_plan(variants, seeds=seeds)
    if len(seed_plan) != seeds:
        raise ValueError("seed_plan must contain exactly one route per seed")
    base_dir = Path(work_dir) if work_dir is not None else rs.SUMO_DIR
    if work_dir is not None:
        base_dir.mkdir(parents=True, exist_ok=False)
    scratch: list[Path] = []
    jobs = []
    for s, (seed, route_path) in enumerate(seed_plan):
        seed_dir = base_dir / f"seed-{seed}" if work_dir is not None else base_dir
        if work_dir is not None:
            seed_dir.mkdir(parents=True, exist_ok=False)
        run_add_paths = add_paths
        ed_file = None
        if closed_edges:
            safe_label = "".join(c if c.isalnum() or c in "_-" else "_" for c in run_label)
            stem = f"{safe_label}_{route_path.stem}_{seed}"
            ed_file = seed_dir / f"edgedata_{stem}.xml"
            ed_add_path = seed_dir / f"edgedata_add_{stem}.add.xml"
            rs.write_edgedata_additional(ed_add_path, ed_file, end_s, begin_s=begin_s)
            run_add_paths = add_paths + [ed_add_path]
            scratch.append(ed_add_path)
        # flush_s=0: this is always a bounded time-of-day window experiment
        # (D2 micro, D3 meso) — the default 3600s meso flush would admit
        # departures up to an hour past the requested window into the
        # "window" total (verified empirically 2026-07-11: 55%
        # contamination at the default window with the old default). A
        # vehicle that departed in-window but hasn't finished by end_s is
        # honestly tracked via unfinished_trips, not silently padded in.
        jobs.append({
            "seed": seed, "route_path": route_path, "run_add_paths": run_add_paths,
            "end_s": end_s, "home": home, "micro": micro, "begin_s": begin_s,
            "net_path": net_path, "ed_file": ed_file, "seed_dir": seed_dir,
            "vehroute_output": (vehroute_outputs[s]
                                 if vehroute_outputs is not None
                                 else vehroute_output if s == 0 else None),
        })
        if ed_file is not None:
            scratch.append(ed_file)

    def run_one(job: dict) -> tuple[int, cm.DisruptionMetrics, SignalRun, list[Path]]:
        metric_paths = rs.run_sumo(
            job["seed"], job["route_path"], job["run_add_paths"], job["end_s"],
            job["home"], micro=job["micro"], metrics=True,
            begin_s=job["begin_s"], net_path=job["net_path"], flush_s=0,
            vehroute_output=job["vehroute_output"], run_label=run_label,
            **({"work_dir": job["seed_dir"]} if work_dir is not None else {}))
        closed_edge_throughput = None
        if job["ed_file"] is not None:
            closed_edge_throughput = cm.read_closed_edge_throughput(
                job["ed_file"], set(closed_edges))
        metrics = cm.build_metrics(
            metric_paths["tripinfo"], metric_paths["statistics"],
            summary_path=metric_paths["summary"],
            closed_edge_throughput=closed_edge_throughput)
        return (job["seed"], metrics,
                SignalRun(seed=job["seed"], demand_variant=job["route_path"].name,
                          metrics=metrics), list(metric_paths.values()))

    if seed_workers == 1 or len(jobs) == 1:
        completed = [run_one(job) for job in jobs]
    else:
        executor = ThreadPoolExecutor(max_workers=min(seed_workers, len(jobs)),
                                      thread_name_prefix="signal-seed")
        futures = [executor.submit(run_one, job) for job in jobs]
        try:
            completed = [future.result() for future in as_completed(futures)]
        except BaseException:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)
    completed.sort(key=lambda item: item[0])
    per_seed_metrics = [item[1] for item in completed]
    per_seed_runs = [item[2] for item in completed]
    for item in completed:
        scratch.extend(item[3])
    for p in scratch:
        p.unlink(missing_ok=True)
    if work_dir is not None:
        # Empty seed directories are disposable; the parent owner decides
        # whether to remove the complete condition workspace after validation.
        for seed_dir in sorted(base_dir.glob("seed-*")):
            try:
                seed_dir.rmdir()
            except OSError:
                pass
    return aggregate_seed_metrics(per_seed_metrics), per_seed_runs


def relative_pct(baseline_val: float, candidate_val: float) -> float | None:
    if baseline_val == 0:
        return None
    return round(100 * (candidate_val - baseline_val) / baseline_val, 1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--window-start", default="07:00", metavar="HH:MM")
    p.add_argument("--window-end", default="09:00", metavar="HH:MM")
    p.add_argument("--scenario-spec", default=None, metavar="PATH",
                   help="Load a validated ScenarioSpec; its analysis_window "
                        "supplies the study period.")
    p.add_argument("--seeds", type=int, default=3,
                   help="Fixed Monte Carlo seeds, 1000..1000+seeds-1 (default 3).")
    p.add_argument("--seed-workers", type=int, default=1,
                   help="Concurrent SUMO seeds per condition (default 1; benchmark first).")
    p.add_argument("--keep-scratch", action="store_true",
                   help="Keep isolated condition workspaces after completion.")
    p.add_argument("--out", type=Path, default=None,
                   help="Result JSON path (default: sumo/signal_optimize_<window>.json).")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.seeds < 1:
        sys.exit("--seeds must be >= 1")
    if args.seed_workers < 1:
        sys.exit("--seed-workers must be >= 1")
    home = rs.sumo_home()
    rs.SUMO_DIR.mkdir(parents=True, exist_ok=True)

    with open(rs.SUMO_DIR / "demand_meta.json") as f:
        meta = json.load(f)
    total_duration_s = meta["n_intervals"] * 900
    spec = None
    if args.scenario_spec:
        try:
            spec = load_scenario_spec(Path(args.scenario_spec))
            rs.validate_scenario_spec(
                spec, meta=meta, duration_s=total_duration_s,
                network_path=rs.NET_PATH)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.exit(f"invalid ScenarioSpec: {exc}")
        if spec.closures:
            sys.exit("signal_optimize cannot evaluate closures; use "
                     "signal_closure_combine for a closure ScenarioSpec")
        if spec.analysis_window is None:
            sys.exit("signal ScenarioSpec requires analysis_window")
        try:
            measure_start = datetime.fromisoformat(
                spec.analysis_window.measure_start.replace("Z", "+00:00"))
            measure_end = datetime.fromisoformat(
                spec.analysis_window.measure_end.replace("Z", "+00:00"))
        except ValueError as exc:
            sys.exit(f"invalid ScenarioSpec analysis_window: {exc}")
        args.window_start = measure_start.strftime("%H:%M")
        args.window_end = measure_end.strftime("%H:%M")
    try:
        begin_s, end_s = window_offsets_s(meta["epoch_sim"], args.window_start,
                                          args.window_end)
    except ValueError as exc:
        sys.exit(str(exc))
    if not (0 <= begin_s < end_s <= total_duration_s):
        sys.exit(f"window {args.window_start}-{args.window_end} falls outside "
                 f"the calibrated demand period (0-{total_duration_s / 3600:.1f}h)")

    variants = rs.demand_variants(meta)
    try:
        seed_plan = rs.seed_variant_plan(variants, spec=spec, seeds=args.seeds)
    except ValueError as exc:
        sys.exit(f"invalid ScenarioSpec demand mapping: {exc}")
    args.seeds = len(seed_plan)
    demand_sig = rs.demand_signature(meta)
    baseline_net_fp = net_fingerprint(rs.NET_PATH)
    label = signal_artifact_label(args.window_start, args.window_end,
                                  demand_sig, baseline_net_fp,
                                  scenario_spec_key(spec) if spec is not None else None)
    print(f"Signal optimize: {args.window_start}-{args.window_end} window, "
         f"{args.seeds} seed(s), MICRO — {SIGNAL_CONDITION_COUNT} conditions "
         "vs synthetic baseline")

    conditions = build_signal_conditions(home, variants, begin_s, end_s, label)
    net_fps = condition_net_fingerprints(conditions)
    non_tls_net_fps = condition_non_tls_fingerprints(conditions)
    # Validate every TSFS-grounded phase plan before it is used in a
    # comparison. Raw netconvert/built-in conditions remain explicitly
    # uncertified references: forcing them through the regulation certificate
    # makes the optimizer abort before it can compare anything, because the
    # imported 3 s yellow phases are precisely what the regulated conditions
    # were built to improve. Offset-only coordinator files stay paired with
    # their phase file in both the certificate and the published artifact.
    plan_certificates = {}
    plan_artifacts = {}
    for condition, cfg in conditions.items():
        phase_path = cfg["add_paths"][0] if cfg["add_paths"] else cfg["net_path"]
        overlay_paths = tuple(cfg["add_paths"][1:])
        artifact_path = rs.SUMO_DIR / f"signal_plan_{label}_{condition}.json"
        write_signal_plan_artifact(
            cfg["net_path"], phase_path, artifact_path,
            overlay_paths=overlay_paths,
            provenance=condition_tls_provenance({condition: cfg})[condition])
        plan_artifacts[condition] = str(artifact_path)
        if condition in TSFS_INFORMED_CONDITIONS:
            plan_certificates[condition] = timing_certificate(
                cfg["net_path"], phase_path, overlay_paths=overlay_paths,
                provenance=condition_tls_provenance({condition: cfg})[condition])
            if plan_certificates[condition]["status"] != "pass":
                raise RuntimeError(
                    f"signalplan {condition} klarade inte säkerhetscertifikatet: "
                    + "; ".join(plan_certificates[condition]["errors"][:3]))
        else:
            plan_certificates[condition] = {
                "schema_version": 1,
                "status": "not_applicable",
                "provenance": condition_tls_provenance({condition: cfg})[condition],
                "plan_file": str(phase_path),
                "overlay_files": [str(path) for path in overlay_paths],
                "reason": "oregelad referens används endast för jämförelse",
            }

    batch_workspace = Path(tempfile.mkdtemp(prefix=".signal_optimize_",
                                             dir=str(rs.SUMO_DIR)))
    results = {}
    condition_runs: dict[str, list[SignalRun]] = {}
    t_total = time.time()
    try:
        for name, cfg in conditions.items():
            print(f"  running condition '{name}' ({args.seeds} seeds) …")
            t0 = time.time()
            metrics, per_seed_runs = run_condition(
                net_path=cfg["net_path"], add_paths=cfg["add_paths"], variants=variants,
                seeds=args.seeds, begin_s=begin_s, end_s=end_s, home=home,
                seed_plan=seed_plan,
                run_label=f"d2_{name}",
                work_dir=batch_workspace / name,
                seed_workers=args.seed_workers)
            elapsed = time.time() - t0
            print(f"    {elapsed:.0f}s: timeLoss={metrics.total_time_loss_s:.0f}s, "
                 f"{metrics.trip_count} trips, {metrics.teleport_total} teleports")
            results[name] = {"metrics": dataclasses.asdict(metrics),
                             "per_seed_time_loss_s": [run.metrics.total_time_loss_s
                                                       for run in per_seed_runs],
                             "runs": [{"seed": run.seed,
                                       "demand_variant": run.demand_variant,
                                       "metrics": dataclasses.asdict(run.metrics)}
                                      for run in per_seed_runs],
                             "elapsed_s": round(elapsed, 1)}
            condition_runs[name] = per_seed_runs
        total_elapsed = time.time() - t_total
    finally:
        # Keep the workspace until the complete result has been validated and
        # atomically written below. If a later comparison or serialization
        # fails, the artifacts remain available for diagnosis.
        pass

    baseline_metrics = cm.DisruptionMetrics(**results["baseline"]["metrics"])
    comparisons = {}
    for name in conditions:
        if name == "baseline":
            continue
        candidate_metrics = cm.DisruptionMetrics(**results[name]["metrics"])
        comparison = cm.compare_metrics(baseline_metrics, candidate_metrics)
        comparisons[name] = {
            **dataclasses.asdict(comparison),
            "relative_time_loss_pct": relative_pct(
                baseline_metrics.total_time_loss_s, candidate_metrics.total_time_loss_s),
            "paired": paired_comparison(condition_runs["baseline"],
                                         condition_runs[name]),
        }
        flag = " DISQUALIFIED" if comparison.candidate_disqualified else ""
        # The paired (common-random-numbers, seed/demand-variant-matched)
        # comparison was computed above but, before this fix, sat inert in
        # the JSON — never printed, never surfaced to serve.py/the UI,
        # never checked against the aggregate number it was built to
        # cross-validate (found in review 2026-07-12). Printed alongside
        # the aggregate delta here, plus an explicit warning when the two
        # disagree in SIGN (aggregate says improved, paired median says
        # worse, or vice versa) — a threshold-free, always-defensible
        # check; deciding a numeric "practical equivalence" tolerance is a
        # research-methodology call for whoever reads these numbers, not
        # something to invent here.
        paired = comparisons[name]["paired"]
        sign_disagrees = (
            comparison.delta_time_loss_s != 0 and paired["median_time_loss_delta_s"] != 0 and
            (comparison.delta_time_loss_s > 0) != (paired["median_time_loss_delta_s"] > 0)
        )
        warn = "  ⚠ aggregate/paired-median DISAGREE in sign" if sign_disagrees else ""
        print(f"  {name}: Δ={comparison.delta_time_loss_s:+.0f}s "
             f"({comparisons[name]['relative_time_loss_pct']}%){flag} "
             f"[paired median Δ={paired['median_time_loss_delta_s']:+.0f}s, "
             f"stddev={paired['stddev_time_loss_delta_s']:.0f}s over "
             f"{paired['n_pairs']} pairs]{warn}")

    # IMPROVEMENT_PLAN.md D5: per-junction cycle/split/offset diff, adapted_coordinated
    # (the full pipeline: cycle adaptation + green-wave offsets) against the
    # deployed baseline net — reuses the exact add_paths build_signal_
    # conditions() already produced for that condition rather than
    # re-deriving the adapted/coordinated paths a second time.
    adapted_path, coordinated_path = conditions["adapted_coordinated"]["add_paths"]
    plan_diff = tls_plan_diff(rs.NET_PATH, adapted_path, coordinated_path)
    timing_schedule = tls_timing_schedule(adapted_path, coordinated_path, rs.NET_PATH)

    result = {
        "method": "IMPROVEMENT_PLAN.md Phase D2: off-the-shelf signal-timing optimizers vs D1 baseline",
        "window_start": args.window_start, "window_end": args.window_end,
        "begin_s": begin_s, "end_s": end_s, "seeds": args.seeds,
        # Imported from signal_lab.py (D1) rather than a second hardcoded
        # "synthetic" literal — found in self-review 2026-07-11: the two
        # copies would silently stop matching the moment IMPROVEMENT_PLAN.md D6 flips
        # D1's TLS_PROVENANCE to "city-configured" but this file's literal
        # string was never updated alongside it.
        "tls_provenance": TLS_PROVENANCE, "caveat": CAVEAT,
        "recommendation_allowed": TLS_PROVENANCE != "synthetic",
        # Per-condition override: every TSFS-grounded condition is
        # explicitly labelled, not just "regulation_compliant" by name.
        "tls_provenance_by_condition": condition_tls_provenance(conditions),
        "net_fingerprint": baseline_net_fp,
        "net_fingerprints_by_condition": net_fps,
        "non_tls_network_fingerprints_by_condition": non_tls_net_fps,
        "demand_signature": demand_sig,
        "scenario_spec": spec.to_dict() if spec is not None else None,
        "sumo_version": sumo_version(home),
        "command": sys.argv,
        "conditions": results,
        "comparisons_vs_baseline": comparisons,
        "tls_plan_diff": plan_diff,
        "tls_timing_schedule": timing_schedule,
        "signal_plan_certificates": plan_certificates,
        "signal_plan_artifacts": plan_artifacts,
        "elapsed_s": round(total_elapsed, 1),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    out_path = args.out or rs.SUMO_DIR / f"signal_optimize_{label}.json"
    rs.atomic_write_json(out_path, result, indent=2)
    print(f"Wrote {out_path}  ({total_elapsed:.0f}s total)")
    if args.keep_scratch:
        print(f"  kept signal workspace: {batch_workspace}")
    else:
        shutil.rmtree(batch_workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
