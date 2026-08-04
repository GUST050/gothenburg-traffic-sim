#!/usr/bin/env python3
"""Paired cold-vs-warm equivalence harness for the monthly backend.

Runs the SAME frozen schedule, demand, seeds and mode through the production
cold arm and the opt-in warm arm in ISOLATED workspaces, then compares the
canonical production observation from each. It publishes a passing equivalence
record — and therefore reusable cache material — only when every semantic check
passes; any mismatch produces honest fail evidence and no usable cache.

This is the ONLY caller permitted to set `warm_execution=True`.

Revision 1 is not executable: `--execute` requires an approval token bound to
the frozen manifest content key, and no such approval exists yet. Running this
without it performs the frozen-input checks and stops.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import resource
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from traffic_sim.simulation.monthly_warm_state import (
    observation_digest, parse_prefix_evidence, semantic_payload)

REPO_ROOT = Path(__file__).resolve().parent
# v16 is the live, frozen-but-unapproved contract. It preserves v14's proven
# population reconstruction, removes v15's refuted event-dependent lookahead,
# and normalizes high-precision whole-vehicle values with SUMO-compatible
# decimal half-up formatting instead of Python's ties-to-even ``round``.
#
# v13 established the source-corrected mechanism. Official SUMO source showed
# why v12 failed: mesoscopic tripinfo uses a private accumulator omitted by
# save/load, while TraCI reports waiting time. v13 transports that accumulator
# through high-precision unfinished prefix tripinfo and rounds each reconstructed
# whole vehicle once to production precision.
#
# v8 was SUPERSEDED in process-free review, never approved and never executed.
# Its binding was right — it closed v7's omission — but its regression suite
# pinned a predecessor TEST file as immutable evidence and asserted its own
# currency directly, so a successor could not land without editing artifacts
# that are supposed to be frozen. v9 pins predecessor tools and manifests only,
# and proves currency in the generic current suite instead.
#
# v7 was REJECTED in process-free review, never approved and never executed: its
# resolver repair was correct, but its fingerprints omitted two regressions that
# give the fix its meaning, so those tests could have been weakened without
# invalidating the contract.
#
# Earlier contracts, none adoptable: v6 was EXECUTED by LUNA-WARM-14 and failed
# with the cause named — a bare `import traci` that could never resolve, so
# warming had never started. v5 was EXECUTED by LUNA-WARM-12 and failed readably,
# naming the bootstrap, but the production call did not forward the attempt, so
# the bootstrap's own reason was lost. v4 was EXECUTED by LUNA-WARM-10 and failed
# honestly but uninformatively — three silent cold fallbacks with no recorded
# cause — which is why v5 binds a structured warm-attempt contract. v1 was spent
# by LUNA-WARM-05 and v2 by LUNA-WARM-07; v3 was frozen but never executed,
# because the approved LUNA-WARM-08 measurement refuted its boundary-offset
# premise first. Their frozen source fingerprints predate the
# preserved-accumulator accounting v4 introduces.
#
# Retired contracts are SUCCEEDED, never edited: their fingerprints drift, their
# loads fail closed, and that drift is what keeps them unadoptable.
DEFAULT_MANIFEST = Path("validation/monthly_warm_state_manifest_v16.json")
ARTIFACT_ROOT = Path("runs") / "monthly-warm-state-validation"
RECORD_KIND = "monthly_warm_state_equivalence_record"


class HarnessError(SystemExit):
    """Fail closed with an explicit reason."""


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HarnessError(f"JSON input must be an object: {path}")
    return payload


def canonical_key(payload: Mapping[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "content_key"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def load_frozen_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Validate the frozen contract and refuse any drift."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise HarnessError(f"manifest must be a regular file: {path}")
    manifest = _read(path)
    if manifest.get("kind") != "monthly_warm_state_validation_manifest":
        raise HarnessError("not a warm-state validation manifest")
    recorded = manifest.get("content_key")
    if not isinstance(recorded, str) or canonical_key(manifest) != recorded:
        raise HarnessError("manifest content key does not recompute")
    # Source drift is the retirement mechanism. Check it before live schema
    # eligibility so every superseded contract fails for the stable reason its
    # lifecycle promises, independent of which later schema changed first.
    fingerprints = manifest.get("source_fingerprints")
    if not isinstance(fingerprints, dict) or not fingerprints:
        raise HarnessError("manifest records no source fingerprints")
    drifted = sorted(
        name for name, digest in fingerprints.items()
        if hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest() != digest)
    if drifted:
        raise HarnessError(
            "frozen sources drifted; the campaign identity is invalid: "
            + ", ".join(drifted))
    from traffic_sim.simulation.monthly_warm_state import PREFIX_EVIDENCE_SCHEMA
    recorded_prefix_schema = manifest.get("prefix_evidence_schema")
    if recorded_prefix_schema != PREFIX_EVIDENCE_SCHEMA:
        raise HarnessError(
            f"manifest prefix-evidence schema {recorded_prefix_schema!r} does "
            f"not match production {PREFIX_EVIDENCE_SCHEMA!r}; recording a "
            f"schema without checking it would let the accounting change under "
            f"an unchanged campaign key")
    # The DIAGNOSTIC contract is validated against the live constants, exactly
    # like the accounting schemas above. A manifest that merely records a
    # vocabulary nobody checks could promise codes production cannot emit, and
    # the whole point of v5 is that a failure can be read.
    from traffic_sim.simulation.monthly_sumo import (
        WARM_ATTEMPT_SCHEMA, WARM_INFORMATIONAL_CODES, WARM_OUTCOMES,
        WARM_TERMINAL_CODES)
    contract = manifest.get("warm_attempt_contract")
    if not isinstance(contract, dict):
        raise HarnessError("manifest records no warm_attempt_contract")
    if contract.get("schema") != WARM_ATTEMPT_SCHEMA:
        raise HarnessError(
            f"manifest warm-attempt schema {contract.get('schema')!r} does not "
            f"match production {WARM_ATTEMPT_SCHEMA!r}")
    for field, live in (("outcomes", WARM_OUTCOMES),
                        ("terminal_codes", WARM_TERMINAL_CODES),
                        ("informational_codes", WARM_INFORMATIONAL_CODES)):
        if contract.get(field) != sorted(live):
            raise HarnessError(
                f"manifest warm-attempt {field} drifted from production; the "
                f"recorded vocabulary would describe reasons this build cannot "
                f"emit")
    required = contract.get("required_attempts")
    expected_attempts = (len(manifest["demand_variants"])
                         * int(manifest["repetitions_per_variant"])
                         * int(manifest["schedules_per_case"]))
    if required != expected_attempts:
        raise HarnessError(
            f"manifest requires {required!r} warm attempts but its own frozen "
            f"identity set implies {expected_attempts}")

    bind_contract = manifest.get("localhost_bind_preflight")
    if not isinstance(bind_contract, dict) \
            or bind_contract.get("required") is not True \
            or bind_contract.get("family") != "AF_INET" \
            or bind_contract.get("socket_type") != "SOCK_STREAM" \
            or bind_contract.get("address") != ["127.0.0.1", 0] \
            or bind_contract.get("ordering") != [
                "approval_token", "production_traci_origin_api",
                "ipv4_tcp_loopback_bind", "keyed_root_absence",
                "paired_campaign"]:
        raise HarnessError(
            "manifest does not require the exact fail-first localhost-bind "
            "preflight; refusing a campaign that could spend its keyed root "
            "inside a socket-blocked environment")

    return manifest


def require_approval(manifest: Mapping[str, Any], token: str | None) -> None:
    """Execution needs an approval token equal to THIS manifest's content key.

    The token is NOT stored in the manifest. An earlier version kept an
    `approved_content_key` field inside the hashed body, which was
    self-referential and unusable: setting it changed the very content key the
    approval named, so approving those exact bytes was impossible. The approval
    lives in the workflow record; the harness's job is only to verify that the
    operator is invoking the exact frozen contract that was approved.
    """
    expected = manifest.get("content_key")
    if not token:
        raise HarnessError(
            "execution requires an approval token. A paired run needs a new Sol "
            "task and explicit user approval bound to content key "
            f"{expected} and artifact root {manifest['artifact_root']}; pass it "
            f"back with --approval-token {expected}.")
    if token != expected:
        raise HarnessError(
            f"approval token does not match the frozen contract: expected "
            f"{expected}, got {token}")


def require_resolvable_traci(home=None) -> dict[str, Any]:
    """Resolve and API-check TraCI through the SAME production resolver.

    Import-only: it reads module attributes and makes no TraCI call, opens no
    socket and starts no process. Using the production resolver rather than a
    private copy is the point — a preflight that checked something else could
    pass while the controller still failed.
    """
    from traffic_sim.simulation.runtime import (
        SumoRuntimeError, resolve_traci, sumo_home, validate_traci_api)
    try:
        resolved_home = Path(home) if home is not None else sumo_home()
        module = resolve_traci(resolved_home)
        report = validate_traci_api(module)
    except SumoRuntimeError as error:
        raise HarnessError(
            f"TraCI preflight failed, so no campaign was started and no "
            f"artifact root was created: {error}")
    return {**report, "sumo_home": str(resolved_home)}


def require_localhost_bind(socket_factory=None) -> dict[str, Any]:
    """Prove the environment permits the exact loopback bind TraCI needs.

    This is deliberately separate from :class:`WarmPrefixController`: the
    campaign must discover a sandbox/policy denial BEFORE it checks or creates
    its one-time keyed root.  The factory seam keeps contract tests process-free
    and socket-free; production passes no factory and therefore uses the real
    IPv4/TCP socket implementation.
    """
    import socket                                    # lazy; import opens nothing

    factory = socket.socket if socket_factory is None else socket_factory
    probe = None
    failure = None
    try:
        probe = factory(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        address = probe.getsockname()
        if not isinstance(address, tuple) or len(address) < 2 \
                or address[0] != "127.0.0.1" \
                or isinstance(address[1], bool) \
                or not isinstance(address[1], int) \
                or not 1 <= address[1] <= 65535:
            raise ValueError(
                f"localhost bind returned an invalid address: {address!r}")
    except Exception as error:                       # policy/OS/fake failure
        failure = error
    finally:
        if probe is not None:
            try:
                probe.close()
            except Exception as error:
                if failure is None:
                    failure = error
    if failure is not None:
        raise HarnessError(
            "localhost TCP bind preflight failed, so no campaign was started "
            "and no artifact root was checked or created: "
            f"{type(failure).__name__}: {failure}")
    return {
        "bind_capable": True,
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "host": "127.0.0.1",
        "ephemeral_port": True,
    }


def require_absent_root(manifest: Mapping[str, Any]) -> Path:
    root = REPO_ROOT / manifest["artifact_root"] / manifest["content_key"]
    if root.exists():
        raise HarnessError(
            f"artifact root already exists and is not reused or inspected: {root}")
    return root


def compare_arms(cold: Mapping[str, Any], warm: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic comparison of two canonical production observations."""
    cold_semantic, warm_semantic = semantic_payload(cold), semantic_payload(warm)
    mismatches = []
    for key in sorted(set(cold_semantic) | set(warm_semantic)):
        if key not in cold_semantic or key not in warm_semantic:
            mismatches.append(f"{key}: present in only one arm")
        elif cold_semantic[key] != warm_semantic[key]:
            mismatches.append(f"{key}: differs")
    return {
        "equivalent": not mismatches,
        "mismatches": mismatches,
        "cold_digest": observation_digest(cold),
        "warm_digest": observation_digest(warm),
    }


