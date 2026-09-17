# Gothenburg Traffic Simulation Improvement Plan

## Körinstruktion för hastighetsarbetet — 2026-09-12

Detta är den aktuella instruktionen för en implementerande modell. Den ersätter
den preliminära ordningen i forskningscheckpointen nedan. Äldre avsnitt behålls
som historik. Uppgiften här är resultatneutral prestandaförbättring; ingen av de
föreslagna ändringarna är genomförd enbart för att den står i dokumentet.

### Börja här: uppdrag, nuläge och avgränsning

1. Arbeta i `/Users/gt/Documents/gs-project`. Läs `AGENTS.md`, aktuella markerade
   block i `TASKS.md`/`AGENT_NOTES.md` och relevanta kontrakt i `ARCHITECTURE.md`.
   Kontrollera `git status --short` och diffen för varje fil du tänker ändra.
   Arbetskopian innehåller många andra ändringar: återställ eller skriv inte över dem.
2. Läs `validation/monthly_minutes_research_20260912.md`. Referenskörningen är
   `runs/closure-search/speed-monthly-june-20260912/artifacts/result.json`.
   Körningen är SLUTFÖRD, trots äldre samordningsnotiser om aktiv PID 47204.
3. Utför stegen nedan i ordning. Ett experiment utan visad nettovinst ska
   dokumenteras och lämnas oaktiverat. Fortsätt till nästa oberoende steg.
4. Denna dokumentbeställning är inte en beställning av en ny månadskörning,
   generell uppvärmning, commit, push eller publicering. Vid senare implementering
   används avgränsade diagnostiska körningar i egna utmappar. Använd redan sparade
   spår när de är fullständiga. Starta ingen lång körning bara för att testa en hjälpfunktion.
5. Ändra inte seedantal, mätband, sensoruppsättning, kvartlängd, simuleringshorisont,
   OD/ändamålspopulationer, trafikmodell, ruttpool, stoppbevis eller lösartoleranser.
   q10/q50/q90 kan ha olika fordonsantal; påtvinga aldrig q50:s population på dem.
6. Behåll datum PLUS poolsammansättning och övrig källidentitet i återanvändningen.
   Datum-only-återanvändning är inte godkänd. Radera inte källhashar för att få träffar.

### Mätbas: använd rätt siffror

Total aktiv tid var 3758,937 s, cirka 62 min 39 s. 29 är antalet scheman;
backend använder 31 underlag. 30 arkiv byggdes nya, ett återanvändes från canary.
49 passagekalibreringar utfördes under körningen. Slutrapportens 52 inkluderar
tre historiska kalibreringar. Redovisa utfört arbete separat från ärvd diagnostik.

| Exklusiv tidskategori | Tid | Säkerhet |
| --- | ---: | --- |
| Passagekalibrering | 2092,043 s | Direkta timers, avstämda mot nya arkiv |
| Övrigt inom PFE/variantbygget | 672,457 s | Differens mellan nästlade timers |
| Övrigt inom demandbyggen | cirka 311,5 s | Byggmanifest med sekundupplösning |
| Backend utanför registrerade byggintervall | cirka 214 s | Rekonstruerad tidslinje |
| Kostnadsberäkningens tidsfönster | cirka 82 s | Artefakttidsstämplar |
| Piloternas tidsfönster | cirka 100 s | Artefakttidsstämplar |
| Finalister och resultat | cirka 288 s | Artefakttidsstämplar |

Inuti passagekalibreringen: learning_sumo 339,911 s; validation_sumo 539,680 s;
prepare_system 556,435 s; solve_integer_flows 234,391 s; stage_and_structure
205,391 s; retention 128,500 s; prepare_inputs 33,825 s; report_serialization
24,047 s. Resterande tid är ej namngiven overhead. SUMO-faserna innehåller även
hantering runt processerna. Lägg aldrig ihop en timer med dess barn eller
subprocessernas summerade worker-seconds med väggtid.

En läsande återspelning på `runs/demand-20260912-162423-45495c29-135a` gav:
arkivvalidering 1,24–1,30 s vid första anrop och cirka 0,35 s därefter;
tredagarssammanslagning för alla tre varianter 1,97 s; strukturrapport cirka
1,38 s. 51 640 fordon hade 491 unika rutter. Detta är ett exempelarkiv,
inte en genomsnittsmätning för alla dagar eller en uppmätt optimeringsvinst.

### Gemensamt arbetskontrakt för VARJE ändring

- Baslinje: frys berörda källfiler, Python/SUMO/biblioteksversioner, argv, indatahashar
  och vald cachekonfiguration. Git-SHA räcker inte i en smutsig arbetskopia.
- Kör originalet och spara utdata innan ändringen. Lägg diagnostik i en ny egen
  undermapp i `runs/`; återanvänd inte tidigare evidensmappar som skrivmål.
- Definiera likhet före implementation: samma sensorvärden per kvart, rutter,
  avgångstider, fordonsordning/ID, agentdata, OD/ändamål, strukturflaggor och
  rangordning. Jämför även glesa matrisers form, index, data och randvillkor.
- För befintliga publiceringsformat krävs byte-identiska rutt- och agentfiler.
  Tidsstämplar, externa timings och dokumenterade absoluta utmappar jämförs separat.
  Ignorera aldrig ett fält bara för att jämförelsen annars fallerar.
- Två lika bra lösarobjektiv med olika ruttval är en resultatändring i detta uppdrag.
- Skriv regressionstest för kontraktet. Kör relevanta befintliga tester. Kontrollera
  både giltiga och felaktiga indata: fel ska fortsatt avvisas.
- Mät före/efter utan profilerare, växelvis A/B och B/A, minst tre par initialt.
  Separera kall programstart, återanvänd process, arkivträff och kalibreringsmiss.
  Kalla inte en första läsning disk-kall utan kontroll av operativsystemets cache.
- Rapportera varje par, median, spridning, toppminne och antal nya processer.
  Är skillnaden jämförbar med variationen behövs fler mätningar; deklarera inte vinst.
- Kontrollera avbrott, felstädning och atomisk publicering för ändringar som
  skriver eller parallelliserar. Behåll originalvägen tills experimentet godkänts.
- Slutredovisning per steg: ändrade filer, testkommando/resultat, evidenssökvägar,
  likhetskontroll, tider före/efter, kvarvarande risk och nästa steg.

### Steg 0 — mät de saknade delarna och skapa återspelning

**Filer:** `build_sumo_demand.py`, `traffic_sim/demand/automatic_passage.py`,
`demand/day_library.py`, `traffic_sim/simulation/monthly_demand.py`,
`traffic_sim/simulation/cost_ordered_execution.py`, `traffic_sim/ops/runs.py`.

1. Bygg ett litet diagnostikverktyg i `tools/` som tar explicit indata och utmapp.
   Verktyget ska vägra skriva inuti indataarkivet. Inventera sparade spår först:
   komprimerade, rensade eller ofullständiga spår får inte behandlas som kompletta.
2. Återspela förberedelse/lösning/struktur från fullständiga learning-spår med
   befintlig `tools/trial_dynamic_passage.py` som utgångspunkt. Anpassa inte
   trialens algoritm till produktionsalgoritmen genom antaganden: använd samma
   produktionsfunktioner och visa att originalresultatet reproduceras först.
3. Logga `perf_counter` start/slut, phase, parent_phase, datum, variant, PID och
   input identity. Registrera setup, parsing, matrisbygge, solver, staging,
   rapportering, komprimering, kopiering, hashning, arkivvalidering och costing.
4. Separera väggtid runt SUMO-subprocessen från parsing och health-kontroll efteråt.
   Mät arbetsvågornas väggtid; summera inte samtidiga körningar till total väntetid.
5. Profilera vardag, helg/blandad pool och en svår boundary-dag; välj dem ur
   artefakterna och dokumentera varför. Mät separat ett underlag med enbart träffar.
6. Redovisa exklusiva tider och en explicit residual. Lägg timings utanför
   semantiska fingeravtryck enligt befintligt kontrakt; testa detta.

**Klart när:** alla stora pipelinefaser har mätpunkter, timers kan stämmas av
utan dubbelräkning, och återspelning reproducerar baslinjen. Historiska minuter
utan timers ska fortfarande betecknas rekonstruerade, inte nyuppmätta.
**Tester:** `tests/test_automatic_passage.py`, `tests/test_build_sumo_demand.py`,
`tests/test_demand_provenance.py`, samt diagnostikverktygets egna kontraktstester.
**Forskningsstöd:** [Python cProfile/pstats](https://docs.python.org/3/library/profile.html).
Profilering identifierar arbete men dess overhead gör den olämplig som ensam A/B-klocka.

#### Steg 0 — utfört 2026-09-12: mätverktyget finns, produktionsspåren saknas här

**Var arbetet gjordes.** Inte i `/Users/gt/Documents/gs-project` utan i en ren
molnklon av samma repo på commit `a64315f` (gren
`claude/exciting-rubin-1e6k5m`). Arbetskopian var ren; inga andra ändringar
fanns att bevara. `runs/` är gitignorerad och TOM i den här miljön, så varken
`runs/closure-search/speed-monthly-june-20260912` eller
`runs/demand-20260912-162423-45495c29-135a` gick att läsa. **Ingen
återspelning av junikörningens verkliga evidens har därför körts.** Det som
levereras är mätförmågan och dess kontrakt, inte nya produktionssiffror.

**Ändrade filer:** två nya, inga ändrade.
`tools/profile_passage_replay.py` (verktyget) och
`tests/test_profile_passage_replay.py` (41 kontraktstester). Verktyget är ett
LÖV i importgrafen: det importerar produktionen, produktionen importerar aldrig
det. Verifierat i test att det varken ingår i `demand_source_paths` (38 källor)
eller i `CATALOG_SOURCE_LABELS` (12 etiketter), så det kan inte flytta
demand- eller katalogidentitet — kravet i steg 8 punkt 3. Placeringen är inte
kosmetisk: `demand_source_paths` globbar `demand/*.py` OCH
`traffic_sim/demand/*.py`, så samma fil lagd där hade ogiltigförklarat varje
befintligt demandarkiv bara genom att existera. Diagnostik hör hemma i
`tools/`.

**Vad verktyget gör.** `--source <evidensrot>` `--out <ny mapp>`, där utmappen
vägras om den ligger i eller innehåller evidensroten; ett test jämför varje
källfils digest och storlek före/efter och kräver att de är oförändrade.
Inventeringen klassar varje krävd fil som `raw`, `compressed`,
`raw_hash_mismatch` eller `missing` och roten som `complete`, `compressed`
eller `incomplete`; en komprimerad eller ofullständig rot replayas aldrig som
komplett. Eftersom `prune_evidence` gzippar allt normalt kan `--expand-compressed`
packa upp VERIFIERADE kopior i utmappen — aldrig på plats — och rapporten
behåller då etiketten `trace_state: compressed`.

**Återspelningen använder produktionsfunktionerna**, i `_refine`:s ordning:
`trial.load_source` (som i sig kräver att varje indata-/spårhash stämmer OCH
att ruttidsprojektionen rekonstruerar de råa `entered`-cellerna — det är den
reproduktion av originalresultatet som steg 0 punkt 2 kräver INNAN någon tid
redovisas), `passage._parse_entered` per arm, `dynamic.build_passage_system`
(bas och expanderad, mätta var för sig — det är den dubblering steg 1 ska
åtgärda), `expand_departure_support`, `fit_integer_flows`,
`automatic_passage._stage_selection`, `calibrated_structure_report` för källa
och kandidat, samt `_gzip_verified` på en KOPIA i utmappen. Med `--archive` +
`--demand-spec` tidtas även `validate_demand_archive`, första anropet skilt
från de följande i samma process.

**Tidsredovisningen.** Varje fas bär `start_s`/`end_s` (`perf_counter`),
`phase`, `parent_phase`, `date`, `variant`, `pid` och `input_identity`.
Exklusiv tid är väggtid minus SEKVENTIELLA barn. En fas som markeras
`concurrent` får `exclusive_s: null` med `exclusive_basis:
concurrent_children_overlap` plus barnens summa och max var för sig — en våg
får aldrig subtraheras som om den vore seriell. Roten redovisar en explicit
residual. Kategorier som återspelningen INTE kan mäta är namngivna i
`unmeasured_categories` (SUMO-subprocessen, de sex mätvågorna, publicering/
rollback, costing/resolver, och arkivvalidering när den inte begärts) i stället
för att tyst hamna i residualen. En återspelning som FALLERAR skriver ändå sin
rapport, med `status: failed`, orsaken och de faser som hann mätas — ett
mätverktyg får inte tappa mätningen när något går sönder.

**Tester:** `python3 -m pytest -q tests/test_trial_dynamic_passage.py
tests/test_dynamic_assignment.py tests/test_automatic_passage.py
tests/test_passage_solver_checkpoint.py tests/test_build_sumo_demand.py
tests/test_day_library.py tests/test_monthly_demand.py
tests/test_independent_daily.py tests/test_monthly_search.py
tests/test_demand_provenance.py tests/test_profile_passage_replay.py` →
**427 passed, 2 failed, 1 skipped**. De två felen är miljöbundna och FANNS
FÖRE ändringen (samma två före som efter): `test_independent_daily.py` kräver
den genererade `sumo/net.net.xml`, som inte är spårad i git. Planens
`tests/test_passage_evidence_pruning.py` finns inte i repot; närmaste
befintliga täckning är `tests/test_automatic_passage.py`. `git diff --check`
rent; pylint 10.00/10 på båda nya filerna.

**Enda mätningen som faktiskt gjordes** (på testfixturen, sex fordon — INTE en
produktionssiffra och inte jämförbar med junikörningens minuter): tre
återspelningar i samma process gav `structure_source` 0,304 s första gången
mot 0,077 s median därefter, medan `structure_candidate` låg på 0,077 s hela
tiden. Det är `load_edge_geometry`-cachen som slår igenom, och det är precis
den skillnad mellan kall programstart och återanvänd process som det
gemensamma arbetskontraktet kräver att man separerar.

**Kvarvarande risk och vad som återstår av steg 0.** Ett verktyg som aldrig
körts mot riktig evidens har bara sina kontrakt som bevis. Innan steg 1 påbörjas
på riktigt behöver följande köras på maskinen som har artefakterna:
`python3 -m tools.profile_passage_replay --source <demandarkiv>/passage/q50
--out runs/<ny mapp> --expand-compressed --repeats 3 --label <vardag|blandad
pool|boundary>` för de tre deklarerade urvalen plus ett underlag med enbart
träffar, och med `--archive`/`--demand-spec` för arkivvalideringen. Först då
finns exklusiva tider för verkliga faser. Notera också att återspelningen utan
`source_reports.json` löser UTAN de bevarade PFE-gränserna; rapporten
deklarerar det som `solver_constraints: targets_and_groups_only` och sätter
`selection_reproduced` till `unavailable`/`not_comparable` i stället för att
låtsas att lösartiden är produktionens. En rot sparad med
`TRAFFIC_SIM_KEEP_PASSAGE_EVIDENCE` ger den starkare jämförelsen.

**Korrigering efter oberoende review 2026-09-12.** Den första implementationen
kunde inte göra den utlovade ekvivalenta solverreplayen. Råa `hard_bounds_pq`
är en separat inparameter till `_refine`; de finns inte i `source_reports.json`,
och normal pruning tar dessutom bort den filen. Att lösa med enbart mål och
grupper är ett annat optimeringsproblem och får inte kallas `replayed`.

Reparationen sparar därför ett hashbundet
`input/passage_replay_contract.json` i framtida passageevidens. Det innehåller
exakt behållna kvartalsgränser och producerande kodidentitet. Full replay
kräver detta kontrakt och ett validerat `result.json`, och vägrar källdrift,
arkivavvisning, ännu ej implementerad boundary/structure-repair samt varje
skillnad i selection/routes/agents. Äldre evidens kan endast köras med
`--preparation-only`; då körs ingen solver. Första upprepningen kallas inte
längre programkall eftersom modulimporterna sker innan tidtagningen.

Rätt variantrot är `runs/automatic-passage-<id>/q50` (eller `_v1`/`_v2`),
inte en undermapp i demandarkivet. En verklig komprimerad äldre q50-rot kördes
säkert i preparation-only-läge: 19 558 fordon, 136 780 expanderade kolumner,
894 019 sparse nonzeros och 7,798 s total replaytid. `load_source` tog 3,126 s,
departure expansion 2,140 s, basbygget 0,689 s, expanderat systembygge 1,510 s
och verifierad uppackning 0,324 s. Det är en diagnostisk observation, inte ett
A/B-resultat. 67 fokuserade profiler/automatic-passage-tester passerar.

**Nästa steg:** bygg exakt en ny dag med normala produktionsgrindar och bevarad
evidens. Kör sedan verktyget mot dess
`runs/automatic-passage-<id>/q50` och kräv `status: replayed` plus identiska
selection/routes/agents innan steg 1 ändrar produktionskoden. Ingen hel
datumuppvärmning behövs för denna mätning.

**Körkorrigering 2026-09-13.** Försöket i en ren molnklon använde builderns
standardväg och startade därför `assignment_priors.py` och
`build_candidates.py`. Det var fel experiment: den vägen behöver den lokala
POI-cachen eller Overpass, och mäter ny kandidatgenerering i stället för den
redan kvalificerade katalog som hastighetsprovet ska hålla fast. Kör provet på
maskinen med katalogartefakterna och ange explicit `--candidate-source catalog`.
Den deklarerade endagsprovdagen är 2027-06-25 med `--source forecast`, inte
builderns historiska standarddatum.

Arkivvalidering är en separat mätning. Ett vanligt q50-bygge har en variant,
medan closure-arkivets validator kräver q10/q50/q90. Verktygets
`--archive-only` mäter därför ett befintligt giltigt trevariantsarkiv utan att
koppla det till q50-replayen eller starta ett nytt trevariantsbygge.

### Steg 1 — återanvänd verifierat passagesystem

**Filer:** `tools/trial_dynamic_passage.py:load_source`,
`traffic_sim/demand/automatic_passage.py:_refine`,
`traffic_sim/experimental/dynamic_assignment.py:build_passage_system` och
`expand_departure_support`.

1. Mät de tre förekomsterna separat: load_source bygger ett system, _refine
   bygger grundsystemet igen, expand_departure_support bygger ett dummy-system
   för validering. Den expanderade kandidatmatrisen är däremot ett annat system.
2. Extrahera den gemensamma fullständiga indatavalideringen. Den publika vägen
   ska fortfarande kontrollera identiteter, scenarioomfång, monotona ändliga tider,
   kapacitet, sensorordning och kvartgränser.
3. Inför en intern representation som bär verifierade options, groups, metadata
   och grundsystemet. Behåll `load_source`-tuple-API för befintliga anropare,
   exempelvis med en separat intern loader. Dela inte skrivbara arrayer okontrollerat.
4. Låt _refine använda exakt det system som verifierats. Beräkna expanderat
   system när supporten ändras. Ta inte bort råspårens hash- eller entered-kontroll.
5. Testa upprepade sensorpassager, startedge, tomma data, olika scenarioordning,
   negativa/NaN tider och passager utanför horisonten. Jämför grund- och randmatriser.

**Tester:** `tests/test_trial_dynamic_passage.py`, `tests/test_dynamic_assignment.py`,
`tests/test_automatic_passage.py`, `tests/test_passage_solver_checkpoint.py`.
**Vinstområde:** del av prepare_systems 556 s; hela posten kan inte elimineras.
**Stöd:** konkret dubbelarbete i repot. [SciPy sparse](https://docs.scipy.org/doc/scipy/reference/sparse.html)
stöder formatvalet; CSR och delade observationer används REDAN och är inte nya förslag.

#### H1 + steg 1 — utfört 2026-09-13 (molnklon; ingen produktionsvinst mätt här)

**H1, repetitionsmätningen.** Varje repeat fick tidigare sin egen
`out/repeat-N/solver-cache`, så alla tre solve-körningarna var kalla medan
rapporten kallade repeat 2–3 återanvänd process. Produktionen hade cacheträff,
så prioriteringen pekade fel. `profile()` skapar nu EN tom cache i profilens
egen utmapp, delad mellan repeats, och vägrar en cache som ligger i eller runt
källevidensen. Efter varje solve läses solverns eget `solver/state.json`:
`solver_cache_hit` och request key rapporteras per repeat, och en saknad,
oläsbar eller icke-boolesk status är en refusal, inte ett tyst antagande.
Summeringen bär `solver_cache_basis:
empty_output_local_cache_then_shared_across_repeats`, och `process_state`
beskriver process- och solvercache var för sig. En ensam `replay()` behåller
sin privata cache under sin egen utmapp. Regressionstestet kräver
`[false, true, true]` och samma request key i alla tre.

**Steg 1, återanvänt grundsystem.** Tre systembyggen mättes lokalt; två av dem
var dubbelarbete. `trial.load_verified_source` är den interna loadern och
returnerar en frusen `VerifiedSource` med options, groups, metadata och det
grundsystem vars projektion rekonstruerade de råa `entered`-cellerna. Publika
`load_source` behåller sitt tuple-kontrakt och ger nya, redigerbara behållare.
`_refine` använder exakt det verifierade systemet och avvisar om dess
sensoruppsättning eller kvartantal inte matchar de kalibrerade målen.
`expand_departure_support_verified(system, …)` hoppar över dummy-systemet som
bara upprepade optionsvalideringen — systemet ÄR beviset, eftersom en
`PassageSystem` inte kan existera utan att dess options passerat, och det
undviker en publik `skip_validation` som kan användas fel. Publika
`expand_departure_support` validerar fortfarande godtyckliga anropare, och
expansionens egna regler (enhetskapacitet, deklarerad horisont, reserverade
scenarionamn) gäller i båda vägarna. Den expanderade kandidatmatrisen byggs
fortfarande.

**Mätt här, på testfixturen och inte i produktion:** produktionsvägen gick från
fyra till två systembyggen — `[1 option, 4 kvart]`, `[1, 4]`, `[1, 1]`, `[6, 4]`
blev `[1, 4]`, `[6, 4]`. De två som försvann är `_refine`:s omkonstruktion och
dummy-valideringen; det expanderade systemet är kvar. En A/B på samma fixtur
före och efter gav byteidentiska `selection_sha256`, `routes_sha256` och
`agents_sha256`. Det är en strukturell räkning på en enfordonsfixtur, inte en
tidsvinst: molncontainern har varken katalogartefakter eller POI-cache, så
ingen produktionsdag kunde byggas här. Den lokala A/B/B/A på det frysta
underlaget är det som avgör.

### Steg 2 — beräkna fasta ruttegenskaper per unik rutt

**Filer:** `demand/structure.py:calibrated_structure_report`,
`_route_structure_metrics`, `purpose_lengths_km`, `purpose_length_bins`,
`route_od_distance_km`; anrop i `automatic_passage.py`.

1. Profilera antalet unika edge-tupler och endpoint-par per faktisk dag/variant.
   Använd inte automatiskt tredagarsexemplets kvot för passagekalibreringen.
2. Skapa en lokal route-facts-tabell: full edge-tupel -> endpoint-avstånd,
   antal sensorpassager, sista sensorposition och onward-sträcka. Nyckeln måste
   också bindas till den geometri/sensoruppsättning som använts.
3. Behåll nuvarande avståndsformel och summeringsordning. Räkna ruttfakta en gång
   och använd dem för varje fordon. Behåll samtliga observationer för medianer
   och andelar; deduplicera aldrig bort fordon ur statistiken.
4. Dela parserresultat mellan rapportens hjälpfunktioner. Stöd både inline-rutter
   och namngivna rutter. Saknad/ogiltig rutt ska fortfarande ge samma avvisning.
5. Poolfakta kan återanvändas inom en innehållsbunden körning. Kandidatens
   kvartantal, ändamålsfördelning och short-trip-audit beräknas på nytt efter tidsflytt.
6. Testa särskilt avstånd på trösklarna, loopar med återbesök vid en sensor,
   identiska rutter med olika ändamål/avgångar och geometri som ändras på disk.

**Klart när:** hela rapportobjektet och flaggorna är identiska på referensfallen,
och uppmätt total rapport-/passagetid minskar. Testa cacheinvalidiering.
**Tester:** lägg beteendetester i relevant befintlig strukturtäckning, lokalisera
den med `rg -n 'calibrated_structure_report|under_1km' tests`; kör även
`tests/test_automatic_passage.py` och `tests/test_build_sumo_demand.py`.
**Stöd:** uppmätta 229 444 avståndsanrop i exempelrapporten, 51 640 fordon/491 rutter.
[ElementTree](https://docs.python.org/3/library/xml.etree.elementtree.html) dokumenterar
parseralternativen; streaming ger inte automatiskt mindre CPU-tid.

#### Steg 2 — mätinstrumentering klar 2026-09-13, ingen cache byggd

Endast punkt 1 i steg 2 är gjord: att mäta hur mycket upprepad geometri som
faktiskt finns och vad rutt- och avståndsfunktionerna kostar. Ingen
route-facts-tabell finns; `route_facts_cache: not_implemented` står i varje
rapport.

`route_shape_inventory()` läser en konkret ruttfil med produktionens egen
`read_route_vehicles` och rapporterar fordon, unika edge-tupler, unika
endpoint-par och kvoten fordon per unik rutt — för både källrutten och den
stagade kandidaten, i en egen fas så att parsningen inte hamnar i
strukturfasens tid.

`measure_structure_calls()` räknar anrop och EXKLUSIV tid för
`_route_structure_metrics`, `purpose_lengths_km`, `purpose_length_bins`,
`route_od_distance_km`, `gravity_distance_km` och `load_edge_geometry`.
Exklusiv tid är egen tid: förfluten tid minus instrumenterade barns tid, så
`_route_structure_metrics` och dess avståndsanrop aldrig dubbelräknas.
Funktionerna byts tillbaka i ett `finally`, även när rapporten kastar, och ett
test kräver att den instrumenterade rapporten är identisk med den
oinstrumenterade. Mätdata ligger bara i profilerns diagnostiska rapport, som
ingen pipeline läser och som inte ingår i någon semantisk fingeravtryck.

**Ingen produktionssiffra finns här.** Molncontainern har varken
katalogartefakter eller POI-cache, så ingen dag kunde byggas. På
enfordonsfixturen stänger redovisningen: summan av exklusiva tider för
källrapporten var 0,3016 s mot fasens väggtid 0,3017 s, och `load_edge_geometry`
syns som 0,153 s första gången mot 0,000014 s när cachen är varm. Det är
instrumenteringens egen verifiering, inte ett mått på produktionen.

Exakt lokalt mätkommando, på maskinen med artefakterna och mot en q50-rot som
redan har `passage_replay_contract.json`:

```sh
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/gs-mpl \
python3 -m tools.profile_passage_replay \
  --source runs/automatic-passage-<id>/q50 \
  --out runs/profile-structure-<stamp> \
  --pool sumo/candidates.rou.xml \
  --label forecast-weekend-full-day \
  --repeats 3
```

Läs `structure_measurement` i `runs/profile-structure-<stamp>/repeat-1/replay_report.json`:
`source_route.vehicles` mot `source_route.unique_edge_tuples` säger hur mycket
upprepning som finns, och `structure_source_calls` säger vad den kostar per
funktion. Först med de siffrorna är det avgjort om en route-facts-tabell är
värd att bygga, och för vilka funktioner.

#### Steg 2 — experimentet byggt 2026-09-13, inte aktiverat som standard

Den lokala mätningen på 2027-06-25 (9 675 fordon, 296 unika edge-tupler, 296
unika endpoint-par, 32,686 fordon per unik rutt, 108 094 `gravity_distance_km`-
anrop, observerad omfattning 0,691741 s för de två strukturfaserna) motiverar
ett avgränsat experiment. Den motiverar inget globalt eller månatligt
prestandaanspråk, och 0,691741 s är hela den observerade övre gränsen för just
det fasparet.

`demand/structure.py` har nu en `StructureContext`: en operationsbunden,
icke-global memo som binds till geometrins INNEHÅLL (SHA-256) och den sorterade
mätsensoridentiteten, aldrig till mtime eller storlek. Per unik full edge-tupel
beräknas en gång: endpoint-avstånd med samma formel och flyttalsordning,
destinationskant och dess near-sensor-utfall, antal sensorpassager inklusive
återbesök, sista sensorposition och onward-sträcka. Varje FORDON behåller sin
egen observation i dokumentordning, så antal, medianer, andelar och per-kvart-
fält är oförändrade — det är BERÄKNINGAR som dedupliceras, aldrig fordon.
Endpoint-avstånd delas med agent-/ändamålssidecars utan att deras iterations-
eller reduktionsordning ändras. En delad parser löser både inline-rutter och
namngivna `<route id=...>`; oupplösliga och tomma rutter avvisas som förut.
Poolens fakta beräknas en gång per `_refine`-operation och varje anropare får
en djup kopia, så två rapporter delar ingen muterbar struktur. Kandidatens
kvart- och ändamålsaggregat räknas alltid om efter tidsflytt.

Publika `calibrated_structure_report(route_path, pool_path)` är oförändrad; en
kontext skickas bara via det privata `_context`-nyckelordet från kontrollerade
produktionsanropare, och `structure_context()` är den interna fabriken.

**Störst repetition låg inte där planen antog.** Den dominerande dubbleringen
var inte per fordon utan per RAPPORT: `destination_sensor_proximity` beräknade
`baseline_pct_within` över HELA nätet varje gång en rapport togs fram — ett
avståndsanrop per kant i nätet, per rapport. På enfordonsfixturen mot den
riktiga 7 125-kantsgeometrin föll `gravity_distance_km` från 21 441 till 14 294
anrop, en minskning på 33,3%, med byteidentiska selection-, route- och
agenthashar. Där kan per-rutt-dedupliceringen inte hjälpa alls (ett fordon, en
rutt), så hela minskningen kommer från baslinjen. Med 296 unika rutter bakom
9 675 fordon tillkommer per-rutt-effekten ovanpå den.

**Ingen tidsvinst hävdas.** Anropsräkning är inte väggtid, och fixturen är inte
produktionen. Kör den motviktade A/B:n lokalt innan detta blir standard:

```sh
PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/gs-mpl \
python3 -m tools.profile_passage_replay \
  --source runs/automatic-passage-<id>/q50 \
  --out runs/profile-structure-<arm>-<stamp> \
  --pool sumo/candidates.rou.xml \
  --label forecast-weekend-full-day \
  --repeats 3
```

Kör det på `d10361b` (arm A) och på den här commiten (arm B), i ordningen
A/B/B/A i samma miljö. Godkänn endast om `selection_reproduced.state` är
`identical` i alla körningar, `selection_sha256`/`routes_sha256`/
`agents_sha256` är oförändrade mot steg 1:s accepterade utfall, och
`structure_measurement.input_identity` är samma i båda armarna. Jämför sedan
`structure_source_calls` och `structure_candidate_calls` samt fasernas
väggtider.

OBS för steg 8: `demand/structure.py` ingår i `demand_source_paths`, så den här
ändringen ger demandarkiven en ny källidentitet. Den ingår INTE i
`CATALOG_SOURCE_LABELS`, så den adopterade ruttkatalogen påverkas inte.

**Granskningsfixar 2026-09-13, före godkännande.** Fem korrigeringar:
testfilen använder postponed annotations så den går på Python 3.9;
`StructureContext` läser geometrin EN gång och härleder både SHA-256 och
arrayerna ur samma bytes, så en omskrivning med oförändrad storlek och mtime —
precis det en stat-nyckel missar — invalidiserar kontexten; poolrapportens
cache nycklas på INNEHÅLLET i ruttfilen, dess ändamålssidecar och
generationsmålet, inte på sökvägen, så samma sökväg med nya bytes aldrig ger en
gammal rapport; `demand/structure.py` ingår nu i
`automatic_passage.replay_source_sha256()`, eftersom en ändrad strukturrapport
ändrar vad en sparad replay betyder; och profilern skapar EN kontext per replay
som delas av source- och candidate-rapporten, med
`route_facts_cache: operation_scoped_content_bound` och kontextens
innehållsidentitet i rapporten. Verifierat att digests och anropsantal är
oförändrade mot `b375c3c` (14 294 anrop, samma selection/routes/agents).

**Accepterat lokalt A/B (Codex, motviktad A/B/B/A på samma sparade q50-evidens):**
varm replaymedian 6,6376895 → 5,956609 s (10,26%), varm strukturtid 1,3972755 →
0,749738 s (46,34%), kall replaymedian 9,943896 → 9,233191 s (7,15%), alla 11
solver-request-arrayer exakt lika och identiska selection-/route-/agenthashar.
Med granskningsfixarna och korrekt delad kontext: varm replaymedian 5,8138625 s
och varm strukturtid 0,651292 s. Steg 2 är därmed godkänt. Detta är diagnostisk
replay, inte releasebevis och inte ett fullständigt produktionsdagsbygge.

### Steg 3 — lösar- och supportkostnad, endast efter mätning

**Filer:** `dynamic_assignment.py:fit_integer_flows`,
`departure_bound_constraints`, `departure_group_bound_constraints`.

1. Dela den befintliga solve_integer_flows-timern i supportmatris, constraintbygge,
   lösaranrop, checkpoint/cache och efterverifiering.
2. Identifiera om samma constraintmatris byggs flera gånger för identisk support.
   Återanvänd endast när options, ordning, bounds och grupper matchar exakt.
3. Behåll nuvarande CSR-representation, scenario-differenser och binära
   objektivförenklingar; dessa är redan införda. Börja inte om med samma optimeringar.
4. Ändra inte kolumnordning, reduktionsordning eller solverinställningar för en
   snabbare lösning utan att kontrollera faktisk vald lösning, inte bara objektivet.

**Tester:** dynamic_assignment, passage_solver_checkpoint och automatic_passage.
**Vinsttak:** hela den nuvarande posten är 234 s för månaden. Det är inte rimligt
att prioritera en riskfylld solvermigration som lösning på en timmes körtid.

#### Steg 3 — mätinstrumentering klar 2026-09-13, ingen optimering föreslagen

Endast mätning. Lösarmatematik, solverinställningar, kolumnordning,
cacheidentitetens KONSTRUKTION och checkpointformatet är orörda.

`dynamic_assignment` har nu en privat, diagnostisk hook: `SOLVER_PHASE_NAMES`,
`_SOLVER_PHASE_OBSERVER` (None i produktion) och
`_observe_solver_phases(observer)` som installerar och ALLTID avinstallerar i
ett `finally`. Nio regioner mäts med exklusiv tid — indatavalidering och
conservation/hard/rhs, scenario-differensmatrisen,
`departure_bound_constraints`, `departure_group_bound_constraints`,
sammanslagning till constraints/bounds, kolumnekvivalens och
representantreduktion, checkpointserialisering/request key/requestskrivning/
cache lookup/cacheskrivning, `scipy.optimize.milp` när den körs, samt
återexpansion och full efterverifiering. Exklusiv tid är egen tid, så en
nästlad fas aldrig dubbelräknas. Profilern aktiverar hooken privat runt
`fit_integer_flows` och rapporterar `solver_measurement` med alla faser, även
de som inte kördes: **vid cacheträff visar rapporten `milp_solve` med 0 anrop
och `milp_executed: false`.**

**Verifierat att mätningen inte rör resultatet.** Mot `b270087` på fixturen är
`request.npz` byte-identisk, selection/routes/agents oförändrade, och
`state.json` har exakt samma nycklar. Med och utan observer i samma process är
request key identisk.

**En konsekvens som måste vara känd före nästa A/B.** `solve_checkpointed`
hashar avsiktligt bytes ur `passage_solver.py` OCH
`experimental/dynamic_assignment.py` in i cachenyckeln. Instrumenteringen
redigerar båda, så nyckeln FLYTTAR — mätt `f85eec4175f4` → `709c822ed204` —
medan modell-delen av nyckeln är oförändrad (`2d6c534b167f` i båda). Det är
kontraktet som fungerar, inte en defekt: varje redigering av de filerna, även
en kommentar, gör samma sak. Praktiskt betyder det att **alla befintliga
passage-solver-cacheposter är ogiltiga från den här commiten**, så arm B i
nästa A/B startar med kall solvercache. Jämför inte varm arm A mot kall arm B.

**Ingen steg 3-optimering föreslogs i det här skedet.** Fixturens faser är
mikrosekunder och säger ingenting om vilken fas som dominerar i produktion.
Mätningen måste köras på den sparade q50-evidensen och
`solver_measurement.phases` läsas; först då finns underlag för att välja
åtgärd.

#### Steg 3 utfall — mätt och avgjort 2026-09-13

**Reviewfixarna först (`7e1a976`).** `_SOLVER_PHASE_OBSERVER` är en
`contextvars.ContextVar` som sätts och återställs via token i `finally`, så två
samtidiga profiler varken blandar observerare eller lämnar någon installerad.
Det tillagda testet interfolierar två observerare med events och faller på den
gamla modulglobalen med `KeyError: 'milp_solve'` — fasen hamnade i fel
insamlare. `passage_solver.py` ingår nu i `replay_source_sha256()`, eftersom
replaykontraktet måste bindas till filen som styr solvercache,
feasibility-kontroll och själva solveranropet.

**Mätningen pekade ut avgångsgränserna.** På den sparade q50-evidensen för
2027-06-25 (9 675 fordon, 296 unika kantsekvenser) går MILP:en redan förbi vid
cacheträff; det som återstår av varm `fit_integer_flows` är matrisbygge.
`departure_bound_constraints` var 53,31 % av varm fit.

**Åtgärden.** En anropslokal memo i `departure_bound_constraints`, nycklad på
VÄRDET av `(avgångskvart, option.edges)` och returnerande exakt tuple av
bound-rader. Ingen global, ingen sökväg, inget överlever anropet. Ordningen är
säker därför att `coo_matrix.tocsr()` kanoniserar — dubbletter summeras,
index sorteras — verifierat direkt på scipy 1.13.1. `option.edges` är tuple i
hela produktionen: `departure_reconciliation` parsar med `tuple(...)` och
`_shifted_alternatives` bär samma objekt genom `replace()`, vilket också är
varför memon träffar så ofta.

**Resultat, A/B/B/A med tre repeats per profil och egen tom solvercache per
profil** (aldrig delad mellan revisioner — nyckeln hashar källan med flit):

| Mått, varm | Baslinje | Kandidat | Förbättring |
|---|---|---|---|
| `departure_bound_constraints` median | 0,400201 s | 0,192282 s | **51,95 %** |
| `fit_integer_flows` median | 0,762698 s | 0,575360 s | 24,56 % |

Sämsta kandidatmätningen ligger 51,61 % under bästa baslinjemätningen, så
fördelningarna överlappar inte. Kall `fit_integer_flows` är praktiskt oförändrad; kandidatmedianen är 0,59 %
snabbare eftersom den domineras av ~3 s MILP.

**Exakthet.** Alla 11 solver-request-arrayer (`c`, `data`, `indices`,
`indptr`, `integrality`, `lower`, `options`, `row_lower`, `row_upper`,
`shape`, `upper`) är identiska i varje par av baslinje- och kandidatrepeat,
`request.npz` är byte-identisk i alla fyra armar, publicerade
routes/agents/selection är byte-identiska och `selection_reproduced.state` är
`identical` i samtliga 12 replays. Enhetsexaktheten är pinnad mot en fryst
ordagrann kopia av implementationen före ändringen. Evidens:
`validation/passage_step3_departure_bounds_experiment_20260913.json`.

**Vad som medvetet INTE gjordes.** `column_equivalence_reduction` är nu den
största varma fasen (median ~0,266 s mot avgångsgränsernas ~0,192 s) och är
orörd: ingenting är mätt om huruvida dess arbete är återanvändbart. Ingen
SUMO-körning, månadssökning, kataloggenerering eller uppvärmning. Solver,
MILP-inställningar och den persistenta resultatcachen är oförändrade.

**Driftanmärkning.** `tools/profile_passage_replay.py` måste köras med
`PYTHONPATH=.`; som skript är `sys.path[0]` katalogen `tools/`, och importen av
`demand` misslyckas annars. Verktyget ligger utanför
`replay_source_sha256()`, så en framtida fix där invaliderar ingen evidens.

### Steg 4 — bevisfiler, parsing och serialization

**Filer:** `automatic_passage.py:_gzip_verified` och retention-anroparen,
`tools/trial_dynamic_passage.py:load_source/materialize_selection`,
`traffic_sim/ops/runs.py`, `build_sumo_demand.py:_tracked_main`.

1. Profilera bytes och tid för kopiering, komprimering, dekomprimerad verifiering,
   hashning, XML och JSON var för sig. Gzip nivå 3, fast mtime och parallell
   retention finns redan: föreslå dem inte som nya förändringar.
2. Testa i första hand färre pass över samma oföränderliga bytes. Samordna
   kopiering och hashning endast om publicerat mål fortfarande verifieras enligt
   arkivkontraktet. Ta inte bort kontrollen av det faktiskt skrivna resultatet.
3. Undvik hårdlänkar till muterbara livefiler. Ändrad komprimeringsnivå kräver
   tydligt hanterade komprimerade/okomprimerade digest-kontrakt.
4. Behåll atomisk tempfil/publicering, avbrottsstädning, manifest och råevidens.
   Skjut inte integritetsarbete till efter att jobbet redan rapporterat succeeded.

**Tester:** `tests/test_passage_evidence_pruning.py`, provenance och relevanta
run-registry-tester. Testa avbruten komprimering, korrupt mål och saknad evidens.
**Vinstområde:** retention 128,5 s; övrig parsing ingår i andra poster och får
inte dubbelräknas. Kompakt atomisk demand_meta-skrivning finns redan.

#### Steg 4 utfall — replay-baslinje mätt, retention-baslinje återstår, 2026-09-13

**Ingen optimering är implementerad.** Detta är enbart mätning, enligt planens
egen ordning.

**Instrumenteringen.** `traffic_sim/ops/io_phases.py` är ny och ligger utanför
både `demand_source_paths` och `replay_source_sha256`: en diagnostik som
adderas till någon av de inventarierna skulle ändra vad varje lagrat arkiv och
varje sparad replay påstår om koden som skapade dem. Den är inert tills ett
verktyg installerar en `PhaseCollector`, installationen är kontextlokal och
återställs alltid i `finally`, och två samtidiga profiler kan inte mötas.
Två redovisningsregler har egen maskinell hantering: en sekventiell förälder
redovisar väggtid MINUS sina direkta barn, och en förälder vars barn kördes
SAMTIDIGT redovisar `exclusive_s: null` med barnens summa och max var för sig.
Retention komprimerar upp till tre filer i en trådpool; att addera dem som om
de kört sekventiellt skulle uppfinna väggtid som aldrig förflutit. Trådarna
startar med tomma kontextvariabler, så arbetaren lindas explicit.

**Ström-uppdelningen.** `shutil.copyfileobj` läser, komprimerar och skriver i
ett svep. De tre kostnaderna är inte separerbara genom att linda ANROPET, men
väl vid strömmen: varje `read` och varje `write` tidtas, och komprimeringen är
vad som återstår av den omslutande fasen. Inga bytes ändras.

**Mätt på sparad q50-evidens för 2027-06-25** (9 675 fordon, 67 463 kolumner,
3 repeats i samma process, kall + två varma). Rangordning på de varma
repeats:

| Fas | Varm median | Andel | Bytes |
|---|---|---|---|
| `source_trace_xml` | 0,9146 s | **47,02 %** | 40,8 MB lästa |
| `stage_reformat_xml` | 0,1826 s | 9,39 % | 9,5 MB in, 9,5 MB ut |
| `selection_transform` | 0,1648 s | 8,47 % | — |
| `stage_validate` | 0,1542 s | 7,93 % | — |
| `selection_write` | 0,1349 s | 6,94 % | 14,9 MB skrivna |
| `source_routes_xml` | 0,1264 s | 6,50 % | 9,5 MB lästa |
| `gzip_compress` | 0,0620 s | 3,19 % | — |

Alla gzip-faser tillsammans är under 5 %. **Komprimering är inte kostnaden i
den här formen** — läsning och parsning av sparade vehroute-spår är det.

**Vad som INTE mättes, uttryckligen.** En envariantsreplay anropar
`_gzip_verified` direkt på en kopierad fil och kör aldrig `prune_evidence`, så
filinventeringen, den tredelade trådpoolen och städpasset är TESTADE men inte
uppmätta här. Detsamma gäller `traffic_sim/ops/runs.py` och de instrumenterade
ställena kring `build_sumo_demand.py::_tracked_main`: de kräver ett riktigt
demand-bygge, vilket ligger utanför detta steg. Planens `retention 128,5 s`
avser ett fullt bygge, inte den här replayen; siffrorna ovan motsäger den inte.

**Reviewkorrigering.** Den första rapportören lade både den samtidiga
förälderns kritiska tid och barnens summerade trådtid i den globala rankingen.
Tre parallella 50 ms-barn kunde därför redovisas som cirka 220 ms för en region
vars uppmätta väggtid var cirka 55 ms. Barnen ska finnas kvar som diagnostisk
detalj, men bara den samtidiga regionens direkt uppmätta väggtid får bidra till
rankingen. q50-replayen ovan anropade inte `prune_evidence`, så dess befintliga
fasvärden påverkas inte av felet. Instrumenteringen måste korrigeras och den
fulla trevariants-retentionen mätas på en kopia innan steg 4 kan stängas.

En innehållsnycklad cache för `source_trace_xml` är inte ännu en godkänd
produktionsoptimering. Varje av de tre spårfilerna i den mätta varianten hade en
egen digest och lästes en gång i ett normalt `_refine`-anrop. Profilens varma
repeats läser däremot samma evidens igen. En cachevinst där riskerar därför att
mäta profilverktygets upprepning i stället för dagsbyggets kritiska väg.

`build_sumo_demand.py::_tracked_main` instrumenterades inte i `90c0675` trots
att den ingår i steg 4:s fil- och mätlista. `traffic_sim/ops/runs.py` mäter
kopiering och målhashning när en observerare finns, men det finns ännu ingen
fas för bygganropet, produktarkiveringen, metadata-parsningen,
valideringsrapporten eller slutpubliceringen runt `_tracked_main`. Det ska
läggas till och testas, men produktionstid får inte hävdas utan en avgränsad
riktig byggmätning.

**Driftfix.** `tools/profile_passage_replay.py` startar nu från repo-roten utan
`PYTHONPATH=.`; ett subprocess-test kör `--help` med `PYTHONPATH` borttaget.

**Evidens:** `validation/passage_step4_io_measurement_20260913.json`,
`release_evidence: false`. Källkopian skiljer sig från originalet i exakt två
filer, båda från ombindningen av replaykontraktet, och alla tre replays
reproducerar frysta selection/routes/agents-hashar.

#### Steg 4 reparation och full retention — 2026-09-13

**Två fel i den föregående rapporten, båda mätta.** Concurrent-rankingen
räknade en samtidig region två gånger: regionen bidrog med sitt långsammaste
barn OCH varje ättling bidrog med sin egen exklusiva tid till samma total. Mätt
direkt: tre 50 ms-barn under en region med 0,0553 s verklig väggtid gav
0,2165 s rankad tid, 3,91 gånger det som förflöt. Faser bär nu
`wall_contribution_s`: sekventiella faser utanför en samtidig region bidrar med
sin exklusiva tid, en samtidig gräns med sin uppmätta inclusive, och allt under
gränsen med noll — men behåller full diagnostik. Och `_tracked_main` beskrevs
som instrumenterad utan att vara det; `90c0675` rör inte filen. Den är nu
instrumenterad på riktigt (`6d76ffe`), men fortfarande OMÄTT: den kräver ett
riktigt demand-bygge.

**Full trevariants-retention är nu mätt** (`--retention-root`, egen kopia per
repeat, aldrig hårdlänkad, originalet orört). Roten
`runs/automatic-passage-97f1ab116a8e48d8a05621247431715a`: 156 filer,
1 075,1 MB, varav 118 råa XML på 877,5 MB.

| Repeat | Kopiering | Retention root wall | Residual |
|---|---|---|---|
| 1 | 0,677 s | **5,137 s** | 0,000 s |
| 2 | 0,446 s | 5,811 s | 0,000 s |
| 3 | 0,465 s | 5,709 s | 0,000 s |

118 råa XML → 0, 114 `.gz` skrivna, kvarvarande trädkvot 0,1923 och
gzip-payloadkvot 0,1737, 114 verifierade
mot ORIGINALET via dekomprimerad digest, noll avvikelser. Bytes: 805,3 MB
lästa, 139,9 MB skrivna, 805,3 MB hashade, 805,3 MB verifierade.
Originalrotens alla paths, storlekar och SHA-256 var identiska efter varje
repeat.

Rankingen för repeat 1: `retention_compress` 99,81 %
(`concurrent_region_wall`), `retention_cleanup` 0,12 %,
`retention_inventory` 0,06 %. Inuti regionen: 15,197 s trådtid i
5,127 s väggtid — **2,96× samtidighetsfaktor på tre arbetare**, med
`gzip_compress` 10,717 s, `gzip_target_verify` 2,991 s och
`gzip_source_hash` 0,817 s av trådtiden.

**Planens 128,5 s för retention reproduceras inte.** En färdig trevariantsrot
retentioneras på 5,14 s i repeat 1 och 5,76 s median i repeat 2–3. Vad den siffran än
aggregerar är det inte detta. Retention är alltså INTE den stora posten, och
parsingförslaget skjuts därmed upp enligt planens egen regel.

#### Steg 4 KLART — worker-experimentet, 2026-09-13

Retentionens komprimeringsregion var 99,74 % av retention och kördes med högst
tre arbetare trots tio logiska kärnor. Taket är nu en uttrycklig konstant,
`RETENTION_MAX_WORKERS`, med en privat policyfunktion som rapporterar både
BEGÄRT och FAKTISKT antal — färre filer eller färre kärnor sänker tyst det
andra. Inget miljövariabelkontrakt tillkom.

A/B/B/A på den verkliga roten (156 filer, 1 075 071 455 byte, 118 råa XML,
877 499 944 råa XML-byte, alla fyra verifierade före mätning), tre repeats per
arm, egen process och egen outputrot per arm, egen vanlig kopia per repeat:

| | Baslinje (3) | Kandidat (6) |
|---|---|---|
| retention root wall, median | 5,4473 s | **3,9178 s** |
| spridning | 5,2144–5,6531 s | 3,6136–4,0173 s |
| child thread sum, median | ~15,8 s | ~21,8 s |
| peak RSS | 100–116 MB | 126–129 MB |

**28,08 % snabbare**, och sämsta kandidatmätningen (4,0173 s) ligger under
bästa baslinjemätningen (5,2144 s) — fördelningarna överlappar inte.

Exakthet, alla tolv repeats: originalrotens paths, storlekar och SHA-256
oförändrade; alla 114 gzipfiler dekomprimerar till originalets digest, noll
avvikelser; hela det kvarvarande filträdet (137 filer) byte-identiskt mellan
tre och sex arbetare; identiska counts, `retained_tree_ratio` 0,192312 och
`gzip_payload_ratio` 0,173675; identiska kontraktsutfall; rankad väggtid
översteg aldrig root wall (max differens 0,000000 s).

**Vinsten är ca 1,53 s per färdig trevariantsrot.** Trådtiden STEG från ~15,8
till ~21,8 s — den är diagnostik och får inte multipliceras till en
månadssiffra. Åtta eller fler arbetare är inte mätta. Gzipnivå, mtime,
verifiering, tempfil/publicering och städsemantik är orörda.

**Steg 4 är därmed stängt.** `_tracked_main`-instrumenteringen drogs senare
tillbaka med avsikt i `1309cf6`: `build_sumo_demand.py` är katalogbundet byte
för byte, och mätningen hade ingen profileringscollector som kunde motivera
identitetsdriften. Den mätgruppen är pensionerad, inte levererad eller uppskjuten.
Processcachen för `source_trace_xml` implementeras inte.

Eftergranskning av profileraren hittade en isoleringslucka: dess experimentella
worker-tak ändrade tillfälligt den processglobala produktionskonstanten. Det
påverkade inte A/B-resultatet eftersom varje arm kördes i en separat process,
men två samtidiga profiler i samma process kunde påverka varandra. Taket skickas
nu som en privat, anropslokal parameter genom profileraren till
`prune_evidence`; produktionsanrop använder fortfarande exakt
`RETENTION_MAX_WORKERS`. Ett samtidighetstest låser att 3- och 6-workerprofiler
ser sina egna tak medan produktionspolicyn förblir oförändrad, även vid fel.
Steg 4:s verdict och tidsvärden är oförändrade. Eftersom
`automatic_passage.py` ingår i `demand_source_paths` flyttar reparationen
arkivens källidentitet igen; ingen mellanliggande re-warm ska göras enbart för
denna fix. Bygg biblioteket först när den avsedda kodserien är frusen.



### Steg 5 — underlag, arkiv och kostnadsberäkning

**Filer:** `monthly_demand.py:find_demand_archives/validate_demand_archive/prepare`,
`demand/day_library.py:assemble_window`, `cost_ordered_execution.py`,
`traffic_sim/simulation/deterministic_disruption.py` och `monthly_sumo.py`.

1. Mät faktiska antal läsningar och valideringar per arkiv under resolver-setup.
   Specbaserad arkivindexering finns redan. Cost-ledger-cachen finns redan.
2. För samma verifierade snapshot, skicka vidare en intern descriptor och dess
   parsade metadata. Inför inte en global `path -> valid`-cache. Inträde från
   disk kräver fortsatt full kontroll och senare filändringar ska upptäckas.
3. Välj en explicit snapshot-strategi: en ägd isolerad kopia som konsumeras under
   kontrollerat livscykelansvar, eller fortsatt kontroll vid läsgränser. Enbart
   mtime/size räcker inte mot innehållsändringar med bevarad stat-information.
4. Mät kostnad för route parsing och ruttresolver per unik rutt i costing. Återanvänd
   resolverresultat bara med hela routingskonfigurationen/closureidentiteten som
   nyckel och bevara scorer/writer-överensstämmelsen.
5. Optimera assemble_window endast om mätningen motiverar det. Behåll datumordning,
   tidsförskjutning, ID-sekvens, fordonsantal och agentkoppling. Exemplet tog bara 1,97 s.

**Tester:** `tests/test_monthly_demand.py`, `tests/test_day_library.py`,
`tests/test_independent_daily.py`, `tests/test_monthly_search.py`, provenance.
**Klart när:** träff/miss/korruption/källbyte och ofullständiga manifest ger rätt
utfall; identisk kostnadslista, vinnare och stoppbevis. Ingen liveträdsmutation.
**Prioritet:** villkorad av steg 0; exempelvalidering på 0,35 s är inte bevis
för många minuters möjlig vinst.

#### Steg 5 initial mätning — senare reparerad, 2026-09-13

**Preflight avgjorde saken först.** Av 142 arkiv med metadata matchar **noll**
`demand_source_fingerprints` för det aktuella trädet; varje arkiv skiljer sig i
minst åtta inventariefiler som steg 1–4 rörde. De är korrekt invaliderade.
Ingen produktionstid rapporteras, ingen gammal evidens bands om till nya
source-hashar, och inget ersättningsarkiv byggdes. Exakt lokalt kommando när
ett kvalificerat arkiv finns står i evidensfilen.

Commit `294c75a` instrumenterade `_read`, `validate_demand_archive`,
`_archive_validation_state` och digest-loopen, men dess slutsats om säker varm
återanvändning var fel. Testet återställde `st_mtime` som flyttal medan
cache-nyckeln använde `st_mtime_ns`; nanosekunderna ändrades och testet skapade
en cachemiss. Med exakt återställda nanosekunder serverades ändrade route-bytes
från `_VALIDATED_ARCHIVE_CACHE`, och ändrad `demand_meta.json` låg kvar under
gammal build-key i `_ARCHIVE_METADATA_INDEX`. Båda var processglobala
`path/stat -> valid/index`-cacher av den form steg 5 uttryckligen förbjuder.

Reviewreparationen tar bort båda globala cacherna. Varje ny diskentré bygger
indexet från `demand_meta.json`-innehåll och fullvaliderar kandidaterna. Inom
`_resolve_new_release` kan samma index skickas vidare anropslokalt till flera
build-keys och byggs om efter varje demand-builder-anrop; valideringsresultat
återanvänds aldrig över en ny läsgräns.

**Hermetiskt reviewresultat — uttryckligen INTE produktionstid** (tre
syntetiska arkiv): en kall diskentré gör tre fullvalideringar, 12 JSON-läsningar
och 30 digests. Tre nya diskentréer gör nio valideringar, 36 JSON-läsningar och
90 digests. Tre uppslag som delar ett operationslokalt index gör fortfarande
nio valideringar och 90 digests men bara 27 JSON-läsningar, eftersom enbart
metadataindexeringen återanvänds.

Den ursprungliga slutsatsen “ingen onödig upprepning” är därmed inte giltig:
den mätte en osäker global cacheträff. Ingen optimering får väljas från denna
fixturmätning. Först måste den verkliga `prepare`-kedjan mätas när ett
current-source-arkiv finns; en ny diskentré ska fortsatt fullvalidera.

**Vid denna revision var steg 5 inte komplett.** `assemble_window`-detaljerna,
costing-/`ClosureRouteResolver`-räknarna och en samlad jämförelse av exakt
ledger, vinnare, disqualifications och stop proof saknas fortfarande.
`tools/profile_monthly_cost_ledger.py` kan utökas eller återanvändas; att den
redan binder identiteter ersätter inte de saknade räknarna och tiderna.

#### Steg 5 instrumentering KLAR — produktion fortfarande omätt, 2026-09-13

Reviewreparationen landade i `f5d0148`. Slutförandet hittade samma defekt i en
tredje processglobal cache: `ArchiveInputs` återanvände route-digests genom en
nyckel av path, size, inode och `st_mtime_ns`. Ett innehållsbyte med exakt
bevarad storlek och nanosekund-mtime gav stale identitet. Den globala cachen är
borta. Månadsresolvern delar i stället en verifierad `ArchiveInputs`-descriptor
inom sin egen operation, så den normala kostkedjan hashar varje arkiv en gång
utan att ett senare diskinträde auktoriseras av stat-data.

Mätgrupp 5–7 är nu komplett instrumenterade:

1. `assemble_window` mäter route-läsning, radtransformering, agent-JSON och
   atomisk publicering samt dagar, rader, agenter, bytes och output-digests.
   Den omätta produktionsvägen fortsätter att streama route-rader; mätningen
   får inte materialisera hela filen.
2. Cost-ledgern mäter varje parent och läser pricerens egna unit-/cachetal en
   gång. Resolvern räknar anrop och unika edge-tupler. Full identitet kommer
   från den befintliga innehållsbundna provideridentiteten som omfattar arkiv,
   nät och schedule; inga processlokala objekt-ID:n används.
3. Ett komplett hermetiskt beslut körs med och utan observer. Ledger,
   selected IDs, disqualifications, candidate statuses, cursor, stop proof och
   provider identity är exakt lika.

Detta är kontrakts- och instrumenteringsbevis, inte produktionstid. Av 142
arkiv matchar fortfarande noll den aktuella källidentiteten. Alla
produktionstider är därför `null` och ingen optimering väljs från
fixturvärdena. Steg 5:s nästa mätning sker först när den planerade riktade
värmningen ger ett current-source-kvalificerat arkiv. Steg 6 har inte startat.

#### Steg 5 riktad produktionsmätning — tidigare blockerare korrigerad, 2026-09-13

**Status: TARGETED PRODUCTION MEASURED, FULL MONTH UNMEASURED.**

Den oberoende granskningen accepterar `d221797` först tillsammans med
`977712a`: före reparationen låg de mätberoende output-digesternas arbete
utanför redovisningsroten och 45,8 % av den observerade
`assemble_window`-körningen saknades i fasbokslutet. `1eedc04` och `40f3cca`
är däremot överspelade i sin slutsats att en ny katalogkvalificering krävdes.
Den isolerade grenen saknade både huvudträdets befintliga, byteidentiska
katalogförnyelse och kompletta ignorerade indata. Efter att samma
innehållsbundna förnyelse och samtliga indata återställts rapporterade både
vardags- och helgkatalogen noll drift. Ingen grind försvagades.

En riktad byggning av 2027-06-25 kördes med explicit katalogkälla och
riktningsvarianter. Helgpoolen gav `cache_event: hit`,
`catalog_fallback: null` och ingen ny kandidatpool byggdes. Bygget gav 9 675
q50-, 9 374 q10- och 9 217 q90-fordon. Passagekalibreringen tog 48,437 s;
hela `pfe_variants_and_rounding` tog 66,064 s. Sensorernas heltalsmål och
GEH-grindar passerade, men arkivets samlade validering är `WARN`: trip-length
L1 är 0,599 mot gränsen 0,2, fritidsresornas median är kortare än arbetsresornas
och temporal holdout är inaktuell. Arkivet är därför endast diagnostik.

`tools/profile_monthly_cost_ledger.py` kan inte ta denna endagsprodukt som en
fullmånadsmätning. Dess fail-closed kontrakt kräver 1 950 unika dagenheter,
5 850 variantposter, 1 690 föräldrakandidater och ett kvalificerat manifest.
Siffrorna är mekaniskt härledda ur
`validation/subhour_monthly_search_profile_spec_v1.json`: 30 kalenderdatum
gånger 65 möjliga starttider ger 1 950 unika dagenheter; 26 möjliga
femdagarsstarter gånger samma 65 tider ger 1 690 föräldrar; tre
efterfrågevarianter per dagenhet ger 5 850 variantposter. Detta motsvarar 30
unika tredagarsbyggen för demand, med startdatum 2027-08-31 till 2027-09-29,
inte 1 950 separata demandbyggen.

En eftergranskning körd i en annan checkout rapporterade felaktigt att
endagsarkivet och dagbiblioteket saknades. De finns kvar i den isolerade
worktree där mätningen skapades och är lokalt omverifierbara. Den separata
fullmånadsgaten är ändå öppen: 0 av dess 30 demand-specifikationer har ett
aktuellt matchande arkiv i denna worktree. Manifestproducenten
`tools/qualify_subhour_demand.py` bygger saknade arkiv i en ny runs-rot och är
inte en ren indexerare. Därför startades varken de 30 byggena, manifestet eller
fullmånadsmätningen utan ett separat kostnadsbeslut.
I stället kördes en avgränsad mätning genom samma produktionsfunktioner för
arkivvalidering, assembly och en verklig heldagsstängning, utan SUMO:

| Del | Uppmätt resultat |
|---|---:|
| arkivvalidering, kall | 0,644 s |
| arkivvalidering, återläst i samma process | 0,184 / 0,183 s |
| assembly q10 / q50 / q90 | 0,354 / 0,394 / 0,355 s |
| costing, en heldagsstängning över tre varianter | 3,811 s |
| route grouping | 0,820 s |
| shortest-path detour | 0,562 s |
| window aggregation | 0,562 s |
| XML-parse | 0,323 s |

Alla sex reassemblerade route-/agentfiler är byteidentiska med arkivet.
Costing omfattade 28 266 fordonsanrop men 315 unika kanttupler. Det är inte i
sig en ny cachemöjlighet: `ClosureRouteResolver` memoiserar redan ruttens
offsets och vägkostnader, medan varje fordon fortfarande måste klassificeras
mot det aktuella tidsfönstret.

Eftergranskningen mätte också profilerarens egna resolverräknare till 0,0373 s,
cirka 1,0 % av den rapporterade costingtiden. Den ursprungliga artefaktens
`measurement_only_phases: []` beskriver därför dess dåvarande instrumentering,
inte noll overhead. Framtida körningar redovisar konstruktion och anrop i den
namngivna measurement-only-fasen `resolver_observer_measurement`. Observatören
patchar ett klassattribut processglobalt medan profilen kör; låset serialiserar
två profiler men kan inte isolera ett samtidigt produktionsanrop i samma
process. Den stödda isoleringen är därför profil-CLI:ns egen process.

Commit `1309cf6` innehåller dessutom den sedan tidigare motiverade kompakta,
atomiska skrivningen av `demand_meta.json`. Det parsedokument som används av
`build_id` är oförändrat och beteendet har test, men ändringen är ett separat
output-formatomfång som commitrubriken inte beskriver.

**Beslut:** steg 5 är riktat produktionsmätt men fullmånadsmätningen är öppen.
Ingen ny optimering eller månadsprojektion väljs från ett enda schema. Den
befintliga `ParsedWindowCostIndex`/`WindowCostIndex` är den robusta
återanvändningsvägen och förblir opt-in tills ett aktuellt fullmånadstest
passerar komplett population, identitet, exakt oracle-jämförelse och positiv
end-to-end-vinst. Evidensen finns i
`validation/passage_step5_targeted_day_measurement_20260913.json` och har
`release_evidence: false`. Steg 6 har inte startat.

#### Steg 5 fullmånadskorrigering — passagebevis PASS, 2026-09-14

Den första fullmånadskvalificeringen avslutades felaktigt med 1 475 q10-celler
som avvikande. Validatorn räknade en sensorträff i fordonets avgångskvart.
Kalibreringen begränsar i stället sensorns predikterade inträdeskvart,
`(departure_s + entry_offset) // 900`. Att alla dagssummor samtidigt var
exakta visade att felet låg i tidsplaceringen hos kontrollen, inte i ruttstöd
eller PFE-avrundning.

Den korrigerade kontrollen rekonstruerar day-library-identiteten och verifierar
dess innehåll, binder varje fit till sparat passage-resultat och output-hashar,
återspelar retained traces och PFE-bounds genom produktionssystemet samt kräver
identiska selection/routes/agents. Den jämför därefter systemets projektion per
sensorinträdeskvart med heltalsmålet och återmonterar varje tredagarsvariant
byteidentiskt. Ett strukturellt reparationspass återskapas med samma
innehållsbundna kandidatpool; boundary-fallback förblir fail-closed tills den
vägen kan reproduceras.

En omvalidering av de redan byggda arkiven passerade 30/30 arkiv, 90/90
q10/q50/q90-varianter, 48 unika day-library-poster och 144 unika
evidens/pool-bindningar. Alla sensorprojektioner och assemblies var exakta.
Körningen tog 2 370,589 s och startade varken SUMO eller demandbygge. Evidensen
är `validation/subhour_passage_entry_quarter_revalidation_20260914.json` med
`release_evidence: false`.

Detta stänger den falska passageblockeraren men inte hela steg 5. Ändringen är
ännu ocommittad, så den tidigare CODE_APPROVED-frysningen får inte återanvändas.
Nästa ordning är review/commit, ny källfrysning, ett nytt append-only
kvalificerat manifest byggt från de 30 befintliga arkiven och därefter den
fullständiga cost-ledger-profilen. Ingen demand behöver byggas om för detta.
Manifestproducenten har därför en explicit `--existing-runs-root`-väg som
kräver exakt ett fullvaliderat arkiv per build-key och delar endast ett
innehållsläst index inom anropet. Den gamla `--fresh-runs-root`-vägen behåller
kravet på en tidigare frånvarande rot. Producenten jämför dessutom de körande
skyddade källbytesen mot CODE_APPROVED; en gammal frysning kan inte märka den
nya validatorn som godkänd. Ett verkligt index-/valideringsanrop hittade 30/30
befintliga arkiv på 95,565 s utan build.

#### Reviewkorrigering och lokal leverans — 2026-09-15

Reviewen hittade och rättade tre problem före commit: replaymanifestets input-
och spårhashar måste matcha det sparade produktionsresultatet; memoiserade
framgångar måste kontrollera innehållet igen vid återanvändning; och profilern
byggde det expanderade passagesystemet en extra gång. Snapshotkontrollen
hashar även råa/komprimerade artefakter och kan inte kringgås genom oförändrad
filstorlek och återställd mtime. Det redan byggda systemet används i första
solve; varje reparationsbygge tidtas separat precis som i produktionen.
Regressionstesterna visade RED före respektive fix.

380 fokuserade tester passerade. Den oberoende eftergranskningen godkände dessa
reparationer och körde 11 riktade tester. Reviewevidensen finns i
`validation/subhour_passage_review_20260915_final.json`: 144 sparade
resultat/input/spår-bindningar kontrollerades och två arkivvarianter återspelades,
varav en med strukturell reparation. Den äldre fullmånadstidens 2 370,589 s
avser den tidigare implementationen; hela månaden är inte omkörd efter reviewen.
Den tiden får inte beskrivas som den nya validatorns eller sökningens hastighet.

Nästa steg är en ny källfrysning med källbundna kontroller, därefter ett nytt
kvalificerat manifest från de befintliga arkiven och fullmånadens cost-ledger-
profil. Kvarvarande ändrade webbartefakter måste redovisas i källfrysningen utan
att denna kodcommit tar över dem. Ingen uppvärmning eller demandgenerering
krävs av denna fortsättning. Steg 5 är fortsatt öppet; steg 6 är inte startat.

#### Steg 5 källfrysning, kvalificerat manifest och kontraktsfel — 2026-09-15

**Första frysningen.** En ny CODE_APPROVED-frysning av `d73ef73` gjordes med
kontrollerns egna `source_manifest`/`run_checks` enligt
`.ai-flow/config.complete-subhour.toml`, i
`runs/step5_code_approved_20260915/checks/`. Källdigest `d47263ec…` omfattar
505 filer, varav 41 under `web/` eftersom policyn binder `web/**/*`. Exakt tre
skyddade filer skilde sig från HEAD: `web/data/od_matrix.csv`,
`web/data/od_matrix.json` och `web/data/validation.json`, skrivna 2026-09-14
01:07 av septemberkampanjens sista demandbygge (2027-09-15). De frystes som de
låg, utan återställning eller commit, och redovisas i frysningens impact
inventory.

De konfigurerade kontrollerna gav: `git diff --check` godkänd; `make lint`
rc 2 med ett enda ofarligt fynd (E1111 i
`tools/profile_monthly_cost_ledger.py:222`); hela `pytest tests` 6 495
godkända, 16 fel och 5 errors. Alla 21 granskades mot sina tracebacks. 20
fanns redan vid `424c626`. Det nya, `test_explain_day_reuse`, körs nu
eftersom `runs/demand-days` skapades 2026-09-13/14 av kampanjen utan det
frysta jobbfönstret från 2026-09-10; dagbiblioteket är inte muterat och
`DayLibrary.lookup` är skrivskyddad. Gating-omkörningen av hela sviten med
exakt de 21 uteslutna gav 6 495 godkända, 21 överhoppade och rc 0 på
1 300,7 s, med oförändrad källdigest. **Obs:** godkännandet vilar därmed på
21 granskade uteslutningar och ett lintfynd, inte på en grön konfigurerad
svit.

**Kvalificeringen.** `validation/subhour_qualified_demand_manifest_20260915.json`
(evidens `subhour-monthly-qualify-2026-09-15`, content key `555c1dc6…`) blev
PASS från de 30 befintliga arkiven under
`runs/step5_monthly_qualify_20260913/demand_archives`: 90/90 varianter med
exakta sensorinträdesbevis och återskapade assemblies, båda support-audits
godkända, 5 068 896 fordon över de 90 variantposterna. Ingen demand byggdes
och SUMO startades inte. **Kvalificeringstid:** 1 842,15 s väggtid
(1 741,66 s user, 85,03 s sys), max RSS 1,707 GB enligt `/usr/bin/time -l`.
Det är tiden för att kvalificera underlaget, inte månadssökningens tid.

**Blockerare i första konsumenten.** `tools/profile_monthly_cost_ledger.py`
stoppade efter 14,58 s i `runner.prepare`, före mätningen. Ingen profilevidens
publicerades. En skrivskyddad reproduktion visade att
`qualified_manifest_archive_mismatch` accepterade 16 arkiv och avvisade exakt
de 14 tredagarsfönster som bara innehåller vardagar (start mån–ons). Kontrollen
krävde `candidate_catalog.keys == adopted_catalog_keys`, alltså båda
poolerna, medan `build_sumo_demand.py` bara skriver de pooler som fönstrets
`pool_composition` använder. Vardagsnycklarna stämde. Befintliga tester
täckte bara tvåpoolsfixturer och arkiv utan katalog, och qualifiern
tillämpade aldrig konsumentkontrollen. Ett PASS-manifest kunde därför vara
okonsumerbart. Samma kontroll skyddar `build_window_cost_index.py` och
cost-ordered-benchmarken.

**Rättning (användarbeslut).** Lokal commit `b5e1564`: konsumentkontrollen
kräver att nyckelmängden är lika med arkivets `pool_composition` och att varje
nyckel är den adopterade nyckeln för sin pool. Fel nyckel, saknad katalog och
en nyckel för en pool som fönstret inte använde avvisas fortfarande.
Qualifiern kör dessutom `require_consumable_archives` på varje PASS-manifest
och publicerar annars den befintliga inconclusive-terminalen. Två
konsumenttester och tre qualifiertester var RED före rättningen och GREEN
efter; 364 fokuserade tester godkändes och pylint gav rc 0. Mot de riktiga 30
arkiven accepteras nu 30 av 30 i stället för 16. Den första frysningen och
manifestet `20260915` bevaras som historik men kan inte auktorisera
`b5e1564`, eftersom qualifiern kräver att godkännandet binder de körande
källbytesen. En ny frysning och ett nytt manifest krävs före profilen.

**Andra frysningen och nytt manifest.** `b5e1564` frystes i
`runs/step5_code_approved_20260915b/checks/` med källdigest `2a157477…` (505
filer). Mot den första frysningen skiljer sig exakt de fyra committade
filerna; samma tre webbfiler skiljer sig fortfarande från HEAD. Kontrollerna
gav `git diff --check` godkänd, samma enda lintfynd och en fullsvit med 6 501
godkända tester och exakt samma 21 fel/errors med samma orsaker. De sex extra
godkända är de nya testerna. Gating-omkörningen med de 21 uteslutna gav 6 501
godkända och rc 0 på 1 312,4 s, med oförändrad källdigest.

`validation/subhour_qualified_demand_manifest_20260915b.json` (evidens
`subhour-monthly-qualify-2026-09-15b`, content key `2cb32dcd…`, sha256
`1b609f70…`) är PASS: 30/30 arkiv och 90/90 exakta varianter, och qualifierns
nya konsumerbarhetskontroll godkände alla 30. Kvalificeringen gjordes om i sin
helhet under den nya källan, fortfarande utan demandbygge eller SUMO.
**Kvalificeringstid:** 2 165,44 s väggtid (2 034,11 s user, 114,01 s sys), max
RSS 1,788 GB. Även denna tid avser kvalificering av underlaget, inte
månadssökningen. Manifestet `20260915` från den första frysningen är
historik och används inte av profilen.

**Fullmånadsprofil — PASS.** `tools/profile_monthly_cost_ledger.py` kördes mot
manifestet `20260915b` med arkivroten som `--runs-root`.
`validation/monthly_cost_ledger_profile_subhour-20260915-v2.json` (content key
`46ee2666…`, sha256 `399f7642…`, byteidentisk med `profile.json`) är PASS:
1 950 dagenheter, 5 850 variantposter och 1 690 föräldrar; SUMO-räknaren var
0 före och 0 efter, alltså noll starter uppmätta; processträdets RSS är
komplett; cache-bokföringen är konsekvent (8 450 uppslag = 6 500 minnesträffar
+ 1 950 missar, 1 950 diskmissar). Ledgerns content key är `bebfc81b…` och
diskväxten 14,3 MB. Försöket under den första frysningen publicerade ingen
evidens och dess tomma rötter ligger kvar.

**Månadsledgerns tid** (skild från kvalificeringstiden ovan): 7 320,35 s
väggtid; hela processen inklusive `prepare` tog 7 702,22 s (7 320,33 s user,
380,81 s sys). Peak RSS för processträdet 6,52 GB. Exklusiva faser:

| Fas | Väggtid | Andel |
|---|---:|---:|
| XML-parse | 4 368,7 s | 54,5 % |
| rutt/fordonsgruppering | 2 477,3 s | 30,9 % |
| `resolver_observer_measurement` (endast mätning) | 1 075,8 s | 13,4 % |
| arkiv-JSON-läsning | 71,0 s | 0,9 % |
| arkivdigest | 14,4 s | 0,2 % |
| kortaste väg / fönsteraggregering / sortering | 0,35 / 0,17 / 0,13 s | < 0,01 % |

Observatören gjorde 329 478 240 resolveranrop mot bara 569 unika kanttupler.
Dess 1 075,8 s ligger inne i den uppmätta väggtiden, så 7 320 s är en övre
gräns för den omätta produktionsledgern och inte dess tid. Den riktade
endagsmätningens cirka 1 % gäller inte i fullmånadsskala. Körningen gick
dessutom under minnestryck (cirka 2,6 GB swap och cirka 9,8 GB i kompressorn,
nästan helt från profilprocessen), vilket kan ha förlängt tiden. Äldre
v12-profilen (5 826,5 s, 1,31 GB) byggde på andra arkiv och äldre kod och är
inte jämförbar.

**Fas 5-beslut:** `phase_5_window_cost_index_needed: true`,
`phase_5_decision: TRIGGERED`, eftersom 7 320 s överskrider gränsen 600 s.
WindowCostIndex utvärderas därför enligt planens villkor.

#### WindowCostIndex — utvärderad, inte aktiverad, 2026-09-16

`tools/build_window_cost_index.py` kördes mot profilen med en färsk indexrot
(`runs/step5_code_approved_20260915b/wci-index`) och en ny evidenssökväg.
Körningen tog 31 273,03 s (8 h 41 min; 30 155,20 s user, 1 103,38 s sys, 6,46
GB max RSS) och slutade med returkod 1. Ingen evidens publicerades.

* **Exakthet: bevisad.** Orakeljämförelsen ligger före indexskrivningen i
  koden och passerade: alla 5 850 dagliga variantposter var fält för fält
  identiska med den bundna deterministiska dagkostnadscachen, och
  `load_index` godkände indexets bundna identitet, 1 950 enheter och 5 850
  poster. Indexet ligger på disk (9,3 MB) men är inte publicerad evidens.
* **Tidsvinst: negativ.** Hela vägen tog 31 273 s mot baslinjen 7 320,35 s.
  Planens villkor om uppmätt total tidsvinst före aktivering är därmed inte
  uppfyllt. Stackprov under körningen dominerades av JSON-flyttalsparsning:
  råvägen anropar `find_demand_archives` en gång per dagenhet utan delat
  arkivindex, så arkivens stora `demand_meta.json` läses om 1 950 gånger.
* **Rangordning, vinnare och stoppbevis: ej prövade.** Adoptionsvägen
  kraschade innan någon indexerad ledger fanns:
  `_IndexedLedgerSource.parent_cost` (rad 88) packar upp `daily_unit_records`
  som `(unit_id, schedule, _build)`, men kontraktet är `(unit_id, identity,
  build_schedule)` — samma fil gör rätt på rad 188. Felet blir
  `AttributeError: 'dict' object has no attribute 'schedule_id'`. Den vägen
  har alltså aldrig körts mot den verkliga populationen; den SUMO-fria
  beslutsjämförelsen (`runs/step5_code_approved_20260915/wci/compare_decisions.py`)
  kunde därför inte köras, eftersom den kräver publicerad PASS-evidens.
* **Beslut:** `WindowCostIndex` förblir opt-in och aktiveras inte. Två saker
  måste åtgärdas och mätas om först: adoptionsvägens uppackningsfel och
  råvägens per-enhets-arkivupplösning. Ingen av dem rättades här, eftersom
  aktiveringsbeslutet redan avgörs av den uppmätta tiden och varje
  källändring kräver en ny frysning. Steg 6 är fortfarande inte startat.

#### Fas 5-vägen rättad efter granskning — inte ommätt, 2026-09-16

Granskningen hittade fler defekter än kraschen. Samtliga är nu rättade och
testtäckta, men **ingen ny resume- eller indexbyggnadskörning har gjorts efter
rättningarna**, så inga nya tidssiffror finns. Det enda mätresultatet är
fortfarande 31 271,161 s preparation mot 7 320,348 s baslinje, alltså 4,27
gånger långsammare.

1. **Adoptionsvägens uppackning** (commit `a5a758d`). `daily_unit_records` ger
   `(unit_id, identity, build_schedule)`, men vägen läste mitten som ett
   schema. RED-testet föll på exakt produktionsfelet före fixen.
2. **Arkivupplösningen validerade per dagenhet.** Den första fixen delade bara
   arkivindexet; `find_demand_archives` kördes fortfarande per enhet, alltså
   upp till 1 950 fulla valideringar och lika många parsningar av samma stora
   `demand_meta.json`. Funktionen är nu uppdelad i fyra pass med en
   operationslokal, innehållshärledd mapping `build_key → validerad
   descriptor`. Kontraktet är en full validering per unikt build key: 30 för
   en månad, inte 1 950. Samma deduplicering finns i resume-vägen.
3. **Provideridentiteter kontrollerades inte på riktigt.** Resume jämförde
   bara nyckelmängden. Nu jämförs varje dagenhets rekonstruerade
   `provider.identity()` mot den sparade, och minsta skillnad avvisar innan
   orakelposten används.
4. **Source drift blockerar nu adoption.** Ett index skrivet av andra
   byggarbytes kan aldrig bli `ADOPTABLE`; driften redovisas i artefakten.
5. **Negativ prestanda är evidens, inte fel.** Byggvägen kastade tidigare ett
   undantag och slängde därmed resultatet av en dyr, korrekt körning. Den
   publicerar nu append-only `NOT_ADOPTED` med baslinje, preparationstid,
   persist/load, replay, total kall tid, negativ vinst, population,
   orakelutfall och ledgerjämförelse.
6. **Ledgerjämförelsen är gemensam.** Båda vägarna anropar
   `compare_decision_ledgers` och jämför `candidate_id`, `cost`,
   `daily_unit_ids` och `per_variant` i kanonisk ordning. Cachefält får skilja
   sig och redovisas separat; hela ledgerns content key duger inte som
   kriterium, eftersom den även digesterar diagnostiska cacheräknare. En
   avvikelse ger aldrig PASS.

Ett fynd på vägen som hade fällt även byggvägen: adoptionsinvarianten jämförde
antalet uppslag med antalet variantposter. Uppslag räknar relationer mellan
förälder och dagenhet, alltså 1 690 × 5 = 8 450, medan variantposterna är
1 950 × 3 = 5 850. Invarianten kräver nu rätt storheter på båda ställena.

**Kontroller:** 51 fokuserade tester passerar (`test_window_cost_index.py`,
`test_cost_ordered_execution.py`, `test_profile_monthly_cost_ledger.py`),
pylint rc 0 och rent `git diff --check`. Commit `f889071`, pushad till
`origin/claude/exciting-rubin-1e6k5m`.

**Uppmätt arkivupplösning — verkliga arkiv, 2026-09-16.**

> **Preliminär, ersatt av v2 nedan.** v1 behålls oförändrad som historik. Dess
> gamla väg extrapolerades från de 30 första dagenhetsidentiteterna i sorterad
> ordning, vilket inte är ett representativt urval, och påståendet att faktorn
> var en undre gräns följde inte av urvalet. Använd v2:s siffror.

`validation/archive_resolution_benchmark_20260916-v1.json`
(`release_evidence: false`) mäter den nya vägen över hela populationen och det
gamla per-enhetsbeteendet på ett avgränsat stickprov:

| | indexbyggen | fulla valideringar | JSON-läsningar | väggtid |
|---|---:|---:|---:|---:|
| ny väg, per build key | 1 | 30 | 150 | 54,75 s |
| gammal väg, per dagenhet (extrapolerad) | — | 1 950 | 7 800 | 2 545,7 s |

Populationen är 1 950 dagenheter, 30 unika build keys och 30 unika arkiv.
`unique_validated_archives` är 30, alltså fullvalideras varje arkiv
fortfarande; besparingen ligger i att samma 30 arkiv inte bevisas om för varje
tidsfönster. Stickprovet var 30 dagenheter på 39,16 s, alltså 1,3055 s och 4
JSON-läsningar per enhet. Hela mätprocessen tog 95,05 s med 586 MB peak RSS.
v1 motiverade faktorn 46,5× som en undre gräns eftersom stickprovet
återanvände det delade arkivindexet. Det påståendet dras tillbaka: urvalet var
inte representativt, och ingen gräns följer maskinellt av redovisningen.

**v2, stratifierad jämförelse — ersätter v1:s extrapolering.**
`validation/archive_resolution_benchmark_20260916-v2.json`
(`release_evidence: false`) mäter med drivern
`validation/benchmarks/archive_resolution_benchmark_v2.py` (SHA-256
`7fe98d9f…`, bunden i evidensen). De 30 build keys och valda arkivens
identiteter binds med digesten `0ea30c1e…`, så samma arkivmängd kan
verifieras vid reproduktion. Populationen är 1 950 dagenheter på 30 build keys,
med exakt 65 dagenheter per nyckel.

| Del | Värde |
|---|---:|
| Ny väg, direkt uppmätt | 54,30 s, 1 indexbygge, 30 fulla valideringar, 150 JSON-läsningar |
| 30 per-key-valideringar, direkt uppmätta | 46,82 s |
| Kontrafaktisk gammal väg, viktad per nyckel | 3 043,3 s, 1 950 valideringar, 7 800 JSON-läsningar |
| Nettobesparing (kontrafaktisk − ny) | 2 989,0 s |
| Kvot | 56,0× |
| Peak RSS / processtid | 575,5 MB / 102,20 s |

Alla 30 arkiv fullvalideras fortfarande (`unique_validated_archives` = 30).
Den kontrafaktiska armen mäter en full validering per build key och
multiplicerar med de 65 dagenheter som använder nyckeln. Två förbehåll gör att
kvoten **inte** är en garanterad gräns åt något håll, och evidensen påstår
ingen (`bound_claimed: null`):

* den kontrafaktiska armen återanvänder arkivindexet och innehåller därför
  inte den gamla kodens upprepade indexbyggen;
* per-key-valideringarna kördes efter den nya vägen, alltså med varm
  filsystemscache.

**Proportionen som avgör nästa beslut:** de viktade 3 043,3 s är cirka 9,7 % av
indexbyggets uppmätta 31 271,161 s. Resten låg i ruttparsning och
indexkonstruktion, som denna ändring inte rör. *(Rättat 2026-09-16: det var
en gissning, och den höll inte. Fasmätningen nedan ger parsning och
indexkonstruktion cirka 1 minut för hela månaden. Den kontrafaktiska armen
utelämnade dessutom de 1 950 indexombyggena, som enligt modellen står för
större delen av tiden. Se "Rättelse".)* Ett nytt fullständigt bygge
skulle alltså fortfarande landa långt över baslinjens 7 320,348 s, och
`WindowCostIndex` förblir opt-in och oadopterad. Ett sådant bygge får inte
startas utan ett uttryckligt beslut, eftersom det kostar timmar. Steg 6 är
fortfarande inte startat.

**Fasmätning av återstående WCI-kostnad — 2026-09-16.**
`validation/wci_phase_cost_benchmark_20260916-v1.json` (driver
`validation/benchmarks/wci_phase_cost_benchmark_v1.py`, urvalsregel och fem
produktionskällors SHA-256 bundna) mätte minsta, median- och största arkiv
efter ruttbytes, var och ett i kall process följt av varm upprepning med
identiska utdatahashar. Ingen produktionskod ändrades.

| Arkiv (ruttbytes) | Fordon | Kall väggtid | xml_parse | grouping | 195 fönster | Peak RSS |
|---|---:|---:|---:|---:|---:|---:|
| 145 760 833 | 150 346 | 2,91 s | 1,71 s | 0,12 s | ≈ 1 ms | 879 MB |
| 163 254 555 | 168 730 | 3,13 s | 1,89 s | 0,14 s | ≈ 1 ms | 957 MB |
| 181 568 252 | 186 847 | 3,32 s | 2,08 s | 0,15 s | ≈ 1 ms | 1 050 MB |

Nätladdningen tog cirka 2,0 s per process. **Modellerat** för 30 arkiv, alltså
inte direkt uppmätt: `xml_parse` 56,8 s, grouping 4,1 s, arkivindata 19,1 s och
totalt 153,7 s inklusive nätladdning per arkiv. Ingen fas kommer i närheten
av de minst 20 962 s som måste bort. En separat mätning visade att
`provider.identity()` och `cache_identity()` tar under 1 ms per anrop.
*(Rättat 2026-09-16: målet "minst 20 962 s" räknades som 28 282 s kvar minus
baslinjen. Den kvarvarande tiden byggde på v2:s kontrafaktiska arm utan
indexombyggen och är därför inte giltig. Se "Rättelse".)*

**Två observationer — rättade 2026-09-16, se "Rättelse" nedan:**

* **Noll korsande fordon i de tre mätta arkiven.** `crossing_vehicles` var 0 i
  vart och ett av de tre valda arkiven, alltså 3 av månadens 30. Ursprungligen
  stod här att specens avstängningskant `26355153_26842525_0` saknar all
  kalibrerad trafik och att spec:en prissätter en degenererad stängning. Det
  följde inte av tre arkiv. Helmånadskontrollen i rättelsen avgör frågan.
  Oavsett svar säger mätningen inget om fönsterkostnaden för en trafikerad
  kant.
* **Minnestryck som förklaring till de 8,7 timmarna var en hypotes, inte ett
  fynd.** `_raw_index_records` behåller alla 30 arkiv × 3 parsade varianter och
  deras index innan enhetsloopen. Kolumnen Peak RSS ovan visar *maximalt RSS*
  i en process som räknade samma arkiv två gånger, kallt och sedan varmt. Den
  mäter alltså inte vad ett kvarhållet arkiv kostar. Det misslyckade byggets
  6,46 GB och 19,58 GB är två olika mått (se rättelsen). Här stod också att
  bygget swappade kraftigt, men byggets egen logg innehåller ingen
  swapmätning. Summeringen "55 s upplösning plus 154 s modellerade faser" är
  struken som uttalande om byggtid. Den lägger ihop delkostnader och är ingen
  uppmätt projektion av ett helt bygge.

**Beslut:** WCI-spåret stängs **inte**. Att bearbeta och släppa ett arkiv i
taget är ett exakthetsbevarande *alternativ*, men dess nytta för byggtiden är
inte visad. Det är en produktionsändring och kräver först ett uttryckligt
beslut, liksom varje ny fullskalekörning.

**Rättelse: stängningskanten över hela månaden och minneshypotesen prövad
direkt — 2026-09-16.** Här redovisas två diagnostiker, båda med
`release_evidence: false`. Ingen produktionskod och ingen `web/data/*` har
ändrats, och varken SUMO, ett demandbygge eller ett WCI-bygge har startats.
Båda körde under Python 3.9.6 på koden i `401b386`. Drivrarna, den gemensamma
mäthjälpen `validation/benchmarks/wci_diag_common.py` och produktionskällornas
SHA-256 är bundna i evidensen.

*A — stängningskanten i alla 30 arkiv.*
`validation/wci_closure_edge_census_20260916-v1.json` (driver
`validation/benchmarks/wci_closure_edge_census_v1.py`, content key
`0ad3f52c…`, output-hash `8f01e425…`, arkividentitetsdigest `54b81834…`).
Drivern läste de 30 kvalificerade arkiven ett i taget, efter produktionens
upplösning och fullvalidering, och släppte varje arkiv innan nästa öppnades.
Footprint efter frigöring låg mellan 217 och 284 MB under hela körningen.
Körtiden var 266 s.

| Mått, 30 arkiv × q10/q50/q90 | Värde |
|---|---:|
| Dagenheter / fordon (produktionsparsern) | 1 950 (65 per arkiv) / 5 068 896 |
| Fordon och unika rutter över `26355153_26842525_0` | **0 / 0**, i varje arkiv och variant |
| Byteförekomster av kant-ID:t i de 90 ruttfilerna | 0 |
| Motsatt kant enligt nätets `from`/`to`: `26842525_26355153_0` | 945 429 fordon (27 690–34 368 per arkiv) |
| Vändningar från motsatt kant in på stängningskanten | 0 |
| Katalograder med kanten (vardag `8548c819…` / helg `77e237f0…`) | 0 av 434 / 0 av 435 |
| Katalograder med motsatt kant | 100 / 91 |

Katalogernas SHA-256 stämmer med `routes_sha256` i alla 30 arkivs metadata.
Parserns fordonsantal är lika med antalet `<vehicle `-taggar i alla 90 filer.

**Klassificering: katalogtäckning.** Det är varken fel kant eller en väg som
saknas i demand:

* Kanten finns i nätet och har fyra inkommande förbindelser.
* Registret (`data_in/sensors.json`) anger den som sensor 133:s *omätta
  motsatta körbana* (`measurement_status: unmeasured_estimated`).
* Den mätta riktningen `26842525_26355153_0` (Läraregatan V) bär trafik i
  varje arkiv.
* Ingen rutt i någon av de två katalogerna går över kanten, så ingen
  kalibrerad bil kan göra det.

Specen (`policy_status: user_supplied_unverified`) stänger alltså den omätta
riktningen. Om det var avsikten är ett beslut för specens ägare, inte för
diagnostiken.

En trolig strukturell orsak, som inte är mätt: i nod 26355153 mäter 133
inflödet medan 134 och 2276 mäter utflöden. En sensorförankrad rutt över
kanten måste därför antingen vända direkt efter 133, vilket ruttfiltren tar
bort, eller korsa en sensor på annat håll.

**Följd:** varje daglig fönsterkostnad i månadsspecen är noll. Fas 4-ledgern
(`runs/step5_code_approved_20260915b/profile/cost-ledger.json`) ger alla
1 690 föräldrakandidater exakt samma kostnad: noll fordon, noll timmar och
noll meter. En "vinnare" avgörs alltså bara av tie-break. Månadsresultatet ska inte användas som stängningsbeslut förrän
kanten är omprövad. Inget verkligt arkiv har trafik på kanten, så
exakthetstest med korsningar måste tills vidare vara syntetiska.

*B — retain eller stream, en kall beräkning per process.*
`validation/wci_retain_stream_memory_20260916-v1.json` (driver
`validation/benchmarks/wci_retain_stream_memory_v1.py`, content key
`4f7eac05…`, output-hash `9cc552ee…`; arkiven är v1-urvalet med minsta,
median- och största arkiv, bundet via `c05dd593…`). Retain speglar
loopstrukturen i `_raw_index_records`. Stream finns bara i drivern: den
beräknar ett arkivs enheter, släpper arkivet och öppnar sedan nästa.
Footprint mäts som `proc_pid_rusage` `ri_phys_footprint`, max RSS som
`getrusage` `ru_maxrss`. Swap är `vm.swapusage` och gäller hela systemet.

| Körning | Footprint efter nät | Footprint efter arkiv 1/2/3 | Efter frigöring | Livstidsmax footprint | Max RSS | Beräkning vägg / CPU |
|---|---:|---:|---:|---:|---:|---:|
| retain-1 | 209 MB | 768 | – | 801 MB | 943 MB | 2,91 / 2,90 s |
| retain-2 | 215 MB | 754 / 1 411 | – | 1 442 MB | 1 603 MB | 6,24 / 6,20 s |
| retain-3 | 219 MB | 753 / 1 404 / 2 119 | – | 2 139 MB | 2 315 MB | 9,58 / 9,53 s |
| stream-3 | 216 MB | 756 / 827 / 913 | 230 / 229 / 240 | 942 MB | 1 114 MB | 9,17 / 9,17 s |

* **Utdata:** identiska byte för byte. Retain-3 och stream-3 har samma
  indexposter, orakelposter och provideridentiteter för alla 195 enheter.
  Retain-1 och retain-2 stämmer med motsvarande delmängd av retain-3. I alla
  fyra körningarna är indexposterna fält för fält lika med den bundna
  dagkostnadscachen. De verkliga arkiven har dock 0 korsningar, så här prövas
  bara den tomma vägen. Den trafikerade vägen prövas syntetiskt i
  `tests/test_wci_retain_stream_diagnostic.py` (5 tester): omväg, avskuren
  destination och nekad avgång. Där är retain och stream byte-identiska och
  lika med den oberoende per-fil-beräkningen, och testerna bekräftar att
  stream har släppt föregående arkiv innan nästa öppnas.
* **Minne:** varje kvarhållet arkiv lade till 534, 651 och 715 MB footprint
  (medel 633 MB). Arkiven är ordnade stigande efter storlek. Stream föll
  tillbaka till 229–240 MB efter varje arkiv. `gc.collect()` hittade 0
  objekt, eftersom referensräkningen redan hade släppt allt. **Modellerat**
  (219 MB + 30 × 633 MB) blir en retain-väg över månaden 19,22 GB footprint.
* **Tid:** systemets swap ändrades med 0,0 MB i alla fyra körningarna, och
  beräkningsdelens CPU-tid låg inom 1 % av väggtiden. Vid tre arkiv kostar kvarhållningen
  alltså ingen mätbar tid. En retain-körning över 30 arkiv gjordes inte.

*Det misslyckade byggets två minnesmått* (`runs/step5_code_approved_20260915b/wci.log`,
`/usr/bin/time -l`, kod `b5e1564`) är olika storheter. Inget av dem är
belägg för det andra, och deras maxima behöver inte ha inträffat samtidigt:

* `maximum resident set size` 6 455 951 360 byte (6,46 GB): största antal
  sidor i RAM;
* `peak memory footprint` 19 578 747 112 byte (19,58 GB): kärnans
  footprint-bokföring, som även räknar komprimerat och utswappat minne.

Loggens `swaps` visar 0, men systemets swap registrerades inte under
körningen. Loggen visar alltså varken att bygget swappade eller att det inte
gjorde det. Samma logg ger 31 273,03 s real, 30 155,20 s user (96,4 %) och
1 103,38 s sys: processen låg på CPU i användarläge nästan hela tiden.

*Konkurrerande förklaring, mätt med en enkel tidtagning.* `b5e1564`
anropade `find_demand_archives` utan `_archive_index` en gång per dagenhet.
Varje anrop byggde då om arkivindexet från alla 30 arkivs
`demand_meta.json` (cirka 20 MB var). Funktionerna `_archives_for_build_key`
och `_read` är byte-identiska i `b5e1564` och nu. Tre nya processer tog
14,98, 15,19 och 15,40 s för ett indexbygge var, med CPU inom 0,7 % av
väggtiden. **Modellerat:** 1 950 × 15,19 s = 29 630 s ombyggnad, plus v2:s
viktade valideringar på 3 043 s, blir 32 673 s. Det är cirka 4 % mer än hela
byggets 31 273 s. Modellen överskattar alltså något, men den lämnar inget
utrymme åt en stor minnesdriven tidskostnad, och den stämmer med stackproven
som dominerades av JSON-parsning. Båda delarna togs bort i `f889071`, som
bygger indexet en gång och validerar en gång per build key.

**Slutsats om streaminghypotesen:**

* *Bekräftat:* kvarhållningen driver minnet. Tillväxten per arkiv är
  linjär, stream ligger platt, utdata är byte-identiska, och den
  modellerade footprinten (19,22 GB) ligger inom 2 % av byggets uppmätta
  toppfootprint (19,58 GB).
* *Inte bekräftat, och nu osannolikt:* att minnet förklarar de 8,7
  timmarna. Byggets tid var nästan helt user-CPU, retain och stream kostar
  lika mycket CPU vid tre arkiv, och de per-enhets indexombyggen som redan
  är borttagna täcker enligt modellen hela tiden.

Stream är därför en minnesåtgärd. Toppfootprinten blir cirka 0,94 GB mot
modellerade cirka 19 GB på en maskin med 24 GiB RAM. När B startade höll
kompressorn redan 20,5 GB data (4,4 GB fysiskt) och systemets swap var
5,1 GB. Någon uppmätt tidsvinst finns
inte. Den tidigare resten "28 282 s", och målet "minst 20 962 s" som räknades
fram ur den, gäller inte längre. Ingen uppmätt projektion finns av hur lång
tid ett helt bygge med nuvarande kod tar, och ingen sådan siffra anges här.
Både ett helt bygge och produktionsändringen till stream kräver ett
uttryckligt beslut.

**Avslutande orsaksmodell och canary på den reparerade koden — 2026-09-17.**
Två nya diagnostiker stänger den avgränsade WCI-utredningen utan att köra ett
fullständigt WCI-bygge, SUMO eller demand. Båda är
`release_evidence: false` och lämnar produktionskoden oförändrad.

`validation/archive_index_resolution_model_20260917-v2.json` (content key
`c9246979…`, output-hash `a6f7bb95…`) upprepade indexmätningen i tre färska
processer med den slutliga diagnostikhjälpen bunden till evidensen. Ett
indexbygge tog 15,647–15,966 s (median 15,826 s), läste 30 JSON-filer och
571 247 660 byte. De sex upplösningsfunktionerna är AST-identiska med koden
`b5e1564` som körde det misslyckade bygget.

Den historiska vägen modelleras till 30 860 s indexbyggen plus 6 222 s
validering, totalt 37 083 s. Det är **18,6 % mer** än den observerade
väggtiden 31 273 s. Modellen är därför inget exakt bokslut och den negativa
residualen tillskrivs ingen fas. Indexdelen ensam motsvarar 98,7 % av den
observerade väggtiden och användar-CPU låg nära väggtid i originalkörningen.
Tillsammans med stackprovet och koden visar detta robust att den upprepade
`_archives_for_build_key`-körningen var huvudorsaken, men inte exakt hur varje
sekund fördelades. `f889071` tar bort den kostnaden genom ett indexbygge per
operation och en validering per unik build key.

`validation/wci_current_code_canary_20260916-v1.json` (content key
`c36c0540…`, output-hash `3ca2abdc…`) kör den oförändrade
`_raw_index_records` på 1, 2 och 3 verkliga build keys i separata processer:

| Build keys | Dagenheter | Variantposter | Väggtid | Indexbyggen | Fulla valideringar |
|---:|---:|---:|---:|---:|---:|
| 1 | 65 | 195 | 25,233 s | 1 | 1 |
| 2 | 130 | 390 | 31,549 s | 1 | 2 |
| 3 | 195 | 585 | 38,323 s | 1 | 3 |

Alla tre körningar är fältidentiska med det bundna oraklet och byteidentiska
med motsvarande retain-körningar för indexposter, orakelposter och
provideridentiteter. Den linjära 1–3-key-modellen ger 215,244 s för
`_bound_inputs + _raw_index_records` vid 30 keys. Det är **inte** en uppmätt
fullmånadstid: slutlig orakeljämförelse, indexskrivning/-laddning och indexed
ledger ingår inte, och 30-key-vägen behåller fortfarande modellerat cirka
19 GB. Dessutom korsar noll verkliga fordon den valda kanten, så canaryn
bevisar inte kostnad eller exakthet för en trafikerad stängning.

**Beslut efter canaryn:** hastighetsdefekten i arkivupplösningen är reparerad
och direkt verifierad i liten skala. Streaming är fortfarande en möjlig
minnesförbättring, inte en visad hastighetsförbättring, och införs inte här.
Ett nytt fullskaligt WCI-bygge mot samma spec skulle producera ett exakt men
beslutsmässigt meningslöst nollresultat. Nästa nödvändiga steg är därför att
ompröva den riktade stängningskanten eller katalogtäckningen, kvalificera ett
fall med verkliga korsningar och först därefter köra en liten icke-noll-canary.
WCI förblir opt-in och **inte adopterat**.

**Varför benchmarken valde en riktning utan trafik, och reparationen — 2026-09-17.**

*Spårning.* `validation/closure_edge_lineage_census_20260917-v2.json`
(content key `35041043…`, output-hash `8ece9b53…`) följer kanten bakåt och
räknar varje kant i kedjan och dess motriktning i alla 30 kvalificerade arkiv,
ett i taget. Motriktningen tas från nätets `from`/`to`. v2 har samma
output-hash som den ocommittade v1-körningen; v1 binder källbytes från före
reparationen och committas därför inte. Kedjan:

1. `validation/closure_survivability_screen_v2.json` (2026-08-10) tar med
   riktade kanter inom 400 m från en sensor, som sekundär- eller tertiärgata och
   minst 30 m långa. Varje riktning bedöms för sig.
2. `tools/cost_ordered_benchmark.surviving_roads` sorterar de överlevande
   kanterna på `dist_sensor_m` och kant-ID och tar de 6 första. Båda
   körbanorna vid sensor 133 ligger 11,0 m bort och hamnar först.
3. `discovered_specs` bygger en spec per kant med `directed_edges=(edge,)`.
   Sub-hour-registreringarna 2026-08-31 valde fall ur den mängden genom
   hashsortering.
4. I `3f20d70` fick den frysta profilspecen kanten
   `26355153_26842525_0`, utan motivering. Specens förfäder använde
   `26842526_96527131_0`.

*Användarens väg är korrekt.* Webbgränssnittet ritar varje riktad kant som en
egen linje med sitt GeoJSON-`id`, och ett klick växlar exakt det ID:t
(`web/render.js`, `web/app.js`). `serve.py` validerar och vidarebefordrar
`directed_edges` oförändrade. `closure_seconds` skapar exakt en stängning per
listad kant, och `ClosureRouteResolver` matchar exakta ID:n. Inget av detta
ändrades. `serve.py` avvisar dessutom (422) en kant som den aktiva efterfrågan
inte bär alls. Det är befintligt beteende och ändrades inte heller.

*Rotorsak.* `surviving_roads` bevisar strukturell genomförbarhet, alltså att
stängningen lämnar nätet användbart. Benchmark- och profilurvalet saknade en
separat kontroll av trafikpåverkan. Närhet till en sensor bevisar inget
ruttstöd, vilket `traffic_sim/demand/route_support.py` redan konstaterar.
Därför kunde automatiken välja en omätt motriktning som ingen rutt använder.
Detta reparerar det automatiska benchmarkurvalet. Användarens vägval och
`ClosureRouteResolver` var inte felaktiga.

*Reparation: en versionerad urvalspolicy (ersätter `ed73102` och `36a61de`).*
`ed73102` lade effektkontrollen direkt i `discovered_specs`. `36a61de` flyttade
den till urvalet, men som en oversionerad modul under
`traffic_sim/simulation/`, och sviten valde fortfarande på
`structurally_eligible`. Båda är ersatta. Evidensen från de två commitsen
(`effect_eligibility_census_20260917-v1/-v2`, `wci_effect_canary_spec_20260917-v1`,
`wci_effect_canary_20260917-v1/-v2`, `closure_edge_lineage_census_20260917-v2`)
binder källkod från respektive commit och kan reproduceras där.

* **`structurally_survivable`** gäller för `surviving_roads` och den
  generella `discovered_specs`, som båda är oförändrade från `10518ae`.
  Upptäckten får innehålla kanter utan nuvarande katalogtrafik.
* **`effect_eligible`** gäller bara automatiskt valda benchmarkfall. De tre
  väljare som använder `discovered_specs` anropar samma funktion efter den
  strukturella upptäckten:
  - `tools/cost_ordered_benchmark.select_case`;
  - `tools/cost_ordered_benchmark_suite.select_suite_cases`, som nu filtrerar
    på `eligible` i stället för `structurally_eligible`;
  - `tools/subhour_cost_ordered_benchmark._metadata_inventory`/`select_cases`.

  Användarvalda stängningar berörs aldrig; en riktad kant utan trafik ger
  fortfarande ett giltigt nollresultat.
* **Versionering.** Policyn heter `closure_effect_eligibility_v1`. En
  registrering utan `case_selection_policy` tolkas som
  `structural_survivability_v1`, och en okänd policy avvisas. Verifierarna
  räknar om urvalet med den policy som artefakten själv namnger, så gamla
  frysta registreringar reproduceras exakt: legacy-inventeringen har inga nya
  nycklar och ingen effektskanning. Nya registreringar skriver
  `case_selection_policy`, `effect_rule` och `effect_inventory`.
* **Placering.** `tools/closure_effect_eligibility.py` ligger utanför
  `demand_source_paths` (39 sökvägar, ingen under `tools/` eller
  `traffic_sim/simulation/`), så inget demandarkiv ogiltigförklaras. Modulen
  ingår i benchmarkens `SEMANTIC_SOURCES`.

*Definition (`closure_effect_eligibility_v1`), fastställd före körning.*
Inventeringen tar kandidat-ID:n och gör ett pass per relevant katalogpool och
ett pass per kvalificerad arkivvariant. Den använder produktionens
`traffic_sim.simulation.disruption.parse_route_vehicles`, utan någon egen
XML-parser, och bygger en gemensam räknetabell
`kant → variant → build key → fordon` plus katalogrutter per pool. Variantens
hash tas från arkivvalideringen och bevakas med en stat-kontroll.
Orsakskoderna är exakt dessa, i denna ordning:

| Kod | Betydelse |
|---|---|
| `missing_network_edge` | kanten finns inte i nätet |
| `missing_required_pool` | en pool som arkiven kräver är obunden, saknas, har fel hash, har ett konfliktande namn, går inte att läsa eller avviker från sin bundna metadata |
| `no_catalog_route_support` | en bunden pool har ingen rutt över kanten (detalj: poolnamn) |
| `missing_required_variant` | en variant saknas, är overifierad, går inte att läsa eller avviker från sitt deklarerade fordonsantal; räknas aldrig som noll |
| `no_observed_archive_crossings` | q10, q50 eller q90 har inget passerande fordon, eller ingen dagenhet har trafik |
| `incomplete_archive_coverage` | en krävd build key saknar kvalificerat arkiv |
| `eligible` | inga av ovanstående |

Urvalet filtrerar först på `effect_eligible` och tillämpar sedan den befintliga
stabila ordningen (strukturell rang respektive `selection_sha256`). Det väljer
aldrig kanten med mest trafik.

*Tester (`tests/test_closure_effect_eligibility.py`, 29 st, plus
upptäcktstesterna).* RED kördes i en separat worktree på `36a61de` med de nya
testerna och den nya modulen. 10 fall föll av rätt skäl:
- sviten valde den tomma riktningen `26355153_26842525_0`;
- `select_case` och `select_cases` saknade policyargument;
- `case_selection_policy` saknades;
- `pre_canary_eligible` var den gamla nyckeln.

Drifttesterna föll när `verify_inventory` muterades så att den inte rehashar.
GREEN: alla 49 går igenom. Testerna bevisar följande:
- de frysta sub-hour-registreringarna (`validation/subhour_bounded_sumo_registration_*.json`)
  tolkas som legacy, och legacy reproducerar den frysta urvalsdigesten
  inklusive nollkanten;
- den nya policyn utesluter Läraregatans tomma riktning med en enda skanning;
- ordningen bland godkända kandidater är deterministisk;
- katalog- eller arkivdrift och manipulerad inventering gör evidensen
  ogiltig;
- varje fil parsas exakt en gång, och namngivna rutter räknas som
  produktionsparsern räknar dem;
- explicita användarspecar är opåverkade.

*Inventering.* `validation/closure_effect_inventory_20260917-v1.json`
(content key `57328bb0…`, output-hash `d41fbb38…`, inventering `4f3342f2…`,
kandidatlista `c348c2b6…`, 205,0 s) parsade 92 filer en gång vardera:
30 arkiv × 3 varianter plus katalogpoolerna weekday (`8548c819…`) och weekend
(`77e237f0…`). Manifestet är `subhour_qualified_demand_manifest_20260915b`.
`verify_inventory` är tom vid omkontroll.

| Kant | Närmaste sensor | Mätstatus | Katalog wd/we | Fordon q10/q50/q90 | Arkiv / dagenheter med trafik | Orsakskoder |
|---|---|---|---:|---:|---:|---|
| `26355153_26842525_0` | 133 | omätt motriktning | 0 / 0 | 0 / 0 / 0 | 0 / 0 | `no_catalog_route_support`, `no_observed_archive_crossings` |
| `26842525_26355153_0` | 133 | mätt | 100 / 91 | 315 143 ×3 | 30 / 1 950 | `eligible` |
| `26355153_96523321_0` | 134 | mätt | 58 / 56 | 264 671 ×3 | 30 / 1 950 | `eligible` |
| `96523321_26355153_0` | 134 | omätt motriktning | 52 / 51 | 172 225 / 234 922 / 254 212 | 30 / 1 950 | `eligible` |
| `8710974792_1759741980_0` | 2276 (91,1 m) | ej i registret | 0 / 0 | 0 / 0 / 0 | 0 / 0 | `no_catalog_route_support`, `no_observed_archive_crossings` |
| `9037093028_1305379743_0` | 1076 (87,6 m) | ej i registret | 1 / 1 | 40 473 / 42 528 / 42 887 | 30 / 1 950 | `eligible` |

*Canary-spec.* Den första godkända kandidaten i strukturell ordning är
`26842525_26355153_0`. Den nya append-only-specen
`validation/wci_effect_canary_spec_20260917-v2.json` har content key
`e73a2a9a…`, spec-nyckel `d38038fd…` och `search_id`
`subhour-cold-ledger-profile-2027-09-effect-canary-v2-fdc134ec4e`. Den binder:
- den valda riktade kanten och policyversionen;
- kandidatlistans digest och arkivmanifestets content key;
- katalogpoolernas identiteter;
- inventeringsevidensens content key;
- källspecens hash, som är oförändrad och identisk med `10518ae`.

Den frysta septemberspecen är orörd.

*Canary med verklig kostnad.* `validation/wci_effect_canary_20260917-v3.json`
(content key `32154c81…`, output-hash `26a94aba…`, status **PASS**). Innan
körningen startar vägrar drivern spec som har drivit, inventering som inte
längre stämmer, inaktuella produktionskällor och en kant som inte är
policyns förstaval. Oraklet (en oberoende per-fil-väg) räknade 195
dagenheter på 999,7 s.

| Build keys | Dagenheter | Berörda fordon | Berörda dagenheter | Kostnad > 0 | `_raw_index_records` | Indexbyggen | Valideringar |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 65 | 232 845 | 65 | 65 | 39,71 s | 1 | 1 |
| 2 | 130 | 514 533 | 130 | 130 | 62,80 s | 1 | 2 |
| 3 | 195 | 866 217 | 195 | 195 | 86,44 s | 1 | 3 |

Kontrollerna gav följande:
- oraklet, kostnaden per enhet och provideridentiteterna är exakt lika;
- 1- och 2-nyckelskörningarna ingår identiskt i 3-nyckelskörningen;
- ingen SUMO-process startades och inget demandarkiv skapades;
- `canary_confirmed` är sant.

Alla hashbindningar räknades om från disk: content keys, driver och hjälpare,
produktionskällor, kontraktsfiler, output-hashar, spec-nyckel,
inventeringsdrift och kedjan canary → spec → inventering. Tiderna är inget
underlag för en projektion av ett helt bygge.

*Kvar efter `9b12e13`:* se granskningen nedan.

**Granskning av `10518ae..9b12e13` och beslutsunderlag för full WCI — 2026-09-17.**

Granskningen gällde nettodiffen, inte commitmeddelandena. Utfall: **`9b12e13`
avvisas som slutleverans.** Grundmodellen höll, men sju kontraktsbrister
rättades med RED/GREEN-test. Kontraktet ändrades inte.

*Det som höll:*
- `surviving_roads` och `discovered_specs` gav samma digest (`a5a8fc0c…`, 6
  vägar och 24 specar) i `10518ae` och i det aktuella trädet, mätt mot de
  verkliga arkiven.
- Effektkontrollen nås bara från de tre automatiska väljarna och
  diagnostikdrivrarna. `serve.py`, `web/`, `traffic_sim/` och
  `run_scenario.py` är orörda sedan `10518ae`.
- Modulen ligger utanför `demand_source_paths` (39 sökvägar). Arkiven
  validerades om utan fel av den nya censusen.
- Gammal evidens är byte-identisk, och den frysta månadsspecen är identisk
  med `10518ae`.

*Fynd, i allvarlighetsordning, alla rättade:*
1. **Hög.** Parserfel och överhoppade fordon blev inte reason codes
   (`tools/closure_effect_eligibility.py`, `_parse_archive`).
   - Oläsbar XML kraschade hela inventeringen.
   - Ett fordon som refererar en namngiven rutt hoppas tyst över av
     produktionsparsern och lästes därför som observerad nolltrafik.
   - Nu jämförs varje variants fordonsantal med `pfe_fit_variants.*.vehicles`
     i den hashbundna `demand_meta.json`. Varje katalog jämförs med
     kandidatantalet i sin `catalog.meta.json`, som är bunden via
     `metadata_sha256`.
   - Avvikelser, lässfel och obunden metadata ger
     `missing_required_variant` eller `missing_required_pool` med detaljen
     `parse_error`, `vehicle_count_mismatch`, `route_count_mismatch` eller
     `metadata_sha256_mismatch`. De blir aldrig `no_observed_*`.
   - Alla 30 verkliga arkiv och båda katalogerna stämmer exakt och har bara
     inline-rutter.
2. **Hög.** Svitens registrering tappade policyn
   (`tools/cost_ordered_benchmark_suite.py`, `build_registration`).
   `record["selection"]` skrevs om utan `case_selection_policy`, så en
   effektgrindad svit skulle ha tolkats som legacy. Dessutom byggdes
   inventeringen två gånger, och andra gången mot fel `data_root`. Nu väljer
   sviten en gång, skickar urvalet vidare och registrerar policy, regel och
   inventering. Under v1 räknas bara ett uttryckligt `eligible: true`.
3. **Medel.** Effektevidensen verifierades inte vid drift.
   - Registreringarna bar bara en inventeringsnyckel.
   - Nu bär ett v1-urval hela inventeringen.
   - `verify_selection_evidence` rehashar varianter, kataloger, katalogmetadata
     och `demand_meta.json`.
   - `cost_ordered_benchmark.verify_bindings` och subhour-verifieraren
     använder den, och subhour kräver dessutom att inventeringsnyckeln
     reproduceras.
   - Legacy-urval kräver ingen evidens.
4. **Medel.** Minnet höll två stora varianter samtidigt. Den förra listan
   levde kvar medan nästa fil parsades. Nu släpps varje tolkad fil innan
   nästa läses, och ett test bevisar det med svaga referenser.
5. **Medel.** Det fasta fallvalet märktes som effektgrindat.
   `select_case(from_archives=False)` skrev `closure_effect_eligibility_v1`
   fast inget screenades. Nu registreras `structural_survivability_v1`, och
   en uttrycklig v1-begäran utan arkiv avvisas.
6. **Låg.** Subhour-väljaren läste nät och kataloger från `ROOT` i stället
   för registreringens `data_root`. Nu används `data_root` både vid bygge och
   vid omräkning.
7. **Låg.** Upptäcktstestets stubb angav ett gammalt filnamn och gav evidens
   utan inventering. Nu returnerar den evidensen för en tom inventering.

*RED/GREEN.* De uppdaterade testerna kördes mot `9b12e13` i en separat
worktree, där bara fixturernas konstant `VARIANT_FIT_KEYS` lagts till.
- 21 föll. Bland beteendeskälen: `ParseError`, en tolkad fil som fortfarande
  hölls, fel policyetikett, dubbel skanning och sviten som valde ett fall
  utan `eligible`.
- Den namngivna rutten gav vid `9b12e13` `no_observed_archive_crossings`,
  alltså exakt felet i fynd 1.
- Sju discovery-fall föll bara för att stubben använder det nya API:et.
- GREEN: 63 policy- och discoverytester går igenom. Den fokuserade sviten gav
  483 godkända, 1 överhoppat och 1 fel (sigillet).

*Ny evidens (append-only, `release_evidence: false`):*
- **Inventering.** `validation/closure_effect_inventory_20260917-v2.json`
  (content key `c298de42…`, inventering `458713e4…`, 104,0 s) ger samma sex
  omdömen och samma ordning som v1.
- **Spec.** `validation/wci_effect_canary_spec_20260917-v3.json` (content key
  `919df52e…`) pekar på samma spec `d38038fd…`.
- **Canary.** `validation/wci_effect_canary_20260917-v4.json` (content key
  `e69c57b1…`) har status **PASS**. Oraklet, enhetsdigesterna,
  enhetskostnaderna och postdigesterna är identiska med v3, och de berörda
  fordonen är desamma (232 845 / 514 533 / 866 217).
- **Beslutsunderlag.** `validation/wci_full_build_decision_20260917-v1.json`
  (content key `8b20acf8…`).
- **Bindningar.** Alla 87 kontroller räknades om från disk.
- **Äldre evidens.** v1-inventeringen, spec v2 och canary v3 binder exakt
  källorna i `9b12e13`. Vid nya HEAD rapporteras de därför som inaktuella,
  vilket är avsett fail-closed-beteende.

*Beslutsunderlag för full WCI: `DO_NOT_START_FULL_BUILD`.* Underlaget utgår
från canary v3, med v4 som replikering.

| Del | Status | Värde |
|---|---|---|
| `_bound_inputs` | mätt | 4,3–4,5 s (v3), 2,2–2,4 s (v4) |
| arkivindex, 30 kataloger | mätt i full skala | 15,65–15,97 s, byggs en gång |
| validering av alla 30 build keys | mätt i full skala | 95,73 s (summan av medianer) |
| `_raw_index_records`, 1/2/3 nycklar | mätt | 39,71 / 62,80 / 86,44 s (v3); 19,82 / 31,11 / 42,72 s (v4) |
| enbart mätning (förpass) | mätt | 0,53–0,54 s per körning |
| oberoende orakel, 195 enheter | mätt i en skala | 999,7 s (v3), 495,2 s (v4) |
| orakel för 1 950 enheter | **okänt** | ingen tillväxtlag; ett fullbygge läser i stället ledgerns cache, som inte finns för en effektkant |
| indexskrivning och `load_index` | ej mätt | – |
| indexerad ledger över 1 690 föräldrar | ej mätt | – |
| toppminne, 1/2/3 nycklar | mätt | 0,78 / 1,43 / 2,15 GB |
| räknare | mätt | index 1; valideringar 1/2/3; JSON-läsningar 34/38/42 |

**Modellerad råfas för 30 nycklar (ingen WCI-tid):**
- Linjär modell: 16,25 + 23,37 s/nyckel ≈ 717 s, med spannet 709–725 s över
  stegen.
- Komponentmodell: index 15,83 s + validering 95,73 s + beräkning 61,04 s
  skalad ×10,009 (routebytes) eller ×10,065 (kryssande fordon) ≈ 722–726 s.
  De tre stickprovsarkiven är precis 10,0 % av månaden.
- Sammantaget blir det **709–726 s (11,8–12,1 min)** i det långsamma
  maskinläget.
- v4 körde samma kod och indata cirka 2× snabbare: CPU-kvoten var 0,49–0,50
  i alla faser, även i ren mätning. Det är maskinens prestandaläge, som inte
  registreras. Över båda lägena blir spannet **347–726 s**.
- Modellen förutsätter inget minnestryck, vilket minnesmodellen motsäger.

**Jämförelse (bara jämförbara delar):**
- Arkivupplösningen var modellerad till 37 083 s för den gamla vägen (varav
  30 860 s indexombyggen) och är nu mätt till 111,6 s.
- Den misslyckade körningens råfas tog 31 271,161 s. Den prissatte dock en
  kant utan kryssningar, så jämförelsen gäller samma funktion men inte samma
  last.
- Ledgerbaslinjen på 7 320,348 s kan bara jämföras med ett helt bygge, och
  ett sådant är inte mätt. Råfasmodellen motsvarar 9,7–9,9 % av baslinjen,
  vilket inte är något nyttopåstående.

**Svar på frågorna:**
1. **Är 8 h 41 min-defekten borta?** Ja, för sin orsak. Canaryn visar ett
   indexbygge och en validering per build key, oberoende av antalet
   dagenheter.
2. **Vad är direkt mätt?** Bundna indata, indexbygget och alla 30
   valideringar i full skala, råfasen och exaktheten vid 1–3 nycklar, oraklet
   för 195 enheter och minnet vid 1–3 nycklar.
3. **Vad är bara modellerat eller omätt?**
   - Modellerat: råfasens beräkning och toppminnet för 30 nycklar.
   - Omätt:
     - månadsledger och cache för en effektkant;
     - indexskrivning och laddning;
     - indexerad ledger;
     - beslutsjämförelse;
     - beteende under minnestryck.
4. **Kan retain-minnet fortfarande nå cirka 19 GB?** Ja. Modellen ger
   19,8–21,5 GB (18,4–20,0 GiB) mot 19,58 GB i den misslyckade körningen och
   24 GiB i maskinen. Produktionsloopen håller fortfarande alla arkiv; bara
   diagnostikens streamloop är liten, modellerat ≤ 3,4 GB.
5. **Ger ett fullbygge beslutsnyttig evidens nu?** Nej.
   - Den enda månadsprofilen binder `26355153_26842525_0`, som policyn
     avvisar, så bygget skulle prissätta noll igen.
   - Tidsmätningen skulle inte representera en trafikerad kant.
   - Retain-minnet ligger över gränsen.
6. **Budget för ett framtida bygge:**
   - råfasen: mjuk gräns 1 090 s, hård gräns 1 820 s;
   - hela bygget: hårt tak 7 320,348 s;
   - minne: livstidsfotavtryck högst 12 GiB och swaptillväxt högst 1 GiB.

   Retain-loopen ryms inte i den minnesgränsen, men streamloopen gör det.
7. **Automatiska stopp:** bygget ska avbrytas vid något av följande:
   - profilens spec är inte effektberättigad, eller dess evidens har drivit;
   - någon bunden hash har drivit;
   - fotavtryck eller swap går över gränsen;
   - råfasen passerar 1 820 s, eller hela bygget passerar 7 320,348 s;
   - antalet indexbyggen är fler än 1, valideringarna fler än 30 eller
     JSON-läsningarna fler än 150;
   - populationen avviker från 1 950 / 5 850 / 1 690;
   - något i oraklet saknas eller skiljer sig;
   - månaden prissätts till noll;
   - en SUMO-process startas eller ett demandarkiv skapas.

*Före ett fullbygge krävs:*
1. ett månadsfall fryst under `closure_effect_eligibility_v1`, eller ett
   uttryckligt beslut att använda canary-specen;
2. en månadsledger och dagkostnadscache för fallet, med eget beslut och egen
   budget;
3. en produktionsloop för råfasen som ryms i minnesgränsen och bevisas exakt
   mot retain-loopen;
4. en övervakare som tillämpar stoppvillkoren.

*Testfel som fanns före ändringen:*
- `test_the_seal_covers_the_real_import_closure` faller med samma lista
  (`demand/__init__.py`, `demand/day_library.py`, `traffic_sim/ops/__init__.py`,
  `traffic_sim/ops/io_phases.py`) på `10518ae` och i det aktuella trädet.
- `test_real_registered_case_publishes_completeness_for_gate_s[performance-miss]`
  är tidsberoende. Med `sumo/` tillgängligt gick det igenom 3/3 gånger på
  både `10518ae` och det aktuella trädet i dag. Tidigare föll det på båda.
  En ren worktree utan `sumo/` ger i stället `network drift` för båda
  parametriseringarna och är ingen giltig jämförelsemiljö.
- Gatarna ändrades inte. Steg 6 är inte startat.

### Steg 6 — isolerad byggare och kontrollerad parallellism

**Filer:** `build_sumo_demand.py`, `monthly_demand.py:_resolve_new_release`,
`day_library.py`, process-/köhantering i `independent_daily.py`.

1. Gör först serial byggning med explicit input-root/output-root. Inventera alla
   globala SUMO_DIR/web-output/importberoenden; CLI ska behålla sitt kontrakt.
2. Låt jobbspecifika produkter byggas i egen temporär katalog. Publicera ett
   komplett arkiv atomiskt. Uppdatering av gemensamma UI-produkter ska vara en
   separat avsiktlig operation. Bekräfta att fel lämnar gamla produkter intakta.
3. Behåll lås tills den skyddade resursen verkligen är isolerad. Inför single-flight
   per identitetsnyckel så två arbetare inte bygger/publicerar samma dag samtidigt.
4. Mät serial baseline. Prova sedan två arbetare med en gemensam totalbudget för
   PFE, solver och SUMO. Multiplicera inte 10 PFE-arbetare med tre varianter och
   flera datum. Bind även minnesbudget och avbrottspropagering.
5. Vänta in och hantera alla barn innan ett fel/avbrott publiceras. Resultatens
   ordning ska följa datum/seed, inte vilken process som blir klar först.

**Tester:** dagbibliotek, månadsdemand, independent_daily_queue och builder;
dubbla nycklar, arbetar-krasch, avbrott, partiella utdata och serial/parallell likhet.
**Stöd:** [Python executors](https://docs.python.org/3/library/concurrent.futures.html).
Processer har serialiseringskostnad; mer parallellism är ett experiment, inte en garanti.

### Steg 7 — SUMO-start, persistent worker och libsumo-experiment

**Filer:** `automatic_passage.py:_measure/_measure_batch`,
`tools/departure_reconciliation.py` och SUMO-runtimeanrop som steg 0 identifierat.

1. Mät tom uppstart på exakt nätverk/runtime, faktisk processväggtid och intern
   simuleringsklocka. Använd skillnaden som blandad overhead, inte automatiskt
   nätparsning. Mät samma samtidighet som produktionen.
2. Prova en worker med två scenarier. `traci.load` laddar nätet igen;
   `traci.simulation.loadState` kan undvika detta. Börja med tomt tillstånd före
   första fordonet. Spara RNG uttryckligen och skapa korrekt tillstånd per seed.
3. Kontrollera klocka, framtida avgångar, fordon, signaler, detektorer, routes,
   closureändringar, filutdata och alla RNG-källor mellan jobben. Testa A→B och
   B→A mot två separata kalla processer; testet ska upptäcka ordningsberoende.
4. Jämför råa sensorceller, rutter/tider, population och health. Testa baseline och
   closure. Samma dags totalantal räcker inte för godkännande.
5. Testa libsumo separat och versionsmatchat i isolerad miljö. Det tar bort socket-
   kommunikation, inte bevisligen nätladdning. Parallella instanser kräver separata
   processer. Ingen global LIBSUMO_AS_TRACI-aktivering före verifiering.
6. Behåll kall processväg vid stödproblem. Välj den nya vägen endast om hela
   mätvågen blir snabbare, minnet är acceptabelt och resultaten förblir identiska.

**Stöd:** [SUMO SaveAndLoad](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html)
anger att RNG inte sparas som standard och att vissa interna modelltillstånd
inte sparas. [Libsumo](https://sumo.dlr.de/docs/Libsumo.html) dokumenterar socketvinsten
och processkravet. Dessa dokument garanterar inte likhet för denna modell.
**Vinstområde:** endast den uppmätta undvikbara delen av 879,6 s SUMO-faser.
Multiplicera inte 0,6 s uppstart med antalet samtidiga processer som om de vore seriella.

### Steg 8 — sammanhängande slutverifiering och leverans

1. Kör de gemensamma referensfallen med alla godkända ändringar tillsammans.
   Summera inte isolerade vinster: mät nettovinsten igen med lika cachetillstånd.
2. Kör en riktig baseline/closure-canary med alla varianter och ordinarie seedar.
   Återspelning av gamla spår är inte test av ändrad SUMO-körning.
3. Kontrollera vilka ändrade källfiler som påverkar demand- och katalogidentitet.
   Gör klart källändringarna innan katalogförnyelse. Använd byte-ekvivalensvägen
   bara om dess befintliga kontrakt medger ändringarna; annars krävs rätt
   kvalificering. Bredda inte certifikatet för att slippa ett misslyckat test.
4. Ingen all-datum-warming. Bygg endast det diagnostiken behöver. Redovisa
   engångskostnad för kall cache separat från följande återanvändningskörningar.
5. Verifiera att status/progress beskriver utfört arbete: requested days, aktuella
   kalibreringar, återanvända historiska underlag, färdiga kandidater och resultatlänk.
   Detta förbättrar observerbarhet; räkna det inte som simuleringshastighet.
6. Uppdatera aktuella projektblock med verifierat läge. Rapportera varje stegs
   accept/reject/deferred och varför. Inga löften om minuter innan mätning finns.

Rekommenderad gemensam fokuserad kontroll (utöka med berörda feltester):

```sh
python3 -m pytest -q tests/test_trial_dynamic_passage.py tests/test_dynamic_assignment.py tests/test_automatic_passage.py tests/test_passage_solver_checkpoint.py tests/test_passage_evidence_pruning.py tests/test_build_sumo_demand.py tests/test_day_library.py tests/test_monthly_demand.py tests/test_independent_daily.py tests/test_monthly_search.py tests/test_demand_provenance.py
git diff --check
```

Om miljö/fil saknas: rapportera konkret vad och använd en tillgänglig smalare
kontroll; påstå inte att hela kontrollen passerar. Fokuserade tester ersätter inte
fullständig releaseevidens. Preliminär policy/global-best-gräns består.

### Instruktion att ge nästa implementerande modell

> Läs denna körinstruktion i IMPROVEMENT_PLAN.md och börja med steg 0. Verifiera
> verkligt repo och indata innan arbete. Fortsätt stegvis genom resultatneutrala
> förbättringar, mät varje ändring och behåll endast verifierade nettovinster.
> Dokumentera alla beslut i samma plan. Ändra inte modellkontrakt, återanvänd inte
> fel datum-/poolidentitet, minska inte seedar och starta ingen generell warming.
> Lägg inga semantiska ändringar i en prestandapatch. När ett experiment inte
> förbättrar helheten, lämna det oaktiverat och fortsätt till nästa oberoende steg.

## Performance research checkpoint — 2026-09-12 completed June search

Research and proposed implementation order; no new optimization is activated by
this section. Scope: preserve exact outputs, sensor constraints, stochastic seeds,
route provenance and the existing composition-aware day identity.

Measured run: `speed-monthly-june-20260912`, 3758.937 seconds active elapsed.
The earlier estimates of 29 demand builds confused parent schedules with demand
envelopes. There are 31 backend entries, including one reused canary archive;
30 new demand archives and 49 new passage calibrations were observed in the run
window. The final accounting's 52 full calibrations includes three historical
canary calibrations and must not be reported as 52 executions in this run.

| Exclusive timing category | Seconds |
| --- | ---: |
| Automatic passage (49 timing records) | 2092.043 |
| Remaining PFE/variants wrapper | 672.457 |
| Outside PFE/variants wrapper, not fully attributed | 994.437 |

Inside automatic passage: learning measurements 339.911 s, validation measurements
539.680 s, system preparation 556.435 s, integer solving/system construction
234.391 s, staging/structure 205.391 s, evidence retention 128.500 s, input
preparation 33.825 s, report serialization 24.047 s. These are nested phase wall
times; do not add them to their parent or sum subprocess worker-seconds as elapsed.
The timings were selected by the job's creation/finish window and cross-checked
against new archive dynamic_passage totals (2092.091 s, rounding difference).

### Implementation order and acceptance

1. **Account for the remaining time on a saved envelope.** Instrument archive
   validation, assembly, copying, hashing, metadata parsing, costing and runner
   initialization separately. Separate executed work from diagnostics inherited
   from an existing archive. Use cProfile for attribution and unprofiled paired
   wall-time measurements for performance. No new month run is needed. Measure
   full-hit and mixed hit/miss paths as well as a cold calibration.
2. **Return and reuse verified passage data.** `tools/trial_dynamic_passage.py`
   `load_source` already builds and verifies the source PassageSystem;
   `automatic_passage._refine` immediately builds it again.
   `expand_departure_support` additionally builds a dummy system solely to
   validate inputs. Extract complete validation into a shared routine and offer
   an internal immutable validated-source object. Keep the public loader's
   existing tuple contract compatible. Preserve sensor/scenario/option ordering,
   CSR entries including duplicate passages, boundary counts and solver inputs.
   Do not replace checks with an unchecked caller-supplied boolean.
3. **Parse structure inputs once per immutable snapshot.**
   `calibrated_structure_report` calls route metrics, purpose lengths and purpose
   bins, then repeats pool analysis. Share parsed routes/agents and cache only
   pool-derived metrics by content identity; compute candidate counts and
   quarter-dependent audits anew after departure changes. Geometry is ALREADY
   cached and the solver ALREADY uses sparse CSR: neither is a new speed claim.
4. **Reuse fully validated archive snapshots within one preparation.**
   `find_demand_archives` validates matches, then resolver `prepare` validates
   the selected archive again. Profile downstream runner reads as well. Pass an
   immutable verified archive descriptor and parsed metadata through these
   boundaries. Preserve verification on external entry, corruption detection,
   generation binding and detection/rejection of files changed during use.
   Path-only or mtime-only caches are not substitutes for integrity validation.
5. **If assembly/copying is material, isolate the builder output workspace.**
   Current `_resolve_new_release` owns a global demand lock and builds through
   live products before restoring them. A pure build-to-directory interface and
   atomic immutable publication would permit controlled independent work and
   remove live-file churn. First preserve serial output equivalence; parallelize
   only after per-key single-flight, cleanup/cancellation and a shared CPU/memory
   budget exist. Preserve exact three-day concatenation and chronological IDs.
6. **Only then experiment with persistent SUMO workers.** `simulation.loadState`
   avoids network reload; `traci.load` does not. Save/restore RNG state and bind
   each seed, input, time origin, output destination and closure configuration.
   Start with an empty pre-simulation state; require cold-vs-reloaded equality
   for sensor counts, routes, departures, health and closure outcomes. libsumo
   removes socket communication but is not proof of avoided network parsing.
   Parallel libsumo requires separate processes. This remains an experiment.

Benefit bounds: halving the 556.435 s preparation bucket saves 4.64 minutes;
halving preparation plus staging/structure saves 6.35 minutes. These are scenarios,
not measured speedups, and overlap with other optimizations. Even eliminating all
integer-solving time saves only 3.91 minutes. Do not advertise seconds-per-day or
a fixed month runtime before measuring the full pipeline with equal cache state.

Acceptance fixtures: frozen weekday, weekend, mixed-pool and difficult boundary
days; q10/q50/q90 including unequal populations; closure and baseline; cache hit,
miss, corrupted entry, changed input and interruption. Require identical selected
route/departure records, canonical agents, populations, sensor-quarter counts,
structural flags, solver objective and deterministic ranking. Require unchanged
validation seeds, tolerances and health gates. Compare peak memory and total wall
time in alternating before/after runs. Any equal-objective but different route
solution is a result change under this performance-only scope.

Do not start with date-only reuse, fewer validation seeds, relaxed solver accuracy,
shorter simulation horizons, extra nested workers or altered traffic models.
Composition-independent day calibration is a separate model experiment; the
existing policy experiment did not establish exact equivalence.

Primary sources checked: [Python profiling](https://docs.python.org/3/library/profile.html),
[ElementTree](https://docs.python.org/3/library/xml.etree.elementtree.html),
[SciPy sparse](https://docs.scipy.org/doc/scipy/reference/sparse.html),
[Python executors](https://docs.python.org/3/library/concurrent.futures.html),
[SUMO save/load](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html),
[libsumo](https://sumo.dlr.de/docs/Libsumo.html).
The documentation establishes API properties, not speedups in this repository.

**Date:** 2026-08-24 (historical plan consolidated 2026-07-18; current status
re-verified against the branch, active artifacts and validation records)
**Status:** Canonical strategic and historical improvement record. The
"Current verified status" block immediately below is the only current status
summary; later dated checkpoints describe their stated date unless explicitly
carried into that block.
**Structural authority:** `ARCHITECTURE.md` remains the source of truth for
the six-stage pipeline and fixed contracts. This is the only improvement,
review, performance, simulation, closure, signal and sensor-growth plan.
Historical Sol/Luna task names and exact-approval wording below describe the
process used at the time; current collaboration follows the flexible,
model-independent protocol in `AGENTS.md`.

## Current reassessment — completed June search, 2026-09-10

This block supersedes the priority order in the September 8 reassessment and
qualifies the timing targets in
[the detailed research report](validation/codebase_simulation_improvement_research_20260910.md).

Verified run: `ui-monthly-g1f50b`, 18:29:15–19:54:32 local time;
active time 5113.831 s (85 min 14 s), versus 2966.486 s in `ui-monthly-97e768`.
These are different date ranges and cache conditions, not a controlled
before/after benchmark. The new run compared 29 schedules, SUMO-checked two,
and selected June 25–27, 00:00–24:00 daily. Deterministic q50 detour cost is
32.0614 vehicle-hours; it is not measured SUMO delay. All 48 recorded launches
succeeded without timeout. Both finalists are eligible with no hard failures.
The policy remains provisional and global-best claims remain disallowed.
`precision_met=false` describes the separate stochastic time-loss estimate;
it is not itself a failure of deterministic q50 ranking.

Verification: all 15 manifest artifact hashes match. Production
`evidence_from_dict` resolves all four pilot/finalist records successfully,
including 90 canonical observation references (some shared), nested routing
provenance and transformed-route/access-impact artifacts. Focused checks:
60 passed in 1.62 s across automatic passage, closure time origin, monthly
progress contract and executable web harnesses. Local `/api/ping` returns ok.
Desktop browser review confirms result dates, 29 computed / 2 verified labels,
provisional-policy disclosure and Escape return; no captured console warnings
or errors. No full suite, new simulation, mobile review or field validation.

Performance observation from artifacts created during the job: 30 three-day
demand archives, 25 with nonzero dynamic_passage timings; the backend names
31 calendar-day units. There are 49 automatic-passage timing records totaling
2722.725 s (45 min 23 s), median 55.792 s, range 24.637–93.034 s.
The build-level dynamic_passage sum is 2722.755 s, consistent with these
records. PFE wrapper time totals 3268.797 s and INCLUDES dynamic passage.
The remaining 1845 s of overall active time is outside that wrapper and has
not been attributed to specific work. Worker-seconds cannot be subtracted
from wall time. This sample does not prove a per-day slowdown: its median
passage time remains within the previous 53–59 s examples.

Day-library measurement, read-only, 2026-09-10 (this reassessment; nothing was
rebuilt, moved or deleted). Every `runs/demand-days/*/*/manifest.json` written
inside the job's own 18:29:15–19:54:32 window was read: 98 entries over 30
distinct calendar dates, of which 49 are three-variant calibrations
(`edge_shares`, `_q10`, `_q90`) and 49 are the q50-only subset entries the
library stores deliberately. Nineteen dates hold two three-variant entries and
eleven hold one, which is exactly the 49. Every one of those 19 repeats differs
in `pool_composition` — with `inputs.candidate_pool`, `candidate_metadata`
and `catalog_keys` differing as a consequence, because the candidate pool is
generated per composition — and no repeat has any other cause. No
(date, composition) pair carries more than one candidate-pool hash, so no
nondeterminism was observed. The gap between 49 and the 31 named calendar-day
units is therefore accounted for; what is still unmeasured is how much of it is
avoidable, since a composition genuinely changes the PFE variable set. A second
miss class is already visible in the store: 2027-07-02 had a `('weekday',)`
entry from 2026-09-05 and was calibrated again on 2026-09-10 because 16 source
   files in `source_hashes` had changed.

Revised implementation order. Item 1 was made the top priority by the project
owner on 2026-09-10; the remaining items keep their previous relative order.

1. **Explain repeated day calibrations, then test safe context-independent
   reuse.** First add a read-only explainer and named cache outcomes. Then use
   that evidence to compare the current context-aware pool with a canonical
   pool and a day-type-local pool. A reuse policy is promoted only if it is
   output-equivalent; a policy that changes calibrated demand is a model change
   for item 7, not a performance optimisation. The complete implementation
   contract is directly below this priority list.
2. **Correct measurement:** add full-job and hierarchical stage timers so the
   unattributed 1845 s becomes attributable without double counting. Inclusive
   and exclusive times per stage, so an optimisation cannot merely move time
   between records.
3. **Bounded variant parallelism:** benchmark 1/2/3 workers on the same frozen
   day, seed set and total SUMO budget, with exact output, memory and failure
   checks. The old 35-second target is an experiment target, not a forecast.
4. **Build I/O and metadata:** compact new JSON first with identical parsed
   content and build identity; profile assembly, hashing, copies and large
   report loading. Treat duplicate-contract removal as a separate migration.
5. **Correctness and status clarity:** repair the previously reproduced B1/B3
   validator crashes; represent terminal completion and computed/verified/
   skipped counts explicitly. The terminal manifest still contains
   completed=0,total=null, though the result UI correctly shows 29/2.
6. **Truthful UI:** fix full-day button help text currently rendered as
   00:00–00:00. Replace the map legend's proximity-based “Säker” wording with
   measurement-support language; distance from a sensor is not demonstrated
   predictive accuracy. Consider “Lägst beräknad q50-kostnad” instead of the
   broad “Bästa period”, and show stochastic uncertainty separately.
7. **Simulation fidelity:** refresh spatial/temporal holdout; validate physical
   sensor positions before E1 adoption; obtain speed/queue/signal evidence
   before supply calibration. Holdout evaluates improvement; it does not
   itself improve the simulated traffic. Successful closure runs do not
   establish unseen-sensor accuracy or exact field passage matching.
8. **Persistent SUMO last:** directly measure process startup and test
   `simulation.loadState` only if the measured benefit warrants maintenance
   and equivalence risk. SUMO documents network reuse, but also RNG and
   future-vehicle limitations:
   https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html .
   Earlier 3–6 percent savings and 5/15 percent cutoffs were exploratory,
   not experimentally established release thresholds.

### Item 1 implementation contract — explain and safely reduce day rebuilds

**Outcome.** Every day-library lookup must have a machine-readable result, and
the completed job must explain how many days were hits, absent, rejected as
corrupt or separated by a semantic identity field. After that instrumentation
exists, one bounded experiment determines whether calendar context can be
removed without changing calibrated output. The diagnostic work is useful by
itself; fewer calibrations are conditional on equivalence evidence.

**Measured baseline.** Use `ui-monthly-g1f50b` as the observational baseline:
49 full three-variant calibrations for 30 distinct dates, including 19 dates
calibrated under two `pool_composition` values. The 49 q50-subset entries are
intentional aliases and do not count as extra solves. The theoretical ceiling
for a stable one-entry-per-date policy in this sample is therefore 19 fewer
full calibrations, or 38.8 percent. The recorded median of 55.792 seconds makes
roughly 18 minutes a useful upper-bound estimate, not a promised wall-time
improvement. `ui-monthly-97e768` is not a control because its dates and cache
state differ.

**Correctness gate.** Preserve q10/q50/q90 populations, integer sensor targets,
route and agent records, departures, route provenance, dynamic-passage fit,
structure guards and publication health. Compare uncompressed route bytes and
canonical JSON, since gzip container bytes may contain non-semantic metadata.
Any candidate whose calibrated artifacts differ from the current policy fails
the performance-only gate. It may only continue later as a separately versioned
simulation-quality experiment with held-out and SUMO evidence.

**Search budget and stop rule.** Do not run a monthly search while developing
this item. Reuse archived baseline entries, allow at most eight new cold
single-day calibrations, and stop after 30 minutes of calibration wall time or
on the first provenance, exactness or health regression. A full controlled
month replay is allowed only after the isolated equivalence gate passes.

#### Stage 1 — read-only explanation with zero cache invalidation

Create `tools/explain_day_reuse.py` and
`tests/test_explain_day_reuse.py`. Neither path is in
`demand_source_paths`, so this stage must leave all existing `DayIdentity`
keys usable. The tool has no third-party dependency and must never modify,
move or prune `runs/demand-days`.

The CLI contract is:

```text
python3 tools/explain_day_reuse.py \
  --root runs/demand-days \
  --since 2026-09-10T18:29:15+02:00 \
  --until 2026-09-10T19:54:32+02:00 \
  --output validation/day_reuse_explanation_v1.json
```

Its deterministic schema is `day_reuse_explanation_v1` with
`release_evidence: false`, the requested filters, a summary, and entries grouped
by date. Within each date, compare full three-variant entries with one another;
link each q50 alias to the full entry that matches after ignoring only
`inputs.constraints` and `inputs.variants`, with key as deterministic
tie-breaker. This prevents the intentional aliases from multiplying the
repeated-calibration count. Each comparison reports sorted dotted paths,
expanding `inputs.*` and `source_hashes.*`, plus exactly one cause:
`variant_subset`, `source_change`, `pool_composition`, `candidate_drift` or
`other`. Cause precedence is source change, variant subset, pool composition,
candidate drift, other. The report also compares stored `source_hashes` with
the current source inventory and counts reusable entries and dates. Unreadable
manifests are counted with their path and error class; they must not abort the
scan or be silently omitted.

Stage 1 tests must cover deterministic ordering, nested-field diffs, cause
precedence, q50 aliases excluded from full-calibration counts, unreadable and
incomplete manifests, time-bound inclusivity and current-source matching. The
real-data acceptance check must reproduce 49 full calibrations, 30 dates,
19 repeated dates and 19 `pool_composition` causes for the recorded job window.
It must also prove that the set of existing manifest paths and identity keys is
unchanged before and after the command.

**Stage 1 status: DONE, 2026-09-10.** `tools/explain_day_reuse.py` and
`tests/test_explain_day_reuse.py` exist; 32 direct tests and 263 selected Stage 1
and related tests pass, including the real-data
acceptance check, which reproduces exactly 49 full calibrations, 30 dates,
49 linked q50 aliases with 0 unlinked, 19 repeated dates and
`{"pool_composition": 19}`. A before/after snapshot of all 1 347 manifest
paths, mtimes, sizes and SHA-256 digests under `runs/demand-days` is identical
across the command, and 98 of the window's 98 entries are still reusable
against the current 40-file inventory. The CLI rejects an output path inside
the library root, including paths resolved through an existing symlink. The
artifact is
`validation/day_reuse_explanation_v1.json` (`release_evidence: false`), and the
report carries no wall clock, so two runs over one tree are byte-identical.
One real defect was found and fixed by a subprocess test: run as a script,
`sys.path[0]` is `tools/`, so the source-inventory comparison silently reported
`ModuleNotFoundError` and "unknown" reusability; the inventory is now anchored
to the repository root the way `build_sumo_demand._source_files` already
requires.

**New finding from the same tool, whole library, no time filter** (reproduce
with `python3 tools/explain_day_reuse.py --output <scratch>.json`): across all
223 dates the store holds 660 full calibrations and 441 repeats. Each later
entry is compared with its closest previously written sibling, giving
`source_change` 241, `pool_composition` 174 and `candidate_drift` 26, plus 27
q50 aliases whose full partner is no longer stored. These are deterministic
identity-difference classifications, not logged historical cache-miss causes;
Stage 2 is required for causal counts. Source drift is the most frequent
classification, but the report cannot establish that code churn caused 241
rebuilds. This does not change Stage 3's per-job ceiling of 19 of 49.

#### Stage 2 — structured lookup diagnostics in the build

This stage changes `demand/day_library.py` and `build_sumo_demand.py`; both are
among the currently fingerprinted sources, so the edit intentionally makes old
entries unreachable by new identities. Do not weaken the source inventory to
avoid that boundary. New entries are created lazily for dates requested by
real work; no multi-date cache prefill is required.

Add these compatible APIs to `demand/day_library.py`:

```python
class LookupReason(str, Enum): ...
class IdentityCause(str, Enum): ...

@dataclass(frozen=True)
class DayLookup:
    manifest: dict[str, Any] | None
    outcome: Literal["hit", "miss", "rejected"]
    reason: LookupReason
    expected_key: str
    compared_key: str | None = None
    differing_fields: tuple[str, ...] = ()
    identity_cause: IdentityCause | None = None

def lookup(self, identity: DayIdentity) -> DayLookup: ...
def get(self, identity: DayIdentity) -> dict[str, Any] | None:
    return self.lookup(identity).manifest
```

Use distinct reasons for `hit`, `entry_absent`, `manifest_unreadable`, schema,
kind, key and identity mismatch, invalid artifact record, missing artifact,
digest mismatch, size mismatch and I/O error. If the exact key is absent,
compare siblings under the same date, choose the nearest identity by number of
differing leaf paths with key as deterministic tie-breaker, and report that
comparison separately. Classify its fields with the same cause precedence as
Stage 1. A sibling is evidence about why identities differ; it must never be
returned as a cache hit.

`build_sumo_demand.py` records one decision per requested day in
`meta["day_library_diagnostics"]`: date, expected key, outcome, reason,
compared key, differing fields, identity cause and lookup duration. It prints
the same named outcome for hits and misses. Add `day_library_diagnostics` to
the explicit build-fingerprint exclusion beside `timings_s` and
`pfe_timing_s`; a regression test must show that different diagnostic timings
and miss reasons do not change `build_id`. Existing `get()` callers and
fail-closed behavior remain unchanged.

Stage 2 tests extend `tests/test_day_library.py` for every reason, nearest-
sibling tie-breaking and the compatibility wrapper. Builder tests cover one
hit, one absent entry and one rejected corrupt entry, plus diagnostic exclusion
from the build fingerprint. Run:

```text
python3 -m pytest -q tests/test_day_library.py \
  tests/test_explain_day_reuse.py tests/test_build_sumo_demand.py \
  -k 'day_library or day_reuse or build_fingerprint'
```

**Stage 2 status: DONE locally, 2026-09-11.** `DayLibrary.lookup()` preserves
the old fail-closed `get()` API while returning named hit, absent and rejected
outcomes, the nearest valid sibling comparison and one identity-cause class.
Artifact records now reject missing or malformed SHA/size fields before byte
verification. `build_sumo_demand.py` records exactly one lookup decision per
requested library day, including duration, and refuses to publish metadata if
the decision dates do not reconcile with the request. Direct/sub-day builds
must have an empty diagnostic list. The entire diagnostic block is excluded
from the semantic fingerprint, and a regression test proves that changing
reason and duration leaves `build_id` unchanged. The prescribed Stage 2
selection passes 88 tests; the combined Stage 1–3 and builder set passes 282.

The current adopted route-catalog record and its stored artifacts still verify,
but an implicit build would not select it: the expected weekday and weekend
keys now differ solely at `source_files.build_sumo_demand`. This contradicts
the remote-session assumption that Stage 2 cannot affect catalog selection;
`build_sumo_demand` is explicitly one of `CATALOG_SOURCE_LABELS`. Finish Stage
4 before one catalog qualification/adoption pass. The owner declined a
multi-date demand re-warm on 2026-09-12; keep day creation on demand. Running a
demand build before catalog adoption would use the slower legacy candidate
builder.

#### Stage 3 — bounded reuse-policy experiment

Keep the current `pool_composition` identity as control. In an isolated harness,
test two policies without writing production paths:

| Variant | Candidate choice set | Expected contribution | Main risk |
|---|---|---|---|
| `context_control` | Current `window_pool_composition` | Reproduces both archived controls | Retains duplicate date calibrations |
| `canonical_union` | Complete ordered `POOL_KEYS` for every date | One stable identity per date with a common choice set | Adds geometries and structure shares to pure windows |
| `day_type_local` | Only the date's own `pool_key` | One stable identity and the smallest solve | Removes geometries available in mixed windows |

Use 2027-06-03 as the weekday and 2027-06-25 as the weekend/holiday case; both
already have pure and mixed-composition controls in the measured artifact set.
For each policy, build the day once and compare it against both archived
context-specific controls. Record candidate count and semantic hash, PFE
shape-variable count, full/dynamic-passage wall time, peak RSS and all
correctness-gate fields. The harness writes a diagnostic artifact with exact
input and source hashes and `release_evidence: false`.

Promotion requires one policy to match both contexts exactly for both dates,
then pass a small overlapping-window integration test and a controlled replay
whose primary metric is full calibrations per distinct date. The target for the
observed month is at most 30 full calibrations instead of 49, with no new cache
collision, timeout or memory regression. If neither policy is equivalent,
retain the current composition-aware identity. Stage 1 and 2 still ship because
they explain legitimate misses; item 1 then makes no speed claim.

Any accepted policy gets an explicit `day_pool_policy` version in
`DayIdentity`. Write new entries beside old ones and keep rollback as selecting
the previous policy version; never overwrite or delete the prior library.

**Stage 3 status: DONE, 2026-09-11 — retain the composition-aware identity.**
The reviewed harness is `tools/experiment_day_reuse_policy.py`, with 77 direct
tests in `tests/test_experiment_day_reuse_policy.py`. Before spending the cold
build budget it verifies the two archived controls against each other. For both
2027-06-03 and 2027-06-25, pure and mixed controls differ with no source or
non-policy input confound in exact uncompressed route bytes, route/departure
records, canonical agent data, population, semantic fit and
`passage_calibration` evidence. One candidate built once cannot be equal to two
unequal controls, so equality transitivity eliminates both `canonical_union`
and `day_type_local` under the frozen performance-only promotion rule. The
diagnostic therefore used 0 of 8 cold calibrations and 0 of 1,800 calibration
seconds. `validation/day_reuse_policy_experiment_v1.json` records the bound
control manifests, source hashes and comparison digests with
`release_evidence: false`; it activates nothing. A model-changing policy could
only be considered later under separate held-out and SUMO evidence.

Review also corrected the initial remote implementation before it was run: it
looked for `dynamic_passage` although real fit files use
`passage_calibration`, made that missing field optional, calculated but did not
compare the full uncompressed route hash, compared candidate count through the
whole provenance object despite candidate count being the variable under test,
and could remove a pre-existing caller workspace. Stored artifacts are now
verified against manifest size and digest, all non-policy identity inputs are
confound checks, dynamic-passage wall time is sourced from build metadata, and
only a newly created scratch workspace can be cleaned up. The direct suite and
the combined Stage 1/Stage 2/Stage 3/day-builder selection pass: 272 tests.

#### Stage 4 — job-level accounting and completion

The monthly job aggregates each build's decisions into its progress/result
artifact: requested days, hits, misses, rejected entries, full calibrations,
q50 aliases, counts by lookup reason and counts by identity cause. Counts must
reconcile exactly; an unknown or missing decision makes the diagnostic summary
incomplete rather than guessing.

**Stage 4 status: DONE locally, 2026-09-11.** Each successful day decision now
records whether a full calibration ran and whether its q50 alias was created,
already present or not applicable. Producer and archive consumer use the same
strict diagnostic validator. At archive level, `rejected` is retained as a
named subset of operational misses, so `hits + misses == requested_days`,
`full_calibrations == misses` and `rejected_entries <= misses`. The monthly
resolver sums complete archive records by outcome, lookup reason, identity
cause and alias status. Any old, missing, malformed or non-reconciling record
produces `status: incomplete` with no inferred hit/miss totals. The aggregate
is written into backend provenance, the live `prepare_backend`/final progress
detail and `result.json`. Diagnostics remain excluded from `build_id`.

Validation after implementation: all 401 tests in the Stage 1-4, builder,
archive, monthly-search and progress-contract files pass; the focused Stage 4
integration set passes 110 tests. `py_compile` and scoped `git diff --check`
also pass. No SUMO build, catalog adoption or search was run. The completed
Stage 1-4 implementation and its required passage-calibration dependencies were
subsequently delivered in commit `5b9a3e9`.

Item 1 is complete when the focused tests pass, the offline report reproduces
the frozen baseline, diagnostics do not affect `build_id`, the old cache API
still fails closed, and the policy experiment ends in one of two explicit
results: an equivalent policy promoted with a controlled speed measurement, or
the current policy retained with the repeats classified as semantically
required. No catalog adoption, release promotion or global-best claim follows
from this work.

No historical result was rewritten and no release claim follows from this
implementation.

## Research reassessment after user challenge — 2026-09-08

This reassessment supersedes the priority order immediately below, not its
measurements. Rank deficiency establishes ambiguity, not that OD-group
regularization is the best remedy. The previous recommendation was too strong.

Code evidence: pfe.py accumulates every route edge in achieved[edge][i], then
assigns departures inside quarter i (around lines 3123–3131). This proves route
membership by departure quarter, not actual sensor-crossing time. SUMO output
is separately checked, but the inspected PFE does not jointly assign flow
across departure and arrival quarters. Longer trips/more widely spaced sensors
make this a relevant hypothesis to test, not an already measured dominant error.
The simulate feedback branch also stops on stable PFE GEH before another
simulation (build_sumo_demand.py around 1846), which cannot establish dynamic
travel-time or route-choice convergence.

Revised priorities:
1. Audit departure-to-sensor travel-time distributions and cross-quarter
   passage fractions. Prototype a sparse dynamic assignment mapping from
   route/departure bin to sensor/passage bin on isolated historical evidence.
   Test delayed demand recovery on synthetic known demand, then real held dates.
   Keep current exact publication contracts; dynamic passage calibration must
   explicitly reconcile them and must not silently replace route-count targets.
2. Calibrate network supply before allowing demand to compensate for wrong
   capacity: verify bottleneck discharge, speeds, travel times and actual signal
   plans. run_scenario.py intentionally uses limited meso junction control due
   to guessed signal timings; blindly enabling full control is not a remedy.
3. Combine counts with independently measured travel times/speeds when available.
   Speed alone is not informative about demand in every traffic regime.
4. Evaluate simulator-in-the-loop demand calibration (Cadyts as an established
   reference) and a physics-based surrogate to limit SUMO calls. Do not introduce
   another optimizer before a correct measurement mapping and cost baseline.
5. Compare representative actual days/conditions, not only mean demand or seed
   variation. Keep grouped unseen-station/date validation as an evidence method,
   not a mechanism that by itself improves model predictions.

Sources and limits:
- https://ops.fhwa.dot.gov/publications/fhwahop18036/chapter5.htm : calibrate
  time-dynamic performance using travel-time/speed and bottleneck measures;
  representative real days; distinguish travel-condition variability from seeds.
- https://pubsonline.informs.org/doi/10.1287/trsc.1100.0367 : Bayesian demand
  calibration using time-dependent counts; supports dynamic calibration, not
  guaranteed accuracy for this project's exact-constraint solver.
- https://eclipse.dev/sumo/docs/Contributed/Cadyts.html : SUMO integration exists;
  compatibility and maintenance suitability need testing before adoption.
- https://arxiv.org/html/2501.04783v1 : preprint, metropolitan highway case
  studies using path travel times; cross-network algorithm evidence does not
  prove unseen-sensor generalization for Gothenburg urban roads.
- https://arxiv.org/html/2412.14089v1 : urban speed-calibration metamodel and
  out-of-sample segment experiments; experimental data/setup limits apply.

Research-only update: no source changes, simulations, retraining or adoption.

Departure-to-passage checkpoint (2026-09-08): the experimental vehroute
mapping now follows SUMO `entered` semantics and excludes vehicles emitted on
the target edge. Recomputed saved-baseline vectors match edgeData exactly for
all 7 target edges in every one of 96 quarters. Cross-quarter fractions remain
material at 14.2–36.5%. This validates the timing extraction on old baseline
evidence; it does not establish better held-date prediction, unseen-sensor
generalization or a production policy change.

## Consecutive-day HiGHS and preparation telemetry — 2026-09-09

A live sample of ui-monthly-198410y located the next-day stall in
HighsTaskExecutor::shutdown, not traffic optimization. Passage MILP had
initialized a native multithread scheduler in the parent; later PFE fork
workers inherited it. Passage now passes threads=1, matching PFE solver calls.
A fresh-interpreter test repeats parent-fit→forked-solve twice with a bounded
child join; failed children are terminated. This changes solver execution only.

Blocking backend preparation now persists active time and timestamp every5s.
The heartbeat explicitly denotes liveness, not completed candidate work. The
UI removes the unsupported per-day estimate and preparation candidate counter,
and shows age of the most recent status update. Old processes must restart to
load the correction; no live process is silently patched.

## Monthly stress-arm regression — 2026-09-09

Corrected the earlier assumption that all PFE direction arms have identical
vehicle totals. Passage policyv2conserves each arm independently; it no longer
adds q50 departure equalities that contradict q10/q90 OD/purpose conservation.
The actual failed2027-06-23spec now completes all3arms in isolation, with
20638/20487/20599vehicles and paired candidate errors6/18/18. All retained
sensor, structural and health checks remain. Evidence:
`validation/monthly_stress_fix_20260909.json`.160focused tests pass; the full
monthly search and full repository suite were not run. Overall validation
still warns on structure/purposes and has missing sections.

Monthly progress now records failed execution separately from workspace
resumability. Dead-owner saved errors are visible in the UI, including older
manifests; completed candidate evidence remains reusable.

## Automatic passage default — 2026-09-08

Implemented after user request. Fresh per-day measurements, retained PFE
bounds, paired-seed regression checks, direction-arm population conservation,
rollback and source/runtime-bound day-cache reuse are now in the ordinary PFE
build path. Forecast2027-08-13 improves paired absolute error4673→16; native
closure reroutes3646 of20059vehicles with no forbidden entries or health flags.
The initial cold passage calibration took60.5s; exact solver simplification and
bounded measurement concurrency now reduce it to23.4s. Total cold day build
75.7→31.9s, repeated31.6s, with unchanged paired error16 and all9measurements.
Cached-day PFE assembly previously measured0.7s, with no new SUMO.
Current performance/native evidence: `validation/passage_speed_20260908.json`.
Both catalogs now use explicit artifact-equivalence renewal of the original
full qualification:115.4s including regeneration,3.5s for the default cached
command,2.1s with an existing build report. Changed generator/input/output
contracts still require full qualification; this is not a new campaign claim.
See `validation/automatic_passage_20260908.json` for source-bound evidence.

Next: independent historical and sensor-layout validation, preexisting slow
subwindow PFE diagnosis. Catalog renewal is implemented for the narrow
identical-artifact case; other changes require full requalification. No full-suite or global/generalization claim. The records below
explain the earlier diagnostic and one-day activation stages.

## Local dynamic passage activation — 2026-09-08

The user subsequently requested activation and a closure test. The active
2027-08-12 q50 build is `e775b28187ae46f52cf5`, preserving3293 structural
constraints without adding relaxations. Three native closure seeds reroute3686
vehicles with zero closed-edge entries, drops or health failures. Baseline raw
exactness is658/672 ensemble cells. Existing publication criteria pass unchanged;
exactness is diagnostic, correcting the earlier publication-gate wording below.
Evidence: `runs/dynamic-activation-final-20260908/activation.json`.
Automatic use on other dates, historical holdouts and sensor generalization
remain future work. The comparison below describes the earlier isolated stage.

## Dynamic passage implementation and real SUMO comparison — 2026-09-08

Implemented `traffic_sim/experimental/dynamic_assignment.py` and
`tools/trial_dynamic_passage.py`, with59 focused passing tests across new and
existing timing paths. The operator uses physical route entry times separately
for every sensor, including cross-quarter trips, repeated visits and boundary
mass. Joint integer flows preserve364 OD/purpose totals on the active fixture;
L1 prior deviation, finite option capacities and a smaller-shift preference
limit arbitrary changes. This is a baseline regularizer, not a substitute for
all production entropy/structure contracts.

The bounded comparison is saved in
`validation/dynamic_passage_calibration_20260908.json`. Input: forecast2027-08-12,
19697 trips. The source operator reconstructs2016/2016 raw learning cells.
Departures are offered within±900s at300s increments with a declared60s
uncertainty envelope; this support needs independent behavioral validation.
The final candidate selects3222 nonzero-shift anonymous flow alternatives.
Ordinary SUMO randomness on unseen seeds4000/4001/4002 yields670/666/669 exact
cells of672, absolute errors2/8/3; the identical-input baseline gives
1557/1510/1549. Summed absolute error falls4616→13, approximately99.7%.
Measured solve14.878s; solve plus six SUMO runs29.427s before artifact hashing.
This is a bounded local improvement, not historical-data validation, a runtime
speedup claim or a successful exactness gate. Status remains `sumo_mismatch`.

Next: route/time-specific travel-time feedback; production entropy/structural
constraints and coherent provenance in continuous/integer/materialization
stages; withheld historical dates and sensors. Preserve the exact gate and do
not select new parameters using the final validation seeds. No production
activation, full warming or catalog rebuild occurred.

## Revised passage model after naturalness objection — 2026-09-08

The user rejected the post-picker reassignment approach. It remains diagnostic.
`validation/quarter_route_assignment_20260908.json` records exact integer
672/672 cells for three learning and three unseen profile arms, preserving all
19,697 routes and departure-quarter counts, but moving 19,622 departures with
253 s median and 892.4 s maximum absolute shift. Exactness does not establish
naturalness. A minimum-cost alternative reached exact learning counts but
failed unseen profiles (640–644/672 cells); retain that counterexample.

Research supports dynamic OD/path-flow estimation coupled to simulated network
loading, rather than treating the source quarter as the sensor quarter:
https://arxiv.org/abs/2202.00099 (SUMO study on an artificial network), and
https://transp-or.epfl.ch/documents/technicalReports/FloeBierNage08.pdf
(Bayesian calibration framework). Our proposed adaptation is a sparse operator
indexed by route, departure period, sensor and passage period. Update its
travel-time information from simulation, retain OD/purpose priors and penalize
unsupported temporal oscillation. Individual trips may cross different sensors
in different quarters. Exact measured constraints remain; deterministic fit on
one simulation seed is not sufficient validation. Evaluate withheld dates and
sensors and sensitivity to congestion, with integer publication using the same
passage semantics as the continuous fit.

Measurement location needs explicit review: edgeData entered records entry to
the street; a physical counter can lie farther along it. SUMO E1 detectors have
lane position and 900 s aggregation, but switching measurement semantics needs
verified mapping and runtime compatibility, not an assumed midpoint:
https://sumo.dlr.de/docs/Simulation/Output/Induction_Loops_Detectors_(E1).html .
No production solver, sensor contract or catalog was changed in this pass.

## Passage integration experiment — 2026-09-08

The user authorized production integration and a catalog rebuild if needed.
Further code inspection found an existing offline monotone departure correction
in `tools/departure_reconciliation.py`. It had previously failed departure
spread checks. Retesting requires actual SUMO output; the 672 exact reconstructed
baseline cells do not establish predictive improvement.

Research: [SUMO Cadyts](https://eclipse.dev/sumo/docs/Contributed/Cadyts.html)
uses simulated route timing and count measurements. Its SUMO coupling selects
trips from alternatives and can distort OD structure when demand scaling is
excessive. [SUMO routeSampler](https://sumo.dlr.de/docs/Tools/Turns.html)
distinguishes entered counts from departures. Neither source establishes that
an aggregate kernel from one old day remains valid after route mix or congestion
changes. Our inference: prefer route/time-specific evidence and validate the
resulting simulation, retaining population and provenance constraints.

Implemented a bounded trial runner and repaired the existing correction:
retain each source departure quarter (so all existing route-count, population,
purpose and bound margins remain valid), retain at least half each adjacent
source departure gap, reject first-edge sensor emissions, invalid/backward times,
and missing/duplicate/incomplete edgeData intervals. Existing exact-output,
health and aggregate dispersion gates remain required. The trial copies input
files and retains three-seed raw evidence and source/input/binary hashes.

Fresh result: `validation/passage_reconciliation_trial_20260908.json`.
19,697 vehicles; seeds 1000/1001/1002; 6.326 s including three learning runs.
The schedule refused before verification: pfe987 needs at least 24342.5 s but
subsequent constraints allow at most 24330.4 s (12.1 s conflict). All original
inputs remain unchanged. This rejects the proposed monotone treatment under
the declared 60 s guard and spacing constraints. It does not prove that other
joint route/time calibration is infeasible. No production integration or new
predictive-accuracy claim is justified by this experiment.

Run again with a fresh output directory:
`python3 -m tools.trial_passage_reconciliation --demand-dir sumo --out runs/<new-trial>`.
No catalog sources or route policy were changed, so no catalog rebuild is
required. Next work is a joint route/departure assignment contract, including
integer publication, driver identity and boundary handling, evaluated on frozen
held dates before adoption. Do not manufacture exact counts through convoys.

## Generalization research — 2026-09-08

Goal: improve prediction on unseen sensors and dates while preserving exact
active sensor constraints. No new production model or SUMO run in this pass.

Fresh structural diagnostic:
`validation/sensor_generalization_geometry_20260908.json`. On 416 unique
geometries, every one of six station rows increases rank when added to the
other registered directed sensor rows plus total flow. Exclusive support is
50 geometries per station except station 107 (100). This tests a linear
incidence model with fixed total, not feasible bounds or predictive accuracy.
It identifies ambiguity in measured margins; it does not prove all such
reallocations remain feasible under the complete production constraints.

Prioritized experiments (hypotheses, not adopted improvements):

1. Separate model selection from final testing. Outer holdouts must cover
   entire stations/corridors and later dates; choose regularization strength
   only within the remaining data. Remove held counts from all upstream
   priors, aggregate totals, caches and candidate selection. Existing fixed-pool
   LOSO tests missing counts at known locations; test new-location onboarding
   separately by withholding that station from candidate construction too.
   Keep both protocols: they answer different questions.
2. Group regularization by origin/destination zones and purpose. Prefer a few
   shared structural parameters over sensor-specific route coefficients.
   Group definitions derive from geography/land use, never held traffic.
   Apply soft penalties only inside the existing feasible constraints; compare
   group prior sensitivity, not just a single assumed prior. Avoid making
   arbitrary candidate multiplicity into a prior on real trip demand.
3. Sensor-independent structural route support, plus explicitly attributed
   exact-fit support where required. Test whether adding a station changes
   predictions on untouched roads excessively. Preserve fastest-route and
   avoidance proofs; more cross-sensor routes must be geographically justified,
   never forced merely to improve a rank diagnostic. Keep current production
   support policy until a separately versioned treatment passes evidence gates.
4. Length-weighted path size remains a small controlled treatment. Since the
   current penalty counts overlap across the entire pool, test sensitivity to
   additional geographically unrelated OD alternatives as well as edge splitting
   and purpose duplicates. Do not claim the global heuristic is already a
   calibrated within-OD discrete-choice model or add sampling corrections without
   a defined route-sampling probability model.

Bounded comparison order: current baseline; length weighting only; baseline
plus group regularization; combination only after independent effects are
understood. Start on separately frozen historical inputs. Reserve a genuinely
unseen station/date cohort for final evaluation: repeatedly selecting winners
on the same six LOSO folds can overfit those folds. With few independent
stations, state uncertainty and avoid universal generalization claims.

Acceptance protocol: bind hashes for network, registry, raw observations,
candidates/sidecars, fold-specific priors and code; pair seeds and simulated
windows. Preserve exact active margins, route proofs and publication health.
Report station-level hourly GEH, absolute count error, multiplicative daily
error where the observed total is positive, worst-station degradation, and
interval/seed stability. Evaluate station/day blocks rather than treating
correlated quarters as independent samples. Stress missing stations and
registry-order changes; measure runtime/memory as sensor count grows. A lower
training error alone is never adoption evidence. Add sensors incrementally
and assess remaining held stations with a frozen model-selection protocol.

Sources: [grouped validation](https://scikit-learn.org/stable/modules/cross_validation.html),
[nested model selection](https://scikit-learn.org/stable/auto_examples/model_selection/plot_nested_cross_validation_iris.html),
[route-alternative sampling](https://transp-or.epfl.ch/documents/technicalReports/FrejBier07.pdf).
These support evaluation and route-choice principles; the proposed group
regularization and pool changes still require project-specific experiments.

## Research implementation checkpoint — 2026-09-08

Verified correctness repairs: hourly GEH interpretation, chronological paired
forecast evaluation and spatial LOSO freshness checks. Existing published
reports and trained models were not rebuilt. Prior UI work remains local.

Point 6: length-weighted overlap is implemented only as a diagnostic. Run:

```sh
python3 -m traffic_sim.demand.route_regularization --candidates sumo/candidates.rou.xml --network sumo/net.net.xml
```

`validation/route_regularization_geometry_20260908.json` binds the measured
416 geometries and 92 changed weights to XML/source hashes. Tests demonstrate
segmentation invariance, not predictive superiority. Before adoption, freeze
historical inputs, run paired LOSO and SUMO with identical seeds and exact
sensor/route constraints, and compare held-out station error, GEH and route
composition. OD/purpose regularization remains a separate hypothesis.
No production route-policy change has been made. Junction realism requires
travel-time/queue observations; congestion convergence, confidence calibration
and broad modularization require separate evidence and scope.

Research basis: [DfT TAG M3.1](https://assets.publishing.service.gov.uk/media/6a033d074fb0713aa63ea802/tag-m3-1-highway-assignment-modelling.pdf),
[rolling-origin evaluation](https://otexts.com/fpp3/tscv.html),
[preprocessing leakage](https://scikit-learn.org/stable/common_pitfalls.html),
and [route-choice modelling](https://transp-or.epfl.ch/documents/technicalReports/KazBierFloe_2015.pdf).

## Current verified status — 2026-08-24

- FASTEST-SENSOR-ROUTE LOSO DIAGNOSTIC (added 2026-09-01). The corrected
  demand contract now emits only deterministic global fastest OD routes for
  sensor-attributed candidates and requires a finite strictly slower
  sensor-avoiding route. The 50-route-floor build contains 539 candidates and
  passed exact active counts. Its six-fold
  `loso_pfe_meso_v11_observability_gate` preserved every active fold margin,
  but all held stations remained structurally underidentified. The
  preregistered same-protocol floors 25/50/100/200/500 produced median daily
  multiplicative errors 1.642x/1.560x/1.946x/2.293x/2.425x and mean GEH<5
  55.8%/57.3%/49.6%/43.3%/37.0%. Floor 50 therefore wins both primary and
  secondary selection rules and is restored as active; its rebuilt candidate
  SHA exactly matches its LOSO report. This is the best tested level for this
  network/date/seed, not a universal optimum. The mechanism is observability:
  route variables rise 390/498/834/1,534/3,634 while independent station data
  do not, and the added routes are largely exclusive to the held sensor.
  Evidence: `validation/sensor_od_ablation_20260901_plan.json` and
  `validation/sensor_od_ablation_20260901_result.json` plus the five linked
  LOSO reports.

- MONTHLY SEARCH THROUGHPUT (added 2026-08-27). The declared 8x1 worker policy
  was never achieved: campaign `ui-monthly-13lhsoy-5d` measured 80 330.94
  worker-seconds over 88 771.27 active seconds (ratio 0.905 - one busy worker
  against eight slots, 11.3% utilization), with 20/20 process samples showing a
  single worker. Cause: batching was PARENT-LOCAL, and a warm five-day parent
  supplies only ~1.04 uncached units (3 229 hits vs 851 misses over 816
  parents), so an eight-wide pool was fed one item at a time. Fixed by an
  opt-in global bounded daily-unit queue in the orchestration-only
  `independent_daily.py`, enabled by
  `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_WORKERS` together with a required
  `TRAFFIC_SIM_GLOBAL_DAILY_QUEUE_SCREENING=independent-exhaustive`
  declaration. SYNTHETIC SCHEDULER SCALING (SUMO replaced by a sleeping
  stand-in): achieved width 0.999 -> 7.771 (7.78x, 97.1% of theoretical) on a
  180-unit fixture with byte-identical cache across arms. SAVED REAL
  OBSERVATION, not repeated: one cold SUMO arm held exactly 8 concurrent
  workers and 8 concurrent SUMO over 170 samples without exceeding either.
  CACHE IDENTITY, corrected 2026-08-27: `monthly_sumo.py` binds NINETEEN
  sources, not fourteen, and `run_monthly_closure_search.py` is one of them,
  so the first implementation's CLI flag WOULD have orphaned all 1 083 cached
  units. The flag was removed and the CLI restored byte-identical to HEAD;
  `independent_daily.py` is genuinely outside the nineteen, and the aggregate
  backend digest is unchanged at `90f07a50...cbeef`. The
  `cache_bound_source_proof` block in the frozen baseline report asserts the
  fourteen-source version and is wrong; it is kept byte-unchanged as frozen
  evidence and superseded here.
  The campaign is OPERATOR-STOPPED and resumable (1 083 of 1 950 units cached
  and fully valid, 0 corrupt, 867 remaining) and was deliberately NOT
  restarted; its workspace manifest still reads `running`/`completed: 0`
  because the shutdown path reset that pointer, so the cache is the resume
  authority, not the manifest. Evidence:
  `validation/monthly_global_queue_baseline_2026-08-27.json` and
  `validation/monthly_global_queue_benchmark_2026-08-27.json`. The 6.58 h cold
  and 2.93 h resume figures are PROJECTIONS from production's 94.396 s/unit
  and the measured width, not measured campaigns; per-unit cost under
  sustained eight-way contention is unmeasured.

- The active published demand is forecast date 2027-11-11, build
  `b927e6de0b6443fd87e2`, one full day and 21,744 vehicles. It is an ordinary
  `q50_only` build: seeds 1000/1001/1002 all use the same central demand arm.
  All 672 directed sensor-edge × 15-minute integer targets match exactly. The
  current validation is `overall: warn`: trip-length external-fit is fail-closed
  pending an absolute threshold, one quarter exceeds the short-trip structure
  cap by two vehicles and two quarters required a six-vehicle purpose-mix
  relaxation. Count fit and SUMO health pass; the raw-source-to-map audit still
  passes on all seven directed sensor edges.
- The active immutable reference is
  `runs/releases/golden-2025-09-16-7day-v1`. Older three-variant releases are
  preserved evidence; they do not describe ordinary current recalibration.
- The deployed direction centre is the simple hour x day-type
  `shrunk_dfactor` curve pooled toward 0.5, with sensor 107 re-levelled to its
  published 2025 aggregate 3400/3100 (0.5231). The retired LightGBM model,
  `model.pkl`, training CLI and Norwegian acquisition client are absent.
- Direction Gate S is still **UNDECIDED** because the preregistered matched-seed
  closure-sensitivity run has not been executed on a suitable calibrated
  historical demand build. Gate M is **INCONCLUSIVE** because the tracked
  aggregate lacks raw counts and independent day blocks. q10/q90 are
  uncalibrated stress bands, not probability intervals.
- Ordinary recalibration is q50-only. q10/q90 are opt-in direction-stress arms
  for closure-envelope studies, and every stress arm is constrained to q50's
  exact integer population per quarter. This runtime repair did not decide
  Gate S or Gate M and did not rewrite legacy evidence.
- The seven-day normal/multi-day release, sensor-output publication gate,
  semantic build identity, purpose-route compatibility, job history and
  synthetic SignalPlan certificates are implemented. Signal studies remain
  synthetic because no city controller plans or local queue/travel-time truth
  are available.
- Monthly search is resumable and defaults to bounded-exhaustive execution
  where an adopted release gate does not authorize proxy claims. Cost-first
  policy v3 and unrestricted/global-best claims remain closed after the frozen
  equivalence benchmark failed. Independent-day UI execution now declares the
  recorded 8-daily × 1-seed worker width inside an eight-SUMO slot ceiling,
  accumulates awake active wall time across resumes and holds a macOS
  keep-awake assertion while it owns the workspace. The closure contract also
  accepts a minimum workday count; min=max requests an exact length while the
  omitted min=1 default preserves legacy identities. On the frozen stopped
  input, exact five-day intent sizes to 780 periods/1,040 units instead of
  1,776/2,224. No replacement throughput campaign has been run, so this does
  not establish a 2.7x or >=2.0x result.
- The next exact-five-day retry failed after roughly 95 minutes while preparing
  the 2027-09-29..2027-10-01 demand window. The 2027-10-01 q50 candidate matrix
  had hundreds of routes through every sensor but no route exclusive to edge
  `26842525_26355153_0`; independent q11/q16/q17 targets were consequently
  integer-infeasible. Candidate generation now guarantees one legal,
  endpoint-grounded single-sensor incidence column per measured edge after all
  route filters, or fails before PFE. The frozen failed pool needed exactly one
  new support-only shape and then published all three quarters exactly. The
  search remains stopped in error state; post-fix end-to-end time and monthly
  throughput are still unmeasured.
- Interactive baseline and closure runs now use three independent seed workers
  through `serve.py` (commit `46e7048`). The adoption check measured baseline
  11.0 -> 5.9 s and closure 21.6 -> 13.9 s with byte-identical scenario,
  trajectory and index output apart from `generated_at`. This current wiring
  supersedes the 2026-07-23 historical decision below that left the default
  serial. The frozen campaign later measured 10.496 s p95; on 2026-08-24 the
  user accepted the current interactive speed. On 2026-08-25 the user reopened
  the faster-closure goal. The active 2027-11-11 fixture then measured a fresh
  10.765 s p95 baseline and a byte-exact single-write JSON candidate at 10.359 s
  p95; the <=10 s target remains open by 0.359 s.
- The externally launched monthly search `ui-monthly-euc9qp` was explicitly
  stopped on 2026-08-21 after 476/1,776 schedules. Its workspace and completed
  evidence remain intact and resumable; the manifest progress pointer records
  `interrupted_by_user` and `resumable: true`. It is not a completed or
  promoted result.
- No annual warming population is currently running. Earlier `RUNNING`
  headings below are preserved historical checkpoints; later source changes
  invalidated those identities, and only bounded pilot evidence was recorded.
- External monthly-search observation is now complete in the working tree.
  The server distinguishes a live CLI owner from a stale running manifest,
  preserves explicit user pauses, verifies a succeeded workspace/result before
  returning it, and labels all observed work `server_tracked: false`. The
  browser restores verified external results and never offers server cancel
  for an unowned process.
- The two existing reusable-Python-worker measurements are single paired
  draws, so they are diagnostics, not an adoption or rejection result. They
  preserved exact evidence and measured 1.027x on two units and 0.998x on six
  units, both inside plausible SUMO/runtime noise. No production pool is
  activated. The benchmark is now schema v2 and requires at least four even,
  counterbalanced paired trials, separate per-trial caches and a conservative
  all-trials gate: continue only if every paired speedup is at least 1.10;
  reject only if every speedup is below 1.10; otherwise report inconclusive.
  The old records remain useful raw observations but cannot close the line.
- The empty-cache single-draw arm also reproduced a real
  matched-baseline publication race. Content-keyed cross-process single-flight
  now repairs that race and its multiprocess regression passes; nested
  concurrency still requires the remaining equivalence, resource and
  cancellation benchmark before adoption. See
  `docs/plans/DAILY_SIMULATION_CONCURRENCY_STRUCTURE_2026-08-21.md` and the two
  bound `daily_worker_pool_*_2026-08-21.json` reports.
- The capacity target is now explicit: 50 physical sensor stations, including
  any larger calibrated vehicle population that their joint evidence requires.
  At fixed load, final sensor-fit validation measured only 3.325 ms p95 for 50
  rows, so the sensor audit is not the current 13.9 s closure bottleneck. A
  one-seed diagnostic doubled demand from 21,408 to 42,816 complete vehicles in
  1.57x wall time, but the 85,632 arm left 28,977 waiting and became 35.1x
  slower; the old 100k point is therefore superseded by a calibrated
  21k/32k/43k/50k/60k ladder that fails closed on insertion backlog. Restricting
  edgeData to production's `entered` and `timeLoss` fields cut output 79.6% and
  diagnostic wall 16.9% with equal flow/recovery values. The full paired
  baseline/closure/trajectory follow-up then passed across 40 scenario runs and
  120 seed executions: identical semantic digests and health, with baseline
  wall 16.4% lower and closure wall 7.1% lower. The qualified field set is now
  the production default, with isolated `--full-edgedata` as rollback. The
  historical paired record still correctly says `production_adopted: false`
  because it predates this implementation. See
  `docs/plans/FIFTY_SENSOR_PERFORMANCE_CONTRACT_2026-08-22.md` and
  `validation/edgedata_attributes_paired_adoption_2026-08-22.json`.
- Sensor satisfaction is now an exact publication contract, not merely a GEH
  score. Every registered directed sensor edge × 15-minute target must equal
  `int(round(target))` in the calibrated route file, with zero maximum and
  summed integer residual. Missing exactness evidence or one mismatching cell
  rejects publication. SUMO loaded/inserted counts remain a separate proof
  that it accepted those already calibrated trips; they do not authorize
  adding traffic to improve performance.
- A separate raw-SUMO 15-minute passage test is implemented for baselines. It
  recomputes the ensemble, representative seed and every individual seed from
  detailed edgeData and cannot be applied to closures to calibrate away their
  effect. The active 2027-11-11 forecast result is 75/672 exact in the ensemble,
  with a maximum absolute residual of 18.333333; individual seeds are 115/672,
  109/672 and 114/672. Therefore departure-quarter calibration is exact but
  simulated crossing-time calibration is not. A bounded, order-preserving
  reconciliation prototype then reached 672/672 in all three seeds without
  changing population or routes, but it was correctly rejected: the median
  departure gap collapsed 2.7→0.1 s, 15,545 adjacent gaps hit the 0.1 s floor,
  peak departures rose 1→10 per second, and a five-trial paired median was
  3.15% slower. The fail-closed implementation and full diagnostic are in
  `tools/departure_reconciliation.py` and
  `validation/departure_reconciliation_diagnostic_2026-08-23.json`; neither
  mutates the active demand.
- Large-simulation function-boundary research is recorded in
  `docs/plans/LARGE_SIMULATION_FUNCTION_STRUCTURE_2026-08-23.md`. A
  process-free trace isolated 4.0654-4.1757 s of the active closure path in
  `closure_disruption_across_variants`. Phase schema v2 now separates that
  work, and grouped sparse routing reduced the same exact calculation from
  4.1364 s to 1.051 s. A production-shaped three-seed closure completed in
  10.690 s versus 11.549 s for full edgeData with equal semantic digests and
  clean evidence. Per-key cross-process matched-baseline single-flight is also
  implemented and tested. These are implementation/adoption diagnostics, not
  a <=10 s p95 or >=2x monthly-throughput claim.

### Current priorities

1. Use and monitor the now-qualified canonical routed weekday/weekend catalog
   matched-size and provenance-bound harness documented in
   [`docs/plans/CANONICAL_ROUTE_CATALOG_PLAN_2026-08-24.md`](docs/plans/CANONICAL_ROUTE_CATALOG_PLAN_2026-08-24.md).
   The two-date invariance proof remains valid. The original unequal
   12,000/6,000-candidate campaign remains diagnostic only. Its schema-v2
   replacement used 6,000 candidates in both arms across 30 counterbalanced
   pairs and passed every gate: median 55.246→24.715 s (2.235x ratio of arm
   medians; 2.220x median paired speedup), adapter p95
   0.678 s, faster medians for every day class, maximum paired vehicle-count
   deviation 0.761% and maximum RSS 0.794 GiB. The old trials did not record
   distinct route×purpose variables, so their PFE timing is not claimed as a
   matched-work solver speedup; new campaigns record that workload. Seven
   catalog soak fixtures plus explicit legacy rollback
   passed. The route catalog owns bounded
   plausible route supply; PFE remains
   responsible for each date's exact sensor totals, purpose margins, vehicle
   multiplicities and departures; warming remains bound to the exact finished
   day. The `--candidate-source catalog` path has content-addressed
   weekday/weekend artifacts, bounded single-flight publication and sizing,
   explicit PFE purpose-mix inputs, isolated two-date verification, a 30-pair
   qualification contract and verdict-gated schema-v3 adoption. Runtime now
   reads and hashes the named qualification/build/trials/suite files and
   cross-checks identities through the chain. Suite-only contracts are counted
   once, not copied into every arm. The corrected
   harness passes the same explicit candidate request to both arms and rejects
   key, size or report mismatches. The historical unequal-size 66.402→19.437 s
   ratio must not be used as a production claim. Catalog is the current default;
   `--candidate-source legacy` remains the tested rollback.
   The
   existing `congestion_iterations > 1` feedback path will bypass catalog v1.
   Do not substitute one historical day's XML or a union of daily pools for
   the canonical pre-resampling template. The resulting daily identity is now
   changed with this repair, so a versioned annual plan and preflight were
   refreshed. Exactly one q50 state succeeded and was restore-verified; 104,684
   planned units remain pending. Annual warming still requires an explicit
   future launch decision; catalog adoption does not start it automatically.
2. Keep both passage candidates out of production. The new day-specific
   standard-pool tool pre-samples explicit driver attributes for three arms,
   keeps population/routes/OD/purpose, retains arm variation and reached raw
   672/672 per arm without insertion bursts on the active day. The picker
   remains responsible for each day's different vehicles/routes; the pool is
   a deterministic post-picker layer, not one year-wide static vehicle file.
   Its first ten-trial baseline comparison measured +2.30% at the median and
   a production-shaped closure comparison is still absent, so it did not clear
   the no-slowdown gate and remains isolated. Before any integration, add a
   compact profile/materialization path that filters closure topology once,
   freeze a route-to-time reassignment bound, and require paired baseline
   **and closure** non-regression. SUMO's calibrator remains out of scope
   because it inserts/removes vehicles. Evidence:
   `validation/standard_driver_pool_diagnostic_2026-08-23.json`.
3. Execute the road-closure and monthly-simulation speed goal in
   [`docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md`](docs/plans/ROAD_CLOSURE_SIMULATION_SPEED_PLAN_2026-08-21.md).
   The named monthly run is paused, so S0 measurement and the remaining
   implementation phases may proceed under the plan's resource guard. The
   adopted active release now has real measurements: exact structured repeats
   pass at 0.329 s p95 over 10/10 verified hits, while ten first-new closures
   give 10.496 s p95 with identical evidence and clean health. The user accepted
   that speed on 2026-08-24 and reopened the goal on 2026-08-25. On the current
   active fixture, single-write atomic JSON publication reduced p50
   10.654→10.212 s and p95 10.765→10.359 s with identical scenario/trajectory
   digests and clean health. Keep the generic Python-worker pool inactive;
   remaining performance work includes the last 0.359 s to the first-new p95
   target, monthly throughput and scale evidence.
   Revisit the pool only with the schema-v2 multi-trial harness; do not infer a
   decision from either historical single draw.
   Preserve the full-edgeData rollback and the 50-station calibrated
   vehicle-load matrix as an independent adoption gate.
4. Run the frozen Gate S study on the specified historical demand if deciding
   whether direction stress has product value is still desired.
5. Treat Gate M as unavailable without independently supplied raw directional
   volumes; do not substitute the aggregate or relax its frozen rule.
6. Keep citywide, OD, queue, signal and closure claims bounded by the six
   clustered stations and the evidence limitations in the error register.

### Active simulation performance goal

The linked speed plan is the canonical current implementation sequence for this
goal. It separates exact-repeat latency, first-new-closure latency and exhaustive
monthly throughput; records which optimizations are already adopted or already
failed; ranks routing, exact caching, resource scheduling, cost-ordered search,
targeted warm prefixes, worker reuse, trajectory output, custom SUMO state and
libsumo by local applicability; and gives Sol phase files, measurements,
acceptance thresholds, stop gates and rollback rules. The user has accepted the
current first-new interactive latency, so the remaining headline targets are
cached p95 <=2 s, async acknowledgement p95 <=1 s and at least 2.0x monthly
verified-unit throughput, all with unchanged semantic/evidence output. The
first implementation slice is now present in
`serve.py`: structured ScenarioSpec requests use an exact, provenance-bound
  sidecar cache and re-validate the published scenario and trajectory before a
  cache hit skips SUMO. Sol review expanded the key to direct route/agent,
  network, runtime and source-tree bytes; cache verification now owns the same
  cross-process workspace slot, coalesces identical misses, rejects malformed
  or path-traversing artifacts, and refuses publication if inputs changed
  during the run. Legacy loose query requests remain uncached. This proves the
  zero-SUMO repeat path structurally, not its latency target. S0 now records
  independent-daily cache/worker
  timing in resumable progress, S1 routing flags are explicit benchmark-only
  options, and S2 enforces a declared monthly active-SUMO-slot budget. These
  are instrumentation/guardrails, not adopted speed claims; the p95 and
  monthly throughput targets remain open pending isolated paired benchmarks.

### Historical-status convention

Everything below is retained because it records measurements, decisions and
superseded designs. Words such as "current", "active", "next" and "running"
inside a dated subsection mean current at that subsection's date, not at
2026-08-21. When such text conflicts with the block above, the block above
wins.

**Direction uncertainty supplement (2026-08-13, scope-corrected after review):**
the researched, decision-gated design for improving `dirsplit` is
[`docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md`](docs/plans/DIRSPLIT_UNCERTAINTY_AND_CLOSURE_USE_PLAN_2026-08-13.md).
It first binds sensor 107's local 52/48 period anchor, runs a small matched-seed
closure-sensitivity study and compares point models. Joint scenarios and any
demand/monthly/warm/API/UI integration are conditional on three explicit
evidence gates; 50/50 plus the local anchor is a planned successful exit.
Existing q-archives and release contracts remain immutable legacy evidence.
**Implementation status (2026-08-16):** the unconditional phases are built —
Fas 0A (sensor 107's provenance-bound period anchor, applied at load time in
`demand/intake.py`), Fas 0B (`tools/measure_direction_decision_sensitivity.py`
plus its frozen registration) and Fas 1 (raw dataset v2, the four-model
tournament in `dirsplit/benchmark.py`, observability v2 in
`dirsplit/coverage.py`). Gate S and Gate M are both still **undecided**: they
need a calibrated demand build with SUMO, and the re-fetched raw Norwegian
volumes, respectively. No Gren B/D, scenario, monthly, warm-state, API or UI
code was written.

## Known Errors, Inaccuracies and Assumptions in the Simulation Flow

Audited end to end 2026-07-17 (raw data → network → demand → calibration →
SUMO → web), every number below re-checked against the working tree that
day.  This register is the honest answer to "what could be wrong?".  An item
being listed does not mean it is unaddressed — most are disclosed by design
(the confidence map exists because of them) — but nothing here may be
silently forgotten.  Ordered by pipeline stage.

### A. Sensor data (the ground truth itself)
1. **Six stations in two ~400 m clusters** constrain 7 directed edges of
   7 125.  Everything else is prior-driven inference; held-out accuracy is
   currently a typical factor 1.56 (geometric absolute error; LOSO
   2026-07-18 median ratio 0.994, range 0.763–2.576).
   Only more/better-placed sensors or external counts fundamentally fix
   this — that is the product's own pitch, not a bug, but every downstream
   number inherits it.
2. **Direction is modelled, not measured**, at the five single-direction
   stations' opposite carriageways and inside every "Total" sum. The current
   centre is the hour x day-type `shrunk_dfactor` curve pooled toward 50/50;
   sensor 107 is re-levelled to its measured 2025 period aggregate 52.31%.
   Gate M remains inconclusive without raw counts/day blocks. q10/q90 are
   leave-city-out residual stress bands with unmeasured coverage, used only in
   explicit stress studies; ordinary builds and all ordinary seeds use q50.
3. **One year (2025), local time**: DST days each miss 4 quarters (kept as
   null); the 2027 forecast is LightGBM point estimates at the 6 sensors
   only, and simulating 2027 assumes 2025 structure (bounds/priors/
   corridor coupling frozen at STRUCTURAL_REFERENCE_DATE 2025-09-16) —
   documented design decision, unverifiable until 2027 data exists.

### B. Network (OSM → SUMO)
4. **9% of edges have defaulted speeds and 70% defaulted lane counts**
   (sumo/network_audit.json: 631 and 4 990 of 7 125) — OSM tags are absent
   there, so class-based defaults (e.g. residential 30 km/h, 1 lane) set
   capacity and free-flow time.  Meso travel times and queue capacity on
   those edges are assumptions.  NVDB import (plan: "Import reviewed road
   structure") is the evidence path.
5. **All 190 signal-controlled edges use GUESSED traffic lights**
   (netconvert --tls.guess; static synthetic programs).  Meso runs
   `--meso-junction-control.limited`, and SUMO's meso engine does not model
   actuated control at all (measured 2026-07-06).  Signal timing effects on
   corridor travel time are therefore approximations everywhere, and the
   signal-optimization products are relative comparisons on synthetic
   plans, never claims about the city's real controllers (deferred-claims
   list).
6. **The frozen OSM snapshot ages**: real drift observed (128 street-name /
   15 highway-class changes in one refresh probe).  Deliberate — stable
   IDs beat freshness — but a periodic reviewed refresh is eventually due.
   One suspicious import artifact remains unverified against reality: node
   3575001205 has a single incoming connection (closing it strands a 63-
   edge pocket, 0.9%).

### C. Demand generation (who drives where)
7. **Endpoint fields are proxies**: SCB DeSO population (2023) for homes,
   OSM buildings/POIs for activities (official buildings file absent —
   OSM fallback in use).  POI density ≠ trip attraction; no local trip-
   generation rates exist.
8. **Behavioural constants are regional survey values, not Gothenburg
   measurements**: purpose shares (RVU Västra Götaland: arbete 0.53 /
   service 0.30 / fritid 0.17 weekday, hourly-modulated), purpose length
   scales, gravity deterrence (Tanner, gravity_km 1.8, α 1.5), tour
   pairing AM/PM structure.  θ was frozen after GEH saturation — sensor
   counts cannot identify these parameters (that is WHY they were frozen),
   so they are priors in the strict sense.
9. **Through traffic is prior-anchored, not measured locally**:
   `through_share_target=0.25` is now the calibrated default, while
   `through_fraction=0.5` controls only candidate-pool supply. Gate weights
   come from the gravity/Dial assignment field and verified via-pairs use
   the 45 s/20% bounded-detour rule. No cordon count exists to check the
   level directly (external data request 2 — the single highest-value
   missing measurement).
   RESEARCHED 2026-07-17 (external evidence survey): no measured
   through-share exists for Gothenburg's inner city.  The comparable
   MEASURED values found are all far lower: Potsdam's 2016 licence-plate
   cordon survey found 14% through traffic at the whole-city boundary and
   ~9% on the inner-city Havel bridges; Schwabach's transport plan
   measured 16–29% through on its main entry roads.  The only Nordic
   number found near ours — "70–80% of inner-ring traffic is through" for
   Uppsala — is a cycling-advocacy ESTIMATE with no cited measurement
   (verified by reading the source).  Two caveats kept the former 70%
   rush-hour output from being plainly refuted: (a) cordon geometry — our
   canvas is a small central
   box that deliberately contains the big approach roads as through
   gates, which raises the true through share relative to a whole-city
   cordon; (b) population — the displayed percentage describes the
   SENSOR-EXPLAINED calibrated population (every simulated vehicle must
   cross a sensor, and the sensors sit on through corridors), not all
   real traffic in the area; short internal trips that never touch a
   sensor are deliberately absent and their real-world share would dilute
   the through percentage.  Conclusion at that stage: the former 70% was
   plausible for this cordon and population but above every measured
   reference found, so it could not remain an unqualified emergent output.
   Only a local cordon/licence-plate count can settle the real level. UI
   FIXED the same day: the
   category is geographic (origin AND destination outside the canvas —
   a commuter driving through counts here), so the label "genomfart" was
   renamed "passerar området" with a tooltip defining every category and
   the population caveat (Gustav's 07:54 screenshot question — "why so
   little arbete at 8?" — was largely this labelling).
   IDENTIFIABILITY MEASURED 2026-07-17 (Gustav challenged the 70% —
   correctly noting the Uppsala figure reflects E4/Stockholm-corridor
   geography and is an unmeasured estimate, hence no anchor).  Same
   historical day built twice, through_fraction 0.5 (deployed) vs 0.3:
   GEH<5 stayed 100.0% on all three variants with 0 infeasible intervals
   in BOTH — and the whole-day calibrated through share was essentially
   UNCHANGED: 59% (prior 0.5) vs 60% (prior 0.3).  Three conclusions:
   (a) the sensor fit is completely indifferent to the through share —
   it can never be presented as a data result; (b) the share is not even
   set by the prior knob — it is an EMERGENT property of the pipeline
   (pre-verified through routes survive filtering at a higher rate than
   rejection-sampled tours, and the PFE amplifies the survivor pool's
   through share ~2× because through routes are its most flexible way to
   close sensor bands), so tuning through_fraction is NOT a lever for
   the displayed number; (c) the 70–75% figures seen in the UI are
   PER-QUARTER shares during commute hours — the whole-day share is
   ~59% — so part of the perceived excess was rush-hour composition of
   the display.  Whole-day ~59% against the measured references above
   (9–29% at other cities' cordons, geometry caveats apply): still
   plausibly high, genuinely unknowable from internal data; the
   cordon/licence-plate count is the only evidence that can move this
   number, in either direction.
   EVIDENCE LADDER (surveyed 2026-07-17, after a second literature pass
   found no further measured city-centre through shares — Oslo/Bergen/
   Trondheim publish only whole-urban-area figures, German VEPs keep the
   percentages inside non-indexed PDFs):
   (i) SELF-SERVE, highest value: Trafikverket's vägtrafikflödeskartan /
   Lastkajen / open API carries MEASURED flows on the state roads at our
   canvas boundary (E6, E20, Rv40, Oscarsleden — exactly the through
   gates).  Adding 3–5 boundary stations to data_in/sensors.json pins
   gate in/outflows; conservation (entered = terminated + exited)
   combined with the internal stations then makes the through/internal
   split PARTIALLY IDENTIFIABLE for the first time, using the product's
   own every-new-sensor mechanism.  No permissions needed.
   (ii) FREE ASK via Miroslaw: an OD extract over our cordon from the
   city/regional VISUM–Sampers model — modelled, but independently
   calibrated; would give a defensible through share quickly.
   (iii) PAID: mobile-network OD data (e.g. Telia Crowd Insights, which
   Swedish cities routinely buy) — direct through-share measurement.
   (iv) GOLD STANDARD: ANPR/Bluetooth cordon survey (the existing
   external data request).
   THROUGH-SHARE SWEEP JUDGED BY HELD-OUT SENSORS (2026-07-17 late
   evening, Gustav's proposal: borrow other cities' levels but let our
   own validation decide): the purpose-margin machinery ENFORCED through
   shares 0.25/0.35/0.45 (achieved exactly in every fold, verified from
   the agents sidecars) and full LOSO ran per level on 2025-09-16.
   Result — monotone, and the literature-anchored low end WINS:
     level    median  geo-err  mean GEH<5   ratios
     0.59 ref  1.71    1.82x     37.5%   0.55…2.73
     θ=0.25    1.00    1.57x     54.2%   0.70…2.51
     θ=0.35    1.18    1.62x     50.0%
     θ=0.45    1.67    1.93x     41.7%
   The cluster-corridor over-prediction largely WAS the through excess
   (107's first edge: 1.71 → 1.00 at θ=0.25).  Honest caveats: (a) this
   selects one knob from three candidates USING the validation set —
   mild tuning, disclosed, and the value must be CONFIRMED on the second
   day (2025-09-17 spec d2eb4e00b7d8be1c) before becoming default;
   (b) 2276 stays over-predicted (~2.4-2.5) at every level — its error
   is NOT through-driven and remains open; (c) 0.25 sits at the low end
   of the measured external range (9-29% + geometry uplift), so pushing
   below it would leave the prior-defensible band and overfit LOSO.
   CONFIRMED ON THE SECOND DAY (2026-07-18, 2025-09-17 historical,
   spec d2eb4e00b7d8be1c — a day never used for selection). The original
   sweep, before validation applied the deployed calendar activity margin,
   reported baseline median 1.63 / geo-err 1.79x / mean GEH<5 43.4% →
   θ=0.25 median 1.04 / 1.53x / 53.6%. REVIEW-CORRECTED exact
   validated-equals-shipped rerun (calendar activity margin + θ=0.25):
   median **1.111**, geo-err **1.545x**, mean GEH<5 **51.2%**, ratios
   0.647–2.515. The precise figures move, but the held-out improvement
   over the old mix survives on the untouched day; 134/2276 remain the
   non-through-driven residual. VERDICT: θ=0.25 is
   validated as "prior-anchored (measured external range) +
   held-out-selected + second-day-confirmed" and is now the default via
   `--through-share-target` (proper flag, provenance in demand_meta,
   disclosed in the UI tooltip) — NEVER described as a measured
   Gothenburg value; the cordon count remains the only decisive evidence.
   DEPLOYED/RE-VERIFIED 2026-07-18 on 2025-09-16: achieved whole-day share
   25.04% (5,422/21,656 agents), GEH<5 100% on all three direction
   variants with 0 infeasible intervals. Leakage-free LOSO under the exact
   shipped activity margin + through target gives median ratio 0.994; four
   of seven directed edges are 0.763–0.994, while the three documented
   residual over-predictions remain 2.111, 2.413 and 2.576.
10. **The 45 s / 20% detour-naturalness constants** are literature-
    plausible and LOSO-supported in *direction* (isolated station 0.05 →
    0.55) but not independently calibrated; LOSO 2026-07-18 shows the
    through target corrected the median to 0.994 but left three
    OVER-predicted cluster-corridor edges (2.111–2.576) — the next tuning
    lever, best
    constrained by tightening the assignment-field ceiling rather than
    refitting the constants against the validation set.
11. **The assignment field's scale fit is weak by construction**: robust
    median ratio on 6 measured edges, R² ≈ −5 (documented "informational
    only").  It is used as a weak ceiling (w=0.15) and for gate draw
    density, not as a load claim — but both uses inherit its shape errors.
12. **Candidate routing is free-flow by default** (congestion_iterations
    defaults to 1 = no feedback round): route choice ignores congestion
    unless a feedback build is requested.  Meso baseline delivery 0.87–
    0.96 suggests acceptable at current volumes; wrong in principle at
    saturation.
13. **Finite support pool** (~10 k candidates → ~6 k route×purpose
    variables): the PFE can only weight offered geometry.  Worst single
    shape still carries ~110–130 veh/day; convoy share ≥10 clones ~1–5%.

### D. Calibration (PFE)
14. **Conservation bounds between sensors assume no unmeasured sources/
    sinks along the corridor segments** — no turning-fraction measurements
    exist; corridor coupling ratios are learned from the same 6 stations.
15. **Structure guards cap against the POOL's own shares** (2.5×/3×
    multipliers): they preserve the generator's seed structure, which is
    itself assumption C7–C9.  Caps are dropped counts-first when
    infeasible (disclosed per quarter, currently 0–2 quarters/variant mix
    relaxation).
16. **Route geometry is shared across the day**: one shape set serves all
    96 quarters (departures vary, geometry does not) — no within-day
    route-choice drift (e.g. rush-hour rat-running) beyond what distinct
    shapes already encode.
17. **100% GEH<5 is fit, not accuracy**: it holds AT the 7 constrained
    edges; the honest generalization number is the LOSO factor (1.56×
    geometric absolute error, median delivery ratio 0.994) and
    it is displayed as the confidence map, never as citywide accuracy.

### E. Simulation (SUMO meso)
18. **Mesoscopic queue model**: no car-following, no lane-changing, no
    actuated signals; validated for 15-min edge flows (delivery 0.87–0.96
    vs micro 0.83–0.94), NOT for queue lengths, spillback geometry or
    travel-time distributions.  Micro exists behind --micro for windows
    that need it; queue-based closure advice carries the
    queue_proxy_unmeasured fail-closed gate for exactly this reason.
19. **Three ordinary seeds over one q50 demand arm** is a small Monte Carlo
    ensemble. Explicit closure-envelope stress builds add q10/q90 as separate
    direction-allocation cases with q50's exact population held fixed.
    Per-edge confidence = spatial_prior × exp(−CV) remains a heuristic
    combination, not a calibrated probability.
20. **No en-route rerouting in the baseline** (closure rerouters only,
    within 400 m): drivers never divert due to congestion alone.
21. **Closure behaviour rules are assumptions**: truncate-stranded (driver
    parks at last reachable edge), rerouting radius 400 m, dropped-only-
    if-first-edge.  Reasonable, argued, untestable without incident data
    (external request: closure-period counts).
22. **1-second exit-time resolution** makes ~4% of edge traversals
    (short edges) look instantaneous in the trajectory export — display
    artifact, not a dynamics error.

### F. Display and confidence
23. **Confidence is distance-only**: exp(−d²/2σ²), σ=127.5 m fitted from
    7 near-field LOSO points inside two clusters.  It ignores network
    topology (a parallel unconnected street 150 m away scores high),
    direction, and volume; beyond ~300 m everything is labelled
    extrapolation with near-zero confidence — honest but coarse.  The
    far field has NO validation points at all.
24. **The Simulering colour scale is a display transform** (conf^(1/8))
    of the true confidence so the gradient stays legible; tooltips carry
    the exact value.  Any reader of the map alone sees relative, not
    absolute, certainty.
25. **The animated vehicles are ONE representative run** (seed 1000, q50)
    while road colours/audits use the ensemble mean — labelled in the UI;
    an individual animated car is an illustration of aggregate 15-min
    flows, not a tracked real journey (candidate tours provide AM/PM
    structure as a prior; PFE calibrates aggregates independently).

The current mitigation order is the one in "Current priorities" above.
Independent external evidence remains the only way to reduce several
scientific limitations, but no such data is expected; those limitations stay
explicit instead of remaining implementation dependencies.

## How To Use This Document

This document consolidates the former forward plan, program-improvement plan,
simulation accuracy/speed plans, code audit, destination-bias research, and
dated execution log. It preserves obsolete proposals and checkpoints as dated
history, not as current instructions.

1. Read `ARCHITECTURE.md` first for system structure and contracts.
2. Use the 2026-08-21 block at the top for current status and priorities; use
   later sections for rationale, evidence gates and history.
3. Treat an item as complete only when its acceptance gate and recorded
   measurement pass. A code change or green sensor GEH by itself is not enough.
4. Keep historical evidence in immutable run artifacts, tests, validation
   reports and Git history rather than duplicating it across planning files.
5. When resuming work, start from "Current verified status — 2026-08-21".
   Dated "current" or "running" statements below it are historical unless the
   top block explicitly carries them forward.

## Consolidated Status

### Foundations completed and retained

- Immutable run manifests, staged publication, per-seed health gates and
  durable job records prevent incomplete work from replacing active results.
- Run archiving is now scoped to the producing process: demand runs no longer
  glob stale closure routes from the shared `sumo/` directory, and scenario
  runs archive the exact scenario/trajectory they wrote instead of guessing
  from filesystem modification time. Run output records now include SHA-256
  content hashes. The golden-release mechanism extends the existing run
  registry under `runs/releases/`; the active immutable reference is
  `golden-2025-09-16-7day-v1`.
- Trajectory parsing includes SUMO reroutes in `routeDistribution`; unfinished
  vehicles and displayed-share integrity are explicit rather than silently
  omitted.
- The demand pipeline is split into modules, has a deterministic PFE benchmark
  fixture, per-variant fit gates, validation reporting and semantic speed
  benchmarks.
- Security hardening is in place for the local API: POST-only mutations,
  origin guard, CSP and safe dynamic result rendering.
- Sensor direction semantics now live in `data_in/sensors.json`, are validated
  before calibration, and participate in fingerprints.
- ScenarioSpec/ClosureSpec validation, time-windowed closures, paired closure
  comparisons, exhaustive candidate evaluation, network metadata and source-
  to-SUMO network auditing are implemented.
- New web closure and closure-time requests now carry the complete validated
  base/closure ScenarioSpec; `serve.py` archives it and invokes the runner
  with `--scenario-spec`. Legacy query requests remain only as a compatibility
  path during migration.
- Signal optimization now uses the same contract end to end: the browser
  sends the active scenario's full `ScenarioSpec`, the API archives and
  forwards it, and both plain and closure signal runners honor its exact seed
  set and demand-variant mapping. Legacy query requests remain supported for
  CLI/backward compatibility, but new UI jobs no longer reconstruct identity
  from loose edge/window parameters.
- Demand recalibration now uses a validated `DemandBuildSpec`: the browser/API
  archives the exact date, source, day range, effective window and structural
  reference; `build_sumo_demand.py` validates repeated legacy flags against it,
  writes `sumo/demand_build_spec.json`, and includes the contract plus all
  demand-affecting options in the content fingerprint. A matching
  `demand_build_key` is returned in job status and carried into `demand_meta`,
  so stale scenario sets cannot be mistaken for the requested calibration.
- Shared runtime code has been reorganized under `traffic_sim/` without
  breaking stable root CLI paths. Contracts, fingerprints, sensor intake,
  demand calibration/PFE, held-out confidence validation, SUMO
  metadata/runtime, disruption metrics, and run/release registries now each
  have one canonical implementation. Root compatibility imports and CLI
  wrappers contain no duplicate logic, and demand cache/build fingerprints
  hash the canonical package files.
- The browser supports focused normal, closure, closure-timing and synthetic
  signal-study workflows. Signal output already shows numeric timing changes
  with provenance.
- Scenario playback publishes a per-direction sensor audit (2026-07-16):
  every scenario JSON carries `sensor_audit` with the frozen source
  observation, the directional calibration target, the Monte Carlo ensemble
  mean and the displayed representative seed, plus GEH summaries — count
  delivery is read from a table, never inferred from dot density. Demand
  builds persist the exact `sensor_targets`/`sensor_observations` they
  calibrated against in `demand_meta`; scenarios built from older demand
  builds reconstruct them from current inputs and are labelled
  `reconstructed_current_inputs` until the next recalibration freezes them.
  Two-way Total stations stay labelled as physical-station totals, never as
  directional measurements, and missing values remain `null`, never zero.

### Active improvements, ordered by value and dependency

This list ranks the remaining work by value; the execution queue — which
interleaves these items with their gate dependencies — is "Recommended
Implementation Order" at the end of this document.

1. **Implemented:** enforce SensorRegistry validity, approved snaps and active
   dates before a sensor can constrain a build.
2. **Implemented:** make final SUMO sensor output auditable and calibrate
   normal runs against the frozen target at the correct physical-station
   aggregation. A fresh staged set fails closed when this evidence is absent.
3. **Implemented (verified 2026-07-20):** purpose-compatible route
   allocation is complete and `purpose_claims_allowed` is TRUE on the
   active release.  This entry previously described the pre-fix state and
   was stale: the fix landed with the 2026-07-17 realism pass (`46719f4`,
   hardened in `ce7112d`) and is exactly the formulation this plan asked
   for — compatibility BEFORE publication, not relabelling after
   calibration.  `prepare_calibration` keys the LP/IPF variable pool on
   `(geometry, provenance purpose)`, so a geometry occurring in several
   generated purpose classes becomes several variables and the selected
   route's purpose is an INVARIANT rather than a post-hoc label; the
   production path then uses `allocate_strict_interval_provenance`, which
   raises rather than substituting when a shape has mixed purposes or no
   matching-purpose source.  When an exact purpose margin is infeasible
   beside the hard sensor counts it falls back counts-first and discloses
   `mix_deviation` instead of forcing the mix.  Measured on the active
   2025-09-16 release, all three variants: 0 incompatible quarters, 0
   replaced routes, 0 relaxed-mix quarters, RVU length ordering intact
   (through 5.90 km > fritid 2.98 > service 2.78 > arbete 2.51 km median).
   Through-traffic SHARE remains a sensitivity-tested prior until a cordon
   count identifies it (external data request 2) — that is a separate
   open question from route/purpose compatibility, which is closed.
4. Freeze and exercise a golden normal, closure and bounded micro-signal
   release, including rollback.
5. **Contribution slice implemented, but the chain is UNEXERCISED
   (checked 2026-08-04):** `sensor_contribution.py` emits evidence-bound
   before/after holdout, confidence, coverage and isolation reports plus a
   placement screen, and imports cleanly with `sensor_registry.py` against
   the current tree. A new station still needs a real before/after artifact
   before it can be called an improvement.
   However, a repository-wide search finds NO contribution or placement
   artifact anywhere — the tool has never been run for real. So the path
   "new sensor → validated registry record → rebuild → measured
   contribution report" is unproven end to end, and the first time it is
   tried should not be when a real station arrives with someone waiting.
   Cheapest proof that needs no new data: run the contribution report with
   an EXISTING sensor held out and put back, which forces the whole chain
   through. `data_in/sensors.json` currently holds exactly the six
   validated stations (107, 133, 134, 1074, 1076, 2276).
6. **Certificate implemented, and extended to the closure path
   (2026-07-20):** every signal-optimization condition gets a
   machine-readable TSFS-informed phase/link timing certificate, and
   `signal_closure_combine.py` (D4) now emits the same evidence for
   closure-driven arrivals — versioned `SignalPlan` artifacts for both the
   uncertified baseline reference and the optimized plan, plus a timing
   certificate for the optimized plan that ABORTS the study before
   publication if the TSFS-informed envelope is violated.  The result
   carries `signal_plan_id`/artifacts/certificates and `serve.py` surfaces
   them, so Phase 5's exit condition ("every timing result identifies its
   SignalPlan") now holds on both the normal and closure paths.
   **This item is CLOSED, not pending.**  Per the 2026-07-20 decision
   ("External Data Requests — CLOSED, no further data coming"), no city
   controller plan is coming, so signal results are `synthetic`
   PERMANENTLY.  Do not read the earlier phrasing ("until a city controller
   plan is imported") as a waiting dependency — external data request 1
   will never be sent.
   What DOES remain is EXECUTION, not construction.  As of 2026-08-04 the
   only signal artifact anywhere in the repository is
   `signal_golden_smoke_0000_0015.json`: a 15-minute window, 25 vehicles,
   one seed, `recommendation_allowed: false`.  D1–D6 are built and
   unit-tested (117 tests across the signal and sensor modules, all
   importing cleanly against the current tree) but have never been run as
   a real study.  The next step is to RUN `signal_closure_combine.py`
   against a real closure ScenarioSpec in a peak window — it already
   accepts `--scenario-spec` from the closure search, so it can optimise
   against exactly the rerouted arrivals of a chosen closure.
7. **Feasibility slice implemented:** closure-time ranking now rejects missing
   queue evidence, partial/no detours, truncation and unhealthy candidates;
   paired uncertainty and queue deltas are published with every candidate.
8. **Implementation slice complete:** continuous multi-day runs now emit cheap
   periodic SUMO summary evidence, per-day boundary accounting and a
   fail-closed staging gate. The isolated 2025-09-16→18 acceptance study now
   passes every computational gate and its real 192-quarter browser playback
   was accepted by Gustav. Exact 3-, 4- and 5-day studies also passed, and the
   continuous seven-day study proves every day separately (including day 6).
   The bounded browser product samples at most 10 000 real q50 vehicles per
   day; the simulation and confidence calculations still use every vehicle.
   `golden-2025-09-16-7day-v1` is now validated and active.
9. **Network provenance implemented:** the existing network audit records
   OSM/defaulted values and SUMO TLS membership. Actual NVDB import remains
   evidence-bound until a reviewed download is supplied.
10. **Study/job history implemented:** the start workspace now exposes the
   durable `/api/jobs` records; importing a city signal corridor remains
   evidence-bound and is intentionally not fabricated.
11. **Robust finalist decision slice implemented:** matched mesoscopic
    observations now retain candidate, q10/q50/q90 variant, seed, baseline
    and provenance identity. `finalist_decision.py` uses simultaneous 95%
    paired intervals, ranks the worst-variant upper bound, requests adaptive
    repetitions, removes hard failures before ranking, and structurally
    returns `unique_winner`, `tie`, `inconclusive`, or `no_viable`.
    Conditional two/three-finalist microscopic confirmation remains separate
    from the mesoscopic score and exposes unavailable or incomplete queue
    detail instead of guessing. The prior three-seed interval artifacts do
    not retain variant identity and are deliberately not accepted as robust
    finalist evidence.

### Evidence-bound work that must not be faked

- Actual city controller plans, detector logic, movement conflicts, offsets,
  pedestrian/cycle timing and temporary work plans.
- Local link/path travel times, speed distributions, queue discharge and
  detailed lane-changing behaviour.
- Local OD, trip-purpose and through-traffic truth beyond what the six counts
  identify.

The absence of these data does not stop the current product. It limits claims:
citywide mesoscopic normal/closure results remain calibrated near sensors and
prior-driven elsewhere; signal results remain synthetic experiments until a
verified SignalPlan is imported.

## Consolidated Engineering Findings

### Demand, destinations and purposes

Six count stations constrain sensor crossings, not each vehicle's real origin,
destination or purpose. The previous candidate/PFE design could exploit that
underdetermination by selecting routes that ended immediately after a sensor.
The implemented response is retained as a permanent guard: joint natural
sensor-route sampling, destination naturalness masks, downstream-distance and
trip-length structure checks, near-sensor destination diagnostics, and
calibrated-output checks in addition to generated-pool checks.

The improvement demanded here — not relabelling trips after calibration, but
a purpose-stratified formulation making the selected route instance
compatible with purpose, departure time and length BEFORE publication — is
**implemented and its incompatibility diagnostic is zero** (see ranked item
3 for the mechanism and the measured per-variant evidence).  Purpose labels
on the active release therefore rest on compatible provenance rather than
being diagnostics only, and `purpose_claims_allowed` is true.

Two honest limits survive that fix and must not be conflated with it: a
compatible label states which generated behavioural class the route came
from, not verified individual trip intent; and through-traffic SHARE remains
a sensitivity-tested prior until a cordon count or local OD source
identifies it.

### Simulation integrity and closure realism

The former critical defects in rerouted-vehicle animation, missing unfinished
vehicles, fail-open health telemetry, stale route artifacts, unpaired closure
comparisons and closure window mismatch are resolved. Their protections stay
mandatory: final-route parsing, source-file reconciliation, per-seed health,
full input fingerprints, paired seed/variant comparisons and ScenarioSpec
window identity.

The remaining closure work is decision quality: apply access and detour gates
before ranking, evaluate all feasible windows when the candidate set is
bounded, reject low-delay results caused by lost traffic, and expose the
uncertainty/gate state rather than a single score.

### Sensor-value contract and final output calibration (P0)

The program has four different numbers at a sensor.  They must never be
silently substituted for one another:

1. **Source observation** is the delivered historical count, or the forecast
   value when a forecast day is selected.  A forecast value is an input
   estimate, not an observation.
2. **Frozen calibration target** is the exact target given to the demand
   build.  A directed station has one target on its measured directed edge.
   Station 107 is different: the delivery is one physical two-way Total; its
   directional q10/q50/q90 split is a model assumption, not two measured
   values.
3. **Final SUMO output** is the count from the final `edgeData` result after
   vehicles have actually entered the edge during that 15-minute interval.
   This is the number the road colour represents.
4. **Animated representative** is one seed/variant's vehicles.  It is useful
   for visual inspection, but it is not the ensemble mean and cannot be used
   to count all simulated traffic by eye.

The new `sensor_audit` payload correctly exposes all four concepts.  It is
not yet a proof that the final simulation delivers the sensor target.  On the
currently published 2027-10-20 forecast baseline, its PFE fit is 100% GEH<5
and all 53,311 variant vehicles were inserted without teleports, but the
displayed final SUMO values can still differ from their q50 targets.  This is
expected evidence of a missing final-output gate, not a reason to overwrite
the map with input values.  The current audit is additionally labelled
`reconstructed_current_inputs`; it must be rebuilt from a demand build that
persists its frozen inputs before it can be frozen as release evidence.

#### Confirmed defects and semantic risks

1. **P0 — publication validates the wrong stage.** `serve.py`'s
   `validate_staged_scenarios` validates demand/PFE fit and seed health, but
   does not validate `sensor_audit` or compare final SUMO `edgeData` output
   with a frozen target.  `validation_report.py` similarly reports
   `demand_meta.pfe_fit`, not final sensor output.  A normal baseline can
   therefore publish with an excellent PFE GEH result while its final SUMO
   entries at sensors are different.  No normal scenario may be described as
   having *exact sensor delivery* until this is fixed.
2. **P1 — closures lose audit variant identity.** The audit infers q50/q10/q90
   from the literal route filename.  `run_scenario.py` renames closure route
   files to forms such as `calibrated_v1_close_*.rou.xml`, while
   `target_key_for_route_path` recognises only the three original filenames.
   The resulting closure audit cannot recover a target for any seed, so its
   target and fit fields become empty even though the demand variant is known
   earlier in the job.  This is a confirmed code defect, not a modelling
   limitation.
3. **P1 — the two-way station can look double-counted in the table.** The raw
   Total for station 107 is intentionally repeated beside both directed rows
   and labelled `tvåvägs-total`.  A user can nevertheless read or sum it as
   two independent observations.  The calculation is not double-counting it,
   but the presentation is too easy to misread and does not meet the required
   physical-station contract.
4. **P2 — provenance is recoverable but not frozen for legacy runs.** The
   fallback reconstruction keeps old scenarios inspectable, but it reads
   current input files.  It is unsuitable for a historical release claim if
   those inputs later change.  New demand builds persist the data correctly;
   an end-to-end test and the next fresh baseline must prove that the exact
   stored arrays survive into the published audit unchanged.

#### Required implementation sequence

1. **Carry semantic variant identity, never infer it from a filename.** Put
   `demand_variant: q50|q10|q90` and the corresponding frozen target key in
   each seed job.  Return them from `run_seed_job` and use them in the audit,
   trajectory provenance and any paired comparison.  Filename parsing may
   remain only as a compatibility fallback.  Add unit coverage for each
   closure-renamed route and an end-to-end closure-audit test.
2. **Publish a versioned final-output fit artifact.** For every normal seed
   and every 15-minute interval, compare its final SUMO `edgeData` entry with
   that seed's frozen target.  Publish per-direction residuals, absolute
   error, GEH and missing-data state, plus an ensemble section.  Aggregate by
   physical station before declaring a measured fit:
   - sensors 133, 134, 2276, 1074 and 1076: compare their one measured
     directed edge directly;
   - sensor 107: compare `north + south` against the one delivered Total;
     show the q10/q50/q90 directional split separately as an assumption, not
     as two observations.
   The artifact must contain the frozen demand build ID, source fingerprint,
   target provenance and target arrays/hash.  It belongs in the scenario
   payload and in `validation.json`, with a schema version that makes absent
   output-fit data fail visibly rather than look healthy.
3. **Measure the residual mechanism before changing the solver.** PFE selects
   route/departure instances while SUMO counts actual edge entries.  Travel
   time from departure to a sensor, congestion and rerouting can move a
   vehicle into a neighbouring 15-minute `edgeData` bucket.  This is the
   leading hypothesis for part of the current mismatch, not an established
   root cause.  Instrument one frozen normal day to retain, per constrained
   edge and quarter: selected PFE route count, planned departure quarter,
   actual SUMO entry quarter and any reroute/truncation outcome.  Classify
   residuals into timing shift, missing route coverage, integer/bound effect,
   or unexplained.  Do not loosen an error threshold or change raw sensor
   values to make this diagnostic green.
4. **Correct output calibration with bounded feedback only after the
   diagnosis.** If timing is the cause, build the PFE incidence against the
   expected sensor-entry quarter (initially from recorded lag), then make at
   most one or two output-feedback corrections from actual `edgeData`
   residuals.  Reuse the same immutable candidate pool, route alternatives,
   seed/variant plan and hard feasibility bounds.  If a residual cannot be
   corrected within those constraints, report it as infeasible rather than
   fabricating a match.  Benchmark the correction against the golden normal
   day; it may add a bounded meso pass but must not increase PFE work or lower
   seed/variant fidelity.
5. **Make the gate stage-aware.** A normal baseline must require a present,
   frozen final-output-fit artifact and fail publication when a measured
   station's agreed output contract is not met.  The exact contract is zero
   residual after integer aggregation for a historical directed count, and
   zero residual for the station-total at 107; forecast runs apply the same
   check to their frozen forecast target, while remaining labelled forecast.
   Do not adopt a convenient GEH tolerance as a substitute for this exact
   count-delivery check.  A closure must not be forced to retain normal
   sensor counts: it must instead display the matched normal target/output,
   closure output and scenario effect, while retaining its health, access and
   closure-integrity gates.
6. **Make the UI physically unambiguous.** Keep the map value as final SUMO
   output.  On a sensor edge, show `source | target | SUMO output` with the
   active interval and seed/ensemble label.  Render station 107 as one
   physical Total row with two indented modelled-direction rows; never repeat
   the raw Total as though it were two directional observations.  The sensor
   audit table remains the full numerical source of truth; animation stays a
   representative visualisation.

#### Acceptance gate

- A newly recalibrated historical normal baseline stores (not reconstructs)
  all audit inputs and reproduces them byte-for-byte or by recorded content
  hash in the published scenario.
- Every directed measured station has an explicit final SUMO residual for
  every available quarter; sensor 107 additionally has one station-total
  residual.  `null` remains missing, never zero.
- The normal release cannot publish with a missing, stale or failed
  final-output fit.  A closure is correctly labelled as a changed scenario,
  not as a failed normal calibration.
- The output correction has a recorded before/after result on the frozen
  normal day, no new health/structure regression and no reduction in seeds,
  variants or simulation fidelity.

### Whole-program audit: confirmed gaps (2026-07-16)

#### Implementation status (2026-07-16)

The first P0/P1 implementation slice is now in the codebase:

- `SensorRegistry` is fail-closed for accepted quality, approved snaps,
  reviewed edge IDs, snap distance and active study dates. `build_data.py`
  validates the current graph resolution against the registry before writing
  flows. The six existing stations were migrated to their reviewed directed
  edges using true point-to-polyline distances.
- Demand metadata now records registry/network hashes and the exact sensor
  edge contract. Staged publication rejects a new build whose contract is
  missing or stale.
- SUMO scenario audits carry explicit q50/q10/q90 provenance, preserve the
  unrounded ensemble mean for fit calculations, and add a physical-station
  aggregation for two-way totals. The map still receives rounded integer
  flows only for display.
- The staged publication gate requires the new raw final-output-fit artifact,
  frozen demand provenance, complete sensor series and all three declared
  variants for a normal three-variant release. Legacy artifacts remain
  readable but are not treated as proof of the new contract.
- Verification on 2026-07-16: `python3 -m pytest -q tests` reports
  **942 passed, 21 skipped, 2 warnings** (loopback-enabled run; 963 tests
  collected). The warnings are the existing LibreSSL and pandas date-parser
  warnings, not failures.
- The same verification includes the multi-day summary parser/publication
  gate and the synthetic signal-plan timing certificate. Ordinary one-day
  scenario runs do not request summary output, so this evidence path does not
  slow the normal simulation.
- Purpose allocation now has a conservative replacement path: if a selected
  shape lacks the requested purpose but an already generated route has the
  identical measured-edge signature, its unconstrained leg is replaced and
  the count-preserving replacement is recorded. This reduces avoidable
  incompatibility without fabricating routes; remaining categories without a
  same-signature candidate stay explicitly flagged for candidate-generation
  work.

Still deliberately open: completing the purpose-compatible route allocation
(candidate coverage is still insufficient for every through signature),
closure feasibility and closure-driven signal optimization. The continuous
one-through-seven-day publication contract is now release-backed; a separate
six-day calibration was intentionally omitted because day 6 already has
explicit input, output, boundary and health evidence inside the stricter
seven-day run.

This review covers the active intake, demand, normal simulation, closure,
signal, multi-day, publication, UI and release paths against the current code,
tests and generated artifacts.  The items below are **confirmed** by a code
path or an active artifact.  They are not speculative modelling ideas.  They
are deliberately separated from evidence limits such as unavailable real
signal plans or travel-time data, which remain limitations rather than bugs.

1. **P0 — SensorRegistry enforcement (resolved in the current slice).**
   This was a confirmed intake gap: `validate_data_sensors` used to check only
   unknown IDs, catalogue verification and coordinates, while automatic snaps
   could enter calibration.  It now enforces active dates, accepted quality,
   approved snaps, reviewed edge IDs and a true snap-distance limit; `build_data.py`
   also validates the current graph resolution before writing flows.

   **Implemented fix:** bootstrap-review and record the current six resolved directed
   edges, then require an active, accepted record and a resolved snap that
   matches the approved directed edges, bearing and distance threshold for the
   build's network fingerprint.  A changed OSM snap must stop the build and
   produce a review artifact; it must never silently move a measurement to a
   new road.  Date-range validation must exclude a sensor outside the study
   interval.  Store the resolved-snap artifact and registry hash in the
   demand build and add fail-closed tests for every field.

2. **P0 — Normal PFE fit is not final SUMO output fit (artifact/gate now
   implemented; correction threshold remains open).** This was the
   publication and audit issue described in the preceding sensor-value
   section.  The scenario now records raw edgeData fit, station aggregation,
   provenance and registry/network identity, and new staged releases fail
   closed when that artifact is absent or stale.  The remaining work is to
   calibrate the SUMO output itself until the frozen golden case meets the
   chosen residual threshold; GEH is not being used as a substitute for that
   decision.

3. **P0 — Current normal demand still has known realism gates in warning
   state.** The active `validation.json` records
   `purpose_incompatible_quarters_by_variant = 96` for q50/q10/q90, so
   purpose labels are correctly blocked from being evidence.  The new
   count-preserving same-signature replacement reduces avoidable provenance
   mismatches, but it cannot create a missing through-route signature.  It also reports
   two calibrated-versus-pool structure drifts above the 2.5x limit:
   `onward_under_200m_pct` 3.6 vs 1.4 and `trips_under_1km_pct` 1.43 vs 0.5.
   The simulation can still run, but it must not be presented as a validated
   purpose/route-distribution result until the allocation is repaired and
   temporal/LOSO checks are rerun.

4. **P1 — Uncertainty coverage can be silently collapsed by a valid
   ScenarioSpec.** `ScenarioSpec` requires every seed to have a q10/q50/q90
   label, and `run_scenario.py` resolves that mapping, but neither contract
   validation nor the publication gate requires a three-variant normal build
   to actually run all three variants.  A syntactically valid spec can map all
   seeds to q50 and still publish.  This violates the stated Monte Carlo
   uncertainty contract without changing a build ID.

   **Implemented fix:** derive the required variant set from `demand_meta.n_variants`.
   For a normal three-variant release, require q50, q10 and q90 at least once
   and require the published seed mapping to match the declared plan.  Permit
   a deliberately reduced mapping only for an explicitly labelled diagnostic
   study that cannot replace the normal baseline.  Test both rejection and
   the standard 1000/q50, 1001/q10, 1002/q90 path.

5. **P1 — The sensor audit calculates fit from display-rounded ensemble
   flows.** `aggregate_flows` rounds the three-seed mean to an integer for the
   map, then `build_sensor_audit` reuses that rounded value for
   `simulated_mean` and its reported ensemble GEH.  Rounding is appropriate
   for a road-colour label, but not for an accuracy calculation: it can change
   an ensemble residual by up to half a vehicle per directed edge/quarter.

   **Implemented fix:** retain per-seed integer output and an unrounded numerical mean in
   the audit/output-fit artifact.  Use the unrounded value only for ensemble
   statistics; retain the rounded value exclusively as `map_display_flow`.
   The q50 representative remains an exact integer count.  This change is
   computationally negligible and must be covered by a fractional-mean test.

6. **RESOLVED 2026-07-18 — the UI's 1–7 day normal-study contract is
   release-backed.** Exact 3-, 4- and 5-day builds passed the same per-day
   q50/q10/q90 gates as the active two-day case. The continuous seven-day
   build produced 672 quarters and separately passed every day for PFE input,
   raw hourly SUMO output, health and midnight accounting; all 445 144
   variant vehicles were inserted with zero teleports. Day 6 is therefore
   explicitly proved within the week without paying for a redundant separate
   six-day optimization. The deterministic trajectory export is bounded to
   10 000 real q50 vehicles per day (70 000 total, 51.8 MB), while all
   vehicles still contribute to simulation flows and confidence. The staged
   publisher, HTTP API, validation report, integrity checks and real rollback
   exercise passed, and `golden-2025-09-16-7day-v1` is active.

7. **P1 — Closure sensor audits lose target data after route filtering
   (resolved).** The runner now carries the semantic variant in each seed
   result and retains a filename fallback for legacy callers, so closure route
   copies cannot erase q10/q50/q90 audit identity.

8. **P2 — Documentation and release status contain stale assertions.**
   `IMPROVEMENT_PLAN.md` previously claimed a completed golden release even
   though `runs/releases/` is absent, and stated a full-suite result without
   naming its runner or environment (a 2026-07-16 dev-machine run records
   the actual result: 942 passed, 21 skipped, 963 collected — the count was
   real, but a claim without provenance cannot be told apart from a stale
   one, which is the defect).  `ARCHITECTURE.md` also contains
   dated PFE/LOSO figures that it explicitly marks pending revalidation, while
   its product wording says every added sensor *must* improve all outputs.
   The latter is stronger than the statistically honest `improved | neutral |
   insufficient evidence` rule already adopted here.

   **Fix:** record executable results only in immutable run/release artifacts,
   update static documentation with dates and provenance, and change the
   sensor promise to: every validated added station is incorporated without
   code changes and its contribution is measured, not guaranteed positive.
   Do not declare a full-suite count unless the exact runner and result are
   retained; localhost API tests require a runner permitted to bind loopback.

9. **P2 — Browser regression coverage is weaker than the backend contract
   coverage.** The Python suite contains 963 collected tests and strong
   contract tests, but there is no repeatable browser-level regression suite
   for the critical UI states: recovered background job, cancel transition,
   sensor-table station aggregation, stale scenario selection and 1/7-day
   control state.  Manual CDP testing has caught real defects in these paths
   before.

   **Fix:** add a small headless-browser smoke suite against `serve.py` with
   fixture scenario data and deterministic job status stubs.  It should check
   DOM state and console errors, not run a full SUMO demand build.  Keep the
   existing API/unit tests; this is the missing end-to-end seam.

### Performance and architecture

Measured performance work found the PFE's deliberately sequential
Gauss-Seidel update to be the dominant demand cost. Do not rewrite that solver
or reduce accuracy controls without a separate result-equivalence experiment.
The safe levers are complete input fingerprints, immutable candidate/network
caches, one process budget, isolated workspaces, reduced repeated I/O and
capturing required trajectory output during an existing seed run. Every
accepted speed-up requires semantic result comparison and a golden-case
before/after measurement.

#### Speed research 2026-07-18 (measured budget + ranked result-neutral levers)

Gustav's requirement: faster, with results that are NOT allowed to get
worse.  Every candidate below therefore has an equivalence argument and a
proof protocol (byte/semantic-digest comparison against a sequential run);
anything without one was rejected.  Measured time budget on the dev
machine, current deployed 2025-09-16 build:

| Stage | Measured | Notes |
| --- | --- | --- |
| run_scenario (3 seeds, whole day, audits, trajectories) | **13.8 s** | NOT a bottleneck; vehroute parse 0.6 s, JSON writes ~0 s |
| Demand rebuild, cache-hit (PFE stage) | **~173 s** | solve 73 s (already parallel) + route publish 34.5 s + per-variant integer/purpose/report ~65 s (SEQUENTIAL) |
| Candidate generation, cache MISS only | 40–90 s | duarouter + generation; cache hits are ~0.1 s |
| Full LOSO (6 folds) | ~13–15 min | folds SEQUENTIAL; prepare_calibration recomputed identically per fold |

Ranked levers (largest first, all result-neutral by construction):

1. **Parallelize the three variants' post-solve stage** (integer repair +
   purpose allocation + route/agents publish in write_calibration_report):
   independent inputs and output files, no RNG in the path — expected
   −60–70 s of the 173 s on every recalibration.  Proof: byte-identical
   calibrated{,_v1,_v2}.rou.xml + .agents.json + fit reports vs sequential.
2. **LOSO: hoist prepare_calibration out of the fold loop** (the six folds
   rebuild the identical shape pool six times) **and run 2–3 folds
   concurrently** (fully independent read-only inputs, per-fold output
   files, report assembled in sorted station order).  Expected wall
   ~15 min → ~5–7 min.  Proof: identical loso_report.json.
3. **Cache-miss candidate path**: duarouter `--routing-threads N` (SUMO
   docs: per-vehicle routing, deterministic given seed/weights) and
   `--xml-validation never` on our self-generated XML.  −10–30 s, only on
   parameter/date changes.
4. serve.py recalibration inherits (1): ~6 min → ~4–4.5 min cache-hit.

Measured and REJECTED as not worth it: vehroute/JSON parsing optimizations
(0.6 s), further meso flags (--no-step-log/--no-warnings already set).
`--seed-workers >1` for run_scenario was ALSO listed here as "not worth it"
on the early single-day numbers; that rationale is SUPERSEDED — the v4–v6
campaigns measured it as a large, result-preserving speed-up (43.8% baseline,
40.8% closure) and it was rejected for a different, harder reason: the closure
whole-window arm still misses the 10-second gate. See "Seed-parallel campaign
line — measured and closed" in Phase 7 for the final decision.  FORBIDDEN by
the results-must-not-change constraint: numba fastmath, micro `--threads`
(nondeterministic ordering), any solver approximation or tolerance
loosening.  Protocol for every implementation:
tools/benchmark_speed.py before/after + semantic digest + one golden-case
rebuild comparison, per the P2 register row.

**Implementation 2026-07-21 (levers 1, 3, 4 — triggered by the first
multi-week closure-envelope builds).**  A real 11-day envelope build
measured the publish stage as the DOMINANT cost at that scale, far beyond
the 2026-07-18 single-day numbers: 154 min total, of which the three
variants' serial route publishing took 110 min (~37 min each) while the
parallel interval solve took 44 min.

- **Lever 1 done, memory-gated:** `run_pfe_variants_flat_parallel` now
  publishes the three variants through a fork pool sized by
  `_publish_worker_budget()` — one worker per parent-RSS-sized slice of
  60% of machine RAM, else the previously proven serial path.  The
  serial fallback exists because forked publishers can each hold a full
  copy of the parent's shape/solution state (the reason publishing was
  serialized in the first place); the gate makes that a measured
  condition instead of a permanent worst-case assumption.  Result
  identity is by construction (same worker function, disjoint staged
  files, same validate-then-flip publication gate) and covered by tests
  including a real fork-pool vs serial byte comparison.  Expected: big
  envelope builds ~154 min → ~85 min; serve.py recalibration inherits
  this automatically (lever 4).
- **Lever 3 done:** duarouter now runs with `--routing-threads` (≤8) and
  `--xml-validation never` on our self-generated trip XML.  Proven on
  real project data: route bodies byte-identical to a single-threaded
  run (only the header comment's timestamp/echoed options differ) at
  2.2x routing speed.
- **Lever 2 (LOSO hoist + concurrent folds) deliberately deferred:** it
  is not on the monthly-search critical path, and its required proof
  (identical loso_report.json before/after) cannot be run honestly while
  a closure-envelope search occupies the shared `sumo/` directory —
  LOSO would read a closure envelope's candidate pool and fight the
  search for cores.  Do it after the active search completes, with the
  live release restored.

Compatibility note: demand `build_key`s are content-addressed over the
DemandBuildSpec (never source code), and both changes are output-
identical, so archives built before/after this landing mix safely inside
one monthly release.  A search already running picks the fixes up from
its NEXT envelope build (each build is a fresh subprocess); the build in
flight at edit time finishes on the old serial path.

### Active quality register

| Priority | Improvement | Completion evidence |
| --- | --- | --- |
| P0 | Enforce SensorRegistry validity and approved snap identity | Active/accepted station, reviewed directed snap, bearing/distance check and registry/network hashes in every demand build |
| P0 | Verify final SUMO sensor output, not only PFE input fit | Frozen output-fit artifact with correct station aggregation; normal publication fails closed on missing/stale/failed fit |
| P0 | Eliminate purpose-route incompatibility | Zero diagnostic across q10/q50/q90 plus no worse held-out/temporal result |
| P0 | Freeze a normal, closure and micro-signal golden release | Complete health/provenance records, semantic hashes, final-output fit and tested rollback |
| P0 | Make synthetic signal phases explicit and mechanically safe | Versioned `SignalPlan` JSON plus TSFS-informed phase/clearance certificate is emitted per signal condition; city configuration remains evidence-bound |
| P1 | Preserve semantic q10/q50/q90 identity in every scenario | No filename-derived provenance; normal releases cover every declared variant and closure audits retain targets |
| P1 | Separate numerical audit values from map-display rounding | Per-seed integers and raw ensemble mean used for fit; rounded flow only for rendering |
| P1 | Prove the value of every new sensor | `sensor_contribution.py` emits coverage/confidence/LOSO/placement evidence; real before/after artifacts remain required |
| P1 | Make closure advice robust | Access/detour, integrity, paired uncertainty, queue-proxy and no-viable-closure gates are evaluated before ranking |
| P1 | Make multi-day studies continuous and calendar-correct | Active seven-day golden release passes per-day PFE/output/health, boundary, bounded real-vehicle trajectory and publication gates; exact 3/4/5-day builds pass and day 6 is explicitly covered inside the week |
| P1 | Complete study identity across API and UI | Active study exposes exact build, ScenarioSpec, job and validation artifact |
| P1 | Import reviewed road structure | NVDB/OSM mapping audit with provenance and no stable-ID drift |
| P2 | City-configure one signal corridor | Imported plan plus independent turn/travel-time validation |
| P2 | Add browser regression coverage for job and audit states | Deterministic headless smoke suite covers start/recover/cancel, scenario switch and sensor presentation |
| P2 | Keep documentation and release claims executable | Dated artifacts back every stated metric; no stale release/test-status assertion |
| P2 | Improve speed only with proof | Repeated benchmark improvement with unchanged semantic digest |

## Consolidation Coverage and Legacy Labels

The deleted review files are not lost work. Their actionable content is kept
in this plan at the following canonical locations:

| Former material | Canonical location here | Current handling |
| --- | --- | --- |
| Full code and simulation audit | Foundations, quality register and Phases 0-7 | Resolved defects remain permanent gates; unresolved risks have a named owner and acceptance gate |
| Destination, purpose and sensor-endpoint research | Consolidated Engineering Findings and Phase 3 | Prevent endpoint-biased trips; complete purpose-compatible allocation before making purpose claims |
| Speed and robustness implementation plan | Phase 0 and Phase 7 | Accept only result-preserving speed work measured against golden cases |
| Multi-day, closure-timing and signal execution plan | Phase 3, Phase 4 and Phase 5 | Treat time ranges, closures and signals as one versioned study, not unrelated commands |
| Product and sensor-growth roadmap | Practical Development Roadmap and Phases 1-2/6 | Add evidence, provenance and user workflow before expanding scope |

Some source comments, tests, manifests and old run artifacts still carry
historic labels such as `A-D`, `E-K`, `P0`, or `SIM-P1`. They remain useful for
Git-history traceability only; they are not current task identifiers. For
future work, use the phases in this document: historical hygiene/performance
labels map to Phase 7, multi-day labels to Phase 3, closure labels to Phase 4,
signal labels to Phase 5, release/health labels to the completed foundations
plus Phase 0, and demand-science labels to Phase 3. Do not create a new plan
or a new implementation branch from a legacy label.

## Decision

This is the best path forward for the current project:

1. Keep the normal citywide simulation as calibrated, mesoscopic SUMO.
2. Make every normal run, closure run, closure recommendation, and signal
   study consume one exact versioned scenario definition.
3. Make sensor intake data-driven and prove the contribution of each new
   sensor instead of assuming that more data is automatically better.
4. Treat signal optimization as a phase-plan problem, not a collection of
   independent lamps. Use microscopic SUMO only where signal behavior must be
   judged.
5. Publish an answer only when its evidence and provenance are sufficient.

One engine cannot honestly provide both fast citywide flow estimates and
physical per-lane signal behavior. The correct product is one application
with two computation levels:

```text
SensorRegistry + versioned network
             |
             v
Calibrated DemandBuild (one build ID)
             |
             v
ScenarioSpec: normal case or exact closure schedule
             |
             v
Citywide mesoscopic SUMO
        |                         |
        v                         v
Closure decision engine     Signal study input: arrivals, reroutes,
                              affected controller set
                                      |
                                      v
                         Bounded microscopic signal evaluation
```

The user sees one coherent study and one result. Internally, the system uses
the least expensive model that can support each claim.

## Why This Order Is Necessary

The project already has strong foundations:

- Citywide mesoscopic simulation is fast enough for normal days and closure
  studies, with calibrated demand, uncertainty variants, health telemetry,
  build fingerprints, and scenario publication gates.
- Time-windowed road closures and a closure-time screener already exist.
- A signal experiment framework exists for normal and closed-road cases.
- The web application already has focused workspaces rather than a single
  overloaded screen.

There are also foundation gaps that should be resolved before adding more
heuristics:

- New sensor direction metadata historically lived in `build_data.py`
  (`SENSOR_MEASURED_DIRECTION`); it is now sourced from the validated
  `data_in/sensors.json` registry. The hard-won verification workflow (check
  the city's trafikmängder catalogue FIRST; the delivered "Total" label was
  wrong for 4 of 5 sensors) is now represented by explicit registry fields,
  not only by prose. The 2026-07-16 audit found that several of those fields
  are not yet enforced at build time: active dates, quality status and
  approved directed snap identity must become a fail-closed gate before the
  registry can be called fully adopted.
- Structural products can be stale after a sensor change if cache identity
  does not include the sensor registry and all relevant inputs.
- The closure-time feature defaults to a proxy-selected subset of windows for
  fast exploration. `--exhaustive` now evaluates every feasible window; only
  that mode may claim that it searched the global feasible set.
- The ordinary signal baseline is generated by `netconvert --tls.guess`.
  It is not a real Gothenburg controller plan, so synthetic timing results
  must not be presented as operational instructions.
- Legacy signal requests default to 07:00-09:00, but spec-driven signal
  studies now take their measurement window and closure interval from the
  shared ScenarioSpec. A selected time-windowed closure therefore cannot
  silently be optimized for a different period.
- The latest demand diagnostics record purpose-route incompatibility. Until
  this is resolved, trip-purpose labels are useful diagnostics, not proof of
  the real purpose of every simulated vehicle.

Already in place — do NOT rebuild these, extend them (verified against the
working tree 2026-07-15; spot re-verified 2026-07-16 — publish gates, run
registry and benchmark harness confirmed present):

- Trajectory reconciliation is complete: `final_route()` reads rerouted
  vehicles from `<routeDistribution>`, unfinished vehicles park visibly,
  and the artifact withholds itself below 98% source-file integrity. Any
  deliberately non-drawable path remains visible in the published
  `displayed_share` diagnostic rather than being silently lost.
- Health telemetry fails closed (a missing per-seed statistics file flags
  the build), and the E2 publisher refuses any flagged baseline.
- Build-ID equality and per-variant (q10/q50/q90) fit gating in the
  publish gate are implemented in the current working tree
  (`validate_staged_scenarios`); the canonical fingerprint and candidate
  cache implementations now live under `traffic_sim/` and are included in
  invalidation keys.
- An immutable run registry (`runs/<id>/` manifests, `latest_*` pointers,
  `/api/jobs` durable records with orphan reconciliation) exists — the
  release structure in Phase 0/1 must be an EXTENSION of it, never a
  second registry.
- The assembled per-build validation report (`web/data/validation.json` +
  the 🛡 panel) exists; Phase 6's study view builds on it.
- A results-preserving speed benchmark harness exists
  (`make benchmark-speed`, semantic hashes).
- Security hardening is done: mutating endpoints are POST-only with an
  Origin-based CSRF guard, CSP without inline scripts, reflected-XSS fix.

## Product Outcome

The finished program should provide four connected outputs from the same
release:

| User task | Input | Output | Claim level |
| --- | --- | --- | --- |
| Normal traffic | Date, historical or forecast source | Citywide 15-minute flow, representative vehicles, confidence | Calibrated near sensors; prior-driven where unmeasured |
| Road closure | Exact road directions and time window | Rerouted citywide flow, delay, access loss, confidence | Fast mesoscopic incident study |
| Best closure time | Closure requirement and permitted windows | Least disruptive feasible schedule, alternatives, uncertainty | Simulation-backed decision support |
| Signal timing | Normal or closure ScenarioSpec plus SignalPlan | Green/red seconds per phase, cycle, offset, queues, comparison to baseline | Synthetic experiment or city-configured recommendation, stated explicitly |

## Practical Development Roadmap

This section and the numbered Phases later in the document describe the SAME
work from two angles — read them this way, or their numbering will mislead:

- **Stages (this section)** are the product narrative: what the user can
  trust, in which order, and why.
- **Phases 0-7 (below)** are the engineering work-packages with acceptance
  gates. The **canonical execution order is the numbered task list** in
  "Recommended Implementation Order" at the end. Since 2026-07-16 that list
  interleaves the phases (registry, variant-identity and output-fit gates
  run before the Phase 0 freeze), so it is no longer simply the phase
  order; each task there names the phase whose acceptance gate governs it.

Mapping, so no executor has to reconstruct it:

| Stage | Built from phases |
| --- | --- |
| 1 Normal baseline trusted | 0 (freeze) + 3 (demand realism) |
| 2 Closure as incident study | 1 (ScenarioSpec) + 4 (decision engine) |
| 3 Signals on closure traffic | 5 (synthetic study, steps 1-4) |
| 4 Sensors prove their value | 1 (SensorRegistry) + 2 |
| 5 Physical network inputs | 3 (network realism) |
| 6 One city-configured corridor | 5 (steps 5-6, blocked on city data) |
| 7 Scale under gates | 7 |

A later stage may use an earlier stage's artifact only after that artifact
has passed its stated gate.

### Stage 1: Make normal traffic the trusted baseline

**Do now, without new external data.** Enforce the SensorRegistry and reviewed
directed snaps, then verify final SUMO output against frozen sensor targets at
the correct physical-station aggregation. Finish purpose-compatible route
allocation, retain exact time-of-day and day-type demand, and rerun temporal
holdout plus leave-one-sensor-out validation. Keep the existing structural
checks for route length, route diversity, onward distance after a sensor, and
near-sensor destinations. Freeze one normal golden day only after all q10/
q50/q90 variants pass their own input-fit, final-output-fit, structure and
health gates.

**Why first:** a closure or signal optimizer can only be as credible as the
normal traffic it compares against.

**Exit condition:** the baseline has a content-addressed build ID, an approved
sensor-snap manifest, a validated normal scenario, a reproducible validation
report with final sensor output fit, and no purpose-level claim when purpose
compatibility fails.

### Stage 2: Make road-closure simulation a proper incident study

**Do next, using the trusted normal build.** Every closure must be one exact
ScenarioSpec: directed edge IDs, active start/end, duration, permitted-access
exceptions, analysis window, demand build, network build, seed set, and
direction-variant mapping. Use the same seed/variant pairs for the normal
baseline and the closure so differences are caused by the closure rather than
Monte Carlo noise.

For each closure, the engine must:

1. verify that the selected road directions and closure interval are exactly
   what SUMO receives;
2. check topology before simulation, including detour existence, access loss,
   and whether an affected movement can become stranded;
3. reroute traffic through the full city graph, not only the visible map;
4. measure closed-edge leakage, dropped/truncated vehicles, teleports,
   unfinished vehicles, added distance, paired delay, throughput and queue/
   spillback proxy;
5. reject a superficially fast result when it obtained that result by losing
   access or dropping vehicles;
6. publish the changed flows, route consequences, uncertainty and explicit
   health gates alongside the map animation.

Use mesoscopic SUMO citywide for speed and coverage. Use a bounded
microscopic component only around the affected junctions when queue, lane,
roundabout or signal claims are needed. Do not turn the whole city
microscopic.

**Exit condition:** a closure result is reproducible, has no integrity or
health failure, and clearly distinguishes "lower delay" from "least harmful
overall". A closure-time recommendation uses `--exhaustive` for a bounded
candidate set, or explicitly says that it is only a screened subset.

### Stage 3: Optimize signals for the actual closure traffic

**Works now as a synthetic experiment.** Take the exact closure ScenarioSpec
from Stage 2, use its actual rerouted arrivals, and optimise only the legal
phase structure. The optimizer may change cycle, phase green budget, legal
yellow/all-red/red-yellow and offset; it may not create new simultaneous
greens. Compare candidate and baseline with identical warm-up, measurement,
drain, seeds and demand variants.

Publish numeric timings per controller/link and a paired delay comparison.
The candidate is rejected when it harms a hard safety/access/health gate,
creates spillback, or is not robust across uncertainty variants. While the
phase structure is generated from SUMO, label it `synthetic`; it is an
experiment, not an instruction to operate a city controller.

**Exit condition:** every timing result identifies its SignalPlan,
ScenarioSpec, timing window, provenance, safety status and comparison.

### Stage 4: Let every new sensor make a measured difference

**Do continuously as sensors arrive.** Add data through `data_in/` and the
SensorRegistry, never through a source-code constant. Validate station ID,
raw columns, timestamp coverage, count units, directional semantics,
coordinate CRS, bearing, snap distance, counterpart direction and active
period before calibration. A sensor must overlap the study date; future-only
data cannot validate a historical 2025 simulation.

After each addition validate its active period, quality status, approved snap,
bearing and network-specific snap distance; then rebuild features, direction
splits, observability, bounds, priors, forecast inputs, demand and validation.
Publish a contribution report:
the new measured edges, coverage, confidence change, holdout recovery,
affected closure corridors, and `improved`, `neutral`, or `insufficient
evidence`. Prefer new directional counters on signal approaches and detour
routes, where they can change a closure/signal decision rather than merely
duplicate an existing count.

**Exit condition:** no sensor can enter calibration without a validated
registry record, and its contribution is measured rather than assumed.

### Stage 5: Improve physical network inputs without inventing observations

Use public sources such as NVDB only for documented road structure: speed
limits, lanes, road class and prohibited directions. Reconcile them against
the stable edge-ID mapping, record imported/defaulted/manual provenance in
the network audit, and repair only reviewed mismatches. Do not calibrate
driving speed, lane changing or queue discharge from speed limits alone.

**Exit condition:** every network value used by SUMO is traceable to OSM,
NVDB, a reviewed override, or a declared fallback.

### Stage 6: Upgrade one corridor with city-provided evidence

When data access becomes possible, choose one signal corridor affected by a
realistic closure. Import its real controller plan, phase/movement mapping,
conflict and clearance matrices, pedestrian/cycle constraints, detector and
transit-priority rules, offsets, approach/turn counts and matching travel
times. Map and validate the package against the SUMO network before using it.

This upgrades that corridor from `synthetic` to `city-configured`; it does not
silently make the rest of the city equally accurate. Use it to validate the
synthetic model and decide whether its assumptions are useful elsewhere.

**Exit condition:** one corridor can reproduce its real signal plan and has
independent arrival/travel-time evidence for normal and closure evaluation.

### Stage 7: Scale only after the evidence gates pass

Expand sensor coverage, controller imports and closure scope incrementally.
Preserve performance by caching immutable fingerprinted artifacts, using a
measured process budget, and retaining citywide meso as the default. Every
speed claim requires a golden-case before/after measurement and semantic
result comparison. Never gain speed by dropping seeds, uncertainty variants,
rerouter coverage or solver work.

### What the user can do at each stage

| Stage reached | User-visible capability | Honest limitation |
| --- | --- | --- |
| 1 | Simulate normal traffic with calibrated sensor evidence and confidence | Unmeasured streets remain prior-driven |
| 2 | Close one or more roads and compare rerouted delay, access and confidence | Queue detail is limited outside micro study areas |
| 3 | See green/red numbers for normal or closure traffic | Synthetic controller phases until city plans are imported |
| 4 | Add a sensor through data/registry and see its measured benefit | More sensors help where they add independent information |
| 5 | Trust that speeds/lanes/turn rules trace to a named source | Free-flow structure only — never observed driving behaviour |
| 6 | Receive city-configured timings for one validated corridor | Does not validate every other junction automatically |
| 7 | Larger sensor sets and more corridors at the same trust level | Growth is gated — each expansion re-proves its gates |

## Non-Negotiable Rules

- Keep stable edge IDs, WGS84, absolute time, `null != 0`, and the browser
  `flowAt(edgeId, t)` seam.
- Every artifact must carry the exact demand build ID, network fingerprint,
  random seeds, direction variants, tool version, and source provenance.
- Do not make a run faster by reducing seeds, variants, solver iterations,
  rerouter coverage, or mesoscopic fidelity.
- A sensor count is measured evidence. A learned prior, mathematical bound,
  and simulated result must remain distinguishable in the UI and output.
- A failed or incomplete job must never replace the currently published
  release.
- A signal result may only be called a recommendation when its plan provenance
  and safety gates permit that claim.

## Phase 0: Freeze a Reference Release

**Purpose:** Establish one reproducible baseline before changing behavior.

### Work

1. Commit the already validated accuracy, robustness, and speed work as one
   named release.
2. Freeze three golden cases under that release — pinned to the exact
   inputs the repo already validates against, so the baseline is the
   system's own proven state rather than a new arbitrary one:
   - normal historical full day: **2025-09-16** (the structural reference
     date; the LOSO baseline, the PFE benchmark fixture, and every
     structure-gate number on record were measured against it);
   - a known-detour full-day closure: **Skånegatan two-edge closure**
     (60786979_3575001205_0 + 1455801464_18241874_0 — the closure whose
     rerouting, truncation, and leak behaviour is already documented);
   - one bounded microscopic signal smoke case.
3. Record semantic hashes, run time, peak memory, build ID, network hash,
   route artifacts, input and final-output sensor fit, approved-snap manifest,
   variant coverage, seed health, closure integrity, and trajectory
   reconciliation.
4. Keep the golden artifacts and their manifest separate from normal pytest
   timing tests. Build the release directory as an extension of the
   EXISTING `runs/` registry (same manifest conventions, same atomic
   `latest` pointer mechanism) — two parallel registries is how artifacts
   get separated from their provenance again.
5. The release pointer must be reversible: rolling back to the previous
   golden release is one pointer flip, exercised once as part of this
   phase (an untested rollback is not a rollback).

### Acceptance gate

- The full test suite passes.
- Each golden case has a complete health record and no publication gate
  failure.
- The normal golden case has frozen sensor inputs, approved sensor snaps and
  a passing final SUMO output-fit artifact; it is not a legacy reconstructed
  audit.
- A refactor intended to preserve results matches the semantic baseline.

## Phase 1: Shared Versioned Contracts

**Purpose:** Prevent normal, closure, and signal paths from using different
definitions of the same study.

### New artifacts

#### `SensorRegistry`

Store it as the versioned machine-readable file `data_in/sensors.json`. It
must contain at least:

```text
sensor_id
active_from, active_to
measurement_semantics: directional | two_way_total
measured_bearing or permitted_bearings
coordinates and coordinate_reference_system
source and source_file identifier
snap_status, approved_edge_ids, snap_distance_m
catalogue_verification: status, date, verifier   # trafikmängder catalogue
quality_status and notes
```

The registry, not a Python constant, becomes the source of truth for sensor
direction semantics (it absorbs `SENSOR_MEASURED_DIRECTION`). Manual
overrides remain possible, but must be explicit registry records with a
reason and reviewer marker. `catalogue_verification` encodes the project's
hardest-won intake lesson as a field instead of folklore: the delivered
"Total" label was wrong for 4 of 5 sensors and was only caught by checking
the city's own trafikmängder catalogue — a sensor whose semantics have not
been verified against the catalogue must not reach calibration.

The registry is not valid merely because its JSON parses.  For the exact
study interval and network fingerprint, intake must require: an active period
covering the data, `quality_status: accepted`, a reviewed snap status, one or
two explicitly approved directed edge IDs consistent with the measurement
semantics, matching bearing, and a recorded snap distance within the agreed
limit.  The network build writes a resolved-snap artifact; demand consumes
that artifact instead of silently trusting a fresh automatic snap.  This lets
OSM evolve without silently moving a real counter.

#### `ScenarioSpec`

One versioned object must be consumed by `run_scenario.py`,
`suggest_closure_time.py`, `signal_optimize.py`, `signal_closure_combine.py`,
`serve.py`, and the browser:

```text
scenario_id
demand_build_id
network_build_id
start_time, end_time
closures: [ClosureSpec]
simulation_mode
seed_set and demand_variant mapping
analysis_window: warmup_s, measure_start, measure_end, drain_s   # optional
objective_profile
signal_plan_id or no_signal_plan
```

`ClosureSpec` contains directed edge IDs, active start/end time, closure type,
and any permitted-access exceptions. No feature may recreate a closure from a
different fixed time window.

`analysis_window` exists because the accuracy review found signal studies
that started with an empty network at 07:00 and stopped abruptly: any
bounded evaluation must state its warm-up, its measured period, and its
drain explicitly, IN the spec — not as per-tool constants — so a
07:00-09:00 signal request can never silently mean "green splits from
07:00-08:00, offsets from the whole day, evaluated over two cold hours".

#### `SignalPlan`

This is the only input accepted by a signal study:

```text
signal_plan_id, network_build_id, provenance
controller/TLS mapping, link-index mapping
movement and conflict definitions
phase states and compatible movements
cycle, green, yellow, all-red, red-yellow, offset
min/max timing constraints
pedestrian, cycle, transit, emergency, and detector rules
day type and active time range
```

Until a verified city plan exists, the provenance must be `synthetic` and the
UI must describe the result as an experiment, not an operational instruction.

#### `DecisionResult`

The closure and signal tools should return a shared result envelope with the
input IDs, alternatives tested, objective metrics, uncertainty, gates,
provenance, and a machine-readable recommendation status.

### Implementation rules

1. Use dataclasses or typed validation functions at every file/API boundary.
2. Include all schemas and all sensor metadata in content fingerprints.
3. Reject a build if an artifact required by its job type is absent: a registry
   entry/snap approval where sensor calibration is used, a route artifact where
   routes are consumed, a SignalPlan for a signal study, or a matching build ID
   whenever artifacts are combined.
4. When `n_variants=3`, derive the required q50/q10/q90 coverage from demand
   metadata and reject a normal ScenarioSpec that maps every seed to one
   variant.  A reduced mapping is diagnostic-only and cannot publish a normal
   release.
5. Keep backward-compatible CLI shims while migrating callers one by one.

### Acceptance gate

- A normal, closure, and signal job loaded from the same ScenarioSpec report
  identical date/window/closure identity.
- A selected 13:00-15:00 closure cannot accidentally produce a 07:00-09:00
  signal study.
- A stale observability, bounds, prior, or signal artifact is rejected rather
  than reused.
- A pending, inactive, unapproved or changed sensor snap is rejected before
  it can affect a calibration target.
- A normal uncertainty build cannot publish unless it includes every declared
  demand variant and records the exact seed-to-variant mapping.

## Phase 2: Sensor Growth That Proves Its Value

**Purpose:** Let the city add sensors without source-code edits and make the
accuracy benefit visible.

### Intake workflow

1. Validate raw CSV columns, timestamps, count units, active period, missing
   quarters, duplicate records, impossible negative values, and station IDs.
2. Validate every registry entry against the network: distance to geometry,
   direction/bearing, two-way counterpart when applicable, edge uniqueness,
   and lane/road-class plausibility.
3. Fail closed for an unknown sensor or ambiguous directional meaning. A warning
   is not enough because an incorrectly snapped count corrupts calibration.
4. Rebuild features, direction split, observability, bounds, corridor priors,
   assignment priors, forecast inputs, demand, baseline, and validation from
   the new release fingerprint.
5. Preserve the old release until the replacement satisfies all gates.

### Sensor contribution report

For every added sensor, publish:

- its data-quality and snap report;
- measured edges and time coverage;
- bounds narrowed by the sensor;
- leave-one-sensor-out and temporal holdout results before and after addition;
- confidence reduction by edge and by incident-relevant corridor;
- forecast impact once sufficient history exists;
- a clear `improved`, `neutral`, or `insufficient evidence` conclusion.

The LOSO mechanism already exists (`validate_sim.py`, calibrating each fold
with the exact deployed constraint set) — this phase reuses it per added
sensor rather than building new machinery; the temporal holdout is the new
piece. One documented interpretation rule carries over from the G1
investigation: a fold ratio near zero can measure the sensor's
INFORMATIONAL ISOLATION (nothing else constrains its corridor — sensor
1076 sits at 0.05 for exactly this reason, proven by a controlled pre-fix
rerun), not model error. The contribution report must therefore always
pair a recovery number with the sensor's isolation context before
concluding `improved` or `neutral`.

More sensors should improve the estimate where they add independent information.
The program must not falsely promise that one poorly located or low-quality
sensor improves every road in the city.

### Sensor placement guide

Rank prospective locations by expected information gain, combining:

- wide observability bounds;
- low current confidence;
- network connectivity and corridor coverage;
- closure-critical detour corridors;
- complementarity with existing sensors;
- practical station data quality and directional observability.

### Acceptance gate

- A new station can be added through data and registry metadata only.
- Its addition forces a new fingerprinted build.
- The map shows both the added measurement and its actual confidence impact.
- The generated `network.geojson` and demand manifest name the same reviewed
  directed snap, bearing, active period and registry hash.

## Phase 3: Improve Normal Citywide Realism

**Purpose:** Improve what the default simulation means before making more
decisions from it.

### Demand and validation work

1. DONE and guarded (verified 2026-07-16): the exact-day departure shape is
   aggregated per physical station, not per directed edge — `real_day_shape`
   counts a duplicated two-way Total once, and would sum genuinely different
   directional arrays instead of discarding one (regression test
   `test_genuinely_directional_values_are_summed_once_per_station`).
2. Calibrate and validate the final SUMO edge-entry output against the frozen
   target before interpreting a normal run as sensor-delivering.  Preserve
   per-seed integers and raw ensemble values separately from map rounding;
   compare station 107 only at its physical two-way total.  Diagnose entry-
   time residuals before introducing the bounded output-feedback correction
   described above.
3. Make purpose compatible with the selected route instance. The proper fix is
   a purpose-aware route allocation or a purpose-stratified PFE formulation,
   benchmarked against the current solver. Do not simply relabel incompatible
   routes after solving. (The current length-aware post-solve allocation
   preserves P(length|purpose) and the exact purpose×time mix but current
   q10/q50/q90 diagnostics report at least one provenance-incompatible
   allocation in all 96 quarters. Do not quote a vehicle-share percentage
   until a versioned artifact calculates it; this item must drive the
   incompatibility diagnostic to zero. The H2 benchmark fixture exists
   precisely so a solver-formulation change here cannot silently alter
   results.)
4. Surface purpose compatibility in the validation report and block claims
   about purpose-specific behavior when the diagnostic fails.
5. Rerun leave-one-sensor-out validation and temporal holdouts after each
   substantive demand change, and update the recorded honest baseline
   (currently min 0.05 / median 0.78 / max 1.95, measured 2026-07-13 —
   quoting any older number is a documentation bug). Sensor GEH is
   calibration fit, not independent validation.
6. Keep structure gates for trip length, near-sensor destinations, onward
   distance after the last sensor, route diversity, and unserviceable counts.

### Multi-day simulation

The trusted units are now one complete local calendar day and continuous
ranges through seven days. A multi-day study must not be implemented by
concatenating independent daily outputs or by silently resetting the network
at midnight. The active 2025-09-16→23 golden release proves that contract for
the maximum supported range; exact 3-, 4- and 5-day builds also passed, while
day 6 is explicitly gated inside the seven-day study.

That rule remains the default and the only continuous-traffic claim. A separate
user-authorized planning approximation now exists under the explicit contract
`independent_daily_reset_v1`. It records the reset in search, schedule, backend
and result identity; keeps downloaded date-specific demand and the full
production recovery window for every daily unit; and may never be relabelled as
continuous evidence. Its purpose is long-range road-work timing where the user
accepts negligible cross-work-day carryover. Exact daily results are cached and
summed only by matched variant/seed identity. A work sequence advances through
consecutive eligible work dates, skipping deselected weekdays and blackouts;
the exact selected dates are part of the immutable schedule identity. Any
caller that omits the policy continues to receive the continuous behaviour
described below.

1. `ScenarioSpec` and demand metadata carry an explicit local-date range,
   time zone, ordered analysis windows and the exact source selected for each
   day. Internally use monotonic simulation seconds; retain ISO datetimes with
   offsets for artifact identity and browser display.
2. Produce one monotonically departed route set across the entire range.
   Vehicles still active at midnight remain accounted for into the next day;
   no vehicle is dropped merely because its departure date changes.
3. Preserve the correct day-of-week, holiday and forecast/historical profile
   for every 15-minute interval. The known DST gaps remain `null`/explicit
   gaps, never invented zero traffic or a duplicated hour.
4. Calibrate and health-check each date and direction variant separately, then
   publish one range-level manifest that links all daily diagnostics. Do not
   let a good first day hide a failed later day.
5. Aggregate flows by absolute timestamp and day, retain boundary-vehicle,
   unfinished and route-error accounting, and make trajectory export opt-in
   or sampled for multi-day runs so the browser and disk footprint stay
   bounded.
6. Add cancellation, disk-budget and process-budget tests before permitting a
   multi-day API request. A longer study must not block ordinary one-day work
   or overwrite its artifacts.

**Acceptance gate:** the frozen continuous normal case has monotonic time,
correct local calendar labels, per-day q10/q50/q90 input and final-output fit
plus health, explicit midnight carry-over accounting, no missing interval
silently read as zero, bounded real-vehicle browser trajectories, and a
range-level manifest. The active seven-day release satisfies this gate.

### Network realism work

1. Build a source-to-SUMO network audit sidecar for speed, lane direction,
   turn lanes, turn restrictions, priority, traffic-signal membership, and
   roundabout membership.
2. Retain source provenance for each value: imported, defaulted, or manually
   reviewed.
3. Repair only reviewed network issues while preserving the stable edge-ID
   mapping layer.
4. Use OSM/default speeds as free-flow constraints. Do not tune vehicle
   behavior, lane changing, or speed factors without held-out speed/travel-time
   observations.

### Confidence work

Replace a single unexplained confidence number with components for measured
evidence, mathematical bounds, held-out error, demand uncertainty, and
Monte Carlo stability. Keep the simple map presentation, but allow the user
to inspect why an edge is uncertain.

### Acceptance gate

- All q10/q50/q90 variants pass their own input-fit, final-output-fit,
  structural, and health gates.
- Purpose incompatibility is zero or explicitly blocks purpose-level claims.
- The new release is no worse on frozen temporal and held-out validation.

## Phase 4: Make Road-Closure Advice a Real Decision Engine

**Purpose:** Answer "when should this road be closed?" as a constrained,
auditable decision rather than a low-flow guess.

### Final design decision (frozen 2026-07-18)

Version 1 optimizes a **traffic closure schedule**, not construction
productivity and not permit compliance. Its precise promise is:

> Within the dates and daily hours allowed by the user, find the simulated
> full-road-closure schedule that has the smallest robust traffic impact.

The road is closed during one contiguous work interval per selected calendar
day, at exactly the same local clock time every day. It is open between work
shifts. The selected days are consecutive calendar dates; a disallowed
weekday or blackout date invalidates the whole candidate rather than being
silently skipped.

The duration input means **minimum total work time required**. Version 1 uses
the explicit assumption that one scheduled work minute requires one minute
of road closure: the road is closed for the complete work shift and opens
between shifts. Setup, teardown and pauses must be included by the user in
the required work time when they consume part of the shift. An optional
per-shift overhead/productivity model is deliberately deferred until there
is real construction evidence.

Version 1 supports:

- one connected worksite/corridor made from one or more directed SUMO edges;
- the same closure interval for every selected direction;
- forecast demand and a permitted date range;
- minimum total required work minutes;
- maximum consecutive closure days, initially 1--7;
- an earliest start and latest end daily band, including an overnight band;
- allowed weekdays and explicit blackout dates;
- one 15-minute-aligned contiguous interval per day;
- full motor-vehicle closure only.

It does not silently accept lane-only closures, access exceptions, disjoint
worksites, changing daily work hours, split shifts, non-consecutive workdays,
or effective-work-hour claims. Those require later explicit contracts and
validation.

### Canonical inputs and generated schedules

Create one immutable `ClosureSearchSpec` in `traffic_sim/core/`:

```text
search_id and content_key
directed_edges
source and demand_build_id
permitted_date_start, permitted_date_end
timezone = Europe/Stockholm
dst_policy = exclude_transition_dates
required_work_minutes
max_consecutive_start_days
permitted_daily_band
allowed_weekdays
blackout_dates
same_daily_window = true
resolution_minutes = 15
closure_type = full
duration_basis = required_work_time
work_to_closure_assumption = one_to_one
objective_profile
policy_status = user_supplied_unverified
```

For each possible day count `n`, the generator uses
`ceil(required_work_minutes / (15*n))` quarters per day. It enumerates
every feasible same-time daily window and records the resulting rounding
overshoot. A `ClosureSchedule` contains the exact dated intervals,
start/end clock time, day count, required and scheduled work minutes, actual
closed minutes, and overshoot. Under the version-1 one-to-one assumption,
scheduled work minutes and actual closed minutes are identical. Fewer shifts
and less rounding overshoot are tie-breakers only; they may never defeat a
meaningfully better traffic result.

An overnight interval occupies both calendar dates. The complete interval,
including its after-midnight part, must remain inside the permitted range and
must not touch a blackout or disallowed date. Until the forecast contract is
fully timezone-aware, Swedish daylight-saving transition dates are rejected
rather than interpreted ambiguously.

All selected edges must be connected in the underlying undirected road graph.
Repeated closures of the same edge are valid only when their intervals do
not overlap. `ScenarioSpec` validation must therefore change from "edge may
appear once" to "same edge may appear in multiple non-overlapping
intervals". Simultaneous selected edges are emitted together in each SUMO
rerouter interval; one interval is emitted for every workday.

### Simulation envelope: warm-up, continuous days, and recovery

Every finalist is one continuous multi-day simulation. Days must never be
simulated independently and added together, because vehicles and congestion
can carry into the next shift.

The simulation envelope is separate from the workday count:

1. warm up before the first closure using a duration derived from the
   baseline p95/p99 trip duration plus a safety margin;
2. measure from the first closure through all work shifts;
3. keep simulating after the final shift until the affected network has
   returned near its matched baseline for a sustained interval;
4. apply a bounded recovery cap; if traffic has not recovered at the cap,
   mark the candidate ineligible as `congestion_not_dissipated`.

This may require eight or nine calendar days for a seven-day closure. The
current 1--7-day demand contract must therefore be extended and
resource-tested before the UI can offer seven workdays. The engine must fail
closed instead of clipping the warm-up, overnight interval, or recovery tail.

A warmed baseline state may be cached once per unique demand/date block and
branched for candidates, including SUMO's random-number state. This
optimization is allowed only after an equivalence test proves that a
save/load branch produces the same decision metrics as an uninterrupted run.
The cache key includes all inputs, network and demand hashes, code
fingerprint, SUMO version and platform; a mismatch invalidates the cache.

### Two-stage monthly search

Running SUMO for every 15-minute start time across a month would be wasteful.
The search therefore has two explicitly different evidence levels.

**Stage A -- screening proxy**

Enumerate every legal schedule and rank it cheaply from the forecast demand,
the structural 96-slot assignment field, traffic on the closed edges, and
estimated load/reserve on plausible detour corridors. Missing values remain
missing and never become zero. The proxy produces a rank and screening
features, not invented seconds of delay and not the final recommendation.

The shortlist is stratified so adjacent times on one date cannot consume it:
include the best overall schedules, the best for every feasible day count,
distinct date blocks, and validation controls. Low spatial support, a missing
assignment prior, or out-of-domain road features automatically enlarge the
SUMO shortlist or withhold a recommendation.

Before release, validate the proxy out of sample on representative held-out
roads, road classes, sensor distances, topologies, durations and day types.
Freeze the validation set before tuning. Report:

- Spearman rank correlation as a diagnostic;
- recall of the exhaustive SUMO winner in the shortlist;
- shortlist regret: the difference between the exhaustive SUMO optimum and
  the best shortlisted SUMO candidate;
- failure/disqualification recall.

The initial gate is at least 90% winner recall, p90 normalized shortlist
regret at most 10%, and median Spearman at least 0.6. If it fails, increase
the shortlist or fall back to exhaustive SUMO for a bounded search; the UI
must not claim a global best from an unvalidated proxy.

**Stage B -- matched SUMO finalists**

Group finalists by demand/date envelope so their calibrated demand, matched
baseline and validated warm state can be reused. Begin with one heavy SUMO
worker. Enable parallel workers only after a resource benchmark proves a real
speed gain, semantic equivalence and safe peak memory.

### SUMO evidence and hard gates

Use citywide mesoscopic SUMO for screening finalists because this project has
validated it for 15-minute edge-flow studies, not for exact lane queues or
spillback geometry. The primary comparison is the paired change in total
SUMO `timeLoss` against the same no-closure baseline. Also retain validated
edge travel time, waiting time, entered/left counts, throughput, added
distance and affected/rerouted vehicles where available.

The current mesoscopic `halting`/network-wide queue value is diagnostic only.
It must never be a primary ranking objective or be presented as an exact
queue length. For the top two or three candidates, run bounded microscopic
confirmation when the worksite is near a signal, roundabout or known
bottleneck, when the halting diagnostic is high, or when the candidates are
too close to distinguish. If microscopic confirmation is not available,
state `queue_detail_not_assessed`.

Reject a candidate before ranking when any of these gates fails:

- no valid detour or unacceptable access loss;
- trips dropped in a way that can make low flow look artificially good;
- closed-edge leakage outside the declared tolerance;
- teleport, route-error, stranded, unfinished or simulation-health failure;
- closure interval mismatch, midnight clipping or stale demand/baseline;
- recovery not complete before the bounded drain cap;
- incompatible scenario, network, demand, variant or cache provenance.

Flow reduction is never evidence of success by itself.

### Uncertainty, repetitions, and the winner

The q10, q50 and q90 direction-demand variants represent different
epistemic demand assumptions. They are not independent random seeds and must
never be pooled as if they were.

For every variant:

1. run baseline and candidate with common matched seeds;
2. start with four random seeds;
3. use paired differences and add repetitions adaptively until the
   pre-registered 95% precision target is met or a maximum run cap is
   reached;
4. preserve the separate variant result and confidence interval.

The initial statistical tolerance is 5% at 95% confidence; an absolute
tolerance floor and maximum repetition count are frozen from the golden
benchmark before release. Multiple finalist comparisons use a Holm-adjusted
or simultaneous procedure, rather than repeated uncorrected pairwise tests.
At the repetition cap, an unresolved comparison is reported as inconclusive.

The frozen v1 primary objective is the smallest **worst-variant upper 95%
confidence bound of paired total time-loss increase**. It remains available
only for compatibility with evidence and policy identities produced under
that contract.

The provisional v2 primary objective is deterministic `closure_cost_v1`:
field-wise worst added vehicle-hours across q10/q50/q90, followed
lexicographically by added metres and affected vehicles. A vehicle with no
legal detour disqualifies the schedule. Pilot and finalist stages must use the
same explicit objective, and missing disruption evidence fails closed. Paired
time loss, throughput/access integrity and queue/halting remain health or
diagnostic evidence; they do not silently replace the v2 ranking key. An exact
vehicle-hour tie may use the secondary keys, while a declared nonzero
vehicle-hour equivalence band produces an honest practical tie.

**Rolling multi-month period comparison, 2026-08-10.** The work-period API/UI
now accepts a date interval spanning multiple months and compares rolling
periods of up to 90 permitted workdays. A period may cross ISO-week and month
boundaries; `rolling_period_v1` groups the compact response by candidate start
date but preserves the exact winning schedule, end date and workday count.
Rolling comparison always uses `exact_equal_daily_v1`: every selected workday
has the same start and end time, and period lengths that cannot divide the
requested work into equal 15-minute-aligned shifts are excluded. The balanced
policy remains available only to explicit non-rolling internal workflows. This
keeps allocation count bounded through 90 days; the older continuous model
retains its 21-day ceiling.

Every candidate is still evaluated by exact date-specific independent daily
SUMO units and the v2 closure-cost objective. This is provisional analysis,
not a new release claim: the golden-policy and untouched-heldout gates remain
closed.

**Scaling and validation implementation plan, 2026-08-10.** The next
implementation sequence is specified in
`docs/plans/CLOSURE_SEARCH_SCALING_AND_VALIDATION_PLAN_2026-08-10.md`. It
freezes the measured 10,000-daily-unit limitation, adds an exact read-only
preflight, replaces full parent/unit materialization with versioned streaming
ledgers, moves deterministic disruption cost before SUMO, and proves a
cost-ordered exact pilot scan against the current exhaustive result before
activation. Persistent workers/libsumo are a later measured optimization;
demand/work-zone calibration and independent-reset validation are separate
evidence tracks. No existing policy, held-out record, or release claim is
changed by the plan.

**Objective-alignment benchmark checkpoint, 2026-08-10.** The pinned isolated
v1 diagnostic for policy v2 completed on the original immutable golden archive
in 318.18 s and resumed in 0.0 s with the identical result hash. Both pilot and
final records name the closure-cost methods, and q10/q50/q90 disruption
evidence is present. It is deliberately not a freeze benchmark: 06:00 and
06:15 were cheaper on vehicle-hours but failed the teleport hard gate, leaving
06:30 as the only viable schedule. The next benchmark must select cases by
pre-outcome structural criteria that yield multiple health-viable candidates;
only then can a nonzero equivalence tolerance be justified before the new
untouched held-out campaign is frozen.

**Closure-scaling review checkpoint, 2026-08-11.** PR C's five-repeat
Darwin/arm64 run now passes the 64 MiB process-total gate at 25.30 MiB for the
720-hour case. PR D's current process-free provider and runner path are
field-identical on the pinned real q10/q50/q90 golden archive, and current cost
fields/order match the preserved benchmark. PR E's named golden replay returns
the same `ready` status and selected ID as exhaustive, but saves 0 of 3
verifications because the benchmark has only one health-viable candidate. The
records are `validation/closure_search_streaming_v1.json` and
`validation/closure_cost_ordering_golden_v1.json`; both are diagnostic and
open no release claim. The next priority is no longer to build demand—the dev
machine already has a calibrated archive library—but to connect the persistent
cost-ordered cursor/provider to actual product execution and then freeze a
pre-outcome discriminating benchmark with several health-viable candidates.
Policy v3, UI/global-best wording and held-out adoption remain closed.

**Benchmark provenance and the v2 timeout, 2026-08-11.** The v2 benchmark
failed with `sumo timed out after 300s (seed 1000)` and zero completed pilots.
The cause is visible in the frozen registration without running anything: the
selected case is a SINGLE work date (2027-03-22, 07:00-15:00, 300 required
minutes) whose independent daily unit declares a one-day envelope
`2027-03-22T00:00:00 -> 2027-03-23T00:00:00`, while the demand archive it
resolves to (`5ac74750843384b3`) is the canonical three-day
previous/current/next build starting 2027-03-21, `n_intervals: 288`. SUMO ran to
the archive's far end, so a five-hour closure was observed by simulating
seventy-two hours — roughly 3x the necessary work per observation. `adf765b`
corrects exactly this by bounding an independent-daily cold run to its declared
envelope.

That correction also exposed a provenance hole: it changed `monthly_sumo.py` and
`suggest_closure_time.py`, and neither was in the benchmark registration's
source seal, so a v2 registration could not have reported the runtime change as
drift. Registration schema v3 seals every module on the arms' real import path
and binds the caller's outcome path instead of the tool's default. It also
leaves `validation/closure_cost_ordering_golden_v1.json` with a stale bound
digest for `monthly_sumo.py` — a real consequence to resolve deliberately, not
by editing the frozen record.

**Cost-first execution and the remaining measurements, 2026-08-11.** Cost
ordering is now the product execution path, not a post-hoc replay:
`cost_ordered_execution.py` prices every candidate before any SUMO process
exists, persists a content-keyed cost ledger and a cursor after every
verification, refuses a resume whose ledger key or verified prefix does not
match, and reconciles the pre-SUMO cost against the post-SUMO evidence
field-by-field. The durable cursor mirrors the scan rather than hooking into
it, because `cost_ordered_search.py` is bound byte-for-byte by the golden
record; a fault-injection test proves the divergence check fires.

The discriminating benchmark is registered but not run:
`tools/cost_ordered_benchmark.py --preregister` selects structurally, freezes
eleven thresholds including a strictly positive saving minimum, and reports
`archives_available: 0` in this checkout — measured, not assumed. Held-out
validation therefore did not run and no held-out evidence exists.

Two measurements did complete. PR H's harness
(`tools/measure_independent_vs_continuous.py`) examined all 84 pre-registered
cases — 24 unsupported by contract, 25 unpairable, 35 blocked on demand, 0
measured — and found a THIRD contract divergence: 11 of the 35 "pairable" cases
search different candidate spaces in both directions, because
`equal_daily_rounded_v1` can serve the work requirement in fewer days than
`exact_equal_daily_v1` can express, and because the two policies walk different
date axes. `tools/preflight_libsumo.py` corrected PR G's blocker: eclipse-sumo
1.27.1 is installed and ships libsumo's C++ library and headers with no Python
binding, so the previously recorded fix would not have worked. Policy v3,
global-best and UI claims remain closed; nothing was activated.

**Evidence-pipeline review and completion, 2026-08-11.** Re-reviewing the
product integration found one defect that voided its main claim: every resume
of a real cost-ordered search failed closed, because `IndependentDailyRunner`
suppresses per-parent pilot artifacts and the resume then demanded evidence
that had deliberately never been written. Compaction is now disabled whenever a
cost source is present — cost-first execution simulates only the boundary set,
so the file count it was invented to bound does not arise. `execution_record`
was also dead code; the saving, the stop proof and the final cursor are now
published and surfaced in the result. A third suspected defect turned out not
to be one and is pinned as such.

`--run` is implemented: bindings re-hashed, both arms under one workspace lock
into separate roots, built from the CLI's own helpers via
`tools/product_arm.py`. The field-by-field cost gate compares the published
cost LEDGER — every candidate, priced before any SUMO — rather than the two
candidates both arms simulated; the stop proof is re-derived against its own
vocabulary and can fail; fault injection is part of the run, and skipping it
fails the restart gate. Benchmark cases are now DISCOVERED from the archive
library's metadata around dates that exist and roads that survive their own
closure, and a discovery that finds nothing refuses to freeze.

Measured here on in-memory arms: 45 exhaustive pilots against 2 cost-ordered,
43 saved, identical selected IDs and final decision, valid band-exhausted stop
proof, restart equivalent. The real benchmark, and therefore held-out
validation, remain unrun: this container has no calibrated archive library.
Policy v3, global-best and UI claims are unchanged and closed.

**Real evidence review, 2026-08-11.** The environment-local conclusion above
is superseded for the primary dev root. Review found that the first archive
discovery still selected non-runnable data: it equated work dates with archive
start dates and ignored the product's multi-day warm-up envelope. Discovery now
uses the product resolver and full archive validation, permits a single work
date when it supplies a discriminating 9–13 start-time set, and binds the exact
active network/data root. Frozen v2 selected 13 schedules on 2027-03-22 with
demand build `5ac74750843384b3`. Its first exhaustive SUMO observation reached
the unchanged 300 s timeout, so
`validation/cost_ordered_benchmark_outcome_v2.json` records
`failed_execution`; all gates remain false and held-out did not run. The next
work is profiling that bound timeout without raising it, not claiming demand is
absent.

The same review made runtime errors durable benchmark outcomes, corrected the
shared lock to the registered data root, and fixed PR G's read-only preflight
for packaged binaries and `.dylib`/`.dll` libraries. Linux v1 remains frozen;
Darwin v2 finds SUMO 1.27.1 and `lib/libsumocpp.dylib`, but still no Python
binding. This changes no worker cap or activation decision.

**Runtime correction, 2026-08-11.** The first timeout was not caused solely by
the three-day archive size. For `independent_daily_reset_v1`, the cold runner
was simulating the reusable archive's prior/current/next-day tail even when
the declared unit envelope ended earlier. It now starts at the envelope
midnight, stops at the recovery boundary with `flush=0`, and includes that
window in the matched-baseline cache identity. Continuous runs retain their
archive-start horizon and carryover semantics. A SUMO timeout or failed
process is recorded as a candidate-local hard failure rather than aborting the
search. This is a source correction only: frozen v2 remains `failed_execution`,
the diagnostic rerun still found a later 07:15/q10 candidate above 300 s, and
no benchmark, held-out or release gate opened. A new v3 registration is
required before measuring the corrected source.

### Confidence, policy, and wording

Do not compress confidence into a fake probability. The result exposes:

- forecast temporal error at sensors;
- LOSO/spatial support for the worksite and detours;
- proxy held-out coverage and regret;
- spread across q10/q50/q90 demand variants;
- paired seed confidence intervals;
- simulation and closure-integrity health.

Low spatial confidence or out-of-domain proxy use may still produce
exploratory results, but suppresses the strong `recommended` label.

The program says **trafficmässigt bäst inom angivna tider**. It does not say
that a schedule is permitted, safe for workers, compliant with a TA plan, or
acceptable for public transport, events, emergency access or construction
noise. Allowed weekdays and blackout dates are user-supplied and stored as
`policy_status = user_supplied_unverified`. Relevant warnings are shown, but
policy cannot become a hard automatic gate until authoritative machine-
readable inputs exist.

### Persistence, API, and UI

Each search writes an isolated immutable artifact under
`runs/closure-search/<search_id>/`, including the input spec, candidate
ledger, proxy evidence, exact simulated schedules, matched baselines,
statistics, health reports, hashes and final `DecisionResult`. It must never
reuse the old shared suggestion file or overwrite the active golden release.

The API follows the existing start/status/cancel job pattern. Progress stages
are enumerate, screen, build/cache demand, warm baseline, simulate finalists,
confirm, and publish. Cancellation removes only isolated scratch data; a
failed or cancelled search leaves the active release untouched.

The UI shows the exact interpretation before starting:

```text
Road closed 08:00--14:00 on 5 consecutive permitted days
Minimum work requested: 30 h; scheduled work/closure: 30 h
Screened schedules: N; SUMO-verified finalists: K
```

The result separates proxy-screened candidates from SUMO-simulated
candidates, shows every exact interval and each evidence component, and can
load only the exact winning/tied `ScenarioSpec` back into the forecast
simulation.

### Build order and stop gates

1. Implement pure contracts and calendar enumeration; stop until month-end,
   leap-day, overnight, blackout, weekday, rounding and DST tests pass.
2. Implement repeated SUMO intervals plus warm-up and adaptive drain; stop
   until continuous 2-, 7-, and extended-envelope resource tests pass.
3. Add isolated workspaces, baseline/warm-state caching and save/load
   equivalence; stop on any provenance or semantic mismatch.
4. Build and validate the monthly proxy out of sample; do not expose it in
   the UI until recall/regret gates pass.
5. Add paired robust finalist statistics and conditional microscopic
   confirmation; stop until tie, inconclusive and no-viable cases work.
6. Add asynchronous API, status, cancellation and restart recovery.
7. Add the forecast UI and exact-schedule handoff.
8. Freeze a golden monthly search, benchmark wall time/RSS/disk, run browser
   recovery tests, and publish only if every previous gate passes.

**Implementation status 2026-07-18:** Steps 1, 2, 3 and 5 are internally
complete. Step 5's synthetic stop cases cover unique winner, practical tie,
adaptive/inconclusive, repetition-cap, no-viable, incompatible provenance,
conditional micro confirmation and missing queue detail. Existing SUMO
evidence also reproduces a real `no_viable` case; old eligible evidence is
three-seed and variant-collapsed, so it correctly cannot be promoted into the
new decision contract. Step 4 is implemented, but its held-out release gate
failed; it is therefore complete as an evidence-producing stage and
deliberately not released to the UI. Consequently, Step 5's decision engine
is also internal only: no API/UI/global-best claim is permitted. The
absolute precision floor, practical-equivalence tolerance and repetition cap
remain required explicit policy inputs and must be frozen against the named
golden monthly benchmark before release rather than hidden in code defaults.

Step 1 added the canonical `ClosureSearchSpec`, `DailyTimeBand`,
`ClosureInterval` and `ClosureSchedule` contracts in
`traffic_sim/core/contracts.py`, plus the pure deterministic generator,
exact `ClosureSpec` expansion, DST exclusion and connected-worksite
validator in `traffic_sim/core/closure_calendar.py`.

Step 2 now lets one directed edge close in multiple non-overlapping
intervals, groups simultaneous multi-edge closures into one SUMO rerouter
interval, and proves with a real SUMO network that the edge reopens between
two work shifts. `traffic_sim/simulation/envelope.py` derives a continuous
full-day envelope from the baseline trip-duration p99 plus margin, rejects
DST transitions and envelopes beyond the validated nine-day ceiling, and
evaluates sustained post-closure recovery against the matched no-closure
per-edge `timeLoss` series. The ordinary recalibration contract remains
limited to seven days; only an isolated `purpose=closure_envelope` build can
represent days eight and nine, and the generic recalibration API rejects
that internal purpose.

The resource gate uses the existing frozen two- and seven-day evidence plus
`tools/benchmark_nine_day_envelope.py`. The two-day computational run passed
in 25.34 s at 623,591,424 bytes RSS; the frozen seven-day acceptance passed
in 96.56 s at 1,959,968,768 bytes RSS. The isolated nine-day SUMO 1.27.1
proof ran 190,730 vehicles in 19.345 s at 208,715,776 bytes RSS, with every
vehicle loaded and inserted, none waiting or running at drain completion,
and zero teleports. Days eight and nine deliberately repeat frozen q50 days
one and two, so this proves continuity and resources only, not new
calibration. The focused integration gate passes 210 tests and the complete
project suite passed 1,088 tests with 20 expected skips at the step-2 gate.

Step 3 adds exclusive `runs/closure-search/<search_id>` workspaces in
`traffic_sim/simulation/search_workspace.py`. The exact search input and
every published artifact are hashed and ledgered; search IDs cannot be
reused, path traversal and overwrites fail closed, unledgered/tampered files
block success, failure preserves only its own diagnostic scratch, and
cancellation removes only that isolated scratch without touching an active
release.

`traffic_sim/simulation/warm_state_cache.py` adds two immutable caches: one
for SUMO warm states and one for matched no-closure baseline metrics. The
warm-state key covers the network and route bytes, demand build and variant,
seed, mode, warm-up boundary, any baseline additional inputs, mandatory code
hashes, Git commit, Python, the full SUMO version, platform, RNG-state flag
and state precision. Baseline identity additionally covers its exact
analysis window, affected-edge set, objective profile and metric schema.
Missing, changed, corrupt or incompatible provenance is a cache miss; an
invalid existing entry cannot be silently overwritten.

Cache publication requires a save/load equivalence certificate for the
exact warm-state identity and the fixed
`closure_decision_metrics_v1` schema. The cache itself enforces the policy:
`entered`, `left`, primary `timeLoss` and every other field are exact
(absolute tolerance zero); only supporting per-edge `travel_time_s` may
differ by at most one default SUMO simulation step (1 s). A caller cannot
substitute a looser global tolerance or another metric schema.

MONTHLY PAIRED WARM-STATE VALIDATION (LUNA-WARM-01..04, 2026-07-28). The
monthly backend has an opt-in, default-OFF warm branch plus a frozen paired
cold-versus-warm harness. Its first real execution (LUNA-WARM-03) FAILED, and
usefully: `combine_prefix_and_post_warm` refused to merge `max_queue_vehicles`
(prefix 0, post-warm 5) because a network-wide MAXIMUM is neither additive nor
something two segments agree on. The aggregate prefix object was the wrong
contract — it also conflated boundary-active trips with finished observations
and carried no prefix recovery buckets.
It is replaced by VERSIONED PREFIX EVIDENCE (`monthly_prefix_evidence_v1`):
completed-only prefix trip aggregates, prefix queue maximum, prefix counters and
prefix recovery buckets, stored inside the warm-state entry's atomic
digest-bound member set and re-verified on every restore. Legacy
`prefix_metrics`-only entries are a cache MISS, never repaired. Every
`DisruptionMetrics` field crosses the boundary by an explicit rule bound
mechanically to `dataclasses.fields`, so adding a production field without
deciding its semantics fails a focused test: disjoint accumulators sum,
end-state and candidate-route-only fields come from the post-warm segment,
`max_queue_vehicles` takes the maximum over MEASURED segments, and closure
throughput is post-closure with a fail-closed pre-closure invariant. Recovery
buckets are concatenated into one ordered, gap-free domain and never
synthesised. The bootstrap requests completed-only tripinfo
(`run_sumo(tripinfo_write_unfinished=False)`, default True everywhere else) so a
vehicle still driving at the snapshot is counted once, by the resumed run.
LUNA-WARM-05 EXECUTED v1 AND IT FAILED (2026-07-29). The first real
cold-versus-warm comparison. The warm branch genuinely ran at the frozen warm
point, and baseline metrics, feasibility, hard failures, recovery, the
concatenated bucket domain, truncation and provenance all matched the cold arm
EXACTLY — which is what made the three real differences legible:
`loaded` 84065 vs 85146 and `inserted` 84065 vs 85130; `closed_edge_throughput`
0 vs None; `total_time_loss_s` 558026.99 vs 558019.26 (-7.73 s on 558k).
Coverage was 1 of 3, because `run_candidate` stops at the first hard failure and
q10 hit `truncated_unreachable_vehicles`, so q50/q90 never ran. Nothing was
published.
LUNA-WARM-06 corrected three mechanisms and froze v2:
(i) SUMO's `loaded`/`inserted`/`teleport_total`/per-reason counters are
CUMULATIVE across a loaded state, so reconstruction takes the post-state value
and treats the prefix as a lower bound instead of summing — summing
double-counted every vehicle live at the snapshot, which is exactly the
+1081/+1065 shape;
(ii) the post-warm invoker now measures active-closure throughput from its own
edgeData with closed edges zero-filled, so measured zero stays distinct from
never-looked;
(iii) the candidate route is now filtered BEFORE any state lookup, audited
against the original by vehicle id/departure/route, and the snapshot is chosen
strictly before the earliest changed or dropped departure — a prefix simulated
from the unfiltered route is invalid for any vehicle the filtering touches.
Measured on the real archive, this closure changes 23-38 vehicles per variant
with the earliest affected departure around 24 900 s, so the old 24300 point
happened to be route-safe: route mutation was NOT what broke LUNA-WARM-05, and
the audit is a guarantee rather than a retrofit for that specific failure.
The validation harness now requests every frozen identity directly from the
production observation path, so one disqualified variant no longer hides the
other two; ordinary search keeps its fail-fast ordering unchanged.
LUNA-WARM-07 EXECUTED v2 AND IT FAILED — narrowly, and informatively
(2026-07-29). Coverage was 3 of 3, execution evidence complete, and 16 of 18
semantic groups matched EXACTLY. One group differed: the objective,
`total_time_loss_s`, with warm LOWER on every identity — q10 -7.73 s, q50
-80.62 s, q90 -138.97 s — increasing with demand. Warm was also slower on this
case (98.4 s vs 85.7 s), so no speedup is claimed.
The monotone-in-demand ordering is the diagnosis. A vehicle still driving at
the snapshot is BOUNDARY-ACTIVE: completed-only prefix tripinfo excludes it,
and after `--load-state` its tripinfo reports only post-boundary time loss, so
its pre-boundary delay is counted NOWHERE. Denser demand strands more such
vehicles. Completed-only tripinfo (LUNA-WARM-04) had removed double-counting
and replaced it with under-counting; neither aggregate is sufficient, because
the right answer depends on WHICH vehicles were airborne.
LUNA-WARM-08 fixes the accounting and freezes v3:
(i) `warm_state_boundary.py`'s `WarmPrefixController` owns ONE SUMO process and
its TraCI connection, captures a PER-VEHICLE ledger at exactly the saved step
and saves the state through that SAME connection, so the two cannot disagree
about which vehicles were in flight; it refuses outright if the simulation did
not land on the warm point, and always reaps the process. `traci`, `subprocess`
and `socket` are imported lazily inside its methods, so constructing one starts
nothing and every check stays process-free. The validation harness supplies a
real controller to the warm arm by default — without that the campaign silently
fell back to cold and could not test what it was frozen to test;
(ii) `monthly_prefix_evidence_v2` carries that ledger plus the per-vehicle
completed-trip map, and the objective is RECONCILED by vehicle identity: each
boundary vehicle contributes its post-warm trip plus its ledger offset exactly
once. Segment values stay RAW and are normalised ONCE per final per-vehicle
total — rounding halves separately loses 0.01 s on a vehicle accruing 1.005 s
either side of the snapshot. Raw in memory is not enough: the post-warm half
round-trips through a file SUMO writes at its reported precision. That residual
has no precision-based fix — `--precision` is global and perturbs recovery and
waiting semantics, and no finite precision guarantees exactness (proven for
2..12 decimals). Warm argv stays byte-identical to cold argv and the residual is
DECLARED in the manifest: at most one unit in the last reported place per
boundary vehicle, which can make the campaign fail rather than pass quietly. The completed map must agree
with its aggregates (count, total, disjoint from the active set) on write AND
read. Every invalid-warm-evidence path records a reason and returns None so the
unchanged cold arm runs — the guard is at the consuming boundary, so it covers
failures nobody predicted. Missing, duplicate or overlapping identities, malformed tripinfo, and
legacy v1 evidence all fail closed; v1 evidence is a cache MISS, never repaired
or reinterpreted;
(iii) the boundary schema and tripinfo precision are bound into the warm
identity by content — precision is a runtime parameter, so binding module bytes
alone would not catch a change from 2 to 3 decimals, which would silently alter
every reconstructed objective;
(iv) split diagnostics expose only BOUNDED facts (count, digest, reconciliation
totals), never the per-vehicle map, which would make the canonical payload grow
with traffic.
Reconciliation exactness is tested as a PROPERTY, not an example: randomised
splits reproduce the uninterrupted total exactly, and the v2 failure mode is
reproduced (120.0, losing 30.0) and fixed (150.0).
TRACI DISCOVERY FIXED, v8 FROZEN 2026-07-31 (LUNA-WARM-14/15). The v6 campaign
named the cause of every fallback: `No module named 'traci'`. Production imported
TraCI bare; it ships inside the SUMO installation at `<sumo_home>/tools/traci`.
Warming had therefore never started — warm_executions was 0 in v4, v5 and v6.
`runtime.resolve_traci()` now imports from the exact active home and proves the
module's origin; the controller resolves before launching anything; and the
harness runs the same resolver as a mandatory preflight before any artifact root
is created, so an unusable environment cannot consume an approved campaign again.
A fake-SUMO-tree regression exercises the real import machinery — the check that
was missing for three campaigns. One audit-guarded import-only probe of the
installed package confirmed the origin and required API; it is consumed evidence
and proves nothing about warming.
v7 carried that repair but was REJECTED in process-free review for binding an
incomplete regression set (it omitted `tests/test_warm_state_boundary.py` and
`tests/test_monthly_warm_state.py`, which could then have been weakened without
invalidating its key). It was never approved or executed, cost no campaign, and
is preserved byte-for-byte rather than repaired in place. v8 supersedes it with
the same rules and the complete binding, enforced at freeze time.
SUPERSEDED 2026-08-02: v9 was approved and EXECUTED once (LUNA-WARM-16). Warming
ran for the first time and the comparison failed with residual -7.73/-80.62/
-138.97 s — bit-identical to v2's, refuting the state-serialization hypothesis.
LUNA-WARM-22 localized it to 5/10/12 vehicles in flight across the warm point,
whose deltas sum to the residual exactly; most return with 0.0 accumulated time
loss while 99.99% of the population is unchanged. v12 (LUNA-WARM-23/24) then
bound and executed a selective `saved - restored` TraCI correction. All three
warm arms completed, but their save/restore ledgers were equal and the exact
7.730000004/80.620000002/138.970000003-second cold-minus-warm residual remained.
That refutes the correction; no cache was published.

V13 (LUNA-WARM-25) is grounded in the SUMO mesoscopic implementation rather than
another behavioral guess. Meso tripinfo outputs private
`MSDevice_Tripinfo::myMesoTimeLoss`; device save/load omit it; and TraCI
documents `getTimeLoss()` as accumulated loss. Frozen SUMO 1.27 mesoscopic
save/load evidence nevertheless shows it does not exactly reproduce the private
tripinfo accumulator across this boundary. The prefix therefore captures
only the exact active ID set on the state-writing connection and lets SUMO emit
high-precision unfinished tripinfo at normal close. Prefix and resumed private
accumulators are joined per identity, then each whole vehicle is rounded once to
production precision. The active-population digest is reconstructed from the
warm point and exact ledger IDs on every read, rather than accepted as a
self-consistent string. Prefix XML completion order is retained separately from
canonical identity storage, and resumed records continue that accumulator so
floating-point grouping cannot manufacture a mismatch. Completed,
boundary-active and post-boundary populations must be disjoint and exhaustive;
malformed, duplicate, missing, overlapping or unknown pre-boundary records cause
a recorded cold fallback. Warm-only global
precision is normalized per edge before recovery aggregation so supporting
metrics retain cold semantics. The obsolete keep-after-arrival terminal ledger
is removed, avoiding its memory/runtime cost. Cache identity advances to schema
2 and legacy entries fail closed as misses.

V13 executed once but its sandbox denied the localhost IPv4/TCP bind before all
three warm prefixes. The three fallback payloads matched cold exactly, but zero
valid warm executions means they prove neither equivalence nor speedup; no cache
was published. V14 preserves the v13 mechanism and physical experiment and adds
a mandatory bind-capability check before keyed-root inspection/creation. It is
frozen, unapproved and unexecuted. Warming stays default-OFF; after review, the
shortest remaining path is fresh exact-key approval and one frozen execution
with escalated socket permission. Further mechanism work is justified only if
that socket-capable warm run produces a real semantic mismatch.

v4 FROZEN 2026-07-30 (LUNA-WARM-09), REPLACING v3's REFUTED DESIGN.
Preserved-accumulator accounting: the objective is the completed-prefix aggregate
plus the resumed aggregate, each vehicle counted once and whole, with no
per-vehicle boundary offset. Prefix evidence is `monthly_prefix_evidence_v3`
carrying bounded snapshot facts rather than a ledger; v1/v2 evidence are cache
misses. Because aggregates are whole values, v3's ±0.01 s serialization residual
does not arise at all.
The prefix snapshot command now carries exactly one `--save-state.rng true` and
one `--save-state.precision 16`, derived from the cache constants the identity
records — v3 recorded both and applied neither, which is the current hypothesis
for the residual. `WarmPrefixController` snapshots at the exact step through one
process and connection, requires an observed zero exit, and reaps without masking
errors.
STILL UNPROVEN, and recorded as such in the manifest: LUNA-WARM-07's
−7.73/−80.62/−138.97 s gap is UNEXPLAINED. The default-serialization hypothesis is
what a campaign would test, and v4 states the condition that refutes it. Warming
is default-OFF, v4 is unapproved and unexecuted, and the one remaining gate is a
single fresh approved paired campaign.

MEASURED 2026-07-30 (LUNA-WARM-08 revision 3), AND IT REFUTES THE v3 PREMISE.
One approved non-campaign SUMO/TraCI diagnostic
(`tools/diagnose_warm_state_time_loss_semantics.py`, outcome at
`validation/warm_state_time_loss_semantics_v2_outcome`, with cold/prefix/resumed
return codes all observed as 0) asked whether SUMO's saved state preserves a
vehicle's `timeLoss` accumulator. It does. The earlier revision-2 run produced
the same numbers but never checked its processes' exit codes, so it was rejected
and rerun under enforcement rather than reinterpreted. On the
controlled fixture the restored vehicle reports 15.72 s immediately after
`--load-state` against a boundary capture of 15.7184 s, and the resumed
tripinfo reports 109.90 s — identical to the uninterrupted run, field for field,
not the 94.18 s a post-boundary-only segment would give.
So a resumed vehicle's tripinfo ALREADY carries its whole trip's time loss, and
v3's per-vehicle ledger offset would double count the pre-boundary delay. Two
things follow. First, v3's reconciliation rests on the opposite assumption and
cannot be adopted as written; its artifacts are left untouched pending a new
evidence-based decision, and selecting the replacement is separate work.
Second, the
original LUNA-WARM-07 gap is now UNEXPLAINED again: if resumed tripinfo is
complete, completed-only prefix plus resumed already sums correctly, so the
monotone −7.73 / −80.62 / −138.97 s shortfall has some other cause. It was only
ever consistent with boundary-active accounting, never proven to be it.
Limits of the measurement: one synthetic vehicle, one edge, no interacting
traffic, one SUMO version and platform, one snapshot instant. It says nothing
about a vehicle mid-junction, mid-lane-change, teleporting, or queued at the
snapshot.

HONEST BOUNDARY: this is process-free work. Passing tests and a fresh freeze
prove the ACCOUNTING is exhaustive and fail-closed; they prove NOTHING about
whether warm and cold agree under real SUMO, or about any speedup. v3 states
its own refutation condition in advance: if the objective still differs after
reconciliation, boundary-active accounting is NOT the cause and the campaign
fails honestly. The v1 and v2 keys are spent, their roots are preserved failed
evidence, and a fresh paired campaign needs a new task, a new frozen key and
explicit user approval.

The real small-network closure test proves that a closure introduced after
the warm state produces the same decision metrics in an uninterrupted,
freshly loaded and cache-restored branch. The production-scale proof in
`tools/validate_warm_state_equivalence.py` uses the frozen Gothenburg
seven-day q50 release with SUMO 1.27.1: four 15-minute intervals, 7,125
edges, 4,806 entered, 4,793 left and total `timeLoss` 27.33 s were exact in
all branches. Four of 28,500 supporting travel-time values differed, with a
maximum 0.23 s and signed total 0.04 s, inside the explicit one-step limit;
the difference is disclosed rather than mislabeled as bit-identical. The
restored matched baseline was identical to the stored evidence. The focused
step-3 gate passes 113 tests and the complete project suite passes 1,115
tests with 20 expected skips.

Step 4 adds the reusable proxy, projection and validation modules under
`traffic_sim/simulation/`, with `screen_monthly_closures.py` as an internal
screening CLI and `run_monthly_proxy_validation.py` as the resumable,
isolated SUMO validation runner. The proxy reports separate ranks/features
for closed-edge vehicle exposure, existing detour utilization/reserve and
post-diversion utilization. It never emits invented delay seconds. Missing
closed-edge data or a missing evaluable detour makes a schedule unscoreable;
low spatial/structural/domain support enlarges the shortlist and withholds a
recommendation.

The 2027 projection is explicit about its evidence boundary. Agent 1 directly
forecasts only seven measured directed sensor edges. Every other requested
edge comes from the fixed 96-slot structural hierarchy
`learned_direction_prior → corridor_prior → assignment_prior`, scaled per
quarter by the median station-level change from the real 2025-09-16
reference. Two-way sensor 107 is direction-split once before aggregation;
each physical station receives equal scale influence. Fewer than three valid
stations leaves the structural projection `null`, never zero. A real
July-2027 smoke search ranked 272 legal schedules in 2.2 s, selected 54
stratified SUMO finalists, labelled all road-domain evidence unvalidated and
kept both UI/global-best flags false.

The held-out manifest
`validation/monthly_proxy_manifest.json` was frozen before SUMO outcomes
with content key
`b3c2416d7b9a5b8784a30f756a96d37c915f0f0bccb734701aa7fefb9d0d53c1`.
It contains 12 cases and 140 exact schedules spanning five road classes,
three sensor-distance bands, three topology classes, four work-duration
classes and weekday/weekend/holiday/mixed periods. Every outcome requires an
exhaustive schedule set, matched three-seed baseline, SUMO/network/demand/
proxy hashes and explicit disqualifications. Partial evidence can never open
the release gate.

Nine cases (81 schedules) could be run entirely from immutable archived
demand without touching the active release. The observed diagnostic metrics
were winner recall 0.6667, p90 normalized shortlist regret 0.08168, median
Spearman 0.50, Spearman case coverage 0.50 and failure/disqualification
recall 0.6726. Three demand envelopes remain unbuilt, but the gate is already
mathematically decided: even if all three became ranking cases and recalled
their winners, the optimistic upper bound would be
`(4 + 3) / (6 + 3) = 0.7778`, below the required 0.90. The current proxy
therefore has a conclusive `fail`; the UI was not changed and may not claim a
global best.

Any proxy-v2 tuning must treat these outcomes as development evidence and
freeze a new untouched held-out set before release. The safe alternative is
to enlarge the SUMO shortlist or use bounded exhaustive SUMO while continuing
to withhold a global recommendation. Re-running or relabelling the same
frozen cases as new out-of-sample evidence is forbidden. The focused step-4
gate passes 59 tests; the complete project suite passes 1,148 tests with 20
expected skips.

**Step-4 recovery decision, frozen 2026-07-19.**  The v1 failure is not a
reason to fit new rank weights to the nine observed cases.  Inspection found
two different problems that must not be conflated:

1. one real safety bug: a legal candidate whose structural detour flow could
   not be scored was omitted even when the normal bounded-exhaustive policy
   had room to run every schedule;
2. two apparent winner misses were differences of about 4 s and 22 s in
   whole-network aggregate time loss, while their three-seed ranges spanned
   hundreds of seconds.  Calling either noisy median a unique exact winner is
   false precision, not evidence for a new proxy weight.

The shortlist contract is therefore `stratified_shortlist_v2` while the
unchanged analytical ranks remain `monthly_proxy_v1`.  For at most 120 legal
schedules, every candidate is selected for SUMO, including candidates with
no proxy rank.  Above that bound, unscoreable candidates are explicit SUMO
controls whenever the 240-candidate cap permits; any omitted unscoreable
candidate structurally withholds a recommendation.  Missing evidence is
never interpreted as poor traffic performance.

The researched release path is a three-fidelity ranking-and-selection
procedure:

1. use the zero-cost analytical proxy only to order and stratify candidates;
2. use matched mesoscopic SUMO pilot runs for the broad shortlist, preserving
   q10/q50/q90 identities and common random seeds;
3. spend adaptive repetitions only on the surviving finalists and decide
   them with `finalist_decision.py`'s worst-variant simultaneous bound.

This follows SUMO's documented division of labour: mesoscopic simulation is
intended for large urban areas and is much faster than microscopic
simulation, while microscopic confirmation is reserved for local
lane/signal/bottleneck questions
([SUMO mesoscopic model](https://sumo.dlr.de/docs/Simulation/Meso.html)).
It also follows simulation ranking-and-selection practice: the smallest
operationally meaningful separation must be pre-registered as an
indifference zone, common random numbers reduce comparison variance, and
inferior alternatives should be eliminated sequentially rather than giving
every option the final replication budget
([INFORMS simulation optimization tutorial](https://pubsonline.informs.org/doi/pdf/10.1287/educ.2013.0118?download=true)).

This decision does **not** reopen the UI gate.  Before a global-best claim,
the pilot/finalist policy, practical-equivalence tolerance, precision floor
and repetition cap are frozen against a named golden monthly benchmark; a
new untouched held-out set then measures practical-winner recall, regret and
failure recall.  The old v1 cases remain development diagnostics only.

**Implementation checkpoint 2026-07-19.**  The shortlist-v2 safety contract,
the fail-closed matched pilot selector, explicit per-replication
q10/q50/q90+seed records and identity-based baseline/candidate pairing are
implemented.  Pilot output is structurally `screening_only` and cannot carry
a winner.  Missing pilot pairs return `incomplete`; too many contenders
inside the frozen retention band return `capacity_exceeded`; every hard-gated
candidate returns `no_viable`.  The real v1 failure case
`tertiary-far-weekday-4h` now selects all 9/9 legal schedules despite having
0/9 scoreable proxy schedules.  The focused recovery gate passes 117 tests
and the full suite passes 1,190 tests with 20 expected skips.  The golden
policy freeze and new held-out SUMO outcomes remain the next stop gate.

**Runnable decision checkpoint 2026-07-19.**  The existing active-demand
closure-time path now returns the same fail-closed robust result categories
instead of choosing the minimum of three noisy medians.  It runs a matched
three-seed q50/q10/q90 pilot, removes candidates that fail closure-integrity
or health gates, then evaluates retained finalists with 12 matched SUMO
runs (four per variant).  Only `finalist_decision.py` may emit
`unique_winner`; the API and web UI otherwise show `tie`, `inconclusive`,
`no_viable`, or that the pilot could not advance.  Structured web requests
must be mesoscopic and carry the canonical 1000--1011 seed/variant mapping.

The frozen real smoke record
`validation/robust_closure_search_smoke_v1.json` closes the executable slice:
on the 2025-09-16 demand release and edge `26842526_96527131_0`, three
four-hour candidates were run in SUMO 1.27.1.  Two failed health/integrity
gates; 00:00--04:00 was the robust winner among the verified finalists after
12 matched runs, with q90 the worst demand variant and a 142.96 s
simultaneous upper bound.  The record deliberately retains
`global_best_claim_allowed=false`.  It validates the implementation and
result honesty, not the future monthly scheduling policy.

The next stop gate is unchanged: build the resumable multi-day monthly
orchestrator, freeze its pilot/finalist tolerances and repetition cap against
a named golden monthly benchmark, then evaluate once on a newly frozen
untouched held-out set.  Until that passes, the product may return “best
among SUMO-verified finalists” for the active demand window but may not claim
the globally best work period for a month.  This checkpoint passes 183
focused search/API tests and the complete suite passes 1,193 tests with 20
expected skips.

**Resumable monthly execution checkpoint 2026-07-19.**  The missing
orchestration layer is now implemented in
`traffic_sim/simulation/monthly_search.py`, with an archived-demand SUMO
backend in `monthly_sumo.py` and
`run_monthly_closure_search.py` as the stable root CLI.  The workspace
persists immutable policy, calendar ledger, screening, each pilot candidate,
pilot selection, cumulative adaptive finalist rounds, every robust decision,
complete input/runtime/source provenance and the terminal result.  A restart
loads completed candidate evidence instead of rerunning SUMO; a succeeded
search is idempotent.  A changed policy, backend/archive, semantic source
digest, malformed screening or non-canonical seed fails before it can
reinterpret an old result.

The implementation review corrected one prerequisite that the earlier pure
decision slice could not represent: candidates on different dates
necessarily have different no-closure traffic.  They may now use distinct
matched baseline IDs while sharing one study provenance. Candidates inside
the same date/envelope baseline group still must share the exact baseline
value and common variant/seed identity. This preserves paired comparisons
without incorrectly requiring July 5 and July 20 to have identical normal
traffic.

**Golden monthly policy v1 — PASS (internal), 2026-07-19.**  The tracked
policy in `validation/monthly_search_policy_v1.json` is frozen from the
diagnostic and confirmed by
`validation/golden_monthly_search_v1.json`: one pilot repetition per
q10/q50/q90 variant, 300 s pilot retention band, four initial finalist
repetitions, 600 s absolute precision floor, 300 s practical-equivalence
tolerance and at most 12 repetitions per variant.  The final portable golden
v6 ran three bounded-exhaustive four-hour windows on a real Gothenburg demand
archive in SUMO 1.27.1.  It completed in **211.42 s**, peak RSS
**427,819,008 bytes**, used one worker, and selected **06:30--10:30** as the
only viable window; 06:00 and 06:15 failed the teleport gate.  The finalist
met precision after q10/q50/q90 repetition counts **4/5/7**, below the cap.
The immutable workspace passed its ledger/hash verification, and an
idempotent completed-result reload required no new SUMO work.

This closes the CLI/orchestration and named golden-policy parts of the stop
gate, not the release gate.  The production backend currently accepts one
explicit successful demand archive and proves that all shortlisted envelopes
fit it. A real future-month search still needs a resolver that groups
schedules by their 1--9 day envelopes and builds/reuses every required
forecast demand archive.  After that, one newly frozen untouched monthly
held-out set must pass practical-winner recall, regret and failure recall.
The asynchronous API/status/cancel path, exact-schedule UI handoff and
browser recovery test also remain. Therefore the golden result deliberately
retains `global_best_claim_allowed=false` and `ui_exposure_allowed=false`.
The focused monthly execution gate passes 63 tests; the complete project
suite passes **1,212 tests with 20 expected skips**.

**Multi-envelope resolver + forecast smoke — 2026-07-19.**  The missing
resolver exists: `traffic_sim/simulation/monthly_demand.py` groups
shortlisted schedules by the exact `DemandBuildSpec` of their simulation
envelope, finds or builds one succeeded immutable archive per envelope,
freezes the mapping in a release manifest under
`runs/monthly-demand-releases/`, and routes each candidate to its matched
`ArchivedDemandSumoRunner`.  A pinned archive that changes after freezing
fails closed.  `validation/multi_envelope_forecast_smoke_v1.json` records a
real two-envelope 2027 forecast search (two demand builds + 28 SUMO runs in
489 s): 07-15 06:00--10:00 was the robust winner and the 07-22 candidate was
excluded by real teleport/throughput hard gates.  Claim boundary unchanged:
diagnostic smoke, `global_best_claim_allowed=false`.

**Async API + live-release protection checkpoint (2026-07-19, this
session).**  Build-order step 6 is implemented: `POST /api/monthly_search`
(body exactly `{"closure_search_spec": ...}`), `GET
/api/monthly_search/status` and `POST /api/cancel?kind=monthly` in
`serve.py`, sharing `_sim_lock` and the durable job records with the other
four simulation jobs.  The server forces the frozen golden policy file and
bounded-exhaustive screening; a browser cannot supply tolerances or a
proxy shortlist (the proxy stays failed/unreleased).  Status polling
surfaces the CLI child's own persisted workspace progress pointer, so any
tab sees the live phase; the curated "done" summary always carries the
result's `claim_boundary` verbatim.  Cancel kills the process group but the
workspace stays resumable — POSTing the same spec continues from completed
SUMO evidence.

Two defects found reviewing the resolver slice, both fixed with regression
tests:

1. **P0 — envelope demand builds clobbered the live release.**  The
   resolver's automatic `build_sumo_demand.py` runs write THROUGH the live
   `sumo/` demand products and `web/data/od_matrix.*`; the smoke left the
   deployed site silently calibrated for 2027-07-22 forecast (committed in
   9bb3e28).  `monthly_demand.py` now snapshots the runtime release product
   set before the first missing-envelope build and restores it
   byte-for-byte afterwards, on success and failure; the live release was
   repaired to the documented 2025-09-16 historical build
   (57e3fd904e32776bc481) from its immutable run archive, with
   scenarios/OD/validation verified coherent.
2. **P1 — archived routes could run on a different network.**  Demand
   archives carry no `net.net.xml`, so the runner's network check was
   vacuous.  It now enforces `demand_meta.json`'s
   `sensor_contract.network_sha256` against the active `sumo/net.net.xml`
   whenever the record exists.

Remaining before release: the new untouched monthly held-out set
(practical-winner recall, regret, failure recall), the step-7 forecast-UI
exact-schedule handoff, and the browser recovery test.  Until then the API
result keeps `global_best_claim_allowed=false` and the winner wording stays
"best among SUMO-verified finalists".  Complete suite after this
checkpoint: **1,237 tests with 20 expected skips**.

**Step-7 UI + evidence-level claim boundary (2026-07-19, same session).**
The claim boundary in `_final_result` is now evidence-level aware: bounded
exhaustive screening involves no proxy — every ranked candidate carries
real SUMO evidence, the same evidence level the released closure-time
feature already shows — so those results carry
`ui_exposure_allowed=true` with scope `sumo_verified_bounded_exhaustive`;
proxy-screened results stay unexposed, and `global_best_claim_allowed`
stays false in EVERY mode until the untouched held-out gate passes (the
pilot retention band is golden-frozen, not held-out validated).  The
result now also records `shortlisted_schedules` (every SUMO-verified
candidate's exact intervals) so readers can map robust statistics to real
dates without re-deriving the calendar.

The web app gained the "Bästa arbetsperiod" workspace: edge picking reused
from the closure flow, a multi-month date-range/daily-band/weekday/work-hours
form with rolling periods up to 90 workdays,
start + poll with the workspace's own persisted phase shown live, cancel
(kind=monthly, workspace stays resumable — the deterministic
form-content-derived `search_id` means re-running the same search resumes
its immutable workspace), an on-load running-job discovery, and a result
table showing every SUMO-verified schedule with its worst-variant robust
point/upper-95 deltas, hard-failure tags and honest wording ("bäst bland
SUMO-verifierade scheman inom angivna tider", never "globalt bäst").  The
exact-schedule handoff builds the loaded scenario from the schedule's OWN
intervals: if the live demand does not cover the schedule's dates it first
runs the ordinary recalibration pipeline (confirmed with the honest
~6 min/day cost), then runs a normal windowed multi-interval closure
ScenarioSpec through `/api/close` (the ScenarioSpec contract already
accepts multiple non-overlapping intervals per edge — verified).  Headless
Chrome/CDP smoke: page loads with zero console errors, the workspace
opens, weekday/source controls respond, and the run gate (no edges → no
POST) holds.  The full browser recovery test (reload mid-search) remains
open for step 8.  Complete suite: **1,238 tests with 20 expected skips**.

**Step-8 browser recovery test — PASS (2026-07-19, same session).**
`tools/browser_recovery_test.py` is a repeatable headless-Chrome/CDP test
of the exact incident shape this project shipped twice in 2026-07: a
long server job whose starting tab disappears.  It runs the real serve.py
handler/threading/status stack and the real web-app JS against a faked
monthly CLI (35 s, persisting the same workspace manifest phases the
status endpoint reads), and asserts four things: (1) a search started
from one page keeps running after that page reloads mid-job; (2) the
fresh page's on-load discovery re-attaches — run button locked to
"Söker…" and the live workspace phase surfaced from the persisted
manifest; (3) completion reaches the re-attached page, rendering the
result panel with the honest claim wording and the global-best
disclaimer; (4) controls return to idle.  Screenshot-verified.  A first
run of the test caught its own fixture racing a too-short fake job —
the mid-run window must exceed page load + reload + one 4 s poll tick.

The only remaining release gate for the monthly product is the untouched
monthly held-out set (practical-winner recall, regret, failure recall),
which is what keeps `global_best_claim_allowed=false`; bounded-exhaustive
results are UI-exposed with the restricted wording.

**Held-out v2 campaign — PASS (2026-07-20).**  The release gate has now
run and passed.  Method, frozen before outcomes (commit e4edb90):
`validation/monthly_proxy_manifest_v2.json` — 12 NEW cases, 104 exact
schedules, every edge disjoint from all 12 v1 edges, full strata
coverage, with the gate thresholds content-keyed INTO the manifest
(practical-winner recall ≥0.90 at the golden policy's frozen 300 s
practical-equivalence tolerance, p90 normalized shortlist regret ≤0.10,
failure recall ≥0.60; Spearman and strict exact-tie recall demoted to
reported diagnostics per the step-4 recovery decision — v1's two "missed
winners" were 4 s and 22 s on medians whose seed ranges spanned hundreds
of seconds).  Three missing demand envelopes (2027-12-24 holiday 1-day,
2027-07-15 3-day and 5-day) were built first, all 100% GEH<5 with zero
infeasible intervals, with the live release snapshotted/restored around
the builds (verified back on 2025-09-16/57e3fd90 afterwards).

**V5 EXECUTED AND FAILED; V6 FROZEN UNEXECUTED (LUNA-V5-02 / LUNA-V6-02,
2026-07-27).** The v5 campaign ran once and failed honestly: median objective
spread 0.0 across all five held-out edges, so `discriminating_case_coverage`
and `discriminating_practical_winner_recall` failed while practical-winner
recall, regret, failure recall and shortlist coverage passed. No gate record was
written and none was adopted. Root cause was the SELECTION, not the proxy: v5
chose edges structurally, with no pre-outcome signal for objective spread. V6
replaces that rule with `demand_exposure_v1`, which requires strictly positive
q10/q50/q90 route exposure in every frozen closure window and ranks candidates
by temporal variation, computed from the canonical archived demand bound by
exact path and five file hashes. V6 is frozen, UNEXECUTED and UNAPPROVED; its
archive designation is v6-local and does not repair the globally ambiguous
demand key. Demand exposure is a selection signal only and is NOT claimed to
guarantee a 300-second SUMO spread. Every case keeps its raw per-window
q10/q50/q90 counts and the schedule IDs they belong to, so the ranking is
recomputable from the frozen artifact alone rather than trusted; the freeze
tool has no overwrite flag and rolls back a failed publish. Adoption remains
default-closed: no gate record and no adoption certificate exist.

**AUDIT PASSED, ADOPTION REJECTED (LUNA-V4-04 concluded rejected;
LUNA-V5-01, 2026-07-27).** The v4 audit stands: the preserved evidence is
complete, identity-bound and reproduces its report and gate record
canonically. ADOPTION was rejected for whole-record integrity — a lone gate
record is self-certifying, so a byte edited inside it still validated
against itself. The tracked candidate was REMOVED; the product is in
bounded-exhaustive fail-closed mode with UI/global-best claims closed.
Adoption now needs a gate record AND a post-review adoption certificate
binding its exact bytes (contract:
`validation/monthly_gate_adoption_contract_v1.json`). V5 is FROZEN but
UNEXECUTED and UNAPPROVED: five cases, 75 schedules, edges disjoint from all
v1-v4 held-out edges, deterministic pre-outcome selection, v4 thresholds
unchanged; no v5 gate record or certificate exists. The caveats below still
apply to any future adoption — negative median Spearman (shortlister, NOT a
reliable ranker) and failure-disqualification recall only modestly above its
floor. Historical v4 detail follows.

**Superseded header (v4 adoption, now rejected):** The v2 result below is retained as history and is NOT the
active gate. The audited v4 record (campaign key `1505ecfb…`, root
`runs/closure-proxy-validation/1505ecfb…`, record SHA-256 `9ba2fa10…`) was
copied byte-for-byte to `validation/monthly_proxy_v4_gate.json` after the
LUNA-V4-03 audit reproduced its report and gate record canonically. Frozen
gate: 5/5 cases, 75 schedules, all seven checks pass — practical-winner recall
1.0, discriminating practical-winner recall 1.0, p90 normalized shortlist
regret 0.0, failure-disqualification recall 0.6819, discriminating case
fraction 0.6, ranking case fraction 1.0, all shortlists contain an eligible
candidate. LIMITATION: median Spearman is NEGATIVE (-0.371; -0.637 on
discriminating cases), so the proxy is adopted as a SHORTLISTER and explicitly
NOT as a reliable full ranker; Spearman remains a diagnostic under v4's
practical-winner gate. Claims stay bounded to SUMO-verified schedules within
the enumerated search space, and the loader fails closed on a missing,
altered, incomplete or earlier-campaign record.

Result (`validation/monthly_proxy_v2_gate.json`, evidence digests inside;
raw outcomes under `runs/closure-proxy-validation/dec211d4…/`): all 12
cases and 104 schedules completed exhaustively.  **Practical-winner
recall 1.0 (strict recall also 1.0), p90 regret 0.0, failure recall
0.867 (39/45 disqualified schedules caught), ranking coverage 7/12 —
every check passed.**  Honest composition: 5 of 7 ranking cases involved
genuine proxy pruning (shortlists of 4-6 of 9) and still recalled every
exhaustive winner with zero regret; 2 ranking cases were
unscoreable-fallback (shortlist-everything, trivially recalled — the
deployed safety behavior); 5 cases were failure-only, with real
infrastructure findings (closing those roads strands ~200-4,300 vehicles
per run).  Spearman was measurable in only 1 of 7 ranking cases (0.894)
— reported, not gated.

Consequence, wired fail-closed in `monthly_search.py`
(`load_passing_heldout_gate` + the evidence-aware claim boundary): with
the tracked passing record present, bounded-exhaustive results now carry
`global_best_claim_allowed=true` (scope: the enumerated search space),
and `monthly_proxy_v1`-screened results are UI-exposable with
`sumo_verified_monthly_shortlist_heldout_validated` scope.  Any missing,
failed or malformed record reverts every claim to the pre-release
boundary; a proxy version not covered by the record stays closed.  The
pre-registered release contract (golden-frozen policy + passing untouched
held-out set) is therefore satisfied and the claim language may say
**trafikmässigt bäst inom angivna tider** — still never permitted/safe/
TA-plan compliant, which remain user-supplied unverified policy.

### Final acceptance gate

- The displayed schedule is byte-for-byte derivable from the immutable
  schedule that SUMO ran.
- Same daily hours, consecutive dates, opening between shifts and requested
  total work time are all enforced by contract tests.
- Warm-up, overnight vehicle carryover and recovery cannot be truncated.
- Every eligible candidate uses matched baselines, seeds and separate demand
  variants from the same demand release.
- Proxy quality passes held-out recall/regret gates or the system clearly
  falls back/withholds a global recommendation.
- Mesoscopic diagnostics are never mislabeled as exact queues.
- Access, integrity, simulation health and policy limitations cannot be
  hidden by a favorable delay score.
- The system can return a unique robust winner, a tie, an inconclusive
  result, no viable closure, or insufficient evidence without fabricating
  certainty.

## Phase 5: Build a Defensible Signal Optimizer

**Purpose:** Produce actual green/red values in a way that preserves safety
and reflects normal or closure traffic.

### Usable now: the current synthetic signal study

This feature is already useful without waiting for a new data delivery. The
user selects a normal scenario or an exact road-closure scenario; the program
uses the same demand build, closure interval, seeds, and direction variants;
then it runs microscopic SUMO signal experiments against the rerouted traffic.
For a closure, the two-pass study first drives the closure with the baseline
program, extracts the trips that actually rerouted, and optimizes/evaluates
the signal timings against those post-closure trips. The UI already reports
numeric cycle, offset, green, yellow, and red seconds per controlled link.

The present source of phase compatibility is `netconvert --tls.guess`, so the
result must be labelled **synthetic experiment**. It answers: "within this
network model and these conservative timing rules, which green splits reduce
the simulated delay after this closure?" It does not answer: "change
Gothenburg's controller to these values." The distinction is a claim gate,
not a reason to withhold the useful simulated result.

### Execution order: useful now, then city-configured

1. **Freeze the current synthetic study.** Keep the existing normal and
   closure paths (`signal_optimize.py` and `signal_closure_combine.py`) as the
   usable product. Every result must identify `synthetic` provenance, the
   exact ScenarioSpec, the closure's active interval, the measured window,
   seed/variant pairs, and whether it passed health and closure-integrity
   gates.
2. **Make a versioned synthetic SignalPlan.** Export the generated TLS
   topology, link mapping, phase states, compatible-movement sets, timing
   limits, and clearance values to a `SignalPlan` artifact. Preserve the
   generated phase structure; never infer a new simultaneous green merely
   because two movements have high demand. Reject a junction whose generated
   link map or phase sequence cannot be reconciled with the SUMO network.
3. **Optimize the closure's actual arrivals.** Use the closure ScenarioSpec
   rather than a fixed time window. Run citywide meso once for demand/reroute
   context; use micro only for the affected signal component and its queues.
   Optimise cycle, phase green budget, legal clearance, and offsets jointly.
   A higher flow may receive a longer green, but only after competing phases,
   pedestrian/cycle minimums, and spillback guards are evaluated.
4. **Publish an honest numeric result.** For every controller show baseline
   versus candidate green/red/yellow/all-red, the affected movements, paired
   delay change, queue/spillback/health state, and `no valid improvement`
   when the candidate is unsafe, disqualified, or not robust across q10/q50/
   q90 demand variants.
5. **Import one real junction cluster before scaling.** Build an adapter from
   the city's documents into the same SignalPlan schema, run mapping and
   safety checks, and compare synthetic versus imported plans on the same
   normal and closure scenarios. Do not import citywide plans first: one
   reviewed corridor is the correct acceptance gate.
6. **Promote only verified plans.** `city-configured` is allowed only when
   every controller/link/phase mapping, conflict matrix, clearance, detector
   rule, and active time plan is present and valid. Otherwise keep
   `synthetic` even if the numerical result looks attractive.

### Sensor growth and signal accuracy

A new sensor improves this workflow by narrowing the calibrated traffic
arrivals, not by directly revealing a green split. The most valuable future
sensors are directional counters on approaches to the selected signal cluster
and on likely detour routes before and after a closure. A station must overlap
the simulated date and be added through the registry with its coordinate CRS,
measurement semantics, direction/bearing, active dates, raw-file identity,
and approved edge snap. After every addition, rebuild all derived artifacts
and publish a before/after contribution report: holdout recovery, confidence
change, affected movement demand, and whether the signal recommendation
changed. A sensor added after the historical date may improve future studies;
it must not be silently used as evidence for a 2025 historical run.

### Verified data position: what is available, what is only city-held

The table below deliberately separates evidence that is publicly confirmed
from data that the city is likely to manage but has not committed to deliver.
"City-held" means the documented signal process requires or uses the artifact;
it does **not** mean that this project is automatically entitled to receive
it.

| Data | Status on 2026-07-15 | Use now | Needed for |
| --- | --- | --- | --- |
| Supplied 2025 six-sensor, 15-minute counts | Present in this project | Calibrate current normal and closure demand | Current simulation |
| Göteborg traffic catalogue: counts, average speeds, nearby flow measurements from 2019 onward | Public interactive catalogue; no bulk/API delivery is assumed | Manual/approved external reasonableness checks only | Extra validation, not calibration replacement |
| NVDB lane counts, speed limits, functional class, forbidden direction and road network | Publicly documented API data; download requires registration | Network audit and legal/free-flow constraints | Better routing structure, not observed travel speed |
| Signal plans, conflict/spärrtid matrices, signal-group functions, detector functions, plan selection and priority rules | City signal process requires these documents; no public per-controller dataset was found | Not yet | Real controller-compatible timing |
| Historical link/path travel times and detector logs | A city/Trafikverket travel-time camera system is documented, but no current public historical export was verified | Not yet | Validate speeds, queues and signal effects |
| Turning counts at signal approaches | Not publicly verified | Not yet | Validate left/through/right demand |
| Local OD/purpose microdata | Not open data; SCB access is a project-specific, reviewed order | Not needed for the current synthetic study | Stronger OD and purpose claims |

Sources: Göteborg's public catalogue explicitly exposes traffic volumes,
average speeds and nearby measurements, but as an interactive report rather
than a promised bulk feed ([Göteborg traffic catalogue](https://goteborg.se/wps/portal/start/trafik-och-resor/trafik-och-gator/trafikinformation/statistik-om-trafiken-i-goteborg/trafikmangder-pa-olika-gator)).
Trafikverket documents NVDB's available lane, speed-limit and forbidden-
direction datasets and its registration requirement for downloads
([NVDB open API](https://bransch.trafikverket.se/tjanster/data-kartor-och-geodatatjanster/nyheter-om-trafikverkets-data/2025/nvdb-vagdata-tillgangliga-i-trafikverkets-datautbytesportal-for-anvandning-i-oppet-api/)).
Göteborg's current technical handbook requires conflict and clearance matrices,
signal-group functions, detector functions and plan-selection/priority
descriptions for traffic-signal work
([signal requirements](https://tekniskhandbok.goteborg.se/12-projektering/12b-projekteringsforutsattningar/12bh-trafiksignaler/)).
It also documents travel-time cameras operated jointly with Trafikverket, but
the available public page is archival and does not establish a current raw-data
service ([travel-time system](https://tekniskhandbok.goteborg.se/Arkiv/2015-1/__site/__planering__planeringsf%C3%B6ruts%C3%A4ttningar__grunddata__restider.html)).
SCB confirms that microdata requires a defined research/statistics project and
a confidentiality review ([SCB microdata](https://www.scb.se/vara-tjanster/bestall-data-och-statistik/mikrodata/)).

### Exact request to send when data becomes possible

Ask the city for **one selected signal corridor**, not the whole city. Request
the following for one normal weekday and the planned closure period: controller
and junction IDs; signal-group-to-SUMO-movement mapping or drawings; active
time plans; phase sequence; conflict and clearance/spärrtid matrices; minimum/
maximum green, yellow, all-red and red-yellow; pedestrian/cycle constraints;
detector and transit-priority logic; offsets/coordination; temporary work-plan
rules; 5- or 15-minute approach/turn counts; and matching link/path travel
times. Ask separately whether the city can license a historical extract for
research. This is the smallest package that upgrades one corridor from a
synthetic experiment to a defensible city-configured study.

### Optimization model

1. Optimize legal **phases**, each a set of compatible movements that can be
   green simultaneously. Never optimize each lamp separately.
2. Preserve conflict-free phase structure from SignalPlan.
3. Choose cycle length, phase green split, yellow, all-red/red-yellow,
   offsets, and plan switching by time of day within legal min/max limits.
4. Use arrivals and movement demand derived from the exact normal or closure
   ScenarioSpec. A closure study must receive the actual closure edges and
   active times, not a fixed default window.
5. Evaluate a normal and closure plan against their own real baseline plan
   using equal warm-up, measurement, drain, seeds, variants, and network.
6. Optimize a robust objective: total and percentile delay, stops, queue
   length, spillback, throughput, transit/pedestrian constraints, and
   robustness across demand variants.

### Fidelity and performance rule

Use citywide meso to create normal/closure flows and identify affected signal
components. Use bounded microscopic evaluation for signal phases, queues,
lane use, and roundabouts. This can remain invisible to the user as an
implementation detail, but the result must state its model and area.

### Result shown to the user

For each controller and time period, show:

```text
baseline plan and provenance
cycle length and offset
phase name and compatible movements
green, yellow, all-red/red-yellow, and resulting red seconds
normal or closure ScenarioSpec identifier
paired baseline comparison and uncertainty
queue/spillback/safety status
```

### Acceptance gate

- No conflicting protected greens or invalid clearance timing.
- The actual scenario window, closure schedule, plan ID, and demand build ID
  match exactly.
- No health, access, closure-integrity, or spillback gate fails.
- A candidate is selected only when it robustly improves on the baseline;
  otherwise publish `no valid improvement`.

## Phase 6: One Application, Not Separate Tools

The existing task-oriented UI should remain. Do not rebuild it as another
landing page. Instead, make every task open a shared study context.

### Required behavior

1. Normal simulation selects a DemandBuild.
2. Road closure creates a ScenarioSpec from that build.
3. Closure timing creates candidate ScenarioSpecs from that same build.
4. Selecting a closure recommendation loads that exact ScenarioSpec.
5. Signal optimization consumes the selected ScenarioSpec and its exact time
   window.
6. The validation panel follows the active build and scenario.
7. A normal scenario exposes its source, frozen target, final SUMO output and
   final-output fit at each physical sensor; a two-way Total is presented as
   one station with modelled directional children.
8. A job page shows progress, cancellation, logs, artifacts, confidence, and
   final gates for each study.

Every result page must make three things immediately visible: what was run,
whether it completed healthily, and what the result is allowed to claim.

### Acceptance gate

- From any result page, the operator can reach the exact ScenarioSpec,
  build ID, job record, and validation report that produced it, without
  reading server logs.
  **DONE 2026-07-20.**  The data was always durable and API-reachable
  (job records embed the full spec; the scenario index carries
  `scenario_spec`/`build_id`/`demand_build_key`), but no surface exposed
  it — the history panel showed only status/kind/time/id, so an operator
  had to hand-query `/api/jobs/<id>`.  Job rows are now clickable and
  open a detail view rendering the job record, the run's exact
  ScenarioSpec (summary fields plus the verbatim JSON), its demand and
  network build IDs, closure intervals and seed set — and a linkage line
  stating whether the active validation report covers THIS job's build,
  with the same amber warning as the shield when it does not.  Built with
  `textContent` throughout: job args echo user-supplied edge IDs and
  error strings.
- The 🛡 validation panel reflects the ACTIVE study's build, not merely
  the latest demand build.
  **DONE 2026-07-20.**  This was a real integrity gap, not a cosmetic
  one: `validation.json` recorded `demand_window`/`demand_source` but no
  build identity, and the panel rendered it beside whatever scenario was
  loaded — so a scenario from another build showed a green shield
  validating something else.  The report now records `demand_build_id`
  (null, never invented, when the build did not record one), and the web
  app compares it against the active scenario's own
  `scenario_spec.demand_build_id`.  On mismatch the shield drops to "–"
  (never "pass"), an amber banner states that the report describes a
  DIFFERENT build and says the gates below mean nothing for what is on
  screen, and the gate table is visually muted.  Verified in headless
  Chrome in both states (matching → normal shield with the build id in
  the provenance line; mismatched → warning state), plus regression tests
  on the report side.

  KNOWN STATE while doing this (2026-07-20): the tracked
  `web/data/validation.json` is deliberately left at its last COHERENT
  version and therefore does not yet carry `demand_build_id`; it will on
  the next legitimate regeneration.  Reason: the held-out v2 campaign's
  envelope builds overwrote `sumo/candidates.rou.xml`, which the report
  hashes to prove the frozen temporal-holdout evidence still belongs to
  the live release.  Regenerating now records a FALSE stale for that
  section — the evidence is valid for release `57e3fd90…`, only the proof
  file on disk was clobbered — and the live pool is not recoverable (no
  archive stores it, and rebuilding would mint a new build id that no
  longer matches the published scenarios).  The pool is now part of the
  protected live-release product set so this cannot recur.  The UI treats
  a missing `demand_build_id` as "cannot compare": it neither warns
  falsely nor claims the gates apply.
- The sensor table never makes a source observation, a split assumption, a
  rounded map value and a final SUMO count look like the same number.
  **VERIFIED ALREADY SATISFIED 2026-07-20** (audited, no change needed):
  the source cell is labelled by kind (`riktad` / `tvåvägs-total (en
  mätning)`); a two-way station renders as ONE station row with indented
  children whose source cell reads `modellandel av total`, so the raw
  Total is never repeated as if it were two measurements; frozen target
  and simulated output are separate columns, as are representative-seed
  and ensemble values; `auditSimMean` prefers the pre-rounding
  `simulated_mean_raw` over the map's rounded integer; every cell keeps
  the exact value in its `title`; and `auditSum` propagates null so a
  missing directed value makes the station value unknown, never zero.
- A cancelled or failed study leaves the previous published study visible
  and clearly labelled as the one still in force.
  **DONE 2026-07-20.**  The BEHAVIOUR was already correct and tested
  (staging plus atomic publish; cancellation/failure leaves the live
  scenario set untouched; the publish gate refuses staged sets on GEH
  collapse, infeasible intervals, build-ID mismatch, variant gaps or
  corrupt JSON).  The LABEL was missing: a failure fired a transient
  `alert()` and then went quiet, so after a failed 2027-03-15
  recalibration the operator was looking at 2025-09-16 data with the only
  evidence being a dismissed dialog.  A persistent, operator-dismissible
  banner now names both sides — e.g. "Omkalibreringen för 2027-03-15
  (forecast) misslyckades. Kartan visar fortfarande den föregående
  studien: 2025-09-16 (historik), bygge 57e3fd904e32." — and is wired
  into the recalibration and closure paths, whose outcomes replace what
  is displayed.  Deliberately NOT wired into the closure-time and monthly
  searches: those do not publish a study, so claiming "the previous study
  is still shown" would misdescribe what happened; they keep their local
  error reporting.  Verified in headless Chrome by driving the real
  failure path (stubbed API, app's own poll loop), asserting the previous
  scenario stays loaded and the banner names the attempted study, the
  in-force study and its build.

## Phase 7: Maintain Performance Without Reducing Fidelity

The performance policy is result preservation first.

1. Continue using the benchmark harness and semantic hashes for normal,
   closure, and microscopic smoke cases.
2. Keep citywide meso as the default for normal and closure work.
3. Cache only immutable, fully fingerprinted artifacts such as candidate
   geometry, network metadata, and validated signal plans.
4. Use isolated workspaces and a single process budget for candidate/seed
   batches. Do not overlap PFE workers with SUMO workers.
5. Promote parallel seed or candidate execution only after repeated measured
   trials show a material wall-time improvement with identical results — AND it
   clears the user-facing latency contract for the case it targets. Parallel
   seed execution was measured this way and NOT adopted: see "Seed-parallel
   campaign line — measured and closed" below.
6. Keep detailed vehicle, lane, and queue output limited to the selected
   signal study so citywide runs do not produce unnecessary I/O.

Do not reduce seed count, uncertainty variants, solver iterations, closure
rerouter coverage, or simulation step fidelity as a speed shortcut.

### Seed-parallel campaign line — measured and closed (2026-07-23)

The paired serial/parallel seed-worker campaign line (phase-profile v4–v6,
`--seed-workers 3` over the frozen baseline and whole-window closure) was
executed under recorded approval and is now CLOSED. Final reviewed evidence
from the v6 verification run (three seeds q50/q10/q90, five trials per arm,
mesoscopic, result-preserving with identical scenario/trajectory digests):

- baseline: p95 wall **5.883 s** at three workers, a **43.8%** improvement over
  the serial arm — comfortably under the 10 s validated-completion gate.
- closure whole-window: p95 wall **10.4234 s** at three workers, a **40.8%**
  improvement — but it **misses the 10 s gate by 0.4234 s**.

Three consecutive campaigns (v4, v5, v6) all landed the closure whole-window
parallel arm just over the ceiling; the case is dominated by `sumo_execution`,
which seed-parallelism at three workers does not shrink enough. The result is
faster and result-preserving but does not satisfy the latency promise for the
one case it needed to.

DECISION: seed parallelism is **not adopted** as a production default, **not
retried**, and **not mechanically refrozen as a v7**. The production
seed-worker default is unchanged, the phase-profile campaign harness has no
current executable campaign (`CURRENT_CAMPAIGN_ID = None`; v1–v6 all retired),
and the honest product path for a closure query that cannot finish inside the
budget is the ALREADY-IMPLEMENTED asynchronous `/api/close` start/poll/cancel
workflow (serve.py + web/app.js) — no new async work is created or claimed
here. A materially different architecture, not a fourth seed-parallel campaign,
is the only path that would reopen this.

One measured performance boundary to respect (2026-07-14): the dominant demand
cost is the deliberately sequential per-edge IPF update, already
flat-parallel across all cores — 96% of solve time with no safe lever
short of a JIT dependency. Performance work under this plan means caching,
scheduling, and I/O discipline, not touching the solver.

### Acceptance gate

- Every claimed speed-up ships with before/after wall time on a golden
  case AND an identical semantic hash (or a documented, reviewed reason
  the results changed).
- No cache is keyed on anything less than the full build fingerprint.

### Architecture boundary for closure latency — static study (2026-07-24)

Static, non-SUMO boundary discovery after the v4–v6 seed-parallel line closed.
No SUMO/libsumo/TraCI was invoked, no outcome or state snapshot was inspected,
and nothing here is a measurement: every number is either a source fact or an
ESTIMATE derived only from the reviewed PERF-16/17 summaries.

**1. The supported new-closure control path (source symbols, not inference).**
`serve.py::_run_close()` writes a `ScenarioSpec` under `SPEC_DIR`, then shells
`run_scenario.py --scenario-spec` (or `--closure` JSON per window, or legacy
`--close`) through `run_in_new_session(..., timeout=600)`. It never blocks the
HTTP request: `_close_state` under `_close_lock` drives `GET /api/close/status`
(`idle|running|done|error|cancelled`) and `POST /api/cancel?kind=close` stops
the job by process group. `runs/jobs/<id>.json` records each job and, at
startup, `simulation_recovery_block()` marks a surviving pgid
`orphaned_running` (cancellable) or a dead one `orphaned` — that is **orphan
detection, visibility and cancellation, NOT resumption**: an interrupted close
job is never continued, only reported and stopped. `run_scenario.main()` then
runs the frozen `PHASE_NAMES` sequence:

| phase | source | work |
|---|---|---|
| `input_validation` | `main()` | spec/window/demand validation |
| `job_preparation` | `main()` | window, variant and seed resolution (`demand_variants`, `variant_path`) |
| `closure_preparation` | `main()` | `edges_near(close_edges, REROUTER_RADIUS_M=400)` → `write_closure_additional()` (`<rerouter>`/`closingReroute` per `grouped_closure_intervals`), `build_edge_graph(set(close_edges))`, `edge_freeflow_times()`, then `prepare_closure_variants(prep_jobs)` → serial `prepare_variant_job` → `truncate_stranded_vehicles` per demand variant |
| `job_preparation` | `main()` | per-seed `scratch_dir/seed-<seed>` isolation + `write_edgedata_additional` |
| `sumo_execution` | `run_seed_job` → `run_sumo` | one SUMO **subprocess per seed** (`--seed <seed>`, `--mesosim true`, private `work_dir`) |
| `aggregation_validation` | `parse_edgedata`, `aggregate_flows`, `closure_integrity_status` | flows, Monte-Carlo confidence, integrity/health gates |
| `trajectory_publication` | `publish_trajectories_from_vehroute` | trajectory product |
| `disruption_analysis` | `closure_disruption_across_variants` | grouped/sparse deterministic closure cost, with the former per-OD path retained as an oracle |
| `payload_construction` | `build_scenario_payload` | deterministic in-memory payload construction |
| `artifact_publication` | `publish_scenario_artifacts` | atomic scenario JSON and `index.json` manifest publication |
| `cleanup` | `cleanup_scenario_workspace` | scratch removal, only after successful publication |

Artifact lifecycle. **Staged/mutable** — everything under
`create_scenario_workspace()` (`runs/<run-id>/scratch` or a private temp dir):
`closure_<name>.add.xml`, the filtered `<stem>_<name>.rou.xml` variants,
per-seed dirs, edgedata additionals, vehroute XML. **Published/durable**, and
these carry DIFFERENT fields — the scenario JSON and its `index.json` entry
carry `scenario_spec`, `closures`, `closure_integrity`, `demand_signature`,
`build_id`, `demand_build_key`; the trajectory JSON carries only
`n_vehicles`, `n_unfinished`, `inserted_in_run`, `sampling`,
`displayed_share`, `edges`, `vehicles`, i.e. no ScenarioSpec/build/closure
identity of its own. **Reusable/immutable inputs** — `sumo/net.net.xml`, the
calibrated q50/q10/q90 route variants, `sumo/demand_meta.json`.

**2. Identity/key matrix, by layer.** No single existing structure covers the
whole thing; reuse must be keyed at the layer it actually applies to.

| layer | required key | what exists today |
|---|---|---|
| network-derived indices | `net_sha256`, `schema_version` | `metadata.load_metadata()` refuses a stale `sumo/network_metadata.json` |
| simulator state snapshot | demand build id, network build id, demand variant, seed, simulation mode, warmup end, input + source fingerprints, git commit, Python/SUMO version, platform, `save_state_rng`, `save_state_precision` | `WarmStateIdentity` encodes exactly these **and only these** — it does not carry ScenarioSpec/closure intervals, output configuration, validation rules or publication identity |
| closure-input preparation | closed edge set, closure intervals, demand variant content, network build | no cache exists |
| whole-query result | everything above **plus** output configuration, validation rules and publication identity (scenario name, manifest entry) | no cache exists |

Missing identity must invalidate reuse, never silently widen a key.

**3–4. Candidate classes.**

*A. Exact-query result reuse.* **Not implemented today.** The manifest is not
consulted before a run: `index_for_current_demand()` is called once, inside
`scenario_publication`, purely to drop entries from a different demand
calibration before writing `index.json`; neither `/api/close` nor
`run_scenario.main()` performs a pre-run lookup. Removable phase: on a hit, the
ENTIRE `PHASE_NAMES` pipeline (input validation through publication). Remaining floor:
a manifest read plus the HTTP response. But a
correct key must include the full whole-query layer above, so a genuinely NEW
closure can never hit — it answers repeats only. Concurrency/restart: a lookup
is a read of the published manifest, so it adds no concurrency of its own, but
it must not observe a half-published run — `scenario_publication` writes the
scenario JSON and `index.json` with `atomic_write_json`, and a lookup would
have to treat an entry as valid only once both writes have landed; a server
restart loses nothing because the manifest is on disk. Invalidation: any
demand, network, source, SUMO or output-configuration change. Provenance: a
served result must carry the original run's identity, never be re-attributed to
the new request. Deterministic-output risk: serving a stored result is exact by
construction (no simulation re-runs), so the risk is not numerical drift but
MIS-ATTRIBUTION — a key any coarser than the whole-query layer would return a
different query's bytes, which is why the key cannot be narrowed to make it hit
more often. **Rejected as a new-query speed-up** (it would serve the
already-fast cached-render case, not validated completion of a new closure).

*B. Fully keyed closure-input preparation reuse.* Removable phase:
`closure_preparation`, ≈1.15–1.25 s (ESTIMATE from the reviewed summaries).
Its network-only component is **already cached** —
`edge_freeflow_times()` and `build_edge_graph()` both take the
`load_metadata(NET_PATH, sumo/network_metadata.json)` fast path. What remains
is `truncate_stranded_vehicles` per demand variant, keyed on closed edges +
closure intervals + variant content, so a new closure cannot hit and a repeat
degenerates to class A. Remaining floor: `sumo_execution`. Concurrency/restart:
pure per-variant work writing distinct staged files (`prepare_variant_job`
returns counts only), so it parallelises safely in principle; but a cache would
move those files OUT of `create_scenario_workspace()`, which today guarantees
`cleanup_scenario_workspace()` removes them only after successful publication —
a restart mid-write would leave a cached artifact no run tree owns, so the
cache would need its own atomic publish and staleness sweep. Invalidation:
variant content, network build, or any change to `truncate_stranded_vehicles`
itself (its filtering rules are part of the key, not just its inputs).
Provenance: filtered routes are staged inputs, never published. Deterministic-
output risk: `truncate_stranded_vehicles` is deterministic given
(routes, closed edges, adjacency, free-flow times), so a correctly keyed hit
reproduces the same bytes; the risk is a key that omits one of those inputs —
notably the closure INTERVALS, since the same edge closed over a different
window yields different truncation. **Rejected**: no new-query benefit remains.

*C1. Persistent EXTERNAL sumo controlled over TraCI.* A long-lived `sumo`
process driven by the TraCI socket protocol. Removable phase: **only per-seed
process spawn and teardown**, NOT network load. Official SUMO docs are explicit
that TraCI `simulation.load` reloads the simulation *with command-line options*
— it re-reads the net and additionals for a new scenario; the distinct
`loadState` operation is the one that retains the network/additional objects,
and that is class D, not this. A new closure changes the rerouter additional and
the truncated routes, so it needs a full `load` and re-parses the network anyway.
Remaining floor: the simulated 24 h itself with the closure active PLUS the
network reload on every new query. IPC is NOT a per-simulated-second cost: SUMO
documents `simulationStep(t)` as advancing to a target time in a single call,
so a batch closure using SUMO's own `<rerouter>`/`closingReroute` runs to the
end with a small constant number of socket round-trips, not one per second — the
earlier "per-step IPC net cost" claim was wrong and is withdrawn.
Concurrency/restart — a genuinely NEW ownership boundary, not the current one.
Today each request is a short-lived `run_scenario.py` process group that
serve.py reaps with `killpg`; a SUMO process that must survive to serve the NEXT
request cannot be owned by that exiting group. A persistent pool therefore needs
its own longer-lived supervisor (serve.py itself, or a dedicated pool manager)
with an explicitly different model: (i) LIFECYCLE — the pool is spawned at
server start or lazily on first close and retired wholesale on any net/demand/
SUMO-version/configuration change; (ii) CANCELLATION — a per-request cancel must
abort the in-flight `load`/`simulationStep` on the borrowed member and return or
discard THAT member, not `killpg` the pool, so serve.py's current per-request
pgid cancellation no longer covers it and must be extended; (iii) CRASH/ORPHAN —
a member that crashes or hangs mid-query is discarded (never reused), and pool
members orphaned by a server crash must be detectable and reapable the way
`runs/jobs/<id>.json` makes subprocess jobs recoverable today; (iv) COLD
FALLBACK — a query that cannot get a healthy member falls back to the current
fresh-subprocess path rather than blocking. None of this exists yet; it is part
of what any adoption after the experiment would have to build and have reviewed. Invalidation: net, demand, SUMO version or
configuration change must retire the process. Provenance: `source_fingerprints`
and the phase profile currently describe a fresh process per seed; a reused
process must bind and re-verify that identity per query. Deterministic-output
risk: LOW here — a per-query `simulation.load` re-reads command-line options
including `--seed`, so each query is re-seeded exactly as a fresh process would
be; the risk reduces to proving no simulation state leaks across a `load`, which
is what the paired digest check below verifies.

*C2. In-process libsumo.* Removable phase: per-seed process spawn and teardown
plus TraCI socket setup — but, as in C1, NOT network load (only
`loadState`/class D retains it). Process boundary, corrected: libsumo would run
inside `run_scenario.py`, which `serve.py::_run_close` already launches as a
job CHILD via `run_in_new_session`; a libsumo crash therefore takes down that
job child, not `serve.py`, and the existing job-gate/orphan-recovery machinery
still applies. Concurrency/restart: SUMO documents that concurrent libsumo
instances require Python `multiprocessing` (one interpreter cannot host
concurrent simulations), so keeping the parallel-seed capability is a design
obligation — one worker process per concurrent seed, which also RESTORES the
per-seed cwd isolation `run_sumo` needs for relative edgeData paths — not a
capability proved impossible; a crashed worker is discarded and respawned.
Remaining floor: the same simulated 24 h plus per-query network reload.
Invalidation: as C1. Provenance: as C1, and each worker must bind and
re-verify identity per query. Deterministic-output risk: with per-query
`load` re-applying `--seed` the RNG hazard is the same LOW one as C1; the
in-process specifics to prove are that no module-level state leaks between a
worker's successive queries and that each worker keeps its own cwd, both
verifiable by the paired digest check.

*D. Per-seed/variant save-load checkpoint replay before the earliest closure.*
Machinery exists and is keyed: `save_state_arguments()` /
`load_state_arguments()` (`--save-state.rng true`, `--save-state.precision 16`),
`WarmStateIdentity`, `store_warm_state` / `restore_warm_state`,
`certify_warm_state_equivalence`, and `run_sumo()`'s `save_state_path` /
`save_state_time_s` / `load_state_path` — but `main()` never passes them, so the
`/api/close` path does not use it. Removable phase: simulated time before the
earliest closure. **Decisive limit for the failing case**: the frozen
`closure_whole_window` case has `start_offset_s: 0`, so there is nothing before
the closure to skip and the mechanism removes zero. For time-windowed closures
(`--closure` JSON with a later `begin`) one warm state per (demand, network,
seed, variant, warmup_end_s) would serve many different closure edges, which is
a genuine new-query benefit — for a different case. Remaining floor: simulated
time from the warm point to the end. Concurrency/restart: per-seed states are
independent files, so seeds parallelise unchanged; `store_warm_state` ALREADY
publishes atomically (writes a `.{content_key}.tmp` directory and `os.replace`s
it into place), so a crash mid-write leaves no half-published entry, and
`restore_warm_state` already refuses an entry whose identity does not verify —
the remaining obligation is only that a verification miss falls back to a cold
t=0 run rather than loading a partial snapshot, which the existing
`CacheLookup` miss path already does.
Invalidation: any field of `WarmStateIdentity`. Provenance: the
published run must record that it resumed from a certified state, not claim a
cold run. Deterministic-output risks to prove first: RNG continuity across the
seam, incrementally loaded vehicles at the load boundary, edgeData/vehroute
output continuity, state precision and SUMO version compatibility, and closure
timing alignment. Note `CACHE_FIELD_TOLERANCES = {"travel_time_s": 1.0}` is a
**decision-metric** policy, not an exact-flow equivalence, and must not be
repurposed as one.

**5. Decision: select ONE bounded, future approval-gated experiment — a
persistent-process (C1) arm proven result-equivalent to the current subprocess
arm.** Criterion 5 asks for a candidate that could *plausibly* affect the hard
ceiling with paired before/after cases and semantic + health equivalence proof.
Producing the SAME scenario and trajectory as today is exactly what that proof
checks, so it is the target, not a disqualifier. Ruling the field down:

- **A, B**: remove no work from a NEW closure query (rejected above).
- **D (save/load checkpoint)**: removes ZERO for the failing case, whose closure
  is active from `start_offset_s: 0`; it helps only time-windowed closures, a
  different case.
- **C2 (in-process libsumo)**: removes the same process-creation cost as C1 plus
  socket setup, but requires a `multiprocessing` redesign to keep parallel seeds
  and per-seed cwd isolation, a larger change for a marginal additional saving
  over C1. Not selected; kept as a fallback only if C1 proves the lever real but
  socket cost material.
- **C1 (persistent EXTERNAL sumo over TraCI)**: SELECTED. It keeps SUMO in
  external processes, so external isolation is retained — but, per the
  Concurrency/restart clause above, a pool that spans requests is a NEW
  ownership boundary: serve.py's current per-request `killpg` cannot own it and
  must be EXTENDED with member-level cancellation and pool orphan-reaping. A
  per-query `simulation.load` re-applies `--seed`, so determinism is preserved by
  construction; and its output is identical to the subprocess arm by design, so
  paired digest/health/integrity equality is directly provable. Its removable
  work is per-seed process creation only (the net is reloaded on each `load`), an
  **unmeasured** quantity — NOT assumed small — and whether it reaches the
  ≈0.42 s p95 gap is precisely what the experiment measures.

**The one bounded experiment (defined here, NOT authorized or executed).**

- *Question*: does a persistent-process closure arm reduce the p95 PARALLEL wall
  time of the failing case below the 10 s ceiling — and below the current
  subprocess arm — while producing a semantically identical result?
- *Proposed files*: a new benchmark harness under `tools/` that drives a fixed
  pool of three reused, TraCI-controlled `sumo` processes (one per concurrent
  seed), plus focused tests. No change to
  `run_scenario.py`, `serve.py`, production defaults or any contract; production
  keeps the subprocess path until and unless a separate adoption task passes.
- *Immutable key*: the canonical scheme in the note below.
- *Arms and query sequence, exact*: two arms — `arm_subprocess` (today's fresh
  process per seed) and `arm_persistent`, a fixed pool of **three** reused
  TraCI-driven `sumo` processes, one dedicated member per seed slot (member_0 →
  seed 1000/q50, member_1 → seed 1001/q10, member_2 → seed 1002/q90), each in its
  own private `work_dir` so the per-seed cwd isolation `run_sumo` relies on is
  preserved. Every query runs its three seeds concurrently across the three
  members, each member serving its seed via `simulation.load` (which re-applies
  that seed) and never crossing to another seed's slot; a member that faults on
  any query is retired and, on the cold-fallback path, that seed for that query
  runs as a fresh subprocess. Because a stale or no-op reload could silently
  return the PREVIOUS query's result and still "match" a same-query reference,
  the persistent arm must run a sequence of DISTINCT queries that exercises both
  transition directions and both scenarios, not five reloads of one closure:
  `baseline → closure → baseline → closure → …` for ten queries (five
  `closure_whole_window` on `26842525_26355153_0` 00:00–24:00 interleaved with
  five `baseline`), all seeds 1000/1001/1002 → q50/q10/q90, meso, same net and
  demand build. The five closure queries are the latency gate; the interleaved
  baseline queries are the isolation control.
- *Equivalence proof (semantic, hard gates, any miss fails the experiment)*: for
  EACH query in the sequence, that query's `scenario_digest` AND
  `trajectory_digest` must equal a fresh-subprocess reference of THAT SAME query
  — so a reload that returns the wrong scenario (baseline digest where a closure
  is expected, or vice versa) fails immediately. These are the harness's
  `canonical_digest()` values, i.e. SEMANTIC equality (it strips
  `generated_at`/`created_at`/`finished_at` and `path`/`source_path`/`workspace`
  before hashing), not raw byte identity; the claim is exact semantic
  equivalence, not byte-for-byte files. Every seed-health record must stay 0
  collisions, 0 teleports, 0 running_at_end, 0 waiting_at_end and
  loaded == inserted; every closure query must stay `verified_clean`.
- *Latency gate, frozen numerically*: the statistic is the p95 of the five
  closure queries' PARALLEL wall time with `seed_workers = 3` (the deployed count,
  one seed per pool member), never a sum or median of per-seed spawn times.
  TIMER BOUNDARY, frozen: the measured wall time is per-query and EXCLUDES the
  one-time pool startup/warm-up — that is amortized across queries and is exactly
  what the persistent arm exists to remove — but INCLUDES the per-query
  `simulation.load` (net reload) on every query, since that recurs per closure.
  The one-time pool bring-up (`pool_warmup_queries = 0` billable warm-up queries;
  the pool is ready before the timed sequence begins) is measured and reported
  separately, never folded into the gate. PASS requires BOTH
  `parallel_p95_wall_s ≤ 10.0` (the hard ceiling) AND
  `parallel_p95_wall_s < arm_subprocess_p95` by at least
  `min_p95_improvement_fraction = 0.04` (≈ the 0.42 s / 10.4 s crossing the
  failing case needs); an identical-or-slower persistent arm is a no-go, not a
  tie.
- *Failure cleanup*: on success, failure OR interruption the harness closes every
  TraCI socket and terminates and reaps all three resident `sumo` members (no
  orphaned simulator may outlive the run), publishes no scenario, manifest entry
  or state, preserves its run tree, and spends the attempt. A per-query
  `timeout_seconds = 600` bounds any single query (matching serve.py's current
  close timeout); a member that exceeds it is killed and reaped, not left
  resident.
- *Execution boundary*: it invokes SUMO, so it requires a clear user request for
  the frozen experiment plus the normal safety confirmation appropriate to an
  expensive, evidence-producing run. Nothing here initiates that run, and no
  key or value is computed or frozen.
- *Pre-committed reading*: if the closure p95 is ≤ 10.0 s AND ≥ 4% below the
  subprocess arm with EVERY query semantically identical and healthy, it advances
  to a separate adoption task (production change and the C1 supervisor model
  still gated). If every query is semantically identical but the closure p95
  stays over 10.0 s (or the improvement is below 4%), process-lifecycle
  amortization is a definitive NO-GO for this case and the line closes for good.
  Any digest/health/integrity miss on ANY query fails the experiment outright —
  a faster but different result is never adopted.

**Frozen experiment contract (LUNA-PERF-19, UNEXECUTED / UNAPPROVED).** The C1
experiment defined above is now built as a fail-closed, non-production harness
(`tools/benchmark_persistent_sumo.py`) and frozen as
`validation/persistent_sumo_campaign_v1.json`, experiment id
`persistent_sumo_v1`, content key
`72108df6b3ec61de33e5006181d38abc3aba3292bcb8b907643dd9d7f431f588`. It has NOT
been executed: no SUMO/TraCI ran, no socket opened, no outcome exists, and the
contract carries no measured value or approval. Importing the harness, validating
the contract, or running its focused tests never imports TraCI or starts SUMO;
TraCI is imported lazily only after an explicit `--execute` passes the full
contract + environment preflight. This freeze is NOT adoption authority and NOT a
performance claim. The prior seed-parallel PERF-16 key/approval is spent and
invalid for this experiment. Actually running it — preflight, execution or any
outcome inspection — requires a separate explicit user request matching the
frozen key above, normal execution-safety confirmation, and a real TraCI driver
that is
deliberately out of this pre-outcome build. Until then the asynchronous
`/api/close` path remains the product path.

**RESULT (LUNA-PERF-20, 2026-07-24): FAILED EXPERIMENT — C1 REMAINS UNTESTED.**
The one authorized invocation ran at key `72108df6…` into
`validation/persistent_sumo_campaign_v1_outcome` (264 files preserved; the
attempt is spent and must not be rerun). Verdict `eligible_and_passed: false`,
failed gates `member_fault`, `fallback_used`, `seed_health:{1,3,5,7,9}`,
`parallel_latency_ceiling`, `p95_improvement_floor`. This is NOT a C1 no-go:
the persistent arm never existed, so nothing about persistent SUMO was
measured. Two defects in the never-executed `--execute` path caused it, both
invisible to the fake-driven suite:
(1) FATAL — `_TraciConnector._default_spawn` launches `sumo --remote-port <p>
--num-clients 1` with NO network file, so SUMO exits at once ("Quitting (on
error)"); all three pool members died during warm-up and all 30 persistent
seed-runs faulted with "Connection closed by SUMO" and took the cold
fallback. Reproduced independently with a bare launch. The reported
`persistent_p95_wall_s` 19.28 s vs `subprocess_p95_wall_s` 11.38 s
(-69.3%) therefore measures dead-pool retry plus cold-child overhead, NOT
process reuse, and must never be quoted as a persistent-SUMO measurement.
(2) `_variant_family` does not recognise the real filtered-route filename
`calibrated.rou_close_<edge>.rou.xml` (`Path("calibrated.rou.xml").stem`
keeps `.rou`), so seed health failed on all five closure queries even though
the telemetry itself was perfect.
What the run DOES establish, because both arms degenerated to fresh
subprocesses: the shared production payload/assembly path is sound end to end
— 10/10 scenario digests and 10/10 trajectory digests identical across arms,
5/5 closures `verified_clean`, every seed `loaded == inserted` with zero
teleports/collisions/running/waiting, and no orphaned process after the run.
The frozen key is spent; retesting C1 needs a repaired harness, a NEW frozen
identity and fresh exact-key user approval. No adoption, deployment, release
or publication follows, and `/api/close` remains the product path.

**REPAIRED AND RE-FROZEN (LUNA-PERF-21, 2026-07-24): `persistent_sumo_v2`,
content key
`fa07c8b8b356d8cd938f22a9e8b27f2b5fbc98d5deaff963bf12a838ed215e70`,
UNEXECUTED and UNAPPROVED.** Both proven v1 execute-path defects are fixed
process-free: (1) a pure `build_bootstrap_args` now starts each pool member as
`sumo -n <net> --remote-port <port> --num-clients 1` in its own work directory
and session, so a member can actually reach a TraCI client; the v2 contract
binds that bootstrap template exactly and refuses a re-keyed mutation of it.
Every TIMED query still `simulation.load`s the full fresh-subprocess argument
set — the bootstrap network is scaffolding only and no bootstrap-only option
ever enters a timed load. (2) `_variant_family` now maps production's real
filtered-route names (`calibrated.rou_close_<edge>.rou.xml` and the q10/q90
equivalents), so clean three-seed closure telemetry passes seed health while
cross-bound or malformed evidence still fails closed. v2 preserves v1's matrix,
seed/member map, ten-query order, timer boundary, report schema, shared
production builders and every strict gate, binds the finalized harness plus the
current `run_scenario.py`/network/demand/route fingerprints, sets
`outcomes_present_at_freeze:false`, and names v1 as its failed/spent
predecessor. `persistent_sumo_v1` is RETIRED in the harness and refused before
any executable boundary; its spent attempt and 264-file outcome tree are
preserved read-only and may never be rerun. There is still NO measured C1
result and no adoption authority. Any v2 preflight, execution or outcome
inspection requires a separate task and fresh exact-key user approval naming
`fa07c8b8…`. `/api/close` remains the product path.

**RESULT (LUNA-PERF-22, 2026-07-24): C1 IS A DEFINITIVE NO-GO — VALID
EXPERIMENT, HYPOTHESIS REJECTED.** The repaired campaign ran once at key
`fa07c8b8…` into `validation/persistent_sumo_campaign_v2_outcome`; the run tree
is preserved and the attempt is spent and never rerunnable. Unlike the failed
v1 attempt this run was fully ELIGIBLE, on the preserved report's own evidence:
`member_faults: 0`, `fallbacks: 0`, `pool_warmup_queries: 0` with a reported
one-time `pool_warmup_wall_s` of 3.03 s excluded from every query wall, 10/10
scenario digests and 10/10 trajectory digests equal between the persistent and
paired fresh-subprocess arms, 5/5 closures `verified_clean`, every seed
`loaded == inserted` with zero teleports/collisions/running/waiting, the frozen
alternating query order, and a report envelope matching the contract schema.
The repaired seed-health path was exercised on production's real
`calibrated*.rou_close_<edge>.rou.xml` names.
It failed exactly the two performance gates: `parallel_latency_ceiling` and
`p95_improvement_floor`. Persistent closure p95 **11.3904355838 s** vs paired
subprocess p95 **11.0998385168 s** — improvement **-0.0261802968**, i.e.
process reuse is marginally SLOWER, and both arms sit above the 10.0 s ceiling.
Baseline queries show the same pattern (persistent 6.10-6.66 s vs subprocess
6.07-6.35 s).
INTERPRETATION, per the pre-committed reading "equivalent but slow/insufficient
improvement is a definitive C1 no-go": persistent SUMO process reuse does NOT
deliver the required speed-up. What this experiment establishes is narrow and
exact — ELIMINATING PER-QUERY PROCESS CREATION DID NOT IMPROVE p95. It carries
NO phase-profile evidence, so it must not be read as showing which remaining
phase dominates a query; that would need a separate profiling task. C1 is
CLOSED — do not re-open persistent pooling as a latency lever without a new
hypothesis. Equivalence is positively demonstrated in the exact sense the gate
defines: TraCI-driven reuse reproduces artifacts that are equal under the
frozen CANONICAL SEMANTIC DIGEST, which deliberately excludes volatile
timestamps (`generated_at`/`created_at`/`finished_at`) and
path/`source_path`/`workspace` fields. That is semantic equivalence, NOT a
byte-identity claim. No adoption, production default, API, deployment, release
or publication follows; `/api/close` remains the product path, and the
10-second goal must be pursued elsewhere in Phase 7.

**Reusable identity scheme (used by the experiment above; defined, not
instantiated).** The immutable key is hex
`sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())`
with the contract's own `content_key` removed — identical to
`campaign_content_key()` in `tools/benchmark_speed.py`, so identity semantics do
not fork. `payload` must carry every identity-bearing field: schema/experiment
id and freeze timestamp; `net_sha256` and network build id; demand build id,
`demand_build_key` and calibrated variant fingerprints; source and harness
fingerprints; SUMO version and the exact argument template (`--mesosim true`,
`--meso-junction-control true`, `--meso-junction-control.limited true`, `-n`,
`-r`, `-a`, `--seed`, `--begin`/`--end`, `--no-step-log`, `--no-warnings`); the
two-arm, ten-query sequence with its exact `baseline`/`closure` ORDER, and per
query the seed↔demand-variant mapping
(`{"1000": "q50", "1001": "q10", "1002": "q90"}`), simulated window and (for
closure queries) the closed edge `26842525_26355153_0`; the deployed
`seed_workers = 3` used for the parallel p95 and the matching three-member pool
size; the per-query `timeout_seconds = 600`; the frozen gate values
(`max_parallel_p95_wall_s = 10.0`, `min_p95_improvement_fraction = 0.04`, the
health and closure-integrity requirements); the timer boundary
(per-query timing EXCLUDES one-time pool warm-up, INCLUDES the per-query
`simulation.load`) and `pool_warmup_queries = 0`; the persistent-arm lifecycle
and restart policy (seed↔member binding, cold-fallback rule, retire-on-fault
rule, terminate-and-reap-on-exit rule); trial count; and platform id. Missing any field
invalidates the key rather than widening it. No key or value is computed or
frozen here.

Until and unless that experiment is approved, executed and passes BOTH its
equivalence and its latency gate, **the product path stays the already-implemented
asynchronous validated completion**: `/api/close` starts the job and returns
immediately, `/api/close/status` polls, `/api/cancel` cancels, and orphaned jobs
are detected and cancellable at startup (not resumed). No new async work is
created or claimed here.

**6.** This study adopts no mechanism, freezes no v7, reopens no v1–v6
identity, and changes no code, test, contract, production default or
architecture.

## External Data Requests — CLOSED, no further data coming (decided 2026-07-20)

**DECISION (Gustav, 2026-07-20): there is no more data. The project ships
with the delivered 2025 six-sensor counts as its permanent measured input.**
The four requests below will NOT be sent, and their absence is no longer a
pending dependency — it is a fixed boundary. The consequences are therefore
PERMANENT honest labels, not provisional ones awaiting an unlock:

- Signal results are `synthetic` **permanently** — a real, mechanically
  safe, TSFS-certified experiment, never `city-configured`. The step-5/6
  city-import rungs of Phase 5 are closed as won't-do, not deferred.
- Through-traffic SHARE stays a sensitivity-tested prior, permanently. The
  OD matrix stays "one plausible matrix consistent with the six counts",
  as already labelled.
- Purpose labels rest on compatible generated provenance (ranked item 3,
  done) and state a behavioural class, not verified individual intent —
  permanently.
- No local road-speed/travel-time calibration; queue/roundabout/spillback
  numbers stay diagnostic, never presented as measured.

None of this stops the product. It is complete on the data it has, and the
confidence map plus these labels are exactly the honesty mechanism that
makes shipping on six sensors defensible. `docs/plans/DATA_REQUEST_2026-07.md` is
retained only as a record of what WOULD strengthen which claim, marked
not-sent; it is not an action item.

The original four (kept for the record — what each would have unlocked):

1. **City signal controller plans** (phase diagrams, timings, detectors,
   priority rules — the exact one-corridor package is spelled out in
   Phase 5's "Exact request to send" section): unlocks `city-configured`
   signal recommendations; until then every signal result stays labelled
   synthetic.
2. **A cordon count for the inner city** (all gates, one day): the only
   measurement that identifies the through-traffic share — currently a
   supply-tuned prior with a documented sensitivity sweep in
   "Consolidated Engineering Findings." Unlocks a calibrated E-E/E-I/I-I
   composition and a stronger OD-matrix claim.
3. **RVU Västra Götaland microdata** (or a regional OD matrix): upgrades
   the purpose×length priors from shrunk national ratios to local
   estimates (Phase 3), and gives the purpose-compatibility work a ground
   truth to validate against.
4. **Time-stamped link travel-time or speed observations** (normal and, if
   available, incident periods): unlock local road-speed calibration,
   travel-time validation, and evidence for queue and roundabout claims.

## Recommended Implementation Order

The original July order is complete through the registry, final sensor-output
gate, semantic demand identity, purpose/structure repair, immutable reference
release, multi-day product, job history and synthetic SignalPlan contracts.
It is preserved in Git history rather than repeated here as unfinished work.

The current order is:

```text
1. Run the speed plan's isolated S0 references on current frozen inputs
2. Benchmark routing S1A and resource-allocation S2 under the active-slot cap
3. Adopt only arms that pass paired semantic, restart, provenance and RSS gates
4. Run frozen direction Gate S if that product-value decision is still wanted
5. Obtain independent raw direction volumes or keep Gate M inconclusive
6. Add independent spatial counts/travel-time evidence before broader claims
7. Re-run the applicable frozen gates after any model, source or contract change
```

External monthly-search observation, stale-owner handling, verified completion
recovery and unowned-cancel suppression are implemented and tested; they are no
longer an unfinished item in this order.

Do not reopen completed foundation work merely because an older dated section
calls it "next". Do not treat unavailable external data as an implementation
dependency: the product remains bounded and explicit about what cannot be
validated without it.

### Historical verified status and concrete entry points — 2026-07-16

Verified end to end on the dev machine 2026-07-16 evening, after the
2027-10-20 forecast demand was REBUILT with the new code and the baseline
scenario rerun (this section describes the working tree about to be
committed as one unit):

- The active 2027-10-20 forecast baseline now carries FROZEN audit inputs
  (`provenance: demand_metadata` — the reconstructed-inputs caveat is gone),
  the raw-edgeData `output_fit` (100% GEH<5, mean abs error 2.07 veh,
  station-aggregated for 107) and per-station rows.  `validation.json`:
  counts_fit/structure/simulation/sensor_output/multi_day **pass** —
  the two long-standing structure drift flags cleared with this rebuild —
  and only `purposes` still warns (see next bullet — that warning is
  SUPERSEDED; every section is green on the current release).
- ~~Purpose compatibility remains the open P0~~ **SUPERSEDED 2026-07-20.**
  This bullet described the state on 2026-07-16, before the realism pass;
  it is retained only to date the change.  Purpose-stratified calibration
  landed and closed it: on the active release all three variants report 0
  incompatible quarters, 0 replaced routes and 0 relaxed-mix quarters, and
  `purpose_claims_allowed` is TRUE.  See ranked item 3 for the mechanism
  and evidence.  The cordon count is still wanted, but for the
  through-traffic SHARE — not for route/purpose compatibility.
- `data_in/sensors.json` has six catalogue-verified records with approved
  directed snaps; intake fails closed on pending/expired/unapproved/changed
  records and build_data revalidates resolved snaps against the registry.
- SUPERSEDED 2026-07-18 — `runs/releases/` was empty at this checkpoint.
  `golden-2025-09-16-v1` is now validated and active as described in the
  dated status entry below.
- Full suite on the dev machine 2026-07-16: **944 passed, 21 skipped**
  (~47 s).  Tests no longer rewrite the live `web/data/validation.json`
  (test_serve.py redirects the report path; found when suite runs churned
  the tracked artifact's generated_at).  A frozen release must still record
  its own exact runner and result in the release artifact.

Status of the previously listed concrete changes:

1. DONE — `validate_data_sensors` enforces quality/snap/active-date,
   `validate_resolved_edges` guards OSM drift, six-station bootstrap
   approved, negative tests in place.
2. DONE — semantic `demand_variant`/`target_key` on every seed job/result,
   `validate_variant_coverage` + publish-gate coverage check, tests for
   closure-renamed files and an all-q50 mapping.
3. DONE — `sensor_audit.output_fit` from raw pre-rounding edgeData in
   scenario JSON, `validation_report.py` (`sensor_output` section) and
   `serve.validate_staged_scenarios` (fails closed for new builds).
4. DONE (2026-07-20) — residual CLASSIFIED as unbiased stochastic
   dispersion; no bounded output correction is justified and none was
   applied.  Analysis on the active golden release's frozen audit series
   (7 directed sensor edges × 96 quarters, targets vs raw edgeData
   ensemble): (a) NOT a timing shift — MAE at lag 0 (1.9-3.2 veh/q) is
   strictly better than at ±1 quarter (4.2-8.6) on every edge; (b) NOT a
   coverage deficit — per-edge daily sums deviate +0.04% to +0.80%
   (network +0.42%, slightly positive, no end-of-day loss pattern);
   (c) volume-proportional dispersion — |residual| 0.95 veh/q below 20
   veh/q vs 3.94 at ≥60 veh/q (~5.4% of volume), signed mean +0.16 veh/q,
   and the ensemble-vs-target residual (2.62 veh/q mean) is the same
   order as the single-seed vs ensemble-mean spread (2.07 veh/q) — i.e.
   the residual is at the Monte-Carlo noise floor of a 3-run ensemble.
   A "correction" would fit noise.  Consequently the contingent temporal/
   LOSO rerun is not triggered: the release is unchanged and the standing
   post-destination-fix LOSO baseline remains authoritative.
5. DONE — normal, closure and bounded micro-signal cases are frozen under the
   active golden release, including browser/API, memory, full-suite and
   rollback evidence.

### Simulation realism pass (2026-07-17): findings and fixes

Triggered by Gustav's report that the simulation/trips "does not look
right".  Three root causes found and fixed, one follow-up designed and
partially delivered.  Deployment spec throughout: 2027-09-15 forecast
(`5699948becd95c03`).

1. FIXED — **Driven lengths were distorted by netconvert junction cutting.**
   Where two OSM ways run parallel out of one node, netconvert's junction
   hull swallowed the street: 319 of 7 125 edges were >30% off, worst case
   an 88 m street simulated as a **0.20 m lane** (traversed instantly; the
   browser showed 426 km/h), and every edge was systematically shortened
   (meso has no internal-link distance).  Fix: `build_sumo_net.py` writes
   the OSM `length` explicitly on every plain-XML edge (netconvert honours
   it regardless of lane cutting; verified), the network audit records
   `graph_length_m`/`sumo_length_m`/`length_ok` per edge, and the build
   **fails closed** on any distorted length.  netconvert warnings are no
   longer discarded (`--no-warnings` removed; digest printed, full log in
   `sumo/netconvert_warnings.log` — the suppression had hidden this class
   of problem).  After rebuild: 0 mismatches; middle-edge traversals over
   130 km/h fell 2.07% → **0.000%**; instant (same-second) edge exits fell
   18.6% → 4.3% (the rest are genuinely short edges at SUMO's 1 s output
   resolution); trip durations essentially unchanged (p50 280 → 278 s);
   GEH<5 stayed 100% on all three variants with 0 infeasible intervals.

2. FIXED — **Candidate draw density ignored expected approach flow, so
   calibration stacked convoys of identical trips.**  Measured on the
   post-length-fix build: 776 distinct shapes carried all 17 983 vehicles;
   ONE shape (Boråsleden → via sensors 1074+1076 → Eklandagatan) carried
   1 486 veh/day, with 42 clones in a single quarter — visible in the
   browser as trains of identical vehicles.  Root cause chain: (a)
   `gate_weights()` was road-class-only, giving the busiest approach
   (Boråsleden, structural load 14 244 veh/day — the field's largest) just
   0.37% of candidate draws, so its main corridor had ONE pool shape; (b)
   E-E through pairs were drawn UNIFORMLY from the verified pair lists,
   ignoring approach importance entirely; (c) the PFE's origin-edge 3× caps
   were correctly violated-and-dropped by the counts-first fallback
   (19-21% of quarter flow from one origin vs its 1.1% cap) because the
   pool offered no alternatives — the guard's own design when feasibility
   demands it.  Fix: tour ANCHOR gate draws now follow the gravity/Dial
   structural assignment field (`sumo/assignment_priors.json`; weights
   normalised so the measured-data scale factor cancels — LOSO-safe;
   road-class fallback when the field is absent), wired through
   `build_sumo_demand.py` (cache-fingerprinted) with unit tests.
   MEASURED NEGATIVE RESULT, kept for the record: extending the same
   weighting to E-E through PAIRS (probability ∝ product of the two
   gates' weights) was implemented, built and measured — it made the
   worst-shape concentration (636 → 1 465 veh/day) and structure drift
   (18.5 → 25.2% near-sensor destinations) WORSE, and was reverted.
   Reason: the candidate pool is a SUPPORT SET for the PFE, which
   reweights freely — pool value is distinct-pair coverage, which the
   uniform draw maximises; anchor draws differ because rejection sampling
   makes their density decide which corridors exist in the pool at all.

3. FIXED — **Two web-app defects that made the simulation view lie.**
   (a) Colour semantics: non-sensor edges fell back to "count / own max",
   so EVERY street reached full red at its own peak — at rush hour the
   whole city lit up alarm-red next to visibly sparse vehicles.  Scenario
   providers now expose a per-edge calm midday (10:00-15:00) mean from
   their own flows; the renderer uses it with the same "vs calm daytime"
   semantics as sensor edges, with an absolute floor so a street under
   ~20 veh/15 min can never show alarm red.  Legend text updated.
   (b) `?mode=scenario` deep links switched the provider underneath the
   new workspace landing page without dismissing it; they now route
   through `openWorkspace()`.

4. FIXED (superseding the "signature-conditioned densification" follow-up
   drafted earlier the same day) — **Exact-shortest-path naturalness was
   the real root cause of endpoint inaccuracy** (Gustav: some areas get no
   trip starts, streets on/near sensors get far too many).  Every
   naturalness check (`via_is_natural_in_cost_matrix`,
   `natural_far_end_weights`, `natural_sensor_masks`,
   `natural_origin_weights`) required the sensor to lie on the EXACT
   shortest path (±0.5 s).  Measured on the real network: 6 of 7 sensor
   edges had ZERO verified through gate pairs (city-wide union: 2 pairs —
   ALL 6 000 through candidates, 76% of calibrated traffic, entered at 2
   street cuts and exited at 1), and tour destination masks admitted only
   the shadow cone immediately behind each sensor (destinations 100-200 m
   from a sensor: 18.0% of trips vs 1.2% of edges).  Real route choice is
   stochastic-multipath — the same finding assignment_priors.py already
   validated.  Fix: bounded-detour naturalness, `via − direct ≤ max(45 s,
   0.20 × direct)` (constants `VIA_DETOUR_ABS_S`/`VIA_DETOUR_FRAC`),
   admitting 18-58 pairs per sensor (union 265); the exact ±0.5 s rule
   stays only in `shortest_paths_use_node`, whose U-turn-guard purpose
   genuinely needs it.  Measured after full rebuild (same spec, GEH<5
   100%/0 infeasible on all variants, 974 tests green):
   - dests within 200 m of a sensor **18.5→2.8%** (baseline 1.9) — gone;
   - onward-after-sensor median 1 115→**2 904 m**, under-200 m 14.6→1.4%;
   - trip-length L1 vs RVU 0.69→**0.286**; structure gate **pass, zero
     flags** (first genuine pass on the length-corrected network);
   - through pool 2 origins/1 dest → **28 origins/19 dests** (every
     entry gate used); OD matrix now spans all 8 compass sectors;
   - worst shape 636→**111 veh/day**, ≥10-clone convoys 13.9→**0.9%**,
     distinct shapes 813→1 238, distinct trip origin edges 234→290;
   - purpose-incompatible through routes ~9 000→**~3 250** per variant
     (then to **0** by item 5 below — the purposes P0 is closed);
   - vehicles 17 097→21 338: single-sensor passages replace artificial
     multi-sensor chains (3+ passages 1 910→649), so the same counts
     need more, more-local vehicles — expected and more realistic.

5. CLOSED the same day — **purpose-stratified PFE** (implemented in the
   parallel session; pfe.py/demand/calibration.py/validate_sim.py): PFE
   variables are now (geometry × purpose provenance), the solver enforces
   each quarter's generated purpose mix as required groups in a two-stage
   solve (counts first, then the exact margin; counts-first fallback with
   honestly-reported mix deviation instead of relabelling), strict
   provenance allocation raises rather than fabricating a label, all
   variants stage-then-flip atomically, and the LOSO fold path uses the
   identical formulation.  Also fixed: sub-day windows read the correct
   purpose-mix clock (a 06:00 build no longer inherits the midnight mix).
   Verified on the combined rebuild (2027-09-15 forecast spec):
   **validation.json PASS overall with zero warnings — every section
   green for the first time** — purpose_incompatible 0/0/0,
   mix_relaxed 0/0/0, purpose_claims_allowed true, GEH<5 100% on all
   variants, 0 infeasible, 984 tests passing.  Final trip realism:
   1 492 distinct shapes, worst 127 veh/day, no shape ≥200/day, shapes
   ≥50/day carry 14.0% of vehicles (was 53%).  One disclosed relaxation:
   q90 quarter 85 relaxed 1 structural bound edge-quarter (sensor
   constraints retained).

6. VERIFIED + HARDENED 2026-07-18 on the historical reference day
   (`2025-09-16`) — the remaining purpose warning was not a candidate-support
   failure: every affected quarter had a feasible exact integer margin, but
   the local MILP abandoned it from a poor directly-rounded starting vector.
   Publication now retries exact provenance after count/bound repair and
   again after the optional structure repair supplies its final warm start.
   Result on the unchanged 9 424-candidate pool: q50/q10/q90 all remain
   **100% GEH<5, 0 infeasible, 0 purpose-incompatible, 0 mix-relaxed and 0
   structural flags**; `validation.json` is PASS with no warnings or missing
   sections.  The normal and Skånegatan reference-closure scenarios were
   rebuilt from that same demand release (all vehicles inserted, 0 teleports).
   That rebuild also exposed and fixed two scenario-publication regressions:
   legacy `--close` second offsets are now converted to bounded ISO
   `ClosureSpec` datetimes, and edges omitted by SUMO's
   `excludeEmpty="true"` edgeData are retained as measured zero specifically
   for closure-integrity evaluation.  Full suite: **1 011 passed, 20
   skipped**.

   Leakage-free LOSO was rerun under `loso_pfe_meso_v3`: held-out
   simulated/measured ratio min **0.76**, median **0.99**, max **2.58**.
   This is useful but not uniformly strong generalisation: edges 134 and
   2276 remain high at 2.41 and 2.58, and one direction of station 107 is
   2.11 while its opposite direction is 0.99.  Keep those outliers visible
   in confidence/reporting; a good median is not permission to claim every
   held-out road is accurate.

7. CLOSED 2026-07-18 — **the temporal holdout is now an executable,
   stale-safe release artifact**, not a manually quoted second-day run.
   `validate_sim.py --holdout-date YYYY-MM-DD` keeps the current candidate
   pool, network, assignment scale, structural priors and through-share
   contract frozen, moves the measurement window to a distinct same-day-type
   historical date, calibrates every fold only on the other stations, and
   reserves the held station's later-date values for evaluation.  It rejects
   same-day, cross-year, unlike day-type and <90%-coverage comparisons, writes
   atomically to `web/data/temporal_holdout_report.json`, and leaves the
   production demand/scenarios untouched.  `validation_report.py` includes
   the evidence only when candidate/network hashes, source, reference window
   and through-share target still match the current release; stale evidence
   becomes explicitly missing.  Reproducible command: `make
   validate-temporal`.

   Frozen 2025-09-16 release evaluated on independent 2025-09-17:
   min **0.757**, median **0.881**, max **2.536**, with 95–96 observed
   quarters on every directed sensor edge.  The same residual pattern
   survives across dates: 134/2276 remain high at 2.511/2.536 and one
   direction of 107 is 1.965, while the other four edges are 0.757–0.881.
   This is temporal stability evidence, not a claim that every corridor is
   accurate.

8. CLOSED 2026-07-18 — **the first golden release is validated and active.**
   `golden-2025-09-16-v1` contains 22 integrity-checked
   artifacts: the normal and Skånegatan scenario/trajectory pairs, all three
   q10/q50/q90 normal and closure route files, producer manifests,
   demand/network/sensor/validation evidence, and a fresh bounded microscopic
   signal smoke. Release schema v2 stores each case in its own directory so
   same-named manifests cannot collide, and activation now fails closed unless
   the full suite, browser/API smoke, peak-memory measurement, and rollback
   exercise are all explicitly passing. Golden rollback revalidates the
   predecessor's complete bundle and gates before flipping the pointer, so a
   damaged former release cannot be restored merely because it was once active.

   Final gate: **1 026 passed, 20 skipped**; release integrity has zero
   errors; the local API serves the expected 2025-09-16 demand signature,
   two-scenario manifest, clean closure integrity and PASS validation report.
   Isolated reruns are semantically identical to the frozen normal, closure and
   signal artifacts, with byte-identical representative trajectories. Peak
   RSS: normal **357 040 128 B**, closure **353 927 168 B**, bounded micro
   signal **222 052 352 B**. Manual Chrome validation loaded the real Scenario
   workspace and exposed one red console error: Leaflet's source-map request
   was blocked by the CSP. `serve.py` now narrowly permits the already-trusted
   pinned `unpkg.com` origin in `connect-src`; the security regression test
   passes and Gustav confirmed a clean Console after reload.

   The real registry pointer was exercised, not only a temporary unit-test
   root: ordinary bootstrap A→B→A first established the initial predecessor,
   then two complete golden clones were activated A→B and
   `rollback_golden_release()` restored A after revalidating its complete
   bundle/gates. The one-day release was then activated and later became the
   validated rollback predecessor of the two-day release described below.

9. CLOSED 2026-07-18 — **the continuous two-day golden release is validated
   and active.** An isolated historical build for
   2025-09-16→18 produced 192 monotonically ordered quarters. Every q50/q10/
   q90 variant passed **166/166 hourly sensor checks on each day** with zero
   infeasible intervals. The three continuous SUMO seeds inserted
   43 857/43 857, 42 548/42 548 and 45 224/45 224 vehicles with zero
   teleports. Final raw edgeData passed 168/168 directed sensor-hours per day
   and 144/144 physical station-hours per day; worst GEH was **1.320** on day
   1 and **2.985** on day 2.

   The first attempted publication was correctly rejected twice and exposed
   two contract bugs instead of hiding them. First, 15-minute GEH treated
   ordinary route travel across an adjacent quarter as lost demand
   (sensor 133, day 2 21:30: target 0, ensemble 12.67). PFE already uses the
   standard hourly GEH metric, so the final-output contract now retains every
   raw 15-minute value but declares and recomputes four-quarter/hour sums,
   independently per date. All 336 directed and 288 station hours pass; the
   worst hourly result is 2.985. Second, SUMO's `t=0` summary can already show
   one loaded vehicle. Day-1 deltas now use the mathematical zero baseline
   while retaining that observed snapshot for audit; the publisher
   independently revalidates the corrected cumulative counters.

   Midnight continuity is explicit: three q50 vehicles were pending insertion
   at the first boundary, 13 were actively driving across midnight, and all
   were accounted for on day 2. The 30.1 MB q50 trajectory product contains
   all 43 857 vehicles, no non-monotonic exit series, no exit before departure,
   and no unfinished vehicle. Against independently built one-day references,
   all **336/336** continuous-vs-reference directed sensor-hours pass GEH<5
   (worst 0.868/1.723), frozen targets are identical, daily q50 vehicle totals
   differ by only +0.466%/+0.432%, and citywide daily flow-vector cosine is
   0.9876/0.9848. Full regression: **1 034 passed, 20 skipped**.

   Gustav then exercised the staged 192-quarter result in the real browser at
   the midnight boundary and confirmed “de funkar”: the day label and clock,
   moving vehicles, scenario/validation panels, network requests and console
   showed no issue. The immutable 36-artifact release
   `golden-2025-09-16-2day-v1` passed integrity, API, browser, full-suite,
   peak-memory and rollback gates and was activated. `latest.json` now points
   to it with `golden-2025-09-16-v1` preserved as the validated rollback
   predecessor; both release validators return zero errors. It is retained as
   the rollback predecessor of the seven-day release below.

10. CLOSED 2026-07-18 — **normal studies through seven continuous days are
    validated and active.** Exact 3-, 4- and 5-day calibrations passed all
    daily q50/q10/q90 input gates. The final 2025-09-16→23 study produced 672
    quarters and independently passed all seven dates at 100% GEH<5 with zero
    infeasible intervals or hard-bound violations. Its three SUMO variants
    inserted 147 405/147 405, 143 704/143 704 and 154 035/154 035 vehicles,
    with zero teleports and zero unfinished vehicles at the end.

    Raw final SUMO output passed 100% of 1 152 directed sensor-hours and 984
    physical station-hours (range maximum GEH 2.000); each date separately
    passed 100%, including day 6 (maximum GEH 1.447). Every midnight boundary
    has fresh per-seed accounting with zero boundary lag and zero queue. A
    separate six-day calibration was intentionally not run: the user chose
    speed over a redundant prefix build, and day 6 is already tested as a
    first-class daily row inside the stricter week.

    Browser playback remains real-vehicle playback but is now bounded and
    deterministic: SHA256-lowest selection keeps at most 10 000 q50 vehicles
    per day, 70 000 of 147 405 for the week (51.8 MB, below the 96 MiB
    publication limit). All vehicles still drive in SUMO and contribute to
    flows and confidence; only the optional visual payload is sampled. The
    scenario took 96.56 s and peaked at 1 959 968 768 B RSS. Staging,
    validation-report and exact HTTP payload checks passed. The in-app browser
    was unavailable for a new visual run, so the visual basis remains
    Gustav's accepted continuous two-day boundary playback using the same UI
    contract; this limitation is recorded in the release evidence.

    Publishing the three independent q50/q10/q90 route variants in parallel
    reduced the exact three-day build from 1 085.90 s to 800.26 s (about 26%)
    while all six route/agent artifacts remained byte-identical. Full
    regression is **1 039 passed, 20 skipped**. Immutable release
    `golden-2025-09-16-7day-v1` passed integrity and rollback, was activated,
    rolled back to `golden-2025-09-16-2day-v1`, then reactivated; both
    validators return zero errors.

Honest status note: the 45 s / 20% detour constants are
literature-plausible route-choice bounds, chosen from the measured
admission curve (10%→157, 20%→265, 30%→464 pairs); they are assumptions
and the LOSO + temporal evidence above still covers only seven directed
near-field edges in two clusters.  It confirms a repeatable residual pattern,
not every road, weekend/holiday transfer, or citywide accuracy.

## Definition of Success

### Completed: exact monthly warm-state reuse (2026-08-03)

The warming accuracy blocker is closed. The private mesoscopic tripinfo
accumulator omitted by SUMO state serialization is transported through exact
unfinished-tripinfo identity reconstruction, and whole-vehicle values use
SUMO-compatible decimal half-up formatting. The fresh v16 paired campaign
passed 3/3 exact semantic comparisons and published three certified states.
Those states are installed atomically in the product cache and the monthly
command now selects warm execution by default with an explicit cold escape
hatch and fail-closed fallback. Measured cache-hit runtime improved from
88.506 s cold to 71.568 s warm across q10/q50/q90 (19.1%). The next performance
work is coverage and connection-safe parallelism, not another accuracy retry.

### Superseded: narrow 2027 candidate-free population (2026-08-03)

The coverage and isolated-population foundation is ready at annual plan key
`b89e4a5e…105a542`. It binds 363 eligible dates, 1,089 closure slots, 363 exact
daily demand contracts and 3,267 production-mapped requests
(`1000→q10`, `1001→q50`, `1002→q90`). The compact store retains the exact route
input because future departures are absent from SUMO state files; a byte-exact
pilot reduced 375,668,139 unique original bytes to 33,316,391 stored bytes
(8.87%) with zero restore mismatches.

A real three-worker SUMO/TraCI pilot populated the July 15 06:45 checkpoint for
all three variants with zero failures. The production root is initialized with
all 3,267 units pending and can resume by rerunning the frozen command in
`validation/annual_warm_readiness_v1.json`. This is population readiness only:
annual artifacts remain candidate-free and uncertified for product reuse, so
route-safe binding, equivalence evidence and cold fallback are unchanged. Full
population under that narrow plan was not started and the plan is no longer
eligible for execution.

### Historical, superseded run: audited full-day 2027 population (started 2026-08-04)

Plan `de071336…f203db` is the active plan; it supersedes `9cc823d3…45283b` and
every earlier root after the candidate/demand release was extended to all 7,125
routable edges, the disk architecture was corrected (archive pruning,
proportional gate, LZMA encoding) and the transient-launch retry policy was
added. Each of those edits changed a fingerprinted source, so the source seal
correctly forced a fresh plan key and root — that is the seal working, not
churn. It retains every
15-minute-aligned
00:00–24:00 independent daily interval. Exact source-year and DST rules support
1,682,634 of 1,699,440 interval placements; these collapse to 367 canonical
demand builds, 34,895 checkpoints and 104,685 q10/q50/q90 states. Unsupported
envelopes remain explicit cold fallbacks rather than synthesized coverage.

Population is organized into exact demand-build/seed/variant chains. Only the
first checkpoint in a chain starts at zero; each later checkpoint validates and
extends its predecessor. This removes the previous design error where 104,685
distinct keys implied 104,685 independent prefix simulations. Real q10/q50/q90
SUMO diagnostics reproduce exact prefix evidence and closure metrics after
chaining. A late favourable checkpoint reduced direct candidate runtime from
16.726 s to a 6.773 s cache-hit suffix; a 900-second adjacent extension also
beat a new prefix run.

The final maximum-depth q10 pilot completed all 96 links with zero failures.
Route-window shards prevent the full three-day route from accumulating in every
saved state; expanded states remain 1.24–1.59 MiB. Independent cold checks at
links 2, 48 and 96 match every behavioural evidence section exactly. The sole
byte difference is the recorded non-behavioural `loaded` lookahead count from
the cold full-route parser; inserted/teleport counters and all vehicle evidence
remain exact gates. Native millisecond accumulator handoffs are pinned by a
96-link regression.

Warm-cache schema v3 removes the Git commit from effective identity while
retaining exact source/input/runtime fingerprints, so documentation-only
commits no longer invalidate states. Historical schema-v2 entries remain
fail-closed and are not silently promoted.

The final pre-run audit also removes scale-only overhead that would have become
hours during population: plan context is indexed once, one runner is reused per
worker/current demand build, archive validation records are forwarded from the
main process, predecessor restore selects only state/prefix evidence, and SQLite
finishes each dependency-ready batch transactionally. Semantic orphan recovery
now validates prefix, demand and SUMO state contents before promotion, and
provisional monthly workspaces are cleaned on success or failure. Bound
measurements and official SUMO references are recorded in
`docs/reviews/WARMING_FINAL_AUDIT_2026-08-03.md`.

Progress is transactional SQLite rather than a 100k-entry rewritten JSON
ledger. The pre-run audit added exact immutable-row/lifecycle verification,
SQLite integrity checking, orphan-artifact reconciliation, non-replacing
manifest publication, archive-to-member hash binding, unique atomic temp files,
symlink rejection, shared inter-process demand-build ownership, runtime/source
plan provenance, and realistic initial/runtime disk gates. Three persistent
spawn-isolated workers bind the plan once and retain private TraCI connections.

A measured canonical three-day archive occupies 326 MiB and the q10 96-link
store occupies 40 MiB. **Superseded 2026-08-04: the flat 192-GiB gate was
sized around retaining all 367 three-day archives at once (367 x 326 MiB ~=
117 GiB), which dwarfed the ~42 GiB of artifacts.** Retention was never
necessary — `pack_artifact` already binds the route, demand meta, build spec
and manifest as content-addressed blobs, and the group loop only resolves an
archive while that build still has selectable units. Three changes replaced the
gate:

- **Archive pruning.** `_prune_demand_archive` deletes a three-day archive once
  every unit for its build is durably succeeded, refusing if any unit remains
  selectable, if the path is outside `runs/`, if it is not named `demand-*`, or
  if it is a symlink. `--keep-demand-archives` opts out. Peak archives fall from
  116.8 GiB to 0.6 GiB.
- **A proportional gate.** `required_free_bytes` derives the requirement from
  the units this invocation can actually select, at a measured per-unit rate.
  The flat constant had refused every bounded pilot for archives it would never
  build, which made `--max-units`, `--demand-build-key` and `--variant`
  unusable on any realistic disk.
- **LZMA as a third store encoding**, chosen per member from measured output
  size: route 6,371,443 -> 950,432 B (14.9%), prefix evidence 181,037 ->
  110,204 B (60.9%). SUMO's already-gzipped state correctly stays `identity`;
  a ratio guard skips the LZMA attempt so no chain pays CPU for zero bytes.

Projected peak is now 42.0 GiB against a 55.8-GiB preflight requirement (the
gate charges the unsealed rate, so real usage falls below it as chains
complete). Preflight passes and full population is RUNNING.

A further 3.2x was measured but deliberately not taken: `prefix_evidence.json`
stores a cumulative record at every link, 97.8 MB of JSON per chain carrying
665 KB of new information (0.9%). Sealing a whole chain as one LZMA stream
collapses evidence to 0.23 MB and states to 2.79 MB, taking the store to
~4.1 GiB and the peak to ~13 GiB. Two measured caveats: containers must group
like members together (interleaving states and evidence costs 16.6 MB instead
of 3.0 MB), and the state saving only materialises if states are stored
EXPANDED — which means asking SUMO for uncompressed state XML and therefore
re-running the 96-link chain audit. It was not done because it would invalidate
a passing audit immediately before the run.

**Transient-failure policy (2026-08-04).** The first production run aborted
after 311 units when one worker's SUMO did not accept its TraCI socket
("Could not connect in 61 tries") while its two siblings started normally, 38
units into a build's chain. Aborting a multi-day population for a startup
hiccup discards every completed chain, and over 104,685 units such a hiccup is
near-certain to recur. `_is_transient_launch_failure` now permits at most
`TRANSIENT_UNIT_RETRY_LIMIT` retries for process/socket startup failures only;
every validation, provenance, artifact and semantic failure still aborts
immediately, because those mean the bank would be wrong rather than merely
delayed. Retries are printed, never silent.

Release, adoption and any proxy licence remain separate evidence gates and do
not weaken exact exhaustive execution.

The project has reached its intended next level when:

- a new sensor is added through a validated data record, triggers a new build,
  and reports the uncertainty it actually reduces;
- normal and closure simulations share one validated demand release;
- the closure tool can explain whether a road should be closed, when, and why
  a candidate is rejected or uncertain;
- the signal tool provides phase-level green/red seconds for both normal and
  closure demand, with the signal plan provenance made explicit;
- all results remain fast enough for the intended workflow without hiding
  model limitations;
- the user can inspect the exact evidence behind every recommendation.

## Explicitly Deferred Until Evidence Exists

- Claiming that synthetic SUMO signals are Gothenburg's actual controllers.
- Calibrating car-following, lane-changing, queue tails, or road-speed
  distributions without independent local speed/travel-time data.
- Claiming that every added sensor improves every edge rather than measuring
  its actual information contribution.
- Replacing the fast citywide mesoscopic model with citywide microscopic
  simulation.

## Closure-search scaling status — 2026-08-11

The implementation cap that rejected the plan's six-month 360-hour example is
removed from the web product path without making partial searches look
exhaustive. Enumeration is now paged transactionally at parent boundaries,
with distinct per-invocation and cumulative unit limits, immutable checkpoints,
real resume, early termination before the simulator stack and a versioned
API/UI resource policy. A multi-leg regression reproduces the uninterrupted
754-parent/910-unit result exactly, and the named 11,813-parent/23,349-unit
preflight is classified as runnable.

The process-free cost provider and current runner are re-frozen in
`validation/closure_cost_ordering_golden_v4.json`; the record reproduces byte
for byte. The real v3 benchmark exposed and preserved a restart-identity bug;
v4 reran exhaustive, cost-ordered and fault-injection/resume after the fix.
Semantics and restart matched, but both arms verified 13/13 and ended
`no_viable`, so the required positive saving was zero. Policy v3, held-out
adoption, unrestricted UI claims and global-best therefore remain closed.

Libsumo remains a measured non-adoption: SUMO 1.27.1 and `libsumocpp.dylib`
exist on the development host, but its Python binding does not. No dependency
is installed and the current process-isolated TraCI backend remains the
production reference. New external demand/work-zone data and citywide micro
calibration remain outside the authorized scope; microscopic confirmation is
therefore conditional on real finalists and available calibrated evidence, not
fabricated as a plan-completion artifact.

Post-review hardening on 2026-08-13 closed the remaining source-level budget
findings: unknown stop markers produce readable diagnostics, unused RSS/ledger
runtime fields were removed, the parent ceiling cannot diverge between CLI and
budget, irrelevant budget flags fail explicitly, and a first parent larger
than one page fails instead of emitting a cursor that cannot advance. The
checkpoint/result type split remains deliberate: paused work has no shortlist
or normal execution payload and cannot reach final-result materialization.

The review also found that `adf765b` changed the independent-day cold horizon
after the v16 warm equivalence campaign. Warm execution is now gated per
observation: it can run only when the selected cold window equals the full
window v16 covered. A differing window runs trimmed-cold and records the
unproven reason. A future trimmed-warm optimization needs a new paired campaign;
the old v16 result is not silently generalized. The resulting source drift is
bound by a new process-free `closure_cost_ordering_golden_v4.json`; v1-v3 were
not overwritten.

The first real multi-month API run on integrated `main` found and closed a
parent/child workspace-lock deadlock. The fixed rerun traversed the full product
sequence for 2027-03-22 and 2027-07-15 and published a complete period
comparison. It ended `no_viable` after the two finalists timed out in adaptive
collection, without raising the 300 s limit or manufacturing a winner. The
diagnostic record is `validation/monthly_multimonth_e2e_outcome_v1.json`.

The next benchmark was not allowed to choose another single case after seeing
v4. `validation/cost_ordered_benchmark_registration_v5.json` instead froze a
four-case, outcome-blind suite across two archived dates and four distinct
directed edges; commit `259c492` was pushed before execution. The associated
outcome measured 18 fewer SUMO candidate verifications and three cases with at
least two health-viable pilot candidates, but the aggregate equivalence gate
failed. The strongest case saved 11/13 and retained the same unique winner,
selected ids, hard failures, health and restart result, yet strict cost-field
identity still failed because four timeout observations had null exhaustive
post-SUMO fields. Two other cases classified the same candidate/seed on
opposite sides of the fixed 300 s wall-clock limit in the two execution
orders, changing finalist and restart evidence; the fourth was field-identical
but saved nothing. Every stop proof was valid. This is negative evidence, not
permission to raise the timeout or weaken a gate. Policy v3, held-out, micro,
unrestricted UI and global-best remain closed.

## Historical supersession note — 2026-08-09

The earlier statement that a full population was running described a prior
warming identity and was historical evidence, not current execution state even
at this later checkpoint.
Production source changes created plan `9b640a0c…`; its preflight passes, but
only two deliberately bounded q10 pilot units have been populated. The current
status is 2 succeeded, 0 failed and 104,683 pending. Full population remains a
separate decision. The active local demand build is `dbb44172f30778adf8c0`,
with zero short-trip cap violations and zero unanchored vehicles. Fresh
temporal LOSO remains structurally underidentified and fails the TAG-aligned
aggregate, so no absolute validation claim is made.

## Route-specific passage support — 2026-09-09

The automatic passage stage now treats a fixed departure-shift grid as a fast
first representation rather than an assumption that every feasible sensor fit
must satisfy. If the fit introduces a short-trip concentration, it adds the
affected departure-quarter class to the optimization with the publication
gate's exact integer semantics. An infeasible grid expands to route-specific
sensor-passage change points and deduplicates alternatives by their complete
observation signature. This retains physical routes, OD/purpose conservation,
sensor targets and PFE edge bounds.

The exact 2027-04-28 failure was replayed as a fresh three-arm day build. Its
q10 arm exercised the boundary fallback, preserved all 18,889 route and
OD/purpose records, removed the quarter-2 short-trip excess and reduced
independent-seed passage error from 4,405 to 30. The build completed rather
than failing after PFE. This is diagnostic evidence for the known failure, not
a completed monthly search or approval of pre-existing purpose-length drift.
Detailed evidence is in
`validation/automatic_passage_structural_repair_20260909.json`.
