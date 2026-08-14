# Plan för dirsplit, efterfrågeosäkerhet och korrekt användning i vägavstängning

**Datum:** 2026-08-13
**Status:** Forsknings- och implementationsplan. Ingen ny modell eller policy är
aktiverad av detta dokument.

## Grindstatus 2026-08-14 (efter granskning)

**Ingen av grindarna är avgjord.** Fas 2, 3 och 4 är stängda.

| Grind | Status | Bevis |
|---|---|---|
| Gate S | `NOT_RUN` — extern egress-policy **och** konfunderade stressfall | `validation/dirsplit_direction_sensitivity_blocker_v3.json` |
| Gate M | `INCONCLUSIVE` — `blocked_date` kan inte byggas | `validation/dirsplit_gate_m_outcome_v3.json` |

**NYTT BLOCKERANDE FYND (2026-08-14): q10/q90 isolerar inte riktningsaxeln.**
`dirsplit/predict.py` skriver `edge_shares_q10` som `(e0 → s10, e1 → 1 − s90)`
och `edge_shares_q90` som `(e0 → s90, e1 → 1 − s10)`, så varje ytterpar
summerar till `1 ∓ (s90 − s10)` i stället för 1. Uppmätt på de artefakter som
byggdes i denna session: **största |parsumma − 1| = 0,297** (tolerans 0,001);
q50 summerar exakt till 1. Ett Gate S-utfall på dessa filer kan alltså bero på
ändrad TOTAL trafikvolym snarare än ändrad riktning, och i efterhand går de
två inte att skilja åt. Verktyget mäter detta vid registreringen, lägger in
resultatet i den frysta content-nyckeln och returnerar `INCONCLUSIVE` när
isolationen inte är styrkt. **Att bygga om q-artefakterna med bevarad parsumma
är därför en förutsättning för ett meningsfullt Gate S, inte en förbättring.**
Det arbetet hör till Fas 2 och är inte utfört.

En tidigare publicerad `Gate M = BASELINE` är **ÅTERKALLAD som beslut**.
v1-filerna är bevarade oredigerade och uttryckligen ersatta; inget i dem får
citeras som ett aktuellt resultat. Särskilt gäller att påståendet "den
deployade LightGBM-modellen är 31,6–39,2 % sämre" inte stöds — det mätte en
annan modell på en annan population.

Två granskningsrundor har genomförts. Den första fann tre kritiska och tre
höga fel; den andra fann att den reparerade beslutsregeln fortfarande kunde ge
ett FALSKT `YES` på tre separata sätt. Alla är åtgärdade i koden, men en
åtgärdad grind är fortfarande en okörd grind:

1. **Gate S körde inte olika q-fall eller seeds.** Verktyget byggde sitt
   kommando utan routefil och utan seed, läste ett `disruption`-fält som
   produkten inte producerar, använde inte sitt eget registrerade
   06:00–10:00-fönster, och reducerade ett privat mål i stället för de
   förregistrerade beslutsfälten. Nu binds varje `(fall, seed)` genom en egen
   en-seeds `ScenarioSpec` (befintligt kontrakt, oförändrat) och beslutet tas
   av `closure_ranking` oförändrad.
2. **Sensor 107:s ankare nådde inte produkten.** `load_direction_split`
   hade en `anchor_day`-parameter som ingen produktanropare skickade.
   `build_targets` tar nu `anchor_day`/`anchor_epoch` och alla tre
   anropsställen i `build_sumo_demand.py` skickar byggets eget datum.
   Års-D-factorn matchas **volymviktat** från samma `flows`.
3. **Gate M mätte fel population och fel modell, under kod som inte
   implementerade sin egen frysta regel.** Regeln är nu
   `simplest_defensible_v2`: vinst krävs under *varje* foldtyp, jämförelsen
   sker mot den *aktuella* incumbenten, och en obyggbar foldtyp ger
   `INCONCLUSIVE`.

Runda 2 (2026-08-14) åtgärdade dessutom:

4. **Ett rankningsvärde som varierar mellan seeds gav `YES`.** Nyckeln är
   efterfrågesidig och *kan inte* variera med seed; när den gör det har
   mätningen misslyckats och varken `YES` eller `NO` är tillgängligt. Nu
   `INCONCLUSIVE`.
