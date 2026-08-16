# Plan för dirsplit, efterfrågeosäkerhet och korrekt användning i vägavstängning

**Datum:** 2026-08-13
**Reviderad:** 2026-08-16 mot mätt evidens — se
`docs/reviews/DIRSPLIT_PLAN_RESEARCH_REVIEW_2026-08-16.md` och de tre
reproducerbara verktygen `tools/research_direction_split_evidence.py`,
`tools/research_direction_sum_constraint.py` och
`tools/research_direction_solution_space.py`.
**Status:** Forsknings- och implementationsplan. Ingen ny modell eller policy är
aktiverad av detta dokument.
**Gäller omedelbart:** sensor 107:s lokala ankare, `dirsplit/` dataset/modellval
och en ovillkorlig intervallrättning.
**Villkorad senare omfattning:** demand-byggaren, scenarioavtal,
closure-screening, SUMO-finalister, warm-state, API och webbgränssnitt får bara
ändras om de explicita beslutsgaterna nedan passerar.
**Bakåtkompatibilitet:** Befintliga q10/q50/q90-arkiv och frysta
releaseartefakter är historisk evidens och får inte skrivas om.

## Vad revisionen 2026-08-16 ändrade

Planens revisionsfynd reproducerades exakt (`sumo/direction_split.json`:
median |q50−0,5| 0,0070, max 0,0340, medianbredd 0,1070). Fem saker mättes
sedan som planen inte kunde veta, och de ändrar ordningen på arbetet:

1. **Ankaret dominerar modellen med 3:1.** Att predicera en hållen station med
   dess EGET medelvärde — exakt motsvarigheten till stadens publicerade
   årsvärde för 107 — ger +22,7 % mot 50/50. Hela transferapparaten ger
   +7,0 % ovanpå det. Planen behandlar detta som ett modelleringsproblem;
   det är först ett dataanskaffningsproblem.
2. **Turneringens vinnare fanns inte i den ursprungliga fyrfältstabellen.** Den
   är varken 50/50 eller en gatubetingad modell, utan en OBETINGAD
   tidsvarierande kurva. Tabellen är utökad till sex fält nedan.
3. **Intervallet är en aktiv defekt, inte en etikettfråga.** Det nominella
   80 %-intervallet har uppmätt 47,0 % täckning. Det matar kartans
   confidence-tal idag och får inte vänta på någon grind.
4. **"Låt entropin välja" finns inte som alternativ.** En summa-constraint
   skalar båda körriktningarna med samma faktor; splitten bestäms helt av
   kandidatpoolen, vars implicerade värde svänger 35 procentenheter.
5. **Två till familjer är falsifierade** — korridorkontinuitet från 1076 och
   profildekonvolution (den familj `estimate_directions.py` tillhör).

Gate S är fortfarande omätt. Inget här påstår att riktning påverkar ett
avstängningsbeslut.

## Beslut i korthet

Programmet ska inte välja mellan "en enda simulering" och "tre kvantiler" som
om de vore samma sorts lösning. De svarar på olika frågor:

1. **Det lokala ankaret är den enskilt viktigaste inputen.** Sensor 107:s
   publicerade 2025-ankare 52,3/47,7 ska maskinbindas med rätt tidssemantik,
   med källa, år OCH den verifierade kant/riktningsmappningen. Det får inte
   behandlas som 96 uppmätta kvartsvärden. Mätt värde: +22,7 % mot 50/50.
2. **Nästa steg är att leta fler lokala ankare, inte att bygga en bättre
   modell.** Göteborgs publika trafikmängder-katalog kan bära riktade rader
   för de övriga fem stationerna eller för närliggande gator. Det är en
   publik katalog och inte en dataförfrågan, så den berör inte beslutet från
   2026-07-20 — men bekräfta den tolkningen med Gustav innan den används.
3. **Centralprofilen är ankare × pooled tidvattenkurva**, sammansatt på
   logitskalan så att ankaret återges exakt. Kurvan byggs över HELA dygnet,
   inte träningsfönstret 06–20. Den används för billig bred screening och för
   en representativ visualisering.
4. **Intervallrättningen är ovillkorlig.** Det nominella 80 %-intervallet har
   uppmätt 47,0 % täckning och ärlig bredd 0,193 mot utlagda 0,099. Antingen
   vidgas det till 0,193 eller så märks det `stress_only`. Detta väntar inte
   på Gate S, Gate M eller Gate P — det matar kartans confidence-tal idag.
