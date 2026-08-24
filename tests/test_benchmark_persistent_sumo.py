"""Fake-driven tests for the persistent-SUMO experiment harness (rev 2).

All non-SUMO: no `traci`/libsumo import, no socket, no process, no outcome. Pool
lifecycle, shared per-seed→payload assembly (through run_scenario's real
builders), true-concurrency dispatch with a query-wide 600 s deadline, pairing,
gates, member-fault/fallback and fail-closed cleanup are exercised with fakes
and a fake clock. A separate test drives the REAL `ScenarioAssembler` through
the shared `run_scenario` builders; command parity is captured against the real
`run_scenario.run_sumo` without invoking SUMO.
"""
import hashlib
import json
import types
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import benchmark_persistent_sumo as h        # noqa: E402

CONTRACT = Path("validation/persistent_sumo_campaign_v2.json")


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _flows(case, seed):
    return {"e0": [seed % 7, 1 if case == "closure" else 2], "e1": [3, 4]}


def _good_health(seed, variant):
    return {"seed": seed, "variant": variant, "loaded": 100, "inserted": 100,
            "running_at_end": 0, "waiting_at_end": 0, "collisions": 0,
            "teleports": 0}


def _seed_out(case, seed, variant, *, flows_of=None, health_ok=True):
    flows_of = flows_of or _flows
    hh = _good_health(seed, variant)
    if not health_ok:
        hh = dict(hh, waiting_at_end=2)
    traj = None
    if seed == h.TRAJECTORY_SEED:
        traj = {"vehicles": [{"d": 0}], "n_unfinished": 0, "inserted": 1,
                "sampling": {}, "inv": ["e0"]}
    return {"flows": flows_of(case, seed),
            "active_closure_entries": 0 if case == "closure" else None,
            "trajectory": traj, "seed_health": hh, "truncated": 0, "dropped": 0}


class FakePrep:
    def prepare(self, work_dir, seed, variant, query):
        return {"route": Path(work_dir) / "route.rou.xml",
                "truncated": 0, "dropped": 0}

    def read_outputs(self, work_dir, seed, variant, query):
        return _seed_out(query["case"], seed, variant,
                         flows_of=getattr(self, "flows_of", None),
                         health_ok=getattr(self, "health_ok", True))


class FakeAssembler:
    """Deterministic digest = f(aggregated per-seed flows + case). Reproduces the
    real semantics (aggregate then digest) without loading the network."""

    def __init__(self, *, integrity="verified_clean"):
        self.integrity = integrity

    def assemble(self, case, per_seed):
        blob = json.dumps([s["flows"] for s in per_seed], sort_keys=True)
        scen = hashlib.sha256((case + "scen" + blob).encode()).hexdigest()
        traj = next((s["trajectory"] for s in per_seed
                     if s["seed"] == h.TRAJECTORY_SEED), None)
        traj_digest = (hashlib.sha256((case + json.dumps(traj, sort_keys=True))
                                      .encode()).hexdigest()
                       if traj is not None else None)
        return {"scenario_digest": scen, "trajectory_digest": traj_digest,
                "closure_integrity": (self.integrity if case == "closure"
                                      else None),
                "seed_health": [s["seed_health"] for s in per_seed]}


class FakeConnector:
    def __init__(self, slot, *, fault=None, close_fault=False):
        self.slot = slot
        self.fault = fault
        self.close_fault = close_fault
        self.calls = []
        self.closed = False
        self.aborted = False

    def load(self, args):
        self.calls.append("load")
        if self.fault == "load":
            raise RuntimeError("fault at load")

    def advance(self, end_s):
        self.calls.append("advance")

    def collect_live_health(self):
        self.calls.append("collect_live_health")
        return {"running": 0}

    def finalize(self):
        self.calls.append("finalize")
        if self.fault == "finalize":
            raise RuntimeError("fault at finalize")

    def abort(self):
        self.calls.append("abort")
        self.aborted = True
        self.closed = True

    def close(self):
        if self.close_fault:
            # A permanently un-reapable child: close cannot prove it reaped.
            raise h.MemberFault(f"member {self.slot['member']} could not be reaped")
        self.closed = True


def connector_factory(*, fault=None, close_fault=()):
    created = []

    def factory(slot, work_dir):
        conn = FakeConnector(slot, fault=(fault or {}).get(slot["member"]),
                             close_fault=slot["member"] in close_fault)
        created.append(conn)
        return conn
    factory.created = created
    return factory


def reference_runner(*, flows_of=None, health_ok=True):
    def runner(slot, query, prep, registry=None):
        return {"member": slot["member"], "seed": slot["seed"],
                "variant": slot["variant"],
                **_seed_out(query["case"], slot["seed"], slot["variant"],
                            flows_of=flows_of, health_ok=health_ok)}
    return runner


def fake_dispatch(clock, *, closure_wall, baseline_wall, timeout=False):
    """Return per-job OUTCOMES like the real thread_dispatch: {ok, value|error}.
    A job that raises becomes a per-job ok:False (no query-wide abort); only the
    deadline path aborts+raises."""
    def dispatch(jobs, timeout_s, on_timeout=None):
        if timeout:
            if on_timeout is not None:
                on_timeout()
            raise h.MemberFault(f"per-query deadline exceeded after {timeout_s}s")
        outcomes = []
        for job in jobs:
            try:
                outcomes.append({"ok": True, "value": job()})
            except Exception as exc:                       # noqa: BLE001
                outcomes.append({"ok": False, "error": str(exc)})
        results = [o["value"] for o in outcomes if o["ok"]]
        case = "closure" if any(r.get("active_closure_entries") is not None
                                for r in results) else "baseline"
        clock.advance(closure_wall if case == "closure" else baseline_wall)
        return outcomes
    return dispatch


# ── Import & CLI safety (criterion 1, 12) ────────────────────────────────────
class TestImportAndCliSafety:

    def test_import_pulls_in_no_traci_or_run_scenario(self):
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; import tools.benchmark_persistent_sumo; "
             "assert 'traci' not in sys.modules and 'libsumo' not in sys.modules "
             "and 'run_scenario' not in sys.modules; print('clean')"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent))
        assert r.returncode == 0 and "clean" in r.stdout, r.stderr

    def test_help_creates_nothing(self):
        r = subprocess.run(
            [sys.executable, "tools/benchmark_persistent_sumo.py", "--help"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).resolve().parent.parent))
        assert r.returncode == 0

    def test_validate_contract_only_creates_nothing(self, tmp_path, capsys,
                                                    monkeypatch):
        artifact = tmp_path / "nope"
        monkeypatch.setattr(sys, "argv", [
            "x", "--campaign", str(CONTRACT), "--validate-contract-only",
            "--artifact-dir", str(artifact)])
        assert h.main() == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["executed"] is False and payload["artifact_dir"] is None
        assert not artifact.exists()

    def test_execute_requires_artifact_dir(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv",
                            ["x", "--campaign", str(CONTRACT), "--execute"])
        assert h.main() == 2
        assert "artifact-dir" in capsys.readouterr().err

    def test_traci_import_is_function_local(self):
        src = h.HARNESS.read_text()
        assert "\nimport traci" not in src and "import traci" in src


# ── Contract validation (criterion 3) ────────────────────────────────────────
class TestContractValidation:

    def test_loads_and_recomputes(self):
        c = h.load_contract(CONTRACT)
        assert c["experiment_id"] == "persistent_sumo_v2"
        assert h.content_key(c) == c["content_key"]

    def test_duplicate_json_key_is_rejected(self, tmp_path):
        raw = CONTRACT.read_text().rstrip().rstrip("}")
        bad = raw + ', "kind": "duplicate"}'
        p = tmp_path / "dup.json"; p.write_text(bad)
        with pytest.raises(h.ContractRefused, match="duplicate key"):
            h.load_contract(p)

    def test_edited_body_fails_the_key(self, tmp_path):
        c = json.loads(CONTRACT.read_text())
        c["execution"]["trials"] = 2
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="does not recompute"):
            h.load_contract(p)

    def test_renamed_is_refused(self, tmp_path):
        c = json.loads(CONTRACT.read_text())
        c["experiment_id"] = "persistent_sumo_v9"
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="only persistent_sumo_v2"):
            h.load_contract(p)

    def test_retired_is_refused(self, tmp_path):
        c = json.loads(CONTRACT.read_text())
        c["experiment_id"] = "persistent_sumo_v0"
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="not executable"):
            h.load_contract(p)

    @pytest.mark.parametrize("mutate, match", [
        (lambda c: c["execution"].__setitem__("sim_end_s", 3600), "sim_end_s"),
        (lambda c: c["execution"].__setitem__("n_intervals", 48), "n_intervals"),
        (lambda c: c["matrix"].__setitem__("query_order", ["baseline"] * 10),
         "query_order"),
        (lambda c: c["gates"].__setitem__("min_p95_improvement_fraction", 0.0),
         "0.04 floor"),
        (lambda c: c["gates"].__setitem__("max_parallel_p95_wall_s", 30.0),
         "10.0 s ceiling"),
        (lambda c: c["gates"].__setitem__("authorizes", "deployment"),
         "no-authority"),
        (lambda c: c["execution"]["timer_semantics"].__setitem__(
            "per_query_includes_payload_build", False), "payload build"),
    ])
    def test_structural_drift_is_refused(self, tmp_path, mutate, match):
        c = json.loads(CONTRACT.read_text())
        mutate(c)
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match=match):
            h.load_contract(p)

    def test_dropped_fingerprint_is_refused(self, tmp_path):
        c = json.loads(CONTRACT.read_text())
        c["frozen_fingerprints"].pop("source:run_scenario.py")
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="frozen_fingerprints"):
            h.load_contract(p)

    def test_an_unknown_top_level_field_is_refused(self, tmp_path):
        c = json.loads(CONTRACT.read_text())
        c["surprise"] = 1
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="unknown field"):
            h.load_contract(p)

    @pytest.mark.parametrize("obj", ["execution", "matrix", "gates"])
    def test_an_unknown_nested_field_is_refused(self, tmp_path, obj):
        c = json.loads(CONTRACT.read_text())
        c[obj]["surprise_nested"] = 1
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="unknown field"):
            h.load_contract(p)


