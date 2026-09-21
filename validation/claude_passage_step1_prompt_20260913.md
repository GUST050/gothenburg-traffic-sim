# Prompt till Claude: reparera profileraren och genomför passageplanens steg 1

Arbeta vidare på `claude/exciting-rubin-1e6k5m` från `6986299`. Läs först
`AGENTS.md`, aktuella block i `TASKS.md` och `AGENT_NOTES.md`, samt
`IMPROVEMENT_PLAN.md:86-140`. Bevara andra ändringar. Detta är ett
resultatneutralt prestandaarbete; ändra inga sensorer, frön, mätband,
kvartgränser, rutter, OD/ändamål, lösartoleranser eller valideringsgrindar.

## Ny lokal evidens som du ska utgå från

Codex körde den mätning som din molncontainer inte kunde köra, lokalt i
`/Users/gt/Documents/gs-project`:

- datum: `2027-06-25`, 96 kvart, forecast, helgdag/weekend-pool;
- exakt en ny kalibrering i ett isolerat dagbibliotek;
- kataloggrind: explicit `catalog`, `candidate_source_implicit=false`,
  `catalog_fallback=null`, `catalog_cache_events={"weekend":"hit"}`;
- byggväggtid: 26,405 s;
- `pfe_variants_and_rounding`: 19,939 s;
- `dynamic_passage`: 13,155 s;
- passagefasernas produktionstid: prepare_inputs 0,198 s, learning_sumo
  2,306 s, prepare_system 3,830 s, solve_integer_flows 1,437 s,
  stage_and_structure 1,366 s, validation_sumo 3,663 s och
  report_serialization 0,173 s;
- solver-requesten var en verifierad cacheträff: 0,0109 s i HiGHS-checkpointen;
- alla tre replays reproducerade exakt samma selection-, route- och agent-hashar,
  9 675 fordon och samma strukturflagga.

Evidensen finns lokalt i
`validation/passage_profile_local_20260913.json`. Behandla siffrorna som
diagnostik, inte release-evidens: den byggövergripande valideringen var `WARN`
och sample-matrisen är ännu inte fullständig.

## H1: fixa repetitionsmätningen först

I `6986299` använder varje repeat `out/repeat-N/solver-cache`. Därför blev alla
tre solver-körningar kalla (~4,23 s), samtidigt som rapporten kallade repeat 2–3
återanvänd process. Det ger fel prioritering mot produktionen, som hade cacheträff.

Gör detta testdrivet:

1. Låt `profile()` skapa en enda ny cache under profilens egen utmapp och dela
   den mellan repeats. Den får aldrig ligga i eller runt källevidensen.
2. Repeat 1 ska börja med tom cache; repeat 2–3 ska få cacheträff för identisk
   request. Läs `solver/state.json` och rapportera ett booleskt
   `solver_cache_hit` per repeat samt request key.
3. Lägg `solver_cache_basis =
   "empty_output_local_cache_then_shared_across_repeats"` i summeringen och
   beskriv både process- och solver-cache i `process_state`.
4. Vägra en saknad/icke-boolesk cache-status. Bevara källträdet byteidentiskt.
5. Regressionstestet ska kräva `[false, true, true]`. Codex referenspatch finns
   i `validation/profile_passage_solver_cache_fix_20260913.patch`. Granska den;
   kopiera den inte blint.

Efter fixen mättes samma riktiga underlag till 3,513 s kall solve och
0,754/0,762 s vid cacheträff. Alla tre valen var fortfarande byteidentiska.

## Steg 1: återanvänd det redan verifierade grundsystemet

Implementera därefter exakt `IMPROVEMENT_PLAN.md` steg 1. Den lokala mätningen
bekräftar tre kostnader:

- `trial.load_source` bygger ett grundsystem för entered-rekonstruktionen;
- `_refine` bygger samma grundsystem igen direkt efter `load_source`;
- `expand_departure_support` bygger ett dummy-system enbart för att upprepa
  options-valideringen.

Den expanderade matrisen är ett annat system och måste fortfarande byggas.

Krav:

1. Inför en intern, typad representation för verifierade options, groups,
   metadata och grundsystem. Den ska skapas en gång per source-load.
2. Behåll det publika `load_source(source) -> (options, groups, metadata)` så
   befintliga anropare fungerar. Använd en intern loader för produktionsvägen.
3. `_refine` ska använda exakt det grundsystem som också verifierade råa
   `entered`-celler. Ingen hashkontroll, trace-kontroll eller projektion får
   tas bort.
4. Skapa en intern expansion från verifierade options så att dummy-systemet
   inte byggs igen. Det publika `expand_departure_support` ska fortfarande göra
   full validering för godtyckliga anropare. Undvik en publik `skip_validation`
   flagga som kan användas fel.
5. Dela inga skrivbara NumPy/SciPy-arrayer mellan oberoende jobb. Återanvändning
   gäller inom samma `_refine`-anrop och samma verifierade objekt.
6. Den expanderade kandidatmatrisen, retained PFE bounds, kolumnordning,
   randmatris och solver-request måste förbli identiska.

Skriv RED-tester först för:

- att produktionsvägen bygger grundsystemet en gång och inte kör
  dummy-valideringen efter verifierad load;
- att publika API:er fortfarande avvisar tom input, dubbla option-ID,
  olika scenarioordning/omfång, NaN/negativa tider, ogiltig kapacitet och
  passager utanför horisonten på samma sätt;
- upprepade sensorpassager och startedge-semantik;
- byteidentiska `selection.json`, route XML och agents JSON;
- identisk CSR shape/indptr/indices/data för både grund- och randmatris samt
  identisk solver-request key.

Kör minst:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/gs-mpl python3 -m pytest -q \
  tests/test_profile_passage_replay.py \
  tests/test_trial_dynamic_passage.py \
  tests/test_dynamic_assignment.py \
  tests/test_automatic_passage.py \
  tests/test_passage_solver_checkpoint.py
git diff --check
```

Rapportera ändrade filer, exakt testutfall och vad som inte gick att mäta i
molncontainern. Påstå ingen produktionsvinst där. Committa och pusha endast de
granskade H1- och steg 1-ändringarna till samma Claude-gren och ange SHA; starta
ingen SUMO-körning, månadskörning, kataloggenerering eller generell värmning.
Codex kör sedan lokal A/B/B/A på det frysta underlaget och godkänner ändringen
endast om alla semantiska hash- och matrisgrindar är identiska.