5. Innan någon scenarioarkitektur byggs körs ett litet outcome-blind
   känslighetstest: ändrar rimlig riktningsvariation viable set, finalistlista
   eller vinnare när samma SUMO-seeds används? **Det måste köras på det ärliga
   bandet ±0,0965, inte på de utlagda q-artefakterna** — de spänner halva
   bredden och skulle underskatta känsligheten per konstruktion.
6. Om 50/50 vinner modellturneringen **och** känslighetstestet visar att
   riktningsaxeln är beslutsirrelevant avslutas dirsplit-utbyggnaden. Då används
   50/50 plus sensor 107:s lokala ankare; ingen ny ensemble-, warm-, API- eller
   UI-arkitektur byggs.
7. Om riktningsvariation påverkar beslutet får nästa minimala gren byggas:
   enkla residualscenarier runt centralprofilen om ingen prediktiv signal
   finns, eller en villkorad modell om den faktiskt vinner held-out.
8. SUMO:s egen slump mäts separat med flera **matchade random seeds**. Samma
   seed används för basfallet och varje avstängningsalternativ.
9. Låg observability ska normalt ge bredare osäkerhet, svagare anspråk och
   eventuellt ett `inconclusive`-resultat. Den ska inte automatiskt förbjuda en
   väg. Topologi, framkomlighet och no-detour är däremot hårda säkerhetsgrindar
   oavsett datatäckning.
10. **Riktningsosäkerhet bor MELLAN lösningar, aldrig inuti en.** En PFE-lösning
    returnerar ett tal; det finns ingen representation av "vi vet inte" inuti
    den. Osäkerheten hör hemma i demand-varianterna, vilket q-variantarkitekturen
    redan gör — dess enda fel är bredden.

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

### Uppmätt 2026-08-16 — vad som nu är avgjort

Turneringen i avsnitt 3 nedan kördes i förväg på samma spårade tabell, med
allt anpassat innanför respektive fold:

| modell | LCO alla | LCO domän | leave-station-out | λ |
|---|---:|---:|---:|---:|
| 50/50 | 0,0639 | 0,0569 | 0,0639 | — |
| **pooled timkurva** | **0,0604** | **0,0532** | **0,0608** | 0,93–0,98 |
| LightGBM, enkel | 0,0686 | 0,0618 | 0,0687 | 0,21–0,41 |
| LightGBM, similarity-viktad (utlagd) | 0,0670 | 0,0609 | 0,0679 | 0,23–0,44 |

En obetingad timkurva UTAN gatufeatures vinner på varje folddesign; varje
LightGBM-variant förlorar mot 50/50 på varje folddesign. Shrinkage-λ förklarar
varför: kurvan behåller nästan hela sin signal, LightGBM en fjärdedel. Den
utlagda λ=0,289 var aldrig en tuningdetalj — det var valideringen som korrekt
raderade brus.

Nivå kontra form, och varför ordningen i planen måste vändas:

| estimator | MAE | mot 50/50 | mot ankaret |
|---|---:|---:|---:|
| 50/50 | 0,0639 | — | −29,4 % |
| pooled kurva utan ankare | 0,0603 | +5,5 % | −22,2 % |
| **lokalt ankare, platt** | **0,0494** | **+22,7 %** | — |
| **lokalt ankare + pooled form** | **0,0459** | **+28,1 %** | **+7,0 %** |

Replikerat leave-station-out (+22,7 % och +7,2 %). Formens tillskott är
statistiskt reellt — parad bootstrap 95 % KI [+0,0020, +0,0048] — men det är
den mindre halvan med faktor tre.

Intervallet, mätt: samma kvantilarkitektur omanpassad leave-city-out ger
**47,0 % täckning** för ett nominellt 80 %-intervall. Ärlig bredd 0,193 mot
utlagda 0,099. Eftersom raderna redan är ~8-dagarsmedelvärden är 47 % en ÖVRE
gräns för endagstäckning.

### Falsifierade familjer — återföreslå dem inte

Fyra ytterligare vägar testades och stängdes. De dokumenteras här så att de
inte återuppfinns:

| familj | utfall | evidens |
|---|---|---|
| Summa-constraint, "låt entropin välja" | falsifierad | `groups` läggs till `bounds_items`, vars korrigering multiplicerar VARJE medlemsrutt med samma faktor. En summa bär ingen information om splitten; utfallet blir exakt `n_A/(n_A+n_B)`. Poolens implicerade split vid 107 spänner 0,230–0,581 (35 pe) enbart på sigma-ratten. |
| Summa + tvåsidigt level-2-band | falsifierad | Blir poolsoberoende — men landar deterministiskt på `lo_A/(lo_A+lo_B)`, bandets nedre hörn, inte dess mitt. Ett förklätt punktestimat. |
| Korridorkontinuitet 1076 → 107 | falsifierad | 1076 mäter södergående Skånegatan 257 m från 107. Kvoten ger 0,677 mot publicerade 0,477, och 1076 överstiger 107:s TVÅVÄGSTOTAL i 7,9 % av kvartarna. Flöde tillkommer mellan stationerna. |
| Profildekonvolution (familjen `estimate_directions.py`) | falsifierad två gånger | Anpassning av `107_total = a·P_i + b·mirror(P_j)` mot de fem lokalt mätta enkelriktade profilerna ger implicerade N-andelar 0,878–1,034 vid R² 0,93–0,98; över 1,0 är inte fysiskt. Kontrollen avgör: den speglade basen slår en ospeglad i **0 av 10** par. Göteborgs egna data förkastar motfas-premissen — vilket är exakt varför den gamla AM/PM-gaussianen gav 80/20. |

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
- **skriv in den verifierade riktningsmappningen explicit**: kanten
  `60786979_3575001205_0` har bäring 352,1° och är NORRGÅENDE och den mot
  centrum riktade kanten (radial_cos +0,61); `1455801464_18241874_0` är
  174,4°, södergående. Katalogens N-rad är alltså samma fysiska riktning som
  "mot centrum". Utan den raden kan 52/48 appliceras baklänges, och ingenting
  nedströms skulle fånga en 4,6-procentenheters teckenfel;
- använd 52,3/47,7 som aggregerad lokal centralankare för 2025-struktur;
- tillåt tidsvariation bara där den stöds av data/modell och normalisera så att
  ankaret återfås över sin deklarerade period. Den mätta sammansättningen är en
  enda logit-offset (+0,1166) som återger ankaret exakt och bevarar
  tidvattenamplituden (0,098 mot 0,099);
- märk 107:s kvartsvärden som estimerade även när deras periodmedel är lokalt
  förankrat;
- lägg tester som hindrar att ett års-D-factor serialiseras som 96 oberoende
  Level-1-mätningar.

En hårdkodad `0.52` i `build_targets` är inte acceptabel eftersom den skulle
förlora källa, år, riktning och tidssemantik.

**Uppgraderad prioritet 2026-08-16.** Detta avsnitt hette tidigare "före
modellprojektet" i betydelsen ordningsföljd. Mätningen visar att det är
viktigare än så: ankaret bär +22,7 % och hela modellprojektet +7,0 % ovanpå.
Rubriken gäller alltså inte bara sekvens utan storleksordning — och den
motiverar punkt 2 i "Beslut i korthet", att leta fler lokala ankare i stadens
publika katalog innan mer modelleringsarbete görs.

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

Reviderad 2026-08-16: turneringen flyttad före SUMO-testet, och det ärliga
bandet — inte de utlagda q-filerna — matar Gate S.

```text
        lokalt 107-ankare  ────────────►  +22,7 %   (dominerande termen)
         (+ ev. fler ur stadens katalog)
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
 ovillkorlig intervall-      råa riktningspar
 rättning (47 % → 0,193)             │
          │                          ▼
          │              dataset v2 + modellturnering
          │                          │
          │                      Gate M
          │                 prediktiv signal?
          │                          │
          │        centralprofil = ankare × pooled kurva
          │                          │
          └────────────┬─────────────┘
                       ▼
          matched-seed-test på ±0,0965
                       │
                    Gate S
             beslutskänslighet?
                       ▼
                 sexfältsbeslut
       ┌───────────────┴────────────────┐
       ▼                                ▼
 Exit A/C/E: STOPP                Gren B/B′/D
 centralprofil utlagd        minimal offline prototyp
                                         │
                                      Gate P
                               ┌─────────┴─────────┐
                               ▼                   ▼
                            STOPP          produktintegration
```

