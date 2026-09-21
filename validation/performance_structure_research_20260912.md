# Performance structure investigation, 2026-09-12

## Scope and safety boundary

This investigation is read-only with respect to simulation state. It did not
build demand, warm dates, qualify or adopt a catalog, or launch a monthly
search. The recommendations preserve the current sensor, provenance, archive,
and SUMO validation gates. Timings below are measurements from the current
workspace unless explicitly marked as estimates.

## Main finding: archive discovery reads the wrong file

`traffic_sim/simulation/monthly_demand.py::_archives_for_build_key` reads every
`runs/demand-*/demand_meta.json` merely to extract `demand_build_key`. The same
identity can be derived from the existing, canonical `demand_build_spec.json`.
Full archive validation already reopens the metadata and checks the spec,
runtime/source hashes, artifact hashes, and build key for the selected
candidate. Using the small spec as an index therefore avoids work without
weakening validation.

Current workspace measurements:

| Measurement | Result |
| --- | ---: |
| Demand archives with `demand_meta.json` | 110 |
| Aggregate metadata size | 9.02 GiB |
| Median metadata size | 95.58 MiB |
| Metadata files larger than 50 MiB | 92 |
| Ten-file sample read and JSON parse | 1.037 GiB in 3.005 s |
| Estimated complete cold index scan | about 26 s |
| One 106.65 MiB metadata key extraction | 297.532 ms median |
| Same key from 270-byte build spec | 0.013 ms median |

The complete-scan number is a linear estimate from the bounded sample and must
be replaced by a paired before/after benchmark when the implementation lands.
Its size closely matches the previously unexplained 25-28 seconds in demand
preparation.

### Proposed implementation

1. Index archives from `demand_build_spec.json` and derive the key through
   `DemandBuildSpec.from_dict(...).build_key` rather than trusting an unparsed
   string.
2. Bind the in-process index-cache signature to each spec file's path,
   `mtime_ns`, and size. Directory metadata alone is not a sufficient cache
   invalidation contract for an in-place file edit.
3. Treat a missing, unreadable, or invalid spec as an unindexable candidate.
   Do not infer identity from sibling archives.
4. Keep `validate_demand_archive` as the mandatory second stage. It remains the
   authority for metadata, provenance, duration, and artifact integrity.
5. Keep historical archive compatibility: no schema rewrite or bulk migration
   is required because these archives already contain the build spec.

### Acceptance and performance gates

- Identical archive selection for valid fixtures before and after the change.
- A nonmatching archive's `demand_meta.json` is never opened during indexing.
- The selected archive still fails closed on metadata/spec, source, runtime,
  duration, or artifact mismatch.
- Mutating a spec invalidates the index cache in the same process.
- Corrupt and incomplete archives are skipped or rejected with the current
  diagnostics; they never become reusable.
- Benchmark a cold index over a copied or synthetic 110-archive fixture, then
  confirm one read-only resolution against the live tree. The expected gain is
  removal of most of the estimated 26-second scan, not a promised exact value.

### Local implementation result

The spec-based index was implemented locally after the external worktree had
finished. A cold-cache check over the 110 real archive specifications indexed
all 110 archives and 102 build keys in 4.459 ms median across seven runs; the
first run took 28.450 ms. The focused seven-file suite passed 405 tests, and
`git diff --check` plus Python compilation passed. No SUMO or demand build was
started. The earlier approximately 26-second old-path figure remains a bounded
sample estimate rather than a paired full-tree measurement.

## Second finding: metadata is dominated by duplicated, pretty-printed data

A sampled three-day `demand_meta.json` is 106.65 MiB. Serializing the same
Python object as compact JSON produces 19.28 MiB, a 5.53x reduction. The
largest fields in the compact representation are:

| Field | Compact size |
| --- | ---: |
| `build_fingerprint.contract` | 9.64 MiB |
| `pfe_fit_variants` | 7.15 MiB |
| `passage_calibration` | 2.40 MiB |

The fingerprint contract duplicates large top-level structures. Removing only
that duplicate from the compact representation would leave about 9.65 MiB,
but changing fingerprint representation is a schema and compatibility change.

### Safe first step

Write future `demand_meta.json` files with compact separators while preserving
the exact object and build-ID calculation. In the sample this reduced output
from 106.65 to 19.28 MiB. Write time changed only from 1.758 to 1.619 seconds,
and parse time improved modestly because JSON decoding still creates the same
large object. The main gain is disk usage, archive copying, hashing, and lower
I/O pressure.

