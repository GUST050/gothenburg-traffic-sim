# Warming speed research — 2026-08-08

## Slutsats

Uppvärmningen kan göras betydligt snabbare i en framtida körning. För den
nuvarande banken är den bästa åtgärden däremot att fortsätta oförändrat.

Statusen vid granskningen var:

- 74 094 av 104 685 tillstånd klara, 70,8 %;
- 0 misslyckade;
- 30 588 väntande;
- 3 markerade som `running` efter den pausade körningen; och
- planens content key var fortfarande
  `adf91205bfcafc0cebbb18613e064e49fa9d3321758638c418e36d41552b30b2`.

De två huvudsakliga produktionsloggarna omfattade cirka 34,42 aktiva timmar
och 74 049 publicerade artefakter. Det motsvarar:

- cirka 1,6735 sekunder per publicerad enhet räknat över hela pipelinen;
- cirka 482 sekunder per grupp med 288 state requests;
- cirka 48,7 aktiva timmar för hela banken; och
- cirka 14,2 aktiva timmar för de 30 588 enheter som återstod.

Den tidigare baslinjen var 59,5 timmar. Den nuvarande demand-prefetchen och
överlappningen har alltså gett ungefär 18 % förbättring i den riktiga
årskörningen. Avbrott och tiden då datorn eller processen är pausad ingår inte
i dessa aktiva tider.

## Vad som redan har testats

| Försök | Uppmätt resultat | Slutsats |
| --- | ---: | --- |
| Överlappa nästa demand-build med nuvarande SUMO-kedjor | cirka 18 % förbättring i årskörningen | Fungerar och används |
| 96 kedjade checkpoints | V1–V3 underkändes; V4 klarade alla 96 och beteenderelevanta jämförelser exakt | Nuvarande säkra metod |
| 96 snapshots i en SUMO-process | 5,04 s mot 104,6 s kedjat, cirka 20,8 gånger snabbare | Inte användbar med nuvarande evidenskontrakt |
| 8 parallella SUMO-processer | 164,53 s till 97,49 s, 1,69 gånger snabbare; identiska resultat; 2,11 GiB peak RSS | Bra generellt men hjälper inte den nuvarande schedulern |
| Demand day library | Implementerad; fullskalig golden A/B blev byte-identisk | Stor förbättring, men viss kalenderredundans återstår |
| LZMA, deduplicering och selective restore | Implementerat; selective restore var 25,49 gånger snabbare | Lagringen är inte längre huvudproblemet |
| Libsumo | Inte testat på produktionsvägen; Python-bindningen saknas på värden | Möjligt framtidsspår |

De viktigaste lokala bevisen är:

- [WARMING_PLAN_2026-08-05.md](WARMING_PLAN_2026-08-05.md)
- [PRE_WARMING_REVIEW_2026-08-04.md](../reviews/PRE_WARMING_REVIEW_2026-08-04.md)
- [WARMING_FINAL_AUDIT_2026-08-03.md](../reviews/WARMING_FINAL_AUDIT_2026-08-03.md)
- [annual_warm_chain_pilot_v4.json](../../validation/annual_warm_chain_pilot_v4.json)
- [a2_parallel_seed_benchmark_v1.json](../../validation/a2_parallel_seed_benchmark_v1.json)

### Kedjepiloterna V1–V4

V1 återställde alla 96 länkar men state-filerna växte kraftigt jämfört med
oberoende cold states. Vid länk 96 var den expanderade filen ungefär 33,9
gånger större än cold-jämförelsen.

V2 och V3 stoppade state-tillväxten men hade fortfarande skillnader i delar av
prefixevidensen. V4 separerade beteenderelevant evidens från bokföringsfält
som legitimt kan skilja sig, och klarade:

- alla 96 restore-länkar;
- begränsad state-storlek;
- exakta beteenderelevanta jämförelser vid länkarna 2, 48 och 96; och
- exakt aktiv meso-ackumulator och completed-trip-resultat där kontraktet
  kräver det.

Det är denna metod den nuvarande banken bygger på.

### En process med många snapshots

SUMO stöder officiellt flera sparpunkter i samma körning genom
`--save-state.times` och `--save-state.files`. Den lokala piloten mätte:

- 5,04 sekunder för en heldagskörning som sparade 96 states;
- 5,06 sekunder för motsvarande körning utan states; och
- 104,6 sekunder för att skapa motsvarande kedja med en process per länk.

Det innebär ungefär 95 gånger färre processstarter och cirka 20,8 gånger
kortare state-simulering. Metoden antogs ändå inte. Orsaken är att den exakta
prefixevidensen hämtar oavslutade fordons `tripinfo` när SUMO-processen
avslutas. SUMO:s mesomodell använder den privata
`MSDevice_Tripinfo::myMesoTimeLoss`-ackumulatorn, men den ingår inte i den
nuvarande state-serialiseringen. TraCI `vehicle.getTimeLoss` returnerar inte
exakt samma mesovärde.

En kontinuerlig process kan därför skriva rätt dynamiska states, men kan inte
producera det exakta checkpoint-underlag som det nuvarande kontraktet kräver.
Att använda den ändå skulle försvaga exactness-garantin.

SUMO:s officiella SaveAndLoad-dokumentation bekräftar dessutom att framtida
avgångar inte lagras i state-filen, att route input fortfarande behövs och att
RNG-state inte sparas som standard:

<https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html>

### Varför åtta state-workers inte hjälper nu

Varje demand-arkiv innehåller tre oberoende kedjor:

- q10;
- q50; och
- q90.

Nästa checkpoint i en kedja får inte starta innan dess exakta föregångare är
permanent publicerad. Därför finns bara tre dependency-ready enheter samtidigt
inom ett demand-arkiv. Om `--state-workers` höjs från 3 till 8 får fem workers
inget arbete.

För att använda sex eller åtta workers måste schedulern bearbeta två eller tre
demand-arkiv samtidigt. Det har inte implementerats eller verifierats för
annual population.

Det tidigare parallelltestet visade att åtta fristående SUMO-processer gav
1,69 gånger förbättring och identiska resultat. Det bevisar att separata
simuleringar kan köras säkert parallellt på värden, men inte att den nuvarande
annual-schedulern kan mata åtta workers.

SUMO:s officiella FAQ anger att kärnsimuleringen huvudsakligen kör på en kärna.
Flera oberoende SUMO-processer är därför den relevanta parallellformen;
`--threads` ger ännu ingen meningsfull generell förbättring:

<https://sumo.dlr.de/docs/FAQ.html#can-sumo-be-run-in-parallel-on-multiple-cores-or-computers>

## Rekommendation för den nuvarande banken

1. Fortsätt med exakt samma plan och `--state-workers 3`.
2. Starta om `tools/resume_warming.sh`. Den återupptar efter SUMO-krascher och
   använder `caffeinate` för att hindra datorn från att somna.
3. Undvik andra tunga PFE-, LOSO- och SUMO-körningar medan warming pågår.
4. Ändra inte bound source-filer, SUMO-version, snapshot-precision,
   RNG-inställningar, lagringsformat eller checkpoint-kontrakt innan banken är
   färdig.
5. Kontrollera efter återstart att de tre orphaned `running`-artefakterna
   återvinns och att `succeeded` fortsätter öka utan fel.

En bound source- eller runtime-förändring ger en ny planidentitet. De 74 094
redan färdiga enheterna raderas inte, men de är inte valbara som den nya
bankens evidens. Utveckling och verifiering av en ny metod skulle sannolikt ta
längre tid än de cirka 14,2 aktiva timmar som återstår.

## Prioriterad plan för nästa warming-generation

### P0 — Benchmarka resursfördelningen

Den nuvarande demand-byggaren använder normalt tio PFE-workers samtidigt som
tre SUMO-processer kör state-arbete. Det betyder upp till tretton CPU-tunga
processer på en tiokärnig värd.

I den senaste produktionsloggen tog `pfe_variants_and_rounding` i genomsnitt
369,5 sekunder över 122 demand-builds, med intervallet 214,7–696,4 sekunder.
Demand-fasen är därmed den dominerande begränsningen under stora delar av
körningen.

Kör en isolerad, parad benchmark på samma sju demand-grupper med:

| Arm | PFE-workers | State-workers |
| --- | ---: | ---: |
| Referens | 10 | 3 |
| Kandidat A | 7 | 3 |
| Kandidat B | 5 | 3 |

För varje arm ska följande registreras:

- total wall time;
- sekunder per demand-grupp;
- sekunder per state-enhet;
- PFE-fasernas tider;
- peak RSS och minsta fria minne;
- SUMO-startfel och retries; och
- hashjämförelse av demand-, state- och evidensartefakter.

Adoptera endast en kandidat om artefakterna är identiska, inga nya fel uppstår
och total genomströmning förbättras tydligt.

### P1 — Mata sex state-workers med två demand-grupper

Ändra schedulern så att två färdigbyggda demand-arkiv kan ha state-enheter i
gång samtidigt. Det skapar sex oberoende q-kedjor.

Benchmarka minst:

| Arm | Samtidiga demand-grupper | State-workers | PFE-workers |
| --- | ---: | ---: | ---: |
| Referens | 1 | 3 | 10 |
| Kandidat A | 2 | 6 | 4 |
| Kandidat B | 2 | 6 | 7 |
| Kandidat C | 2 | 6 | demand-build och state körs i separata faser |

Kontrollera dependency-ordning, exakt predecessor provenance, atomisk
publicering, peak RSS, disk, orphan recovery och identiska slutartefakter.
Det tidigare åtta-worker-testet är positiv bakgrund men inte tillräcklig
release-evidens för denna scheduler.

### P2 — Minska day-library-redundansen

Produktionsloggarna visar återkommande `0/3 day(s) reused` när ett
tre-dagarsfönsters pool composition växlar mellan `weekday` och
`weekday+weekend`.

Under en typisk vecka byggs ungefär elva nya dag-identiteter i stället för sju:

- en ny composition-grupp börjar med tre kalibreringar; och
- efterföljande fönster i samma grupp återanvänder två dagar och bygger en.

En verkligt dagsspecifik pool composition skulle kunna reducera antalet
dagskalibreringar med ungefär 36 %. Det är dock en modelländring, inte bara en
cacheoptimering. En veckodag som ligger i ett blandat fönster ser i dag unionen
av weekday- och weekend-geometrier för att bevara den äldre fönstersemantiken.

Innan en ändring får antas krävs:

- golden A/B på samma demand-fönster;
- sensor- och GEH-kontroller;
- corrected LOSO och DMRB;
- candidate-support och strukturgränser;
- q10/q50/q90-provenance; och
- verifiering att förändringen förbättrar modellen eller är semantiskt neutral,
  inte bara snabbare.

### P3 — Gör SUMO-state komplett för meso-tripinfo

Detta är den största möjliga framtida förbättringen.

Utred en frusen egen SUMO-build där
`MSDevice_Tripinfo::myMesoTimeLoss` serialiseras i `saveState/loadState`, eller
exponeras genom en API-funktion som bevisligen returnerar exakt samma värde som
meso-tripinfo använder.

Om den privata ackumulatorn transporteras exakt kan en process:

1. simulera en kedja kontinuerligt;
2. spara alla 96 states;
3. skapa checkpoint-evidens utan att avsluta efter varje kvart; och
4. återuppta varje state med hela aktiva fordons ackumulator bevarad.

Den lokala piloten antyder cirka 20,8 gånger snabbare state-simulering. Om
demand-generationen därefter dominerar kan hela årskörningen preliminärt hamna
i storleksordningen 12–17 timmar i stället för cirka 49. Detta är en hypotes,
inte en tidsutfästelse.

En sådan runtime måste få en ny identitet och passera:

- enhetstester av state-version och saknade/malformed fält;
- save/load-jämförelse för aktiva fordon;
- full 96-länks kedjepilot;
- oberoende cold-jämförelser vid tidiga, mellersta och sena checkpoints;
- bit-exakt objective- och tripinfo-rekonstruktion;
- q10/q50/q90 och flera seeds;
- crash/orphan/resume-test; och
- fullskalig golden A/B innan en ny bank byggs.

### P4 — Minska mängden sparad RNG-state