Det finns alltså två normala, lyckade slutlägen: en liten central-only-lösning
och en validerad ensemblelösning. `STOPP` betyder att onödig kod inte byggs;
det betyder inte att forskningen misslyckades. Efter mätningen 2026-08-16 är
**Exit E det mest sannolika utfallet**, och det är ett bra utfall.

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

Turneringens utfallsrum hade ett hål. Fyrfältstabellen antog att punktmodellen
antingen är 50/50 eller gatubetingad. **Den uppmätta vinnaren är ingendera** —
den är en obetingad TIDSVARIERANDE kurva, alltså kandidat 2 utan gatufeatures.
Som ursprungligen förregistrerad hade det ärliga resultatet tvingats in i
`BASELINE` och sedan lästs som "riktning saknar signal". Riktning HAR signal;
den är bara inte gatuspecifik. Utfallsrummet får därför en femte rad:

| Punktmodell | Closure-känslighet | Beslut |
|---|---|---|
| 50/50 vinner | ingen materiell påverkan | **Exit A:** avveckla dirsplit som releaseberoende; använd 50/50 plus 107-ankaret. Behåll q-filer endast som legacy/stressdiagnostik. |
| 50/50 vinner | materiell påverkan | **Gren B:** ingen prediktiv ML-modell; pröva en liten residualensemble centrerad på 50/50. |
| **Obetingad tidskurva vinner** *(uppmätt utfall)* | ingen materiell påverkan | **Exit E:** lägg ut ankare × pooled kurva som centralprofil, ovillkorlig intervallrättning, och stoppa där. Ingen ensemble, ingen ny arkitektur. Detta är ett fullvärdigt slut. |
| **Obetingad tidskurva vinner** | materiell påverkan | **Gren B′:** residualensemble centrerad på ankare × kurva i stället för på 50/50. |
| Villkorad modell vinner | ingen materiell påverkan | **Exit C:** använd vinnande centralprofil om den har annan produktnytta, men bygg ingen closure-ensemble. |
| Villkorad modell vinner | materiell påverkan | **Gren D:** pröva centralmodell plus residualensemble offline. |

Att 50/50 vinner säger att ett villkorat medelvärde inte har visats bättre. Det
säger inte att den faktiska riktningsandelen saknar spridning. Därför kräver en
exit både modellresultatet och closure-känslighetstestet.

**Turneringen är i praktiken redan körd** (se "Uppmätt 2026-08-16" ovan) på den
spårade aggregerade tabellen. Gate M kan därför avgöras billigt. Vad som
kvarstår för en fullständig Gate M är dataset v2:s råa dag-nivå — den påverkar
inte rangordningen mellan kandidaterna, men den behövs innan något intervall
får kallas kalibrerat.

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

**Ovillkorlig del (ny 2026-08-16).** Oavsett hur någon grind faller måste den
utlagda intervallbredden rättas, eftersom `edge_shares_q10/q90` bygger de
demand-varianter vars Monte Carlo-spridning blir kartans `confidence`-tal
idag. Uppmätt täckning är 47,0 % mot nominella 80 %; ärlig bredd 0,193 mot
utlagda 0,099. Två godtagbara åtgärder, båda små:

- vidga till den uppmätta bredden 0,193 och behåll etiketten som EMPIRISK,
  inte garanterad; eller
- behåll bredden men märk fältet `calibration_status: stress_only` och sluta
  presentera det som ett 80 %-intervall någonstans i kedjan.

Det som inte är godtagbart är att lämna ett nominellt 80 %-intervall med 47 %
täckning inkopplat i confidence-talet i väntan på Gate P. Notera också att
47 % är en ÖVRE gräns: raderna är redan ~8-dagarsmedelvärden, så
endagstäckningen är sämre.

**Villkorad del — endast Gren B/D.** Full conformal-/blockkalibrering
implementeras bara om riktningsvariation var beslutskänslig. Kalibrera på
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

**METODFEL RÄTTAT 2026-08-16.** Detta avsnitt föreskrev ursprungligen att
använda de BEFINTLIGA q10/q50/q90-routeartefakterna som stressfall. De spänner
0,107 — ungefär halva den ärliga bredden 0,193. Ett Gate S-test på dem skulle
underskatta riktningskänsligheten **per konstruktion** och kunna returnera `NO`
av fel skäl, vilket sedan skulle stänga scenariointegrationen på ogiltig grund.

