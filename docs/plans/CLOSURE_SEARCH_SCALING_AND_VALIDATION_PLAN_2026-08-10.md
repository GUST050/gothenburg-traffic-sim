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


---

## 8. Uppmätt resultat: PR A och PR B (2026-08-10)

Tillagt efter genomförandet. Avsnitt 1–7 är oförändrade; inget historiskt
resonemang och ingen tidigare slutsats har skrivits om.

### PR A — fryst baslinje

`tools/benchmark_closure_search_scaling.py` →
`validation/closure_search_scaling_baseline_v1.json`. Sex fall: planens två
sexmånadersfall plus fyra små brute-force-fall (en arbetsdag, rullande period
över helg, blackout-delad körning, DST-övergång). Fem repetitioner per fas,
median och p95. Baslinjen binder Python, SUMO, nät, rutter, policy och
källkodshashar — inklusive `closure_teleport.py` och
`closure_survivability.py`, eftersom de avgör vad en stängningskörning ÄR.
Posten är innehållsadresserad över sina INDATA (`input_content_key`), inte
över mätvärdena, som legitimt varierar mellan körningar. Den är märkt
`diagnostic_baseline` och öppnar ingen grind.

| fall | föräldrar | dagsenheter | kalender p95 | preflight p95 | RSS materialisera | RSS preflight |
|---|---:|---:|---:|---:|---:|---:|
| 720 h | 2 186 | 5 676 | 2,900 s | 0,0147 s | 175,5 MiB | 16,4 MiB |
| 360 h | 11 813 | 23 349 | 12,095 s | 0,0514 s | 489,9 MiB | 21,8 MiB |
| brute-force (4 fall) | 85–754 | 85–910 | 0,004–0,054 s | 0,0006–0,0031 s | 76,6–79,7 MiB | 15,2–15,5 MiB |

Antalen reproducerar avsnitt 2:s tabell exakt (2 186/5 676 och 11 813/23 349),
vilket är vad som gör posten till samma referens. Varje fall registrerar också
`semantic_agreement`: preflightens antal jämförs mot generatorns och båda
sparas, så en framtida snabbare väg som ändrar ett antal faller här istället
för att läsas som en förbättring.

RSS mäts PER FALL i en egen tolk. Föräldraprocessens topp är kumulativ över
alla fall och är märkt som sådan. Fallbacken normaliserar `ru_maxrss` korrekt:
macOS rapporterar byte medan Linux rapporterar KiB. Claudes första version
multiplicerade macOS-värdet med 1 024 och kunde därför rapportera 19 GiB för en
19 MiB-process; ett plattformsregressionstest spärrar det felet.

De två externa faser som arbetsordern kräver är också uppmätta fem gånger i en
explicit diagnostisk arm: `deterministic_cost` för q10/q50/q90 har median
12,564 s och p95 12,799 s; en q50-dagsenhet i SUMO har median 8,808 s och p95
9,040 s. Armen binder demand-meta, SUMO 1.27.1 och binärens SHA-256, nätet,
alla tre routefiler och de exekverade källorna. SUMO-resultaten skapas endast
under den privata temporära roten och tas bort efter varje repetition; posten
behåller mätning, scope och resultatdigest men inget scenarioresultat. Därmed
mäts faserna utan att skriva i befintliga evidence-rötter eller öppna en
releasegrind.

### PR B — exakt read-only preflight

`traffic_sim/simulation/closure_preflight.py` + `POST
/api/monthly_search/preflight` + UI-visning före start.

**Exit-grinden är uppfylld:** exakta antal i samtliga fixtures, och
sexmånaderspreflight p95 0,0147 s / 0,0514 s mot taket 3 s, topp-RSS 16,4 /
21,8 MiB mot taket 32 MiB.

Räkningen är en run-length-identitet i stället för en objektvandring: en
maximal följd av `L` användbara datum innehåller `max(0, L - n + 1)` fönster
av längd `n`. Det ger 40 (dagantal, starttid)-par över ~130 möjliga datum för
sexmånadersfallen, i stället för tiotusentals objekt. `exact_equal_daily_v1`
räknas på den behörighetsindexerade axeln och `equal_daily_rounded_v1` på
kalenderaxeln, eftersom generatorn stegar olika under de två.

Exaktheten är differentialtestad mot den riktiga generatorn och den riktiga
dagsuppdelningen: 40 slumpade kontrakt plus årsskifte, skottdag, båda
DST-övergångarna, blackout-datum, nattband över midnatt och planens
07:30–15:15 inom 06:00–18:00. Kuvertklassificeringen (warm-up/recovery
utanför demandåret) jämförs mot `build_simulation_envelope` självt.

`exact_balanced_daily_v1` skapar upp till 4 096 varaktighetsmönster vars
giltighet varierar med positionen i fönstret. Den VÄGRAS
(`UnsupportedPreflightSpec` → HTTP 422) i stället för att approximeras: ett
tal som utger sig för att vara exakt utan att vara det vore sämre än ingen
uppskattning alls.

