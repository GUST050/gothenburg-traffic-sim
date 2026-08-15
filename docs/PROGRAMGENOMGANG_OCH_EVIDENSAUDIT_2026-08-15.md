# Programgenomgång och evidensaudit

**Datum:** 2026-08-15
**Omfattning:** det nuvarande programmet från rå sensordata till ett
avstängningsbeslut. Dokumentet beskriver nuläget och pekar ut nästa förbättring;
det ersätter inte `ARCHITECTURE.md` eller `IMPROVEMENT_PLAN.md`.

## Kort dom

Programmet är starkast i sina kontrakt: mätvärden skyddas, härledning och
priorer hålls isär, artefakter binds med hashvärden och många fel ger stopp i
stället för en tyst fallback. Det är också starkt på att reproducera de
sensorer som används i kalibreringen.

Den stora begränsningen är information, inte optimeringskod. Sex stationer på
sju riktade kanter ska informera ett nät med 7 125 kanter. Den identitetsbundna
06–10-körningen ger spatiala held-out-kvoter 0,466–1,354 (median 0,613), och
den oberoende dagen ger 0,445–1,356 (median 0,623). Alla sex stationer är
dessutom klassade som underidentifierade i båda körningarna.

Revisionen hittade också ett konkret lokalt bevisfel:
`web/data/validation.json` binder aktuell demand-meta
`4afe9e3ae2e74a4b872e`, men dess simulation- och sensor-outputsektioner läser
en äldre `baseline.json` med build-id `fa259a2892a974c27e8c`. Webbgränssnittet
kunde varna när en aktiv studies build-id skiljde sig, men själva rapporten
sa fortfarande `overall=pass` och blandade därmed två byggen internt.

**Rättat och verifierat 2026-08-15:** rapporten har nu en egen
`scenario_identity`-grind.
Vid mismatch blir totalsvaret `warn` och gammal simulation/sensor-output märks
`missing` med den exakta identitetskonflikten. En ny matchad treseedsbaslinje
har därefter byggts, staging-validerats och publicerats för build
`4afe9e3ae2e74a4b872e`: alla 14 994 fordonsinstanser sattes in, inga blev
ofärdiga eller teleporterade och slutlig sensor-output fick 100 % GEH<5.
`validation.json` är därför åter `overall=pass` utan varningar eller saknade
sektioner; detta är fortfarande diagnostik, inte en releasepromovering.

## Så ska bevisen läsas

| Klass | Betydelse | Vad den får stödja |
|---|---|---|
| **M — mätning/bundet kontrakt** | Direkt mätvärde eller maskinverifierad identitet/hash | Starka påståenden om exakt den bundna artefakten |
| **H — held-out/verklig körning** | Data eller fall hölls undan, eller SUMO kördes på riktigt | Generalisering inom den testade populationen |
| **D — diagnostik** | Reproducerbar mätning utan releaseauktoritet | Felsökning, prioritering och negativa resultat |
| **A — antagande/proxy** | Saknar lokal observation eller oberoende validering | Ska visas som osäkerhet, aldrig som mätning |

Ett grönt internt fitvärde och ett bra held-out-resultat är olika saker. Det
första visar att lösaren följde sina indata; det andra visar om indata och
modell kan återskapa något den inte fick se.

## Programflödet

```text
råa sensorfiler + sensorregister
              ↓
       A. intake och OSM-nät
              ↓
    B. observabilitet och gränser
              ↓
  riktningsmodell + prognos + OD-priorer
              ↓
       C. kandidatrutter och PFE
              ↓
      kalibrerade SUMO-routefiler
              ↓
 E. normal-/avstängningssimulering och sökning
              ↓
       F. confidence och validering
              ↓
          webb/API och release
```

## Steg 1 — kontrakt och byggidentitet

Varje större studie börjar med kontrakt i `traffic_sim/core/contracts.py` och
innehållshashar i `traffic_sim/core/fingerprint.py`. En demand-build beskriver
datum, historisk/prognostiserad källa, antal dagar, tidsfönster, seed och
solverinställningar. `build_sumo_demand.py` skriver `sumo/demand_meta.json`
först när kalibreringen lyckats.

Detta är viktigt eftersom filnamn inte bevisar innehåll. Två filer som båda
heter `calibrated.rou.xml` kan komma från olika datum, modeller eller nät.

**Bra bevis:**

- Demand-buildens input- och outputfiler finns i ett content-fingerprint.
- Gate S v6 verifierade routehash, demand lineage, seed och fysisk q-variant
  för 48 verkliga körningar.
- Historiska beslut skrivs inte över; nya beslut får en ny versionsfil.