# ── Environment identity (criterion 3, 12) ───────────────────────────────────
class TestEnvironmentIdentity:

    def test_binds_non_null_sumo_and_platform(self):
        c = json.loads(CONTRACT.read_text())
        assert isinstance(c["expected_sumo_version"], str) and c["expected_sumo_version"]
        assert isinstance(c["expected_platform"], str) and c["expected_platform"]

    def test_sumo_drift_aborts(self, monkeypatch, tmp_path):
        c = h.load_contract(CONTRACT)
        # This historical experiment is intentionally fingerprint-bound to
        # the tree that executed it.  Current source/demand bytes may have
        # moved on; isolate the SUMO-version condition this test names rather
        # than letting an earlier fingerprint refusal mask it.
        monkeypatch.setattr(
            h, "file_fingerprints", lambda: dict(c["frozen_fingerprints"])
        )
        (tmp_path / "demand_meta.json").write_text(json.dumps({
            "build_id": c["demand_identity"]["build_id"],
            "demand_build_key": c["demand_identity"]["demand_build_key"],
        }))
        monkeypatch.setattr(h, "SUMO_DIR", tmp_path)
        monkeypatch.setattr(h, "sumo_version", lambda: "Eclipse SUMO sumo 9.9.9")
        with pytest.raises(h.ExperimentAborted, match="SUMO version drift"):
            h.verify_environment(c)

    def test_platform_drift_aborts(self, monkeypatch, tmp_path):
        c = h.load_contract(CONTRACT)
        monkeypatch.setattr(
            h, "file_fingerprints", lambda: dict(c["frozen_fingerprints"])
        )
        (tmp_path / "demand_meta.json").write_text(json.dumps({
            "build_id": c["demand_identity"]["build_id"],
            "demand_build_key": c["demand_identity"]["demand_build_key"],
        }))
        monkeypatch.setattr(h, "SUMO_DIR", tmp_path)
        monkeypatch.setattr(h, "sumo_version",
                            lambda: c["expected_sumo_version"])
        monkeypatch.setattr(h.platform, "platform", lambda: "OtherOS-1.0")
        with pytest.raises(h.ExperimentAborted, match="platform"):
            h.verify_environment(c)


# ── Command parity (criterion 5) ─────────────────────────────────────────────
class TestCommandParity:

    def _args(self, traci_server, seed=1000, vehroute=True):
        return h.build_sumo_args(
            seed=seed, route_path=h.SUMO_DIR / "calibrated.rou.xml",
            add_paths=[Path("/tmp/x/e.add.xml")],
            edgedata_out=Path("/tmp/x/e.xml"),
            statistics_out=Path("/tmp/x/s.xml"),
            vehroute_out=Path("/tmp/x/v.xml") if vehroute else None,
            traci_server=traci_server)

    def test_persistent_is_subprocess_plus_the_traci_pair(self):
        sub, per = self._args(False), self._args(True)
        assert per[:len(sub)] == sub
        assert per[len(sub):] == ["--remote-port", "0", "--num-clients", "1"]

    def test_vehroute_only_when_requested(self):
        assert "--vehroute-output" in self._args(False, vehroute=True)
        assert "--vehroute-output" not in self._args(False, vehroute=False)

    def test_matches_run_scenario_run_sumo_flags(self, tmp_path, monkeypatch):
        import run_scenario as rs
        captured = {}

        class Stop(RuntimeError):
            pass

        def fake_run(cmd, *a, **k):
            captured["cmd"] = cmd
            raise Stop
        monkeypatch.setattr(rs.subprocess, "run", fake_run)
        monkeypatch.setattr(rs, "sumo_home", lambda: tmp_path)
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "sumo").write_text("")
        route = tmp_path / "r.rou.xml"; route.write_text("<routes/>")
        add = tmp_path / "a.add.xml"; add.write_text("<additional/>")
        try:
            rs.run_sumo(1000, route, [add], 90000, tmp_path,
                        net_path=h.NET_PATH, begin_s=0, flush_s=0)
        except Stop:
            pass
        prod = captured["cmd"]
        for flag, value in (("--mesosim", "true"),
                            ("--meso-junction-control.limited", "true"),
                            ("--seed", "1000"), ("--begin", "0"),
                            ("--end", "90000"), ("--no-step-log", "true")):
            assert flag in prod and prod[prod.index(flag) + 1] == value


# ── Matrix (criterion 4) ─────────────────────────────────────────────────────
def test_matrix_is_ten_alternating_with_fixed_slots():
    m = h.query_matrix(h.load_contract(CONTRACT))
    assert [q["case"] for q in m] == list(h.QUERY_ORDER)
    for q in m:
        assert [(s["member"], s["seed"], s["variant"]) for s in q["slots"]] \
            == [(0, 1000, "q50"), (1, 1001, "q10"), (2, 1002, "q90")]
    assert sum(q["case"] == "closure" for q in m) == 5
    assert h.TRAJECTORY_SEED == 1000


# ── Real ScenarioAssembler via the shared run_scenario builders (crit 2, 9) ──
class TestRealAssemblerUsesSharedBuilders:

    def _ctx(self):
        return {"web_edges": ["e0", "e1"], "prior": {"e0": 1.0, "e1": 1.0},
                "meta": {"epoch_sim": "2025-09-16T00:00:00", "source": "historical",
                         "agent_demand": None, "build_id": "b", "demand_build_key": "k",
                         "date": "2025-09-16", "begin": "00:00", "end": "24:00"},
                "sig": "sig", "window_label": "00:00–24:00",
                "sensor_audit_for": lambda case, ps, fo, arr: {"audit": case},
                "fit_errors_for": lambda case, audit: [],
                "closures": [{"edge_id": h.KNOWN_CLOSURE}],
                "spec_for": lambda case: _Spec(case),
                "name_for": lambda case: ("close_x" if case == "closure"
                                          else "baseline"),
                "label_for": lambda case: "L"}

    def _per_seed(self, flows=None):
        flows = flows or (lambda s: {"e0": [s % 5, 1], "e1": [2, 3]})
        rows = []
        for s, v in ((1000, "q50"), (1001, "q10"), (1002, "q90")):
            traj = ({"vehicles": [{"d": 0}], "n_unfinished": 0, "inserted": 1,
                     "sampling": {}, "inv": ["e0"],
                     "variant": "calibrated.rou.xml"} if s == 1000 else None)
            rows.append({"seed": s, "variant": v, "flows": flows(s),
                         "active_closure_entries": 0, "trajectory": traj,
                         "seed_health": _good_health(s, v),
                         "truncated": 0, "dropped": 0})
        return rows

    def test_it_digests_a_shared_production_payload(self):
        agg = h.ScenarioAssembler(self._ctx())
        out = agg.assemble("closure", self._per_seed())
        assert h._is_sha256(out["scenario_digest"])
        assert h._is_sha256(out["trajectory_digest"])
        assert out["closure_integrity"] == "verified_clean"
        # different per-seed flows -> different aggregated payload -> new digest
        out2 = agg.assemble("closure",
                            self._per_seed(flows=lambda s: {"e0": [9, 9],
                                                            "e1": [9, 9]}))
        assert out2["scenario_digest"] != out["scenario_digest"]

    def test_a_leaking_closure_is_not_verified_clean(self):
        agg = h.ScenarioAssembler(self._ctx())
        rows = self._per_seed()
        for r in rows:
            r["active_closure_entries"] = 50
        assert agg.assemble("closure", rows)["closure_integrity"] != "verified_clean"

    def test_baseline_has_no_closure_integrity(self):
        agg = h.ScenarioAssembler(self._ctx())
        assert agg.assemble("baseline", self._per_seed())["closure_integrity"] is None


class _Spec:
    def __init__(self, case):
        self.scenario_id = "close_x" if case == "closure" else "baseline"
        self.simulation_mode = "meso"
        self.network_build_id = "nb"

    def to_dict(self):
        return {"scenario_id": self.scenario_id, "mode": self.simulation_mode}