Gate S ska därför köras på det ärliga bandet: centralprofilen ±0,0965, alltså
riktningsandelar runt centralprofilen som faktiskt motsvarar den uppmätta
osäkerheten. Om de gamla q-filerna används av bekvämlighetsskäl måste de först
skalas till den bredden, och det måste stå i registreringen.

Använd ett litet fryst urval av closure-kandidater. Kör samma seedlista för
varje stressfall och samma `(stressfall, seed)` för baslinje och kandidat. Detta
är en diagnostisk korsprodukt, inte ett nytt `ScenarioSpec` och inte
release-evidens.

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

**ORDNINGEN ÄNDRAD 2026-08-16.** Planen lade Fas 0B (matched-seed-SUMO) före
Fas 1 (turneringen). Det är fel väg runt: Fas 1 är nästan gratis — merparten
kördes på minuter mot spårade artefakter — medan Fas 0B är den dyra fasen som
kräver SUMO. Att köra den billiga grinden först hade dessutom avslöjat 47 %-
täckningen innan någon SUMO-tid spenderades, och det är just den siffran som
avgör vilket band Fas 0B ska köras på. Ordningen är därför:

**Fas 0A → Fas 1 → Fas 0B → (Fas 2 → Gate P → Fas 3 → Fas 4)**

Fas 0B behåller sitt namn för spårbarhet mot registreringsartefakterna.

### Fas 0A — lokal rättning och nulägeslåsning, alltid

**Kod och artefakter**

- Lägg sensor 107:s `directional_reference` i det validerade sensorregistret,
  inklusive år, råa riktningstal, period, källa och kantmappning — med den
  verifierade bäringen (`60786979_3575001205_0` = 352,1° = N = mot centrum)
  utskriven, inte underförstådd.
- Gör minsta möjliga ändring i befintlig dirsplit/predict- eller intakeväg för
  att ankra periodmedlet utan att fabricera kvartsmätningar.
- **Rätta intervallbredden** enligt avsnitt 5:s ovillkorliga del — vidga till
  0,193 eller märk `stress_only`. Detta hör hit därför att det inte beror på
  någon grind och därför att det påverkar en utlagd produkt.
- Lägg fokuserade tester för 107:s total, riktning, provenance och periodmedel.
- Pinna nuvarande q-routefiler, q→seedmapping och closure-ranking i legacytester.

**Ingen ny generell schema- eller product-modul skapas i denna fas.**

**Acceptans**

- 107:s två riktningar summerar varje slot till den uppmätta tvåvägstotalen.
- Deklarerad period återger 52,3/47,7 inom avrundningstolerans.
- Riktningsmappningen är testad så att ett omkastat ankare failar.
- Kvartsvärden är märkta estimerade, inte Level-1-riktningsmätningar.
- De fem enkelriktade stationernas Level-1-mål är byte-identiska.
- Inget nominellt 80 %-intervall är kvar med 47 % täckning i confidence-kedjan.
- Gamla golden- och resume-artefakter är oförändrade.

**Parallellt, utanför kod (högsta hävstång, se "Beslut i korthet" punkt 2):**
kontrollera Göteborgs publika trafikmängder-katalog för riktade rader vid de
övriga fem stationerna eller närliggande gator. Varje nytt lokalt ankare är
värt ungefär tre gånger vad hela transfermodellen är värd. Bekräfta först med
Gustav att en publik katalog inte omfattas av 2026-07-20-beslutet.

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

**Gate M — finns en robust prediktiv riktningssignal, och av vilket slag?**

Tre utfall räckte inte: de skilde inte på "ingen signal" och "signal som inte
är gatuspecifik". Fyra utfall:

- `BASELINE`: 50/50 är bäst eller statistiskt oskiljbar från allt annat.
- `UNCONDITIONAL`: en obetingad tidsvarierande kurva vinner, men ingen
  gatubetingad modell slår den. **Detta är det uppmätta utfallet** — se
  "Uppmätt 2026-08-16": pooled timkurva 0,0604/0,0532/0,0608 mot 50/50:s
  0,0639/0,0569/0,0639, medan varje LightGBM-variant förlorar mot 50/50 på
  varje folddesign.
