# Åtgärdsplan: riktningsdelning, osäkerhet och q10/q90

**Datum:** 2026-08-16
**Status:** Mätt evidens + åtgärdsplan. Fas A är landad i samma commit som detta
dokument. Ingen produktkod, ingen modell och ingen policy är ändrad.
**Förhåller sig till:** `docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`
(nedan "augustiplanen"). Detta dokument **ersätter den inte** — det besvarar
Gate M för den deployade modellen och gör om resten av dess villkorade grenar
till konkreta faser med tester.
**Bakåtkompatibilitet:** Befintliga q10/q50/q90-arkiv, frysta releaseartefakter
och stängda closure-grindar är historisk evidens och skrivs inte om. Allt nedan
gäller nya byggen.

---

## 1. Sammanfattning

Augustiplanen ställde tre frågor och byggde grindar för dem. Den här planen
besvarar en av dem med mätning:

| Grind | Fråga | Status efter denna plan |
|---|---|---|
| **Gate M** | Slår någon punktmodell 50/50 på held-out? | **BASELINE** — nej, för den deployade modellen. Mätt. |
| **Gate S** | Ändrar rimlig riktningsvariation closure-beslutet? | **ÖPPEN** — Fas C mäter det. |
| **Gate P** | Är en produktensemble värd sin arkitektur? | **STÄNGD** tills Gate S öppnar den. |

Två fynd utöver Gate M, båda nya:

- **Intervallet är kraftigt under-dispersivt.** `[q10, q90]` täcker **39,3 %**
  av held-out-observationerna där namnet lovar 80 %. Etiketterna får inte
  skeppas som de är.
- **Kernelcentrum ligger utanför träningsstödet för 4 av 6 sensorer**, och de
  "per-sensor-lokala" modellerna använder 68–91 % av hela träningsmängden.
  Båda undergräver `CLAUDE.md`:s beskrivning av metoden.

Rekommenderad väg: **Fas B → Fas C → beslutsgren**. Bygg ingen ensemble-,
schema-, API- eller UI-arkitektur innan Gate S har svarat.

---

## 2. Mätt evidens

Allt nedan är reproducerbart:

```bash
python3 -m dirsplit.validate --boot 2000 --out validation/dirsplit_gate_m_20260816.json
```

Körtid ~12 min på 10 kärnor. Artefakt:
`validation/dirsplit_gate_m_20260816.json`. Harness: `dirsplit/validate.py`,
enhetstestad i `tests/test_dirsplit_validate.py` (22 tester).

Harnesset använder `dirsplit.train`:s **egna** funktioner (`load_table`,
`kernel_weights`, `make_model`, `target_static_features`) så att pipelinen är
identisk med träningen, och reproducerar `train_report.json`:s publicerade
siffror exakt innan det mäter något nytt.

### 2.1 Replikationskontroll

| | `train_report.json` | Harness | |
|---|---|---|---|
| λ | 0,289 | 0,289 | ✅ |
| Poolad MAE, rå | 0,0641 | 0,0641 | ✅ |
| Poolad MAE, krympt | 0,0557 | 0,0557 | ✅ |
| Poolad MAE, 50/50 | 0,0565 | 0,0565 | ✅ |

### 2.2 Gate M — nästlad λ

**Läckan som mäts.** `train.py:214–225` anpassar λ som minstakvadrat-lutningen
genom origo på de poolade held-out-paren, och rapporterar sedan
`pooled_mae_shrunk` på **samma par med samma λ**:

```python
dp = np.array(pooled_pred) - 0.5
dy = np.array(pooled_y) - 0.5
lam = float(np.clip((dp @ dy) / max(dp @ dp, 1e-12), 0.0, 1.0))
mae_shrunk = float(np.mean(np.abs(lam * dp - dy)))
```

`CLAUDE.md`:s formulering *"By construction the shrunk estimate cannot be worse
than 50/50 in expectation"* är därför sann **på anpassningsstickprovet**, inte
out-of-sample. Modellstegen är korrekt leave-city-out; det är krympningssteget
som ser all data.