**Cacheläget är `unknown`, inte en falsk miss.** Den dagliga
backend-identiteten som cachen nycklas på existerar först efter att
demand-resolvern förberett ett arkiv, vilket en read-only-anrop inte får
göra. Att kalla varje enhet en miss skulle underskatta cachen; att kalla dem
träffar skulle överskatta den. Ett tredje läge redovisas därför explicit,
och en anropare som redan känner identiteterna får exakta träffar/missar.
Kända cacheträffar räknas endast för körbara enheter: warm-up/recovery-enheter
utanför demandåret kan aldrig bli SUMO-arbete och får därför inte minska den
uppskattade arbetsmängden en andra gång.

**Resursgränsen är oförändrad.** Preflighten RAPPORTERAR mot 100 000
föräldrar och 10 000 dagsenheter; den höjer dem inte. 360 h-fallet klassas
`over_resource_budget` före start — precis den sena överraskning avsnitt 2
beskriver — och UI:t vägrar starta det men lämnar datumintervall, arbetstimmar
och dagtak redigerbara.

### Vad som INTE gjordes

PR C (strömmande ledgers), PR D–F och allt som rör rangordning, beständiga
SUMO-utfall eller releasepolicy är orörda. PR A:s isolerade SUMO-probe är
diagnostisk och dess temporära utfall tas bort. Ingen v11 skapades och ingen
held-out-kampanj kördes. `validation/monthly_proxy_manifest_v10.json` är
avsiktligt oförändrad historisk evidens för 03ca5d7 och rapporterar fortsatt
källdrift.

---

## 9. Uppmätt resultat: PR C (2026-08-10)

Tillagt efter genomförandet av steg 2. Avsnitt 1–8 är oförändrade; PR A:s och
PR B:s historiska mätningar har inte skrivits om.

### Vad som byggdes

`iter_closure_schedules(spec)` i `closure_calendar.py` ger scheman lat i
identisk kanonisk ordning; `generate_closure_schedules(spec)` är nu
`tuple(iter_closure_schedules(spec))` och behålls för cachar, frysta
kampanjverktyg och alla tidigare anropare. Uppräkningskroppen är oförändrad —
den `yield`:ar där den tidigare `append`:ade — eftersom schema-ID:n,
intervallordning och sekvensordning är kontrakt.

`traffic_sim/simulation/closure_ledgers.py` skriver tre NDJSON-ledgers i en
strömmande pass: `parents.ndjson`, `units.ndjson` (en rad per UNIK dagsenhet)
och `parent_units.ndjson` (en rad per parent med dess ordnade unit-ID:n). Den
omvända unit→parents-grafen finns inte längre i den nya vägen; den var bara
inversen av relationsledgern och kostade minne proportionellt mot
parents×dagar. Enda index i minnet under skrivningen är mängden redan skrivna
unit-ID:n. `StreamingDailyUnit` bär `unit_id`, `schedule` och `identity` —
inget föräldrafält. `DailyClosureUnit` är oförändrad för v1-anropare.

Identiteten beräknas genom EN implementation, `daily_unit_records`, som både
v1-vägen och strömningsvägen använder. Schemaobjektet byggs SENT, bakom en
`build`-callable: en parent bidrar med en post per intervall men bara en unik
enhet behöver ett schemaobjekt (171 880 poster mot 5 676 enheter i 720 h-fallet,
85 µs mot 11 µs styck). Att bygga det ivrigt gjorde `decompose_schedules` fem
gånger dyrare — en regression i v1-vägen som upptäcktes och åtgärdades innan
mätningen; efter fixen kostar dekompositionen 3,56 s för 720 h-fallet på
mätmaskinen, i nivå med baslinjens 3,55 s.

Publicering är atomisk och ordnad: varje ledger skrivs till `.partial`,
flushas, fsync:as, `os.replace`:as och katalogen fsync:as; manifestet
publiceras SIST och dess närvaro är färdigsignalen. Saknat manifest ⇒
`LedgerIncomplete` (säkert att bygga om). Manifest som inte stämmer med sina
ledgers ⇒ `LedgerCorrupt` och stopp — storlek, SHA-256 OCH radantal kontrolleras
var för sig.

`monthly_search._candidate_ledger` öppnar uppräkningen i tre lägen: gammal
`candidate-ledger.json` läses precis som den skrevs, publicerat manifest
verifieras och failar stängt, och en opublicerad katalog är ett byggområde som
byggs om. `ParentLedgerIndex` håller byte-offset per schema-ID i stället för
objekt, så screening, pilot och periodjämförelse arbetar mot samma
`Mapping[str, ClosureSchedule]`-söm som förut.
`IndependentDailyRunner.prepare_from_ledgers` läser bara kortlistans parents och
de enheter de refererar. En backend utan den metoden får fortfarande en
materialiserad kortlista, men bara under `MATERIALISED_SHORTLIST_LIMIT` (512) —
över det vägrar den i stället för att tyst falla tillbaka.

### Mätning

`tools/benchmark_closure_streaming.py` →
`validation/closure_search_streaming_v1.json` (`diagnostic_comparison`, öppnar
ingen grind). Samma sex frysta fall, fem repetitioner, varje fall kört BÅDE
materialiserande och strömmande i en egen färsk tolk på SAMMA värd i samma
körning. PR A:s baslinje skrivs inte om och rapporterar nu avsiktlig källdrift
på de fyra filer PR C ändrade; dess externa SUMO- och deterministic_cost-armar
kördes inte om.