5. **En kandidat som var diskvalificerad i alla q-fall kunde öppna grinden**
   genom sin kostnadsspridning, trots att policyn aldrig läser den kostnaden.
   Nu `decision_relevant: false`; är *alla* diskvalificerade blir utfallet
   `INCONCLUSIVE` — ingen viable set bildades, alltså fanns inget beslut att
   vara känsligt för.
6. **Seed-variationskontrollen grupperade fel** (per q-fall, inte per
   `(q-fall, kandidat)`), så en skillnad mellan kandidater kunde maskera sig
   som en skillnad mellan seeds. Nu måste *varje* grupp variera.
7. **Ett closure-fönster utanför demand-perioden ersattes tyst** med hela
   fönstret. Nu ett hårt fel.
8. Topologifiltret failade öppet utan `sumolib`; registreringens datum
   validerades inte mot `demand_meta.json`; Gate M-rapporten saknade
   digest-/content-key-bindning. Alla åtgärdade.

En strukturell iakttagelse värd att bevara: den deployade rankningsnyckeln
(`closure_disruption`) är efterfrågesidig och därmed **seed-deterministisk per
konstruktion**. Det gamla "spridningskvot mot seed-brus"-testet på den nyckeln
var därför en tautologi. Verktyget verifierar invarianten i stället och vägrar
publicera om seed-axeln visade sig vara inert.

Gate M mätte i runda 1 fortfarande inte exakt den deployade modellen: den
anpassade **en modell per stad** i stället för en per station, och den
nästlade shrinkage-beräkningen återanvände fel centrum. Båda är rättade;
`blocked_date` saknas fortfarande, så utfallet är oförändrat `INCONCLUSIVE`.
**Gäller omedelbart:** sensor 107:s lokala ankare, ett avgränsat
matched-seed-test och `dirsplit/` dataset/modellval.
**Villkorad senare omfattning:** demand-byggaren, scenarioavtal,
closure-screening, SUMO-finalister, warm-state, API och webbgränssnitt får bara
ändras om de explicita beslutsgaterna nedan passerar.
**Bakåtkompatibilitet:** Befintliga q10/q50/q90-arkiv och frysta
releaseartefakter är historisk evidens och får inte skrivas om.

## Beslut i korthet

Programmet ska inte välja mellan "en enda simulering" och "tre kvantiler" som
om de vore samma sorts lösning. De svarar på olika frågor:

1. Sensor 107:s publicerade lokala 2025-ankare 52/48 ska maskinbindas med rätt
   tidssemantik och slå transfermodellens aggregerade nivå. Det får inte
   felaktigt behandlas som 96 uppmätta kvartsvärden.
2. En central riktningsprofil används för billig, bred screening och för en
   representativ visualisering.
3. Innan någon scenarioarkitektur byggs körs ett litet outcome-blind
   känslighetstest: ändrar rimlig riktningsvariation viable set, finalistlista
   eller vinnare när samma SUMO-seeds används?
4. Om 50/50 vinner modellturneringen **och** känslighetstestet visar att
   riktningsaxeln är beslutsirrelevant avslutas dirsplit-utbyggnaden. Då används
   50/50 plus sensor 107:s lokala ankare; ingen ny ensemble-, warm-, API- eller
   UI-arkitektur byggs.
5. Om riktningsvariation påverkar beslutet får nästa minimala gren byggas:
   enkla residualscenarier runt 50/50 om ingen prediktiv signal finns, eller en
   villkorad modell om den faktiskt vinner held-out.
6. SUMO:s egen slump mäts separat med flera **matchade random seeds**. Samma
   seed används för basfallet och varje avstängningsalternativ.
7. Nuvarande q10/q90 behålls som versionsbundna **stressfall**, inte som
   sannolikhetsutsagor, tills deras nominella 80-procentiga intervall har
   validerats utanför träningsdata.
8. Låg observability ska normalt ge bredare osäkerhet, svagare anspråk och
   eventuellt ett `inconclusive`-resultat. Den ska inte automatiskt förbjuda en
   väg. Topologi, framkomlighet och no-detour är däremot hårda säkerhetsgrindar
   oavsett datatäckning.

Detta gör den lilla evidensvägen obligatorisk och den stora produktintegrationen
villkorad. Målet är inte att få en ensemble till varje pris, utan att bygga den
minsta lösning som mätbart förbättrar closure-beslutet.

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