**Rättat kontraktsfel och körd kontroll:** valideringssammanställningen kontrollerar nu att
`baseline.json` har samma build-id innan den läser simulation och
sensor-output. Mismatchfallet ger `overall=warn`; den nu publicerade matchade
baslinjen passerar samma kontroll med exakt build-id.

## Steg 2 — sensordata och sensorregister

`build_data.py` läser 15-minutersfiler, koordinater och
`data_in/sensors.json`. Registret bestämmer vilken riktad kant som verkligen är
mätt, giltighetsperiod, riktning och granskad snap. Sensorn matchas mot OSM med
riktning och verkligt avstånd, inte bara närmaste linje.

Utdata är bland annat:

- `web/data/flows.json`: mätserier;
- `web/data/network.geojson`: kartnät och confidence;
- `web/data/graph.graphml`: källgraf för SUMO-bygget.

**Bra bevis (M):** registervalideringen stoppar okänd sensor, saknad riktning,
fel kant eller för långt snap. Graf, flöden och GeoJSON publiceras
transaktionellt från samma körning.

**Begränsning (A):** bara sex stationer i två geografiska kluster. De mäter
sju av 7 125 riktade kanter. En korrekt sensor kan därför vara helt korrekt
utan att göra resten av staden identifierbar.

## Steg 3 — vägnätet

`build_sumo_net.py` konverterar den frysta GraphML-grafen till
`sumo/net.net.xml` med stabila kant-ID:n. `sumo/network_audit.json` jämför OSM
och SUMO och sparar källa för längd, hastighet, körfält, enkelriktning,
restriktioner, rondeller och trafikljus.

**Bra bevis (M):** samtliga 7 125 kanter har matchande längdkontrakt och nätet
är hashbundet till scenarierna.

**Begränsning (A):** 631 kanter, 8,9 %, använder standardhastighet och 4 990
kanter, 70,0 %, använder standardantal körfält. Alla 190 signalstyrda kanter
har syntetiska, gissade signalprogram. Kapacitet och restid kan därför vara
fel även när efterfrågan är perfekt kalibrerad vid sensorerna.

## Steg 4 — matematisk observabilitet

`observability.py` använder flödesbevarande i korsningar:

1. Om alla utom ett ben är kända kan den sista kanten härledas exakt.
2. Annars beräknas min/max-intervall med icke-negativitet och kapacitet.
3. Sensorer på samma korridor ger residualer och konsistenslarm.

Resultaten finns i `web/data/observability.json` och
`web/data/observability_bounds.json`.

**Bra bevis (M):** en exakt härledd kant är algebra, inte maskininlärning.
Mätningar ligger över gränser och priorer i PFE:s relaxeringsstege.

**Begränsning:** ett brett intervall är ett ärligt resultat men innehåller
lite information. Observabilitetsdiagnostiken visar att samtliga sex
stationer fortfarande är underidentifierade när de hålls undan.

## Steg 5 — riktningssplitten

Pipeline i `dirsplit/` är:

1. `dataset.py` skapar en datum×station×timme-tabell från norska riktade
   volymfiler och en separat aggregerad diagnostiktabell.
2. `train.py` tränar lokalt likhetsviktade LightGBM-kvantiler per
   Göteborgssensor och krymper avvikelsen mot 50/50.
3. `evaluate.py` kör blockerade datum-, leave-city- och leave-station-folds.
4. `predict.py` producerar komplementära q10/q50/q90-par.
5. `prior_flows.py` räknar om den uppmätta körbanans andel till en mjuk prior
   för den omätta motriktningen.

Aktiv regel:

- lokal publicerad riktning gäller först;
- tränad q50 används vardagar 06–20;
- 50/50 används utanför träningsstödet eller när modell saknas;
- q10/q90 används bara vid explicit stressbygge;
- omätt motriktning får mjuk prior och tak, aldrig ett positivt minimiflöde.

**Bra bevis (H):** 247 464 datumrader, 232 500 användbara rader och 188 bundna
källfiler. Produktträningen använder 23 472 stödda rader från 83 stationer.
Gate M v5 väljer `similarity_weighted_lgbm_no_profile`; pooled domain-MAE är
0,0604 mot 0,0617 för 50/50.

**Bra negativt bevis (D/H):** Gate S v6 gav `NO` på 48/48 användbara körningar
med noll hårda fel. Viable set, ranking, vinnare och beslutskostnader var
identiska i q10/q50/q90. Överförd q50 flyttar bara 0,104 % av all uppmätt
massa mot 50/50; aktiv policy inklusive lokalt ankare flyttar 0,604 % och
modellens tidsform ensam 0,101 %.