SUMO anger att `--save-state.rng` normalt tillför omkring 500 KB och att
`--thread-rngs` kan minskas, men inte under antalet routing- eller
simulationstrådar.

Den nuvarande simulationen är single-threaded. Benchmarka därför
`--thread-rngs 1`, `3` och standardvärdet `64` med oförändrad seed,
state-precision och demand.

Mät:

- state-storlek före och efter komprimering;
- save/load-tid;
- total state-enhetstid;
- RNG- och trajectory-exakthet; och
- hela 96-länkskedjans resultat.

Färre RNG-strömmar får bara antas om alla resultat och återupptagna
simuleringar är exakta.

### P5 — Profilera återstående per-enhetskostnad

Tidigare mätning visade cirka 2,65 sekunder per individuell state-enhet mot en
SUMO-nedre gräns omkring 1,09 sekunder. Ungefär 1,5 sekunder låg alltså utanför
själva 900-sekunderssimuleringen.

Profilera separat:

- SUMO-processstart och nät-/route-laddning;
- TraCI connect;
- de 60-sekunders `simulationStep`-chunkarna;
- `saveState`;
- avslut och unfinished-tripinfo;
- prefixevidensbygge;
- gzip/LZMA; och
- store publish, hashning och validering.

Testa därefter isolerat:

1. en enda `simulationStep(target)` i stället för 60-sekunderschunkar, med den
   befintliga wall-clock-watchdogen kvar;
2. direkt codecval för kända redan-komprimerade state-filer, om profileringen
   visar mätbar kompressionskostnad;
3. grouped chain storage för kumulativ prefixevidens; och
4. libsumo endast om socket- och processkostnaden faktiskt dominerar.

SUMO rekommenderar libsumo när TraCI:s socketkommunikation är ett problem:

- <https://sumo.dlr.de/docs/Libsumo.html>
- <https://sumo.dlr.de/userdoc/TraCI/#performance>

Libsumo löser dock inte automatiskt kravet att avsluta processen för exakt
unfinished-tripinfo. Det bör därför prioriteras efter meso-ackumulatorn och
resursfördelningen.

## Förbjudna genvägar

Följande får inte användas för att få en snabbare grön körning:

- minska antalet checkpoints utan ett uttryckligt produktkontrakt;
- sänka state-precision från 16 utan full exactness-evidens;
- stänga av RNG-state;
- återanvända states mellan olika datum, seeds eller varianter;
- acceptera tolerans där nuvarande kontrakt kräver exakt resultat;
- behandla en diagnostic replay som release-evidens;
- hoppa över prefixevidens eller unfinished-tripinfo; eller
- höja worker-antalet utan minnes-, fel- och resultatjämförelse.

## Rekommenderad ordning

1. Slutför den nuvarande banken oförändrad.
2. Auditera bankens fullständighet, artefakter och användbarhet.
3. Kör P0-resursbenchmarken.
4. Prototypa och benchmarka P1 med två demand-grupper.
5. Avgör P2 tillsammans med pool-, picker-, LOSO- och sensorarbetet.
6. Utred P3 som en separat, versionsbunden SUMO-runtime.
7. Testa P4 och P5 endast med parade, exactness-kontrollerade armar.
8. Skapa en ny annual plan och bank först när en förbättring är uppmätt,
   verifierad och värd kostnaden.

## Forskningskällor

- SUMO SaveAndLoad:
  <https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html>
- SUMO FAQ om parallellisering och prestanda:
  <https://sumo.dlr.de/docs/FAQ.html>
- SUMO Libsumo:
  <https://sumo.dlr.de/docs/Libsumo.html>
- SUMO TraCI performance:
  <https://sumo.dlr.de/userdoc/TraCI/#performance>
- SUMO mesoscopic simulation:
  <https://sumo.dlr.de/docs/Simulation/Meso.html>
- SUMO simulation state commands:
  <https://sumo.dlr.de/docs/TraCI/Change_Simulation_State.html>

## Granskningsgräns

Detta dokument bygger på read-only inspektion av koden, planerna,
valideringsartefakterna, SQLite-statusen och produktionsloggarna samt officiell
SUMO-dokumentation. Ingen kod, plan, warming-bank, körstatus eller evidensfil
ändrades under själva undersökningen.

