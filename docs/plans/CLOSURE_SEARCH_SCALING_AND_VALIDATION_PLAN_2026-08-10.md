# Implementationsplan: snabbare och starkare optimering av vägavstängningar

**Datum:** 2026-08-10  
**Status:** Genomförandeplan; ingen release- eller global-bäst-grind öppnas av
detta dokument.  
**Omfattning:** Den rullande flermånaderssökningen med
`independent_daily_reset_v1`, `exact_equal_daily_v1`, upp till 90 arbetsdagar
och `closure_cost_v1`.  
**Utanför omfattningen:** Årlig demand-warming, ändring av fryst policy v1,
publicering, lane-only-avstängningar och produktivitetsmodell för byggarbete.

## 1. Målet

Programmet ska kunna söka över flera månader och långa perioder utan att en
giltig sökning stoppas enbart för att den innehåller fler än 10 000 unika
dagsenheter. Samma valda start- och sluttid ska fortfarande användas varje
arbetsdag. Resultatet ska förbli exakt inom den deklarerade
independent-day-modellen och alla befintliga hälso-, integritets-, proveniens-
och releasegrindar ska behållas.

Målet delas i två spår:

1. **Beräkningsspåret:** minska minne, processstarter och antalet SUMO-körningar
   utan att ändra vilket resultat dagens uttömmande algoritm skulle ge.
2. **Evidensspåret:** göra trafik- och avstängningsmodellen mer representativ
   för köer, omledning, kapacitet och variation mellan dagar.

## 2. Nuläge och uppmätt flaskhals

Den nuvarande sökningen gör följande:

1. `closure_calendar.py` materialiserar alla giltiga föräldrascheman.
2. `independent_daily.py` delar schemana i innehållsadresserade dagsenheter och
   sparar deras föräldramedlemskap.
3. `run_monthly_closure_search.py` stoppar över 100 000 föräldrascheman eller
   10 000 unika dagsenheter.
4. Varje cache-miss startar via `IsolatedDailySumoRunner` en ny Python-process
   som i sin tur kör dagsenhetens SUMO-arbete.
5. `ArchivedDemandSumoRunner._closure_disruption()` beräknar redan den
   deterministiska `closure_cost_v1`, men först inne i kandidatens vanliga
   exekveringsväg.
6. Alla föräldrar pilotkörs innan `pilot_selection.py` kostnadssorterar de
   hälso-godkända kandidaterna.

Uppmätt på den nuvarande arbetsstationen:

| Sökfall | Föräldrascheman | Unika dagsenheter | Generering + uppdelning | Toppminne | Nuläge |
|---|---:|---:|---:|---:|---|
| 2027-01-01–2027-06-30, 720 h, max 90 dagar, vardagar, 06–18 | 2 186 | 5 676 | 21,48 s | 90 MiB | Körbar under gränsen |
| Samma intervall, 360 h | 11 813 | 23 349 | 26,55 s | Ej separat fryst | Avvisas av 10 000-gränsen |

Det andra fallet är giltigt enligt kalenderkontraktet. Avvisningen är tydlig
och fail-closed, men visar att gränsen är en implementeringsbegränsning och
inte en egenskap hos problemet.

## 3. Forskningsbeslut som planen bygger på

### 3.1 Behåll parallellism mellan simuleringar

