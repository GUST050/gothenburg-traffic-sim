# Closure integrity, stages 3 and 4 — implementation record

**Date:** 2026-08-10 · **Status:** implemented and unit-tested; the two
measured gates are UNRUN, because they need SUMO plus the canonical demand
archive. Read with `docs/plans/CLOSURE_INTEGRITY_PLAN_2026-08-05.md`, which
this closes out.

---

## Where the plan stood

| stage | before | now |
| --- | --- | --- |
| 1 — measure the leak mechanism | done, premise REVISED (`d07e586`) | unchanged |
| 2 — rerouter reach sweep | done, no change adopted (`a32006d`) | unchanged |
| 3 — teleport policy for closure runs | open | **implemented; mechanism measured on a synthetic probe, gate on real demand unrun** |
| 4 — v10 selection precondition | open | **implemented; freeze unrun** |
| 5 — warming | superseded by `WARMING_PLAN_2026-08-05.md` | unchanged |
| unplanned finding (21/61) | open, "not re-measured" | **re-score tool built; unrun** |

Stage 1 refuted the plan's §1.1 premise that the leak and the teleport are one
event counted twice, and stage 2 refuted both premises behind widening
`REROUTER_RADIUS_M`. What survived stage 1 is the fact stage 3 rests on:

> teleports are a NECESSARY condition: throughput never occurs without them in
> any of the 75 schedules
> — `validation/closure_leak_mechanism_v1.json`

## Stage 3 — the teleport policy

`traffic_sim/simulation/closure_teleport.py`. A closure run now passes
`--time-to-teleport -1` to SUMO; nothing else does.

**Why `-1` and not a finite threshold.** The plan proposed defaulting to "a
value chosen from Stage 1's waiting-time distribution". Stage 1's own result
rules that out: any finite threshold still teleports, a teleport is the
necessary condition for the leak, and Stage 3's gate requires closed-edge
throughput to reach zero. Only disabling it can satisfy the gate the plan set.

**This is not option D.** The plan rejected "stop counting teleport-induced
entries as leaks" outright, and rightly. Nothing here loosens a measurement.
The stuck vehicle still exists; it stops being relocated through a closed road
and is counted as a trip that did not arrive — the plan's own requirement:

> A vehicle that cannot get through must show up as *not arriving*, never as
> *driving through*.

**The vacuousness guard.** Once teleporting is off, `teleport_total` is zero by
construction, so a zero stops being evidence. Every consumer therefore carries
`closure_teleport.policy_record()` beside the count:

- published scenario JSON gains `scenario.teleport_policy`, always next to
  `closure_integrity`, so a `verified_clean` can never be read without knowing
  how it was obtained;
- `suggest_closure_time.closure_feasibility` gains `teleport_policy` beside
  `hard_failures`;
- the record's `teleport_count_is_informative: false` states it in one field.

**What deliberately keeps SUMO's default.** The BASELINE arm of a paired study.
It has no closed edge to leak onto, and its teleports are the live congestion
signal `closure_feasibility` disqualifies a whole observation on. Disabling it
there would leave the pair with no integrity check on either side. Warm and
cold closure arms take the same constant, so the warm arm stays an optimisation
of an equivalent cold arm rather than a different simulation.

**A constant that had to be pulled apart.** `truncate_stranded_vehicles`
compared the remaining closure wait against a bare `300`, with a comment tying
it to SUMO's teleport default. It is not that number: it models a driver who
parks short of an eight-hour closure and walks rather than queueing it out. It
is now `closure_teleport.MAX_CLOSURE_WAIT_S`, and a test asserts it does not
track the teleport option.

**The mechanism, MEASURED against real SUMO** ·
`validation/closure_teleport_mechanism_probe_v1.json`. Everything else about
stage 3 is argv-level and fake-driven, which is the seam failure this project
already paid for once (LUNA-WARM-08 built a boundary connector, tested it, and
wired it to nothing a real campaign used). So the mechanism gets one test that
runs the simulator, on `tools/c1_temporary_closure_probe`'s eight-edge network
whose lower chain `no_in → no_closed → no_out` has no detour at all:

| arm | closed-edge throughput | teleports | unfinished |
| --- | --- | --- | --- |
| SUMO default | **1** | 1 | 0 |
| `--time-to-teleport -1` | **0** | 0 | **1** |

The default arm reproduces the documented leak exactly: the vehicle is
relocated along its own route through the closure, records an entry on the
closed edge, and reports as a **completed trip** — which is what makes the leak
invisible without this measurement. Under the policy the same closure records
zero entries and the vehicle reports as not arriving. That is the plan's
requirement, demonstrated rather than argued.

Two things this probe is not: it is eight edges and one vehicle, and it is not
the Stage 3 gate. It says nothing about how many vehicles a real closure
strands.

*A first cut of the probe measured nothing and looked like a refutation.* Its
closure ended mid-run, and `active_closure_throughput` counts only
fully-contained 15-minute buckets — deliberately, so a bucket straddling the
boundary cannot be blamed on the closure. The raw `entered` it was reading was
a legitimate post-reopening entry. The probe now closes the edge for the whole
run, and the test says so in a comment, because that mistake is easy to repeat.

**The gate, unrun.** `closure_teleport.evaluate_stage3_gate` implements the
plan's wording exactly — throughput measured and zero, no teleports, and the
unfinished/dropped population growing by no more than the demand-side
`vehicles_no_detour` budget. `None` throughput fails: "we never looked" is not
"we looked and it was zero". `tools/measure_closure_teleport_policy.py` runs one
closure twice on the same demand and seeds, differing only in the option, and
writes `validation/closure_teleport_policy_v1.json`.

```
python3 tools/measure_closure_teleport_policy.py \
    --edge 7532496160_7532496129_0 --begin 25200 --end 54000
```

The paired arm is not optional. A single run under the new policy reports zero
teleports and zero throughput and proves nothing, because both are zero by
construction; the arm that still teleports is what makes the zero mean
something.

## Stage 4 — the v10 selection precondition

`traffic_sim/simulation/closure_survivability.py` and
`tools/freeze_heldout_v10.py`.

**The separation the rule turns on.** `closure_disruption` reported one number,
`vehicles_no_detour`, for two different facts. Stage 4 needs only one of them,
so the report now splits — additively, leaving the total and every existing
consumer untouched:

- `vehicles_severed_destination` — the destination is unreachable by car once
  the edge is gone. A topology fact about the edge. **Gates.**
- `vehicles_denied_departure` — the route starts on the closed edge. Access the
  closure genuinely removes. Every street with departures has some, so gating on
  it would refuse to close any real street — the mistake C1 fixed on 2026-08-06.
  **Reported, never gates.**

**Two probes, because demand alone is not enough.** Per-vehicle severance is
measured from the edge immediately BEFORE the closure on that vehicle's own
route — where SUMO's rerouter re-plans from, and the correction the 2026-07-09
review forced on `truncate_stranded_vehicles`. Beside it, a pure-topology check
refuses an edge that is the only way into a successor: that is the
Skånegatan/Engelbrektsgatan shape in `CLAUDE.md`, where one node had exactly one
incoming connection in the whole network, and a demand-only check would miss it
on any quarter where nothing happened to drive there.

The probe calls `run_scenario.build_edge_graph` and `run_scenario.reachable`
rather than reimplementing them. A second reachability implementation would be
free to disagree with the simulation, which is the one thing this check may not
do.

**One thing changed, not two.** v10 inherits v9's canonical archive and full
identity, the 400 m band, `MIN_WINDOW_SUPPORT = 10`, the ranking signal, the
road-class guarantee, the gate thresholds and the indifference zone — asserted
by test, by identity, against the v9 module. `FORMULA_VERSION` moves to
`demand_exposure_v4_survivable` because ELIGIBILITY changed; the ranking did
not.

**The trap that voided two campaigns, avoided.** `v10` is in
`EXACT_DEMAND_BINDING_CAMPAIGNS` **now**, before the freeze. The freeze
fingerprints `run_monthly_proxy_validation.py`, so registering afterwards would
change the digest the frozen manifest recorded.

**The freeze, unrun.** It needs the canonical archive under `runs/` and
`sumo/net.net.xml`, neither of which is tracked. On a machine that has both:

```
python3 tools/freeze_heldout_v10.py --dry-run    # compose and report, writes nothing
python3 tools/freeze_heldout_v10.py              # publish the three artifacts
```

The refusal path is as important as the selection: fewer than
`MIN_SURVIVING_CASES = 4` survivors is a refusal, not a smaller campaign, so
`ranking_case_fraction >= 0.5` is achievable before a single SUMO run. The
refused candidates are recorded in the selection artifact — a rule that refused
nothing would be indistinguishable from no rule, and the reader could not tell
which it was.

## The unplanned finding

`tools/remeasure_closure_disqualification.py` closes the OPEN item in
`OPEN_ISSUES_2026-08-06.md` §5 — 21 of 61 disqualified v9 schedules carried no
teleports and no throughput, and C1's fix "has not been re-measured".

It re-scores a stored `outcomes.json`, partitioning each schedule's recorded
`hard_failures` into: access reasons C1 already reclassified; `teleports`,
impossible under Stage 3; `active_closure_edge_throughput`, kept in its own
bucket because Stage 1 proved a teleport NECESSARY for it rather than identical
to it; and everything neither change touches, which still disqualifies.

**It refuses to predict.** Disabling teleporting changes the simulation, so a
re-run produces different queues and arrivals. `projected_eligible` answers only
"is any recorded reason still a failure under today's rules", it is labelled a
projection everywhere it appears, and the tool emits no campaign gate verdict.

```
python3 tools/remeasure_closure_disqualification.py \
    --outcomes runs/closure-proxy-validation/43e040ca…/outcomes.json
```

## What this costs, stated plainly

**Every closure result changes.** The teleport policy is an input to the
simulation exactly as `REROUTER_RADIUS_M` is, so existing scenario outputs were
produced under different semantics and are not comparable to new ones. Stage 2
declined to widen the rerouter partly for this reason; Stage 3 accepts the cost
because it is the only lever the measurements left standing.

**The annual warming plan key moves.** `run_scenario.py`,
`suggest_closure_time.py` and `traffic_sim/simulation/monthly_sumo.py` are
plan-bound sources. Merging this while a population run is active discards the
units already built, not just the run — `WARMING_PLAN_2026-08-05.md` has the
constraint table. Land it between runs, or regenerate the plan afterwards.

**Two gates are unrun.** Nothing here claims the leak is fixed. It claims the
mechanism is removed by construction and the measurement that would prove it is
built and ready. Until
`validation/closure_teleport_policy_v1.json` exists, Stage 3 is implemented and
undecided.

## Checks

```
python3 -m pytest tests/test_closure_teleport.py \
    tests/test_closure_teleport_wiring.py \
    tests/test_closure_teleport_probe.py \
    tests/test_closure_survivability.py \
    tests/test_closure_disruption.py \
    tests/test_heldout_v10_freeze.py \
    tests/test_remeasure_closure_disqualification.py
```

108 passed. `test_closure_teleport_probe.py` starts real SUMO and skips when it
is absent, so the suite still runs on a machine without it.

The full suite was run twice — once on a clean worktree of the same base, once
on this branch — because this environment has no `sumo/` or `runs/` tree and so
fails a large block of tests for reasons that have nothing to do with the
change:

```
before   267 failed, 3818 passed, 20 skipped
after    267 failed, 3917 passed, 20 skipped
```

Failure sets compared element by element: identical, apart from
`test_serve.py::TestSuggestClosure::test_cancel_stops_suggestion_without_reporting_an_error`,
which failed on the clean tree and passed here — a cancellation race, not a
change in behaviour.

THREE existing pinned assertions were updated deliberately, each a canary whose
whole purpose is to require a deliberate edit rather than silently absorb drift:
the `EXACT_DEMAND_BINDING_CAMPAIGNS` set in `tests/test_monthly_proxy_runner.py`,
and the drifted-source sets in `tests/test_heldout_gate.py` and
`tests/test_heldout_v6_freeze.py`. The last two record that
`heldout_selection.py` has moved, which keeps the spent v6 campaign refused —
exactly the property they exist to hold.