def performance_record(phase_runtime_s: Mapping[str, float]) -> dict[str, Any]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    scale = 1 if os.uname().sysname == "Darwin" else 1024
    return {
        "phase_runtime_s": {k: round(float(v), 6)
                            for k, v in sorted(phase_runtime_s.items())},
        "peak_rss_bytes": int(usage.ru_maxrss) * scale,
        "recorded_at_monotonic_s": round(time.monotonic(), 6),
    }


def expected_identity_set(manifest: Mapping[str, Any], schedules) -> set:
    """Every (schedule, variant, seed) the frozen contract requires.

    Derived from the frozen schedules, declared variants and the PRODUCTION
    canonical seed rule, so the expectation cannot drift from what the runner
    actually executes.
    """
    from traffic_sim.simulation.monthly_search import canonical_seed

    repetitions = int(manifest.get("repetitions_per_variant", 1))
    if repetitions < 1:
        raise HarnessError("repetitions_per_variant must be at least 1")
    return {
        (schedule.schedule_id, variant, canonical_seed(variant, repetition))
        for schedule in schedules
        for variant in manifest["demand_variants"]
        for repetition in range(repetitions)
    }


def coverage_report(comparisons, expected: set) -> dict[str, Any]:
    """Compare observed identities against the required set, exactly.

    `run_candidate()` stops at the first hard failure, so a run can legitimately
    produce FEWER observations than the contract requires. Treating that as a
    pass would certify equivalence on a sample nobody chose — and would publish
    cache material for the seeds that happened to run first. Missing, extra,
    duplicate and unidentified observations are all coverage failures.
    """
    observed = []
    unidentified = 0
    for comparison in comparisons:
        identity = (comparison.get("schedule_id"),
                    comparison.get("demand_variant"), comparison.get("seed"))
        if any(part is None for part in identity):
            unidentified += 1
            continue
        observed.append(identity)
    seen = set(observed)
    duplicates = sorted(
        f"{s}/{v}/{d}" for s, v, d in seen if observed.count((s, v, d)) > 1)
    missing = sorted(f"{s}/{v}/{d}" for s, v, d in expected - seen)
    extra = sorted(f"{s}/{v}/{d}" for s, v, d in seen - expected)
    return {
        "required": len(expected),
        "observed": len(observed),
        "missing": missing,
        "extra": extra,
        "duplicates": duplicates,
        "unidentified": unidentified,
        "complete": not (missing or extra or duplicates or unidentified),
    }