### Den faktiska beslutsytan är liten

Sensor 107 är ensam om att ha två godkända kanter i sensorkartan. Därför är den
ensam om att få en tvåvägstotal delad med dirsplit till två Level-1-mål i
`demand/intake.py::build_targets`. För de övriga fem stationerna går den
uppmätta riktningen in i Level 1 oförändrad. Den uppskattade motsatta
körriktningen används som Level-2-tak och Level-3-prior; taket kan släppas vid
`RUNG_NOBND_TOL1` och priorn vid `RUNG_NOPRIOR_TOL1` innan det uppmätta bandet
vidgas.

Det innebär att riktningsmodellens största direkta Level-1-effekt finns vid
107. Övriga effekter är svagare och måste mätas, inte antas. De kan fortfarande
påverka route pool, PFE och slutlig belastning, vilket routefilernas olika
fordonsantal visar, men hierarkin begränsar deras auktoritet.

### Sensor 107 ska åtgärdas före modellprojektet

Göteborgs publicerade årsankare för 107 är 3 400/3 100, alltså ungefär 52/48
för 2025; 2023–24 ligger omkring 50/50. Det är lokal evidens och ska slå en
norsk transfermodell för samma aggregerade storhet. Men det nuvarande
maskinregistret beskriver bara `measurement_semantics: two_way_total` och säger
att per-slot-splitten estimeras. Ankaret är alltså inte ännu ett spårbart
15-minutersindatafält.

Den omedelbara ändringen ska därför vara liten men korrekt:

- lägg ett provenancebundet `directional_reference` för 107 med år, råa
  riktningstal, källa, kant/riktningsmappning och tidssemantik;
- använd 52/48 som aggregerad lokal centralankare för 2025-struktur;
- tillåt tidsvariation bara där den stöds av data/modell och normalisera så att
  ankaret återfås över sin deklarerade period;
- märk 107:s kvartsvärden som estimerade även när deras periodmedel är lokalt
  förankrat;
- lägg tester som hindrar att ett års-D-factor serialiseras som 96 oberoende
  Level-1-mätningar.

En hårdkodad `0.52` i `build_targets` är inte acceptabel eftersom den skulle
förlora källa, år, riktning och tidssemantik.

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

**Planföljd:** Fas 0B mäter om q-fall och `simulation_seed` kan hållas isär med
ett fristående korsproduktstest. Baslinje och kandidat kör exakt samma par av
stressfall och seed. Ett nytt produktkontrakt med ortogonala
`demand_case_id`/seed-axlar införs bara efter Gate P=`YES`. Befintlig
finalistpolicy har redan fyra initiala repetitioner; den ska återanvändas, inte
implementeras om.

### D-factor stödjer en enkel baseline men inte blind säkerhet

FHWA definierar D-factor som den riktade andelen av dubbelriktad trafik och
noterar att centrala urbana vägar ofta ligger nära 50/50, medan lokala eller
route-specifika mätningar är att föredra. Det stöder 50/50 som en nödvändig
baseline och shrinkage-mål, inte som ett universellt facit.

**Planföljd:** den enklaste modell som klarar held-out-grindarna ska användas.
Mer modellkomplexitet är inte ett produktmål.

## Målsemantik och invariants för en öppnad ensemblegren

Följande begrepp behövs bara om Gate P öppnar produktintegrationen. De
dokumenteras nu för att prototypen ska kunna testas utan semantisk sammanblandning,
men de motiverar inte att schema- eller produktkod skapas i förväg:

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
              lokalt 107-ankare
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
 befintliga q-stressfall     råa riktningspar
          │                         │
          ▼                         ▼
 matched-seed-test          dataset v2 + modellturnering
          │                         │
      Gate S                    Gate M
 beslutskänslighet?       prediktiv signal?
          └────────────┬────────────┘
                       ▼
                fyrfältsbeslut
       ┌───────────────┴────────────────┐
       ▼                                ▼
 Exit A/C: STOPP                  Gren B/D
 central-only/legacy        minimal offline prototyp
                                         │
                                      Gate P
                               ┌─────────┴─────────┐
                               ▼                   ▼
                            STOPP          produktintegration