**Resultat.** λ anpassad på tre städer, poängsatt på den fjärde:

| Fold | λ (övriga 3) | MAE nästlad | MAE 50/50 | Förbättring |
|---|---|---|---|---|
| Bergen | 0,484 | 0,0597 | 0,0557 | **−7,2 %** |
| Oslo | 0,218 | 0,0457 | 0,0477 | +4,2 % |
| Stavanger | 0,303 | 0,0523 | 0,0533 | +1,8 % |
| Trondheim | 0,166 | 0,0690 | 0,0700 | +1,5 % |
| **Poolat** | — | **0,0568** | **0,0565** | **−0,53 %** |

**Stationsnivå-bootstrap** (41 stationer, 2 000 dragningar, resamplingsenhet =
stationsriktning eftersom timrader inom en station är korrelerade):

```
delta (nästlad − 50/50) = +0,00030
95 % KI                 = [−0,00301, +0,00406]
P(modellen bättre)      = 0,322
KI utesluter noll       = nej
```

**Tolkning.** Modellen är **statistiskt oskiljbar från att skriva `0.5`**. Inte
bevisat sämre — men utan påvisbar fördel. Hela det publicerade försprånget på
0,0008 är λ som anpassats på utvärderingsdatan.

**Mekanismen** syns i λ-kolumnen: 0,166 → 0,484, nästan 3× beroende på vilka
tre städer den anpassas på. En skalär skattad på fyra korrelerade stadsblock
är för brusig för att bära ett försprång av den storleken.

### 2.3 Täckning — under-dispersion

Mätt med `predict.py`:s exakta efterbehandling (clamp → crossing guard →
shrinkage-omcentrering → clamp), replikerad i
`validate.apply_deployment_interval`:

| Stad | Täckning | Medianbredd |
|---|---|---|
| Bergen | 43,8 % | 0,060 |
| Oslo | 35,6 % | 0,068 |
| Stavanger | 50,0 % | 0,074 |
| Trondheim | 31,3 % | 0,068 |
| **Poolat** | **39,3 %** | **0,066** |

Nominellt: **80 %**.

Shrinkage-omcentreringen gör täckningen marginellt **sämre** (41,2 % före →
39,3 % efter): intervallet flyttas mot 0,5 med bibehållen bredd, alltså bort
från sanningen precis där sanningen avviker.

Det som heter `q10`/`q90` beter sig empiriskt ungefär som `q30`/`q70`.

### 2.4 Domän och orientering

`load_table()` filtrerar bort allt utom riktningar mot centrum:

```python
if float(r["radial_cos"]) <= 0:
    continue
```

Uppmätt träningsstöd: `radial_cos ∈ [0,001, 1,000]`.

Men `target_static_features()` hämtar via `coverage.target_matrix()`, som bara
läser kanter med `sensor_id` i `network.geojson`. Fem av sex stationer är
enkelriktade och har därmed **en** kant, så `max(rows, key=radial_cos)` får ta
den som finns — oavsett tecken:

| Sensor | target radial_cos | Utanför träningsstöd | ESS | Andel av träningsmängd |
|---|---|---|---|---|
| 107 | +0,611 | nej | 1 100 | 91 % |
| 1074 | +0,798 | nej | 921 | 76 % |
| 2276 | −0,138 | **ja** | 989 | 81 % |
| 133 | −0,160 | **ja** | 918 | 76 % |
| 1076 | −0,734 | **ja** | 1 016 | 84 % |
| 134 | −0,997 | **ja** | 826 | 68 % |

Två separata defekter:

- **D1 — orientering.** För fyra sensorer viktas den per-sensor-lokala modellen
  mot en punkt utanför träningsstödet på modellens viktigaste feature.
  `predict.py` orienterar däremot paret mot positiv `radial_cos` innan den
  predikterar (`if f1["radial_cos"] > f0["radial_cos"]`), så kernelcentrum och
  prediktionspunkt hamnar på motsatta sidor av gatan.