def frozen_warm_point_map(manifest) -> dict:
    """The frozen safe warm point per demand variant.

    v2 records `route_safety[variant].safe_warm_point_s`; a pre-v2 contract
    carried one scalar for every variant. Both are resolved here so the
    evidence check has exactly one shape to compare against.
    """
    safety = manifest.get("route_safety")
    if isinstance(safety, Mapping) and safety:
        out = {}
        for variant, record in safety.items():
            point = (record or {}).get("safe_warm_point_s")
            if not isinstance(point, int) or isinstance(point, bool) or point <= 0:
                raise HarnessError(
                    f"frozen route safety for {variant!r} has no positive "
                    f"safe warm point")
            out[str(variant)] = point
        return out
    scalar = (manifest.get("cases") or [{}])[0].get("expected_warm_point_s")
    if not isinstance(scalar, int) or isinstance(scalar, bool) or scalar <= 0:
        raise HarnessError(
            "the manifest records neither per-variant route safety nor a "
            "positive expected warm point")
    return {variant: scalar for variant in manifest["demand_variants"]}


def validate_warm_attempts(attempts, expected) -> tuple[list, list]:
    """Validate the campaign's warm-attempt records against the frozen set.

    WHY THIS IS A GATE. The v4 campaign failed with three silent cold
    fallbacks and its immutable record could not say which of eleven paths
    produced them. A diagnostic that MIGHT be present is not evidence, so a
    missing, duplicated, unexpected, malformed or self-contradictory attempt
    fails the record and forbids publication exactly like any other gap.

    Returns (validated_records, problems).
    """
    from traffic_sim.simulation.monthly_sumo import (
        WARM_OUTCOME_EXECUTED, WARM_OUTCOME_FALLBACK, validate_warm_attempt)

    problems: list[str] = []
    validated: list[dict[str, Any]] = []
    seen: dict[tuple, int] = {}
    for index, attempt in enumerate(attempts or []):
        try:
            record = validate_warm_attempt(attempt)
        except Exception as error:
            problems.append(f"warm attempt {index} is malformed: {error}")
            continue
        identity = (record["schedule_id"], record["demand_variant"],
                    record["seed"])
        seen[identity] = seen.get(identity, 0) + 1
        validated.append(record)

    expected_set = set(expected)
    observed_set = set(seen)
    for identity in sorted(expected_set - observed_set):
        problems.append(
            f"{'/'.join(str(p) for p in identity)}: no warm attempt was "
            f"recorded, so a fallback here would be unexplained")
    for identity in sorted(observed_set - expected_set):
        problems.append(
            f"{'/'.join(str(p) for p in identity)}: unexpected warm attempt")
    for identity, count in sorted(seen.items()):
        if count != 1:
            problems.append(
                f"{'/'.join(str(p) for p in identity)}: {count} warm attempts, "
                f"expected exactly 1")
    return validated, problems