| fall | föräldrar | dagsenheter | ström p95 | materialisera p95 | ström RSS över import | materialisera RSS över import |
|---|---:|---:|---:|---:|---:|---:|
| 720 h | 2 186 | 5 676 | 7,425 s | 6,541 s | **1,33 MiB** | 173,53 MiB |
| 360 h | 11 813 | 23 349 | 30,474 s | 27,238 s | **7,00 MiB** | 708,88 MiB |
| brute-force (4 fall) | 85–754 | 85–910 | 0,046–0,417 s | 0,040–0,364 s | 0,00 MiB* | 0,00–9,22 MiB |

\* De små fallen stannar under värdens RSS-högvattenupplösning; den dynamiska
smoke-fixturen kräver därför icke-negativa deltan, medan minnesminskningen
krävs och mäts på de två namngivna sexmånadersfallen.

Strömning byter minne mot väggtid, och det ska sägas rakt ut: den skriver
29 MB (720 h) respektive 121 MB (360 h) NDJSON till disk, vilket
minnesvägen inte betalar. På 720 h-fallet är materialisering därför en aning
SNABBARE i median (6,45 s mot 7,38 s). Vinsten är minnet — och att ledgern
efteråt finns kvar som oföränderlig evidens i stället för att ha existerat
bara i en process.

Antalen är identiska med PR A:s och avsnitt 2:s tabell (2 186/5 676 och
11 813/23 349) och `semantic_agreement.all_match` är sant i samtliga sex fall.
Byte-ekvivalensen mellan iterator och wrapper är låst i testsviten med frysta
`to_dict()`-digester tagna med implementationen FÖRE PR C, inte i den här
posten: en prestandapost ska inte vara enda stället där en korrekthetsegenskap
kontrolleras.

Ledgerstorlekar för 720 h: `parents.ndjson` 2 186 rader / 17 209 558 B,
`units.ndjson` 5 676 rader / 5 335 440 B, `parent_units.ndjson` 2 186 rader /
6 677 902 B. För 360 h: 11 813 / 71 404 374 B, 23 349 / 21 948 060 B och
11 813 / 27 448 965 B.

### Exit-grinden: en del uppfylld, en del ÖPPEN

Samma schema-ID:n och samma ordning som före ändringen: **uppfyllt**, låst av
frysta digester över fem kontraktsformer plus ledgerbytes reproducerade under
tre `PYTHONHASHSEED`-värden i riktiga barntolkar.

360 h-fallet stoppas inte längre av minnesmaterialisering: **uppfyllt**. Det
räknar upp 11 813 föräldrar och 23 349 enheter i 7,00 MiB över importbaslinjen,
mot 708,88 MiB för v1-vägen (101× mindre).
Det är fortfarande — avsiktligt — VÄGRAT av 10 000-enhetstaket, som PR C inte
rör.

720 h under 64 MiB: **uppfyllt efter slutmätningen 2026-08-11.** Fem
repetitioner på Darwin/arm64 gav 25,30 MiB processtotal, utan importerad SciPy,
mot 64 MiB-gränsen. Se avsnitt 10.

### Reviewkorrigeringar efter implementationen

Produktens `independent-exhaustive`-CLI kör nu den exakta PR B-preflighten före
nätfingerprint, runnerkonstruktion och öppning/publicering av sökworkspace. Ett
överbudgetfall stoppas därmed innan den stora candidate-ledgern skrivs.
För legacy-allokeringspolicyn `exact_balanced_daily_v1`, vars exakta preflight
avsiktligt är unsupported, bevaras kompatibiliteten och båda taken kontrolleras
i den strömmande passagen. Preflightens antal jämförs mot den faktiska
strömningen när den stöds; avvikelse stoppar körningen.

Reviewn rättade även två benchmarkfel: en liten fixture får ligga under
värdens RSS-upplösning, och en öppen processgrind på den jämförbara värden får
inte beskrivas som om endast en ommätning återstod.

### Vad som INTE gjordes

PR D–F, kostnadsordning, policy v3, rangordning, `closure_cost_v1`,
pilotval, finalistbeslut, teleport-policy och survivability är orörda.
10 000-enhetstaket och 100 000-föräldratak är oförändrade. Inget fryst v1-,
v6-, v9- eller v10-artefakt skrevs om, ingen v11 skapades, ingen
held-out-kampanj kördes och ingen årlig uppvärmningsindata rördes.

---

## 10. Steg 0 efter PR C: den fasta importkostnaden (2026-08-10/11)

Avsnitt 1–9 är oförändrade. Detta avsnitt beskriver arbetet som gjordes för att
PR C:s processtotalgrind först gjordes mätbar och därefter stängdes på den
frysta referensplattformen.

### Vad mätningen visade

Importkedjan profilerades i en färsk tolk, modul för modul, på mätvärden
(Linux/x86_64):

| import | RSS efter | tillägg |
|---|---:|---:|
| tom tolk | 7,64 MiB | — |
| `traffic_sim.core.contracts` | 17,06 MiB | +9,41 |
| `closure_calendar`, `closure_preflight`, `closure_ledgers` | 17,72 MiB | +0,66 |
| **`finalist_decision`** | **99,40 MiB** | **+81,68** |
| `independent_daily`, `monthly_search` | 99,96 MiB | +0,56 |