**Dom:** dirsplit är tillräckligt säkrat för sin lilla roll. Mer modellarbete
här har lägre förväntad nytta än bättre lokala observationer.

## Steg 6 — normalprofil och 2027-prognos

`build_features.py` bygger tidsfeatures och normalprofil.
`train_agent1.py` tränar LightGBM per sensor. `build_agent1_flows.py` flyttar
kalendern till 2027 och lägger till särskilda helgdagsfaktorer.

**Bra bevis (H):** dokumenterad korsvalidering på 2025-data förbättrar MAE
12–29 % mot seasonal-naive på samtliga sex sensorer under vanliga dagar.

**Begränsning (A):** varje helgdag finns bara en gång i ett enda år och kan
inte korsvalideras normalt. En 2027-prognos kan inte verifieras mot verkligt
2027-utfall ännu. Strukturella gränser och priorer kommer dessutom från
referensdagen 2025-09-16.

## Steg 7 — kandidatrutter och OD-priorer

`build_candidates.py` skapar möjliga resor innan någon volym tilldelas. Den
använder:

- DeSO-befolkning och bostadsbyggnader för hem;
- POI/arbetsplatsproxy för aktiviteter;
- infartsportar för resor in, ut och genom området;
- RVU-baserade ärenden och längdpriorer;
- lagliga SUMO-anslutningar och ruttintegritetsfilter.

`assignment_priors.py` skapar en svag gravity/Dial-liknande tilldelning för
kanter som mätningarna inte bestämmer.

**Bra bevis (M/D):** aktuell pool har 4 695 beteendekandidater och redovisar
varje borttappad turpartner. Kandidatprovenance och agentsidecars följer med.
Den aktuella publicerade strukturen har inga structure flags, 3,4 % mål nära
sensor mot 1,9 % all-edge-baslinje och median 2 870 m kvar efter sista sensor.

**Begränsning (A):** OD, POI-attraktion, ärendeval och genomfartsandel är
priorer. Interna sensorer kan passa perfekt vid flera olika OD-matriser.
Genomfartsnivån 0,25 är vald med held-out-stöd men är inte en lokal
cordonmätning.

## Steg 8 — PFE-kalibrering

`build_sumo_demand.py` sammanför mål, gränser, priorer och kandidatrutter.
`traffic_sim/demand/pfe.py` väljer hur många fordon som ska använda varje
rutt och kvart.

Prioritetsordningen är exekverbar:

1. behåll mätbandet;
2. släpp matematiska tak om de kolliderar;
3. släpp ärendekvoter;
4. släpp mjuka priorer;
5. prova komplett LP;
6. vidga mätbandet först därefter.

Den aktuella 06–10-byggnaden har 4 998 fordon i varje stressarm, 100 % GEH<5
och noll olösliga intervall. q50 och q90 var rena i alla 16 kvartal. q10
släppte ett strukturellt tak men vidgade inte mätbandet.

**Bra bevis (M):** detta visar att den publicerade routefamiljen följer de
sensorer och den relaxeringshierarki som byggdes in.

**Viktig gräns:** 100 % GEH på använda sensorer bevisar inte flödet på en
omätt gata. PFE kan lösa ett underidentifierat problem exakt.

## Steg 9 — SUMO-simulering

`run_scenario.py` skapar en normalarm eller en avstängningsarm, kör SUMO meso
per seed, samlar edgeData/tripinfo och kontrollerar:

- att rätt routevariant och seed verkligen kördes;
- insatta, väntande och ofullbordade fordon;
- teleporter och kollisioner;
- att stängd kant inte läcker trafik;
- om en fysisk omledning finns;
- skillnaden mot en matchad baslinje.

Avstängningar använder `--time-to-teleport -1`. Fordon utan omledning
trunkeras vid sista nåbara kant i stället för att teleporteras genom
avstängningen.

**Bra bevis (H):** Gate S:s 48 nya körningar var health-clean,
closure-integrity-clean och seed/variant-verifierade. Warm-state v16 har tre
aktiverade, hashbundna identiteter och en cold escape hatch.

**Begränsningar (A):** grundscenariot har ingen generell congestion-driven
en-route-rerouting. Avstängningsradien 400 m och truncate-stranded-regeln är
antaganden utan lokala incidentdata. Meso använder dessutom de osäkra
körfälts-, hastighets- och signaluppgifterna från nätet.

## Steg 10 — avstängningsbeslut och sökning

