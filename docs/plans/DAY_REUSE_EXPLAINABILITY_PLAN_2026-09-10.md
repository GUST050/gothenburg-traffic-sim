# Archived draft: day-reuse explainability

> **Superseded 2026-09-10.** Do not implement from this draft. The reviewed,
> canonical implementation contract is now in `IMPROVEMENT_PLAN.md`, under
> “Item 1 implementation contract — explain and safely reduce day rebuilds”.

**Datum:** 2026-09-10
**Status:** ARCHIVED WORKING DRAFT — not a current plan.
**Prioritet:** Punkt 1 i `IMPROVEMENT_PLAN.md`, satt av användaren 2026-09-10.

> **AUKTORITET, tillagt 2026-09-10 efter genomförandet:** det bindande
> kontraktet är avsnittet "Item 1 implementation contract — explain and safely
> reduce day rebuilds" i `IMPROVEMENT_PLAN.md`. Det är skrivet efter detta
> dokument, är mer detaljerat och skiljer sig på två punkter som gäller:
> CLI:t tar ISO 8601-gränser och flaggan heter `--output` (inte epoch och
> `--out`), och q50-alias länkas till sin fullständiga post innan upprepningar
> räknas. **Etapp 1 är byggd och verifierad enligt kontraktet** —
> `tools/explain_day_reuse.py`, `tests/test_explain_day_reuse.py`, 24 tester
> gröna, artefakt `validation/day_reuse_explanation_v1.json`. Uppgiftsstegen
> nedan är därmed historik: de beskriver hur etappen planerades, inte hur den
> ska köras om. Mätningarna, de två hårda begränsningarna och
> praxisgenomgången gäller fortfarande.
**Spec/underlag:** `IMPROVEMENT_PLAN.md` (blocket "Current reassessment —
completed June search, 2026-09-10") och
`validation/codebase_simulation_improvement_research_20260910.md`.

**Mål:** Göra frågan "varför kalibrerades den här kalenderdagen igen?"
besvarbar med maskinläsbar evidens — först för körningar som redan finns på
disk, sedan i själva bygget — utan att ändra en enda identitet, ett enda
publicerat resultat eller invalidera det varma dagbiblioteket.

**Arkitektur:** Två skilda lager, i tvingande ordning. Lager 1 är en
*read-only förklarare* utanför identitetsinventariet som läser
`runs/demand-days/*/*/manifest.json` och rapporterar per datum vilka
identiteter som finns, vilka fält som skiljer dem åt och vilken klass av
orsak det är. Lager 2 är *beslutsloggning i bygget* (`DayLibrary.lookup`
returnerar en orsak i stället för bara `None`), vilket ändrar bytes i
identitetsinventariet och därför bara får landa i ett planerat
invalideringsfönster.

**Teknikstack:** Python 3, stdlib (`json`, `hashlib`, `pathlib`,
`dataclasses`), pytest. Inga nya beroenden.

---

## Globala begränsningar

Dessa gäller varje uppgift nedan. Värdena är mätta i denna kodbas 2026-09-10,
inte antagna.

1. **Identitetsinventariet får inte röras utan avsikt.**
   `traffic_sim/demand/source_identity.py::demand_source_paths` returnerar
   **40 filer**, och varje `DayIdentity.source_hashes` innehåller alla 40.
   Inventariet är *globbat* över `demand/*.py` och `traffic_sim/demand/*.py`
   plus en fast lista. `demand/day_library.py` ingår
   (`demand/day_library.py` → `b8e67b8b…`), liksom `build_sumo_demand.py`.
   **En redigering av någon av de 40 filerna gör varje lagrad dag
   omatchbar.** Mätt idag: 296 poster över 94 datum matchar exakt nuvarande
   källbytes; 1 051 poster är redan onåbara efter äldre kodändringar; hela
   `runs/demand-days` är 8,6 GB. Det som står på spel är alltså timmar av
   passagekalibrering, inte diskutrymme.
2. **Diagnostik får aldrig gå in i byggets kontrakt.**
   `build_sumo_demand.py:2153` bygger `build_fingerprint` av `contract =
   {k: v for k, v in meta.items() if k not in {"timings_s", "pfe_timing_s",
   "build_fingerprint"}}`. Tidsvärden är redan uteslutna, eftersom de varierar
   mellan två i övrigt identiska byggen. Varje nytt diagnostikfält **måste**
   läggas till i samma uteslutningsmängd, annars slutar två likvärdiga byggen
   vara jämförbara.
3. **Ny kod placeras utanför inventariet.** Tillåtna kataloger för ny kod i
   etapp 1: `tools/` (bara namngivna filer ingår i inventariet, ingen glob),
   `tests/`, `validation/`. Förbjudna: `demand/`, `traffic_sim/demand/`.
4. **Förklararen får inte skapa releasebevis.** Artefakten skrivs med
   `"release_evidence": false`, som `validation/anchored_shape_value_v1.json`.
5. **Inget raderas.** Förklararen läser; den flyttar, städar eller gallrar
   ingenting i `runs/demand-days`.

---

## Bakgrund: vad frågan faktiskt gäller

Juni-körningen `ui-monthly-g1f50b` registrerade **49 passagekalibreringar** för
vad backend kallar **31 kalenderdagsenheter**. `AGENT_NOTES.md` formulerar
uppgiften som "investigate identity/context duplication before date-only
reuse". Datumnyckel-cache är uttryckligen förbjuden i projektet, så frågan är
inte "kan vi cacha på datum" utan "vilken av de 40 identitetskomponenterna
skilde de dubblerade dagarna åt, och var någon av dem onödig?".

Idag finns inget maskinläsbart svar. `build_sumo_demand.py:1551` skriver
`day <datum>: library hit <nyckel>` till stdout vid träff, och vid miss skrivs
ingenting alls — bygget kalibrerar bara. `demand_meta.json` innehåller
`timings_s`, men ingen post om vilka dagar som återanvändes, vilka som inte
gjorde det, eller varför. `DayLibrary.get` (`demand/day_library.py:131`)
returnerar `None` för minst åtta olika tillstånd — saknad katalog, oläsbart
manifest, fel `schema_version`, fel `kind`, fel `key`, olik `identity`, saknad
artefakt, fel sha256, fel storlek — och kollapsar dem alla till samma `None`.

## Vad som redan är mätt (read-only, 2026-09-10)

Mätt direkt på `runs/demand-days/*/*/manifest.json`, filtrerat på
skrivtidsfönstret 18:29:15–19:54:32 för `ui-monthly-g1f50b`. Ingenting
ändrades.

| Mått | Värde |
|---|---|
| Poster skrivna under körningen | 98 |
| Distinkta kalenderdatum | 30 (2027-06-03 – 2027-07-02) |
| Trevariantsposter (`edge_shares`, `_q10`, `_q90`) | 49 |
| q50-delmängdsposter (`edge_shares` ensam) | 49 |
| Datum med två trevariantsposter | 19 |
| Datum med en trevariantspost | 11 |

19 × 2 + 11 × 1 = 49. **Hela gapet mellan 49 och 31 är alltså förklarat av att
19 datum kalibrerades två gånger**, och inte av någon okänd upprepning.

Orsaken till varje dubblering är entydig: i alla 19 fallen skiljer sig
`pool_composition`, och de tre följdfälten `inputs.candidate_pool`,
`inputs.candidate_metadata` och `inputs.catalog_keys` skiljer sig med den
(kandidatpoolen genereras per komposition). Inget datum dubblerades av något
annat skäl. Kompositioner i körningen: `('weekday','weekend')` 25 gånger,
`('weekday',)` 21, `('weekend',)` 3.

Detta är designat beteende, inte en bugg. `demand/intake.py:186` och
`demand/day_library.py:84` förklarar varför: PFE löser varje kvart över hela
formpoolen, så en tisdag kalibrerad i ett veckodagsfönster har en mindre
variabelmängd än samma tisdag i ett fönster som också innehåller en lördag.
Det är två olika — båda korrekta — resultat, och de får inte dela post.

Två sekundära fynd, båda relevanta för prioriteringen:

* **Ingen icke-determinism upptäcktes.** Antalet (datum, komposition)-par med
  mer än en `candidate_pool`-hash är **0**. Cachen besegras alltså inte av
  ostabil hashning i denna körning.
* **Kodändringar är den andra missklassen, och den är redan observerad.**
  2027-07-02 hade en trevariantspost med komposition `('weekday',)` skriven
  2026-09-05 17:45 och fick ändå en ny post 2026-09-10 18:48 med samma
  komposition. Skillnaden: 16 källfiler i `source_hashes`, bland dem
  `build_candidates`, `build_sumo_demand`, `demand/structure.py` och
  `demand/day_library.py` självt. Det är precis den invalidering global
  begränsning 1 beskriver, och den har alltså redan inträffat i praktiken.

Detta mättes med ett engångsskript i sessionens scratchpad. Etapp 1 finns för
att göra samma svar reproducerbart, granskningsbart och körbart på framtida
körningar; ingen artefakt skrivs till `validation/` innan verktyget finns.

## Extern praxis: hur andra system besvarar exakt den här frågan

Fyra mogna system har samma problem — en innehållsadresserad cache som ibland
missar och en användare som frågar varför — och alla fyra löser det på samma
sätt: **cachen får berätta vilken nyckelkomponent som skilde sig, inte bara att
den missade.**

* **Bazel** skriver en separat förklaringsfil på begäran:
  `--explain=<fil>` plus `--verbose_explanations` loggar för varje utdata varför
  den byggdes om, och med utförligt läge även det ändrade kommandot. Notera att
  förklaringen är opt-in och kostar prestanda — samma val gäller här.
* **Nextflow** har `-dump-hashes` (och `-dump-hashes json`), som skriver ut
  *komponenterna* i varje uppgifts hash så att två körningars loggar kan
  diffas rad för rad. Dokumentationen listar dessutom sex namngivna orsaker
  till utebliven återanvändning. Det är närmast vår situation: en vetenskaplig
  pipeline där `-resume` ska återanvända dyra steg.
* **Gradle/Develocity** jämför två byggens *task inputs* och pekar ut vilken
  indataegenskap som ändrade cache-nyckeln; `-Dorg.gradle.caching.debug=true`
  loggar nyckelns beståndsdelar med fingeravtryck per indata. Metoden är
  hierarkisk: hitta först den första uppgift som kördes, jämför sedan
  implementationen, sedan indatahasharna.
* **ccache** räknar i stället *namngivna* utfall — `direct_cache_hit`,
  `preprocessed_cache_hit`, `direct_cache_miss`, `cache_miss` med flera — så
  att en försämring syns som en förskjutning mellan räknare i `--show-stats`.

Namngivningen följer OpenTelemetrys konvention för semantiska attribut:
punktseparerad namnrymd, `snake_case` inom ett segment, singular
(`day_library.miss.reason`, inte `dayLibraryMissReasons`).

Slutsatsen för oss: **en enum av orsakskoder plus en fältdiff mot syskonposter
för samma datum**. Enumen ger ccache-stilens räknare; fältdiffen ger Gradles
och Nextflows "vilken komponent ändrades".

---

## Etapp 1 — offline-förklararen (noll identitetsrisk)

Ny fil `tools/explain_day_reuse.py` plus `tests/test_explain_day_reuse.py`.
Ingen fil i identitetsinventariet ändras, så det varma biblioteket överlever.

### Uppgift 1: identitetsdiffen

**Filer:**
- Skapa: `tools/explain_day_reuse.py`
- Test: `tests/test_explain_day_reuse.py`

**Gränssnitt:**
- Producerar: `diff_identity(a: Mapping, b: Mapping) -> tuple[str, ...]` —
  sorterade punktseparerade fältvägar som skiljer två `identity`-dictar åt.
  `inputs` och `source_hashes` expanderas ett steg (`inputs.candidate_pool`),
  övriga fält rapporteras som toppnivånamn (`pool_composition`).

- [ ] **Steg 1: skriv det fallerande testet**

```python
from tools.explain_day_reuse import diff_identity


def test_reports_the_expanded_input_field_not_just_inputs():
    a = {"date": "2027-06-03", "pool_composition": ["weekday"],
         "inputs": {"candidate_pool": "aaa", "variants": ["edge_shares"]},
         "source_hashes": {"pfe": "1"}}
    b = {"date": "2027-06-03", "pool_composition": ["weekday", "weekend"],
         "inputs": {"candidate_pool": "bbb", "variants": ["edge_shares"]},
         "source_hashes": {"pfe": "1"}}

    assert diff_identity(a, b) == ("inputs.candidate_pool", "pool_composition")


def test_identical_identities_have_no_difference():
    a = {"date": "2027-06-03", "inputs": {"x": 1}, "source_hashes": {}}
    assert diff_identity(a, dict(a)) == ()
```

- [ ] **Steg 2: kör testet och se att det misslyckas**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: `ModuleNotFoundError: No module named 'tools.explain_day_reuse'`.

- [ ] **Steg 3: skriv minimal implementation**

```python
"""Read-only explanation of why a calendar day was calibrated more than once.

Nothing here is imported by a build. The module deliberately lives outside
``demand_source_paths``: adding a file to that inventory changes every stored
day's identity, and this tool exists precisely to avoid paying that price to
answer a diagnostic question.
"""
from __future__ import annotations

from typing import Any, Mapping

EXPANDED_FIELDS = ("inputs", "source_hashes")


def diff_identity(a: Mapping[str, Any], b: Mapping[str, Any]) -> tuple[str, ...]:
    """Dotted paths on which two stored identities differ."""
    paths: set[str] = set()
    for field in set(a) | set(b):
        left, right = a.get(field), b.get(field)
        if left == right:
            continue
        if field in EXPANDED_FIELDS and isinstance(left, Mapping) \
                and isinstance(right, Mapping):
            for key in set(left) | set(right):
                if left.get(key) != right.get(key):
                    paths.add(f"{field}.{key}")
            continue
        paths.add(field)
    return tuple(sorted(paths))
```

- [ ] **Steg 4: kör testet och se att det passerar**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: 2 passed.

- [ ] **Steg 5: commit**

```bash
git add tools/explain_day_reuse.py tests/test_explain_day_reuse.py
git commit -m "feat: report which identity field separates two stored days"
```

### Uppgift 2: läs biblioteket och klassificera orsaken

**Filer:**
- Ändra: `tools/explain_day_reuse.py`
- Test: `tests/test_explain_day_reuse.py`

**Gränssnitt:**
- Konsumerar: `diff_identity` från uppgift 1.
- Producerar:
  - `@dataclass(frozen=True) Entry` med fälten `date: str`, `key: str`,
    `written_at: float`, `identity: dict`.
  - `load_entries(root: Path, *, since: float | None = None,
    until: float | None = None) -> list[Entry]`.
  - `classify(paths: tuple[str, ...]) -> str`, som returnerar exakt en av
    `"variant_subset"`, `"pool_composition"`, `"source_change"`,
    `"candidate_drift"`, `"other"`.

Klassificeringsregeln är ordnad, och ordningen är själva poängen: en
skillnad i `source_hashes` betyder att koden ändrades och att posten aldrig
kunde ha återanvänts, oavsett vad som mer skiljer sig. En skillnad i
`inputs.variants` betyder att det är q50-delmängden, som lagras avsiktligt.
Först när ingen av dessa gäller får en skillnad räknas som möjligen
undvikbar dubblering.

- [ ] **Steg 1: skriv det fallerande testet**

```python
from tools.explain_day_reuse import classify


def test_a_source_change_outranks_everything_else():
    paths = ("inputs.candidate_pool", "pool_composition", "source_hashes.pfe")
    assert classify(paths) == "source_change"


def test_a_variant_subset_is_not_counted_as_avoidable_duplication():
    assert classify(("inputs.constraints", "inputs.variants")) == "variant_subset"


def test_composition_is_the_avoidable_candidate():
    paths = ("inputs.candidate_metadata", "inputs.candidate_pool",
             "inputs.catalog_keys", "pool_composition")
    assert classify(paths) == "pool_composition"


def test_candidate_pool_alone_is_drift_not_composition():
    assert classify(("inputs.candidate_pool",)) == "candidate_drift"
```

- [ ] **Steg 2: kör testet och se att det misslyckas**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: `ImportError: cannot import name 'classify'`.

- [ ] **Steg 3: skriv minimal implementation**

```python
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Entry:
    date: str
    key: str
    written_at: float
    identity: dict


def load_entries(root: Path, *, since: float | None = None,
                 until: float | None = None) -> list[Entry]:
    """Every readable manifest under ``root``, optionally by write time."""
    entries: list[Entry] = []
    for date_dir in sorted(p for p in Path(root).iterdir() if p.is_dir()):
        for key_dir in sorted(p for p in date_dir.iterdir() if p.is_dir()):
            manifest = key_dir / "manifest.json"
            if not manifest.is_file():
                continue
            written_at = manifest.stat().st_mtime
            if since is not None and written_at < since:
                continue
            if until is not None and written_at > until:
                continue
            try:
                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            identity = payload.get("identity")
            if not isinstance(identity, dict):
                continue
            entries.append(Entry(date_dir.name, key_dir.name, written_at,
                                 identity))
    return entries


def classify(paths: tuple[str, ...]) -> str:
    """One cause per pair of same-date entries, most decisive first."""
    if any(p.startswith("source_hashes") for p in paths):
        return "source_change"
    if "inputs.variants" in paths:
        return "variant_subset"
    if "pool_composition" in paths:
        return "pool_composition"
    if any(p.startswith("inputs.candidate") for p in paths):
        return "candidate_drift"
    return "other"
```

- [ ] **Steg 4: kör testet och se att det passerar**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: 6 passed.

- [ ] **Steg 5: commit**

```bash
git add tools/explain_day_reuse.py tests/test_explain_day_reuse.py
git commit -m "feat: classify why a calendar date holds several stored days"
```

### Uppgift 3: rapporten och CLI:t

**Filer:**
- Ändra: `tools/explain_day_reuse.py`
- Test: `tests/test_explain_day_reuse.py`

**Gränssnitt:**
- Konsumerar: `Entry`, `load_entries`, `diff_identity`, `classify`.
- Producerar: `explain(entries: list[Entry]) -> dict` med nycklarna
  `calendar_dates`, `full_calibrations`, `subset_entries`,
  `duplicate_calibrations`, `causes` (räknare per klass) och `per_date`.
  En "full calibration" är en post vars `identity["inputs"]["variants"]`
  har mer än ett element; en delmängdspost har exakt ett.

- [ ] **Steg 1: skriv det fallerande testet**

```python
from tools.explain_day_reuse import Entry, explain


def _entry(date, key, variants, composition, pool="aaa", sources=None):
    return Entry(date, key, 0.0, {
        "date": date,
        "pool_composition": list(composition),
        "inputs": {"variants": list(variants), "candidate_pool": pool,
                   "constraints": pool},
        "source_hashes": dict(sources or {"pfe": "1"}),
    })


THREE = ("edge_shares", "edge_shares_q10", "edge_shares_q90")


def test_counts_the_june_shape_without_blaming_the_subset_store():
    entries = [
        _entry("2027-06-03", "k1", THREE, ("weekday",), pool="aaa"),
        _entry("2027-06-03", "k2", THREE, ("weekday", "weekend"), pool="bbb"),
        _entry("2027-06-03", "k3", ("edge_shares",), ("weekday",), pool="aaa"),
        _entry("2027-06-04", "k4", THREE, ("weekday",), pool="aaa"),
    ]

    report = explain(entries)

    assert report["calendar_dates"] == 2
    assert report["full_calibrations"] == 3
    assert report["subset_entries"] == 1
    assert report["duplicate_calibrations"] == 1
    assert report["causes"] == {"pool_composition": 1}


def test_a_date_calibrated_once_produces_no_cause():
    entries = [_entry("2027-06-04", "k4", THREE, ("weekday",))]
    report = explain(entries)
    assert report["duplicate_calibrations"] == 0
    assert report["causes"] == {}
```

- [ ] **Steg 2: kör testet och se att det misslyckas**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: `ImportError: cannot import name 'explain'`.

- [ ] **Steg 3: skriv minimal implementation**

```python
import argparse
import time
from collections import Counter, defaultdict

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("runs") / "demand-days"
DEFAULT_OUT = Path("validation") / "day_reuse_explanation_v1.json"


def _is_full(entry: Entry) -> bool:
    return len(entry.identity.get("inputs", {}).get("variants", [])) > 1


def explain(entries: list[Entry]) -> dict:
    """Per-date and aggregate account of repeated day calibrations."""
    by_date: dict[str, list[Entry]] = defaultdict(list)
    for entry in entries:
        by_date[entry.date].append(entry)

    causes: Counter[str] = Counter()
    per_date: list[dict] = []
    full_total = subset_total = duplicates = 0

    for date in sorted(by_date):
        full = [e for e in by_date[date] if _is_full(e)]
        subset = [e for e in by_date[date] if not _is_full(e)]
        full_total += len(full)
        subset_total += len(subset)
        duplicates += max(len(full) - 1, 0)
        date_causes: list[dict] = []
        for other in full[1:]:
            paths = diff_identity(full[0].identity, other.identity)
            cause = classify(paths)
            causes[cause] += 1
            date_causes.append({"keys": [full[0].key, other.key],
                                "cause": cause, "fields": list(paths)})
        per_date.append({"date": date, "full_calibrations": len(full),
                         "subset_entries": len(subset),
                         "differences": date_causes})

    return {
        "schema_version": SCHEMA_VERSION,
        "release_evidence": False,
        "calendar_dates": len(by_date),
        "full_calibrations": full_total,
        "subset_entries": subset_total,
        "duplicate_calibrations": duplicates,
        "causes": dict(causes),
        "per_date": per_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--since", type=float, default=None,
                        help="unix time; only manifests written at or after")
    parser.add_argument("--until", type=float, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    entries = load_entries(args.root, since=args.since, until=args.until)
    report = explain(entries)
    report["measured_at"] = time.time()
    report["root"] = str(args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"{report['full_calibrations']} calibration(s) for "
          f"{report['calendar_dates']} calendar date(s); "
          f"{report['duplicate_calibrations']} repeat(s): {report['causes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Steg 4: kör testet och se att det passerar**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: 8 passed.

- [ ] **Steg 5: reproducera juni-mätningen**

Kör:

```bash
python3 tools/explain_day_reuse.py --since 1789057755 --until 1789062872
```

Förväntat, och detta är uppgiftens acceptanskriterium: `49 calibration(s)
for 30 calendar date(s); 19 repeat(s): {'pool_composition': 19}`.
Stämmer inte siffrorna är antingen klassificeringen eller tidsfönstret fel —
rätta det innan artefakten sparas. (Gränserna är
`2026-09-10 18:29:15` och `19:54:32` lokal tid; räkna om dem på maskinen med
`python3 -c "import datetime;print(datetime.datetime(2026,9,10,18,29,15).timestamp())"`.)

- [ ] **Steg 6: commit**

```bash
git add tools/explain_day_reuse.py tests/test_explain_day_reuse.py \
        validation/day_reuse_explanation_v1.json
git commit -m "feat: explain repeated calendar-day calibrations from manifests"
```

### Uppgift 4: hur mycket av biblioteket som fortfarande går att återanvända

**Filer:**
- Ändra: `tools/explain_day_reuse.py`
- Test: `tests/test_explain_day_reuse.py`

**Gränssnitt:**
- Producerar: `reusability(entries: list[Entry], current: Mapping[str, str])
  -> dict` med `{"live": int, "stale": int, "live_dates": int}`, där `current`
  är `{label: sha256}` för nuvarande träd.

Detta är siffran som gör global begränsning 1 konkret före varje redigering av
inventariet: den säger exakt hur många lagrade dagar en ändring skulle döda.

- [ ] **Steg 1: skriv det fallerande testet**

```python
from tools.explain_day_reuse import Entry, reusability


def test_counts_entries_that_current_sources_can_still_match():
    live = Entry("2027-06-03", "k1", 0.0,
                 {"inputs": {"variants": ["a", "b"]}, "source_hashes": {"pfe": "1"}})
    stale = Entry("2027-06-04", "k2", 0.0,
                  {"inputs": {"variants": ["a", "b"]}, "source_hashes": {"pfe": "2"}})

    assert reusability([live, stale], {"pfe": "1"}) == {
        "live": 1, "stale": 1, "live_dates": 1}
```

- [ ] **Steg 2: kör testet och se att det misslyckas**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: `ImportError: cannot import name 'reusability'`.

- [ ] **Steg 3: skriv minimal implementation**

```python
def reusability(entries: list[Entry], current: Mapping[str, str]) -> dict:
    """How many stored days the CURRENT source bytes can still match."""
    live = [e for e in entries
            if e.identity.get("source_hashes") == dict(current)]
    return {"live": len(live), "stale": len(entries) - len(live),
            "live_dates": len({e.date for e in live})}
```

Anropas från `main()` med det verkliga trädet:

```python
    from traffic_sim.demand.source_identity import demand_source_fingerprints
    current = {label: record.get("sha256") for label, record
               in demand_source_fingerprints(Path.cwd()).items()}
    report["reusability"] = reusability(entries, current)
```

- [ ] **Steg 4: kör testet och se att det passerar**

Kör: `python3 -m pytest tests/test_explain_day_reuse.py -x -q`
Förväntat: 9 passed.

- [ ] **Steg 5: verifiera mot verkligheten**

Kör: `python3 tools/explain_day_reuse.py`
Förväntat: `reusability` i artefakten rapporterar `live` och `live_dates` som
matchar dagens mätning (296 poster över 94 datum, om inventariet inte ändrats
sedan dess). Ändrats det, är det nya talet det sanna — men då ska
`IMPROVEMENT_PLAN.md` uppdateras, inte testet.

- [ ] **Steg 6: commit**

```bash
git add tools/explain_day_reuse.py tests/test_explain_day_reuse.py
git commit -m "feat: report how many stored days current sources can match"
```

---

## Etapp 2 — orsakslogg i bygget (kräver invalideringsfönster)

**Får inte påbörjas** som en fristående ändring. Den ändrar
`demand/day_library.py` och `build_sumo_demand.py`, båda i
identitetsinventariet, och kostar därför de då levande posterna. Den ska
buntas ihop med annan identitetspåverkande kod och landa omedelbart före en
planerad omvärmning (`warm_demand_horizon.py`).

Innehåll när fönstret öppnas:

1. `demand/day_library.py`: `@dataclass(frozen=True) Lookup` med
   `manifest: dict | None` och `reason: str` ur en namngiven enum
   (`hit`, `absent_no_manifest`, `manifest_unreadable`,
   `schema_version_mismatch`, `kind_mismatch`, `key_mismatch`,
   `identity_mismatch`, `artifact_missing`, `artifact_digest_mismatch`,
   `artifact_size_mismatch`). `DayLibrary.lookup(identity) -> Lookup` bär
   logiken; `DayLibrary.get` blir `return self.lookup(identity).manifest` och
   är därmed bytemässigt oförändrad för varje befintlig anropare. Ett test ska
   pinna att `get` fortfarande returnerar `None` respektive manifestet i
   samtliga tio tillstånd.
2. Vid `identity_mismatch`: slå upp syskonnycklar för samma datum och
   returnera `diff_identity`-vägarna, så byggets logg säger *vilket* fält som
   sköt dagen till en ny identitet — Gradles och Nextflows metod, men i
   realtid.
3. `build_sumo_demand.py`: samla besluten i `meta["day_library_diagnostics"]`
   (per dag: `date`, `key`, `outcome`, `reason`, `fields`, `elapsed_s`) och
   **lägg till nyckeln i uteslutningsmängden vid rad 2154** tillsammans med
   `timings_s` och `pfe_timing_s`. Ett test ska visa att `build_id` för två
   byggen med olika diagnostik är identiskt.
4. Ersätt `print(f"  day {identity.date}: library hit …")` med en rad som även
   skrivs vid miss, med orsakskoden — en miss är i dag helt tyst.

## Etapp 3 — sammanställning på jobbnivå

När etapp 2 finns bär varje `demand_meta.json` sitt eget beslut. Månadssökningen
ska då summera dem till körningens egen progressartefakt, så frågan "31 enheter,
49 kalibreringar" besvaras av körningen själv i stället för av forensik i
efterhand. Ingen ny identitet, inget nytt kontraktsfält — bara en läsning av
befintliga `demand_meta.json` under sökningens arbetskatalog.

## Vad som INTE ska göras

* **Ingen datumnyckel-cache.** Förbudet står i `IMPROVEMENT_PLAN.md` och gäller.
  Ett datum är inte en identitet.
* **Ta inte bort `pool_composition` ur identiteten** för att få bort de 19
  dubbleringarna. Fältet finns för att PFE:s variabelmängd verkligen skiljer sig,
  och `demand/intake.py:200` beskriver villkoren. En eventuell framtida ändring
  måste visa byte-likhet mellan en dag kalibrerad under två kompositioner —
  vilket dagens mätning tvärtom antyder att den inte har.
* **Gör inte inventariet selektivt** för att slippa invalidering. Kommentaren i
  `build_sumo_demand.py:170` beskriver den bugg en handhållen allow-list redan
  har orsakat en gång.
* **Skriv inte `validation/day_reuse_explanation_v1.json` för hand.** Artefakten
  ska komma ur verktyget, annars är den evidens utan proveniens.

## Acceptanskriterier för etapp 1

1. `python3 -m pytest tests/test_explain_day_reuse.py -q` passerar.
2. `python3 tools/explain_day_reuse.py --since … --until …` reproducerar
   49 kalibreringar / 30 datum / 19 dubbleringar / `pool_composition` 19.
3. `git status --short` visar inga ändringar i `demand/`, `traffic_sim/demand/`
   eller `build_sumo_demand.py`.
4. `reusability` i artefakten är oförändrad före och efter etappen — beviset på
   att det varma biblioteket överlevde.
5. Artefakten bär `"release_evidence": false`.

## Källor

- Bazel, *Commands and Options* — `--explain`, `--verbose_explanations`:
  https://bazel.build/docs/user-manual
- Nextflow, *Caching and resuming* — `-resume`, `-dump-hashes`,
  `-dump-hashes json` och de dokumenterade invalideringsorsakerna:
  https://docs.seqera.io/nextflow/cache-and-resume
- Gradle, *Debugging and diagnosing build cache misses* —
  `-Dorg.gradle.caching.debug`:
  https://docs.gradle.org/current/userguide/build_cache_debugging.html
- Develocity, *Diagnosing build cache misses with task inputs comparison*:
  https://docs.gradle.com/enterprise/tutorials/task-inputs-comparison/
- ccache manual — statistikräknare per utfall:
  https://ccache.dev/manual/latest.html
- OpenTelemetry, *Semantic Conventions* — namngivning av attribut och metrik:
  https://opentelemetry.io/docs/specs/semconv/