- **D2 — lokalitet.** Bandbredden (3,445 = medianavstånd till centroiden) är så
  bred att varje "lokal" modell effektivt använder 68–91 % av hela
  träningsmängden. `CLAUDE.md`:s *"each road trained for itself"* motsvaras
  inte av vikterna.

D1 är en **kandidatförklaring** till Gate M-utfallet och behandlas som sådan i
Fas F0.

### 2.5 Vad som inte mättes

Ärliga gränser för evidensen ovan:

- **Fyra städer = fyra block.** Nästlad λ har bara tre städer att anpassa på,
  så en del av λ-instabiliteten är inbyggd i designen snarare än ett fel i
  metoden. Den deployade utsagan *är* dock en leave-city-out-utsaga, så detta
  är rätt test för just den utsagan.
- **Bootstrappen resamplar stationer**, inte städer. Med n=4 städer går
  stadsnivå-bootstrap inte att göra meningsfullt, så stadsnivåberoende är inte
  fångat i KI:t.
- **Täckningen är mätt på norska held-out-stationer**, inte i Göteborg — samma
  population som varje annan siffra i `train_report.json`.
- **Ingen alternativ modell är testad.** Gate M är besvarad för den *deployade*
  modellen. Modellturneringen i augustiplanens Fas 1 (dataset v2, enklare
  kandidater) är en separat fråga som detta gör lägre prioriterad men inte
  formellt stänger.
- **Rank-histogram/PIT är inte beräknat**, bara täckningen vid ett nominellt
  värde. Fas E lägger till det.

---

## 3. Forskningsgrund

Problemet är inte nytt och har tre namn i tre fält: **underdeterminerad ODME**
(transport), **equifinality** (hydrologi), **under-dispersion** (meteorologi).
Sju etablerade praktiker, och vad var och en innebär för oss.

### 3.1 ODME: regularisera mot en prior — ensembla inte priorn

Standardlösningen på underdeterminering är ett tvådelat mål: minimera
avvikelsen mot mätta länkflöden **plus** avståndet till en seed-/target-matris.
Priorn tillför de ekvationer som saknas.

**Följd:** vår PFE gör redan rätt (level 3 är den regulariseringen). Ingen
etablerad praktik replikerar hela pipelinen vid priorns ytterlägen. Det är
q10/q90-replikeringen ovanpå som saknar motsvarighet.

### 3.2 FHWA TMG: faktorgrupper — lokal mätning slår transfer

TMG:s metod för att skatta det man inte mäter är **factor groups**: gruppera
stationer och låna faktorer från kontinuerliga stationer i samma grupp. Två
regler träffar oss direkt:

1. Route-specifika/lokala mätningar är att föredra framför transfererade.
2. Har faktorgruppen för få kontinuerliga stationer — använd default.

Storleksordningen är dessutom känd: transfererade D-faktorer gav 18–20 %
medelfel vid skattning av motsatt riktnings volym i den publicerade
cykelstudien.

**Följd:** Gate M-utfallet är den förväntade nivån för metoden, inte ett
misslyckande i vår implementation. TMG-logiken pekar på sensor 107:s lokala
ankare + default — vilket är Exit A.

### 3.3 Meteorologi: ensembler måste kalibreras före publicering

Vår 39 % har ett namn: **under-dispersion**. Diagnostiken är standardiserad —
spread-skill ratio ska vara ≈ 1, rank-histogram ska vara platt (U-format =
under-dispersivt). Operativa centraler efterbehandlar statistiskt (EMOS/BMA)
tills täckningen stämmer *innan* sannolikhetsetiketter sätts.

Varning från samma litteratur: spread-error och rank-baserade mått kan under
vissa varians-bias felaktigt indikera tillförlitlighet. Täckningsmätning är
nödvändig men inte automatiskt tillräcklig.

**Följd:** en ensemble är inte fel — en **okalibrerad** ensemble med
kvantilnamn är. Fas E.

