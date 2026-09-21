# Prompt till Claude: reparera steg 4 och mät riktig retention

Fortsätt på `claude/exciting-rubin-1e6k5m` från `90c0675`. Arbeta i den
befintliga isolerade worktree:n eller skapa en ny om den saknas. Huvudcheckouten
är smutsig och får inte ändras. Starta ingen SUMO-körning, månadssökning,
kataloggenerering, demand-byggnad eller uppvärmning. Ingen optimering ska
implementeras i denna omgång.

Planläget är: steg 0–3 är klara. Steg 4:s instrumentering och q50-replay finns,
men steg 4 är inte klart. Den största planerade posten, full trevariants-
retention, mättes inte och concurrent-rankingen innehåller ett verifierat fel.

## Del A — applicera reviewfixen

Applicera `validation/claude_step4_measurement_review_fix_20260913.patch` om
den finns. Om patchen inte finns i din miljö, implementera följande exakt:

1. Lägg till `wall_contribution_s` i `traffic_sim/ops/io_phases.py`.
2. En sekventiell fas utanför en concurrent-region bidrar med sin exklusiva
   tid. En concurrent-boundary bidrar med sin direkt uppmätta `inclusive_s`.
3. Alla descendants under en concurrent-boundary finns kvar med calls,
   inclusive/exclusive, bytes, parent, children sum och max, men bidrar med
   noll extra väggtid till den globala rankingen.
4. Stöd även ett fasnamn som förekommer både innanför och utanför concurrent-
   regioner; dess verkliga sekventiella bidrag får inte försvinna.
5. `profile_passage_replay._io_phase_walls` och profilsummeringen ska använda
   `wall_contribution_s` och utesluta rader med noll bidrag ur wall-rankingen.
6. Lägg till regressionstestet där tre parallella cirka 50 ms-barn tidigare gav
   cirka 0,220 s rankad tid för en region med cirka 0,055 s faktisk väggtid.
   Efter fixen ska bara concurrent-regionens uppmätta väggtid räknas.

Kör minst:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/gs-mpl \
python3 -m pytest -q \
  tests/test_io_phases.py \
  tests/test_passage_evidence_pruning.py \
  tests/test_step4_io_instrumentation.py \
  tests/test_trial_dynamic_passage.py \
  tests/test_profile_passage_replay.py \
  tests/test_dynamic_assignment.py \
  tests/test_automatic_passage.py \
  tests/test_passage_solver_checkpoint.py \
  tests/test_build_sumo_demand.py \
  tests/test_day_library.py \
  tests/test_monthly_demand.py \
  tests/test_independent_daily.py \
  tests/test_monthly_search.py \
  tests/test_demand_provenance.py