Hela processtotalen var alltså EN rad: `from scipy.stats import t as
student_t` på modulnivå i `finalist_decision.py`. SciPy används på exakt ett
ställe — en t-kvantil i en konfidensbredd — och `independent_daily` importerar
`finalist_decision`, så uppräkning, preflight, ledgerskrivning och
kostnadsordning betalade 81,68 MiB för en fördelning de aldrig utvärderar.

Produktens CLI var värre. `run_monthly_closure_search.py` importerade
`monthly_demand` och `monthly_sumo` på modulnivå, vilka når `run_scenario`
(pandas, +59,4 MiB) och `suggest_closure_time` (SciPy, +61,9 MiB):
**130,6 MiB innan `main()` ens börjat**, även för en körning som den exakta
preflighten omedelbart vägrar.

### Vad som ändrades

1. `finalist_decision._student_t_ppf()` importerar SciPy **lazily**. Anropet
   och numeriken är oförändrade — samma `scipy.stats.t.ppf` — bara tidpunkten
   är ny. En konfidensbredd betalar fortfarande för SciPy, exakt där den
   beräknas.
2. `approved_seed_workers` och `SEED_WORKER_BENCHMARK_RECORD` flyttades
   oförändrade till `traffic_sim/simulation/seed_worker_budget.py`, en modul
   utan simuleringsberoenden. `monthly_sumo` re-exporterar båda namnen, så
   ingen befintlig importör påverkas.
3. `run_monthly_closure_search.py` importerar SUMO-sidan via
   `_simulation_backends()` först när en simulering blir verklig, och läser
   spec/policy och kör den exakta preflighten INNAN dess — och innan
   demand-workspace-låset tas.

Exakt beteende, cache-ID:n, schema-ID:n, kostnader och finalistbeslut är
oförändrade. `tests/test_finalist_decision.py` och
`tests/test_pilot_selection.py` (50 tester) passerar oförändrade.

### Mätt effekt

| process | före | efter |
|---|---:|---:|
| hela sökimportkedjan | 99,96 MiB | **21,62 MiB** |
| produktens CLI vid import | 130,60 MiB | **21,68 MiB** |
| 720 h strömmande, processtotal | ~102 MiB (Linux) | **23,25 MiB** (Linux) |
| 720 h strömmande, slutgrind | jämförbar Darwin/arm64 krävs | **25,30 MiB** (Darwin/arm64) |

`tests/test_search_import_cost.py` kör riktiga barntolkar och kräver att
uppräkning, preflight och ledgerskrivning aldrig laddar SciPy, samt att
t-kvantilen fortfarande gör det där den används.

### Grindens status

Den sista fem-repetitionskörningen 2026-08-11 gjordes på Darwin/arm64, samma
plattformsklass som den frysta referensen. 720 h-processtotalen blev 25,30 MiB
mot 64 MiB och `imported_scipy` är `false`. Posten rapporterar
`memory_gate.status = passed`: **PR C:s minnesgrind är STÄNGD.**

Reproduktionskommando:

    python3 tools/benchmark_closure_streaming.py --repeats 5 --overwrite

och läs `validation/closure_search_streaming_v1.json` → `memory_gate.status`.

---

## 11. Uppmätt resultat: PR D, PR E, PR F, steg 4 och PR H (2026-08-10)

Detta avsnitt dokumenterar implementationerna efter PR C och deras öppna
evidensgrindar.

### PR D — processfri deterministisk disruption (steg 3)

`traffic_sim/simulation/deterministic_disruption.py`. Publik provider utan
TraCI eller SUMO-process: `DeterministicDisruptionProvider` (protokoll),
`ArchiveDisruptionProvider` (ett arkiv + nät → per-variant-poster),
`NetworkCostModel` (byggs en gång per nät), `DailyCostCache`.

- Aggregering: `sum_daily_disruption` summerar PER VARIANT före field-wise
  worst; `parent_closure_cost` använder oförändrad `ClosureCost.sort_key`.
- `vehicles_no_detour > 0` diskvalificerar före SUMO;
  `disqualification_evidence` behåller hela evidensen.
- Cacheidentiteten binder full dagsenhetsidentitet, SHA-256 för alla tre
  routefiler, nätets SHA-256, validerad adjacency-metadatas SHA-256,
  demand-metadata, disruptionschemats version och bytes för varje källa som
  beräknar talet (`run_scenario.py`,
  `deterministic_disruption.py`, `closure_ranking.py`).
- Routehashar tas en gång när det immutable arkivet öppnas; billiga filstate-
  kontroller vägrar drift under en öppen provider. Concurrent writers använder
  unika partialfiler och atomisk replace.
- Fail-closed: saknad variant, oläsbar post, post vars lagrade identitet inte
  matchar nyckeln, ofullständig variantmängd, schema från annan sökning.
- Den gamla post-SUMO-vägen är inte en andra implementation:
  `monthly_sumo._closure_disruption` delegerar till samma provider och delar
  intervall→sekunder-konverteringen.
- `MonthlyDemandResolverRunner.archive_for()` och
  `.deterministic_disruption_provider()` löser rätt immutable arkiv och
  delegerar utan att starta SUMO.