### 3.4 GLUE/equifinality: många viktade dragningar, inte tre ytterlägen

Bevens GLUE samplar många parameteruppsättningar, behåller de *behavioural*
(passerar ett fit-tröskelvärde), viktar med likelihood och **härleder**
prediktionskvantiler ur den viktade ensemblen.

| GLUE | dagens q10/q50/q90 |
|---|---|
| Många dragningar | 3 |
| Ur den behavioural mängden | Marginalkvantiler ur en prediktiv modell |
| Likelihood-viktade | Oviktade |
| Kvantiler härledda ur ensemblen | Kvantiler antagna, sedan ensemblade |

**Följd:** om Gate S öppnar en ensemblegren ska den ha GLUE-formen (många
viktade, koherenta dragningar), inte kvantilformen.

### 3.5 DfT TAG: kärnscenario + känslighet, med proportionalitet

TAG Unit M4 och Uncertainty Toolkit: analysen ska inte enbart vila på ett
kärnscenario, men **valet av metod avgörs av det extra värde den ger för
beslutet** — högre när osäkerheten eller konsekvensen är större.

**Följd:** detta *är* Gate S. Kör inte en tre-varianters ensemble för att det
är metodologiskt dygdigt; kör den om den ändrar beslutet.

### 3.6 Bayesiansk efterfrågekalibrering: den korrekta versionen

Vill man ha kalibrerade intervall är metoden en posterior över efterfrågan,
samplad och propagerad genom simulatorn (Flötteröd/Bierlaire/Nagel för
dynamisk trafiksimulering). Då är intervallen kalibrerade by construction.

**Följd:** noteras som den principiellt riktiga vägen, men med sex sensorer
blir posteriorn mycket bred och kostnaden hög. Inte föreslagen nu.

### 3.7 SUMO-praxis: en kalibrerad efterfrågan + flera seeds

Dominerande praktik är **en** efterfrågan med **flera** seeds, seedlistan
rapporterad. FHWA rekommenderar minst fyra replikat och common random numbers
när alternativ jämförs.

**Följd:** alternativet till dagens 3 varianter × 1 seed är inte 1 körning —
det är **1 variant × ≥4 seeds**. Besparingen är mindre än den ser ut, och
kvaliteten på osäkerhetsmåttet blir högre.

---

## 4. Beslut

**Gate M = BASELINE** för den deployade modellen. Evidens:
`validation/dirsplit_gate_m_20260816.json`.

Augustiplanens utfallsmatris uppdaterad:

| | Gate S = NEJ | Gate S = JA |
|---|---|---|
| **Gate M = BASELINE** ✅ *(mätt)* | **Exit A** — 50/50 + 107:s ankare, ingen ensemble | **Gren B** — residualscenarier runt 0,5 |
| Gate M = MODELL | *(utesluten av mätningen)* | *(utesluten av mätningen)* |

Endast Gate S återstår för att välja mellan Exit A och Gren B. Ingen väg leder
längre till en villkorad prediktiv riktningsmodell i produkt utan att Fas F0
först vänder Gate M.

---

## 5. Fasplan

### Fas A — Evidens och harness · **LANDAD**

Levererat i denna commit:

| Fil | Vad |
|---|---|
| `dirsplit/validate.py` | Reproducerbart harness: nästlad λ, bootstrap, täckning, orientering |
| `tests/test_dirsplit_validate.py` | 22 enhetstester av den rena kärnan |
| `validation/dirsplit_gate_m_20260816.json` | Evidensartefakt |
| `Makefile` | `make dirsplit-validate` |
| `CLAUDE.md`, `README.md` | Rättade siffror (se §5.1) |

**Acceptanskriterier (uppfyllda):** harnesset reproducerar `train_report.json`
exakt innan det mäter något nytt; inga spårade modell- eller pipelineartefakter
ändrade; alla tester gröna.

#### 5.1 Rättade dokumentationssiffror