# ── Gate evaluation (criteria 9-10) ──────────────────────────────────────────
def _rows(*, per_closure=9.0, ref_closure=10.0, scenario_ok=True,
          health_ok=True, integrity_ok=True, faults=0, fallbacks=0):
    per, ref = [], []
    for i, case in enumerate(h.QUERY_ORDER):
        pw = per_closure if case == "closure" else 5.0
        rw = ref_closure if case == "closure" else 5.0
        s_ref = hashlib.sha256(f"{case}-s".encode()).hexdigest()
        s_per = s_ref if scenario_ok else hashlib.sha256(f"{case}-x".encode()).hexdigest()
        t = hashlib.sha256(f"{case}-t".encode()).hexdigest()
        healthy = [{"seed": s, "variant": v, "loaded": 5, "inserted": 5,
                    "running_at_end": 0, "waiting_at_end": 0, "collisions": 0,
                    "teleports": 0}
                   for s, v in ((1000, "q50"), (1001, "q10"), (1002, "q90"))]
        per_health = ([dict(healthy[0], teleports=1)] + healthy[1:]
                      if not health_ok else healthy)
        integ = "verified_clean" if case == "closure" else None
        ref.append({"index": i, "case": case, "parallel_wall_s": rw,
                    "scenario_digest": s_ref, "trajectory_digest": t,
                    "closure_integrity": integ, "seed_health": healthy,
                    "fallbacks": 0, "member_events": []})
        per.append({"index": i, "case": case, "parallel_wall_s": pw,
                    "scenario_digest": s_per, "trajectory_digest": t,
                    "closure_integrity": (integ if integrity_ok else "meh"),
                    "seed_health": per_health, "fallbacks": 0,
                    "member_events": []})
    return per, ref, faults, fallbacks


def test_faster_identical_healthy_passes():
    per, ref, f, fb = _rows()
    v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
    assert v["eligible_and_passed"] and v["failed_gates"] == []
    assert v["p95_improvement_fraction"] == pytest.approx(0.1)


@pytest.mark.parametrize("kw, gate", [
    ({"per_closure": 10.5, "ref_closure": 12.0}, "parallel_latency_ceiling"),
    ({"per_closure": 9.9, "ref_closure": 10.0}, "p95_improvement_floor"),
    ({"scenario_ok": False}, "digest_mismatch"),
    ({"health_ok": False}, "seed_health"),
    ({"integrity_ok": False}, "closure_integrity"),
])
def test_each_defect_fails_closed(kw, gate):
    per, ref, f, fb = _rows(**kw)
    v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
    assert not v["eligible_and_passed"]
    assert any(g.startswith(gate) for g in v["failed_gates"])


def test_fault_or_fallback_is_ineligible():
    per, ref, _, _ = _rows()
    assert "member_fault" in h.evaluate(per, ref, member_faults=1,
                                        fallbacks=0)["failed_gates"]
    assert "fallback_used" in h.evaluate(per, ref, member_faults=0,
                                         fallbacks=1)["failed_gates"]


def test_incomplete_evidence_fails_closed():
    per, ref, f, fb = _rows()
    v = h.evaluate(per[:8], ref, member_faults=f, fallbacks=fb)
    assert not v["eligible_and_passed"] and "persistent_incomplete" in v["failed_gates"]


# ── Concurrency + deadline (criteria 7, 8) ───────────────────────────────────
class TestDispatchDeadline:

    def test_runs_every_job_and_returns_outcomes(self):
        jobs = [(lambda i=i: i) for i in range(3)]
        outcomes = h.thread_dispatch(jobs, timeout_s=5)
        assert all(o["ok"] for o in outcomes)
        assert sorted(o["value"] for o in outcomes) == [0, 1, 2]

    def test_one_query_deadline_fires_and_aborts(self):
        import threading, time as _t
        released = threading.Event()
        aborted = {"v": False}

        def slow():
            released.wait(2.0)
            return 1

        def on_timeout():
            aborted["v"] = True
            released.set()
        t0 = _t.perf_counter()
        with pytest.raises(h.MemberFault, match="deadline exceeded"):
            h.thread_dispatch([slow], timeout_s=0.1, on_timeout=on_timeout)
        assert aborted["v"] and _t.perf_counter() - t0 < 1.5

    def test_a_job_fault_is_a_per_job_outcome_not_a_query_raise(self):
        def boom():
            raise h.MemberFault("boom")
        def ok():
            return 7
        outcomes = h.thread_dispatch([boom, ok], timeout_s=5)
        assert outcomes[0]["ok"] is False and "boom" in outcomes[0]["error"]
        assert outcomes[1] == {"ok": True, "value": 7}