`traffic_sim/simulation/closure_ranking.py` rangordnar kandidater efter
verkliga produktfält: extra fordonstimmar, extra meter, berörda fordon och
fordon utan omledning. Hårda integritets- och hälsoproblem diskvalificerar ett
fall före ranking.

För längre kalendrar används preflight, survivability-filter, streaming
ledgers, warm-state och cost-ordered verification för att undvika onödiga
SUMO-körningar.

**Bra bevis:** cost-ordered benchmark v5 sparade totalt 18 SUMO-verifieringar
och hade inga exekveringsfel.

**Begränsning:** benchmarkens totala gate föll eftersom alla equivalence-gates
inte passerade. Policyn får därför inte göra ett global-best- eller UI-anspråk
från den mätningen. Årsuppvärmningen är `ready_for_full_population`, men bara
3 av 3 267 planerade state-requests finns i piloten; den är inte en färdig
årsprodukt.

## Steg 11 — confidence och validering

`validate_sim.py` kör spatial leave-one-station-out. Confidence på kartan är
en avståndsfunktion vars sigma härleds från LOSO, och scenarier lägger till
spridning mellan seeds.

**Bra bevis (H/D):** LOSO visar öppet när modellen missar i stället för att
dölja det. Spatial körning på referensfönstret ger 0,466–1,354 och median
0,613. Temporal körning på 2025-09-17 med samma frysta pool/nät/priorer ger
0,445–1,356 och median 0,623. Det stabila mönstret över två dagar är negativt
bevis för nätets omätta nivå/allokering, inte för dirsplitten.

**Begränsningar:** LOSO-rapporterna är karakterisering, inte releasegrind.
Samtliga stationer är fortfarande underidentifierade. Confidence är dessutom
huvudsakligen avståndsbaserad och kan ge högre värde på en parallell men
topologiskt frånkopplad gata.

## Steg 12 — webb, API och release

`serve.py` serverar den statiska Leaflet-appen och API för recalibration,
scenario, sökning och signalstudier. Nya scenarier kan byggas i staging och
ersätta den gamla uppsättningen efter validering. Golden releases under
`runs/releases/` binder scenario, trajectories och exakta routeinputs och har
atomisk aktivering/rollback.

**Bra bevis:** webben jämför aktivt scenarios `demand_build_id` med
valideringsrapportens id och kan märka rapporten som stale.

**Rättat efter revisionen:** `traffic_sim/confidence/report.py` gör nu samma
identitetskontroll internt innan `_simulation_section` och
`_sensor_output_section` får läsa `baseline.json`. Regressionstester täcker
både mismatch och en baseline som saknar identitet.

## Var nästa förbättring finns

### 1. Slutförd P0: valideringsrapport och baslinje är byggkoherenta

Felet reproducerades lokalt och rättades samma dag eftersom det kunde ge en
missvisande grön rapport.

Implementerad lösning:

1. Läs `baseline.scenario_spec.demand_build_id` eller
   `baseline.scenario.build_id`.
2. Jämför med `demand_meta.build_id`.
3. Vid mismatch ska `simulation` och `sensor_output` bli `missing/stale`, inte
   `pass`.
4. `overall` får inte bli `pass` om rapporten blandar identiteter.
5. Regressionstest täcker både mismatch och identitetslös baseline.

Hela kedjan är nu körd. Den matchade baslinjen passerade staginggrinden,
publicerades atomiskt och återställde grönt resultat först efter att simulation
och slutlig sensor-output passerade. Registrering och utfall finns i
`validation/matched_baseline_*_v1.json`.

### 2. Slutförd evidensförbättring: spatial och temporal held-out

Spatial och temporal LOSO är omkörda mot samma kandidatpool, nät,
through-share-target och 06–10-referens. Identitetskontrollen är fail-closed.
En temporal publicerings-MILP nådde först tidsgränsen; en valideringsspecifik,
algebraiskt komprimerad heltals-L1-formulering över hela ursprungsdomänen löste
den blockerande kvarten optimalt utan ändring av förseglad demand-kod. En
snabbare floor/ceil-diagnostik avvisades före slutevidens när ett överlappande
marginalmotexempel visade att den kunde missa globalt optimum. Resultat och negativ evidens
finns i `validation/current_heldout_outcome_v1.json`.

### 3. Uppskjuten extern hävstång: 3–5 gränssensorer

Nya sensorer är uttryckligen uppskjutna tills data anländer. När beslutet
öppnas igen är mätta flöden från Trafikverkets vägtrafikflödeskarta/Lastkajen/open API
vid E6, E20, Rv40 och Oscarsleden och lägg dem genom det befintliga
SensorRegistry-flödet. De ligger vid områdets portar och gör in-/utflöde och
genomfart delvis identifierbara för första gången.

