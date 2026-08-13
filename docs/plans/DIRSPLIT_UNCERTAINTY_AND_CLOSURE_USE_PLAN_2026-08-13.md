# Plan för dirsplit, efterfrågeosäkerhet och korrekt användning i vägavstängning

**Datum:** 2026-08-13
**Status:** Forsknings- och implementationsplan. Ingen ny modell eller policy är
aktiverad av detta dokument.
**Gäller:** `dirsplit/`, demand-byggaren, scenarioavtal, closure-screening,
SUMO-finalister, observability och webbgränssnitt.
**Bakåtkompatibilitet:** Befintliga q10/q50/q90-arkiv och frysta
releaseartefakter är historisk evidens och får inte skrivas om.

## Beslut i korthet

Programmet ska inte välja mellan "en enda simulering" och "tre kvantiler" som
om de vore samma sorts lösning. De svarar på olika frågor:

1. En central riktningsprofil används för billig, bred screening och för en
   representativ visualisering.
2. Osäkerheten i riktningen representeras för finalister av flera **koherenta
   dags-scenarier**, där vägar och tider varierar tillsammans på ett sätt som
   har stöd i observerade residualer.
3. SUMO:s egen slump mäts separat med flera **matchade random seeds**. Samma
   seed används för basfallet och varje avstängningsalternativ.
4. Nuvarande q10/q90 behålls som versionsbundna **stressfall**, inte som
   sannolikhetsutsagor, tills deras nominella 80-procentiga intervall har
   validerats utanför träningsdata.
5. Låg observability ska normalt ge bredare osäkerhet, svagare anspråk och
   eventuellt ett `inconclusive`-resultat. Den ska inte automatiskt förbjuda en
   väg. Topologi, framkomlighet och no-detour är däremot hårda säkerhetsgrindar
   oavsett datatäckning.

Detta ger en snabb produktväg utan att publicera en vinnare som bara råkade
vinna under en enda efterfrågebild eller ett enda random seed.

## Varför dagens q10/q50/q90 inte ska användas som tre sannolika världar

### Revisionsfynd i den nuvarande koden

| Del | Nuvarande beteende | Konsekvens |
|---|---|---|
| `dirsplit/dataset.py` | Råa tidsobservationer aggregeras till ett medel per station, riktning, timme och dagtyp före träning. | Kvantilmodellerna ser variation mellan aggregerade stationstimmar, inte den faktiska dag-till-dag-variationen som en framtida dag behöver. |
| `dirsplit/train.py` | Bara vardagar kl. 06–20 tränas. `is_weekend` är därför alltid noll. | Helger och kl. 21–05 saknar träningsstöd trots att prediktionen producerar alla dygnets timmar. |
| `dirsplit/predict.py` | Alla 24 timmar predikteras med `is_weekend=0`, och samma 96 kvartsvärden återanvänds över kalendern. | Off-hours extrapoleras och en sökning över flera månader får ingen riktig dagtyps- eller dagsvariation i riktningsfördelningen. |
| Profilfeatures | Träningens profilfeatures bygger på summan av båda riktningar. För flera Göteborgsmål finns bara en uppmätt riktning i normalprofilen. | Samma feature-namn kan få olika innebörd i träning och användning. |
| Kvantiler | Tre separata LightGBM-modeller uppskattar q10/q50/q90. Crossing repareras och intervallet flyttas efter punktmodellens shrinkage. | Monotoniciteten blir giltig, men täckningen efter efterbehandlingen har aldrig återvaliderats. |
| `dirsplit/coverage.py` | Applicability mäts bara som kNN-avstånd i statiska vägfeatures. | Den ser inte brist på tidsstöd, profilfeature-mismatch, effektiv träningsmängd eller dags-/helgextrapolation. |
| Demand | En q10-, q50- och q90-yta appliceras samtidigt över alla berörda vägar och tider. | Det är tre marginala ytterlägen, inte tre koherenta gemensamma dagsförlopp. |
| Scenarioavtal | Variant och SUMO-seed binds ihop, exempelvis `1000/q50`, `1001/q10`, `1002/q90`. | Skillnader mellan efterfrågefall och simulatorns slump är sammanblandade i en enreplikatskörning. |
| Ranking | Vissa deterministiska vägar tar field-wise max från olika q-varianter. | Resultatet kan bli en syntetisk kostnadsvektor som inte inträffade i någon enskild värld. |

### Mätt läge i spårade artefakter

