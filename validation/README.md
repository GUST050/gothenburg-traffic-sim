# validation/

Frozen evidence. Each artifact was true for the code and inputs it names, and
is **not** updated afterwards — that is the point of a seal. Nothing here is
regenerated to make a test pass; see `docs/OPEN_ISSUES_2026-08-06.md` §8 for
why the seal-drift failures are left alone.

## Compressed artifacts

Large **pure-archive** artifacts — ones no code or document reads by name — are
stored gzipped to keep the working tree small. Compression is lossless and
byte-exact, so the recorded hashes still verify:

```sh
gunzip -c <file>.json.gz | sha256sum      # matches the sha in members.json
gunzip -k <file>.json.gz                  # restore alongside the .gz
```

| artifact | stored | original |
| --- | --- | --- |
| `monthly_warm_state_v15_q10_forensics/raw_closure-8bcf7829ae545dffd8ce__q10__1000.json.gz` | 3.2 MB | 36.9 MB, sha256 `6a97fbf115952010ae929ee2b174e9fd2623bc2f3e441983898d19cf48ee3200` |

That sha256 is the value bound in the same directory's `members.json`, so the
seal is verifiable without any change to the seal itself.

**Only compress an artifact that nothing reads by name.** Artifacts referenced
from code, tests or documents stay plain — `annual_warm_plan_2027.json` is read
by `tools/plan_annual_warming.py`, `tools/resume_warming.sh` and
`tools/pilot_annual_warm_storage.py`, and its path and hash are recorded in
`annual_warm_readiness_v1.json`, so it is deliberately left uncompressed.

## What is here

`annual_warm_population_pilot_v1/` is a content-addressed store (its `store/blobs/`
are already gzipped by the populator). Everything else is a flat JSON record:
a preregistration, an outcome, a manifest, a golden or a gate result. The
`*_vN` families are versioned campaign seals — retiring a superseded version is
a decision about evidence, not tidying, and is tracked in `OPEN_ISSUES` §8.
