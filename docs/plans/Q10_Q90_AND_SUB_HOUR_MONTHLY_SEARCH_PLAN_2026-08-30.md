# Reviderad plan: exakt månadssökning under en timme och beslut om q10/q90

**Datum:** 2026-08-30  
**Status:** Forsknings- och implementationsplan. Ingen ny simulering har startats
och ingen produktpolicy ändras av dokumentet. Den stoppade septemberkörningen
ska inte återupptas.  
**Mål:** Hitta det billigaste giltiga blocket med fem sammanhängande dagar och
samma dagliga stängningsfönster, med samma exakta objektiv och säkerhetsgrindar,
men normalt på mindre än 60 minuter på nuvarande dator.

**Automatiskt AI-uppdrag:** `.ai-flow/tasks/subhour-closure-search.md`. Kör med
`./ai-flow --task-file .ai-flow/tasks/subhour-closure-search.md --allow-dirty`
när den nuvarande dirty worktreen medvetet ska ingå i reviewunderlaget. Kommandot
startar inte en full månad; uppdragets scope slutar efter Fas 0–4 och review.

## Beslut i en mening

Den bästa evidensstödda vägen är att behålla dagens robusta deterministiska
q10/q50/q90-kostnad, prissätta alla kandidater utan SUMO, verifiera endast den
billigaste beslutsrelevanta prefixen i SUMO och stoppa med ett kontrollerbart
gränsbevis. q10/q90 körs tills vidare som stress endast för den verifierade
prefixen. Ett separat, senare känslighetstest avgör om de kan tas bort.

Detta är en strukturell hastighetsförbättring: den minskar mängden exakt
simuleringsarbete. Fler workers kan sedan minska väggtiden ytterligare, men är
inte själva lösningen.

## Vad som ändrades efter den kritiska reviewn

Den föregående planen var för bred och hade fel ordningsföljd. Följande är nu
ändrat:

1. En färdig exhaustiv månadskörning är **inte** längre en förutsättning. Den
   körningen stoppades och dess nya records rensades. Ekvivalens ska i stället
   prövas med samma cost-ordered-kodväg i två armar: tidigt stopp på respektive
   av.
2. Ett nytt `WindowCostIndex` är **inte** obligatoriskt. Den befintliga exakta
   kostnadsvägen profileras först. Indexet byggs bara om kostnadsfasen överskrider
   sin tiominutersbudget.
3. q10/q90 tas inte bort för att nå tidsmålet. De behålls i den billiga
   deterministiska prissättningen och för SUMO-verifierade finalister. Frågan om
   deras framtida roll flyttas till en separat beslutskänslighetsgrind.
4. En olöst SUMO-timeout får inte längre slå på full exhaustion. Den ska stoppa
   körningen omedelbart som `INCONCLUSIVE_TIMEOUT`. Att fortsätta kan inte skapa
   ett giltigt vinnarbeslut och slösar därför bara tid och lagring.
5. Ett absolut 60-minuterslöfte och ett garanterat vinnarresultat går inte att
   förena i alla möjliga fall. Om för många billiga kandidater faller, ligger i
   ett exakt tie-band eller får timeout måste körningen sluta fail-closed som
   `INCONCLUSIVE_BUDGET_EXHAUSTED`, aldrig publicera en osäker vinnare.

## 1. Verifierat nuläge

### 1.1 Den verkliga sökfrågan

Septemberfallet innehåller:

- 30 möjliga arbetsdatum;
- 65 åttatimmarsfönster per datum på 15-minutersgrid;
- 1 950 unika dagliga enheter;
- 1 690 föräldrakandidater: 26 startdatum gånger 65 fönster;
- fem sammanhängande dagar med samma klockfönster per förälder.

Målet är alltså inte att välja fem oberoende billiga dagar. Det är:

```text
minimera kostnad(startdatum, klockfönster)
över fem sammanhängande dagar med samma klockfönster
```

Den konkreta billiga beräkningen är precis den användaren föreslog, med
sammanhängandevillkoret bevarat: bygg en exakt matris
`C[datum, klockfönster]`, och beräkna sedan varje förälder med ett glidande
femdagarsfönster över samma kolumn. Använd dagens exakta robusta
variantaggregation och lexikografiska fält; välj inte fem fristående minima.

Den stoppade exhaustiva designen krävde i värsta fall:

```text
1 950 dagliga enheter × 3 demandvarianter × 2 armar
= 11 700 SUMO-starter
```