**Tester efter Codex-review:** `tests/test_deterministic_disruption.py` 32
passerade. Reviewen rättade ett identitetsfel där en explicit `network_path`
hashades medan kostnaden byggdes från `run_scenario.NET_PATH`, och band den
metadata som faktiskt kan välja adjacency.
**Real-golden-grinden är stängd.** Reviewen hittade den redan pinnade
q10/q50/q90-golden-arkivet på utvecklingsmaskinen och körde
`tools/verify_closure_cost_ordering_golden.py`. Alla tre kandidater är
fältidentiska mellan processfri provider och aktuell runner-väg; de publicerade
historiska kostnadsfälten och sorteringsordningen är också identiska. De enda
nya fälten är de två avsiktliga survivability-räknarna. Posten
`validation/closure_cost_ordering_golden_v1.json` reproducerar byte-för-byte.

### PR E — kostnadsordnad state machine i shadow mode (steg 4)

`traffic_sim/simulation/cost_ordered_search.py`. Ren state machine med
serialiserbart cursor/cutoff/verified-tillstånd, maskinläsbart stoppbevis och
`identity_key` som ogiltigförklarar resume vid ändrad policy, kostnadsledger
eller provideridentitet. Cursor måste vara exakt samma prefix som `verified`,
viability måste ligga i samma ordning och stämma med den persistenta evidensen;
det valideras även för ett direkt dataclass-objekt som inte gått genom JSON.

Stoppregeln är planens: no-detour diskvalificeras före SUMO, resten sorteras
med oförändrad `sort_key`, hårda fel stoppar inte skanningen, cutoff sätts vid
k:te viabla kandidatens `added_vehicle_hours`, och verifiering fortsätter
medan nästa kandidat är `<= cutoff + practical_equivalence_vehicle_hours`.
Kandidat exakt på gränsen är INNANFÖR (`<=`). Modulen beslutar ingenting själv
— finalistmängden går till oförändrad `select_pilot_finalists`, så
`capacity_exceeded` och `no_viable` är oförändrade och ingen finalistmängd kan
kapas tyst.

**Tester efter Codex-review:** `tests/test_cost_ordered_search.py` 77
passerade, nästan alla
differentiella mot uttömmande läge: små kalendrar, primär/sekundär/ID-ties,
kandidat exakt på cutoff och precis över, hårdfelsmasker, alla underkända,
alla no-detour, `capacity_exceeded`, för få finalister, 24 slumpade
kontrakt/mask-kombinationer, restart före och efter cutoff samt fyra
resume-ogiltigförklaringar. På 50 kandidater verifieras 4.

Screeningläget `independent-cost-ordered-exact` är registrerat men SHADOW
ONLY: det kör samma uttömmande screening och replayer dessutom den
kostnadsordnade skanningen över körningens egna registrerade beslutsindata,
och skriver `cost-ordered-shadow.json` bredvid workspace. Ingen rangordning,
ingen finalistmängd och inget anspråk ändras.

Detta är uttryckligen en post-hoc replay efter den uttömmande körningen. Den
persistenta state-machine-cursorn driver ännu inte produktens SUMO-exekvering.

**Den namngivna real-benchmark-grinden är stängd.** Golden-replayen ger samma
pilotstatus (`ready`) och samma `selected_ids`
(`closure-d9af6f11562e20e708e5`) som exhaustive. Den sparar däremot 0 av 3
verifieringar eftersom benchmarken bara har en health-viable kandidat. Därför
är positiv-besparingsgrinden och all aktivering fortfarande stängda.

### PR F — policy v3 (steg 4/5)

`validation/monthly_search_policy_v3.json` är provisional och ändrar ENDAST
exekveringsordning: varje beslutsparameter är kopierad oförändrad från v2.
`validation/monthly_search_policy_v3_preregistration.json` fryser praktisk
ekvivalens, precision, finalistkapacitet och maxrepetitioner före
benchmarkutfallet, och säger uttryckligen att aktivering och alla UI- och
global-best-anspråk förblir stängda. v1 och v2 är oförändrade.
**Policy v3 kan INTE aktiveras.**

### Steg 4 — progress, budget och UI

`PROGRESS_PHASES` deklarerar hela vokabulären på ett ställe:
`policy, preflight, enumerate, screen, cost_units, cost_parents, health_scan,
prepare_backend, pilot, finalists, decide, adaptive_finalists, publish`.
`SearchWorkspace.update_progress` tar en valfri `detail`-mapping som valideras
och serialiseras vid skrivning. `serve.py` skickar redan hela progress-objektet
vidare. `web/app.js` etiketterar varje fas och visar detaljraden: X/Y
kostnadsberäknade, cacheträffar, A/B SUMO-verifierade och aktuell gräns i
fordonstimmar. Bundlepinnen är v20.
`tests/test_monthly_progress_contract.py` (10) låser faserna, etiketterna och
detaljen mot varandra.

### PR H — förregistrering av independent vs continuous (steg 7)

`validation/independent_vs_continuous_preregistration_v1.json`: 84 fall, 35
parbara, med frysta toleranser, jämförda mått och båda riskklasserna.

Två KONTRAKTSFYND, viktigare än vad en kampanj hade gett:

1. `ClosureSearchSpec` VÄGRAR en continuous-stängning längre än 21 arbetsdagar.
   Ovanför 21 finns ingen motfaktisk att jämföra mot — inte av brist på
   demand, utan enligt kontraktet. Planens förbud mot att extrapolera
   21-dagarsevidens till 90 har därmed en konkret grund: det finns inget att
   extrapolera från.
2. De två policyerna går på olika datumaxlar. Med helger uteslutna existerar
   ingen continuous-körning på 7 arbetsdagar alls. En parbar jämförelse bortom
   en arbetsvecka är därför bara uttryckbar när alla veckodagar tillåts — nu en
   explicit axel. En overnight-bandbredd är oparbar åt andra hållet:
   independent-policyn kan inte uttrycka den.

**Ingen mätning är gjord.** Posten öppnar ingen grind.

### Grindstatus och kvarvarande blockerare

| steg | status | blockerare | exakt kommando |
|---|---|---|---|
| PR D real-golden-grind | passerad | — | `python3 tools/verify_closure_cost_ordering_golden.py --verify` |
| PR E namngiven real-benchmark | passerad ekvivalens, 0 besparing | endast en health-viable kandidat | samma verifieringskommando |
| PR F aktivering | blockerad | inget pre-outcome diskriminerande benchmark med positiv besparing, ingen held-out | se `monthly_search_policy_v3_preregistration.json` |
| PR G (steg 6) | blockerad | Linux v1 och Darwin v2 hittar eclipse-sumo 1.27.1 och plattformens libsumocpp-bibliotek men INGEN Python-bindning | `python3 tools/preflight_libsumo.py --out validation/libsumo_preflight_v3.json` på en ny miljö; installation kräver separat auktoritet |
| PR H mätning | harness BYGGD och körd; v2 har FEM kategorier; 0 mätta fall | 35 av 84 fall blockerade på kalibrerade arkiv i denna miljö; 11 av dem har dessutom olika kandidatrum | `python3 tools/measure_independent_vs_continuous.py --runs-root <arkivrot> --out validation/independent_vs_continuous_outcome_v3.json` |
| PR I (steg 8) | delvis PERMANENT stängd | projektbeslut 2026-07-20: ingen ny extern data (se CLAUDE.md "Open questions"). Mikrosimulering och work-zone-kalibrering kräver dessutom kalibrerad demand | ingen — beslutet är en fast gräns, inte en TODO |
| Steg 8 (benchmark, held-out, releasegrind) | blockerad av runtime | verklig v2 är förregistrerad och körd; första uttömmande SUMO-observationen nådde oförändrade 300 s och utfallet är `failed_execution`. Profilering fann att independent-day cold körde arkivsvansen och att `SystemExit` avbröt hela sökningen; koden kör nu envelope-midnight→recovery med `flush=0`, binder fönstret i cache-ID:t och gör timeout till kandidat-local hard failure. En diagnostisk körning klarade kandidat 0 men kandidat 1/q10 överskred fortfarande 300 s. | preregistrera v3 med nya source-digests och kör utan `--allow-drift`; höj inte timeout och redigera inte v2 |

Förregistreringens `blocked_by`-text om att inget kalibrerat arkiv finns bevaras
oförändrad som pre-outcome historik, men är superseded för denna dev-maskin:
den har det pinnade golden-arkivet och hundratals kalibrerade dagsarkiv. De
återstående blockerarna är produktintegration, diskriminerande cases,
`libsumo`, held-out och externa-data-gränsen — inte frånvaro av demand.

## 12. Uppmätt resultat 2026-08-11: stegen 1-5

Utfört på branchen `claude/closure-cost-ordered-product-integration` ovanpå
`73f5116`. **Ingenting aktiverades.** Policy v3, global-best och UI-anspråk är
oförändrat stängda.

### Steg 1 — cost-ordered execution är nu produktvägen

`traffic_sim/simulation/cost_ordered_execution.py` (NY) kopplar ordningen till
verklig exekvering: `run_monthly_search(..., cost_source=...)` byter ut den
uttömmande piloten mot `_cost_ordered_pilot`, och prissättningen sker INNAN
någon SUMO-process finns — inte som efterhandsreplay. Båda piloterna bygger sin
evidens genom samma `_pilot_evidence_for`, så vägarna kan skilja sig i VILKA
kandidater som simuleras men aldrig i HUR.

Den durabla markören SPEGLAR scanningens egen bokföring i stället för att haka
in i den: `cost_ordered_search.py` är bunden byte-för-byte av
`validation/closure_cost_ordering_golden_v1.json`, så en callback där hade
brutit golden-postens källdigester. Spegeln rekonstrueras ur samma indata under
samma regel, och varje körning hävdar till sist att den aldrig divergerat — ett
felinjektionstest saboterar scanningens returnerade tillstånd och bevisar att
kontrollen slår till. 21 tester.

### Steg 2 — benchmarken är förregistrerad strukturellt, inte körd

`tools/cost_ordered_benchmark.py` väljer sitt fall enbart ur egenskaper som är
kända före körning och kan inte konsultera ett utfall (testerna monkeypatchar
`parent_closure_cost` och `ArchiveDisruptionProvider.disruption` till att
kasta). Elva grindtrösklar är frysta i förväg, inklusive ett strikt positivt
`sumo_verifications_saved_minimum`. 18 tester.

