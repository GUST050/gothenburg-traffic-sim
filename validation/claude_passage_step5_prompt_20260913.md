# Prompt till Claude: slutför worker-isoleringen och mät steg 5

Fortsätt på `claude/exciting-rubin-1e6k5m` från `fb089b9`. Arbeta i den
isolerade worktree:n och rör inte huvudcheckoutens orelaterade ändringar.
Steg 0-4 är klara i sak. Gör först reviewreparationen nedan och fortsätt sedan
med **steg 5 som mätning**, utan att implementera en optimering i samma omgång.

Starta ingen SUMO-körning, månadssökning, kataloggenerering, demand-byggnad
eller uppvärmning. Använd befintliga arkiv, kvalificerade manifest, sparade
cost-ledgers och testfixturer. Om en nödvändig produktionsartefakt saknas ska du
leverera instrumenteringen och det exakta lokala kommandot, inte skapa en dyr
ersättare eller påstå en produktionstid.

## Del A — applicera Codex reviewfix för worker-profileraren

Applicera `validation/claude_step4_worker_review_fix_20260913.patch` om den
finns. Om den saknas, implementera följande:

1. `profile_retention(max_workers=...)` får inte tillfälligt skriva till
   `automatic_passage.RETENTION_MAX_WORKERS`.
2. Skicka worker-taket anropslokalt genom `_retention_repeat` till en privat
   `_worker_cap`-parameter på `prune_evidence` och vidare till
   `_retention_worker_count`.
3. Produktionsanrop utan override ska fortsatt använda
   `RETENTION_MAX_WORKERS == 6`. Ett worker-tak under 1 ska avvisas.
4. Testa explicit cap, default cap, undantagsvägen och två samtidiga profiler
   som stannar vid samma barriär med cap 3 respektive 6. Båda ska se sin egen
   parameter samtidigt som den globala produktionskonstanten förblir 6.
5. Uppdatera A/B-evidensen ärligt: originalexperimentet använde en separat
   process per arm, så felet påverkade inte tiderna eller verdict. Lägg inte
   in nya tider och kör inte om gigabytemätningen.

Codex verifierade reviewfixen med 80 fokuserade och 610 beroendetester, utan
fel; den enda varningen var den befintliga urllib3/LibreSSL-varningen. Kör
`git diff --check`, committa och pusha denna reparation separat. Rapportera SHA.

`automatic_passage.py` ingår i `demand_source_paths`, så även denna lilla
reparation flyttar arkivens källidentitet. Dokumentera det; kringgå inte
invalideringen och starta ingen re-warm nu. Kodserien ska frysas innan den
senare, riktade uppvärmningen, så att samma datum inte värms efter varje steg.

## Del B — steg 5, mät produktionsformen före ändring

Läs steg 5 i `IMPROVEMENT_PLAN.md` och de aktuella blocken i `TASKS.md` och
`AGENT_NOTES.md`. Kartlägg den verkliga anropskedjan genom:

- `traffic_sim/simulation/monthly_demand.py`:
  `find_demand_archives`, `validate_demand_archive` och `prepare`,
- `demand/day_library.py:assemble_window`,
- `traffic_sim/simulation/cost_ordered_execution.py:build_cost_ledger`,
- `traffic_sim/simulation/deterministic_disruption.py`, inklusive den
  deterministic cost source som faktiskt används,
- `traffic_sim/simulation/disruption.py:ClosureRouteResolver`,
- relevanta arkiv- och ruttvägar i `traffic_sim/simulation/monthly_sumo.py`,
- befintliga `tools/profile_monthly_cost_ledger.py` och dess evidenskontrakt.

Skriv RED-tester före instrumenteringskoden. Instrumenteringen ska vara
diagnostisk, context-lokal och alltid återställd i `finally`. Den får inte
ändra cacheidentiteter, arkivinnehåll, scorer, writer-resultat eller
stoppbeslut.

### Mätetal som måste finnas

För en verklig resolver-setup och ett verkligt cost-ledger-anrop, redovisa:

1. Antal upptäckta arkiv och antal metadataindexläsningar.
2. Antal fulla `validate_demand_archive`-anrop per unik arkivsökväg och totalt.
3. Antal läsningar, parsningar, stat-kontroller och SHA-256-beräkningar per
   arkivfil, med bytes och exklusiv väggtid där det kan mätas utan
   dubbelräkning.
