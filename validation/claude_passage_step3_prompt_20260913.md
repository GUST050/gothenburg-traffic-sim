# Prompt till Claude: reparera steg 2 och mät steg 3

Arbeta vidare på `claude/exciting-rubin-1e6k5m`, vars senaste granskade
prestandacommit är `b375c3c`. Börja med att läsa `AGENTS.md`, de markerade
current-blocken i `TASKS.md` och `AGENT_NOTES.md` samt passageavsnittet i
`IMPROVEMENT_PLAN.md`. Skriv inte om användarens övriga ändringar.

## Del 1: korrigera steg 2 före fortsatt arbete

Codex lokala A/B godkänner idén men hittade fem kontraktsfel. Om filen
`validation/claude_step2_cache_review_fix_20260913.patch` finns i din miljö,
applicera och granska den. Om den saknas ska du själv implementera exakt dessa
korrigeringar:

1. `tests/test_structure_route_facts.py` måste kunna samlas in på Python 3.9.
   Lägg till postponed annotations eller undvik `str | None` i testfilen.
2. `StructureContext` får aldrig para ihop nya geometrihashar med gamla arrayer.
   Läs bytes, beräkna SHA-256 och bygg geometriarrayer från samma bytes. En
   omskrivning med samma storlek och mtime måste invalidiera kontexten.
3. Poolrapportens cache får inte nycklas enbart på `Path`. Bind den till
   innehållet i ruttfilen och alla sidecars/targets som rapporten faktiskt läser.
   En innehållsändring på samma sökväg ska ge en ny rapport. Låt den befintliga
   rapportfunktionen avgöra beteendet för en saknad fil; gör ingen tidig retur
   som bryter instrumenterade tester.
4. `automatic_passage.replay_source_sha256()` ska även binda
   `demand/structure.py`, eftersom strukturflaggorna kan godkänna eller avvisa
   resultatet.
5. Profilern ska skapa en `StructureContext` per replay och skicka samma privata
   `_context` till både source- och candidate-rapporten, precis som `_refine`.
   Rapportfältet ska säga `operation_scoped_content_bound`, inte
   `not_implemented`.

Behåll flyttalsordning, fordonsordning, alla valideringsgrindar och publik API.
Lägg regressionstester för varje punkt. Kör minst:

```sh
python3 -m pytest -q \
  tests/test_structure_route_facts.py \
  tests/test_profile_passage_replay.py \
  tests/test_automatic_passage.py \
  tests/test_build_sumo_demand.py \
  tests/test_build_candidates.py
git diff --check
```

Körningen lokalt gav 507 passerade tester i den valda breda sviten. Ett separat
befintligt fel kvarstod i
`tests/test_validation_report.py::TestAssemble::test_passage_section_is_judged_on_accuracy_not_exactness`;
blanda inte in det i denna prestandafix utan visa att felmängden är oförändrad.

## Lokalt bevis som redan finns

Läs `validation/passage_step2_cache_ab_20260913.json`. Motviktad A/B/B/A på
samma sparade q50-evidens gav:

- varm replaymedian: 6,6376895 s -> 5,956609 s, 10,26 % snabbare;
- varm strukturmedian: 1,3972755 s -> 0,749738 s, 46,34 % snabbare;
- kall replaymedian: 9,943896 s -> 9,233191 s, 7,15 % snabbare;
- alla 11 solver-request-arrayer var exakt lika;
- selection-, route- och agenthashar var identiska i alla replays.

En slutlig replay med granskningsfixarna och korrekt delad kontext gav varm
replaymedian 5,8138625 s och varm strukturmedian 0,651292 s. Detta godkänner
steg 2 efter korrigeringarna. Det är diagnostisk replay, inte ett helt
produktionsdagsbygge och inte releasebevis.

## Del 2: fortsätt med steg 3 som mätning, inte optimering

När del 1 är grön, instrumentera steg 3 utan att ändra lösarmatematik,
solverinställningar, kolumnordning, reduceringsordning, cacheidentitet eller
checkpointformat. Starta ingen SUMO-, katalog-, månads- eller uppvärmningskörning.

Mät följande delar av `fit_integer_flows` separat, med nästlade exklusiva tider
som inte dubbelräknas:

1. validering och konstruktion av conservation/hard/rhs;
2. scenario-differensmatrisen (`solve_hard`);
3. `departure_bound_constraints`;
4. `departure_group_bound_constraints`;
5. sammanslagning av bound-matriser och `LinearConstraint`/`Bounds`;
6. kolumnekvivalens och representantreduktion;
7. checkpoint/cache: request-serialisering, nyckel, lookup/decode och skrivning;
8. själva `scipy.optimize.milp`-anropet när det verkligen körs;
9. återexpansion och full efterverifiering.

Instrumenteringen ska vara diagnostisk och opt-in från
`tools/profile_passage_replay.py`. Produktionsanrop utan diagnostik ska skapa
samma objekt och bytes som tidigare. Vid cacheträff ska rapporten uttryckligen
visa att `milp` inte kördes; tiden får inte felaktigt bokföras som lösartid.

Skriv RED-tester före implementation. Testerna ska minst bevisa:

- summan av exklusiva barn inte överstiger förälderns väggtid annat än en liten
  klocktolerans;
- samma request ger samma `request.npz`, request key och valda resultat med och
  utan mätning;
- cachemiss och cacheträff redovisas olika;
- exception/time-out/infeasible återställer instrumenteringen och lämnar ingen
  global hook kvar;
- ingen ny mätdata går in i demand-, passage- eller solverfingeravtryck.

Kör tre replays på samma frysta q50-underlag om underlaget finns i miljön. Om
det saknas, leverera bara kapabiliteten och säg uttryckligen att inga
produktionssiffror har mätts. Rangordna därefter faserna efter varm median.
Föreslå ingen steg 3-optimering förrän mätningen visar vilken del som dominerar.

Uppdatera current-blocken och det befintliga passageavsnittet i
`IMPROVEMENT_PLAN.md` med exakta testresultat och begränsningar. Commit och push
endast ändringarna på din Claude-gren och rapportera SHA, ändrade filer,
testresultat och kvarvarande risker.
