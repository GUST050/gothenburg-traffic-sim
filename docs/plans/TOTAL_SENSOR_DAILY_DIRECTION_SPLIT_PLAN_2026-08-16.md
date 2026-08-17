# Plan för dygnsförankrad, tidsvarierande riktningssplit vid TOT-sensorer

**Datum:** 2026-08-16, utökad 2026-08-17 med avsnittet *Lokal korridorevidens*.
**Status:** Forskningsgrundad plan; ingen produktkod eller fryst evidens har
ändrats. Tillägget 2026-08-17 lägger till ett mätverktyg och en diagnostisk
artefakt, ingen produktväg och ingen grindavgörelse.
**Beslut som planen ska möjliggöra:** om 52/48 ska vara ett exakt kriterium för
varje dygn vid sensor 107, och vilken generaliserbar metod som får skapa den
friare 15-minutersprofilen vid 107 och framtida tvåvägs-TOT-sensorer — inklusive
**varifrån formen får hämtas**, från de norska stationerna eller från Göteborgs
egna grannsensorer.

## Sammanfattat designbeslut

Den minsta försvarbara lösningen är **inte** att låta PFE välja 96 fria
riktningsandelar. Då kan ett underidentifierat nät använda splitten som
felslukare och förbättra passningen utan att den verkliga riktningen blivit mer
känd. Lösningen ska i stället vara en separat, deterministisk förbehandling:

1. behåll den uppmätta tvåvägstotalen exakt i varje intervall;
2. skapa en datalärd men regulariserad profil för andelen i ledande riktning;
3. projicera hela dygnsprofilen med **en** justering så att dagens volymviktade
   split träffar den beslutade dygnsankaren;
4. härled den andra riktningen som exakt komplement;
5. lämna den färdiga profilen till PFE som ett versions- och
   provenancebundet indatafält, inte som fria beslutsvariabler.

För sensor 107 är målandelen inte avrundade `0.52`, utan
`3400 / (3400 + 3100) = 0.523076923...` för norrgående riktning. Ett exakt
dygnsvillkor är dock starkare än stadens publicerade årsaggregat. Det ska därför
lagras som en **modellpolicy** med egen giltighet och källa till beslutet, inte
som om staden hade mätt 52/48 varje dygn.

Den föreslagna centrala kandidaten är en **dygnsvis logitprojektion av en
held-out-validerad TOT-profil**. Profilens amplitud ska läras från sensorer där
båda riktningarna verkligen är mätta. Den ska inte skapas genom en handvald
35/65- eller 40/60-gräns. Om den kandidaten inte slår enklare baslinjer i
blockerad validering behålls den enklaste vinnaren.

## Vad forskningen faktiskt stödjer

### 1. Ett riktningsaggregat är inte en tidsserie