De tre varianterna per dag är q10, q50 och q90 — tre riktningsefterfrågefall,
inte tre slumpmässiga repetitioner. Varje variant hade dessutom ett matched
baseline/candidate-par. Den nya planen behåller de tre stressfallen endast där
SUMO faktiskt kan påverka finalistbeslutet.

Den observerade takten, cirka 130–140 kompletta dagliga enheter per timme med
åtta workers, visar att målet inte kan nås genom små process- eller
komprimeringsförbättringar. Antalet SUMO-verifieringar måste minska med klart
över 90 procent i ett normalt fall.

### 1.2 Varför det går att stoppa tidigt utan approximativ ranking

Produktobjektivet `closure_cost_v1` är deterministiskt och lexikografiskt:

1. `added_vehicle_hours`;
2. `added_metres_total`;
3. `vehicles_affected`;
4. `vehicles_no_detour > 0` diskvalificerar.

SUMO ska kvalificera eller diskvalificera en kandidat genom routing-, closure-,
hälso- och återhämtningsbevis. SUMO ska inte skapa en ny kostnadsordning.

Om alla kandidater först prissätts exakt och sorteras kan verifieringen gå från
billigast till dyrast. När två giltiga kandidater är verifierade och nästa
osimulerade kandidats exakta kostnad ligger strikt utanför det tillåtna
tie-/ekvivalensbandet kan ingen senare kandidat bli finalist. Det är ett
deterministiskt gränsbevis, inte en statistisk gissning.