`validation/cost_ordered_benchmark_registration_v1.json` i detta träd:
`status = blocked_no_structurally_eligible_case`, `archives_available = 0` —
uppmätt, inte antaget. De åtta utvärderade fallen bär 81-90 kandidater vardera
och skulle alltså diskriminera; de saknar bara arkivbiblioteket.

### Steg 3 — held-out: BLOCKERAD, inget kört

Held-out-validering får per plan köras först efter att benchmarken passerat.
Benchmarken kunde inte köras här, alltså finns ingen held-out-evidens och
ingen produceras. Inga observationer rördes.

### Steg 4 — independent vs continuous: harness byggd och körd

`tools/measure_independent_vs_continuous.py` binder till förregistreringen via
content key och skriver en SEPARAT utfallspost;
`validation/independent_vs_continuous_preregistration_v1.json` är oförändrad.
84 fall undersökta: 24 `unsupported_by_contract`, 25 `unpairable`, 35
`blocked_missing_demand`, 0 `measured`. 4,4 s, 137 MiB topp-RSS, content key
stabil mellan körningar. 22 tester.

**Ett tredje kontraktsfynd, uppmätt här.** Förregistreringens parbarhetstest
jämför det FÖRSTA schemat varje policy räknar upp. Det räcker inte: 11 av dess
35 "parbara" fall söker olika rum, åt båda hållen.

* `equal_daily_rounded_v1` avrundar varje dagspass UPPÅT, så continuous kan
  klara samma arbetskrav på FÄRRE dagar. 21-dagarsfallet (midday) räknar upp
  17-, 18-, 19-, 20- och 21-dagarsscheman — 470 kandidater mot independent-
  armens 150 — och de korta schemalägger upp till 5130 minuter för ett krav på
  5040. `exact_equal_daily_v1` kan inte uttrycka något av dem.
* Independent går på i tur och ordning TILLÅTNA datum och kan därför spänna
  över en helg där kalenderföljande continuous inte kan: 8 mot 6 kandidater i
  3-dagarsfallen med enbart vardagar.

Harnessen behandlar detta som avgörande: ett avvikande kandidatrum, eller en
vinnare den andra armen inte kan uttrycka, kan aldrig rapporteras som låg risk.
Taket på 21 arbetsdagar höjs INTE här —
`docs/plans/CONTINUOUS_CLOSURE_CEILING_2026-08-11.md` beskriver vad den
ändringen faktiskt skulle kosta.

### Steg 5 — PR G korrigerad, PR I oförändrat stängd

`tools/preflight_libsumo.py` (read-only, installerar ingenting) visar att
eclipse-sumo 1.27.1 ÄR installerat med libsumo:s C++-bibliotek och headers men
utan Python-bindning. Planens tidigare åtgärd (`pip install eclipse-sumo`) hade
alltså inte hjälpt. 10 tester.

PR I är oförändrad: projektbeslutet 2026-07-20 att inte hämta mer extern data
står kvar som en fast gräns, inte en TODO. Ingen dataförfrågan skickades och
ingen föreslås.

## 13. Codex-review och verkligt v2-utfall 2026-08-11

Reviewen korrigerade fyra evidensfel. Discovery får inte likställa arkivets
startdatum med vägens arbetsdatum: produktens independent-daily-kontrakt använder
normalt ett tredagars warm-up-envelope. Därför verifieras nu varje föreslaget
fall genom `MonthlyDemandResolverRunner` och `find_demand_archives`, inklusive
manifest, generator/runtime, proveniens och outputdigester. En enskild
kalenderdag tillåts eftersom dess olika 15-minutersstarter ändå ger 9–13
kandidater. Nätverk, metadata och workspace-lock binds till samma explicita
data root som SUMO använder. Runtimefel publiceras som separata
`failed_execution`-utfall i stället för att lämna registreringen utan svar.

`validation/cost_ordered_benchmark_registration_v2.json` frystes före utfall.
Den valde 13 scheman den 2027-03-22 och exakt demand build
`5ac74750843384b3`. Första observationen i den uttömmande armen nådde den
oförändrade 300-sekundersgränsen (`seed 1000`).
`validation/cost_ordered_benchmark_outcome_v2.json` sätter därför alla elva
grindar till falskt. Timeouten höjs inte, held-out körs inte och policy v3
förblir inaktiv.

PR G-preflighten är också plattformsrättad. Linux-v1 bevaras; Darwin-v2 hittar
de paketerade SUMO 1.27.1-binärerna och `lib/libsumocpp.dylib`, men ingen
Python/SWIG-bindning. Det öppnar ingen grind och ändrar ingen workerbudget.

## 14. Slutlig lokal implementation och mätning 2026-08-11

### Resursbudget, riktig paus och riktig resume

Budgetkontraktet är nu `daily_unit_budget_v3` och skiljer på två storheter:
`maximum_daily_units` är NYA enheter per invocation och återställs vid resume;
`maximum_total_daily_units` är ett kumulativt hårt skydd och återställs aldrig.
100 000-parentgränsen är fortsatt fatal. RSS- och ledgerfält som tidigare
deklarerades men inte mättes i produktvägen har tagits bort ur runtimekontraktet
i stället för att ge en falsk garanti.