| Plats | Var | Är |
|---|---|---|
| `CLAUDE.md:418` | "Oslo +11,1 %, Trondheim −0,9 %" som om det vore nuläget | Markerat som pre-FINAL-METHODOLOGY; nuvarande domänsiffror är negativa i 3 av 4 städer |
| `CLAUDE.md:420` | "cannot be worse than 50/50 in expectation" | Kvalificerat: gäller anpassningsstickprovet; out-of-sample mätt till oskiljbart |
| `README.md` | "+11,1 % Oslo" i dirsplit-avsnittet | Samma kvalificering + hänvisning till evidensartefakten |

Detta är dokumentationsrättelser, inte modelländringar. Ombygget som höjde λ
från 0,256 till 0,289 **var** en förbättring på det publicerade måttet — det
som nu mäts är att måttet självt inte generaliserar.

---

### Fas B — Sensor 107:s lokala ankare · **NÄSTA**

Oförändrad från augustiplanens Fas 0A, men nu högre prioriterad: efter Gate M
är 107:s publicerade årsvärde den **enda** riktningsevidens vi har som inte är
transfer.

**Mål.** Bind Göteborgs publicerade 3 400/3 100 (≈ 52/48 för 2025; 2023–24
omkring 50/50) som ett provenance-bundet, periodsemantiskt ankare.

**Konkreta ändringar.**

1. `data_in/sensors.json`: nytt fält `directional_reference` för 107 med
   `year`, råa riktningstal, `source`, kant/riktningsmappning och
   `time_semantics: "annual_mean"`.
2. `demand/intake.py::build_targets`: använd ankaret som aggregerad
   centralnivå för 2025-struktur; tillåt tidsvariation endast där data/modell
   stöder den, och normalisera så att ankaret återfås över sin deklarerade
   period.
3. Märk 107:s kvartsvärden som **estimerade** även när periodmedlet är lokalt
   förankrat.

**Förbjudet.** En hårdkodad `0.52` i `build_targets`. Den skulle förlora källa,
år, riktning och tidssemantik — och en års-D-factor är inte 96 oberoende
Level-1-mätningar.

**Tester.**

| Test | Påstående |
|---|---|
| `test_107_reference_has_full_provenance` | År, källa, råtal, kantmappning och tidssemantik finns |
| `test_annual_anchor_is_not_serialized_as_96_measurements` | Ingen kod skriver års-D-factorn som per-kvarts Level-1 |
| `test_anchor_is_recovered_over_its_declared_period` | Periodmedlet av de estimerade kvartsvärdena återger 52/48 |
| `test_five_directional_sensors_unchanged` | 133, 134, 1074, 1076, 2276 Level-1-mål byte-identiska före/efter |

**Acceptanskriterium.** Alla fyra gröna, och en demand-build ger identiska mål
för de fem enkelriktade stationerna.

---

### Fas C — Gate S: matched-seed-känslighet · **AVGÖRANDE**

**Mål.** Besvara: ändrar rimlig riktningsvariation *beslutet* — viable set,
finalistlista, rangordning eller vinnare?

**Preregistrering är obligatorisk.** Beslutsregeln skrivs till
`validation/gate_s_registration_<datum>.json` **innan** första SUMO-körningen,
och utfallet skrivs till en separat artefakt. Ingen omtolkning i efterhand.

**Design (korsprodukt, outcome-blind).**

| Axel | Nivåer | Motivering |
|---|---|---|
| Efterfrågefall | 50/50, q10, q50, q90 | 4 fall; 50/50 är den nya baseline-kandidaten |
| SUMO-seed | 4 matchade | FHWA-minimum för att bedöma simulatorns egen variation |
| Stängningsfall | 6 preregistrerade | Spänner hög/låg observabilitet och nätverksposition |

**Common random numbers.** Exakt samma seed används för baslinjen och varje
stängningsalternativ inom ett efterfrågefall. Det minskar variansen i
*differensen*, vilket är den storhet beslutet vilar på.

**Utfallsmått, alla preregistrerade.**