Screening före dyr simulering är också den etablerade grundstrukturen i
ranking-and-selection för många alternativ. Nelson et al. beskriver just
screening för att undvika den dyra senare simuleringen av icke-konkurrenskraftiga
alternativ: [INFORMS, 2001](https://pubsonline.informs.org/doi/10.1287/opre.49.6.950.10019).

### 1.3 Vad repositoryt redan har

`cost_ordered_search.py` och `cost_ordered_execution.py` har redan:

- exakt kostnadsledger och deterministisk sortering;
- content-bunden cursor och restart;
- maskinläsbart stoppbevis;
- `disable_early_stop=True`, som kör samma ordning och samma verifierare men
  stänger av endast stoppet;
- fail-closed kontroll att pre-SUMO- och post-SUMO-kostnaden är
  fältidentisk.

Det betyder att nästa steg är att reparera och bevisa den befintliga kärnan,
inte att ersätta den med en ny optimerare.

Den senaste registrerade real-SUMO-sviten (`cost_ordered_benchmark_outcome_v5`)
visade potential men blev korrekt underkänd:

- 18 kandidatverifieringar sparades totalt;
- ett beslutsekvivalent men ännu inte fullständigt gate-godkänt fall gick från
  13 till 2 verifierade kandidater och från cirka
  1 145 till 206 sekunder;
- sviten misslyckades ändå på fältidentisk kostnad, selected IDs,
  hälsoklassificering, hårda fel och restart i flera fall.

Det är inte releasebevis. Det visar både att vinsten är verklig och exakt vad
som måste vara grönt innan produktaktivering.

### 1.4 Baseline återanvänds redan

Baselines caches redan på exakt:

- demand-arkivets digest;
- variant och seed;
- antal intervall, duration, begin och flush;
- SUMO/runtime-identitet.

Kandidater med samma exekveringsfönster kan därför redan dela en baseline.
Att lossa cache-nyckeln skulle riskera fel evidens och är inte en ny huvudvinst.

### 1.5 Vad q10 och q90 faktiskt är

`dirsplit/predict.py` konstruerar q10/q90 från timvisa 0,1- och 0,9-kvantiler
av leave-city-out-residualer från norska träningsstäder. Timvärdena sätts ihop
till en profil och klipps till giltiga andelar. Profilen behöver därför inte
motsvara en observerad sammanhängande dag.

`sumo/direction_split.json` säger uttryckligen:

- `band = leave_city_out_residual_q10_q90`;
- `band_calibration = uncalibrated for Gothenburg`.

q10/q90 är alltså användbara stressfall, men inte ett kalibrerat 80-procentigt
sannolikhetsintervall för Göteborg. Robust optimering kan använda ett
deterministiskt osäkerhetsset när sannolikhetsmodellen är okänd, men setets
utformning och konsekvens måste redovisas; se Bertsimas, Brown och Caramanis:
[Theory and Applications of Robust Optimization](https://arxiv.org/abs/1010.5445).

## 2. Vald målarkitektur

```text
alla 1 690 föräldrar
        │
        ▼
exakt deterministisk q10/q50/q90-ledger
        │
        ▼
sortera på oförändrat closure_cost_v1
        │
        ▼
SUMO-verifiera billigaste föräldern, robust på dess 5 dagar
        │
        ├── hårt fel → backfilla nästa kandidat
        ├── timeout → INCONCLUSIVE_TIMEOUT och stopp
        └── giltig → fortsätt tills minst 2 giltiga
        │
        ▼
nästa exakta kostnad > cutoff + tie-band?
        │
        ├── ja → publicera stoppbevis och finalister
        └── nej → verifiera nästa kandidat
```

För varje faktiskt verifierad identitet gäller fortfarande samma matched
baseline/candidate-par, seeds, closure-routing, teleports, denials, leak,
recovery, health och provenance.

Kandidater utanför det bevisade kostnadsbandet ska märkas
`not_run_decision_irrelevant`. De får aldrig beskrivas som simulerade, friska
eller godkända.

## 3. Kritisk genomförandeväg

### Fas 0 — Frys kontrakt och budget

Skapa en preregistrering innan nya utfall mäts. Den ska binda:

- exakt search spec, nät, demand, route-, policy- och source-digests;
- `closure_cost_v1`, tie-band och min/max finalister;
- q10/q50/q90:s nuvarande robusta aggregationssemantik;
- samma SUMO-version, seeds, timeout och workers i jämförda armar;
- en total aktiv budget på 55 minuter plus 5 minuters publiceringsreserv;
- de fyra tillåtna terminala resultaten:
  `READY`, `INCONCLUSIVE_TIMEOUT`, `INCONCLUSIVE_CAPACITY` och
  `INCONCLUSIVE_BUDGET_EXHAUSTED`.

Ingen kod får automatiskt övergå till exhaustiv SUMO efter en sådan terminal.

### Fas 1 — Reparera cost-ordered-ekvivalensen

Använd samma ledger, verifierare, kandidatordning och checkpointkod i båda
benchmarkarmarna. Den enda skillnaden ska vara:

```text
cost-ordered:       disable_early_stop = false
ordered-exhaustive: disable_early_stop = true
```

Reparera först de avvikelser som v5 exponerade. Särskilt:

- exhaustive-armen måste få samma deterministiska disruptionfält;
- observations-/seedordning får inte ändra timeout eller hårda fel;
- restart ska återge exakt samma cursor, evidensmängd och beslut;
- runner-evidens ska avvisas om ett enda kostnadsfält skiljer sig från ledgern.

Ändra timeoutbeteendet i produktarmen:

- första olösta timeout avslutar med `INCONCLUSIVE_TIMEOUT`;
- bevara timeoutens exakta attempt records;
- publicera ingen vinnare och inget bandstopp;
- starta inga nya kandidater efter terminalen;
- återstart får inte återöppna en terminal cursor utan explicit ny körning.

Detta är fail-closed men mycket billigare än dagens beteende, som fortsätter
till full exhaustion trots att selectorn ändå måste bli inconclusive.

### Fas 2 — Testa hörnfall utan dyr månadskörning

En syntetisk deterministisk suite ska täcka minst:

1. två första kandidater giltiga och nästa utanför bandet;
2. billigaste kandidaten faller och backfill krävs;
3. deterministisk `no_detour` före SUMO;
4. exakt tie vid bandgränsen;
5. fler ties än `maximum_finalists`;
6. timeout före och efter första giltiga kandidat;
7. cancel och resume efter varje cursorposition;
8. korrupt/swapad ledger, evidence, baseline och route provenance;
9. lika primärkostnad men olika sekundär/tertiär tie-break;
10. ingen giltig kandidat.

Varje fall jämför tidigt stopp med ordered-exhaustive och kräver samma
beslut där ett beslut är möjligt.

### Fas 3 — Preregistrerad bounded real-SUMO-ekvivalens

Välj utfallsblint minst åtta små fall över minst:

- fyra olika directed edges;
- två demandperioder;
- ett fall med backfill;
- ett no-detour-fall;
- ett fall med tät kostnadsgräns;
- ett restart/cancel-fall.

Kör båda armarna kallt, med en daily worker och en seed worker, så att
schedulerordning inte blandas ihop med den strukturella vinsten.

Alla grindar måste vara gröna:

- exakt samma terminalstatus när ingen budgetterminal inträffar;
- exakt samma selected IDs och slutbeslut;
- fältidentisk kostnadsledger för **alla** kandidater;
- identiska hårda fel och hälsoklassificeringar för den verifierade prefixen;
- giltigt maskinläsbart stoppbevis;
- restart-/cancel-ekvivalens;
- minst 30 procent färre exakta kandidatverifieringar;
- minst 30 procent lägre aktiv väggtid;
- ingen peak-RSS- eller lagringsregression.

Det krävs inte att den korta armen producerar hälsoklassificering för en
matematiskt beslutsointressant kandidat. Det krävs att den uttryckligen visar
att kandidaten inte kördes och varför den inte kan påverka beslutet.

### Fas 4 — Profilera den exakta kostnadsfasen

Kör en SUMO-fri dry-run över alla 1 950 dagliga enheter och alla tre varianter.
Mät separat:

- XML-parse;
- fordons-/routegruppering;
- shortest-path/detour;
- fönsteraggregation;
- föräldrasummering och sortering;
- cache hits, peak RSS och disk.

Beslutsregel:

- om totalen är högst 10 minuter och högst 20 procent av den beräknade
  end-to-end-tiden: behåll nuvarande implementation;
- annars implementera Fas 5;
- optimera aldrig på uppskattad profil eller på en varm cache utan att även
  redovisa cold-resultatet.

### Fas 5 — Villkorad `WindowCostIndex`

Denna fas är endast tillåten om Fas 4 missar budgeten.

För den fasta closure edge-mängden ska varje datum/variant-routefil parsas en
gång. Bygg därefter prefixsummor eller motsvarande exakta intervallaggregat för
varje 15-minutersfönster. Detour för en unik routeform beräknas en gång och
vägs sedan med exakt fordonsmängd i fönstret.

Obligatorisk oracle-grind:

- jämför samtliga 1 950 × 3 dagliga records mot dagens implementation;
- kräv exakt likhet för `vehicles_affected`, `vehicles_no_detour`,
  `added_metres_total`, `added_vehicle_hours`, refusals och tie-breakfält;
- bind route-, network-, closure- och policy-digests i indexet;
- avvisa partiellt, gammalt eller swap-at index;
- mät att indexet verkligen sparar väggtid och inte bara flyttar arbete till
  en dold förberedelsefas.

### Fas 6 — Budgetstyrd full månad

Kör först när Fas 0–4, och Fas 5 om den behövdes, är granskade och gröna.

Aktiv tidsbudget:

| Del | Budget |
|---|---:|
| Preflight + exakt ledger | 10 min |
| SUMO-verifierad prefix + backfill | 40 min |
| Finalisering och evidensvalidering | 5 min |
| Reserv | 5 min |

Regler:

- beräkna ETA från avslutade identiska work units, inte från processstarter;
- efter 10 minuter ska manifestet visa faktisk ledger-tid och verifieringstakt;
- vid 45 minuter får nya kandidater startas endast om konservativ ETA ryms
  före 55 minuter;
- vid 55 minuter stoppas nya starter, pågående identitet avslutas/cancelas
  enligt befintligt säkert kontrakt och resultatet blir
  `INCONCLUSIVE_BUDGET_EXHAUSTED`;
- ingen automatisk fallback till exhaustive;
- `READY` kräver ett giltigt stop proof och full evidensvalidering.

Den realistiska hypotesen är att endast 2–6 föräldrar behöver verifieras i ett
normalt fall i stället för 1 690. Det är inte ett löfte: täta ties, många
diskvalificeringar eller timeout kan kräva mer och ska då ge ett transparent
inconclusive-resultat.

### Fas 7 — Separat beslut om q10/q90

Prestandavägen aktiverar inte automatiskt q50-only.

Kör en förregistrerad Gate S på den verifierade prefixen och den bounded real-
sviten. Jämför:

1. `ROBUST_FINALIST`: nuvarande robusta q10/q50/q90-kostnad och alla tre
   stressarmar på verifierade kandidater;
2. `Q50_PLUS_STRESS`: q50 normalarm, q10/q90 endast före finalistgodkännande;
3. `Q50_ONLY`: q50 i både kostnad och SUMO.

Tillåtna policyutfall:

- `ROBUST_THREE_VARIANT`: en stressvariant ändrar beslutsrelevant kostnad,
  finalist eller hårt fel;
- `FINALIST_STRESS`: q10/q90 påverkar finalistkvalificering men behöver inte
  köras utanför cost-ordered-prefixen;
- `Q50_ONLY`: endast efter noll beslutsregret, identiska finalister och 100
  procent recall av beslutsrelevanta variantunika fel i den förregistrerade
  sviten;
- `INCONCLUSIVE`: otillräcklig lokal kalibrering, coverage eller teststyrka.

Även om q10/q90 behålls ska användargränssnittet kalla dem
`low/high direction stress`, inte sannolikhetsgränser.

## 4. Varför övriga idéer inte ligger på kritiska vägen

### Fler SUMO-trådar

SUMO:s FAQ säger att mikrosimuleringen körs på en kärna och att `--threads`
ännu inte ger meningsfull speedup för kärnsimuleringen. Oberoende processer är
rätt sätt att använda flera kärnor, vilket projektet redan gör. Fler än åtta
workers kan profileras senare men minskar inte det strukturella arbetet.
[SUMO FAQ](https://sumo.dlr.de/docs/FAQ.html)

### Libsumo

Libsumo tar bort TraCI:s socket-overhead och kan vara värdefullt för
stegintensiva TraCI-program. Den här vinsten ska inte antas för en runner vars
huvudkostnad är separata mikrosimuleringar och fil-/routearbete. Parallella
libsumo-instanser kräver dessutom multiprocessing. Lägg endast till en bounded
paired benchmark efter att cost-ordered-vägen är klar.
[SUMO Libsumo](https://sumo.dlr.de/docs/Libsumo.html)

### Save/load och warm state

SUMO stöder state save/load, men dokumentationen varnar bland annat för att
framtida vehicles inte finns i staten, att original-routefilen fortfarande
behövs för flows, att RNG-state inte sparas som standard och att vissa interna
car-follow/lane-change-tillstånd inte sparas. Repositoryts warm-state-väg har
redan en strikt ekvivalensgrind och är inte godkänd för de nya trimmade
fönstren. Den får inte användas utan en ny paired cold/warm-kampanj.
[SUMO SaveAndLoad](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html)

### Mesosimulering

SUMO anger att meso kan vara upp till 100 gånger snabbare, men modellen har
grövre kö-, korsnings- och lane-change-semantik. Det ändrar den vetenskapliga
modellen och kan därför endast användas som separat screening med en bevisad
recall-grind, inte som ersättning för final SUMO-evidens.
[SUMO Meso](https://sumo.dlr.de/docs/Simulation/Meso.html)

### Approximerad proxy eller ML-ranker

En proxy kan prioritera arbete men får inte skapa stop proof om den inte är en
bevisad lägre gräns. Eftersom den exakta deterministiska closure-kostnaden redan
är tillgänglig till mycket lägre kostnad än SUMO finns ingen anledning att göra
vinnarrankningen approximativ i första versionen.

## 5. Acceptanskriterier före produktaktivering

### Korrekthet

- Samma search scope: 30 datum, 65 fönster och exakt fem sammanhängande dagar.
- Samma `closure_cost_v1`, tie-band och no-detour-semantik.
- Exakt field equality i den deterministiska ledgern.
- Samma status, selected IDs och vinnare som ordered-exhaustive i alla
  beslutsbara preregistrerade fall.
- Samma hårda fel och hälsoklass för alla faktiskt verifierade identiteter.
- Giltigt stop proof för varje `READY`.
- Skippade kandidater är `not_run_decision_irrelevant`.

### Säkerhet och evidens

- Original origin → destination och snabbaste lagliga väg utan stängda edges.
- Inga looser timeouts, teleporttrösklar, health gates eller provenancekrav.
- Matched baseline, route, access-impact och canonical observation är
  hash-resolverbara.
- Cancel/resume/restart och terminal cursor är testade.
- Ingen gammal och ny backendproveniens blandas.

### Prestanda

- Minst 30 procent färre exakta SUMO-verifieringar och aktiv väggtid i den
  bounded paired-sviten.
- En komplett cold real-månad `READY` på mindre än 60 minuter på samma dator.
- Tills minst tio oberoende cold real-månader finns får rapporten säga
  `single-run sub-hour`, inte p95 under en timme.
- Efter tio körningar kräver produktpåståendet p95 under 60 minuter och ingen
  korrekthets-/evidensregression.

## 6. Implementationsordning för en automatisk AI

1. Läs `AGENTS.md`, current blocks, arkitektur, dirty diff och v5-evidensen.
2. Frys Fas 0-preregistreringen utan att läsa nya outcomes.
3. Gör product cost-ordered timeout terminal och fail-closed; lägg till tester
   för terminal cursor och att inga senare kandidater startas.
4. Gör ordered-exhaustive och tidigt stopp till samma exekveringsväg med endast
   `disable_early_stop` som skillnad.
5. Reparera fält-, health-, failure-, selected-ID- och restartavvikelserna från
   v5; ändra inte gates för att få grönt.
6. Kör Fas 2:s syntetiska suite.
7. Registrera och kör Fas 3:s bounded real-SUMO-suite; gör en oberoende review.
8. Profilera hela SUMO-fria ledgern.
9. Implementera `WindowCostIndex` endast om den uppmätta tiominutersgränsen
   missas; kräv full 1 950 × 3-oracle.
10. Lägg till budgetterminal, ETA och tydlig UI/API-status.
11. Kör en ny, isolerad fullmånad först när alla tidigare grindar är gröna.
12. Kör Gate S separat och ändra q-policyn endast efter dess beslut.

## 7. Copy-paste-uppdrag till implementerande AI

```text
Implementera den reviderade planen i
docs/plans/Q10_Q90_AND_SUB_HOUR_MONTHLY_SEARCH_PLAN_2026-08-30.md.

Målet är en exakt cost-ordered septemberkörning som normalt blir READY på
under 60 minuter på nuvarande dator. Bevara exakt fem sammanhängande dagar med
samma åttatimmarsfönster, closure_cost_v1, q10/q50/q90:s nuvarande robusta
deterministiska kostnad, matched baseline, routing, health, recovery,
provenance, tie och no-detour-semantik.

Arbeta i denna ordning:
1. Frys preregistrering och tidsbudget.
2. Gör olöst timeout terminal som INCONCLUSIVE_TIMEOUT; fortsätt aldrig
   exhaustivt efter timeout eller budgetterminal.
3. Bevisa cost-ordered mot ordered-exhaustive via samma ledger, runner,
   ordning, cursor och verifierare; endast disable_early_stop får skilja.
4. Reparera alla v5-avvikelser i kostnadsfält, hard failures, health,
   selected IDs och restart utan att lösa upp någon gate.
5. Kör syntetiska hörnfall och minst åtta preregistrerade bounded real-SUMO-
   fall. Kräv identiskt beslut där beslut är möjligt, giltigt stop proof och
   minst 30 procent lägre exakt SUMO-arbete och aktiv tid.
6. Profilera hela 1950x3 SUMO-fria ledgern. Bygg WindowCostIndex endast om
   fasen tar över 10 minuter eller 20 procent av totalbudgeten; om den byggs,
   jämför samtliga 1950x3 records fält för fält med oracle.
7. Implementera 55-minuters hard stop plus 5 minuters publiceringsreserv.
   Budgetmiss ska ge INCONCLUSIVE_BUDGET_EXHAUSTED, aldrig en osäker vinnare.
8. Starta ingen full månad förrän implementation, tester och oberoende review
   är gröna. Kör q10/q90 Gate S som ett separat senare beslut.

Märk osimulerade kandidater not_run_decision_irrelevant. Påstå aldrig att de
är friska. Skapa nya append-only evidence-ID:n, bevara användarens övriga
dirty changes och commit/pusha/deploya inget utan uttrycklig begäran.
```

## 8. Slutlig reviewbedömning

Det går inte att matematiskt säga att någon plan är “absolut bäst” utan att
mäta alla konkurrerande implementationer. Denna plan är däremot den starkaste
som nu stöds av både repositoryts evidens och externa primärkällor, eftersom
den:

- angriper den dominerande kostnaden: antalet exakta SUMO-körningar;
- återanvänder en redan byggd exact cost-ordered-kärna;
- gör den största nya arkitekturen villkorad av profilering;
- skiljer vetenskaplig q-policy från prestanda;
- bevarar ett verifierbart exakt beslut när `READY` publiceras;
- håller den hårda timgränsen genom ett ärligt inconclusive-resultat i
  patologiska fall.

Nästa beslutspunkt är därför entydig: implementera och granska Fas 0–3. Starta
inte en ny fullmånad ännu.
