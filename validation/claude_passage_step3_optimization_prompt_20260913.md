# Prompt till Claude: reparera steg 3 och optimera den mätta avgångsgränsfasen

Fortsätt på `claude/exciting-rubin-1e6k5m` från commit `450de9b`. Läs först
`AGENTS.md`, current-blocken och steg 3 i `IMPROVEMENT_PLAN.md`. Arbeta endast
med passage-solverns nu uppmätta kostnad. Starta ingen SUMO-, katalog-, månads-
eller uppvärmningskörning.

## 1. Två obligatoriska reviewfixar

Om `validation/claude_step3_review_fix_20260913.patch` finns, applicera och
granska den. Om den saknas ska du implementera följande själv:

1. Ersätt den processglobala `_SOLVER_PHASE_OBSERVER` med en
   `contextvars.ContextVar`. `_observe_solver_phases()` ska använda token/reset
   i `finally`, och `_solver_phase()` ska läsa med `.get()`. Skriv ett
   deterministiskt tvåtrådstest som visar att överlappande observatörer inte
   blandar mätningar och att huvudkontexten återgår till `None`.
2. Lägg `traffic_sim/demand/passage_solver.py` i
   `automatic_passage.replay_source_sha256()`. Filen styr requestnyckel,
   cachevalidering och om MILP körs, så ett replaykontrakt utan den är
   ofullständigt. Lås det med test.

Kör därefter minst:

```sh
python3 -m pytest -q \
  tests/test_dynamic_assignment.py \
  tests/test_passage_solver_checkpoint.py \
  tests/test_profile_passage_replay.py \
  tests/test_automatic_passage.py
git diff --check
```

Lokalt gav denna del 182 passerade fokustester efter review. En bredare vald svit gav 578
passerade och samma fristående, befintliga fel i
`TestAssemble.test_passage_section_is_judged_on_accuracy_not_exactness`.

## 2. Utgå från den lokala steg 3-mätningen

Läs `validation/passage_step3_solver_measurement_20260913.json` om filen finns.
På samma sparade q50-underlag för 2027-06-25 mättes:

- kall `fit_integer_flows`: 1,971947 s;
- kall `milp_solve`: 1,576139 s, 79,93 %;
- varm cache: 0,3868705 s median och `milp_executed: false`;
- varm `departure_bound_constraints`: 0,206246 s, 53,31 %;
- varm `column_equivalence_reduction`: 0,126166 s, 32,61 %;
- 99,69 % av den kalla fit-tiden är redovisad;
- `request.npz` är byteidentisk med steg 2 och alla outputhashar är identiska.

Det betyder att en varm solvercache redan hoppar över MILP. Börja därför med
avgångsgränsmatrisen; bygg inte en ny solver eller en andra persistent
resultatcache.

## 3. Avgränsat optimeringsexperiment

Optimera endast `departure_bound_constraints()` genom att återanvända
incidensen för upprepade `(departure quarter, full edge tuple)` inom ett anrop.
Den nuvarande semantiken ska vara exakt:

- varje ruttkant räknas högst en gång per option genom `set(option.edges)`;
- endast deklarerade `(quarter, edge)`-bounds skapar en rad;
- option-/kolumnordning, CSR-radordning, data, lower och upper ska vara exakt
  desamma;
- negativa och bortre kvartal ska behålla nuvarande behandling;
- inga globala eller sökvägsbaserade cacher;
- cachen ska dö med funktionsanropet eller med ett uttryckligt, oföränderligt
  solverförberedelseobjekt;
- inga sensor-, PFE-, grupp-, population-, solver- eller verifieringsgrindar får
  ändras.

En säker första kandidat är en lokal memo från `(quarter, option.edges)` till
den exakta tuple av bound-rader som dagens kod skulle producera. Bygg raden med
samma `set(option.edges)`-iteration första gången nyckeln ses och återanvänd den
för övriga alternativ med samma rutt och avgångskvart. Ändra inte algoritmen
utöver detta innan kandidaten är mätt.

Skriv RED-tester först. Jämför en fryst referensimplementation av nuvarande
funktion mot kandidaten och kräv exakt likhet för:

- `.data`, `.indices`, `.indptr`, `.shape`;
- lower och upper;
- upprepade kanter och sensoråterbesök;
- flera options med samma rutt/kvart;
- samma rutt i olika kvart;
- mätta och omätta kanter blandade;
- tomma bounds och bounds med nollor;
- randomiserade små system med fast seed.

Kontrollera dessutom att full `request.npz`, request key inom samma revision,
valda counts och selection/routes/agents förblir identiska.

## 4. Mätbeslut

Kör baseline och kandidat i ordningen A/B/B/A, tre repeats per profil och nya
tomma profilcacher. Båda armarna ska jämföras kall mot kall och varm mot varm;
återanvänd inte en cache key mellan revisioner eftersom källhashen avsiktligt
ändras.

Godkänn kandidaten endast om:

1. samtliga exakthetskontroller passerar;
2. `selection_reproduced.state` är `identical` överallt;
3. varm median för `departure_bound_constraints` sjunker minst 15 %;
4. varm total `fit_integer_flows` inte går bakåt utanför normal A/B-variation.

Om kandidaten inte klarar gränsen ska du återställa produktionsändringen och
rapportera experimentet som förkastat. Börja inte automatiskt optimera
kolumnekvivalensen; lämna i så fall en mätt rekommendation för den som separat
nästa experiment.

Uppdatera de befintliga plan- och current-blocken med mätdata och begränsningar.
Commit och push endast reviewfixarna och den kandidat som faktiskt klarar
acceptanskriterierna. Rapportera SHA, filer, tester, exakta A/B-tider och om
optimeringen accepterades eller förkastades.