- Viable set: medlemskap identiskt över efterfrågefall? (Jaccard)
- Finalistlista: identisk topp-k?
- Rangordning: Kendall τ mellan efterfrågefallens rankningar
- Vinnare: samma stängningsfönster?
- Regret: hur mycket sämre blir 50/50-vinnaren utvärderad under q10/q90?

**Beslutsregel (skrivs i registreringen, inte här som slutgiltig).** Förslag:
Gate S = `YES` om vinnaren skiljer sig i ≥ 1 av 6 stängningsfall **eller**
median-Kendall τ < 0,8 mellan efterfrågefallen. Gate S = `NO` om vinnaren är
identisk i alla 6 och τ ≥ 0,8. Annars `INCONCLUSIVE`, vilket **inte** är en
vinst för någon sida.

**Vad Gate S inte får göra.** Den får inte hårt förbjuda en väg — topologi,
framkomlighet och no-detour förblir hårda säkerhetsgrindar oberoende av
datatäckning.

**Kostnadsuppskattning.** 4 fall × 4 seeds × (1 baslinje + 6 stängningar) = 112
meso-körningar. Vid ~40 s per heldags-meso-körning ≈ 75 min ren simuleringstid,
plus efterfrågebyggen. Ryms i ett kvällsjobb.

**Acceptanskriterium.** Registrering skriven före körning; utfallsartefakt med
alla fem mått; en av `YES`/`NO`/`INCONCLUSIVE` klart deklarerad.

---

### Fas D — Beslutsgren

#### D1 · Gate S = `NO` → **Exit A**

Riktningsaxeln är beslutsirrelevant. Avsluta dirsplit-utbyggnaden.

1. Efterfrågan byggs med **50/50** plus 107:s ankare från Fas B.
2. `build_sumo_demand.py` bygger **en** variant.
3. `run_scenario.py` kör **≥4 seeds på den varianten** (FHWA-minimum), inte
   1 seed per variant.
4. **Konfidensformeln måste omprövas.** `confidence = spatial_prior × exp(−CV)`
   får då en CV som mäter **enbart SUMO-slump**, eftersom scenarioaxeln är
   borta. Det är ärligare än idag (där de är hopblandade) men betyder något
   annat, och `IMPROVEMENT_PLAN.md`-punkt 19 samt tooltip-texten måste
   uppdateras i samma ändring.
5. `dirsplit/` behålls som forskningsmodul med sitt harness; den slutar vara
   en efterfrågekälla. `estimate_directions.py` förblir fallback.

**Vinst:** ~2/3 av kalibreringstiden, och ett konfidensmått som mäter en sak.

#### D2 · Gate S = `YES` → **Gren B: residualscenarier**

Riktningen påverkar beslutet, men ingen prediktiv signal finns (Gate M).
Bygg då spridning utan att låtsas om prediktion.

1. **Scenarioenhet = en hel dag**, alla berörda sensorer och tider samtidigt —
   inte en oberoende kvantil per cell.
2. **Blockbootstrap av observerade residualer** på logit-skala runt 0,5,
   vilket bevarar rums- och tidskorrelation (ECC-motivet i §3.4).
3. **Fler än tre dragningar, viktade** (GLUE-formen).
4. **Ortogonala axlar:** `demand_case_id` och `simulation_seed` är separata
   fält. Scenarioidentitet kopplas aldrig till seednummer.
5. **Ingen field-wise max över fall.** En kostnadsvektor måste komma från en
   värld som faktiskt kördes; rapportera i stället per fall plus en deklarerad
   riskstatistik.
6. Täckningsvalidering enligt Fas E innan något publiceras.

---

### Fas E — Avveckla q10/q90-etiketterna · **OBEROENDE AV GREN**

Gäller oavsett Gate S, eftersom 39 % täckning är fel oavsett vad man beslutar
om ensemblen.

**Val (ett av två, beslutas när Fas D:s gren är känd):**

- **E1 — Byt namn.** `edge_shares_q10/q90` → `edge_shares_lo/hi`, plus ett
  obligatoriskt fält `interval_semantics` som bär **uppmätt** täckning,
  mätprotokoll och datum. Inga sannolikhetsnamn utan mätning bakom.