- `MODEL`: en villkorad modell vinner robust och utan materiell huvudgruppsskada.
- `INCONCLUSIVE`: leakage, otillräckliga oberoende block eller instabil ranking.
  Behåll nuvarande releaseväg som legacy och åtgärda endast evidensfelet.

Oavsett utfall gäller att det lokala ankaret läggs ut — det konkurrerar inte
med kurvan utan multipliceras med den, och det bär den större delen (+22,7 %
mot +7,0 %).

Kombinera Gate S och Gate M enligt sexfältstabellen i modellavsnittet. Exit A,
Exit C och Exit E är fullvärdiga slutresultat och stoppar resten av planen.

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
- `validation/dirsplit_falsified_families_v1.json` — de fyra stängda vägarna
  (summa-constraint, summa+band, korridorkontinuitet, profildekonvolution) med
  sina mätvärden, så att negativ evidens överlever och inte återuppfinns.

Reproducerbarhet: de tre verktygen `tools/research_direction_split_evidence.py`,
`tools/research_direction_sum_constraint.py` och
`tools/research_direction_solution_space.py` kör enbart mot spårade artefakter
och återger varje siffra ovan. De är evidenskällan för revisionen 2026-08-16;
`release_evidence: false`.

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
- **Applicera inte ankaret utan den verifierade riktningsmappningen.** N är
  kanten med bäring 352,1°. Ett omkastat ankare är ett 4,6-procentenheters fel
  som ingenting nedströms fångar.
- **Kör inte Gate S på de utlagda q-artefakterna.** De spänner halva den
  ärliga bredden och skulle ge `NO` av fel skäl.
- **Föreslå inte "låt entropin/PFE:n välja splitten" igen.** En summa-constraint
  skalar båda riktningarna uniformt; splitten blir kandidatpoolens
  sammansättning, som svänger 35 procentenheter på en ruttparameter. Detsamma
  gäller reparationen med ett tvåsidigt band — den blir ett förklätt
  punktestimat på bandets nedre hörn.
- **Återuppfinn inte profildekonvolution eller AM/PM-nedbrytning.** Motfas-
  premissen är förkastad på Göteborgs egna data (speglad bas vinner 0/10).
- **Anta inte att 1076 ger 107:s split** för att gatorna är desamma; 1076
  överstiger 107:s tvåvägstotal i 7,9 % av kvartarna.
- **Skjut inte upp intervallrättningen bakom en grind.** Den är ovillkorlig.
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
   används utan falsk kvartsmätningssemantik — inklusive den verifierade
   riktningsmappningen, testad så att ett omkastat ankare failar.
2. Intervallrättningen är gjord: inget nominellt 80 %-intervall med 47 %
   uppmätt täckning matar längre kartans confidence-tal.
3. Gate S är förregistrerad och avgjord med samma seeds, **på det ärliga
   bandet ±0,0965**.
4. Centralprofilen väljs mot enkla baselines på läckagefri held-out i Gate M,
   med den obetingade tidskurvan som en förregistrerad kandidat i sin egen rätt.
5. Observability beskriver evidensstyrka utan ett godtyckligt vägförbud.
6. De falsifierade familjerna är dokumenterade som negativ evidens så att de
   inte återföreslås.
7. Legacy q- och closureartefakter är oförändrade.

### Done för Exit A/C/E

Gatekombinationen är dokumenterad, scenario-/produktintegrationen är uttryckligt
stängd och den minsta centralprofilen är vald och utlagd. För Exit E betyder det
konkret: ankare × pooled kurva över hela dygnet, ärligt intervall över
demand-varianter, LightGBM-stacken avvecklad. Inga oanvända schema-, monthly-,
warm-, API- eller UI-moduler har skapats. Detta är ett lyckat slut.

### Done för Gren B/D-prototyp

Marginaler och koherenta dags-/flerdagarsscenarier är validerade offline, Gate P
är avgjord och negativ evidens stoppar arbetet om den inte passerar.

### Done för produktensemble, endast efter Gate P=`YES`

Demand case och SUMO-seed är separata; finalister kör matchade par; ranking
använder kompletta scenarier; flermånadersrutter byggs lazy; CLI/API/UI redovisar
status sanningsenligt; shadow och held-out passerar utan försvagad gate; och
aktivering sker i en separat versionsändring.