`data/dirsplit/train_report.json` har 1 214 träningsrader. Den krympta
punktmodellen har pooled domain-MAE 0,0557 mot 0,0565 för 50/50. Det är bara en
liten total förbättring. På domain-subset är modellen sämre än 50/50 i Bergen,
Oslo och Stavanger och endast marginellt bättre i Trondheim. Shrinkage är
`lambda=0.289`, vilket innebär att merparten av modellens råa avvikelse från
50/50 tas bort av valideringen.

I `sumo/direction_split.json` är medianavvikelsen för q50 från 0,5 bara 0,007
och den största 0,034. Medianbredden q10–q90 är 0,107, men ingen rapport visar
att intervallet täcker ungefär 80 procent av framtida observationer. De tre
spårade routefilerna har 19 845, 20 836 respektive 21 749 fordon; alltså ändrar
ytterfallen också nätets totala belastning, inte bara riktningen.

Slutsatsen är inte att 50/50 säkert är sann. Slutsatsen är att den nuvarande
komplexa punktmodellen ännu inte har visat en robust fördel över en enkel
baseline, och att q10/q90 ännu inte har visat den sannolikhetsbetydelse som
namnen antyder.

## Forskningsgrund och följd för designen

### Kalibrering före etiketter som q10 och q90

Gneiting, Balabdaoui och Raftery beskriver målet för sannolikhetsprognoser som
att maximera skärpa under kalibrering: ett smalt intervall är bara värdefullt
om dess frekvensmässiga täckning stämmer. Proper scoring rules och pinball loss
gör att modeller kan jämföras utan att belöna felaktig säkerhet. Följden här är
att q10/q90 inte får presenteras som ett kalibrerat 80-procentsintervall innan
out-of-sample-täckning och intervallscore har mätts.

Conformalized Quantile Regression kan kalibrera marginalintervall under
exchangeability. Men de norska stationerna, dagarna och Göteborgsmålen är inte
självklart exchangeable. Resultat för covariate shift kräver att förhållandet
mellan mål- och träningsfördelning är känt eller tillräckligt väl skattat, och
tidsserier kräver metoder som uttryckligen hanterar beroende. Nuvarande
Gaussian-kernelviktning är därför en rimlig modelleringsheuristik men inte i
sig ett formellt täckningsbevis.

**Planföljd:** använd blockerade datum/stationer/städer vid validering,
rapportera empirisk transfer-täckning per domängrupp och kalla inga intervall
"garanterade".

### Marginalkvantiler är inte gemensamma scenarier

Kalibrering av varje väg och tidpunkt separat bevarar inte korrelation mellan
vägar och timmar. Ensemble Copula Coupling-litteraturen visar varför en
beroendemall behövs för att återbilda realistiska rums- och tidsförlopp efter
marginal kalibrering. Variogram score är särskilt relevant eftersom energy
score kan vara relativt okänslig för felaktig korrelationsstruktur.

**Planföljd:** scenarioenheten ska vara en hel dag med alla berörda sensorer
och tider, inte en oberoende kvantil per cell. Den första versionen återanvänder
observerade residualblock på logit-skalan; en parametrisk copula är en senare
kandidat endast om residualbiblioteket är för tunt.

### En scenarioensemble måste bedömas genom beslutet

Kaut och Wallace visar att scenariogenerering inte bara kan bedömas genom hur
väl några marginalmoment matchar. Lösningens stabilitet och beslutskvalitet
måste testas när scenarioantal eller scenariosampel ändras. Birges "value of
the stochastic solution" formaliserar att en lösning baserad på förväntade
indata kan skilja sig från en lösning som tar hänsyn till fördelningen.

**Planföljd:** jämför central-only och ensemblebeslut, öka scenarioantalet i
förregistrerade batcher och stoppa först när finalistmängd, rangordning och
regret är stabila eller när ett beräkningsbudgettak nås. Ett tak som nås utan
stabilitet ger `inconclusive`, inte en vinnare.

### Flera seeds behövs även med bra efterfrågan

FHWA rekommenderar att varje mikrosimuleringsvariant först körs fyra gånger med
olika random seeds för att bedöma variation. Trafiksimuleringsforskning om
common random numbers visar att samma seed över jämförda alternativ kan minska
variansen i deras differens.

**Planföljd:** `demand_case_id` och `simulation_seed` blir ortogonala axlar.
Baslinje och kandidat kör exakt samma par av scenario och seed. Minst fyra
seeds används i den första finalistbatchen; fler läggs till adaptivt när
skillnaden är nära beslutsgränsen.