```

Codex referens efter reviewfixen är 574 passerade tester i denna filuppsättning,
plus en befintlig urllib3/LibreSSL-varning. Antal kan skilja om checkoutens
tester skiljer sig; inget relevant test får falla. Kör även `git diff --check`.

Committa och pusha reviewfixen separat först när testerna är gröna.

## Del B — komplettera den saknade `_tracked_main`-instrumenteringen

Rapporten påstod att ställena kring `build_sumo_demand.py::_tracked_main` var
instrumenterade, men `90c0675` ändrar inte den filen och den innehåller ingen
användning av `io_phases`. Korrigera detta med RED-tester först.

Mät som separata diagnostiska faser:

- själva `main()`-anropet,
- produktlistning och `run.add_output`-loopen,
- läsning/parsing av `demand_meta.json`,
- skapande och registrering av valideringsrapporten,
- `run.finish` för success respektive failure.

Observeraren ska fortfarande vara avstängd i produktion och återställas efter
undantag. Behåll `preserve_demand_on_failure`, run-status, outputmanifest,
latest-pekare och failure-semantik exakt. Testa med stubbar och temporära filer;
kör inte ett riktigt demand-bygge. Rapportera denna produktionsfas som
instrumenterad men fortfarande omätt.

Observera att en ändring av `build_sumo_demand.py` påverkar demand-källidentitet.
Dokumentera cacheinvalideringen ärligt. Försök inte kringgå den genom att ta bort
filen ur källinventariet.

## Del C — mät full retention utan ett nytt bygge

På den lokala maskinen finns en komplett rå trevariantsrot:

```text
runs/automatic-passage-97f1ab116a8e48d8a05621247431715a
```

Den har `q50`, `_v1`, `_v2`, 118 råa XML-filer och cirka 877,5 MB rå XML
(ungefär 1,0 GB totalt). Kontrollera dessa fakta igen innan mätning. Om roten
saknas i din miljö: bygg ingenting som ersättning. Leverera koden och det exakta
lokala kommandot och säg att retention fortfarande är omätt.

Skapa ett explicit profilerläge för `prune_evidence` som:

1. vägrar source=out eller överlappande sökvägar,
2. inventerar och hashar originalroten före mätning,
3. gör en vanlig ägd kopia per repeat; använd inga hårdlänkar,
4. mäter kopieringen separat och utesluter den ur retentionens väggtid,
5. kör `prune_evidence` på kopian under `PhaseCollector`,
6. verifierar varje `.gz` genom dekomprimerad digest mot motsvarande original,
7. verifierar att förväntade råa XML-filer, candidate-mappar och rollbackfiler
   behandlades enligt nuvarande kontrakt,
8. verifierar att originalrotens alla paths, storlekar och SHA-256 är identiska
   efter varje repeat,
9. tar bort endast profilerverktygets egna temporära kopia efter att dess
   rapport skrivits,
10. kör tre repeats i samma process och redovisar första respektive senare
    repeats separat.

För varje repeat måste rapporten visa:

- retentionens verkliga root wall,
- inventory, concurrent-region, cleanup och publicering,
- child CPU/thread sum och max som diagnostik,
- exakt en global wall contribution för den samtidiga regionen,
- bytes read, written, hashed och verified,
- antal råa och komprimerade filer före och efter,
- kompressionsgrad,
- residual samt omätta kategorier.

Lägg till en invariant i testerna: summan av rankade wall contributions får inte
överstiga den mätta retention-rootens inclusive wall mer än en liten timer-
tolerans. Andelarna för positiva rankingrader ska summera till cirka 100 %.

Uppdatera `validation/passage_step4_io_measurement_20260913.json` eller skapa en
version 2 med tydliga fält:

- `baseline_revision: 1f1f2c8...`,
- `instrumentation_revision: <reviewfixens exakta SHA>`,
- q50-replaymätningen separat från full retention,
- komplett trevariants-retention kall och varm,
- källdigests och source-tree unchanged,
- `release_evidence: false`.

Det gamla fältet `source_revision: 1f1f2c8...` är tvetydigt eftersom mätningen
kördes med instrumenterad kod. Ersätt det med de två fälten ovan.

## Beslut efter mätningen

Implementera inte processcachen för `source_trace_xml` i denna omgång. De tre
spårfilerna i q50-underlaget har olika digest och varje fil läses bara en gång i
ett normalt `_refine`-anrop. Profilens repeats läser samma evidens flera gånger,
men det bevisar inte en träff eller tidsvinst i ett dagsbygge.

Efter retentionprofilen ska du endast rangordna kandidater efter faktisk
produktionsform. Om retention åter står för den stora posten ska nästa förslag
rikta sig mot dess största exklusiva eller kritiska fas. Om full retention är
liten ska parsingförslaget antingen förbättra den första och enda parsningen
eller skjutas upp. Påstå ingen månadsvinst genom att multiplicera parallell
trådtid eller repeat-cachevinster.

Uppdatera de markerade current-blocken i `TASKS.md` och `AGENT_NOTES.md` samt
steg 4-avsnittet i `IMPROVEMENT_PLAN.md`. Markera steg 4 klart först när
concurrent-redovisningen är reparerad och full trevariants-retention har mätts.
Rapportera commits, exakta tester, alla mätbegränsningar och nästa enda
resultatneutrala optimeringshypotes. Pusha endast gröna commits till samma
Claude-gren.