# ── Orchestration with fakes (criteria 4-10) ─────────────────────────────────
class TestRunExperiment:

    def _run(self, tmp_path, clock, cf, *, dispatch=None, ref=None,
             fallback=None, prep=None, assembler=None):
        return h.run_experiment(
            h.load_contract(CONTRACT), tmp_path / "campaign",
            connector_factory=cf, reference_runner=ref or reference_runner(),
            assembler=assembler or FakeAssembler(), prep=prep or FakePrep(),
            fallback_runner=fallback, dispatch=dispatch,
            reference_dispatch=fake_dispatch(clock, closure_wall=10.0,
                                             baseline_wall=5.0), clock=clock)

    def test_healthy_faster_passes_and_reaps(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()
        rep = self._run(tmp_path, clock, cf,
                        dispatch=fake_dispatch(clock, closure_wall=9.0,
                                               baseline_wall=5.0))
        v = rep["verdict"]
        assert v["eligible_and_passed"] and v["failed_gates"] == []
        assert v["persistent_p95_wall_s"] == 9.0
        assert v["p95_improvement_fraction"] == pytest.approx(0.1)
        assert rep["pool_warmup_queries"] == 0
        assert all(c.closed for c in cf.created)
        assert (tmp_path / "campaign" / "persistent_sumo_report.json").exists()

    def test_each_member_runs_the_timed_sequence(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()
        self._run(tmp_path, clock, cf,
                  dispatch=fake_dispatch(clock, closure_wall=9.0, baseline_wall=5.0))
        assert all(c.calls[:4] == ["load", "advance", "collect_live_health",
                                   "finalize"] for c in cf.created)

    def test_divergent_per_seed_is_not_a_pass(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()
        prep = FakePrep(); prep.flows_of = lambda case, seed: {"e0": [seed, 99],
                                                               "e1": [7, 7]}
        rep = self._run(tmp_path, clock, cf, prep=prep,
                        dispatch=fake_dispatch(clock, closure_wall=9.0,
                                               baseline_wall=5.0))
        assert not rep["verdict"]["eligible_and_passed"]
        assert any(g.startswith("digest_mismatch")
                   for g in rep["verdict"]["failed_gates"])

    def test_unhealthy_seed_is_not_a_pass(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()
        prep = FakePrep(); prep.health_ok = False
        rep = self._run(tmp_path, clock, cf, prep=prep,
                        dispatch=fake_dispatch(clock, closure_wall=9.0,
                                               baseline_wall=5.0))
        assert not rep["verdict"]["eligible_and_passed"]
        assert any(g.startswith("seed_health")
                   for g in rep["verdict"]["failed_gates"])

    def test_member_fault_without_fallback_reaps(self, tmp_path):
        clock = FakeClock(); cf = connector_factory(fault={1: "finalize"})
        with pytest.raises(h.MemberFault):
            self._run(tmp_path, clock, cf,
                      dispatch=fake_dispatch(clock, closure_wall=9.0,
                                             baseline_wall=5.0))
        assert all(c.closed for c in cf.created)

    def test_member_fault_with_fallback_is_ineligible(self, tmp_path):
        clock = FakeClock(); cf = connector_factory(fault={1: "finalize"})

        def fallback(slot, query, reason):
            return {"member": slot["member"], "seed": slot["seed"],
                    "variant": slot["variant"],
                    **_seed_out(query["case"], slot["seed"], slot["variant"])}
        rep = self._run(tmp_path, clock, cf, fallback=fallback,
                        dispatch=fake_dispatch(clock, closure_wall=9.0,
                                               baseline_wall=5.0))
        v = rep["verdict"]
        assert not v["eligible_and_passed"]
        assert ("fallback_used" in v["failed_gates"]
                or "member_fault" in v["failed_gates"])
        assert all(c.closed for c in cf.created)

    def test_deadline_aborts_reaps_and_faults(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()
        with pytest.raises(h.MemberFault, match="deadline exceeded"):
            self._run(tmp_path, clock, cf,
                      dispatch=fake_dispatch(clock, closure_wall=9.0,
                                             baseline_wall=5.0, timeout=True))
        assert all(c.aborted for c in cf.created)

    def test_cleanup_reaps_on_unexpected_exception(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()

        def boom_ref(slot, query, prep, registry=None):
            raise RuntimeError("ref blew up")
        with pytest.raises(h.MemberFault, match="ref blew up"):
            self._run(tmp_path, clock, cf, ref=boom_ref,
                      dispatch=fake_dispatch(clock, closure_wall=9.0,
                                             baseline_wall=5.0))
        assert all(c.closed for c in cf.created)

    def test_no_member_crosses_slots(self, tmp_path):
        clock = FakeClock(); cf = connector_factory()
        self._run(tmp_path, clock, cf,
                  dispatch=fake_dispatch(clock, closure_wall=9.0, baseline_wall=5.0))
        assert [c.slot["member"] for c in cf.created] == [0, 1, 2]

    def test_an_unreapable_persistent_member_fails_the_run_closed(self, tmp_path):
        # A member whose graceful close cannot prove it reaped is an orphan:
        # the run must abort, never return a report as if cleanup succeeded.
        clock = FakeClock(); cf = connector_factory(close_fault={1})
        with pytest.raises(h.ExperimentAborted, match="reaped"):
            self._run(tmp_path, clock, cf,
                      dispatch=fake_dispatch(clock, closure_wall=9.0,
                                             baseline_wall=5.0))
        assert not (tmp_path / "campaign" / "persistent_sumo_report.json").exists()

    def test_an_inflight_fault_does_not_suppress_a_leaked_member(self, tmp_path):
        # A reference fault raises inside the body; a persistent member that
        # cannot be reaped on the resulting shutdown must STILL surface as the
        # orphan abort, not be hidden behind the in-flight MemberFault.
        clock = FakeClock(); cf = connector_factory(close_fault={1})

        def boom_ref(slot, query, prep, registry=None):
            raise RuntimeError("ref blew up")
        with pytest.raises(h.ExperimentAborted, match="reaped"):
            self._run(tmp_path, clock, cf, ref=boom_ref,
                      dispatch=fake_dispatch(clock, closure_wall=9.0,
                                             baseline_wall=5.0))
        assert not (tmp_path / "campaign" / "persistent_sumo_report.json").exists()


# ── Real drivers wired behind the lazy import (criteria 1, 5, 8, 12) ─────────
class TestRealDriversWired:

    def test_execute_touches_traci_only_via_lazy_import_then_aborts(
            self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(h, "verify_environment", lambda c: None)

        def no_traci():
            raise ImportError("traci unavailable")
        monkeypatch.setattr(h, "_import_traci", no_traci)
        monkeypatch.setattr(h, "run_experiment", lambda *a, **k:
                            (_ for _ in ()).throw(AssertionError("must not run")))
        artifact = tmp_path / "root"
        monkeypatch.setattr(sys, "argv", [
            "x", "--campaign", str(CONTRACT), "--execute",
            "--artifact-dir", str(artifact)])
        assert h.main() == 2
        assert "aborted" in capsys.readouterr().err.lower()
        assert not artifact.exists()

    def test_bodies_are_real_not_stubbed(self):
        src = h.HARNESS.read_text()
        assert "build_sumo_args(" in src         # shared builder, both arms
        assert "subprocess.Popen(" in src         # reference launches a child
        assert ".simulationStep(" in src          # persistent advances
        assert "rs.build_scenario_payload(" in src   # shared production builder
        assert "rs.build_trajectory_payload(" in src
        assert "cm.active_closure_throughput(" in src
        assert "rs.truncate_stranded_vehicles(" in src
        assert "rs.structured_closures([], [KNOWN_CLOSURE]" in src  # whole-edge
        assert "is not wired" not in src

    def test_construction_functions_exist(self):
        for n in ("real_connector_factory", "real_reference_runner",
                  "real_fallback_runner", "build_assembler_context",
                  "ScenarioAssembler", "_TraciConnector", "_SharedPrep"):
            assert hasattr(h, n)


# ── Real composition: _SharedPrep / context / registered abort (crit 2,5,6,8) ─
class TestRealSharedPrepComposition:
    """Exercise the REAL `_SharedPrep` parsers/measurement/reconciliation on
    tiny static SUMO-shaped fixtures (no SUMO process). Proves the seam
    composition, not just isolated helpers."""

    def _write_outputs(self, wd, *, closed, inserted=10, in_file=10,
                       closed_flow=0):
        # edgeData excludeEmpty output: one interval, a normal edge, and
        # (unless closed_flow) the closed edge is ABSENT (measured zero).
        rows = ['<meandata>', '  <interval begin="0" end="90000">',
                '    <edge id="e_normal" entered="5"/>']
        if closed and closed_flow:
            rows.append(f'    <edge id="{h.KNOWN_CLOSURE}" entered="{closed_flow}"/>')
        rows += ['  </interval>', '</meandata>']
        (wd / "edgedata.xml").write_text("\n".join(rows))
        (wd / "statistics.xml").write_text(
            f'<statistics><vehicles loaded="{inserted}" inserted="{inserted}" '
            f'running="0" waiting="0"/><teleports total="0"/>'
            f'<safety collisions="0"/></statistics>')
        # vehroute: `in_file` finished vehicles on e_normal.
        veh = "".join(
            f'<vehicle id="v{i}" depart="{i}"><route edges="e_normal" '
            f'exitTimes="{i}"/></vehicle>' for i in range(in_file))
        (wd / "vehroutes.xml").write_text(f'<routes>{veh}</routes>')

    def _prep(self):
        return h._SharedPrep({"e_normal", h.KNOWN_CLOSURE}, h.DURATION_S,
                             "2025-09-16T00:00:00")

    def _query(self, closed):
        return {"index": 0, "case": "closure" if closed else "baseline",
                "closed_edges": [h.KNOWN_CLOSURE] if closed else []}

    def test_measurement_closures_have_begin_s_end_s(self):
        cl = self._prep().measurement_closures([h.KNOWN_CLOSURE])
        assert cl and all("begin_s" in c and "end_s" in c for c in cl)
        assert cl[0]["edge_id"] == h.KNOWN_CLOSURE

    def test_read_outputs_retains_the_measured_zero_closed_edge(self, tmp_path):
        wd = tmp_path / "m0"; wd.mkdir()
        self._write_outputs(wd, closed=True, closed_flow=0)
        out = h._SharedPrep({"e_normal", h.KNOWN_CLOSURE}, h.DURATION_S,
                            "2025-09-16T00:00:00").read_outputs(
            wd, 1000, "q50", self._query(closed=True))
        # closed edge present as a measured zero, and integrity is clean.
        assert h.KNOWN_CLOSURE in out["flows"]
        assert out["active_closure_entries"] == 0

    def test_read_outputs_flags_a_leaking_closed_edge(self, tmp_path):
        wd = tmp_path / "leak"; wd.mkdir()
        self._write_outputs(wd, closed=True, closed_flow=50)
        out = h._SharedPrep({"e_normal", h.KNOWN_CLOSURE}, h.DURATION_S,
                            "2025-09-16T00:00:00").read_outputs(
            wd, 1000, "q50", self._query(closed=True))
        assert out["active_closure_entries"] and out["active_closure_entries"] > 0

    def test_trajectory_uses_selected_route_and_reconciles(self, tmp_path):
        wd = tmp_path / "traj"; wd.mkdir()
        self._write_outputs(wd, closed=True, inserted=10, in_file=10)
        prep = self._prep()
        prepared = prep.prepare  # noqa: not called (needs real net); set cache:
        prep._selected_route[str(wd)] = wd / "calibrated_close.rou.xml"
        (wd / "calibrated_close.rou.xml").write_text("<routes/>")
        out = prep.read_outputs(wd, h.TRAJECTORY_SEED, "q50",
                                self._query(closed=True))
        assert out["trajectory"] is not None
        assert out["trajectory"]["variant"] == "calibrated_close.rou.xml"

    def test_a_short_vehroute_file_withholds_the_trajectory(self, tmp_path):
        wd = tmp_path / "short"; wd.mkdir()
        self._write_outputs(wd, closed=False, inserted=100, in_file=10)  # 10%
        prep = self._prep()
        prep._selected_route[str(wd)] = h.SUMO_DIR / "calibrated.rou.xml"
        out = prep.read_outputs(wd, h.TRAJECTORY_SEED, "q50",
                                self._query(closed=False))
        assert out["trajectory"] is None            # < 98% reconciliation


class TestRealAssemblerContextIsFaithful:
    """`build_assembler_context` reads the real static net/demand/geojson (no
    SUMO) and must reproduce production's ScenarioSpec fields."""

    def test_spec_has_production_identity(self):
        ctx = h.build_assembler_context()
        base = ctx["spec_for"]("baseline")
        clos = ctx["spec_for"]("closure")
        # non-empty network_build_id (the rev1 bug), baseline start != end,
        # ISO closure times (not "00:00").
        assert base.network_build_id and len(base.network_build_id) == 64
        assert base.start_time != base.end_time
        assert clos.closures and "T" in str(clos.closures[0].start_time)
        assert ctx["label_for"]("closure").startswith("Avstängning")
        assert ctx["label_for"]("baseline").startswith("Baslinje")


class TestRegisteredReferenceChildIsAbortable:
    """A reference query registers each launched child so a query-wide deadline
    terminates+reaps it — driven with a fake launcher (no SUMO)."""

    class _FakeProc:
        def __init__(self):
            self.killed = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def test_children_are_registered_and_abortable(self, tmp_path):
        procs = []

        def launch(args, wd):
            p = self._FakeProc(); procs.append(p); return p

        class _Prep:
            def prepare(self, wd, seed, variant, query):
                return {"route": Path(wd) / "r.rou.xml", "truncated": 0,
                        "dropped": 0}

            def read_outputs(self, wd, seed, variant, query):
                return {"flows": {"e0": [1]}, "seed_health":
                        _good_health(seed, variant),
                        "active_closure_entries": None, "trajectory": None}
        runner = h.real_reference_runner(tmp_path, launch=launch)
        registry = h.ChildRegistry()
        query = {"index": 0, "case": "baseline", "closed_edges": [],
                 "slots": list(h.SEED_MEMBER_MAP)}
        runner(query["slots"][0], query, _Prep(), registry)
        assert len(procs) == 1 and registry.procs == procs
        registry.abort_all()
        assert all(p.killed for p in procs)


# ── Filesystem safety (criterion 12) ─────────────────────────────────────────
class TestFilesystemSafety:

    def test_refuses_existing_root(self, tmp_path):
        root = tmp_path / "e"; root.mkdir()
        with pytest.raises(h.ExperimentAborted, match="already exists"):
            h.prepare_campaign_root(root)

    def test_refuses_symlink(self, tmp_path):
        t = tmp_path / "t"; t.mkdir()
        link = tmp_path / "l"; link.symlink_to(t)
        with pytest.raises(h.ExperimentAborted, match="symlink"):
            h.prepare_campaign_root(link)

    def test_refuses_escape(self, tmp_path):
        with pytest.raises(h.ExperimentAborted, match=r"\.\."):
            h.prepare_campaign_root(tmp_path / ".." / "esc")

    def test_creates_absent_root(self, tmp_path):
        assert h.prepare_campaign_root(tmp_path / "fresh").is_dir()


# ── Freeze integrity (criteria 11, 13) ───────────────────────────────────────
class TestFreezeIntegrity:

    def test_historical_fingerprints_are_well_formed_and_drift_refuses(self):
        c = json.loads(CONTRACT.read_text())
        for label in (
            "harness:benchmark_persistent_sumo.py",
            "source:run_scenario.py",
        ):
            frozen = c["frozen_fingerprints"][label]
            assert len(frozen) == 64
            assert set(frozen) <= set("0123456789abcdef")
        # A completed experiment's frozen contract is historical evidence,
        # not a manifest that should be rewritten after every source change.
        # When the live tree differs, the executable gate must refuse it.
        if h.file_fingerprints() != c["frozen_fingerprints"]:
            with pytest.raises(h.ExperimentAborted, match="fingerprints drifted"):
                h.verify_environment(c)
        assert c["outcomes_present_at_freeze"] is False
        assert c["gates"]["authorizes"] == h.ADOPTION_AUTHORIZES_NOTHING

    def test_carries_no_outcome_or_measured_value(self):
        c = json.loads(CONTRACT.read_text())
        assert c["outcomes_present_at_freeze"] is False
        for banned in ("verdict", "persistent_queries", "report"):
            assert banned not in c

        def floats(node, path=""):
            if isinstance(node, dict):
                for k, v in node.items():
                    yield from floats(v, f"{path}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    yield from floats(v, f"{path}[{i}]")
            elif isinstance(node, float):
                yield path
        allowed = {".gates.max_parallel_p95_wall_s",
                   ".gates.min_p95_improvement_fraction"}
        assert [p for p in floats(c) if p not in allowed] == []
        assert not (Path("runs") / "persistent-sumo" / c["content_key"]).exists()
        assert not Path("validation/persistent_sumo_report_v1.json").exists()


# ── Deeper contract strictness (criterion 3) ─────────────────────────────────
class TestDeeperContractStrictness:

    def _mutate(self, tmp_path, fn):
        c = json.loads(CONTRACT.read_text())
        fn(c)
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        return p

    @pytest.mark.parametrize("fn, match", [
        (lambda c: c.pop("expected_sumo_version"), "non-empty string"),
        (lambda c: c.pop("expected_platform"), "non-empty string"),
        (lambda c: c.__setitem__("expected_sumo_version", None),
         "non-empty string"),
        (lambda c: c.pop("lineage"), "missing required"),
        (lambda c: c.pop("report_schema"), "missing required"),
        (lambda c: c["demand_identity"].__setitem__("extra", 1),
         "exactly build_id"),
        (lambda c: c["execution"]["lifecycle"].__setitem__("surprise", 1),
         "unknown field"),
        (lambda c: c["execution"].pop("option_template"), "option_template"),
        (lambda c: c["report_schema"].__setitem__("surprise", 1),
         "unknown field"),
        (lambda c: c["report_schema"].__setitem__("per_query", ["index"]),
         "report_schema.per_query"),
        (lambda c: c["report_schema"].__setitem__("verdict", ["failed_gates"]),
         "report_schema.verdict"),
        (lambda c: c["report_schema"]["top_level"].remove("member_events"),
         "report_schema.top_level"),
        (lambda c: c["execution"]["timer_semantics"].__setitem__("x", 1),
         "unknown field"),
        (lambda c: c["matrix"]["closure"].__setitem__("x", 1), "unknown field"),
        (lambda c: c.__setitem__("outcomes_present_at_freeze", True),
         "outcomes_present_at_freeze"),
        # Value-binding (not just presence): a re-keyed mutation of any frozen
        # execution VALUE must be refused.
        (lambda c: c["execution"]["option_template"].append("--extra x"),
         "option_template"),
        (lambda c: c["execution"].__setitem__("persistent_arm_only_options",
                                              ["--remote-port 1"]),
         "persistent_arm_only_options"),
        (lambda c: c["execution"].__setitem__(
            "shared_production_builders", ["run_scenario.build_scenario_payload"]),
         "shared_production_builders"),
        (lambda c: c["execution"]["lifecycle"].__setitem__(
            "cleanup", "reworded"), "exact frozen text"),
        (lambda c: c["execution"]["timer_semantics"].__setitem__(
            "parallel_wall_method", "reworded"), "parallel_wall_method"),
        # Lineage strictness + nested member-event report schema.
        (lambda c: c["lineage"].__setitem__("surprise", 1), "unknown field"),
        (lambda c: c["lineage"].pop("supersedes"),
         "outcome_access, selected_by, supersedes"),
        (lambda c: c["report_schema"]["per_query"].remove("member_events"),
         "report_schema.per_query"),
        (lambda c: c["report_schema"].pop("member_event"),
         "member_event and member_event_entry"),
        (lambda c: c["report_schema"]["member_event"].remove("events"),
         "report_schema.member_event"),
        (lambda c: c["report_schema"]["member_event_entry"].remove("error"),
         "report_schema.member_event_entry"),
        (lambda c: c["report_schema"]["top_level"].remove("content_key"),
         "report_schema.top_level"),
    ])
    def test_a_re_keyed_but_invalid_contract_is_refused(self, tmp_path, fn, match):
        with pytest.raises(h.ContractRefused, match=match):
            h.load_contract(self._mutate(tmp_path, fn))

    def test_a_renamed_or_moved_contract_path_is_refused_by_the_cli(
            self, tmp_path, monkeypatch, capsys):
        # A byte-identical copy at a different path still recomputes its key, but
        # the CLI runs ONLY the canonical frozen path.
        copy = tmp_path / "persistent_sumo_campaign_v2.json"
        copy.write_text(CONTRACT.read_text())
        monkeypatch.setattr(sys, "argv",
                            ["x", "--campaign", str(copy),
                             "--validate-contract-only"])
        assert h.main() == 2
        assert "canonical" in capsys.readouterr().err


# ── Row-schema / health integrity in the gate (criterion 9) ──────────────────
class TestProofRowSchema:

    def test_a_missing_wall_time_is_malformed(self):
        per, ref, f, fb = _rows()
        del per[0]["parallel_wall_s"]
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])

    def test_a_negative_health_count_is_not_healthy(self):
        per, ref, f, fb = _rows()
        per[1]["seed_health"][0]["teleports"] = -1
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("seed_health") for g in v["failed_gates"])

    def test_a_non_integer_health_count_is_not_healthy(self):
        per, ref, f, fb = _rows()
        per[1]["seed_health"][0]["loaded"] = None
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("seed_health") for g in v["failed_gates"])

    def test_an_unknown_row_field_is_malformed(self):
        per, ref, f, fb = _rows()
        per[2]["surprise"] = 1          # exact schema, not a subset
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])

    def test_a_missing_fallbacks_field_is_malformed(self):
        per, ref, f, fb = _rows()
        del per[3]["fallbacks"]
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])

    def test_a_wrong_seed_variant_is_not_healthy(self):
        per, ref, f, fb = _rows()
        # seed 1000 must carry the q50 route family; q90 is a broken binding.
        per[1]["seed_health"][0]["variant"] = "q90"
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("seed_health") for g in v["failed_gates"])

    def test_an_arbitrary_member_events_entry_is_malformed(self):
        per, ref, f, fb = _rows()
        per[0]["member_events"] = [{"unexpected": True}]   # not {member,seed,error}
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])

    def test_a_well_formed_member_events_entry_is_accepted(self):
        per, ref, f, fb = _rows()
        # One recorded event must be paired with exactly one fallback.
        per[0]["member_events"] = [{"member": 1, "seed": 1001, "error": "boom"}]
        per[0]["fallbacks"] = 1
        assert h._row_schema_ok(per[0])

    def test_a_fault_event_with_zero_fallbacks_is_inconsistent(self):
        per, ref, f, fb = _rows()
        per[0]["member_events"] = [{"member": 1, "seed": 1001, "error": "boom"}]
        per[0]["fallbacks"] = 0                # event with no fallback
        assert not h._row_schema_ok(per[0])

    def test_a_cross_bound_member_seed_pair_is_malformed(self):
        per, ref, f, fb = _rows()
        # member 0 is bound to seed 1000; pairing it with 1001 is corrupt.
        per[0]["member_events"] = [{"member": 0, "seed": 1001, "error": "boom"}]
        per[0]["fallbacks"] = 1
        assert not h._row_schema_ok(per[0])

    def test_a_malformed_wall_time_fails_closed_without_a_typeerror(self):
        per, ref, f, fb = _rows()
        # A string wall on a closure row must not reach percentile arithmetic.
        per[1]["parallel_wall_s"] = "oops"   # index 1 is a closure query
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])
        assert "latency_sample_incomplete" in v["failed_gates"]
        assert v["persistent_p95_wall_s"] is None

    @pytest.mark.parametrize("mutate, gate", [
        # Malformed NESTED evidence must fail the verdict, never raise out of
        # the gate (the evaluator is the fail-closed boundary).
        (lambda p: p[1].__setitem__("seed_health", ["a", "b", "c"]), "seed_health"),
        (lambda p: p[1].__setitem__("seed_health", [1, 2, 3]), "seed_health"),
        (lambda p: p[1]["seed_health"][0].__setitem__("seed", None), "seed_health"),
        (lambda p: p[1]["seed_health"][0].__setitem__("seed", "1000"), "seed_health"),
        (lambda p: p.__setitem__(1, "garbage"), "malformed_row"),
        (lambda p: p[1].__setitem__("index", None), "malformed_row"),
        (lambda p: p[1].__setitem__("index", ["unhashable"]), "malformed_row"),
    ])
    def test_malformed_nested_evidence_fails_closed_without_crashing(
            self, mutate, gate):
        per, ref, f, fb = _rows()
        mutate(per)
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)   # must not raise
        assert not v["eligible_and_passed"]
        assert any(g.startswith(gate) for g in v["failed_gates"])

    @pytest.mark.parametrize("index", [99, -99, -1, 10])
    def test_an_out_of_range_index_fails_closed_without_crashing(self, index):
        # Out-of-range indices must never reach QUERY_ORDER[i]: a large one
        # would raise IndexError, a negative one would wrap onto another query.
        per, ref, f, fb = _rows()
        per[1]["index"] = index
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)   # must not raise
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])

    def test_a_reference_row_can_never_carry_a_fallback(self):
        # The reference arm has no cold fallback; such a row is impossible.
        per, ref, f, fb = _rows()
        ref[1]["member_events"] = [{"member": 1, "seed": 1001, "error": "boom"}]
        ref[1]["fallbacks"] = 1
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])
        assert h._row_schema_ok(ref[1], arm="persistent")          # fine here
        assert not h._row_schema_ok(ref[1], arm="reference")       # never here

    def test_duplicate_member_events_are_impossible_evidence(self):
        # One query has three fixed members, each faulting at most once.
        per, ref, f, fb = _rows()
        per[1]["member_events"] = [{"member": 1, "seed": 1001, "error": "b"}] * 4
        per[1]["fallbacks"] = 4
        v = h.evaluate(per, ref, member_faults=4, fallbacks=4)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("malformed_row") for g in v["failed_gates"])

    def test_row_fallbacks_must_reconcile_with_the_global_counters(self):
        per, ref, _, _ = _rows()
        per[1]["member_events"] = [{"member": 1, "seed": 1001, "error": "b"}]
        per[1]["fallbacks"] = 1
        # Global counters claim a clean run while a row records a fallback.
        v = h.evaluate(per, ref, member_faults=0, fallbacks=0)
        assert not v["eligible_and_passed"]
        assert "fallback_count_mismatch" in v["failed_gates"]
        assert "member_fault_count_mismatch" in v["failed_gates"]

    def test_an_unknown_seed_health_key_is_rejected(self):
        per, ref, f, fb = _rows()
        per[1]["seed_health"][0]["smuggled"] = True
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("seed_health") for g in v["failed_gates"])

    def test_a_baseline_row_may_not_carry_closure_proof(self):
        per, ref, f, fb = _rows()
        assert h.QUERY_ORDER[0] == "baseline"
        per[0]["closure_integrity"] = "verified_clean"
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert any(g.startswith("closure_integrity") for g in v["failed_gates"])

    def test_non_list_row_collections_fail_closed(self):
        per, ref, f, fb = _rows()
        v = h.evaluate("not-a-list", ref, member_faults=f, fallbacks=fb)
        assert not v["eligible_and_passed"]
        assert "malformed_rows" in v["failed_gates"]

    def test_a_production_route_name_variant_is_healthy(self):
        # Production tags health by the route FILENAME, not the q-alias; the
        # closure route family must still resolve to the seed's variant.
        per, ref, f, fb = _rows()
        for row in (per, ref):
            for r in row:
                for hh, fam in zip(r["seed_health"],
                                   (f"calibrated.rou_close_{h.KNOWN_CLOSURE}.rou.xml",
                                    f"calibrated_v1.rou_close_{h.KNOWN_CLOSURE}.rou.xml",
                                    f"calibrated_v2.rou_close_{h.KNOWN_CLOSURE}.rou.xml")):
                    hh["variant"] = fam
        v = h.evaluate(per, ref, member_faults=f, fallbacks=fb)
        assert v["eligible_and_passed"] and v["failed_gates"] == []