4. Kall och varm väg separat: första diskentré, återanvändning inom samma
   verifierade snapshot samt en ny entré efter att en fil ändrats.
5. `assemble_window`: dagar, route-rader, agents, lästa/skrivna bytes och
   exklusiv väggtid. Ändra inte implementationen i denna omgång.
6. Costing: antal parent candidates, daily units, cache hits/misses, unika
   ruttbytes/route identities, XML-parsningar, skapade
   `ClosureRouteResolver`-instanser, `resolve`-anrop och tid i faktisk
   resolver/ruttberäkning.
7. Slutresultatets digest: hela cost-ledgern, sorterad kostnadslista, vinnare,
   disqualifications, stop proof och provider/routing identity.
8. Residual och uttryckligen omätta kategorier. Summera aldrig överlappande
   barn som väggtid.

### Snapshot- och cachekrav

Inför ingen global `path -> valid`-cache. En post från disk ska fortsatt
fullvalideras. Om samma verifierade arkiv behöver användas flera gånger inom
en och samma operation får nästa steg senare bära en frusen intern descriptor
med parsad metadata, fulla content digests och en tydlig snapshot-livstid.
Instrumenteringen i denna omgång ska mäta om den möjligheten faktiskt finns.

`mtime` och storlek är inte ett innehållsbevis. Lägg till en test där innehåll
ändras med bevarad storlek och återställd mtime; en ny läsgräns måste upptäcka
ändringen eller avvisa snapshoten. Korrupt manifest, källbyte, ofullständig
variantuppsättning och fel demand-spec ska fortsätta falla stängt.

### Mätning och beslut

Preflighta först om något redan existerande kvalificerat demand-manifest och
arkiv faktiskt passerar `validate_demand_archive` mot den aktuella
källidentiteten. Äldre arkiv kan vara korrekt invaliderade av steg 1-4. Om inget
passerar får du använda hermetiska testfixturer för att verifiera mätkontraktet,
men du får inte kalla deras tider produktionstider eller re-binda gammal
evidens till nya source-hashar. Sätt då produktionsdelen till
`instrumented_unmeasured` och namnge `current-source qualified archive` som
saknad förutsättning.

Om en aktuell, kvalificerad artefakt finns, använd den utan att mutera den.
Bind före och efter:

- exakta Git-SHA:n och Python/SUMO-runtime,
- spec- och policy-digests,
- varje konsumerat arkivs path, storlek och SHA-256,
- den befintliga ledgerns och resultatets content keys.

Kör en kall och minst två varma upprepningar i samma process endast när det
motsvarar produktionsåteranvändning. Kör dessutom nya processer när en
processcache annars skulle smickra resultatet. Redovisa båda; kalla och varma
tider får inte blandas i en median.

Skapa
`validation/passage_step5_archive_cost_measurement_20260913.json` med råvärden,
fasräkning, bytes, exklusiva tider, identiteter, residual,
`release_evidence: false` och ett rangordnat resultat. Om riktig
produktionsmätning inte kan köras ska status vara `instrumented_unmeasured` och
fältet tydligt ange exakt saknad artefakt.

Föreslå högst en optimering efter mätningen, riktad mot den största
produktionsfasen. Implementera den inte ännu. En acceptabel hypotes ska ange:

- observerad onödig upprepning i den riktiga anropskedjan,
- en innehållsbunden och operationslokal identitet,
- hur senare filändringar upptäcks,
- förväntad övre tidsvinst från mätningen,
- RED/GREEN- och A/B-krav som bevarar exakt ledger, vinnare och stop proof.

Optimera inte `assemble_window` om dess uppmätta andel är liten. Skapa inte en
route-cache om mätningen visar att varje route redan parsas eller löses endast
en gång per relevant operation.

Kör minst de relevanta testerna för `monthly_demand`, `day_library`,
`independent_daily`, `monthly_search`, `cost_ordered_execution`,
`deterministic_disruption`, `monthly_sumo`, profileraren och provenance. Kör
även `git diff --check`. Committa och pusha endast mätinstrumentering, tester,
evidens och uppdaterade current-block. Rapportera exakta testresultat,
produktionstider respektive omätta delar och den enda rekommenderade hypotesen.