def cross_check_attempts_against_arms(records, comparisons) -> list[str]:
    """Every arm label must agree with its attempt's terminal outcome.

    A cold arm with no terminal decline, or a warm arm without a matching
    successful attempt, is a contradiction between two things the same run
    reported — and the contradiction is the finding, not a detail to smooth over.
    """
    from traffic_sim.simulation.monthly_sumo import (
        WARM_OUTCOME_EXECUTED, WARM_OUTCOME_FALLBACK)

    problems: list[str] = []
    by_identity = {(r["schedule_id"], r["demand_variant"], r["seed"]): r
                   for r in records}
    for comparison in comparisons:
        identity = (comparison.get("schedule_id"),
                    comparison.get("demand_variant"), comparison.get("seed"))
        label = "/".join(str(part) for part in identity)
        record = by_identity.get(identity)
        if record is None:
            continue                                   # already reported above
        arm = comparison.get("warm_arm")
        if arm == "warm" and record["outcome"] != WARM_OUTCOME_EXECUTED:
            problems.append(
                f"{label}: the warm arm ran but its attempt says "
                f"{record['outcome']!r}")
        if arm == "cold" and record["outcome"] != WARM_OUTCOME_FALLBACK:
            problems.append(
                f"{label}: the arm fell back to cold but its attempt says "
                f"{record['outcome']!r}")
    return problems


def execution_evidence_report(comparisons, provisional_states, expected,
                              frozen_warm_points, published=None,
                              warm_attempts=None):
    """Prove the warm branch actually RAN, for every frozen identity.

    Semantic equality alone cannot show this. The comparison strips
    execution-only fields, so a campaign whose warm arm declined and ran the
    cold path would produce two identical cold observations and compare
    perfectly equal — a green result proving nothing about warming. This checks
    the evidence the comparison cannot: that each pair really is cold-vs-warm,
    that the warm side started at the frozen warm point, that exactly one
    promotable state exists per identity, and that publication produced exactly
    as many cache keys as identities.
    """
    problems: list[str] = []
    # ATTEMPT COVERAGE is counted in finalized IDENTITY attempts, never in
    # events: one attempt may record several events on its way to one outcome.
    attempt_records, attempt_problems = validate_warm_attempts(
        warm_attempts, expected)
    problems.extend(attempt_problems)
    problems.extend(cross_check_attempts_against_arms(attempt_records,
                                                      comparisons))

    by_identity: dict[tuple, list] = {}
    for comparison in comparisons:
        identity = (comparison.get("schedule_id"),
                    comparison.get("demand_variant"), comparison.get("seed"))
        by_identity.setdefault(identity, []).append(comparison)

    for identity in sorted(expected):
        label = "/".join(str(part) for part in identity)
        found = by_identity.get(identity, [])
        if len(found) != 1:
            problems.append(f"{label}: expected 1 comparison, found {len(found)}")
            continue
        comparison = found[0]
        if comparison.get("cold_arm") != "cold":
            problems.append(
                f"{label}: cold arm reported {comparison.get('cold_arm')!r}")
        if comparison.get("warm_arm") != "warm":
            problems.append(
                f"{label}: warm arm reported {comparison.get('warm_arm')!r} — "
                f"a cold fallback cannot count as a warm execution")
        expected_point = (frozen_warm_points.get(identity[1])
                          if isinstance(frozen_warm_points, Mapping)
                          else frozen_warm_points)
        if expected_point is None:
            problems.append(f"{label}: no frozen warm point for this variant")
        elif comparison.get("warm_point_s") != expected_point:
            problems.append(
                f"{label}: warm point {comparison.get('warm_point_s')!r} != "
                f"frozen {expected_point!r}")

    observed_states = [
        (entry.get("schedule_id"), entry.get("variant"), entry.get("seed"))
        for entry in (provisional_states or [])
    ]
    # Each promotable state must ALSO have been warmed to its variant's frozen
    # point. Checking only the comparison left a wrong-point cache entry able to
    # reach publication: the comparison describes the run, the identity
    # describes what would be stored and later restored.
    for entry in (provisional_states or []):
        identity = entry.get("identity")
        variant = entry.get("variant")
        label = f"{entry.get('schedule_id')}/{variant}/{entry.get('seed')}"
        expected_point = (frozen_warm_points.get(variant)
                          if isinstance(frozen_warm_points, Mapping)
                          else frozen_warm_points)
        actual = getattr(identity, "warmup_end_s", None)
        if expected_point is None:
            problems.append(f"{label}: promotable state has no frozen point")
        elif actual != expected_point:
            problems.append(
                f"{label}: promotable state identity was warmed to {actual!r}, "
                f"not the frozen {expected_point!r}")
    state_set = set(observed_states)
    duplicates = sorted("/".join(str(p) for p in identity)
                        for identity in state_set
                        if observed_states.count(identity) > 1)
    missing = sorted("/".join(str(p) for p in i) for i in set(expected) - state_set)
    extra = sorted("/".join(str(p) for p in i) for i in state_set - set(expected))
    if missing:
        problems.append(f"no promotable state for: {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected promotable state for: {', '.join(extra)}")
    if duplicates:
        problems.append(f"duplicate promotable state for: {', '.join(duplicates)}")

    if published is not None:
        if len(published) != len(expected):
            problems.append(
                f"published {len(published)} cache entries for "
                f"{len(expected)} identities")
        if len(set(published)) != len(published):
            problems.append("published duplicate cache keys")

    return {
        "required_identities": len(expected),
        "warm_executions": sum(
            1 for c in comparisons if c.get("warm_arm") == "warm"),
        # The attempts themselves, so a future failure says which identity was
        # attempted, what happened, and why it fell back — without a rerun.
        "warm_attempts": attempt_records,
        "warm_attempt_count": len(attempt_records),
        "provisional_states": len(observed_states),
        "published_count": None if published is None else len(published),
        "frozen_warm_points": (dict(frozen_warm_points)
                               if isinstance(frozen_warm_points, Mapping)
                               else frozen_warm_points),
        "problems": problems,
        "complete": not problems,
    }