En parent är transaktionen. En budgetkorsande parent skriver inga unit-ID:n,
ingen envelopeklassificering och ingen eligibility innan nästa invocation.
Checkpointen binder exakt search key, budget key, unitprefix, parentprefix och
cursor. Paus publiceras som `monthly_screening_checkpoint`, utan shortlist, och
`run_monthly_search` returnerar före backend, SUMO, pilot och finalister.

Resume är differentialtestad över flera 300-enhetersinvocations: 754 parents
och 910 unika enheter reproducerar den oavbrutna payloaden byte-för-byte.
API:t använder en versionsbunden serverpolicy (30 000 nya enheter per
invocation, 100 000 totalt, 100 000 parents), rapporterar `paused`, frigör
simuleringslåset och återupptar samma workspace när exakt samma spec skickas
igen. Browsern återställer datum, källa, tidsband, arbetstid, dagtak,
veckodagar, periodläge och vägar efter reload.

Planens sexmånadersfall med 360 timmar är nu produktkörbart i preflight:
11 813 parents / 23 349 enheter och inte längre vägrat av den historiska
10 000-gränsen. Samma start- och sluttid används fortfarande varje vald
arbetsdag genom `exact_equal_daily_v1`; perioderna kan vara 8 dagar eller upp
till 90 arbetsdagar och behöver inte vara kalenderveckor.

### Ny golden och verkliga benchmarkutfall

`validation/closure_cost_ordering_golden_v2.json` band då aktuell
`monthly_sumo.py`; den processfria providern, runner-vägen, sorteringen och den
namngivna golden-replayen reproducerar byte-för-byte. v1 är orörd historik.

V3-benchmarken reproducerade ett riktigt durabilityfel: wrappern hashade
kalenderordnad ledger medan state machine hashade den faktiska
kostnadssorterade, no-detour-filtrerade ordningen. Första körningen lyckades,
men fault-injection-resume vägrade sin egen cursor. `bound_identity` använder
nu exakt scanordning medan full originalledger fortsatt binds separat av sin
content key; ett osorterat/no-detour-regressionstest låser felet.

V4 frystes efter fixen och fullföljde exhaustive, cost-ordered och
fault-injection/resume. Status, selected IDs, slutbeslut, hårdfel,
hälsoklassificering, stopproof, cachebokföring och resursgränser var identiska.
Grinden UNDERKÄNDES ändå korrekt:

- båda armarna verifierade 13/13 och slutade `no_viable`; besparing 0;
- exhaustive timeoutposter saknade post-SUMO disruptionfält, så full
  fältegenskap mot cost-ledgern kunde inte bevisas.

Policy v3 aktiveras därför inte och held-out körs inte. Ett annat fall väljs
inte i efterhand för att få ett positivt utfall.

### Återstående evidensgränser är avgjorda, inte dolda

`validation/independent_vs_continuous_outcome_v3.json` körde samtliga 84
förregistrerade fall mot den verkliga Mac-arkivroten: 35
`blocked_missing_demand`, 25 `unpairable`, 24 `unsupported_by_contract`, 0
`measured`. De frysta datumen har alltså inga exakt matchande demandenvelopes;
inga syntetiska ersättningar skapades.

`validation/libsumo_preflight_v3.json` bekräftar SUMO 1.27.1, headers och
`libsumocpp.dylib`, men ingen Python-bindning. Ingen installation gjordes och
TraCI-backenden behålls. PR I:s förbud mot ny extern data står kvar. Eftersom
v4 inte gav en hälso-viabel, diskriminerande finalistmängd finns ingen legitim
held-out- eller microkampanj att köra; båda förblir fail-closed i stället för
att fyllas med fabricerad evidens.

## 15. Eftergranskning av budget och exekveringsfönster 2026-08-13

De rapporterade pause/resume-felen 1–5 och 12 beskriver den äldre
`17cc0e6`-vägen. Den aktuella vägen har en separat checkpointtyp utan shortlist,
returnerar före backend/SUMO/resultat och återupptar med en nollställd
per-invocation-räknare. Felaktig cursor, ändrat prefix och en budgetkorsande
halvparent vägras innan något partiellt resultat kan publiceras.

De kvarvarande mindre kontraktsfelen är stängda: `describe()` klarar okända
stop-markörer, oanvända RSS/ledgerfält är borttagna, parenttaket får inte skilja
mellan CLI och budget, `--daily-unit-budget` vägras i screeninglägen som inte
kan använda det och första parent som är större än en sida ger ett explicit
fel i stället för en null-cursor. Golden v4 binder den aktuella
cost-order-källan; den gamla v1-filen skrivs inte om.

Warm/cold-fyndet var däremot nytt och giltigt. V16 jämförde mot fullarkivets
cold-arm, medan `adf765b` senare kortade independent-day-cold till exakt
envelope. Produktvägen tillåter därför endast warm när samma candidates valda
cold-fönster fortfarande är fullfönstret som v16 täckte. Vid skillnad körs
trimmed cold och orsaken `warm_cold_window_equivalence_unproven` bokförs. En
framtida trimmed-warm-väg kräver en ny parad ekvivalenskampanj.
Golden v4 binder denna nya källa mot samma existerande observationer utan en
ny SUMO-körning; v1-v3 är kvar som oförändrad historik.