# ── Per-member fault isolation + events (criterion 8) ────────────────────────
class TestPerMemberFault:

    def _run(self, tmp_path, clock, cf, **kw):
        return TestRunExperiment()._run(tmp_path, clock, cf, **kw)

    def test_only_the_faulting_member_falls_back(self, tmp_path):
        clock = FakeClock(); cf = connector_factory(fault={1: "finalize"})
        seen = {"members": []}

        def fallback(slot, query, reason):
            seen["members"].append(slot["member"])
            return {"member": slot["member"], "seed": slot["seed"],
                    "variant": slot["variant"],
                    **_seed_out(query["case"], slot["seed"], slot["variant"])}
        rep = self._run(tmp_path, clock, cf, fallback=fallback,
                        dispatch=fake_dispatch(clock, closure_wall=9.0,
                                               baseline_wall=5.0))
        # ONLY member 1 fell back, every query, and the report records it.
        assert set(seen["members"]) == {1}
        assert rep["member_faults"] >= 1
        assert rep["member_events"] and all(
            e["events"] and e["events"][0]["member"] == 1
            for e in rep["member_events"])
        assert not rep["verdict"]["eligible_and_passed"]  # any fallback => fail

    def test_a_query_deadline_with_a_fallback_records_ineligible_evidence(
            self, tmp_path):
        # A query-wide deadline must NOT crash the run when a cold fallback
        # exists: it aborts+retires every member, records the deadline events,
        # falls back each seed, and the verdict is ineligible.
        clock = FakeClock(); cf = connector_factory()
        seen = {"n": 0}

        def fallback(slot, query, reason):
            seen["n"] += 1
            assert "deadline" in reason
            return {"member": slot["member"], "seed": slot["seed"],
                    "variant": slot["variant"],
                    **_seed_out(query["case"], slot["seed"], slot["variant"])}
        rep = self._run(tmp_path, clock, cf, fallback=fallback,
                        dispatch=fake_dispatch(clock, closure_wall=9.0,
                                               baseline_wall=5.0, timeout=True))
        assert seen["n"] > 0
        assert rep["member_faults"] > 0 and rep["member_events"]
        assert all("deadline" in ev["error"]
                   for e in rep["member_events"] for ev in e["events"])
        assert not rep["verdict"]["eligible_and_passed"]
        assert all(c.aborted for c in cf.created)