SUMO:s kärnsimulering kör på en kärna. Officiell dokumentation rekommenderar
därför flera oberoende simuleringar framför att förvänta sig meningsfull fart
från SUMO:s allmänna `--threads`-flagga. TraCI kan styra flera simuleringar,
men varje instans behöver fortsatt isolerad livscykel och resursgräns
([SUMO FAQ](https://sumo.dlr.de/docs/FAQ.html#can-sumo-be-run-in-parallel-on-multiple-cores-or-computers)).

**Beslut:** behåll högst tre isolerade dagsworkers tills ett nytt benchmark
visar att en annan gräns ger identiska resultat och acceptabelt toppminne.
Multiplicera aldrig seed- och dagsparallellism.

### 3.2 Utvärdera libsumo först efter den algoritmiska förbättringen

Libsumo undviker TraCI:s socketkommunikation och beskrivs som det bättre valet
när prestanda är viktig och GUI inte behövs. Parallella libsumo-instanser i
Python kräver multiprocessing
([libsumo](https://sumo.dlr.de/docs/Libsumo.html),
[TraCI från Python](https://sumo.dlr.de/docs/TraCI/Interfacing_TraCI_from_Python.html)).

**Beslut:** benchmarka en libsumo-instans per beständig worker. Byt inte
backend förrän semantiska resultat, avbrott, omstart, minne och felisolering är
verifierade. Denna förändring kommer efter kostnadsordnad sökning, eftersom
den senare kan ta bort mycket mer arbete än en snabbare processkoppling.

### 3.3 Separera deterministisk rangordning från SUMO:s kvalificering

`closure_cost_v1` består av värsta q10/q50/q90-värdet för tillagda
fordonstimmar, därefter tillagd sträcka och antal påverkade fordon.
`vehicles_no_detour > 0` diskvalificerar kandidaten. Dessa värden beräknas från
nät och ruttfiler och innehåller inget Monte Carlo-brus. SUMO-observationerna
används fortfarande för hälso- och integritetsgrindar.

**Beslut:** beräkna exakt kostnad före SUMO, sortera efter samma lexikografiska
nyckel som `closure_ranking.py` och SUMO-verifiera i den ordningen. Detta är en
lokal slutsats från programmets nuvarande kontrakt, inte ett påstående från en
extern källa. Den måste bevisas mot dagens uttömmande resultat innan aktivering.

### 3.4 Förbättra modellens data och kalibrering före starkare verklighetsanspråk

SUMO:s `routeSampler` kan kombinera länk-, sväng- och OD-räkningar, men
räkningarna definierar inte en unik ruttlösning. Det passar därför som
diagnostik och kompletterande demand-källa, inte som en tyst ersättare för
projektets nuvarande PFE
([SUMO: routes from observation points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html)).
SUMO varnar också för att naiv snabbaste-vägstilldelning kan överbelasta ett
fåtal flaskhalsar; DUA itererar mellan ruttning och simulerade länktider
([SUMO Dynamic User Assignment](https://sumo.dlr.de/docs/Demand/Dynamic_User_Assignment.html)).

FHWA:s vägledning kräver separat kalibrering av basmodell och
vägarbetsmodell, med geometri, efterfrågan, kapacitet, signaler, restider och
köer. För mesomodeller lyfts både kapacitets- och OD-kalibrering fram
([FHWA Work Zone Analysis](https://ops.fhwa.dot.gov/publications/fhwahop12009/sec4.htm)).
FHWA rekommenderar dessutom representativa dagar, variationsintervall,
replikationer och känslighetstestning
([FHWA Traffic Analysis Toolbox III](https://ops.fhwa.dot.gov/publications/fhwahop18036/)).

**Beslut:** håll prestandaoptimeringen och evidensförbättringen separata. En
snabbare sökning får inte marknadsföras som en mer verklighetstrogen modell.

### 3.5 Behåll meso som bred modell och micro som riktad kontroll

SUMO beskriver mesomodellen som upp till cirka 100 gånger snabbare än micro,
men den saknar explicit lateral rörelse, lane-specifik output, E2/E3-detektorer
och actuated traffic lights
([SUMO Meso](https://sumo.dlr.de/docs/Simulation/Meso.html)).

**Beslut:** meso för den breda sökningen; micro endast för finalister där
körfält, signaler eller lokal spillback kan ändra beslutet. En micro-körning
får inte läggas till överallt utan ett definierat utlösningsvillkor.

### 3.6 Inför inte CP-SAT ännu

CP-SAT stödjer heltalsvillkor, lösningsuppräkning och schemaläggning med
precedens/no-overlap
([OR-Tools CP-SAT](https://developers.google.com/optimization/cp/cp_solver),
[OR-Tools job shop](https://developers.google.com/optimization/scheduling/job_shop)).
Den nuvarande rullande kalendern har däremot en enkel, uttömmande struktur och
flaskhalsen ligger efter kalenderlogiken.

**Beslut:** inför CP-SAT först om produkten får flera arbetslag, flera
samtidiga vägarbeten, resurskapacitet, precedens eller andra kopplade
restriktioner. Det är inte en dependency för denna plan.

## 4. Målarkitektur

```text
ClosureSearchSpec
      │
      ├── exakt preflight ──> storlek, cacheträffar, risk, kostnadsbudget
      │
      └── strömmande kalendergenerator
                    │
                    ├── parent-ledger (schema + exakta datum/tider)
                    └── unika dagsenheter
                              │
                              ├── deterministisk disruption-cache
                              │         │
                              │         └── exakt aggregerad parentkostnad
                              │
                              └── SUMO-cache / beständiga workers
                                          │
                           kostnadsordnad hälsoverifiering
                                          │
                      befintlig pilot_selection + finalist_decision
                                          │
                    periodjämförelse + begränsat sanningsanspråk
```

De tre evidensslagen ska ligga i skilda artefakter:

- `calendar/preflight`: ren kombinatorik, ingen trafikmodell;
- `deterministic_disruption`: rutt- och nätbaserad rangordning, inga seeds;
- `sumo_health`: seed/variant-bundna hälso- och integritetsobservationer.

## 5. Implementationsordning

Varje steg ska landa separat och lämna den gamla vägen körbar tills stegets
exit-grind är godkänd.

### Steg 0 — Frys baslinje och referensfall

**Syfte:** göra prestanda och semantik jämförbara före kodändringar.

**Ändringar**

1. Lägg till `tools/benchmark_closure_search_scaling.py`.
2. Frys båda sexmånadersfallen ovan plus små brute-force-fall i
   `validation/closure_search_scaling_baseline_v1.json`.
3. Mät separat kalendergenerering, dagsuppdelning, deterministisk kostnad,
   cacheuppslag, SUMO-walltime, topp-RSS och artefaktstorlek.
4. Spara Python-, SUMO-, nät-, route-, policy- och källkodshashar.
5. Kör varje rent prestandafall minst fem gånger och rapportera median/p95.

**Primära filer:** nytt benchmarkverktyg, ny valideringspost,
`tests/test_independent_daily.py`.

**Exit-grind:** reproducerbara hashade baslinjer finns; mätningen får inte
skriva i befintliga evidence-rötter.

### Steg 1 — Bygg en exakt preflight

**Syfte:** visa sökningens verkliga storlek innan jobbstart och undvika sen
överraskning från 10 000-gränsen.

**Ändringar**

1. Skapa `traffic_sim/simulation/closure_preflight.py` med ett versionsbundet
   `ClosureSearchPreflight`-kontrakt.
2. Räkna utan fulla `ClosureSchedule`-objekt:
   - giltiga dagantal;
   - föräldrascheman per dagantal;
   - unika `(datum, start, slut, väg)`-dagsenheter;
   - enheter vars warm-up/recovery går utanför demandåret;
   - kända cacheträffar, kända cache-missar och `unknown` när ett exakt
     backend-/routefingerprint inte kan bestämmas read-only;
   - uppskattad mängd SUMO-arbete med tydligt angiven uppskattningsgrund.
3. Kör samma kalenderregler som produktionen: 15 minuter, tidszon, DST,
   weekdays, blackout-datum, exakta lika skift och högst 90 arbetsdagar.
4. Lägg till `POST /api/monthly_search/preflight`; endpointen ska vara
   read-only och får inte starta demandbygge eller SUMO.
5. Visa resultatet i webben före start: `normal`, `stor men körbar` eller
   `över resursbudget`. Användaren ska kunna ändra datum, timmar eller dagtak.
6. Behåll hårda resursgränser tills senare steg är godkända; höj dem inte bara
   för att preflight finns.

**Primära filer:** ny `closure_preflight.py`, `serve.py`, `web/app.js`,
`web/index.html`, `tests/test_serve.py`, ny `tests/test_closure_preflight.py`.

**Tester**

- jämför preflightens antal med faktisk generator för små och stora fixtures;
- test av årsskifte, skottår, DST, blacklist och 07:30–15:15;
- test att endpointen inte skapar run-, demand- eller SUMO-artefakter;
- test att saknat demandarkiv ger `cache_unknown`, inte en falsk cache-miss;
- egenskapsbaserat test: preflightantal = uttömmande antal för små slumpfall.

**Exit-grind:** exakt antal i samtliga fixtures; sexmånaderspreflight p95 högst
3 s och topp-RSS högst 32 MiB på baslinjemaskinen.

### Steg 2 — Strömma scheman och ta bort omvänd föräldragraf

**Syfte:** ta bort nuvarande 90 MiB-materialisering och göra 100 000
föräldrar hanterbara.

**Ändringar**

1. Lägg till `iter_closure_schedules(spec)` i `closure_calendar.py`.
2. Behåll `generate_closure_schedules()` som kompatibilitetswrapper som
   materialiserar iteratorn för gamla anropare.
3. Skapa versionsbundna NDJSON-ledgers i workspace:
   - en rad per föräldraschema;
   - en rad per unik dagsenhet;
   - en rad per parent med dess ordnade unit-ID:n.
4. Ta bort behovet att lagra alla `parent_schedule_ids` på varje unit i den
   nya exekveringsvägen. V1-cache och gamla arbetsytor ska fortfarande läsas.
5. Publicera varje ledger atomiskt med antal, byte-storlek och SHA-256 i ett
   litet manifest. En halvskriven ledger får aldrig återupptas.
6. Läs föräldrar sekventiellt vid kostnadsaggregering och bygg endast det
   minsta index som nästa fas behöver.

**Primära filer:** `traffic_sim/core/closure_calendar.py`,
`traffic_sim/simulation/independent_daily.py`,
`traffic_sim/simulation/monthly_search.py`, workspace/manifest-hjälpare och
fokuserade tester.

**Tester**

- byte-ekvivalenta `ClosureSchedule.to_dict()` mellan iterator och v1-wrapper;
- samma ID:n oavsett iterations- eller dictionaryordning;
- avbrott mitt i ledger, restart och checksummefel;
- 100 000-parent syntetiskt test utan full objektgraf i minnet.

**Exit-grind:** samma schema-ID:n och ordning som före ändringen; 720 h-fallet
ska ligga under 64 MiB och 360 h-fallet får inte stoppas av minnesmaterialisering.

### Steg 3 — Flytta deterministisk kostnad före SUMO

**Syfte:** kunna rangordna exakt utan att först simulera varje dagsenhet.

**Ändringar**

1. Extrahera `ArchivedDemandSumoRunner._closure_disruption()` till en publik,
   processfri provider, exempelvis `deterministic_disruption(schedule)`.
2. Definiera ett litet `DeterministicDisruptionProvider`-protokoll i
   `monthly_search.py` eller en ny modul. Protokollet får inte exponera TraCI.
3. Låt `MonthlyDemandResolverRunner` lösa rätt immutable routearkiv och delegera
   kostnadsberäkningen utan att starta SUMO.
4. Lägg till en separat innehållsadresserad dagskostnadscache. Nyckeln ska
   minst innehålla:
   - dagsenhetens fulla identitet;
   - q10/q50/q90-routefilernas SHA-256;
   - nätets SHA-256;
   - disruptionschemats version;
   - relevanta källkodshashar och SUMO-network-metadata-identitet.
5. Aggregera parentkostnad exakt som dagens kod: summera dagsposter **per
   variant**, ta därefter field-wise worst q10/q50/q90 och använd
   `ClosureCost.sort_key`.
6. Diskvalificera `vehicles_no_detour > 0` före SUMO, men spara full evidens och
   orsak. Saknad variant, trasig cache eller routeidentitetsfel ska fail-closed.
7. Låt den gamla post-SUMO-beräkningen vara en jämförelseväg under migreringen;
   den tas bort först efter ekvivalensgrinden.

**Primära filer:** `monthly_sumo.py`, `monthly_demand.py`,
`independent_daily.py`, `closure_ranking.py`, ny cachemodul och tester.

**Tester**

- pre-SUMO och post-SUMO disruption ska vara fältvis identiska;
- q10/q50/q90 måste summeras före worst-reduktion;
- cache återanvänds när datumintervallet vidgas men enhetsidentiteten är samma;
- ändrad route, nätfil, kod eller variant ska ge cache-miss;
- `vehicles_no_detour` får aldrig omvandlas till en vanlig hög kostnad.

**Exit-grind:** 100 % identiska kostnader och sorteringsordning på golden,
benchmark och små brute-force-fixtures.

### Steg 4 — Implementera exakt kostnadsordnad pilotverifiering

**Syfte:** köra SUMO endast där utfallet fortfarande kan påverka dagens
piloturval.

**Ny algoritm**

1. Beräkna kostnad för alla tillgängliga föräldrar från steg 3.
2. Ta bort deterministiskt diskvalificerade no-detour-kandidater.
3. Sortera resterande med befintlig `ClosureCost.sort_key`.
4. SUMO-pilotverifiera en kandidat i taget i denna ordning. Cacheträffar räknas
   som redan verifierade.
5. Fortsätt över kandidater som faller på befintliga hårda grindar.
6. När minst `policy.pilot.minimum_finalists` pilot-viabla kandidater finns,
   sätt `cutoff` till den k:te viabla kandidatens `added_vehicle_hours`.
7. Fortsätt verifiera alla kandidater vars primärkostnad är
   `<= cutoff + practical_equivalence_vehicle_hours`.
8. Stoppa först när nästa osökta kandidat ligger strikt över gränsen. Då kan
   ingen osökt kandidat komma in i dagens finalistmängd.
9. Skicka exakt samma finalistmängd till befintliga `pilot_selection.py` och
   `finalist_decision.py`. Om mängden överskrider `maximum_finalists` ska dagens
   `capacity_exceeded` behållas; kapa aldrig tyst.
10. Om alla verifierade kandidater faller, fortsätt till sökrymdens slut och
    returnera dagens `no_viable`.

Detta stoppvillkor reproducerar dagens piloturval eftersom kostnaden är
deterministisk, sorteringen är total och SUMO endast kan kvalificera eller
diskvalificera kandidaten. Det ska ändå behandlas som en hypotes tills
följande tester har passerat.

**Ändringar**

1. Skapa `traffic_sim/simulation/cost_ordered_search.py` med en ren state
   machine och serialiserbart cursor/cutoff-tillstånd.
2. Lägg till screeningläget `independent-cost-ordered-exact`; behåll
   `independent-exhaustive` som referens och diagnostisk fallback.
3. Publicera ordningsledger, verifierade ID:n, stoppgräns och en maskinläsbar
   förklaring till varför osökta kandidater inte kan väljas.
4. Gör restart idempotent: återuppta vid första icke verifierade sorterade ID.
5. Ändra inte policy v2 på plats. Skapa en provisional policy v3 när den nya
   algoritmen ska benchmarkas.

**Tester**

- differentialtest mot uttömmande pilot på alla små kalendrar;
- slumpade hårdfelsmasker och no-detour-fall;
- exakta primärties, sekundärties och kandidat-ID-tie-break;
- kandidat exakt på bandgränsen och precis över;
- färre än `minimum_finalists`, `capacity_exceeded` och alla underkända;
- crash/restart före och efter cutoff;
- ändrad policy eller kostnadsledger ska ogiltigförklara resume.

**Exit-grind:** samma pilotstatus och samma `selected_ids` som uttömmande läge
i varje fixture och i ett namngivet verkligt benchmark. Inget global-bäst-
anspråk ändras.

### Steg 5 — Integrera progress, budget och användargränssnitt

**Syfte:** användaren ska förstå vad som händer i en lång körning.

**Ändringar**

1. Utöka progressfaserna med `preflight`, `cost_units`, `cost_parents`,
   `health_scan` och `finalists`.
2. Visa `X/Y kostnadsberäknade`, cacheträffar, `A/B SUMO-verifierade` samt
   nuvarande stoppgräns.
3. Visa alltid:
   - exakt vald start/sluttid som upprepas varje arbetsdag;
   - första/sista arbetsdag och antal arbetsdagar;
   - om resultatet använder independent reset;
   - om några scheman saknade demandenvelop;
   - anspråksgränsen `best among available/verified`, inte obevisat globalt bäst.
4. Gör resursgränser till versionsbundna serverpolicies, inte dolda CLI-tal.
5. Tillåt aldrig en budget att välja bort kandidater och ändå märka resultatet
   uttömmande. Ett budgetstopp ska ge `paused`/`incomplete`, med resume-token.

**Primära filer:** `serve.py`, `period_comparison.py`, `web/app.js`,
`web/index.html`, API- och webtester.

**Exit-grind:** API-kontraktstest, browsertest för preflight/start/poll/cancel/
resume och korrekt svensk anspråkstext.

### Steg 6 — Benchmarka beständiga workers och libsumo

**Syfte:** minska processuppstart när cost-ordered-sökningen fortfarande har
många SUMO-cachemissar.

**Armar**

1. Nuvarande referens: ny Python-process och TraCI per dagsenhet.
2. Beständig Python-worker, ny extern SUMO/TraCI-instans per enhet.
3. Beständig multiprocessing-worker med en libsumo-instans åt gången.

**Genomförande**

1. Bygg en separat benchmark-harness; ändra inte produktionsdefault först.
2. Kör kalla och varma batchar med 1, 10, 100 och 500 dagsenheter.
3. Mät median/p95 walltime, CPU, topp-RSS, startkostnad och felåterhämtning.
4. Jämför fulla semantiska hashvärden för observationer, disruption,
   hard failures och cacheartefakter.
5. Döda en worker mitt i körning och verifiera att bara dess pågående enhet
   görs om.
6. Verifiera cancel och serverrestart utan föräldralösa processer.
7. Behåll processisolering: en libsumo-instans per worker, högst den redan
   godkända totalen tre.

**Adoptionsgrind:** minst 20 % förbättring av p95 batch-walltime mot referensen,
identiska semantiska resultat, ingen överskriden RSS-budget och godkända
fault-injection-tester. Annars behåll nuvarande worker.

### Steg 7 — Validera antagandet om oberoende dagar

**Syfte:** mäta när nattlig reset ändrar rekommendationen jämfört med en
kontinuerlig simulering.

**Genomförande**

1. Välj i förväg representativa 1-, 3-, 7-, 14- och 21-arbetsdagarsfall:
   låg/hög efterfrågan, morgon/dag/kväll, centralt/perifert och olika
   omledningsstrukturer.
2. Kör samma scheman både som kontinuerlig envelope och som summerade
   oberoende dagar där nuvarande 21-dagarskontrakt tillåter jämförelsen.
3. Jämför rangordning, tillagda fordonstimmar, kö-/haltingdiagnostik,
   återhämtning, teleporter, no-detour och vinnare/ties.
4. Förregistrera toleranser före resultaten.
5. Om reset ger materiella rank flips, inför en riskklass:
   - låg risk: independent-day tillåts;
   - hög risk: kontinuerlig finalistkontroll krävs eller rekommendation hålls
     inne.
6. Extrapolera inte automatiskt 21-dagarsevidens till 90 dagar; dokumentera
   att längre sökningar fortfarande är en modelleringsapproximation.

**Exit-grind:** namngiven jämförelserapport och explicit claim boundary för
1–90 dagar.

### Steg 8 — Förbättra demand och vägarbetsevidens

**Syfte:** göra kostnaden känslig för verkliga flaskhalsar och omledningar,
inte bara fri-flödessträcka.

**Genomförande**

1. Inventera tillgängliga länk-, sväng-, hastighets-, restids-, kö- och
   eventuella OD-data på valda huvud- och omledningskorridorer.
2. Lägg till nya räknare genom projektets befintliga sensor/PFE-kontrakt.
3. Kör `routeSampler` som en fristående diagnostisk jämförelse på samma
   whitelist och räkningar; ersätt inte PFE utan held-out-förbättring.
4. Skapa en DUA-känslighetsarm för några representativa dagar. Bind iterationer,
   konvergensmått, diskbudget och ruttalternativ i planen.
5. Kalibrera befintlig basmodell mot counts, travel times och speeds.
6. Kalibrera work-zone-fallet separat mot lokal kapacitet och, när sådan data
   finns, köer/restider under liknande avstängningar.
7. Kör q10/q50/q90 och flera seeds; rapportera spridning och rankstabilitet.
8. Lägg microkontroll endast på finalister vars kritiska påverkan ligger vid
   körfältsval, komplicerad signal eller lokal spillback.

**Exit-grind:** förbättring på ett fryst kalibreringsset och ett orört held-out
set; inga justeringar får göras enbart för att få vald stängning att se bättre
ut.

### Steg 9 — Ny policy, diskriminerande benchmark och releasegrind

**Syfte:** bevisa både effektivitet och beslutskvalitet utan att skriva om
historisk evidens.

**Genomförande**

1. Skapa en pre-outcome benchmark med flera hälso-viabla kandidater.
2. Frys praktisk ekvivalens i fordonstimmar, precision, finalistkapacitet och
   maxrepetitioner i en ny policy v3.
3. Kör v2 uttömmande och v3 cost-ordered på samma benchmark.
4. Kräv identiska kandidatkostnader, hard failures, finalistmängd och beslut.
5. Frys därefter ett nytt, orört held-out-set med varierande vägar, månader,
   dagantal och demandnivåer.
6. Mät practical-winner recall, regret, failure recall, rank flips mellan
   reset/continuous och faktisk SUMO-besparing.
7. Öppna release/UI-anspråk endast om hela validationsposten och dess adoption
   klarar befintliga provenance- och integritetsgrindar.
8. Bevara policy v1, v2 och alla gamla artefakter byte-identiskt.

**Slutlig acceptans:**

- 249 nuvarande fokuserade tester fortsätter passera;
- nya differential-, preflight-, cache-, restart- och fault-injection-tester
  passerar;
- 360 h-fallet avvisas inte enbart på grund av den gamla 10 000-gränsen;
- cost-ordered och exhaustive väljer samma finalister och resultat;
- faktisk SUMO-besparing redovisas på namngivet benchmark;
- global-bäst förblir `false` tills nytt held-out och adoption har passerat.

## 6. Rekommenderade leveranser

Arbetet bör delas i följande små, verifierbara leveranser:

1. **PR A:** baslinjeverktyg och fryst prestandapost.
2. **PR B:** read-only preflight + API/UI.
3. **PR C:** strömmande ledgers och minnesgrind.
4. **PR D:** processfri disruption-provider och separat kostnadscache.
5. **PR E:** cost-ordered state machine i shadow mode; jämför mot exhaustive.
6. **PR F:** provisional policy v3 och aktivering efter ekvivalensbenchmark.
7. **PR G:** persistent-worker/libsumo-benchmark och eventuell adoption.
8. **PR H:** reset-vs-continuous-evidens.
9. **PR I:** demand/work-zone-kalibrering och nytt held-out.

PR A–F förbättrar skalningen. PR G är en villkorad fartoptimering. PR H–I
förbättrar hur starkt resultatet kan tolkas i verkligheten.

## 7. Första konkreta arbetsordern

Nästa implementation ska börja med PR A och PR B:

1. frys de två uppmätta sexmånadersfallen;
2. implementera den rena preflight-räknaren;
3. differentialtesta den mot den befintliga generatorn;
4. exponera read-only preflight i API och webb;
5. mät tid/minne igen;
6. gå vidare till strömmande ledgers först när preflightens antal är exakt.

Detta ger omedelbar användarnytta och en säker mätgrund för de mer ingripande
ändringarna, utan att röra rangordning, SUMO-resultat eller releasepolicy.