```

Det finns alltså två normala, lyckade slutlägen: en liten central-only-lösning
och en validerad ensemblelösning. `STOPP` betyder att onödig kod inte byggs;
det betyder inte att forskningen misslyckades.

### Villkorad versionsbunden artefakt

Föreslagen fil **endast om offlineprototypen motiverar den**:
`sumo/direction_ensemble_v2.json`.

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

Schemat och dess produktläsare byggs inte i den ovillkorliga fasen. Om grinden
öppnas har ett probabilistiskt scenario vikt; stressfall saknar vikt. Summan av
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

Turneringen har fyra förregistrerade utfall:

| Punktmodell | Closure-känslighet | Beslut |
|---|---|---|
| 50/50 vinner | ingen materiell påverkan | **Exit A:** avveckla dirsplit som releaseberoende; använd 50/50 plus 107-ankaret. Behåll q-filer endast som legacy/stressdiagnostik. |
| 50/50 vinner | materiell påverkan | **Gren B:** ingen prediktiv ML-modell; pröva en liten residualensemble centrerad på 50/50. |
| Villkorad modell vinner | ingen materiell påverkan | **Exit C:** använd vinnande centralprofil om den har annan produktnytta, men bygg ingen closure-ensemble. |
| Villkorad modell vinner | materiell påverkan | **Gren D:** pröva centralmodell plus residualensemble offline. |

Att 50/50 vinner säger att ett villkorat medelvärde inte har visats bättre. Det
säger inte att den faktiska riktningsandelen saknar spridning. Därför kräver en
exit både modellresultatet och closure-känslighetstestet.

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

### 5. Marginal kalibrering utan falska garantier — endast Gren B/D

Implementera detta bara om riktningsvariation var beslutskänslig. Kalibrera på
held-out residualer, med block per dag/station så
att tidsberoende inte behandlas som oberoende rader. CQR eller block-conformal
kan provas i en forskningsarm, men produktartefakten ska ange:

- `empirical_transfer_coverage`;
- vilka städer, datum och grupper som ingick;
- om målet är interpolation eller extrapolation;
- om täckningen är marginal, gruppvis eller gemensam;
- att en formell exchangeability-garanti saknas när det är fallet.

Nuvarande monotone rearrangement/crossing guard kan behållas, men hela kedjan
inklusive clamp och shrinkage måste valideras efter efterbehandlingen.

### 6. Koherenta dags-scenarier och flerdagars paths — endast Gren B/D

Den första implementerbara metoden, om grinden öppnas, är ett empiriskt
residualbibliotek:

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

### Alltid — billig screening över alla datum och längder

- Använd en central demandprofil och befintliga deterministiska
  topologi-/survivabilitygrindar.
- Screening får rangordna och gallra men inte göra ett obegränsat
  sannolikhetsanspråk.
- En q50-liknande centralprofil kan användas här även när dess intervall inte är
  kalibrerat, så länge den är märkt som point estimate.
- Vägar med låg observability ska inte tas bort enbart därför. De behålls med
  högre osäkerhetsflagga; helt saknade eller semantiskt ogiltiga inputs gör
  resultatet `unavailable` eller tvingar explicit fallback.

### Före produktändring — avgränsat känslighetstest

Använd befintliga q10/q50/q90-routeartefakter som namngivna stressfall och ett
litet fryst urval av closure-kandidater. Kör samma seedlista för varje
stressfall och samma `(stressfall, seed)` för baslinje och kandidat. Detta är en
diagnostisk korsprodukt, inte ett nytt `ScenarioSpec` och inte release-evidens.

Mät om riktningsaxeln ändrar:

- hard-failure/viable-status;
- shortlist/finalist-ID:n;
- vinnare eller tie inom den befintliga praktiska toleransen;
- rangkorrelation och maximum regret;
- runtime och antal ytterligare SUMO-enheter.

Urval, toleranser, seeds och jämförelser fryses före körning. Om ingen materiell
beslutsskillnad uppstår stängs scenariointegrationen och detta negativa resultat
bevaras.

### Endast Gren B/D — finalistensemble

För varje finalist och baslinje:

1. Kör samma probabilistiska scenario paths över hela simuleringshorisonten.
2. Kör samma initiala fyra SUMO-seeds inom varje scenario path.
3. Beräkna parade differenser kandidat minus baslinje.
4. Utöka seeds adaptivt där Monte Carlo-osäkerheten är beslutsrelevant.
5. Utöka scenario paths i förregistrerade batcher, exempelvis 4, 8 och 16, och
   kontrollera om finalistmängd, rangordning och regret stabiliseras.
6. Stoppa med vinnare bara om både scenario- och seedgrindarna passerar.

Batchstorlekarna ovan är högst en shadow-standard för en senare prototyp, inte
en aktiverad releasepolicy. De får inte kodas i monthly- eller warm-statevägen
innan offlinegrinden har passerat. Det slutliga taket bestäms från runtime- och
stabilitetsbenchmark innan en produktregistrering fryses.

### Endast Gren B/D — beslut utan syntetisk värsta värld

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

### Endast efter godkänd offlineprototyp — presentation

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

- god evidens: normal centralprofil; scenarioensemble endast om Gate S/P har
  öppnat den grenen;
- begränsad evidens: varning och svagare anspråk; bredare empiriska scenarier
  endast i en öppnad Gren B/D;
- extrapolation: konservativ fallback/stressfall och normalt
  `inconclusive` om valet påverkas;
- inputfel eller omöjlig vägparning: fail-closed `unavailable`.

Detta undviker det orimliga i att "förbjuda vägar vi vet lite om", samtidigt
som programmet inte låtsas att okunnighet är precision. Rapporten bör också
rangordna vilka nya riktade mätpunkter som mest minskar beslutets osäkerhet
(`value_of_information`), men den analysen får inte använda held-out-utfall
för att välja releasefall.

## Villkorad implementationsordning

### Fas 0A — lokal rättning och nulägeslåsning, alltid

**Kod och artefakter**

- Lägg sensor 107:s `directional_reference` i det validerade sensorregistret,
  inklusive år, råa riktningstal, period, källa och kantmappning.
- Gör minsta möjliga ändring i befintlig dirsplit/predict- eller intakeväg för
  att ankra periodmedlet utan att fabricera kvartsmätningar.
- Lägg fokuserade tester för 107:s total, riktning, provenance och periodmedel.
- Pinna nuvarande q-routefiler, q→seedmapping och closure-ranking i legacytester.

**Ingen ny generell schema- eller product-modul skapas i denna fas.**

**Acceptans**

- 107:s två riktningar summerar varje slot till den uppmätta tvåvägstotalen.
- Deklarerad period återger 52/48 inom avrundningstolerans.
- Kvartsvärden är märkta estimerade, inte Level-1-riktningsmätningar.
- De fem enkelriktade stationernas Level-1-mål är byte-identiska.
- Gamla golden- och resume-artefakter är oförändrade.

### Fas 0B — matched-seed-känslighet, alltid och fristående

Bygg högst ett litet verktyg, exempelvis
`tools/measure_direction_decision_sensitivity.py`. Det ska läsa befintliga
q10/q50/q90-artefakter, köra en fryst liten closurematris och använda samma
seedlista för varje q-fall samt samma par för baslinje/kandidat.

Det får anropa befintliga runners men får inte ändra `ScenarioSpec`, monthly-,
warm-state-, API- eller UI-kontrakt. Befintlig finalistkod har redan parade
bas/kandidatobservationer och fyra initiala repetitioner; fasen mäter den
kvarvarande sammanblandningen mellan q-fall och seed.

**Förregistrera** vägar, datum, kandidater, seeds, timeout, jämförelsemått och
materiell tolerans före SUMO-körningen. Spara registration/outcome append-only
med `release_evidence: false`.

**Gate S — är riktning beslutsrelevant?**

- `NO`: samma hard failures, viable set, finalistmängd och vinnare/tie inom
  förregistrerad tolerans. Scenario- och produktintegration stängs.
- `YES`: minst ett förregistrerat beslutsfält ändras materiellt. Endast då kan
  Gren B/D senare öppnas.
- `INCONCLUSIVE`: timeout, bristande health eller för få matchade observationer.
  Ingen produktintegration; reparera endast mätbarheten och kör en ny fryst
  version utan att välja fall från utfallet.

### Fas 1 — dataset v2 och modellturnering, alltid men lokalt i `dirsplit/`

Ändra i första hand befintliga `dirsplit/dataset.py`, `train.py`, `predict.py`
och `coverage.py`. Lägg högst en gemensam evalueringsmodul om det annars skulle
duplicera foldlogik; skapa inte separata schema-, models-, calibration- och
scenariofamiljer i förväg.

Arbetet ska:

1. bevara råa station–datum–timme-antal och `day_block_id`;
2. eliminera paired-total/single-direction feature-mismatch;
3. jämföra 50/50, shrunk D-factor, LightGBM och count-model på samma folds;
4. rapportera central skillnad med osäkerhet, inte bara punkt-MAE;
5. mäta temporal/applicability-support och uttryckligen neka tyst helg- och
   off-hours-extrapolation.

**Gate M — finns en robust prediktiv riktningssignal?**

- `BASELINE`: 50/50 är bäst eller statistiskt oskiljbar från bästa komplexa
  modell enligt den frysta regeln.
- `MODEL`: en villkorad modell vinner robust och utan materiell huvudgruppsskada.
- `INCONCLUSIVE`: leakage, otillräckliga oberoende block eller instabil ranking.
  Behåll nuvarande releaseväg som legacy och åtgärda endast evidensfelet.

Kombinera Gate S och Gate M enligt fyrfältstabellen i modellavsnittet. Exit A
och Exit C är fullvärdiga slutresultat och stoppar resten av planen.

### Fas 2 — minimal offline scenario-prototyp, endast Gren B/D

Denna fas får börja bara när Gate S=`YES` och Gate M är avgjord. Implementera
residualbiblioteket i högst en ny `dirsplit/scenarios.py` plus ett fokuserat
test. Prototypen skriver en diagnostisk valideringsartefakt, inte ett
produktionsmanifest, och körs genom det fristående verktyget från Fas 0B.

- Gren B centrerar residualerna på 50/50, med 107:s lokala periodankare.
- Gren D centrerar dem på den vinnande villkorade modellen.
- Båda jämförs mot central-only och de befintliga q-stressfallen.
- Inga ändringar görs ännu i monthly, warm-state, `ScenarioSpec`, serve eller UI.

**Gate P — förtjänar prototypen produktkod?**

Alla följande måste passera på fryst evidens:

1. marginal kalibrering/skärpa är bättre än eller kompletterar baseline utan
   falsk täckningsetikett;
2. tids-/vägberoendet klarar de förregistrerade joint-score-kontrollerna;
3. closure-beslutet är stabilt när scenarioantalet ökas;
4. ensemblebeslutet förbättrar ett definierat beslut eller visar att
   central-only är otillräckligt;
5. runtime och lagring ryms inom befintliga resursgränser.

Ett `NO` eller `INCONCLUSIVE` stoppar produktintegrationen och bevaras som
negativ evidens.

### Fas 3 — minsta produktintegration, endast efter Gate P=`YES`

Först nu får ett versionsbundet `DemandEnsembleManifest` och explicit
`SimulationMember(demand_case_id, seed)` införas. Gör integrationen vertikalt:

1. demand resolver och lazy/content-adresserad routebyggnad för en fryst
   endags-shadow;
2. cold finalist runner med samma case×seed för baslinje/kandidat;
3. komplett scenario-kostnad utan field-wise splicing;
4. flerdagars path/resume;
5. API/UI efter att CLI/shadow är differentialtestad;
6. warm-state sist och endast efter egen cold-vs-warm-ekvivalens för det nya
   case×seed-kontraktet.

Ändra bara moduler som den vertikala slicen faktiskt når. En generell omskrivning
av `monthly_search`, `monthly_sumo`, `pilot_selection`, `independent_daily`,
`deterministic_disruption`, ranking och warm-state i samma PR är förbjuden.

**Acceptans per slice**

- legacy q-arkiv är fortsatt läsbara och byte-identiska;
- central-only reproducerar legacy q50 eller redovisar avsiktlig differens;
- samma seedlista används för varje case och baslinje/kandidat;
- paus/cap före konvergens kan inte publicera vinnare;
- flermånaderssökningen bygger bara scenario-routes för finalister;
- API/UI läggs inte till innan CLI-resultatet har en sanningsenlig statusmodell.

### Fas 4 — aktivering, endast efter separata shadow- och held-out-grindar

Frys modell, cases, seeds, riskregel, budgets, testdatum och closurefall före
körning. Kör shadow, restart/provenance, scenario-/seedkonvergens och därefter
ett orört held-out endast om alla föregående grindar passerar.

Aktivera inte en ensemble bara för att den byggts eller är bredare. Den måste
ge bättre kalibrerad beslutskvalitet inom runtimegränsen. Ett negativt utfall
stänger aktivering utan att raderas eller följas av en försvagad gate.

## Testmatris

Ovillkorliga testgrupper:

- sensor 107: råa 3 400/3 100, kantorientering, periodmedel, totalbevarande och
  provenance;
- legacy: befintliga q-filer, ranking och releaseartefakter oförändrade;
- känslighet: samma seedkorsprodukt, samma bas/kandidatpopulation, fryst urval
  och deterministisk Gate S-klassning;
- dataset: råa antal, DST, helg, missingness, day blocks och leakage;
- features: paired/single-direction semantik och nya sensorer;
- modell: foldisolering, determinism, baselinejämförelse och shrinkage;
- observability: temporal support, feature-semantic och effective sample size.

Endast Gren B/D:

- intervall: pinball, coverage, rearrangement, clamp och group reports;
- scenario: parsumma, tids-/vägberoende, vikter, stressfall och digests.

Endast efter Gate P=`YES`:

- demand: lazy build, cacheinvalidisering och legacy q-adapter;
- simulation: case×seed-korsprodukt, common random numbers och matched pair;
- ranking: ingen field-wise splicing, hard safety och inconclusive;
- monthly: flerdagars scenarioidentitet över månadsskifte och resume;
- API/UI: truthful labels, progress, reload och claims;
- golden: gammal policy oförändrad, ny policy versionsbunden.

## Forsknings- och evidensartefakter

Ovillkorliga append-only-filer:

- `validation/direction_decision_sensitivity_registration_v1.json`;
- `validation/direction_decision_sensitivity_outcome_v1.json`;
- `validation/dirsplit_point_benchmark_v1.json`;

Endast Gren B/D:

- `validation/dirsplit_interval_calibration_v1.json`;
- `validation/dirsplit_joint_scenario_benchmark_v1.json`;

Endast efter Gate P=`YES`:

- `validation/closure_uncertainty_shadow_registration_v1.json`;
- `validation/closure_uncertainty_shadow_outcome_v1.json`;
- `validation/closure_uncertainty_heldout_registration_v1.json`;
- motsvarande outcome endast efter godkända föregående grindar.

Alla ska innehålla source-, data-, model-, network- och policy-digests,
plattform, SUMO-version, seeds, scenario-ID:n, start/slut, komplett status och
`release_evidence`.

## Det som uttryckligen inte ska göras

- Kör inte en enda q50/seed och kalla resultatet säkert eller robust.
- Bygg inte schema-, monthly-, warm-state-, API- eller UI-integration innan
  Gate S, Gate M och Gate P har öppnat motsvarande gren.
- Tolka inte "50/50 vinner" som "variansen är noll"; använd både Gate M och
  Gate S för exit.
- Hårdkoda inte 107 till 0,52 per kvart; bevara års-/periodsemantiken.
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

Planen har flera tillåtna definitioner av done.

### Done för alla utfall

1. Sensor 107:s lokala periodankare är maskinläsbart, provenancebundet och
   används utan falsk kvartsmätningssemantik.
2. Gate S är förregistrerad och avgjord med samma seeds över q-stressfallen.
3. Centralprofilen väljs mot enkla baselines på läckagefri held-out i Gate M.
4. Observability beskriver evidensstyrka utan ett godtyckligt vägförbud.
5. Legacy q- och closureartefakter är oförändrade.

### Done för Exit A/C

Gatekombinationen är dokumenterad, scenario-/produktintegrationen är uttryckligt
stängd och den minsta central-only-lösningen är vald. Inga oanvända schema-,
monthly-, warm-, API- eller UI-moduler har skapats. Detta är ett lyckat slut.

### Done för Gren B/D-prototyp

Marginaler och koherenta dags-/flerdagarsscenarier är validerade offline, Gate P
är avgjord och negativ evidens stoppar arbetet om den inte passerar.

### Done för produktensemble, endast efter Gate P=`YES`

Demand case och SUMO-seed är separata; finalister kör matchade par; ranking
använder kompletta scenarier; flermånadersrutter byggs lazy; CLI/API/UI redovisar
status sanningsenligt; shadow och held-out passerar utan försvagad gate; och
aktivering sker i en separat versionsändring.