# ── Reference cleanup terminals (criterion 8) ────────────────────────────────
class TestReferenceChildCleanupTerminals:

    class _FakeProc:
        def __init__(self, rc=0, raises=False, reap_timeout=False):
            self.rc = rc; self.raises = raises; self.reap_timeout = reap_timeout
            self.killed = False

        def wait(self, timeout=None):
            if self.reap_timeout:
                raise subprocess.TimeoutExpired("sumo", timeout or 5)
            if self.raises:
                raise RuntimeError("child crashed")
            return self.rc

        def kill(self):
            self.killed = True

    class _Prep:
        def prepare(self, wd, seed, variant, query):
            return {"route": Path(wd) / "r.rou.xml", "truncated": 0, "dropped": 0}

        def read_outputs(self, wd, seed, variant, query):
            return {"flows": {"e0": [1]}, "seed_health": _good_health(seed, variant),
                    "active_closure_entries": None, "trajectory": None}

    def _query(self):
        return {"index": 0, "case": "baseline", "closed_edges": [],
                "slots": list(h.SEED_MEMBER_MAP)}

    class _PrepReadRaises:
        def prepare(self, wd, seed, variant, query):
            return {"route": Path(wd) / "r.rou.xml", "truncated": 0, "dropped": 0}

        def read_outputs(self, wd, seed, variant, query):
            raise RuntimeError("parse blew up")

    def test_a_worker_fault_with_reaped_children_is_a_memberfault(self):
        # A worker fault AFTER a clean wait (here, parsing raises) leaves every
        # child reaped, so it is a MemberFault, not an orphan abort.
        procs = []

        def launch(args, wd):
            p = TestReferenceChildCleanupTerminals._FakeProc(rc=0)
            procs.append(p); return p
        runner = h.real_reference_runner(Path("/tmp/x"), launch=launch)
        with pytest.raises(h.MemberFault):
            h.run_reference_query(self._query(), runner, FakeAssembler(),
                                  self._PrepReadRaises(), dispatch=h.thread_dispatch,
                                  clock=FakeClock())
        assert procs and all(p.killed for p in procs)

    def test_an_unknown_wait_error_is_an_unproved_reap_orphan(self):
        # wait() raising something other than a returned code is NOT proof of
        # reap — it must surface as an orphan ExperimentAborted.
        procs = []

        def launch(args, wd):
            p = TestReferenceChildCleanupTerminals._FakeProc(raises=True)
            procs.append(p); return p
        runner = h.real_reference_runner(Path("/tmp/x"), launch=launch)
        with pytest.raises(h.ExperimentAborted, match="reaped"):
            h.run_reference_query(self._query(), runner, FakeAssembler(),
                                  self._Prep(), dispatch=h.thread_dispatch,
                                  clock=FakeClock())
        assert procs and all(p.killed for p in procs)

    def test_a_nonzero_exit_faults_and_aborts(self):
        procs = []

        def launch(args, wd):
            p = TestReferenceChildCleanupTerminals._FakeProc(rc=1)
            procs.append(p); return p
        runner = h.real_reference_runner(Path("/tmp/x"), launch=launch)
        with pytest.raises(h.MemberFault):
            h.run_reference_query(self._query(), runner, FakeAssembler(),
                                  self._Prep(), dispatch=h.thread_dispatch,
                                  clock=FakeClock())
        assert procs and all(p.killed for p in procs)

    def test_a_child_that_cannot_be_reaped_is_surfaced_not_swallowed(self):
        # A kill+wait that keeps timing out is an orphan; the run must fail
        # closed (ExperimentAborted), never quietly accept it.
        procs = []

        def launch(args, wd):
            p = TestReferenceChildCleanupTerminals._FakeProc(reap_timeout=True)
            procs.append(p); return p
        runner = h.real_reference_runner(Path("/tmp/x"), launch=launch)
        with pytest.raises(h.ExperimentAborted, match="could not be"):
            h.run_reference_query(self._query(), runner, FakeAssembler(),
                                  self._Prep(), dispatch=h.thread_dispatch,
                                  clock=FakeClock())
        assert procs and all(p.killed for p in procs)

    def test_abort_all_records_unreaped_children(self):
        reg = h.ChildRegistry()
        good = TestReferenceChildCleanupTerminals._FakeProc(rc=0)
        stuck = TestReferenceChildCleanupTerminals._FakeProc(reap_timeout=True)
        reg.register(good); reg.register(stuck)
        unreaped = reg.abort_all()
        assert unreaped == [stuck] and good.killed and stuck.killed

    def test_reap_process_proves_reap_only_on_a_returned_wait(self):
        FP = TestReferenceChildCleanupTerminals._FakeProc
        assert h._reap_process(FP(rc=0)) is True
        assert h._reap_process(FP(rc=7)) is True          # any returned code
        assert h._reap_process(FP(reap_timeout=True)) is False
        # An unknown wait error is NOT proof of reap.
        assert h._reap_process(FP(raises=True)) is False

    def test_a_query_deadline_with_an_unreapable_child_is_an_orphan_abort(self):
        # The DEADLINE path (dispatch raises) must also fail closed on an
        # un-reapable registered child, not re-raise the bare deadline fault.
        procs = []

        def launch(args, wd):
            p = TestReferenceChildCleanupTerminals._FakeProc(reap_timeout=True)
            procs.append(p); return p
        runner = h.real_reference_runner(Path("/tmp/x"), launch=launch)

        def deadline_dispatch(jobs, timeout_s, on_timeout=None):
            for job in jobs:                      # register the children first
                try:
                    job()
                except Exception:                 # noqa: BLE001
                    pass
            if on_timeout is not None:
                on_timeout()
            raise h.MemberFault("per-query deadline exceeded")
        with pytest.raises(h.ExperimentAborted, match="orphan"):
            h.run_reference_query(self._query(), runner, FakeAssembler(),
                                  self._Prep(), dispatch=deadline_dispatch,
                                  clock=FakeClock())
        assert procs and all(p.killed for p in procs)