### D-factor stödjer en enkel baseline men inte blind säkerhet

FHWA definierar D-factor som den riktade andelen av dubbelriktad trafik och
noterar att centrala urbana vägar ofta ligger nära 50/50, medan lokala eller
route-specifika mätningar är att föredra. Det stöder 50/50 som en nödvändig
baseline och shrinkage-mål, inte som ett universellt facit.

**Planföljd:** den enklaste modell som klarar held-out-grindarna ska användas.
Mer modellkomplexitet är inte ett produktmål.

## Målsemantik och invariants

Följande begrepp får inte längre blandas:

- `central_profile`: bästa punktuppskattning för en väg, dagtyp och slot.
- `marginal_interval`: kalibrerat eller uttryckligen okalibrerat intervall per
  cell; ett diagnostiskt objekt, inte automatiskt ett SUMO-scenario.
- `demand_case`: ett komplett, koherent förlopp för alla vägar och slots under
  en kalenderdag.
- `scenario_path`: en versionsbunden följd av demand cases över hela
  warm-up-, closure- och recoveryhorisonten. Dagarnas cases kan vara olika men
  får inte väljas om efter kandidatens utfall.
- `stress_case`: ett avsiktligt extremfall utan påstådd sannolikhetsvikt.
- `simulation_seed`: SUMO:s stokastiska replikation, oberoende av vilket
  demand case som används.
- `simulation_member`: den explicita kombinationen
  `(scenario_path_id, simulation_seed)`; för en endagskörning refererar pathen
  till exakt ett `demand_case_id`.

Obligatoriska invariants:

1. Två riktningar summerar till 1,0 i varje slot och scenario före avrundning.
2. En flerdagars closure använder samma `scenario_path_id` från warm-up till
   recovery. Pathen får innehålla olika förregistrerade dagscases, men får inte
   välja "värsta" case separat per dag, kandidat eller kostnadsfält.
3. Baslinje och avstängning använder samma scenario path, vehicle population
   och SUMO-seed.
4. Ett komplett scenario reduceras till en komplett kostnadsvektor före
   förväntan, tail-mått eller worst-case. Field-wise splicing är förbjuden.
5. Ingen probabilistisk vikt sätts på ett stressfall.
6. En ofullständig scenario- eller seedbudget kan aldrig publicera en
   `unique_winner`; utfallet är `paused` eller `inconclusive`.
7. Topologiskt severing, no-detour och andra säkerhetsbrott diskvalificerar
   oberoende av förväntad kostnad.
8. Gamla q-arkiv läses via en legacy-adapter. De migreras aldrig på plats och
   blir inte automatiskt release-evidens för den nya policyn.

## Målarkitektur

```text
råa riktningspar per station–datum–timme
                  │
                  ▼
       dirsplit dataset v2 (ej medelaggregerat)
                  │
       ┌──────────┴──────────┐
       ▼                     ▼
central modellturnering   held-out residualblock
       │                     │
       ▼                     ▼
central_profile      marginal kalibrering +
                     gemensamma dags-scenarier
       └──────────┬──────────┘
                  ▼
      DemandEnsembleManifest v2
                  │
       ┌──────────┴──────────────┐
       ▼                         ▼
central-only bred screening   finalistensemble
                                 │
                    samma seeds för bas/kandidat
                                 │
                                 ▼
                  risk-, stabilitets- och CI-beslut
```

### Ny versionsbunden artefakt

Föreslagen fil: `sumo/direction_ensemble_v2.json`.

Den ska minst innehålla:

```json
{
  "schema_version": "direction_ensemble_v2",
  "model_digest": "...",
  "training_data_digest": "...",
  "calibration_digest": "...",
  "slot_minutes": 15,
  "central_profile": {},
  "marginal_intervals": {
    "nominal_coverage": 0.8,
    "calibration_status": "stress_only"
  },
  "demand_cases": [
    {
      "case_id": "...",
      "kind": "probabilistic",
      "weight": 0.0625,
      "day_type": "weekday",
      "edge_shares": {}
    }
  ],
  "path_generator": {
    "version": "blocked_residual_path_v1",
    "calendar_binding": "content_addressed"
  },
  "stress_cases": [],
  "applicability": {},
  "provenance": {}
}
```

Ett probabilistiskt scenario har vikt; stressfall saknar vikt. Summan av
probabilistiska vikter måste vara exakt 1 inom den numeriska tolerans som
schemat anger. Filen binds till källdata, modell, kalibrering, vägpar och
slotdefinition med digests.

