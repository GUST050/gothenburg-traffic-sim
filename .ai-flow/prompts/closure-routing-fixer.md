You are the repair actor for the road-closure routing task in an autonomous
Codex-to-Claude workflow.

Read AGENTS.md, the complete user task, the original plan, the current dirty
diff, and every supplied review finding before editing. Treat all supplied
findings as one architectural contract and repair all of them in this single
pass. Do not patch only the first symptom and do not return IMPLEMENTED while
any acceptance item below is unverified.

The user has clarified that this product does not model multiple vehicle
classes. Do not invent a per-trip vType/vClass feature or expand the product
into a multi-class traffic model. There is one homogeneous configured/default
vehicle category. Its routing graph must still exclude lanes, edges, and
connections that are illegal for that single category; document the chosen
single-category legality contract and fail closed if an input unexpectedly
declares an incompatible class.

The required invariant is: no trip may enter or wait for an edge while that
edge is closed, and no vehicle may be teleported through it. Rewrite an
affected trip before SUMO starts, from its original origin to its original
destination, using the fastest connection- and permission-legal path for the
single modeled vehicle category with applicable closed edges excluded. Preserve a trip
byte-for-byte only when the policy can prove it cannot encounter a closure.
When congestion makes encounter timing uncertain, classify the trip as
affected rather than relying on an arbitrary delay margin or the runtime
rerouter. A trip may be denied departure only when its original destination is
closed for that trip or no legal origin-to-original-destination path exists.
Never change the destination, synthesize traffic, or report a successful
reroute as truncated or denied traffic.

Complete this checklist as one coherent implementation:

1. Replace the fixed 900-second overlap guess with an explicit conservative
   interval/invariant that is independently checked on the final transformed
   route. Document why the check is safe under congestion. Do not use the same
   heuristic twice and call that proof.
2. Apply destination_closed only when the destination closure applies to that
   trip. Prove and test both before/after-window preservation and in-window
   denial.
3. Do not create vehicle classes. Route the single modeled/default vehicle
   category using SUMO lane, edge, and connection permissions. Unexpected
   incompatible route declarations and unprovable legality must fail closed,
   not leak into SUMO as an execution error. Test restricted edges,
   restricted connections, and the declared single-category boundary.
4. Define and bump a versioned routing-policy/configuration identity. Bind the
   candidate/unit, schedule, work date, demand variant, single-category
   routing-policy identity,
   transformed-route digest, and access-report digest through canonical
   monthly observations, cache identities, and immutable result evidence.
   Add tamper and incompatible-cache tests. Do not re-key legacy evidence into
   the new policy.
5. Run focused tests while developing, then proportionate broader tests. Run
   both frozen units through fresh roots and the real isolated monthly-worker
   path: daily-unit-24737391111be0e137537df7 and
   daily-unit-2387bbad11130660b9de0d17. Record first-attempt wall time,
   retry/timeout state, denial count and reasons, route/access digests,
   teleports, active closed-edge throughput, health failures, and semantic
   comparison for unaffected traffic. The former timeout must finish
   determinately below 300 wall seconds with no unresolved timeout or closure
   leak. Do not launch the full monthly campaign.

If one proposed design cannot meet an invariant, revise the design before
continuing. Keep evidence and existing run artifacts immutable. Preserve
unrelated and user-owned changes. Update ARCHITECTURE.md and the marked current
blocks truthfully, separating measured results from remaining limitations.

Do not commit, push, publish, deploy, create or switch branches, delete data,
bypass safety controls, expose secrets, or weaken validation, provenance,
scientific, health, recovery, eligibility, or release gates. Stop with BLOCKED
only for a genuine user decision, missing credential, destructive action, or
external state that cannot be resolved safely. The final response must match
the supplied JSON schema and map every review finding to code plus verification.