# ── _TraciConnector + cold-fallback lifecycle (criteria 7, 8) ────────────────
class TestTraciConnectorLifecycle:

    _SLOT = {"member": 0, "seed": 1000, "variant": "q50"}
    FP = TestReferenceChildCleanupTerminals._FakeProc

    def test_a_failed_connect_reaping_its_child_reraises_the_connect_error(self):
        proc = self.FP(rc=0)
        with pytest.raises(RuntimeError, match="connect boom"):
            h._TraciConnector(self._SLOT, "/tmp", None, 1, spawn=lambda: proc,
                              connect=lambda p: (_ for _ in ()).throw(
                                  RuntimeError("connect boom")))
        assert proc.killed          # the spawned child was reaped

    def test_a_failed_connect_with_an_unreapable_child_is_an_orphan(self):
        proc = self.FP(reap_timeout=True)
        with pytest.raises(h.ExperimentAborted, match="orphan"):
            h._TraciConnector(self._SLOT, "/tmp", None, 1, spawn=lambda: proc,
                              connect=lambda p: (_ for _ in ()).throw(
                                  RuntimeError("connect boom")))
        assert proc.killed

    def test_close_raises_on_an_unreapable_process(self):
        proc = self.FP(reap_timeout=True)
        c = h._TraciConnector(self._SLOT, "/tmp", None, 1, spawn=lambda: proc,
                              connect=lambda p: None)
        with pytest.raises(h.MemberFault, match="could not be reaped"):
            c.close()

    def test_a_graceful_close_waits_and_does_not_kill_a_cooperative_child(self):
        # The frozen lifecycle text is "graceful-close timeout kills then
        # reaps": a child that exits within the wait window is never killed.
        calls = []

        class Coop:
            killed = False
            def wait(self, timeout=None): calls.append(f"wait({timeout})"); return 0
            def kill(self): calls.append("kill"); Coop.killed = True
        proc = Coop()
        c = h._TraciConnector(self._SLOT, "/tmp", None, 1, spawn=lambda: proc,
                              connect=lambda p: None)
        c.close()
        assert calls == ["wait(5)"] and not Coop.killed

    def test_a_graceful_close_timeout_escalates_to_kill_then_reap(self):
        # Times out once, then the forced reap succeeds.
        calls = []

        class Stubborn:
            def __init__(self): self.n = 0
            def wait(self, timeout=None):
                self.n += 1
                calls.append(f"wait{self.n}")
                if self.n == 1:
                    raise subprocess.TimeoutExpired("sumo", timeout or 5)
                return 0
            def kill(self): calls.append("kill")
        c = h._TraciConnector(self._SLOT, "/tmp", None, 1,
                              spawn=lambda: Stubborn(), connect=lambda p: None)
        c.close()                       # no raise: escalation reaped it
        assert calls == ["wait1", "kill", "wait2"]

    def test_abort_is_a_forced_kill_then_reap(self):
        calls = []

        class P:
            def wait(self, timeout=None): calls.append("wait"); return 0
            def kill(self): calls.append("kill")
        c = h._TraciConnector(self._SLOT, "/tmp", None, 1, spawn=lambda: P(),
                              connect=lambda p: None)
        c.abort()
        assert calls[0] == "kill"       # forced path kills first, then reaps


class TestColdFallbackCleanup:

    _SLOT = {"member": 0, "seed": 1000, "variant": "q50"}
    FP = TestReferenceChildCleanupTerminals._FakeProc

    def _query(self):
        return {"index": 0, "case": "baseline", "closed_edges": [],
                "slots": [dict(self._SLOT)]}

    class _PrepReadRaises:
        def prepare(self, wd, seed, variant, query):
            return {"route": Path(wd) / "r.rou.xml", "truncated": 0, "dropped": 0}

        def read_outputs(self, wd, seed, variant, query):
            raise RuntimeError("fallback parse blew up")

    class _PrepOk:
        def prepare(self, wd, seed, variant, query):
            return {"route": Path(wd) / "r.rou.xml", "truncated": 0, "dropped": 0}

        def read_outputs(self, wd, seed, variant, query):
            return {"flows": {"e0": [1]}, "seed_health": _good_health(seed, variant),
                    "active_closure_entries": None, "trajectory": None}

    def test_a_fallback_never_writes_into_the_reference_work_dir(self, tmp_path):
        # A cold fallback must not overwrite the reference arm's preserved
        # evidence for the same query/seed.
        class P:
            def wait(self, timeout=None): return 0
            def kill(self): pass
        slot, query = dict(self._SLOT), self._query()
        h.real_reference_runner(tmp_path, launch=lambda a, w: P())(
            slot, query, self._PrepOk(), h.ChildRegistry())
        h.real_fallback_runner(tmp_path, self._PrepOk(),
                               launch=lambda a, w: P())(slot, query, reason="fault")
        dirs = sorted(p.name for p in tmp_path.iterdir())
        assert len(dirs) == 2, dirs
        assert any(d.startswith("ref-") for d in dirs)
        assert any(d.startswith("fallback-") for d in dirs)

    def test_a_fallback_child_that_cannot_be_reaped_is_an_orphan(self, tmp_path):
        # A cold fallback whose child cannot be reaped must not discard its
        # throwaway registry and orphan the child.
        proc = self.FP(reap_timeout=True)
        runner = h.real_fallback_runner(tmp_path, self._PrepReadRaises(),
                                        launch=lambda args, wd: proc)
        with pytest.raises(h.ExperimentAborted, match="orphan"):
            runner(dict(self._SLOT), self._query(), reason="deadline")
        assert proc.killed