## Förbättring av `dirsplit`

### 1. Dataset v2: bevara den variation som ska modelleras

Ändra `dirsplit/dataset.py` så att huvudtabellen innehåller en rad per
`station_id, local_date, hour, heading` med:

- `toward_count`, `away_count`, `total_count` och `share`;
- lokal tidsstämpel, veckodag, `is_weekend`, månad och helgdag när källan kan
  stödja det;
- täckningsgrad och explicita missingnessorsaker;
- station, stad och väg-/profilfeatures;
- ett stabilt `day_block_id` som håller samtidiga observationer samman.

Behåll en separat aggregerad export endast för diagnostik. Träningskoden får
inte använda den som standard. Råa antal behövs för att skilja högvolymsbevis
från en instabil andel baserad på få fordon.

Förändra inte tidsupplösningen artificiellt. Om källan bara ger timdata ska
15-minutersprofilen härledas deterministiskt och osäkerheten märkas som
timupplöst; modellen får inte låtsas ha observerat kvartvariation.

### 2. Eliminera feature-mismatch

Inför två uttryckliga inference paths:

- `paired_total`: båda riktningar är mätta och totalprofilfeatures är
  tillgängliga med samma semantik som i träningen;
- `single_direction`: endast en riktning är mätt. Antingen används features
  som kan beräknas likadant i träning och mål, eller så tränas en separat modell
  med simulerad feature-missingness.

En feature får aldrig behålla samma namn om dess innebörd ändras. Om den enkla
modellen utan profilfeatures vinner held-out ska profilfeatures tas bort i
stället för att räddas med en implicit fallback.

### 3. Modellturnering: enklast godkända modell vinner

Implementera samma datadelningar och rapportering för minst fyra kandidater:

1. `constant_5050`;
2. hierarkiskt krympt D-factor per timme/dagtyp, med partiell pooling mot 0,5;
3. nuvarande similarity-weighted LightGBM som punktmodell;
4. binomial eller beta-binomial logistisk modell över riktningsantalen, med
   överdispersion och gruppstruktur när datamängden räcker.

LightGBM är inte automatiskt produktvinnare. Välj centralmodell på
out-of-sample-resultat och komplexitet. Beta-binomial är motiverad av att
etiketten kommer från två antal och att variationen ofta är större än ren
binomialvariation, men ska också vinna sin plats genom validering.

### 4. Korrekt valideringsdesign

Använd tre komplementära tester:

- leave-city-out för geografisk transfer;
- leave-station-out inom stad för ny väg;
- blocked-date eller blocked-week för framtida dagar och tidsberoende.

Alla transformationer, skalning, featureval, bandwidth, shrinkage och
kalibrering ska fitas innanför respektive träningsfold. Ingen information från
testfolden får påverka modellvalet.

Rapportera per stad, stationstyp, dagtyp, timband och applicability-klass:

- MAE och viktad absolut avvikelse för centralprofil;
- pinball loss för varje marginalkvantil;
- empirisk täckning, med konfidensintervall, och medelbredd för q10–q90;
- interval score/weighted interval score;
- bias och fel mot 50/50-baseline;
- effektiv träningsvikt och antal oberoende day blocks.

Centralmodellen går bara vidare om dess förbättring mot 50/50 är robust i
förregistrerade huvudgrupper. Om skillnaden är oklar används den enklare
modellen och osäkerheten redovisas. Exakta toleranser och bootstrapmetod fryses
innan testfoldarna mäts.

### 5. Marginal kalibrering utan falska garantier

Implementera kalibrering på held-out residualer, med block per dag/station så
att tidsberoende inte behandlas som oberoende rader. CQR eller block-conformal
kan provas i en forskningsarm, men produktartefakten ska ange:

- `empirical_transfer_coverage`;
- vilka städer, datum och grupper som ingick;
- om målet är interpolation eller extrapolation;
- om täckningen är marginal, gruppvis eller gemensam;
- att en formell exchangeability-garanti saknas när det är fallet.

Nuvarande monotone rearrangement/crossing guard kan behållas, men hela kedjan
inklusive clamp och shrinkage måste valideras efter efterbehandlingen.

### 6. Koherenta dags-scenarier och flerdagars paths

Första implementerbara metoden är ett empiriskt residualbibliotek:

1. Fit centralmodellen utan en blockerad stad eller period.
2. Beräkna residualer på logit-skalan för varje observerat
   `city, date, station, hour`.