## Pilotutfall 2026-08-09 — aktuell produktionsidentitet

Efter pool/picker- och strukturfixarna skapades en ny innehållsbunden plan:
`9b640a0c76e128207941330e9f72dde989ce838a4fe7aa9dad5c469528812720`.
Den omfattar 367 demand-byggen och 104 685 state-enheter. Preflight
`916e0f8460e6bbc076a026d987c078a07c6ecea05b52912f21fb04258be8fe90`
godkände åtta state-workers med 126,7 GB ledigt utrymme.

En avgränsad q10-pilot kördes för demand-nyckeln `a1ea9baf7002a4b1` med en
worker och högst en enhet per anrop. Första länken vid 3 600 sekunder skapades
och validerades. Samma kommando kördes därefter igen: demand-arkivet återanvändes
och länken vid 4 500 sekunder byggdes som `extend_predecessor` från den första.
Båda enheterna lyckades, inga misslyckades och 104 683 återstår. Detta bevisar
bounded execution, cache/resume och föregångarkedjans förlängning utan att
starta den fulla populationen.

Tvådagarsbygget tog ungefär 290 sekunder i PFE-varianter och heltalsavrundning.
Den andra state-enheten tog ungefär 53 sekunder inklusive preflight och en
kort localhost-retry. Artefaktlagret för de två länkarna är litet; den
40,4 MB stora q10-routefilen lagras som cirka 416 KB xz. Detta stödjer den
tidigare forskningsslutsatsen att demand-bygge och process-/TraCI-start är mer
relevanta pilotkostnader än lagring för de första länkarna.

Piloten hittade en beteendevarning i q50: fritid hade median 2,86 km mot arbete
2,91 km. Kandidatpoolen och q10/q90 behåller avsedd ordning, alla sensor- och
kortresestrukturgrindar passerar, och skillnaden är 0,05 km. Ingen ny
längdconstraint eller testanpassad vikt infördes utan oberoende reslängdsdata.
Varningen ska följas över fler representativa demand-byggen, men klassas inte
som ett fel i sensorankring, poolintegritet eller state-kedjan.

`audit_annual_warm_chain.py --positions 1,2` kan inte användas på en partiell
kedja: verktyget kräver även länk 3 och avslutade med ett tydligt fel utan att
ändra enhetsstatus. Kedjeauditen ska därför köras när den valda kedjan är
komplett, inte genom att värma resten enbart för auditens skull.

## Aktuell omprövning och rensning 2026-08-09

Efter de source-bundna pool/picker-förbättringarna är den aktuella planen
`38d91d22c305a0c32c9d57b622f59c37d31c20362b8bc32836742ac90e52bdf0`.
Ingen enhet från tidigare planer är valbar under denna identitet. Sjutton
superseded annual-rötter och pilotbankerna raderades permanent; sammanlagt
19 171 217 408 allokerade byte frigjordes. JSON-evidens och loggar behölls,
medan den aktuella roten initierades tom med 104 685 pending och noll attempts.
Rensningen är registrerad i `validation/annual_warm_cleanup_20260809.json`.

Den aktuella säkra produktionsinställningen är tre state-workers med demand-
prefetch aktiverad. Värden har verifierats för åtta isolerade SUMO-processer,
men en demand-grupp har exakt tre oberoende seed/variant-kedjor. Åtta workers
kan därför inte höja genomströmningen i nuvarande scheduler; fem blir utan
dependency-ready arbete. Preflighten spelades om med tre workers så att den
operationella posten beskriver den faktiska körformen.

Den uppmätta prefetchvinsten på cirka 18 procent är fortsatt den snabbaste
verifierade ändringen för denna kodväg. Att mata sex workers kräver två aktiva
demand-grupper och en parad benchmark av CPU-/minnesfördelning och byte-identiska
artefakter. Den ändringen antogs inte utan mätning: demand-byggaren använder
normalt alla kärnor, så sex samtidiga SUMO-processer kan annars göra hela
pipelinen långsammare trots att state-fasen isolerat blir snabbare. Ingen
exactness-, checkpoint-, RNG- eller proveniensgrind sänktes för hastighet.