Bevis för prioriteten:

- 7 mätta kanter av 7 125;
- alla sex LOSO-stationer är underidentifierade;
- held-out ratios 0,685–2,613;
- en sweep kunde ändra through share utan att förstöra sensorfit, vilket visar
  att interna sensorer inte identifierar nivån;
- mer kandidatdiversitet/seeds löste inte rankbristen.

Klart när varje ny station har granskad riktning/snap, egen provenance och en
före/efter-jämförelse av observabilitetsrank, LOSO, confidence-yta och
through-share-känslighet. Den gamla `sensor_placement_screen_v1.json` får bara
vara en första spatial lista; verklig placering ska rangordnas efter
informationsvinst och portflöde, inte enbart avstånd till närmaste sensor.

### 4. Nästa aktiva förbättring: importera NVDB-struktur

Ersätt i första hand standardvärdena på de vägar som bär mest kalibrerad trafik
eller ofta ingår i avstängningsvinnare. Börja med körfält, skyltad hastighet,
förbjuden riktning och funktionell klass. Bevara stabila kant-ID:n och skriv en
före/efter-audit.

Detta angriper 4 990 defaultade körfältsvärden och 631 defaultade hastigheter.
Det kan förbättra restid, kapacitet och omledningsval utan att låtsas vara en
ny trafikmätning.

### 5. Därefter: incidentrealism

Med lokala avstängningsdata kan man testa 400-metersradien,
truncate-stranded-regeln och behovet av congestion-driven rerouting. Utan
sådana data skulle mer beteendekod bara byta ett antagande mot ett annat.

### Vad som inte bör prioriteras nu

- Ny dirsplit-arkitektur: den aktiva signalen är liten och Gate S ändrar inget
  beslut i den testade matrisen.
- Större q-ensemble: q10/q90 saknar kalibrerad täckning.
- Full årsuppvärmning: dyrt och lagringskrävande innan accuracy- och
  valideringsidentiteten är stängd.
- Aktivering av cost-ordered global-best: benchmark v5 passerade inte hela
  equivalence-gaten.

## Evidensöversikt

| Del | Starkaste nuvarande bevis | Bedömning |
|---|---|---|
| Sensorintag | register, riktning, snap och transaktionell publicering | **Så bra som nuvarande rådata tillåter** |
| Mätprioritet i PFE | explicit relaxeringsstege; prior/bounds släpps före mätband | **Starkt kontraktsbevis** |
| Fit på använda sensorer | 100 % GEH<5, 0 infeasible i aktuell 06–10-build | **Starkt internt fit, inte spatial generalisering** |
| Riktningspunkt | Gate M=`MODEL`, 0,0604 mot 0,0617 | **Liten men held-out-stödd förbättring** |
| Riktningens beslutspåverkan | Gate S=`NO`, 48/48 clean | **Starkt negativt bevis för den frysta matrisen** |
| Kandidat-/OD-struktur | inga aktuella structure flags; provenancebunden | **Bra intern kontroll, nivåerna är fortfarande priorer** |
| Spatial generalisering | LOSO 0,466–1,354, median 0,613; alla stationer underidentifierade | **Inte tillräckligt bra för stark citywide accuracy** |
| Temporal generalisering | 2025-09-17: 0,445–1,356, median 0,623; identitetsmatchad | **Aktuellt negativt generaliseringsbevis** |
| SUMO-health | flera bundna kampanjer och 48 clean Gate S-runs | **Bra exekveringsbevis för testade fall** |
| Aktuell validation.json | matchad baseline; `overall=pass`, 0 varningar, 0 saknade sektioner | **Byggkoherent diagnostiskt simulationsbevis** |
| Vägnätets fysik | full audit, men 70 % lane defaults och syntetiska signaler | **Ärligt redovisat, inte lokalt validerat** |
| Årsuppvärmning | plan/preflight klar, pilot 3/3267 | **Redo att köras, inte färdig produkt** |

## Praktisk läsordning för nästa utvecklare

1. `AGENTS.md`, därefter current blocks i `TASKS.md` och `AGENT_NOTES.md`.
2. `ARCHITECTURE.md` för hierarkin och de fasta kontrakten.
3. Denna genomgång för dataflöde, bevisstyrka och förbättringsordning.
4. `sumo/demand_meta.json`, `web/data/validation.json` och relevant
   `validation/*outcome*.json` för den exakta körningen.
5. Koden i den ordning som beskrivs ovan; börja inte i webben och arbeta bakåt
   från en grön etikett utan att först kontrollera build-id och hashkedjan.