3. Behåll varje stadsdatum som ett helt residualblock.
4. Matcha varje Göteborgsväg mot en donorstation utifrån samma explicita
   applicability-features; alla vägar för ett scenario hämtar residualer från
   samma donorstad och datum när möjligt.
5. Addera blocket till Göteborgs centralprofil, transformera tillbaka och
   normalisera vägparet exakt.
6. Välj scenariofall outcome-blind med stabil hash eller förregistrerad
   stratifiering över stad, dagtyp, säsong och residualstorlek.
7. För en flerdagars closure: återanvänd i första hand sammanhängande
   observerade residualblock. Om horisonten är längre än tillgängliga block,
   använd en förregistrerad moving/stationary block-bootstrap som bevarar
   observerad flerdagarsautokorrelation. Oberoende dagssampling är bara en
   baseline och måste märkas som sådan.

Det bevarar observerad inomdagskorrelation, en mätbar del av korrelationen
mellan vägar och, när blocklängden räcker, beroendet mellan efterföljande dagar.
Om simultana stationer eller sammanhängande dagar är för få ska artefakten säga
det och använda bredare oberoende stressfall som diagnostik, inte ge dem falska
vikter.

Jämför residualbiblioteket mot minst en enklare oberoende residualbaseline.
Validera marginaler med pinball/intervallscore och gemensam struktur med
energy score, variogram score, autokorrelation, korsvägskorrelation samt
extremhändelsefrekvens.

## Hur detta ska användas i closure-optimeringen

### Fas A — billig screening över alla datum och längder

- Använd en central demandprofil och befintliga deterministiska
  topologi-/survivabilitygrindar.
- Screening får rangordna och gallra men inte göra ett obegränsat
  sannolikhetsanspråk.
- En q50-liknande centralprofil kan användas här även när dess intervall inte är
  kalibrerat, så länge den är märkt som point estimate.
- Vägar med låg observability ska inte tas bort enbart därför. De behålls med
  högre osäkerhetsflagga; helt saknade eller semantiskt ogiltiga inputs gör
  resultatet `unavailable` eller tvingar explicit fallback.

### Fas B — finalistensemble

För varje finalist och baslinje:

1. Kör samma probabilistiska scenario paths över hela simuleringshorisonten.
2. Kör samma initiala fyra SUMO-seeds inom varje scenario path.
3. Beräkna parade differenser kandidat minus baslinje.
4. Utöka seeds adaptivt där Monte Carlo-osäkerheten är beslutsrelevant.
5. Utöka scenario paths i förregistrerade batcher, exempelvis 4, 8 och 16, och
   kontrollera om finalistmängd, rangordning och regret stabiliseras.
6. Stoppa med vinnare bara om både scenario- och seedgrindarna passerar.

Batchstorlekarna ovan är en shadow-standard för mätning, inte en aktiverad
releasepolicy. Det slutliga taket ska bestämmas från runtime- och
stabilitetsbenchmark innan registreringen fryses.

### Fas C — beslut utan syntetisk värsta värld

Varje scenario path ger en komplett kostnadsvektor efter att alla dess dagar
har summerats. Reducera därefter med:

- primärmål: viktat förväntat tillagt vehicle-time för probabilistiska fall;
- separat tail-mått, exempelvis viktad q90 eller CVaR, när scenariomängden är
  stor nog;
- maximum regret och rankstabilitet som robusthetsdiagnostik;
- hård diskvalificering vid no-detour, severing, invalid health eller andra
  säkerhetsbrott.

Stressfall rapporteras separat och påverkar en uttrycklig robustness gate, inte
det viktade medelvärdet. Om olika riskmått föredrar olika kandidater ska
produkten säga `tie` eller `inconclusive` tills en verksamhetsmässig riskpolicy
har valts och förregistrerats. Koden får inte gömma valet i godtyckliga vikter.

### Fas D — presentation

Webben visar:

- central uppskattning;
- empiriskt kalibrerat intervall eller texten "okalibrerat stressintervall";
- antal scenario paths, demand cases och seeds som faktiskt körts;
- scenario- och seedkonvergens;
- observability/applicability för berörda vägar;
- om vinnaren är robust, villkorad eller inconclusive.

Animationen visar en representativ central körning och märks som just en
körning. Den får inte visuellt framställas som hela osäkerhetsanalysen.

## Observability v2

`dirsplit/coverage.py` ersätts inte av ett enkelt ja/nej-filter. Den utökas till
en evidensprofil med separata dimensioner:

- `measurement_level`: båda riktningar mätta, en riktning mätt, eller enbart
  transferprior;