FHWA:s definition av D-factor avser andelen i den högre riktningen under en
bestämd dimensionerande timme. Myndigheten kräver sektionsspecifika mätningar
eller data från samma eller en verkligt liknande väg; generella systemvärden
ska inte användas. Det stödjer lokal auktoritet och transfer med
applicability-kontroll, men inte att ett års- eller timaggregat kopieras till
varje kvart eller dygn. Se [FHWA HPMS Field Manual, Item 27](https://www.fhwa.dot.gov/policyinformation/hpms/fieldmanual/page05.cfm).

Trafikverkets EVA-underlag använder olika riktningsfördelningar för olika
trafikrang och vägtyp, exempelvis 63/37 i den högsta rangen för
ytterområde/citygata och 50/50 i lägre ranger. Det är direkt stöd för att en
rimlig tidsprofil kan svänga mer än dagens nästan plana profil, men tabellen är
en schablon för analysklasser och får inte bli ett lokalt facit. Se
[Trafikvariation och fordonsandelar, avsnitt Riktningsfördelning](https://bransch.trafikverket.se/contentassets/6172806b6959485d895374173243047f/2025_ny/vag/trafikvariation-och-fordonsandelar-vag.pdf).

FHWA:s Traffic Monitoring Guide skiljer uttryckligen på time-of-day-,
day-of-week- och month-of-year-faktorer och rekommenderar att de beräknas från
kontinuerliga mätstationer. Det motiverar dagsblock och dagtyper i stället för
en enda återanvänd 96-punktsprofil. Se
[Traffic Monitoring Guide 2022](https://www.fhwa.dot.gov/policyinformation/tmguide/2022_TMG_Updated_20241008.pdf).

En offentlig KDOT-studie beräknar separata riktningsfaktorer per timme,
vägsegment och dagtyp och visar att 40/60 kan förekomma under en timme trots
ett annat dygnsaggregat. Studien framhåller också att riktningsprofilen kan
ändra när en körfältsavstängning är tillåten. Se
[KDOT Lane Closure Guide study, avsnitt 3.6](https://rosap.ntl.bts.gov/view/dot/36345/dot_36345_DS1.pdf).

### 2. Andelar ska modelleras som kompositioner och flödet ska vara mjukt

Två riktningsandelar ligger på simplexen och måste summera till ett. Logit är
den naturliga representationen för en tidsserie av två komplementära andelar;
den ger obegränsade latenta värden men giltiga andelar efter invers transform.
Se Barceló-Vidal och Aguilar,
[Time Series of Proportions: A Compositional Approach](https://ima.udg.edu/~barcelo/index_archivos/Time_Series_Proportions_C_Barcelo_IWSM2010.pdf).

För tidsmässig regularisering är första- eller andraderivata på logitprofilen
en transparent kandidat. Trend filtering ger en mjuk eller styckvis linjär
signal utan att tvinga bort verkliga toppar; graden av utjämning ska väljas i
träning, inte på målstationen. Se Kim, Koh, Boyd och Gorinevsky,
[L1 Trend Filtering](https://www.web.stanford.edu/~boyd/papers/l1_trend_filter.html).

### 3. Valideringen måste efterlikna den verkliga transferuppgiften

Slumpmässiga radfolds läcker stationers och närliggande tiders struktur.
Blockerad validering ger mer realistiska fel när data har tids-, rums- eller
gruppberoende. Se Roberts et al.,
[Cross-validation strategies for structured data](https://www.biom.uni-freiburg.de/mitarbeiter/dormann/roberts-et-al-2017-ecography.pdf/at_download/file).

Nuvarande SUMO-dokumentation beskriver count-to-route-problemet som beroende av
en kandidatroutefamilj och kalibrering. Repositoryts egen erfarenhet visar
dessutom att flera routeuppsättningar kan passa samma få räknepunkter. Det är
skälet att hålla dirsplit utanför PFE:s fria variabler. Se
[SUMO: Routes from Observation Points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html).

Om osäkerhetsintervall senare publiceras måste tidsberoendet respekteras och
täckningen mätas utanför träningen. Vanlig radvis split-conformal räcker inte
automatiskt för tidsserier. Se Xu och Xie,
[Conformal Prediction for Time Series](https://doi.org/10.1109/TPAMI.2023.3272339).

## Nuläge och varför en ny grind behövs

Sensor 107:s aktiva q50-profil före lokal ankare har bara cirka 1,9
procentenheters spann över dygnet. Efter dagens volymviktade årsankring ligger
norrandelen ungefär mellan 51,56 och 53,46 procent. Mätt mot 2025 års
tvåvägstotal ger samma profil en faktisk dygnssplit mellan cirka 52,22 och
52,36 procent. Den är alltså redan nästan dygnsfast men har mycket liten
intradagsvariation.

Detta är en diagnostik från nuvarande spårade
[`sumo/direction_split.json`](../../sumo/direction_split.json) och
[`web/data/flows.json`](../../web/data/flows.json), inte ny releaseevidens.
Den säger att användarens önskade förändring främst handlar om **formens
amplitud**, inte om att reparera stor daglig drift.

Nuvarande `Gate M` väljer den generella q50-modellen och nuvarande lokala
ankare projicerar periodmedlet. De besvarar inte frågan om ett exakt
dygnsvillkor eller om rätt intradagsamplitud. En ny append-only **Gate D** ska
därför användas; tidigare Gate M/S-resultat bevaras och omtolkas inte.

## Lokal korridorevidens — formkällan denna plan saknade

**Tillagt 2026-08-17.** Planen ovan hämtar fortfarande formen från de norska
stationerna, om än med TOT-matchad semantik, och slår i D3 fast att Göteborg 107
aldrig får vara shape ground truth eftersom dess riktningar inte mäts per
intervall. Det är korrekt om 107 betraktas isolerat. Det är däremot inte hela
den lokala evidensen: **de fem enkelriktade stationerna mäter en körbana var,
var 15:e minut, och en av dem ligger på samma gata som 107.** Den möjligheten var
inte undersökt när planen skrevs.

`tools/measure_local_direction_evidence.py` mäter den nu, generiskt och utan
hårdkodade sensor-ID:n, och skriver `validation/local_direction_evidence_v1.json`
(`release_evidence: false`). Tre mätta resultat på hela referensåret 2025,
vardagar:

| storhet | värde |
|---|---|
| 1076:s kant mot 107:s sydgående: avstånd | 239,9 m |
| samma par: bäringsskillnad | 3,5° |
| 1076 sydgående / 107 tvåvägstotal, flödesviktat dygnsmedel | **0,6946** |
| publicerad sydandel vid 107 (3 100/6 500) | **0,4769** |
| **lokaliseringsbias** | **21,77 procentenheter** |
| 1076-kvotens intradagsspann | 35,41 pe |
| nod 26355153, mätt in / mätt ut | 0,5217 |
| samma nod, omätt andel av utflödet | 0,4783 |
| samma nod, intradagsspann | 26,88 pe |

### Vad mätningarna säger

**Möjligheten är verklig.** 1076 mäter sydgående Skånegatan 240 m från 107:s
sydgående kant, bäringsskillnad 3,5°, alltså samma korridor och samma riktning.
Dess kvot rör sig 35,4 pe över dygnet och nodbalansen 26,9 pe, mot den
överförda norska kurvans 7,7 pe. Lokal data innehåller alltså mätbart mer
tidsstruktur än den vi importerar — och till skillnad från Norge beskriver den
den faktiska gatan.

**Genvägen är samtidigt utesluten.** Kvoten kan inte läsas som en riktningsandel.
Dess flödesviktade dygnsmedel är 0,6946 där den publicerade sydandelen är
0,4769 — en bias på 21,77 pe, långt utanför referensens deklarerade tolerans
0,002. Orsaken är att 1076 står någon annanstans: ~4 800 fordon/dygn sydgående
mot 107:s ~3 100, med korsningar emellan. Kvoten mäter skillnad i **plats**,
inte i **riktning**. Testet
`test_the_donor_ratio_is_materially_above_the_published_share` pinnar detta så
att genvägen inte kan smyga tillbaka.

**Konservering räddar det inte.** Noden 26355153 är bara till hälften
observerad: 47,8 % av utflödet kommer på ben ingen mäter. Den slacken är exakt
den frihet en fri nätinferens skulle ha — och därmed exakt det utrymme där en
lösare kan gömma residualfel i splitten i stället för att lära sig riktningen.
Det är samma felslukarrisk som planens `Sammanfattat designbeslut` avvisar, nu
med ett mätt tal på hur stor den är.

### Följd för designen

Lokal korridorevidens tas in som **formkälla**, inte som andel, och nivån
fortsätter komma från `directional_reference`. Separationen är redan planens
egen: form och nivå är olika storheter med olika evidens. Det som ändras är
varifrån formen får hämtas.

Kandidatfamiljen i D2 utökas därför med, i stigande komplexitet:

- **L0 (obligatorisk baslinje):** den naiva kvoten `donor / total` rakt som
  andel. Den är **förregistrerad som förkastad** med mätt skäl — 21,77 pe bias —
  och finns med bara för att en senare läsare ska se att den prövades.
- **L1:** donorns **normaliserade** form (kvoten delad med sitt eget
  flödesviktade medel, som artefakten redan skriver) som basprofil `p[s,d,t]`,
  därefter planens exakta dygnsprojektion mot ankaret. Nivån kommer alltså helt
  från 107, formen helt från Skånegatan.
- **L2:** L1 med held-out-lärd amplitud `a`, precis som C2, så att formens
  styrka valideras i stället för antas.
- **L3, endast om L1/L2 inte räcker:** en explicit modell av
  lokaliseringsbiasen, härledd ur ruttstrukturen mellan donator och TOT-sensor
  i stället för ur en rå kvot. Detta är den enda kandidat som får försöka
  förklara de 21,77 procentenheterna, och den måste valideras maskerat.

Hårda krav som gäller hela L-familjen:

1. **Ingen fri PFE-auktoritet.** L-kandidater producerar en färdig
   `DailyDirectionProfile` som `build_targets` konsumerar, enligt D7. Splitten
   får aldrig bli en fri variabel som förbättrar andra sensorers residualer.
2. **Ingen träning och utvärdering på samma räknepunkt.** Donatorn 1076 är en
   av de sex sensorer PFE:n kalibreras mot. Använder en L-kandidat 1076:s form
   får 1076:s egen passning inte räknas som bevis för att formen är rätt. Den
   maskerade valideringen i D3 måste hålla donatorn utanför sitt eget
   utvärderingsmål.
3. **Donatorupptäckt är data, inte kod.** Tröskelvärdena (500 m, 30°) är
   deklarerade konstanter i verktyget och ska flyttas till policyn när en
   L-kandidat aktiveras, så en framtida TOT-sensor får donatorer utan
   kodändring.
4. **Ingen donator, inget lokalt anspråk.** En TOT-sensor utan kvalificerad
   donator faller tillbaka på den generella profilen och får inte märkas
   lokalt formad.

### Vad detta gör med Gate D

Gate D:s andra fråga — "finns användbar intradagssignal?" — får nu två
konkurrerande evidensvägar, C (norsk TOT-matchad) och L (lokal korridor), som
måste mätas i **samma** foldfamiljer och mot **samma** baslinje B0. Utfallen
utökas med:

- `SHAPED_DAILY_LOCAL`: exakt dygnsankare med validerad **lokalt** härledd form;
- `SHAPED_DAILY_TRANSFER`: samma, men med den norska TOT-matchade formen.

Vinner ingen av dem över B0 gäller planens ursprungliga utfall oförändrat. Att
lokal evidens intuitivt känns mer relevant är inget skäl att aktivera den; det
är just därför den ska genom samma grind.

En sak talar dock för L-vägen utöver evidenskvaliteten: **den kräver ingen ny
extern data.** Den använder de sex levererade sensorerna och respekterar
beslutet från 2026-07-20. Gate M:s väg är i praktiken stängd sedan
hämtningsklienten raderades och råvolymerna bara kan matas in för hand; den här
vägen är öppen med det material projektet redan äger.

### Vad som fortfarande inte är gjort

Mätningarna ovan är **diagnostik**, inte releaseevidens: de är gjorda på spårad
data men utan förregistrering, och ingen grind får avgöras ur dem. D0 måste
frysa L-familjen, tröskelvärdena och den maskerade valideringsdesignen innan
något held-out-resultat öppnas.

## Matematiskt kontrakt

För TOT-sensor `s`, kalenderdygn `d` och intervall `t`:

- `T[s,d,t]` är uppmätt eller provenancebundet prognostiserad tvåvägstotal;
- `p[s,d,t]` är den centrala profilen före dygnsprojektion;
- `r[s,d]` är den uttryckligen beslutade dygnsankaren;
- `x[s,d,t]` är andelen på den orienteringsbundna ledande kanten.

Profilen uttrycks på logitskalan. Med `z = logit(p)` och en
held-out-vald amplitud `a >= 0` löses en enda dygnsoffset `b` ur:

```text
x[t] = sigmoid(a * centered(z[t]) + b)
sum_t T[t] * x[t] = r * sum_t T[t]
other[t] = 1 - x[t]
```

`centered(z)` tar bort profilens nivå men behåller ordning och tidsform.
Offseten `b` har en unik lösning när dagens positiva totalmassa finns.
Konstruktionen ger följande hårda invariants:

- `0 < x[t] < 1`;
- `x[t] + other[t] == 1` inom numerisk tolerans;
- riktningarnas summa är exakt `T[t]` i varje intervall;
- den volymviktade dygnssplitten är exakt `r` i kontinuerliga mål;
- amplituden kan öka eller minska utan att dygnstotalen ändras.

`a` får aldrig trimmas mot sensor 107:s okända riktningar. Den väljs från
träningsdata och fryses per modell-/applicability-grupp. Kandidaten `a=0` är
den plana dygnsankrade baslinjen. Nuvarande profils amplitud är en annan
förregistrerad baslinje.

Om en mer flexibel kandidat behövs får den minimera ett dokumenterat mål på
logitskalan med avvikelse från priorprofilen och straff för första/andra
tidsskillnader. Den får inte använda PFE:s target residual som träningsmål om
samma sensorer senare används för att utvärdera passningen.

## Generaliserbart sensor- och policykontrakt

### Evidens och tillämpningspolicy ska vara separata

`directional_reference` fortsätter beskriva vad källan faktiskt mätte:

- råa riktningstal och total;
- orientering till exakta edge-id:n;
- aggregering, tidssemantik och källperiod;
- verifiering, kvalitet och källa.

Ett nytt, separat `directional_application_policy` beskriver vad produkten får
göra:

```json
{
  "policy_version": "daily_direction_anchor_v1",
  "mode": "daily_exact",
  "lead_bearing": "N",
  "anchor_source": "directional_reference",
  "application_start": "2025-01-01",
  "application_end": "2025-12-31",
  "temporal_profile": "validated_tot_shape_v1",
  "missing_day_policy": "fail_to_declared_fallback",
  "authority": "model_assumption_not_daily_measurement",
  "decision_record": "validation/...json"
}
```

Tillåtna lägen ska vara få och explicita:

- `period_only`: nuvarande källsemantik; ingen ny dygnslåsning;
- `daily_exact`: exakt volymviktad ankare varje komplett dygn;
- `daily_band`: dygnsandelen får röra sig inom ett validerat intervall;
- `none`: ingen lokal riktningsevidens; använd vald fallback.

`daily_exact` får inte bli implicit standard för alla framtida TOT-sensorer.
Den kräver en verifierad referens, ett separat beslut och en giltig
applicability-period. Ett 2025-ankare får inte automatiskt styra 2027; en sådan
överföring kräver en särskild `structural_transfer`-post och känslighetstest.

### Sensorer utan tillräcklig evidens

- En enkelriktat mätt sensor påverkas aldrig av denna väg.
- En TOT-sensor utan lokal ankare använder den vinnande generella profilen men
  får inte märkas lokalt förankrad.
- Saknat modellstöd ger plan 50/50, inte en aggressiv schablon.
- Låg transferlikhet krymper amplituden mot noll och breddar endast en
  validerad osäkerhetsyta; den skapar inte en hård positiv motriktningsnivå.

## Arbetsplan

### D0 — Frys problem, nuläge och beslutskriterier

Skapa en registrering som binder nuvarande källor, kodhashar,
`direction_split.json`, sensorregister och de mätta nulägesvärdena ovan.
Registreringen ska ange att arbetet gäller TOT-sensorers riktning, inte
enkelriktade stationer, OD-nivåer eller fri PFE-omkalibrering. Frysta Gate M/S-,
demand-, route- och closure-artefakter ändras aldrig.

### D1 — Bygg en dagblockerad ground-truth-tabell

Utgå från de befintliga 188 provenancebundna norska stationerna men gå tillbaka
till samtidiga råvärden per riktning och datum. För varje kvalitetsgodkänt dygn:

1. summera riktningarna till en syntetisk TOT-serie;
2. behåll den riktiga riktningen som endast evaluation target;
3. beräkna features enbart från information som skulle finnas vid en riktig
   TOT-sensor: tvåvägstotal, tid, dagtyp och statiska vägfeatures;
4. bevara hela dygn som block och bind varje råfil med SHA-256;
5. märk tidszon, DST, luckor, nollmassor och mätupplösning explicit.

Detta löser en viktig semantisk brist i den tidigare generella modellen:
profilfeatures från en verklig TOT-sensor beskriver samma tvåvägstotal som i
träningsstationerna. De får därför utvärderas i en separat TOT-modell, medan de
fortsatt är förbjudna där målstationens profil bara är en uppmätt körbana.

### D2 — Förregistrera kandidatfamiljen

Kör kandidater i stigande komplexitet:

1. **B0:** plan andel lika med ankaren i varje intervall;
2. **B1:** nuvarande q50-form plus nuvarande periodprojektion;
3. **B2:** nuvarande q50-form plus exakt dygnsprojektion;
4. **C1:** TOT-semantiskt matchad, datalärd profil plus exakt
   dygnsprojektion;
5. **C2:** C1 med held-out-lärd amplitud och trendregularisering;
6. **C3, endast om C2 inte räcker:** dagblockerade residualprofiler för
   diagnostiska scenarier.

Därtill **L0–L3** ur avsnittet *Lokal korridorevidens*, som mäts i samma
foldfamiljer och mot samma baslinje B0. L0 är förregistrerad som förkastad med
mätt skäl och finns endast som spårbarhet.

Ingen kandidat får läggas till efter att held-out-resultaten har öppnats utan
en ny registreringsversion. Handvalda amplitudgränser är endast diagnostiska
stressfall och kan inte vinna punktmodellgrinden.

### D3 — Validera som verklig användning

Använd tre separata foldfamiljer:

- `blocked_date`: framtida sammanhängande dygn hålls undan;
- `leave_station_out`: en helt ny sensor hålls undan;
- `leave_city_out`: hela målorten hålls undan.

Stratifiera minst på vardag/helg, vägtyp, trafiknivå, transferavstånd och
fullständigt/partiellt dygn. Göteborg 107 används aldrig som shape ground
truth eftersom dess riktningar inte är mätta per intervall.

Primära mått:

- volymviktad MAE i riktad andel;
- MAE/GEH för riktade timvolymer;
- fel i morgon- och eftermiddagsfönstrens riktningsmassa;
- fel i tidpunkt och tecken för riktningsvändning;
- fel i dygnets robusta amplitud, exempelvis p90 minus p10;
- komplementaritet, totalbevarande och dygnsankarfel som hårda kontroller.

Sekundära mått är jämnhet, antal extrema hopp, inference-tid och andelen dagar
som går till fallback. Alla mått rapporteras per foldgrupp; ett bra pooled
medel får inte dölja en förlorad stad eller vägtyp.

### D4 — Gate D väljer policy, inte önskat utfall

Gate D beslutar i denna ordning:

1. **Är `daily_exact` försvarbart?** Jämför B2 mot B1 på maskerade stationer
   där en periodankare beräknas från träningsdelen. Om den hårda dygnslåsningen
   orsakar en materiell held-out-förlust väljs `daily_band` eller
   `period_only`, även om `daily_exact` ser stabilare ut.
2. **Finns användbar intradagssignal?** C1/C2 måste slå B0 i minst en primär
   grupp inom varje foldfamilj och får inte förlora en primär grupp utanför en
   förregistrerad non-inferiority-marginal.
3. **Är transfern till målstationen giltig?** Statisk och dynamisk
   applicability måste ligga inom den frysta träningspopulationens stöd.
4. **Är komplexiteten motiverad?** Vid statistiskt och beslutmässigt likvärdigt
   resultat vinner den enklare kandidaten.

Non-inferiority-marginaler ska härledas från mätupplösning och
inomstationsvariation i endast träningsdelen och frysas före held-out-körning.
En obyggbar fold, otillräcklig effektiv stickprovsstorlek eller semantisk
feature-mismatch ger `INCONCLUSIVE`, aldrig `MODEL`.

Möjliga utfall:

- `FLAT_DAILY`: exakt dygnsankare, ingen intradagsform;
- `SHAPED_DAILY`: exakt dygnsankare med validerad form;
- `SHAPED_BAND`: validerad form och dygnsband, inte hård punkt;
- `PERIOD_ONLY`: nuvarande periodsemantik behålls;
- `INCONCLUSIVE`: ingen ny produktaktivering.

### D5 — Implementera en ren projektionskärna

Först efter Gate D:s registrering implementeras en ren funktion utan fil-I/O
som tar totaler, basprofil, ankare och policy och returnerar båda riktningarna
plus diagnostik. Den ska vara deterministisk, använda en robust monoton
root-solver och faila stängt på NaN, negativ volym, fel längd, ogiltig ankare
eller omappad riktning.

Kärnan ska arbeta över hela kalenderdygnet innan ett 06–10- eller annat
delfönster skärs ut. Annars kan ett deldygn felaktigt göras till 52/48 trots
att ankaren avser hela dagen.

### D6 — Hantera luckor, DST och heltalsflöden explicit

- `None` förblir saknat och blir aldrig noll.
- Ett komplett dygn använder alla kvalitetsgodkända intervall.
- Ett partiellt dygn får bara `daily_exact` om en separat godkänd imputering
  levererar totalmassan; annars används den registrerade fallbacken och
  orsaken skrivs ut.
- Ett nollmassedygn gör ankarekvationen vakant och använder en märkt
  profilfallback utan att påstå att splitten observerats.
- DST-dygn använder faktiska lokala tidsintervall och en explicit
  tidszons-/foldpolicy; 92/100 intervall får inte tyst pressas till 96.
- Kontinuerliga targets uppfyller ankaren numeriskt exakt. Efter
  heltalsreparation rapporteras minsta uppnåeliga avrundningsfel i fordon; det
  får inte döljas genom flytt av totalmassa mellan kvart.

### D7 — Integrera utan att ge PFE ny auktoritet

`build_targets` ska konsumera ett färdigt `DailyDirectionProfile`. PFE får inte
ändra splitten för att förbättra andra sensorers residualer. Om en senare
gemensam nätinferens ska prövas är det en separat kandidat med egen maskerad
held-out-validering och får aldrig tränas och bedömas på samma räknepunkter.

Alla tre nuvarande demandvägar — vanlig PFE, routeSampler-targets och
stressbyggen — måste läsa samma centrala profilkontrakt. q-/stressarmar måste
fortsatt hålla identisk totalbefolkning och använda hela dagsresidualblock,
inte oberoende extrema kvart.

### D8 — Provenance och observerbarhet

Varje byggd dag ska bära:

- sensor-id och båda edge-id:n med orientering;
- reference- och policyversion;
- källperiod, applikationsperiod och om överföring användes;
- modell-, dataset- och Gate D-hash;
- inputtotalens digest;
- basprofil, amplitud, offset, mål- och uppnådd dygnsandel;
- min/max/p10/p90, fallbackorsak och supportstatus;
- tydlig etikett `estimated_per_slot`, även när dygnsankaren är lokal.

UI/API får säga “dygnsförankrad uppskattning”, aldrig “uppmätt riktning”, om
bara totalen mättes.

### D9 — Verifiera downstream-beslut innan bred utrullning

Om den vinnande profilen ändrar riktad massa materiellt mot aktiv policy körs
en liten, portabel matched-seed Gate S med identiska totaler, routespec och
SUMO-seeds. Den avgör om closure-rankning eller viable set påverkas. Ett
`NO` betyder att den bättre riktningen kan aktiveras utan ny ensembleprodukt;
ett `YES` öppnar endast den minsta nödvändiga scenariointegrationen.

### D10 — Rulla ut sensor för sensor

Aktivera först 107 bakom ett explicit policyval. Nästa TOT-sensor kräver egen
orienteringsverifiering, reference-/policybeslut och applicability-rapport,
men ska återanvända samma kod och tester. Inga sensor-id:n eller andelar får
hårdkodas i projektionskärnan.

## Obligatoriska tester

Minst följande ska finnas före aktivering:

- exakt komplement och exakt bevarad tvåvägstotal i varje intervall;
- exakt volymviktad dygnsankare för kompletta dygn;
- amplitudändring påverkar form men inte dygnsandel;
- flat input förblir flat;
- edge-id-sortering kan inte byta fysisk riktning;
- två olika TOT-sensorer med olika ankare fungerar i samma bygg;
- enkelriktade sensorer är byte-identiska;
- multi-day bygger och projekterar varje kalenderdygn separat;
- ett 06–10-fönster använder projektionen från hela dygnet;
- helg, helgdag, DST, lucka, nollmassa och ogiltig policy failar enligt kontrakt;
- 2025-reference tillämpas inte på 2027 utan explicit transferbeslut;
- gammal `period_only` reproduceras byte-identiskt;
- gamla demand-, route-, warm- och releaseartefakter skrivs inte om;
- serialiserad provenance kan verifieras från en ren checkout.

För den lokala korridorvägen finns dessa redan i
`tests/test_local_direction_evidence.py` och ska utökas när en L-kandidat byggs:

- en saknad kvart förblir saknad och blir aldrig noll;
- ett delvis rapporterat par hoppas över i stället för att summeras halvt;
- en tvåvägstotal räknas en gång, inte två (rapporteringsfällan);
- donatorupptäckt sker från gatunamn, geometri och registersemantik, aldrig
  från ett sensor-ID;
- en tvåvägsleverans kan aldrig bli donator;
- donatorns nivå är mätt **oanvändbar** som andel, och det talet är pinnat;
- noden är mätt öppen, så konservering ensam kan inte låsa splitten;
- den normaliserade formen är dimensionslös och omsluter 1,0.

## Filer som sannolikt berörs vid implementation

- `traffic_sim/intake/sensors.py` och `data_in/sensors.json`: separat
  applikationspolicy;
- ny ren modul, exempelvis `dirsplit/daily.py`: profilprojektion och
  diagnostik;
- ny dataset-/evaluationsväg under `dirsplit/`: maskerade dagsblock och Gate D;
- `demand/intake.py` och `build_sumo_demand.py`: hel-dygnsprojektion före
  fönsterslicing;
- tester för sensorregister, targets, PFE-totaler, multi-day och provenance;
- append-only filer under `validation/` för registrering och utfall;
- redan på plats för den lokala vägen:
  `tools/measure_local_direction_evidence.py`,
  `validation/local_direction_evidence_v1.json` och
  `tests/test_local_direction_evidence.py`.

`ARCHITECTURE.md` uppdateras först när ett Gate D-utfall faktiskt aktiverar ett
nytt kontrakt. Den här planen ensam ändrar inte produktarkitekturen.

## Klart när

Planen är genomförd först när:

1. den maskerade dagtabellen och alla råhashar är reproducerbara;
2. Gate D har alla tre foldfamiljer och ett append-only utfall;
3. den valda policyn är enklast bland icke-underlägsna kandidater;
4. samtliga total-, komplement-, dygns-, tids- och provenanceinvariants passerar;
5. sensor 107:s 52/48 fortfarande är märkt som 2025-aggregat plus separat
   modellpolicy, inte som 96 eller 365 uppmätta splitter;
6. saknade data och out-of-support aldrig ger en omärkt profil;
7. downstream-känslighet är mätt om förändringen är materiell;
8. fler TOT-sensorer kan läggas till genom data och policy, utan ny
   sensorspecifik kod;
9. den lokala korridorvägen är mätt mot samma baslinje och foldfamiljer som den
   överförda, och den vinnande formkällan är vald på evidens — inte på att lokal
   data känns mer relevant.

## Rekommenderad första implementation

Genomför D0–D4 innan produktkod ändras. Om `SHAPED_DAILY` vinner är den första
produktionsversionen C1 eller C2 ovan: TOT-semantiskt matchad dagsprofil,
held-out-fryst amplitud och exakt volymvägd dygnsprojektion. Om den inte vinner
ska 107 inte få större intradagsfrihet bara för att ett mer varierat flöde ser
rimligare ut; välj då `FLAT_DAILY`, `SHAPED_BAND` eller `PERIOD_ONLY` enligt
Gate D:s evidens.
