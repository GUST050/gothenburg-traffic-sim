# Prompt till Claude: slutför steg 3-reparationen och mät steg 4

Arbeta på grenen `claude/exciting-rubin-1e6k5m`, vars nuvarande relevanta
revision är `a719da4`. Använd en isolerad worktree om den vanliga checkouten är
smutsig. Rör inte användarens andra arbetskopia eller dess orelaterade filer.
Starta ingen SUMO-körning, månadssökning, kataloggenerering eller uppvärmning.

Planläget är detta:

- Steg 0 är klart: reproducerbar passage-replay och basinstrumentering.
- Steg 1 är klart: verifierat grundsystem återanvänds.
- Steg 2 är klart: innehållsbundet `StructureContext` och delade ruttfakta.
- Steg 3 är mätt och optimerat: varm `departure_bound_constraints` blev
  51,95 % snabbare och varm `fit_integer_flows` 24,56 % snabbare med identiska
  solver-requester och publicerade resultat.
- Steg 3 har en lokal reviewfix som måste in innan det får betraktas som helt
  levererat på grenen.
- Steg 4 är nästa steg och ska först vara enbart mätning.

## Del A — applicera och verifiera reviewfixen för steg 3

Om filen finns lokalt, applicera
`validation/claude_step3_optimization_review_fix_20260913.patch` mot
`a719da4`. Om filen inte finns i din miljö, implementera exakt följande själv:

1. I `departure_bound_constraints` får `option.edges` inte användas direkt som
   dict-nyckel när värdet kan vara en lista. Behåll tuple och tuple-subklasser
   oförändrade; konvertera endast icke-tupler med `tuple(option.edges)`. Använd
   det normaliserade värdet både i memo-nyckeln och `set(...)`.
2. Lägg till regressionstestet
   `TestDepartureBoundIncidenceReuse::test_list_backed_route_keeps_the_previous_public_behavior`.
   Det ska jämföra den optimerade funktionen med den frysta tidigare
   implementationen för en listbaserad `RouteDeparture`.
3. Uppdatera steg 3-evidensen med hela request-digesten:
   `d1cf64e0f6decd4fa2d822bae6ec61c4395b7871ea4ab6e2696dd7d26a38653a`.
4. Korrigera texten om kall solve: baslinjens median är 3,6545955 s och
   kandidatens 3,633094 s. Kandidaten är alltså 0,59 % snabbare. Beskriv det som
   praktiskt oförändrat eftersom MILP-tiden dominerar.

Kör därefter:

```bash
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/gs-mpl \
python3 -m pytest -q \
  tests/test_dynamic_assignment.py \
  tests/test_passage_solver_checkpoint.py \
  tests/test_profile_passage_replay.py \
  tests/test_automatic_passage.py \
  tests/test_build_sumo_demand.py \
  tests/test_build_candidates.py
```

Förväntad lokal referens efter fixen är 544 passerade tester. Testantal kan
skilja mellan checkouts, men inget relevant test får falla. Kontrollera även
`git diff --check`. Committa reviewfixen separat med ett conventional-commit-
meddelande och pusha den till samma Claude-gren först när kontrollerna är gröna.

## Del B — steg 4, endast mätinstrumentering

Läs avsnittet `Steg 4 — bevisfiler, parsing och serialization` i
`IMPROVEMENT_PLAN.md`. Mät den befintliga produktionsvägen innan du föreslår
eller skriver någon I/O-optimering.

Mät minst följande som separata, exklusiva faser:

1. inventering av filer och manifest,
2. läsning av okomprimerade källbytes,
3. gzip-komprimering,
4. skrivning till atomisk temporär målfil,
5. hashning av källan,
6. dekomprimering och hashning av det faktiskt skrivna målet,
7. atomisk publicering och städning,
8. JSON-läsning/parsing för report, metadata och agents,
9. XML-läsning/parsing för routes,
10. `materialize_selection`: transformering, skrivning och efterföljande
    artifact-hashning var för sig,
11. run-registry-kopiering och målverifiering i `traffic_sim/ops/runs.py`,
12. relevant arbete runt `build_sumo_demand.py::_tracked_main` utan att
    dubbelräkna barnfaser.

Krav på instrumenteringen:

- Den ska vara diagnostisk och avstängd i produktion.
- Använd samma säkra mönster som steg 3: en privat `ContextVar`-observer eller
  annan anropslokal mekanism som alltid återställs i `finally`.
- Sekventiella föräldrar får redovisa väggtid minus barn. Samtidiga barn ska
  redovisa `exclusive_s: null` samt både summa och max; summera dem inte som om
  de kördes sekventiellt.
- Ingen mätdata får ingå i demand-, passage-, solver- eller replayidentitet.
- Om instrumenteringen ändrar en fil som ingår i `replay_source_sha256()`,
  arbeta på en isolerad kopia av q50-evidensen och bind om dess replaykontrakt
  till den mätta revisionen. Ändra aldrig original-evidensen.
- Den skrivna målfilen måste fortfarande verifieras. Ta inte bort
  dekompressionskontroll, digestkontroll, atomisk tempfil, avbrottsstädning,
  manifest eller råevidens.
- Gzip nivå 3, fast mtime och parallell retention finns redan. Rapportera dem
  som befintligt beteende och föreslå dem inte igen.
- Undvik hårdlänkar till muterbara livefiler.
- Fixa även profilerverktygets verifierade CLI-problem med ett subprocess-test:
  `python3 tools/profile_passage_replay.py --help` ska fungera från repo-roten
  utan att användaren först sätter `PYTHONPATH=.`. Verktyget ligger utanför
  replay-sourceidentiteten.

Skriv RED-tester först för:

- fasredovisning utan dubbelräkning,
- observer-isolering mellan samtidiga anrop och återställning efter undantag,
- avbruten komprimering lämnar inget publicerat eller tillfälligt skräp,
- korrupt komprimerat mål vägras,
- saknad evidens vägras strukturerat,
- källartefakter är byteidentiska före och efter profilering,
- profileringsdata ändrar inte manifest, request, selection, routes eller agents,
- direkt CLI-start utan `PYTHONPATH`.

Kör sedan en avgränsad baseline-profil på den sparade q50-evidensen för
2027-06-25, tre repeats i samma process. Använd en ny outputkatalog. Detta är
diagnostik, inte release-evidens. Om din miljö saknar den lokala evidensen ska
du slutföra och testa instrumenteringen, skriva det exakta lokala kommandot och
tydligt säga att inga produktionssiffror mättes. Fabricera inga tider och kör
inte ett ersättningsbygge.

Leverera `validation/passage_step4_io_measurement_20260913.json` med:

- källrevision och fulla inputdigests,
- kall och varm tid per exklusiv fas,
- bytes lästa, skrivna, komprimerade och verifierade per fas,
- samtidig respektive sekventiell redovisningsgrund,
- residual och explicit lista över omätta kategorier,
- kontroll att källan och semantiska resultat är oförändrade,
- en rangordning av faser efter faktisk väggtidsandel,
- `release_evidence: false`.

Uppdatera endast de markerade current-blocken i `TASKS.md` och
`AGENT_NOTES.md` samt steg 4-avsnittet i `IMPROVEMENT_PLAN.md`. Markera inte
steg 4 som optimerat; skriv `measurement complete` först när produktionsprofilen
finns. Implementera ingen steg 4-optimering i denna körning. Avsluta med
ändrade filer, exakta testresultat, mätta tider eller miljöbegränsningen, och en
enda rekommenderad optimeringshypotes som följer av den största uppmätta fasen.