- `static_domain`: kNN-/densitystatus för vägfeatures;
- `temporal_support`: vardag/helg, timband och säsong som fanns i träning;
- `feature_compatibility`: exakt samma inputsemantik eller fallback;
- `effective_sample_size`: stationer och oberoende day blocks efter viktning;
- `calibration_support`: held-out-gruppens täckning och intervallscore;
- `local_crosscheck`: jämförelse mot verkliga lokala dubbelriktade stationer
  när en sådan kontroll är geografiskt relevant.

Användning:

- god evidens: normal scenarioensemble och normala anspråk;
- begränsad evidens: bredare empiriska scenarier, varning och högre krav på
  beslutstabilitet;
- extrapolation: konservativ fallback/stressfall och normalt
  `inconclusive` om valet påverkas;
- inputfel eller omöjlig vägparning: fail-closed `unavailable`.

Detta undviker det orimliga i att "förbjuda vägar vi vet lite om", samtidigt
som programmet inte låtsas att okunnighet är precision. Rapporten bör också
rangordna vilka nya riktade mätpunkter som mest minskar beslutets osäkerhet
(`value_of_information`), men den analysen får inte använda held-out-utfall
för att välja releasefall.

## Implementationsordning

### Steg 0 — frys nuläget och inför språkgränsen

**Kod och artefakter**

- Lägg kontraktstester som pinnar nuvarande `direction_split.json`, routefiler,
  seed↔variantmapping och legacy ranking.
- Lägg `dirsplit/schema.py` med versionsbundna dataklasser för centralprofil,
  marginalintervall, demand case, scenario path, stressfall och applicability.
- Lägg en legacy-adapter som läser q10/q50/q90 utan att ändra gamla arkiv.
- Byt nya interna namn från `variant` till `demand_case_id`; gamla API-fält
  behålls endast i legacyobjekt.

**Acceptans**

- Gamla golden- och resume-tester är byte-identiska.
- En seed kan inte implicera ett demand case i v2-kontraktet.
- Stressfall kan inte få sannolikhetsvikt.

### Steg 1 — bygg `training_table_v2`

**Primära filer**

- Ändra `dirsplit/dataset.py`.
- Lägg `dirsplit/dataset_schema.py` och datasetvalidering.
- Dela tester i dataset-, leakage- och tidsstödstester.

**Acceptans**

- Råa antal och `day_block_id` överlever till träningen.
- Inga testdatum påverkar fitade transformationer.
- Weekend/off-hours är antingen verkligt stödda eller explicit unsupported;
  ingen tyst vardagsextrapolation återstår.

### Steg 2 — modellturnering och centralprofil

**Primära filer**

- Refaktorera `dirsplit/train.py` till gemensamt fold-API.
- Lägg `dirsplit/models.py` och `dirsplit/evaluate.py`.
- Uppdatera `dirsplit/predict.py` till den vinnande versionsbundna modellen.

**Acceptans**

- 50/50, shrunk D-factor, LightGBM och count-model får exakt samma folds.
- Rapporten visar uncertainty på skillnaden, inte bara ett punkt-MAE.
- Den enklaste statistiskt försvarbara modellen väljs deterministiskt enligt en
  förregistrerad regel.

### Steg 3 — marginal kalibrering och applicability

**Primära filer**

- Lägg `dirsplit/calibrate.py`.
- Utöka `dirsplit/coverage.py` till evidensprofilen ovan.
- Versionera `data/dirsplit/train_report_v2.json` och
  `data/dirsplit/calibration_report_v2.json`.

**Acceptans**

- Täckning och skärpa finns per huvudgrupp med konfidensintervall.
- Postprocessing, clamp och rearrangement ingår i mätningen.
- Ett okalibrerat intervall serialiseras som `stress_only`; ordet
  "80-procentsintervall" kan inte nå UI:t.

### Steg 4 — gemensamma dags-scenarier

**Primära filer**

- Lägg `dirsplit/scenarios.py` och `dirsplit/scenario_evaluation.py`.
- Skriv `sumo/direction_ensemble_v2.json` och en separat valideringsrapport.

**Acceptans**

- Samma demand-case-ID beskriver hela väg×tidsmatrisen för en dag och samma
  path-ID binder alla dagar i en flerdagars closure.
- Reproducerbarhet och content digests är låsta.
- Marginala och gemensamma scores jämförs mot enkla baselines.
- Scenarioantalets stabilitetskurva finns innan ett produktionstak väljs.