- **E2 — Kalibrera om.** Vidga bredden (variansinflation eller conformal
  kalibrering) tills uppmätt täckning når nominellt värde, och publicera först
  därefter. Notera att conformal kräver exchangeability, vilket över städer och
  tid inte är självklart — kalibreringen måste därför valideras, inte antas.

**Ny stående diagnostik.** Lägg till rank-histogram/PIT i
`dirsplit/validate.py` utöver täckning vid ett nominellt värde, eftersom
punkttäckning ensam inte skiljer en välformad ensemble från en snedvriden.

**Test.** `test_no_quantile_label_without_measured_coverage` — ett artefakt med
kvantilnamn men utan `interval_semantics` ska få bygget att fallera.

**Frysta arkiv rörs inte.** Regeln gäller nya byggen.

---

### Fas F — Orientering och lokalitet

#### F0 · En sista chans för modellen *(valfri, billig, före Fas C)*

D1 är en trovärdig mekanism bakom Gate M-utfallet: fyra av sex per-sensor-
modeller viktas mot fel sida av gatan. **Om** någon vill ge modellen en sista
chans är detta det billigaste experimentet.

1. Spegla feature-vektorn för enkelriktade stationer så att kernelcentrum
   använder samma toward-centre-orientering som `predict.py` (negera
   `radial_cos`, `res_asym`, `major_asym`; byt behind/ahead-paren).
2. Träna om.
3. Kör `python3 -m dirsplit.validate` igen.

**Beslutsregel, preregistrerad:** Gate M vänder endast om nästlad λ ger en
poolad förbättring mot 50/50 **och** bootstrap-KI:t utesluter noll. Ett
positivt punktestimat med KI över noll räcker inte — det är precis felet som
gav 0,0008 från början.

#### F1 · Orienteringsfixen · **OBEROENDE AV GREN**

Även om ingen modell deployas ska defekten inte ligga kvar och vilseleda nästa
omträning.

**Test.** `test_every_target_vector_is_toward_centre` — varje vektor från
`target_static_features()` måste ha `radial_cos ≥ min(träningens radial_cos)`.
Detta test **fallerar idag** och ska landa tillsammans med fixen, inte före.

#### F2 · Lokalitetsanspråket

ESS 68–91 % stöder inte "each road trained for itself". Två vägar:

- Mät om lokaliteten gör nytta över huvud taget: held-out-jämförelse global vs
  lokalt viktad modell, med samma nästlade protokoll. Om ingen skillnad — ta
  bort både bandbreddsmaskineriet och anspråket.
- Eller sänk bandbredden och omvalidera. Får inte göras utan Fas A:s harness,
  eftersom en smalare kernel lätt förbättrar in-sample och försämrar held-out.

**Dokumentationsrättelse sker oavsett:** `CLAUDE.md`:s "each road trained for
itself" kvalificeras med den uppmätta effektiva andelen.

---

## 6. Testmatris

| Fas | Test | Typ | Blockerar |
|---|---|---|---|
| A | `tests/test_dirsplit_validate.py` (22 st) | Enhet | ✅ landad |
| A | Harness reproducerar `train_report.json` | Integration | ✅ landad |
| B | `test_107_reference_has_full_provenance` | Enhet | Fas B |
| B | `test_annual_anchor_is_not_serialized_as_96_measurements` | Regression | Fas B |
| B | `test_anchor_is_recovered_over_its_declared_period` | Enhet | Fas B |
| B | `test_five_directional_sensors_unchanged` | Regression | Fas B |
| C | Registrering skriven före körning | Process | Fas C |
| C | Common random numbers verifierade per fall | Integration | Fas C |
| D1 | Konfidenssemantik uppdaterad i docs + tooltip | Dokumentation | Exit A |
| D2 | `demand_case_id` och seed är separata fält | Kontrakt | Gren B |
| D2 | Ingen field-wise max över efterfrågefall | Kontrakt | Gren B |
| E | `test_no_quantile_label_without_measured_coverage` | Kontrakt | Publicering |
| F1 | `test_every_target_vector_is_toward_centre` | Regression | Fas F1 |