# ── Exact production-payload equality via the shared builder (criterion 2) ───
class TestScenarioDigestIsTheSharedProductionPayload:

    def test_digest_equals_a_direct_shared_builder_call(self):
        import run_scenario as rs
        ctx = {"web_edges": ["e0", "e1"], "prior": {"e0": 1.0, "e1": 1.0},
               "meta": {"epoch_sim": "2025-09-16T00:00:00", "source": "historical",
                        "agent_demand": None, "build_id": "b", "demand_build_key": "k",
                        "date": "2025-09-16", "begin": "00:00", "end": "24:00"},
               "sig": "sig", "window_label": "W", "sensor_audit": None,
               "closures": [{"edge_id": h.KNOWN_CLOSURE}],
               "spec_for": lambda case: _Spec(case),
               "sensor_audit_for": lambda case, ps, fo, arr: {"a": 1},
               "fit_errors_for": lambda case, audit: [],
               "name_for": lambda case: "baseline",
               "label_for": lambda case: "L"}
        per_seed = [{"seed": s, "variant": v, "flows": {"e0": [1, 1], "e1": [2, 2]},
                     "active_closure_entries": None,
                     "trajectory": None, "seed_health": _good_health(s, v),
                     "truncated": 0, "dropped": 0}
                    for s, v in ((1000, "q50"), (1001, "q10"), (1002, "q90"))]
        got = h.ScenarioAssembler(ctx).assemble("baseline", per_seed)["scenario_digest"]

        # Build the SAME payload directly through the shared production builder.
        import numpy as np
        arrays = [{e: np.asarray(v, dtype=float) for e, v in s["flows"].items()}
                  for s in per_seed]
        flows_out, conf_out = rs.aggregate_flows(arrays, {"e0", "e1"},
                                                 ctx["prior"], h.N_INTERVALS)
        payload = rs.build_scenario_payload(
            meta=ctx["meta"], n_intervals=h.N_INTERVALS, generated_at="",
            spec=_Spec("baseline"), traj_name=None, name="baseline", label="L",
            close_edges=[], closures=[], window_label="W", n_truncated=0,
            n_dropped=0, active_closure_entries=None,
            active_entries_by_seed=[None, None, None], closure_integrity=None,
            seed_count=3, seed_values=[1000, 1001, 1002], sig="sig",
            seed_health=[s["seed_health"] for s in per_seed],
            health_flags=rs.seed_health_flags([s["seed_health"] for s in per_seed]),
            multi_day_validation=None, sensor_audit={"a": 1},
            flows_out=flows_out, conf_out=conf_out)
        assert got == h.canonical_digest(payload)


# ── Member bootstrap composition (criteria 1, 4) ─────────────────────────────
class TestMemberBootstrap:
    """The v1 campaign died because members were launched with no network. These
    tests pin the real default-spawn composition WITHOUT a process or SUMO."""

    _SLOT = {"member": 0, "seed": 1000, "variant": "q50"}

    def test_the_builder_is_pure_and_network_backed(self):
        args = h.build_bootstrap_args(sumo_bin="/x/bin/sumo",
                                      net_path=h.NET_PATH, port=4321)
        assert args == ["/x/bin/sumo", "-n", str(h.NET_PATH.resolve()),
                        "--remote-port", "4321", "--num-clients", "1"]
        # The regression guard: a bootstrap without -n is what killed v1.
        assert "-n" in args and args[args.index("-n") + 1].endswith(".net.xml")

    def test_the_frozen_template_matches_the_builder_shape(self):
        args = h.build_bootstrap_args(sumo_bin="/x/bin/sumo",
                                      net_path=h.NET_PATH, port=1)
        flags = [a for a in args if a.startswith("-")]
        assert flags == [f.split(" ")[0] for f in h.BOOTSTRAP_TEMPLATE[1:]]

    def test_default_spawn_composes_exact_argv_cwd_and_session(
            self, monkeypatch, tmp_path):
        # Fake `sumo` package identity + intercepted Popen: no real SUMO, no
        # process, no socket.
        pkg = tmp_path / "sumopkg"; pkg.mkdir()
        fake = types.ModuleType("sumo"); fake.__file__ = str(pkg / "__init__.py")
        monkeypatch.setitem(sys.modules, "sumo", fake)
        seen = {}

        class FakePopen:
            def __init__(self, argv, cwd=None, start_new_session=None):
                seen.update(argv=argv, cwd=cwd, session=start_new_session)

            def wait(self, timeout=None):
                return 0

            def kill(self):
                seen["killed"] = True
        monkeypatch.setattr(h.subprocess, "Popen", FakePopen)
        wd = tmp_path / "member-0"; wd.mkdir()
        h._TraciConnector(self._SLOT, wd, None, 5555, connect=lambda p: None)
        assert seen["argv"] == [str(pkg / "bin" / "sumo"), "-n",
                                str(h.NET_PATH.resolve()),
                                "--remote-port", "5555", "--num-clients", "1"]
        assert seen["cwd"] == str(wd) and seen["session"] is True

    def test_a_timed_load_carries_no_bootstrap_only_option(self, tmp_path):
        # Criterion 2: the per-query load is the fresh-subprocess argument set.
        args = h.build_sumo_args(
            seed=1000, route_path=tmp_path / "r.rou.xml",
            add_paths=[tmp_path / "e.add.xml"],
            edgedata_out=tmp_path / "ed.xml", statistics_out=tmp_path / "st.xml",
            vehroute_out=tmp_path / "vr.xml", traci_server=False)
        assert "--remote-port" not in args and "--num-clients" not in args
        for flag in ("--mesosim", "-n", "-r", "-a", "--seed", "--begin", "--end",
                     "--statistic-output", "--vehroute-output"):
            assert flag in args


# ── Real production variant names (criterion 3) ──────────────────────────────
class TestProductionVariantNames:

    @pytest.mark.parametrize("name, family", [
        ("calibrated.rou.xml", "q50"),
        ("calibrated_v1.rou.xml", "q10"),
        ("calibrated_v2.rou.xml", "q90"),
        (f"calibrated.rou_close_{h.KNOWN_CLOSURE}.rou.xml", "q50"),
        (f"calibrated_v1.rou_close_{h.KNOWN_CLOSURE}.rou.xml", "q10"),
        (f"calibrated_v2.rou_close_{h.KNOWN_CLOSURE}.rou.xml", "q90"),
        ("q50", "q50"), ("q10", "q10"), ("q90", "q90"),
        ("something_else.rou.xml", None), ("", None), (None, None),
    ])
    def test_variant_family_maps_real_names(self, name, family):
        assert h._variant_family(name) == family

    def test_clean_closure_telemetry_with_real_names_is_healthy(self):
        # Exactly the shape the spent v1 run produced — it must now pass.
        records = [
            {"seed": s, "variant": f"{stem}_close_{h.KNOWN_CLOSURE}.rou.xml",
             "loaded": n, "inserted": n, "running_at_end": 0,
             "waiting_at_end": 0, "collisions": 0, "teleports": 0}
            for s, stem, n in ((1000, "calibrated.rou", 21656),
                               (1001, "calibrated_v1.rou", 20879),
                               (1002, "calibrated_v2.rou", 22235))]
        assert h._seed_health_ok(records)

    def test_a_cross_bound_real_name_still_fails_closed(self):
        records = [
            {"seed": s, "variant": f"{stem}_close_{h.KNOWN_CLOSURE}.rou.xml",
             "loaded": 5, "inserted": 5, "running_at_end": 0,
             "waiting_at_end": 0, "collisions": 0, "teleports": 0}
            for s, stem in ((1000, "calibrated_v2.rou"),      # wrong family
                            (1001, "calibrated_v1.rou"),
                            (1002, "calibrated_v2.rou"))]
        assert not h._seed_health_ok(records)


# ── v1 is spent and unrunnable; v2 is the sole executable identity (crit 5) ──
class TestSpentV1IsRefused:

    def test_v1_identity_is_retired(self, tmp_path):
        c = json.loads(CONTRACT.read_text())
        c["experiment_id"] = "persistent_sumo_v1"
        c["content_key"] = h.content_key(c)
        p = tmp_path / "c.json"; p.write_text(json.dumps(c))
        with pytest.raises(h.ContractRefused, match="SPENT"):
            h.load_contract(p)

    def test_the_canonical_contract_is_v2(self):
        assert h.CANONICAL_CONTRACT.name == "persistent_sumo_campaign_v2.json"
        assert h.CURRENT_EXPERIMENT_ID == "persistent_sumo_v2"

    def test_the_cli_refuses_the_spent_v1_file(self, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", [
            "x", "--campaign", "validation/persistent_sumo_campaign_v1.json",
            "--validate-contract-only"])
        assert h.main() == 2
        assert "canonical" in capsys.readouterr().err