### Steg 5 — koppla demand utan att multiplicera hela kalendern i förväg

**Primära filer**

- Generalisera `demand/intake.py` och `demand/priors.py` från q-nyckel till
  demand case.
- Ändra `build_sumo_demand.py` så central demand kan byggas eager men
  finalisternas scenario-routefiler byggs lazy och content-adresserat.
- Generalisera `traffic_sim/simulation/monthly_demand.py` och manifestet.

**Acceptans**

- Central-only v2 reproducerar legacy q50 inom en dokumenterad differential
  tolerans eller redovisar varje avsiktlig skillnad.
- En flermånaderssökning skapar inte alla scenario-routefiler för alla
  kandidater före shortlist.
- Cache-ID binder datum, case, modell, kalibrering, vägpar och routekälla.

### Steg 6 — separera demand cases och SUMO-seeds

**Primära filer**

- Versionera `ScenarioSpec` i `traffic_sim/core/contracts.py`.
- Generalisera `monthly_search.py`, `monthly_sumo.py`,
  `finalist_decision.py`, `pilot_selection.py`, `independent_daily.py`,
  `deterministic_disruption.py`, `closure_ranking.py` och warm-stateindex.
- Ersätt `canonical_seed(variant, repetition)` med explicita
  `SimulationMember`-identiteter.

**Acceptans**

- Samma seedlista används för varje case och för baslinje/kandidat.
- Tester kan ändra demand case medan seed hålls fast och tvärtom.
- Ingen reducerare kan ta fält från olika cases och skapa en falsk kostnad.
- Paus eller cap före konvergens kan inte bli `unique_winner`.

### Steg 7 — beslutsregel, API och UI i shadow mode

**Primära filer**

- Lägg en versionsbunden risk-/stabilitetspolicy i finalistbeslutet.
- Utöka `serve.py`, workspace/progress-kontrakt och webbkomponenterna.
- Behåll nuvarande produktion som default; v2 är opt-in shadow/replay.

**Acceptans**

- UI skiljer central körning, demand-osäkerhet och seed-osäkerhet.
- Väg med låg observability är inte tyst bortfiltrerad.
- `stress_only`, `paused`, `unavailable` och `inconclusive` har egna tydliga
  tillstånd och kan inte läsas som vinnare.

### Steg 8 — evidens och aktivering

Frys före körning:

- modellkandidater, folds och metric hierarchy;
- nominell täckning och gruppgrindar;
- scenario-batchar och högsta beräkningsbudget;
- seedstart, adaptiv repetitionsregel och CI-metod;
- riskmått, tie-/inconclusive-regel och säkerhetsgrindar;
- testdatum, vägar och closure-kandidater outcome-blind.

Kör därefter:

1. dirsplit point/interval held-out;
2. joint-scenario-validering;
3. central-only mot ensemble shadow replay;
4. scenario- och seedkonvergens;
5. restart/cache/provenance;
6. ett fryst held-out closure-test endast om föregående grindar passerar.

Aktivera inte v2 om den bara är bredare. Den ska antingen ändra ett beslut på
ett verifierbart bättre sätt eller visa att central-only-beslutet är stabilt,
samt uppfylla kalibrering, runtime och hälsogrindar. Ett negativt benchmark är
giltig evidens och stänger aktivering utan att utplånas.

## Testmatris

Minsta nya testgrupper:

- dataset: råa antal, DST, helg, missingness, day blocks och leakage;
- features: paired/single-direction semantik och nya sensorer;
- modell: foldisolering, determinism, baselinejämförelse och shrinkage;
- intervall: pinball, coverage, rearrangement, clamp och group reports;
- scenario: parsumma, tids-/vägberoende, vikter, stressfall och digests;
- demand: lazy build, cacheinvalidisering och legacy q-adapter;
- simulation: case×seed-korsprodukt, common random numbers och matched pair;
- ranking: ingen field-wise splicing, hard safety och inconclusive;
- monthly: flerdagars scenarioidentitet över månadsskifte och resume;
- API/UI: truthful labels, progress, reload och claims;
- golden: gammal policy oförändrad, ny policy versionsbunden.

## Forsknings- och evidensartefakter

Föreslagna append-only-filer:

- `validation/dirsplit_point_benchmark_v1.json`;
- `validation/dirsplit_interval_calibration_v1.json`;
- `validation/dirsplit_joint_scenario_benchmark_v1.json`;
- `validation/closure_uncertainty_shadow_registration_v1.json`;
- `validation/closure_uncertainty_shadow_outcome_v1.json`;
- `validation/closure_uncertainty_heldout_registration_v1.json`;
- motsvarande outcome endast efter godkända föregående grindar.