def build_equivalence_record(
    manifest: Mapping[str, Any],
    comparisons: list[Mapping[str, Any]],
    performance: Mapping[str, Any],
    expected: set | None = None,
    execution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Honest record: `pass` needs EVERY comparison equivalent AND complete.

    Equivalence over an incomplete identity set is not equivalence; it is a
    sample chosen by whichever seed failed first.
    """
    failed = [c for c in comparisons if not c["equivalent"]]
    coverage = coverage_report(comparisons, expected if expected is not None
                               else set())
    if expected is None:
        # No expectation supplied: coverage cannot be established, so it is
        # never complete. Fail closed rather than assume the run was whole.
        coverage = dict(coverage, complete=False,
                        missing=["expected identity set was not supplied"])
    if execution is None:
        # No execution evidence supplied: it cannot be established, so it is
        # never complete. Same fail-closed rule as coverage.
        execution = {"complete": False,
                     "problems": ["execution evidence was not supplied"]}
    passed = (bool(comparisons) and not failed and coverage["complete"]
              and execution["complete"])
    record = {
        "schema_version": 1,
        "kind": RECORD_KIND,
        "manifest_content_key": manifest["content_key"],
        "status": "pass" if passed else "fail",
        "comparison_count": len(comparisons),
        "mismatch_count": len(failed),
        "coverage": coverage,
        "execution_evidence": dict(execution),
        "comparisons": [dict(c) for c in comparisons],
        "performance": dict(performance),
        "cache_material_publishable": passed,
    }
    record["content_key"] = canonical_key(record)
    return record


def build_runner(manifest: Mapping[str, Any], *, warm: bool, workspace: Path,
                 sumo_invoker=None, boundary_controller=None,
                 forensic_observer=None):
    """One production runner per arm, in its OWN cache/workspace tree.

    Isolation matters: sharing a baseline cache between arms would let the warm
    arm read a matched baseline the cold arm produced, so an "equivalent"
    result could reflect shared state rather than agreement.
    """
    from traffic_sim.core.contracts import ClosureSearchSpec
    from traffic_sim.simulation.monthly_sumo import ArchivedDemandSumoRunner

    case = manifest["cases"][0]
    spec = ClosureSearchSpec.from_dict({
        "search_id": case["case_id"],
        "directed_edges": case["directed_edges"],
        "demand_build_id": manifest["demand_requirement"]["demand_build_key"],
        **manifest["spec_template"],
    })
    workspace.mkdir(parents=True, exist_ok=True)
    if warm and boundary_controller is None:
        # The warm arm gets a REAL controller by default. Without this the
        # campaign silently fell back to cold and could not test what it was
        # frozen to test. Constructing it starts no process and imports no
        # traci — that only happens if a prefix actually runs.
        from traffic_sim.simulation.warm_state_boundary import (
            WarmPrefixController)
        boundary_controller = WarmPrefixController()
    return ArchivedDemandSumoRunner(
        spec,
        archive=REPO_ROOT / manifest["demand_requirement"]["archive_path"],
        baseline_trip_duration_p99_s=manifest["baseline_trip_duration_p99_s"],
        study_provenance_key=manifest["content_key"],
        cache_root=workspace / "baselines",
        seed_workers=1,
        warm_execution=warm,
        sumo_invoker=sumo_invoker,
        boundary_controller=boundary_controller,
        forensic_observer=forensic_observer,
    )


def run_paired_campaign(manifest: Mapping[str, Any], root: Path,
                        *, runner_factory=None, sumo_invoker=None,
                        boundary_controller=None) -> dict[str, Any]:
    """Execute both arms over the frozen cases and compare them.

    `runner_factory` is an injection seam so the pairing logic can be exercised
    without SUMO. In a real approved run it is None and production runners are
    built. Cache material is published ONLY when every comparison is equivalent.
    """
    from traffic_sim.core.closure_calendar import generate_closure_schedules

    factory = runner_factory or build_runner
    root.mkdir(parents=True, exist_ok=False)
    phase_runtime: dict[str, float] = {}
    comparisons: list[dict[str, Any]] = []
    log_failures: list[str] = []

    cold = factory(manifest, warm=False, workspace=root / "cold",
                   sumo_invoker=sumo_invoker,
                   boundary_controller=boundary_controller)
    warm = factory(manifest, warm=True, workspace=root / "warm",
                   sumo_invoker=sumo_invoker,
                   boundary_controller=boundary_controller)

    schedules = generate_closure_schedules(cold.spec)[:manifest["schedules_per_case"]]
    if len(schedules) != manifest["schedules_per_case"]:
        raise HarnessError(
            f"frozen contract requires {manifest['schedules_per_case']} "
            f"schedule(s); generation produced {len(schedules)}")
    frozen_ids = manifest.get("frozen_schedule_ids")
    if frozen_ids is not None and [s.schedule_id for s in schedules] != frozen_ids:
        raise HarnessError(
            "generated schedules differ from the frozen contract: "
            f"{[s.schedule_id for s in schedules]} != {frozen_ids}")
    expected = expected_identity_set(manifest, schedules)
    derived_seeds = sorted({seed for _s, _v, seed in expected})
    if derived_seeds != sorted(manifest["seeds"]):
        raise HarnessError(
            f"production seed assignment {derived_seeds} does not match the "
            f"frozen seeds {sorted(manifest['seeds'])}; the approved identity "
            f"set would not be the one executed")
    for schedule in schedules:
        # VALIDATION-ONLY orchestration. `run_candidate` stops at the first hard
        # failure, which is correct for a SEARCH — there is no point ranking a
        # candidate already disqualified. But for equivalence evidence it means
        # one disqualified variant hides the other two: LUNA-WARM-05 compared
        # q10 only, because q10 hit `truncated_unreachable_vehicles`. Here each
        # frozen identity is requested directly from the SAME production
        # observation path, so a hard failure disqualifies that candidate
        # without suppressing the others. Production search behaviour is
        # untouched; only this harness bypasses the early stop.
        from traffic_sim.simulation.monthly_search import canonical_seed
        wanted = [(variant, canonical_seed(variant, repetition))
                  for variant in manifest["demand_variants"]
                  for repetition in range(manifest["repetitions_per_variant"])]
        collected = {"cold": [], "warm": []}
        for label, runner in (("cold", cold), ("warm", warm)):
            started = time.monotonic()
            for variant, seed in wanted:
                try:
                    _observation, _failures, canonical = runner._run_observation(
                        schedule, variant=variant, seed=seed)
                except Exception as error:                # honest, not silent
                    collected[label].append(None)
                    log_failures.append(
                        f"{label}:{schedule.schedule_id}/{variant}/{seed}: {error}")
                    continue
                collected[label].append(canonical)
            phase_runtime[f"{label}:{schedule.schedule_id}"] = (
                time.monotonic() - started)
        cold_observations = list(collected["cold"])
        warm_observations = list(collected["warm"])
        if len(cold_observations) != len(warm_observations):
            comparisons.append({
                "equivalent": False,
                "mismatches": [f"arm produced {len(cold_observations)} vs "
                               f"{len(warm_observations)} observations"],
                "cold_digest": None, "warm_digest": None,
                "schedule_id": schedule.schedule_id})
            continue
        for cold_observation, warm_observation in zip(cold_observations,
                                                      warm_observations):
            if cold_observation is None or warm_observation is None:
                comparisons.append({
                    "equivalent": False,
                    "mismatches": ["an arm produced no canonical observation"],
                    "cold_digest": None, "warm_digest": None,
                    "schedule_id": schedule.schedule_id,
                    "demand_variant": None, "seed": None,
                    "cold_semantic": None, "warm_semantic": None})
                continue
            comparison = compare_arms(cold_observation, warm_observation)
            # Identity travels WITH the comparison. Without it a provisional
            # state could be certified from another seed's or variant's result.
            comparison["schedule_id"] = schedule.schedule_id
            comparison["demand_variant"] = warm_observation["demand_variant"]
            comparison["seed"] = warm_observation["seed"]
            # Execution metadata is excluded from the SEMANTIC comparison by
            # design, which is exactly why it has to be captured here: without
            # it a run where the warm arm silently fell back to cold would
            # compare equal to itself and pass.
            comparison["cold_arm"] = cold_observation.get("execution_arm")
            comparison["warm_arm"] = warm_observation.get("execution_arm")
            comparison["warm_point_s"] = warm_observation.get("warm_point_s")
            # Content-keyed split evidence, retained on the record but never
            # compared: criterion 6 wants it reviewable, not authoritative.
            comparison["split_diagnostics"] = warm_observation.get(
                "split_diagnostics")
            comparison["cold_semantic"] = semantic_payload(cold_observation)
            comparison["warm_semantic"] = semantic_payload(warm_observation)
            comparisons.append(comparison)

    # v2 freezes a PER-VARIANT route-safe warm point, because the safe split
    # depends on which vehicles that variant's closure filtering touches. A
    # single `expected_warm_point_s` no longer exists; reading it would have
    # stopped an approved campaign before it produced any evidence.
    frozen_warm_points = frozen_warm_point_map(manifest)
    provisional = list(getattr(warm, "provisional_states", []))
    # Snapshot only THIS campaign's attempts, copied so later activity on the
    # runner cannot change what the immutable record says happened.
    # DEEP copy: a shallow one shares the nested event list, so later activity
    # on the runner could still change what the immutable record says happened.
    warm_attempts = copy.deepcopy(list(getattr(warm, "warm_attempts", [])))
    # Evidence FIRST, publication second: a run that cannot prove it warmed
    # must never write cache material, and checking afterwards would be too
    # late to prevent it.
    execution = execution_evidence_report(
        comparisons, provisional, expected, frozen_warm_points,
        warm_attempts=warm_attempts)
    record = build_equivalence_record(manifest, comparisons,
                                      performance_record(phase_runtime),
                                      expected, execution)
    published = []
    if record["cache_material_publishable"]:
        try:
            published = publish_cache_material(warm, comparisons, expected)
        except BaseException as error:
            # Publication is all-or-nothing, so a failure here means NOTHING was
            # published — the warm-state root does not exist. Record that
            # honestly instead of leaving a "pass" that promised entries which
            # do not exist.
            execution = execution_evidence_report(
                comparisons, provisional, expected, frozen_warm_points, [],
                warm_attempts=warm_attempts)
            execution = dict(execution, complete=False, problems=list(
                execution["problems"]) + [f"publication failed: {error}"])
            record = build_equivalence_record(
                manifest, comparisons, performance_record(phase_runtime),
                expected, execution)
            (root / "NO_CACHE_PUBLISHED").write_text(
                f"publication failed and was rolled back; no warm-state cache "
                f"material exists for this run: {error}\n")
            record["published_cache_entries"] = []
            record["content_key"] = canonical_key(record)
            (root / "equivalence_record.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n")
            return record
        # The count/duplicate checks now live INSIDE publication, before the
        # single atomic rename, so reaching here means the published set is
        # exactly the expected one. Re-record it as evidence.
        execution = execution_evidence_report(
            comparisons, provisional, expected, frozen_warm_points, published,
            warm_attempts=warm_attempts)
        record = build_equivalence_record(manifest, comparisons,
                                          performance_record(phase_runtime),
                                          expected, execution)
    else:
        # Honest fail evidence and NO usable cache. The warm-state root is left
        # untouched so a failed pairing cannot seed a later warm run.
        (root / "NO_CACHE_PUBLISHED").write_text(
            "equivalence did not pass; no warm-state cache material was "
            "published from this run.\n")
    record["published_cache_entries"] = published
    record["observation_failures"] = list(log_failures)
    record["content_key"] = canonical_key(record)
    (root / "equivalence_record.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def publish_cache_material(warm_runner, comparisons, expected,
                           store=None) -> list[str]:
    """Promote provisional states into the cache — ONLY after equivalence.

    A state is reusable evidence: restoring one asserts that resuming from it
    reproduces the cold result. So it is stored only once the paired comparison
    has actually demonstrated that, and it is stored with the passing
    equivalence certificate that `restore_warm_state` re-checks on every hit.
    """
    from traffic_sim.simulation.warm_state_cache import (
        certify_warm_state_equivalence, store_warm_state)

    if not comparisons or any(not c["equivalent"] for c in comparisons):
        raise HarnessError(
            "refusing to publish cache material for a non-equivalent run")
    # Coverage is RECOMPUTED here from the required identity set, never taken
    # on trust. An optional coverage argument would fail open exactly when it
    # was forgotten — the one case where the caller is least likely to notice.
    if not isinstance(expected, (set, frozenset)) or not expected:
        raise HarnessError(
            "refusing to publish cache material without a required expected "
            "identity set")
    coverage = coverage_report(comparisons, set(expected))
    if not coverage["complete"]:
        raise HarnessError(
            "refusing to publish cache material for an INCOMPLETE run: "
            f"missing={coverage['missing']} extra={coverage['extra']} "
            f"duplicates={coverage['duplicates']} "
            f"unidentified={coverage['unidentified']}")
    provisional = getattr(warm_runner, "provisional_states", [])
    final_root = Path(warm_runner.warm_state_root())
    if final_root.exists() or final_root.is_symlink():
        raise HarnessError(
            f"warm-state root already exists; refusing to merge into it: "
            f"{final_root}")
    final_root.parent.mkdir(parents=True, exist_ok=True)

    # CAMPAIGN-ATOMIC publication. The whole set is staged in a sibling
    # directory and the final root appears in ONE rename, so a count mismatch,
    # a duplicate key or a mid-batch failure leaves NO final root at all.
    # Publishing entry-by-entry and warning afterwards is not enough: a warning
    # is not a mechanism, and every entry written before the failure would stay
    # restorable.
    staging = Path(tempfile.mkdtemp(prefix=".warm-publish-",
                                    dir=str(final_root.parent)))
    published = []
    identities = []
    try:
        published, identities = _stage_cache_entries(
            staging, provisional, comparisons, store=store)
        if len(published) != len(expected):
            raise HarnessError(
                f"publication would write {len(published)} entries for "
                f"{len(expected)} identities; refusing to publish any")
        if len(set(published)) != len(published):
            raise HarnessError(
                "publication would write duplicate cache keys; refusing to "
                "publish any")
        _verify_staged_entries(staging, identities)
        os.replace(staging, final_root)          # the single publication step
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return published


def _verify_staged_entries(staging: Path, identities) -> None:
    """Every staged entry must actually restore before any of them is published."""
    from traffic_sim.simulation.warm_state_cache import restore_warm_state

    with tempfile.TemporaryDirectory() as scratch:
        for identity in identities:
            lookup = restore_warm_state(
                staging, identity, Path(scratch) / f"{identity.content_key}.gz")
            if not lookup.hit:
                raise HarnessError(
                    f"staged cache entry {identity.content_key} does not "
                    f"restore ({lookup.reason}); refusing to publish any")


def _stage_cache_entries(staging: Path, provisional, comparisons, store=None):
    """Certify and stage every provisional state. Writes only under `staging`."""
    from traffic_sim.simulation.warm_state_cache import (
        certify_warm_state_equivalence, store_warm_state)

    store = store or store_warm_state
    published, identities = [], []
    for entry in provisional:
        identity = entry["identity"]
        # EXACT identity: a state is certified only by the comparison for its
        # own schedule, variant and seed. Reusing another comparison would
        # certify q90/seed-1002 with q50/seed-1000's evidence.
        matching = [
            c for c in comparisons
            if c.get("schedule_id") == entry["schedule_id"]
            and c.get("demand_variant") == entry["variant"]
            and c.get("seed") == entry["seed"]
        ]
        if len(matching) != 1:
            raise HarnessError(
                f"cannot certify {identity.content_key}: expected exactly one "
                f"comparison for {entry['schedule_id']}/{entry['variant']}/"
                f"{entry['seed']}, found {len(matching)}")
        comparison = matching[0]
        if not comparison["equivalent"]:
            raise HarnessError(
                f"refusing to publish {identity.content_key}: its own "
                f"comparison did not pass")
        # Certify from the FULL semantic payloads of this pair, not digests: a
        # digest pair proves only that two hashes were equal, which is what the
        # comparison already asserted.
        certificate = certify_warm_state_equivalence(
            identity, comparison["cold_semantic"], comparison["warm_semantic"])
        if certificate.status != "pass":
            raise HarnessError(
                f"refusing to publish {identity.content_key}: certificate failed")
        evidence = entry.get("prefix_evidence")
        if evidence is None:
            raise HarnessError(
                f"refusing to publish {identity.content_key}: no versioned "
                f"prefix evidence; a state without it cannot account for the "
                f"pre-warm segment on restore")
        # Bind to the identity's warm point BEFORE storing: publishing evidence
        # that describes a different split would create an entry that can never
        # be restored, and the failure would surface far from its cause.
        parse_prefix_evidence(evidence,
                              expected_warm_point_s=identity.warmup_end_s)
        prefix = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
        # The evidence travels INSIDE the entry, hashed in its manifest and
        # re-verified on every restore. Written beside the entry it would be
        # neither atomic nor tamper-evident.
        store(staging, identity, entry["state_path"], certificate,
              members={"prefix_evidence.json": prefix})
        published.append(identity.content_key)
        identities.append(identity)
    return published, identities


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--execute", action="store_true",
                        help="Run the paired campaign (requires approval).")
    parser.add_argument("--approval-token", default=None)
    parser.add_argument(
        "--check-traci-import-only", action="store_true",
        help=("resolve and API-check TraCI through the production resolver and "
              "exit; imports only, never connects, executes or creates a root"))
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.check_traci_import_only:
        # Deliberately BEFORE manifest loading: this probe is about the
        # environment, not about any campaign, and it must be runnable without
        # naming one.
        report = require_resolvable_traci()
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    manifest = load_frozen_manifest(args.manifest)
    print(f"frozen manifest ok: {manifest['content_key']}")
    print(f"  cases: {len(manifest['cases'])} | seeds: {manifest['seeds']} "
          f"| mode: {manifest['simulation_mode']}")
    if not args.execute:
        print("no execution requested; frozen-input checks only.")
        return 0
    require_approval(manifest, args.approval_token)
    # MANDATORY PRE-ROOT PREFLIGHT. After the approval token is validated and
    # BEFORE the output root is checked or created: v6 burned an approved,
    # non-resumable campaign discovering that TraCI could not be imported, and
    # a run that cannot warm should never reach the point of creating evidence.
    traci_preflight = require_resolvable_traci()
    print(f"  traci: {traci_preflight['origin']}")
    # Real loaded current manifests are schema-checked above and MUST carry this
    # requirement. The conditional retains the old fake-manifest injection seam
    # used by process-free predecessor tests; it cannot bypass a real load.
    if manifest.get("localhost_bind_preflight", {}).get("required") is True:
        bind_preflight = require_localhost_bind()
        print(f"  localhost bind: {bind_preflight['bind_capable']}")
    root = require_absent_root(manifest)
    record = run_paired_campaign(manifest, root)
    print(f"paired campaign {record['status']}: "
          f"{record['comparison_count']} comparisons, "
          f"{record['mismatch_count']} mismatches")
    return 0 if record["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