Do not rewrite existing archives automatically. A later schema version may
replace the stored duplicate contract with `contract_sha256`, provided the
validator recomputes the canonical contract digest and old records remain
readable. That larger change needs separate migration and provenance tests.

## Dynamic passage: measure subphases before changing execution

Across 52 `automatic-passage*/timings.json` files, total passage time has a
55.79-second median and 69.32-second p90. Variant medians are 17.44 seconds for
q50, 16.01 for q10, and 16.47 for q90. Retention is already small at a
2.20-second median after the existing compression improvement.

The current timing records only a whole variant. Add timings for:

- source and route parsing;
- learning SUMO batch;
- incidence and solver-matrix construction;
- continuous solve and integer repair;
- staged artifact writing and hashing;
- validation SUMO batch;
- report serialization.

The result should be an attributable profile for every slow day and an
aggregate p50/p90 comparison. Instrumentation should use `perf_counter`, have
negligible work inside timed regions, and avoid embedding wall-clock timestamps
in release evidence.

Variant-level parallelism is not the first recommendation. The PFE path already
uses a flat bounded process pool, and a dormant variant-parallel candidate has
not proved full-load memory, cancellation, or determinism. Running three outer
variants concurrently would also nest SUMO and solver pressure. Promote it only
if a resource-bounded paired benchmark preserves every output digest and shows
a gain on representative days.

## Exact solver reuse: valuable but identity must remain strict

Among 175 recorded solver states, 167 cache misses had a 5.247-second median;
eight exact cache hits had a 0.010-second median. Preserve the exact model and
input identity because broadening this cache key could reuse a solution for a
different sensor or route system.

After subphase instrumentation, consider caching immutable matrix structure
whose identity includes sensor ordering, route ordering, purpose ordering,
scenario-difference equations, bounds, and policy version. Vectors that depend
on the quarter, population, or variant must remain fresh. This should be
promoted only with byte-identical model inputs and calibrated outputs on misses
and hits.

## Day-library storage pressure

`runs/demand-days` contains 1,347 entries and uses about 8.6 GiB. The three
uncompressed fit JSON families consume about 2.34 GiB. A representative
1.4-MiB fit file compressed to about 0.16 MiB with gzip level 3 in 0.007
seconds.

A backward-compatible reader could accept `fit*.json.gz` first and fall back to
the current plain JSON. New entries could then store compressed fits. This is
primarily a storage and I/O improvement; it is lower priority than archive
indexing. Existing entries must not be mass-migrated without an explicit,
separately verified operation.

## SUMO process changes are experiments, not the first fix

SUMO's official documentation says libsumo removes TraCI protocol and socket
overhead, but the passage code currently launches command-line SUMO processes
rather than driving a high-volume TraCI loop. A libsumo migration would add
architectural and reproducibility risk before addressing the measured archive
scan.

SUMO `loadState` can avoid network reload, but the documentation recommends
using the same input files and records limitations around random state and
loaded vehicles. Passage variants change route inputs, so equality must be
proved before reuse. Route XML validation may also be benchmarked because SUMO
documents validation cost, but disabling it is acceptable only if malformed
generated routes still fail in a prior mandatory validation gate and SUMO
outputs remain identical.

Primary references:

- <https://sumo.dlr.de/docs/Libsumo.html>
- <https://sumo.dlr.de/docs/TraCI.html>
- <https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html>
- <https://sumo.dlr.de/docs/XMLValidation.html>
- <https://sumo.dlr.de/docs/Simulation/Basic_Definition.html>

## Recommended order

1. Replace metadata-based archive indexing with spec-based prefiltering and
   retain full validation for the selected candidate.
2. Add subphase passage timings and capture p50/p90 on frozen existing inputs.
3. Compact future demand metadata without changing its object or fingerprint.
4. Use the new timings to decide whether matrix-structure reuse or a strictly
   bounded execution change is justified.
5. Compress new day-library fit reports if storage pressure remains material.
6. Test SUMO process, state-loading, or XML-validation changes only after the
   measured Python and archive costs have been removed.

This order attacks measured waste first and does not trade simulation quality,
sensor matching, provenance, or validation strength for speed.