---

## 7. Vad som uttryckligen inte ska göras

- Skeppa inte `q10`/`q90` som sannolikhetsintervall vid 39 % uppmätt täckning.
- Kör inte en enda variant/seed och kalla resultatet robust — Exit A kräver
  ≥4 seeds, inte 1.
- Tolka inte "Gate M = BASELINE" som "variansen är noll". Gate S är en separat
  fråga och är fortfarande öppen.
- Hårdkoda inte 107 till 0,52 per kvart; bevara års-/periodsemantiken.
- Koppla inte scenarioidentitet till seednummer.
- Bygg inte schema-, monthly-, warm-state-, API- eller UI-integration innan
  Gate S och Gate P har öppnat motsvarande gren.
- Skriv inte om befintliga q-arkiv eller öppna nu stängda closure-releasegrindar
  på grund av denna plan.
- Optimera inte F0:s spegling mot samma held-out-resultat som ska bedöma den —
  beslutsregeln är preregistrerad ovan.
- Sänk inte bandbredden "för att det låter mer lokalt" utan nästlad omvalidering.

---

## 8. Definition of done

**För Fas A (uppfyllt):** harness landat och testat; evidensartefakt skriven;
replikationskontroll grön; dokumentationssiffror rättade; inga spårade modell-
eller pipelineartefakter ändrade.

**För Exit A:** Fas B levererad med sina fyra tester; Gate S registrerad och
avgjord som `NO`; en variant + ≥4 seeds i drift; konfidenssemantiken
omdokumenterad; `dirsplit/` nedgraderad till forskningsmodul.

**För Gren B:** allt ovan utom att Gate S är `YES`; residualscenariegeneratorn
levererad med koherenta dagsenheter och ortogonala case/seed-axlar; uppmätt
täckning vid nominellt värde innan publicering; Gate P separat passerad före
någon produkt-, API- eller UI-integration.

**Gemensamt för båda:** Fas E:s etikettregel i kraft; Fas F1:s regressionstest
grönt; ingen frusen evidens omskriven.

---

## 9. Källor

Primärkällor för §3, utöver augustiplanens lista:

- FHWA, Traffic Analysis Toolbox — replikat och seeds:
  <https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter6.htm>
- FHWA, Traffic Monitoring Guide — riktningsfördelning/D-factor:
  <https://www.fhwa.dot.gov/policyinformation/tmguide/tmg_2013/traffic-monitoring-theory.cfm>
- NCHRP, *Guide on Methods for Assigning Counts to Adjustment Factor Groups*:
  <https://nap.nationalacademies.org/read/27925/chapter/2>
- *Directional Distribution Factors for Bicycle Traffic* (transferabilitetsfel
  18–20 %): <https://ascelibrary.org/doi/10.1061/%28ASCE%29TE.1943-5436.0000880>
- Beven & Freer, *Equifinality, data assimilation and uncertainty estimation
  (GLUE)*: <https://www.sciencedirect.com/science/article/abs/pii/S0022169401004218>
- Dirkson et al. (2026), *Are we misdiagnosing ensemble forecast reliability?*:
  <https://rmets.onlinelibrary.wiley.com/doi/full/10.1002/qj.70186>
- DfT, TAG Unit M4 *Forecasting and Uncertainty* + Uncertainty Toolkit:
  <https://www.gov.uk/government/publications/tag-unit-m4-forecasting-and-uncertainty>
- Flötteröd, Bierlaire & Nagel, *Bayesian Demand Calibration for Dynamic
  Traffic Simulations*: <https://pubsonline.informs.org/doi/10.1287/trsc.1100.0367>
- Data-driven OD-skattning, underdeterminering och seed-matriser:
  <https://www.tandfonline.com/doi/full/10.1080/21680566.2022.2080128>