Alla ska innehålla source-, data-, model-, network- och policy-digests,
plattform, SUMO-version, seeds, scenario-ID:n, start/slut, komplett status och
`release_evidence`.

## Det som uttryckligen inte ska göras

- Kör inte en enda q50/seed och kalla resultatet säkert eller robust.
- Kör inte global q10/global q90 och kalla dem sannolika gemensamma dagar utan
  joint validation.
- Exkludera inte vägar enbart för att observability är låg.
- Låt inte osäkra empiriska bounds bli hårda fysiska constraints i PFE utan en
  separat slack- och provenancepolicy.
- Koppla inte scenarioidentitet till seednummer.
- Optimera inte scenariofall eller riskvikter på samma held-out-resultat som
  ska bedöma policyn.
- Skriv inte om befintliga q-arkiv eller öppna nu stängda closure-releasegrindar
  på grund av denna plan.

## Primärkällor

- Gneiting, Balabdaoui & Raftery (2007), *Probabilistic forecasts,
  calibration and sharpness*:
  <https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf>
- Gneiting & Raftery (2007), *Strictly Proper Scoring Rules, Prediction, and
  Estimation*:
  <https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf>
- Romano, Patterson & Candès, *Conformalized Quantile Regression*:
  <https://papers.neurips.cc/paper/8613-conformalized-quantile-regression.pdf>
- Tibshirani, Barber, Candès & Ramdas (2019), *Conformal Prediction Under
  Covariate Shift*:
  <https://papers.nips.cc/paper/2019/hash/8fb21ee7a2207526da55a679f0332de2-Abstract.html>
- Chernozhukov, Wüthrich & Zhu (2018), conformal inference for dependent data:
  <https://proceedings.mlr.press/v75/chernozhukov18a.html>
- Xu & Xie (2023), *Sequential Predictive Conformal Inference for Time Series*:
  <https://proceedings.mlr.press/v202/xu23r>
- Schefzik, Thorarinsdottir & Gneiting, ensemble copula coupling:
  <https://nr.no/en/publication/1072359/>
- Scheuerer & Hamill (2015), variogram-based proper scoring rules:
  <https://journals.ametsoc.org/view/journals/mwre/143/4/mwr-d-14-00269.1.xml>
- Kaut & Wallace, *Evaluation of Scenario-Generation Methods*:
  <https://work.michalkaut.net/papers_etc/SG_evaluation.pdf>
- Birge (1982), *The value of the stochastic solution*:
  <https://deepblue.lib.umich.edu/items/c11c92bf-3041-4e83-b561-77daa349bc81>
- FHWA, Traffic Analysis Toolbox, alternatives analysis och flera seeds:
  <https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter6.htm>
- Rathi & Venigalla (1992), common random numbers i trafiknätssimulering:
  <https://www.sciencedirect.com/science/article/pii/019126159290031Q>
- FHWA, Traffic Monitoring Guide, directional distribution/D-factor:
  <https://www.fhwa.dot.gov/policyinformation/tmguide/tmg_2013/traffic-monitoring-theory.cfm>
- FHWA, Traffic Data Computation Method Pocket Guide, urban D-factor:
  <https://www.fhwa.dot.gov/policyinformation/pubs/pl18027_traffic_data_pocket_guide.pdf>
- Chernozhukov, Fernández-Val & Galichon, non-crossing quantiles:
  <https://arxiv.org/abs/0704.3649>

## Definition of done

Planen är färdigimplementerad först när:

1. centralprofilen väljs mot enkla baselines på läckagefri held-out;
2. q-etiketter endast används för empiriskt validerade marginaler, annars
   heter de stressfall;
3. koherenta dags-scenarier har validerad tids-/vägstruktur;
4. demand case och SUMO-seed är separata i kontrakt, cache och evidens;
5. finalister kör matchade bas/kandidatpar över flera cases och seeds;
6. ranking använder kompletta scenarier och kan ge `inconclusive`;
7. observability påverkar osäkerhet och anspråk, inte ett godtyckligt
   vägförbud;
8. flermånaderskörning bygger scenarier lazy och klarar budget/resume;
9. API/UI redovisar central, scenario-, seed- och applicability-osäkerhet
   separat;
10. frysta shadow- och held-out-grindar har passerat utan försvagning och den
    nya policyn aktiveras i en separat versionsändring.
