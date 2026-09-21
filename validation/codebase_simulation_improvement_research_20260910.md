# Förbättringsplan för trafikmodellens hastighet och simuleringskvalitet

> Uppdatering efter juni-körningen: den aktuella omprioriteringen och nya
> mätningen finns i [IMPROVEMENT_PLAN.md](../IMPROVEMENT_PLAN.md#current-reassessment--completed-june-search-2026-09-10).
> Den ersätter ordningen och kvalificerar tidsmålen nedan. Nytt huvudspår:
> förklara 49 passagekalibreringar för 31 kalenderdagar innan cachepolicyn ändras.
> Uppdatering 2026-09-10: gapet är nu mätt read-only ur dagbibliotekets manifest
> — 19 av 30 datum kalibrerades två gånger, varje gång på grund av olik
> `pool_composition`, ingen annan orsak och ingen icke-determinism. Se
> [dagåteranvändningsplanen](../docs/plans/DAY_REUSE_EXPLAINABILITY_PLAN_2026-09-10.md),
> som är punkt 1 i förbättringsplanen sedan projektägarens omprioritering.

## Sammanfattning

Kodbasen har redan flera starka egenskaper: exakta aktiva sensorkrav, bevarade OD- och ändamålssummor, färska SUMO-körningar efter optimering, innehållshashad proveniens och felstängda publiceringsgrindar. Den senaste passagekalibreringen minskade på en isolerad heldag det summerade sensorfelet från 4 673 till 16 utan nya relaxeringar. Den största kvarvarande svagheten är därför inte träffsäkerheten på de sensorer som används för kalibrering, utan att aktuell rumslig och tidsmässig generalisering saknas.

Hastighetsmässigt är den senaste uppmätta **passagekalibreringen för en kall heldag** 53,5–58,9 sekunder för tre varianter. Det är ett steg i efterfrågebygget, inte hela byggets väggtid. Av passagesteget är 9,4–10,2 sekunder unionen av aktiv SUMO-väggtid och 15,7–22,5 sekunder solver-tid; cirka 25 sekunder per dag är ännu ospecificerad initiering, XML/JSON-arbete, validering och serialisering. Därför bör nästa steg vara finmaskig profilering och fullskalevalidering av den redan förberedda processparallella körningen av `q50`, `q10` och `q90`. Den lovande men ännu ofullständigt verifierade parallelliseringen har större sannolik effekt än fler solverjusteringar eller kompressionsändringar.

Byggtimern är nästlad. I det analyserade kalla tredagarsbygget var `pfe_variants_and_rounding=133,612 s` och den därunder uppmätta `dynamic_passage=112,406 s`; värdena får inte adderas. När den aktiva månadssökningen träffar det kvalificerade dagbiblioteket uteblir `dynamic_passage` och de senaste 20 kompletta dagarna låg på 5,07–6,06 sekunder i PFE-steget. Hela kedjan skapade 35 lyckade körkataloger med 23,59 sekunders median mellan manifest, men den kadensen omfattar även övrigt bygg-, publicerings- och orkestreringsarbete och är ingen ren funktionstimer.

Den viktigaste simuleringsförbättringen är en mätmodell som motsvarar de fysiska sensorerna. Nu används SUMO `edgeData entered`, vilket räknar när ett fordon går in på en kant. En verklig slang, radar eller induktionsslinga står på en bestämd position och ofta på en eller flera bestämda körfält. SUMO:s E1-detektorer kan modelleras på exakt körfält och position med 900 sekunders aggregation. Det bör införas som en prövbar alternativ mätväg och jämföras med dagens kantinträde innan den ersätter något.

Efter en andra kodgranskning mot Claudes rapport är rekommenderad ordning:

1. Reparera det levande kraschfelet i kartans sensorvalidator och de två mindre kontraktsfelen.
2. Instrumentera de cirka 25 okända sekunderna i en kall passagekalibrering och verifiera trevariantsparallellisering i full skala.
3. Kompaktera framtida `demand_meta.json` utan att ändra objekt eller `build_id`; gör borttagning av det duplicerade kontraktet som en separat schemamigration.
4. Bygg färsk rumslig och tidsmässig holdout innan fler ruttparametrar aktiveras.
5. Modellera fysiska sensorpositioner med E1 och samla hastighet/ködata med E2 där sådan fältdata finns.
6. Kalibrera nätets utbudssida, särskilt verkliga signalprogram och flaskhalskapacitet, innan efterfrågan får kompensera för nätfel.
7. Utvärdera ruttregularisering och alternativa ruttsamplingsmetoder som challengers under fryst holdout.
8. Mät processuppstarten direkt och testa därefter persistent TraCI med `simulation.loadState` endast om den kvarvarande väggtiden motiverar komplexiteten; behandla `libsumo` som ett separat socketexperiment.

## Evidensnivåer

Rapporten använder tre etiketter:

- **Uppmätt:** resultat från sparad lokal körning eller artefakt.
- **Beräknat mål:** en acceptansgräns för nästa experiment, inte ett redan uppnått resultat.
- **Forskningshypotes:** tekniskt motiverad förbättring vars effekt måste mätas på frysta indata.

Ingen föreslagen hastighetsändring får minska antalet aktiva sensorkrav, ta bort hälsokontroller, återanvända en annan population enbart för att datumet matchar eller acceptera en annan fordonsfil som ”ekvivalent”.

## Nuläge i kodbasen

| Del | Lokal evidens | Slutsats |
|---|---:|---|
| Passagekalibrering för en kall heldag, tre varianter | 53,49 s och 58,88 s | Realistisk referens för just detta steg när dagbiblioteket saknar produkten. |
| Aktiv SUMO-väggtid i passagesteget | 10,19 s och 9,42 s, 27 körningar | Exakt reproducerad som unionen av arkiverade `clockBegin`/`clockEnd`. Processstart och nätparsning före `clockBegin` ingår inte. |
| Solver | 15,71 s och 22,47 s | Betydande men redan kraftigt förbättrad med gles differensformulering. |
| Övrig tid | 25,45 s och 24,98 s | Största okända posten och första profileringsmålet. |
| Evidenskomprimering | 2,14 s och 2,01 s | Inte huvudproblemet. |
| Återanvändning av validerat passagesystem | median 2,00 → 1,70 s per variant | Säker mindre vinst, cirka 0,9 s per dag om effekten håller för alla tre varianter. |
| Kallt tredagarsbygges PFE-wrapper | 133,61 s, varav `dynamic_passage` 112,41 s | Timerintervallen är nästlade; 112,41 s ingår i 133,61 s och ska inte adderas. |
| Aktiva katalogträffar | PFE 5,07–6,06 s för de senaste 20 kompletta dagarna | Visar att dagbiblioteket undviker ny passagekalibrering; det är inte en kallprestandamätning. |
| Aktiv byggkadens | 35 manifest, median 23,59 s mellan färdiga kataloger | End-to-end-observation under pågående sökning; omfattar mer än PFE och ändras med belastning och I/O. |
| Tidigare solverändring | passage 231,6 → 63,9 s på en trearmsdag | Stor uppmätt lokal förbättring; inte ett argument för att släppa validering. |
| Passageprecision, isolerad q50-dag | absolutfel 4 673 → 16 | Kalibrerade sensorer matchas mycket väl. |
| Aktuell releasevalidering | `overall=warn`; spatial och temporal holdout saknas/inaktuella | Generalisering kan inte godkännas i nuläget. |
| Äldre LOSO, annan källa/pool/dag | stationskvoter 0,685–2,613; GEH-pass 16,7–91,7 % | Historisk varningssignal om överanpassning, inte en aktuell resultatmätning. |
| Nät | cirka 15 MB, 27 647 kanter, 7 206 korsningar, 82 gissade signalprogram | Nätparsning och felaktiga signalplan är relevanta separata problem. |
| Artefaktlager, föränderlig ögonblicksbild 16:26 | 88 GB under `runs/`; 132 `demand_meta.json` på totalt 3,7 GB | Lagring och filsystemstryck behöver innehållsbaserad deduplicering och kontrollerad gallring. Tidigare 171 GB beskriver inte den aktuella katalogen. |

Källor i kodbasen: [`traffic_sim/demand/automatic_passage.py`](../traffic_sim/demand/automatic_passage.py), [`run_scenario.py`](../run_scenario.py), [`build_sumo_net.py`](../build_sumo_net.py), [`web/data/validation.json`](../web/data/validation.json), [`web/data/loso_report.json`](../web/data/loso_report.json), [`passage_runtime_analysis_20260910.json`](passage_runtime_analysis_20260910.json), [`passage_solver_performance_20260909.json`](passage_solver_performance_20260909.json), [`passage_source_reuse_20260910.json`](passage_source_reuse_20260910.json) och [`libsumo_preflight_v2.json`](libsumo_preflight_v2.json).

## Dubbelkontroll av Claudes fynd

| Fynd | Beslut efter oberoende kontroll | Evidens och robust åtgärd |
|---|---|---|
| B1 – `merge_day_reports` kraschar på `passage_calibration: null` | **Bekräftat, latent validatorfel.** En ensam rapport med `null` passerar den första vakten och ger sedan `AttributeError`; en blandad dict/`null` avvisas redan korrekt. | Kräv antingen att alla poster saknas/är `null`, eller att samtliga är mappings med `status=validated` och samma policy. Lägg regressionstest för alla-null, blandat, fel status och giltigt fall. |
| B2 – `run_one` har fel returtyp | **Bekräftat.** Annoteringen anger sju tuplefält, funktionen returnerar åtta och anroparen packar upp åtta. Körningen påverkas inte idag. | Korrigera typen eller ersätt positions-tuplen med en namngiven, fryst resultattyp. Lägg en begränsad statisk typkontroll för detta gränssnitt; full mypy-migrering är ett separat arbete. |
| B3 – `check_map_matches_sensors` kraschar innan den rapporterar identitetsfelet | **Bekräftat och levande.** Under kontrollen hade live-demand 288 kvart medan publicerad baseline hade 96 och en annan demand-signatur. Verktyget registrerade identitetsfelet men kraschade sedan på `simulated[i]`. | Dela verktyget i preflight och jämförelse. Skriv preflight-felen och returnera 1 innan numerisk jämförelse när identitet eller horisont inte stämmer. Behåll dessutom explicit längdkontroll före `zip`; tyst trunkering är inte tillåten. |
| P1 – passagevarianter körs sekventiellt | **Bekräftat och fortsatt högsta produktprestandaprioritet.** Tre färska sparade körningar tog 53,49–58,88 s; längsta enskilda variant tog 20,11–22,99 s. | Fullskalebenchmark av 1/2/3 spawn-arbetare under en enda global sexslotsbudget. Kräv exakta artefakter, atomisk publicering, topp-RSS, timeout och avbrottstest. |
| ”53,5–58,9 s är hela bygget” | **Rättelsen är bekräftad.** `timings.json.total_s` avser `refine_variants`, alltså passagesteget. Ett helt efterfrågebygge har fler steg. | Benämn måttet ”kall heldags passagekalibrering”. Rapportera full byggtid separat och använd exklusiva/inkluderande timers så att nästlade steg inte summeras två gånger. |
| ”`pfe_variants_and_rounding` är separat från `dynamic_passage`” | **Delvis rätt etikett, men fel om tiderna adderas.** De skrivs som två nycklar, men på en dagbiblioteksmiss körs `dynamic_passage` inne i `calibrate_window`, som i sin tur mäts av PFE-timern. | Gör timerformatet hierarkiskt med `inclusive_s`, `exclusive_s` och parent-id. I det analyserade bygget är 112,41 s en del av 133,61 s. |
| ”9,4–10,2 s SUMO-väggtid kan inte reproduceras” | **Motsagt av artefakterna.** Oberoende union av `clockBegin`/`clockEnd` i 27 `stats.xml.gz` gav exakt 10,190000057 s respektive 9,419999838 s i sex vågor. SUMO definierar fälten som start/slut i Unix-tid och `clockDuration` som deras differens.^10 | Behåll måttet som ”aktiv simuleringsväggtid”. Lägg separat timer runt `subprocess.run` för full processväggtid inklusive start, nätparsning och avslut. |
| Icke-noll `traciDuration` visar TraCI-kostnad i passagevägen | **Oförklarad mätanomali, inte stöd för slutsatsen.** Arkiven har 21–23 icke-nollvärden och max 204–212 trots `clockDuration` omkring 1–2 s. Dokumentationen anger sekunder,^10 men `_run_sumo` använder `subprocess.run` utan `--remote-port`. | Logga kommando, SUMO-version, subprocessväggtid och rått performance-element i ett minimalt reproducerbart prov. Använd inte fältet för libsumo-beslut innan enheterna och orsaken är verifierade. |
| P2 – onödig omstyling av kartans 7 125 kanter | **Kodfynd bekräftat; Claudes exakta webbläsartid är inte ommätt i denna pass.** `redraw` går igenom alla kanter och `applyFlowStyle` anropar `setStyle`; endast animationsloopen har `_styleKey`-vakt. | Cacha en komplett presentationsnyckel som även omfattar providerläge, stängd/pending, osimulerad, confidence och jämförelsestatus. En nyckel baserad enbart på flöde kan visa fel stil. Browserbenchmark och regressionstest för providerbyte/stängning krävs. |
| P3 – `demand_meta.json` är onödigt stor | **Kärnfynd bekräftat; arkivet är föränderligt.** Livefilen var 99,30 MB; kompakt identisk JSON är 18,17 MB. Det duplicerade `build_fingerprint.contract` är 9,08 MB och är identiskt med top-level-kontraktet. En tidig kontroll fann 98 filer på cirka 421 MB; klockan 16:26 fanns 132 filer på totalt 3,7 GB medan sökningen skrev vidare. Claudes 551/11,3 GB kan därför varken styrkas eller avfärdas från en annan tidpunkt utan en bunden inventering. | Gör två ändringar: (A) kompakt JSON för nya filer, vilket behåller objekt och `build_id`; (B) schema v2 där kontraktet rekonstrueras från top-level och verifieras mot digest. Läsare ska stödja v1 och v2 under migrationen. |
| P4 – Python-loop för avrundning och upprepade nollallokeringar | **Bekräftat.** Oberoende mikromätning på 7 147 × 3 × 96 gav 0,314 s mot 0,042 s; resultaten och half-even-avrundningen var identiska. | Återanvänd en immutable nollvektor per anrop och använd `np.rint(mean).astype(np.int64).tolist()`. Förväntad vinst per heldags passagekalibrering är cirka 0,27 s, så detta är en säker mindre optimering. |
| P5 – installera `pytest-xdist` och kör `-n auto` | **Möjlig men inte direkt säker.** `xdist` saknas och en tidigare helsvit tog 852 s, men testsviten har processglobala modulvärden, sessionfixturer och tester mot livefiler. | Klassificera först pure/unit respektive serial/integration, isolera globala sökvägar per worker och prova deterministisk shard/xdist på pure-delen. Aktivera inte `-n auto` för hela sviten förrän seriell/parallell A/B ger samma utfall upprepat. |
| P6 – ersätt rollbackkopior med hårdlänkar | **Utrymmesproblemet bekräftat, lösningen avvisad som osäker idag.** Backupmängden är 377,73 MB och hårdlänk går på 0,00025 s på samma APFS-volym, men backup och original får samma inode. Kandidat- och kalibreringsverktyg kan skriva till de namngivna målfilerna under bygget. | Behåll kopiering tills alla writers bevisligen använder atomiskt replace. Utvärdera därefter APFS copy-on-write-clone med verifierad fallback, eller flytta gammal inode atomiskt och publicera helt nya filer. En vanlig hårdlänk är inte en rollbackbackup mot skrivning på plats. |
| ”Pylint är rent, alltså inga mekaniska buggar” | **Slutsatsen är fel.** Pylint hittade inget, men B1 och B3 är verkliga kontrollflödesfel och B2 är ett typkontrakt som pylint inte fångade. | Behåll lint som en grind, men komplettera med regressionsfall, statisk typkontroll på gränssnitt och körbar kontraktsvalidering. |

## Uppdaterad prioriterad lista över förbättringar

| Prioritet | Undersökt del | Hur den bör förbättras | Förväntat resultat och godkännandekrav |
|---|---|---|---|
| P0 | Kraschande publiceringsvalidator | Inför identitets-/horisontpreflight i `check_map_matches_sensors`, skriv fyndet och avbryt kontrollerat före arrayjämförelse. | **Resultat:** aktuell mismatch blir ett tydligt felmeddelande och exit 1, aldrig `IndexError`. **Krav:** regressionstest med 96 mot 288, fel signatur, saknad kant och giltigt fall. |
| P0 | Passage-evidensens mergevalidator | Reparera alla-null-fallet och gör tillåtna tillstånd explicita. | **Resultat:** trasig eller blandad evidens avvisas med avsett `ValueError`; legacy utan passagefält kan fortfarande mergeas enligt kontraktet. |
| P0 | Okänd tid i kall passagekalibrering | Lägg monotona stegklockor runt inläsning, hashning, matrisbygge, varje XML-pass, staging, strukturkontroll och serialisering. Skriv hierarkiska inkluderande och exklusiva tider per variant. | **Resultat:** de cirka 25 okända sekunderna blir hänförliga utan dubbelräkning. **Krav:** profileringspåslag under 1 % och oförändrade artefakthashar. |
| P0 | Sekventiella `q50/q10/q90` | Kör de tre hela variantpipelines i separata spawn-processer under en global budget på högst sex SUMO-processer. Publicera först när alla tre lyckats; avbryt och städa barn vid fel. | **Beräknat mål:** median högst 35 s och p95 högst 45 s per kall heldags passagekalibrering på samma maskin. **Krav:** exakt samma route-, agent-, rapport- och sensorresultat som seriell referens; mät topp-RSS och avbrott. |
| P0 | Generalisering till nya sensorer/dagar | Inför nästlad fryst validering: yttre stationer och datum används en gång för sluttest; inre folds används för val av kandidatpool och regularisering. | **Resultat:** ett trovärdigt svar på om modellen fungerar utanför aktiva sensorer. **Krav:** färska LOSO- och temporalrapporter bundna till samma käll-, nät-, kandidat- och kodhashar. |
| P0 | Sensorns fysiska mätpunkt | Utöka sensorregistret med körfält, position, riktning, aggregation och komponentdetektorer. Generera E1-detektorer per körfält och summera dem till station/riktning. Kör parallell jämförelse mot `edgeData entered`. | **Forskningshypotes:** färre kvartsskiften och bättre överföring till nya sensorer. **Krav:** verifierad fältplacering och förbättrad fryst holdout; ingen automatisk `friendlyPos`-korrigering i release. |
| P0 | Korrekthet efter stängningsfelet | Kör först en enda fryst kandidat med både kall och varm backend efter tidsursprungsfixen. Spara 96/288-värdesvektorn för stängda kanter, inte bara totalsiffran. | **Resultat:** bevis att falsk `active_closure_edge_throughput` är borta. Sparad råvektor gör framtida metrikkorrigeringar omräkningsbara på sekunder. **Krav:** varm/kall kanonisk likhet och noll faktisk trafik under aktiv stängning. |
| P1 | Dubbla inläsningar och XML-pass | Återanvänd det redan validerade `PassageSystem`; bygg strukturdata från samma minnesrepresentation; använd strömmande XML där full DOM inte behövs. Behåll en oberoende kontroll efter skrivning. | **Uppmätt delvinst:** cirka 0,30 s per variant. **Beräknat mål för hela området:** 1–4 s per dag efter profilering, med identiska byte eller definierad semantisk hash. |
| P2 | Processuppstart och nätverksparsning i 27 SUMO-körningar | Mät först full `subprocess.run`-väggtid minus `clockDuration`. Prototypa en persistent TraCI-arbetare per frö endast om gapet är materiellt; återställ rent tillstånd med `traci.simulation.loadState` och hantera framtida fordon/RNG explicit. | **Forskningshypotes:** Claudes 0,59 s per start motsvarar cirka 1,8–3,5 s väggtid över 3–6 samtidighetsvågor, ungefär 3–6 % av passagesteget. **Krav:** direkt harnessmätning, minst 5 % reproducerbar medianvinst samt identiska detectorvektorer, fordonsmängder, teleporter och exit-tider. |
| P1 | Utbudssidan: signaler och kapacitet | Ersätt de 82 gissade signalprogrammen vid kritiska platser med verkliga cykler, gröntider och samordning. Kalibrera sedan fri hastighet, köutbredning, flaskhalsarnas urladdningsflöde och resetid. | **Resultat:** mer korrekta restider och köer, särskilt vid stängningar. **Krav:** förbättring på volym, hastighet, restid och ködata; efterfrågan får inte justeras för att maskera nätfel. |
| P1 | Ruttvalets underbestämdhet | Utvärdera längdbaserad path-size/entropiregularisering och `routeSampler` som challengers på exakt samma kandidatwhitelist, OD-/ändamålsmarginaler och frysta holdout. | **Forskningshypotes:** rimligare ruttmix och lägre fel på utelämnade sensorer. **Krav:** bättre yttre holdout utan nya varningar eller sämre aktiva sensorer. |
| P1 | Meso som beslutsmodell | Behåll meso för bred screening men verifiera finalister mikroskopiskt med samma efterfrågan, stängning och frön. Mät om meso-rankningen hittar micro-toppen. | **Resultat:** fortsatt snabb sökning med bättre beslutssäkerhet för köer, körfält och korsningar. **Krav:** förregistrerad shortlist-recall och regret-gräns; ingen global vinnarclaim enbart från meso. |
| P1 | Snabb katalog- och kodvalidering | Dela verifieringen i fyra nivåer: hash/manifest, solver-replay, liten riktig SUMO-smoke och full fryst releasekampanj. Endast nivå fyra får skapa releasebevis. | **Resultat:** vanliga kodändringar kan kontrolleras på sekunder eller tiotals sekunder; full test körs när evidens ska publiceras. **Krav:** varje nivå märks tydligt och snabbtest får aldrig uppgraderas till releasebevis. |
| P1 | Stor `demand_meta.json` | Skriv först kompakt JSON. Inför därefter fingerprint-schema v2 utan duplicerat kontraktsobjekt och med verifierbar rekonstruktion från top-level. | **Uppmätt storleksmål:** cirka 99,3 → 18,2 MB med enbart kompaktering och cirka 9,1 MB efter schemaändring. **Krav:** oförändrat objekt och `build_id` i steg A; v1/v2-läsning, manifest- och driftstest i steg B. |
| P1 | Kartans `redraw` | Cacha den fullständiga presentationsnyckeln och hoppa över verkliga no-op `setStyle`, med uttrycklig invalidation vid provider- och closurebyte. | **Tidigare uppmätt mål:** återbekräfta cirka 8,9 → 3,1 ms per redraw. **Krav:** pixel-/stilsemantik för scenario, delta, pending, closed och sensorläge förblir korrekt. |
| P2 | Artefaktlagret | Lagra oföränderliga nät och stora gemensamma indata en gång per SHA-256 och referera från körmanifest. Skapa inventering och `--dry-run`-gallring; radera aldrig publicerad evidens implicit. | **Resultat:** kraftigt lägre lagring för duplicerade 15 MB-nät och färre stora kopieringar. Hastighetsvinsten är sekundär och ska mätas. |
| P2 | Flödesaggregering | Hoista nollvektorn och vektorisera half-even-avrundningen med NumPy. | **Uppmätt delresultat:** cirka 0,314 → 0,042 s för den realistiska loopen; ungefär 0,27 s möjlig vinst per heldags passagekalibrering. **Krav:** identiska listor för heltal, halvor, noll och stödda/saknade kanter. |
| P2 | Testsvitens ledtid | Isolera worker-state och dela pure/unit från serial/integration innan selektiv xdist eller CI-sharding används. | **Resultat:** snabbare utvecklarfeedback. **Krav:** upprepade seriell/parallellkörningar ger samma testmängd och utfall; inga liveartefakter skrivs. |
| P2 | Returtyper vid seedkörning | Ersätt den felannoterade åttatuplen med korrekt typ eller namngivet resultat. | **Resultat:** statisk kontroll kan upptäcka framtida gränssnittsdrift; ingen runtimevinst. |
| P2 | Syfte och reslängd | Reparera ordningen där fritidsresors median nu är 2,08 km och arbetsresors 2,24 km trots den använda RVU-prioren. Kalibrera gemensamt på reslängdsfördelning och sensorer i träningsfolds. | **Resultat:** mer sammanhängande resebeteende och bättre scenariotolkning. **Krav:** strukturvarningen försvinner utan försämrad sensorholdout. |
| P2 | Sensorernas datakvalitet | Versionssätt kalibrering, riktning, aktiv period, felkoder och osäkerhet för varje fysisk sensor. Vikta mjuka mål efter mätosäkerhet men behåll uttryckliga hårda krav där de verkligen är hårda. | **Resultat:** nya och tillfälligt felande sensorer kan läggas till utan specialkod eller tyst bias. **Krav:** full täckningsrapport, inga implicit imputerade mätvärden. |
| P3 | Transient rollbacklagring | Behåll bytekopior nu; prova copy-on-write-klon först efter ett separat atomic-writer-audit. | **Resultat:** möjligen nästan noll extra fysiska bytes för cirka 378 MB logisk backup. **Krav:** mutation av original får aldrig ändra backup; fungerande kopieringsfallback på andra filsystem. |

## Hastighet: rekommenderad teknisk väg

### 1. Profilera innan nästa optimering

`refine_variants` kör tre varianter sekventiellt. Varje variant kopierar rutter, agenter och nät, gör tre inlärningskörningar, bygger passageincidens, expanderar sju tidsförskjutningar, löser ett heltalsproblem, materialiserar XML, kör sex valideringskörningar och skapar struktur- och proveniensrapporter. Den nuvarande tidsrapporten visar SUMO, solver och retention men lämnar ungefär 25 sekunder som residual.

Profileringen bör minst mäta:

- kopiering och SHA-256 av de tre ingångsfilerna;
- de tre inlärningskörningarnas aktiva tid och summerade CPU-tid;
- parsning av `edge.xml` och `vehroute.xml` per frö;
- `load_source`, incidensbygge och stödexpansion;
- varje MILP-försök och eventuell strukturell reparationsrunda;
- kandidatmaterialisering och båda strukturkontrollerna;
- sex valideringskörningar, parsning och accuracy-bedömning;
- JSON/XML-skrivning, slutlig atomisk publicering och evidenskomprimering.

Målet är inte en ny loggrad i konsolen utan en versionssatt `timings.json` där varje post anger parent, `inclusive_s` och `exclusive_s`, och där exklusiva tider summerar till passagestegets väggtid. Det gör en regression mätbar, undviker dagens PFE/passage-dubbelräkning och hindrar att en optimering bara flyttar tid mellan poster.

### 2. Parallellisera på variantnivå med en global resursbudget

Det finns redan en isolerad kandidatpatch med en toppnivåarbetare som kan serialiseras under Python `spawn`. En liten riktig SUMO-integration gav identiska utdata och förbättrade 6,105 till 5,096 sekunder, men populationen var bara 3–5 fordon. Det räcker för att verifiera kontrollflödet, inte för aktivering.

Fullskaletestet bör använda en redan publicerad, inaktiv heldag och jämföra:

1. seriell körning med dagens kod;
2. två variantarbetare;
3. tre variantarbetare;
4. upprepad trearbetarkörning för stabilitet.

Alla körningar ska ha samma maximala antal aktiva SUMO-processer, sex. Mät väggtid, summerad CPU, topp-RSS, antal processer, solverstatus, hash på slutliga rutter/agenter, råa sensorvektorer och samtliga varningar. Simulera även fel i en barnprocess och användaravbrott. Ingen fil i `sumo/` får bytas innan alla barn lyckats.

Målet 35 sekunder är en beslutspunkt, inte ett löfte. Om topp-RSS blir för hög eller p95 inte förbättras tydligt används två arbetare eller seriell drift.

### 3. Ta bort redundans i samma variant

`load_source` bygger och validerar källsystemet, varefter `_refine` bygger samma `PassageSystem` igen. Den isolerade mätningen med 18 990 fordon visar en medianvinst på ungefär 0,30 sekunder när den redan validerade representationen återanvänds. Ändringen är liten men låg risk om alla matriser, grupper och fordonsordningar jämförs exakt.

XML-optimering bör göras efter profileringen. Full `ElementTree.parse` kan ersättas med `iterparse` för stora läsningar, men slutresultatet måste fortfarande valideras oberoende efter skrivning. Strukturkontrollen kan beräknas från det valda fordonsurvalet före materialisering och sedan bekräftas på den färdiga filen. Det minskar duplicerat arbete utan att ta bort en grind.

### 4. Testa rätt lösning på nätverksuppstart

Den installerade SUMO 1.27.1-distributionen innehåller `libsumocpp.dylib` men saknar Python-SWIG-bindningen. Att installera eller bygga `libsumo` är därför ett miljöarbete, och den officiella dokumentationen beskriver vinsten som borttagen socketkommunikation. Det är inte belägg för att nätet bara parsas en gång.^1

SUMO dokumenterar däremot att `traci.simulation.loadState` tömmer och laddar simulationstillstånd utan att ladda om nätet, och att detta kan vara mycket snabbare på stora nät.^2 Ett avgränsat passageexperiment bör därför komma före en libsumo-migrering. Begränsningarna är väsentliga: ännu ej avgångna fordon ingår inte i tillståndet, ursprunglig ruttfil behövs för flows, RNG sparas endast med särskilt val och vissa interna bilföljnings- och filbyteslägen återställs inte fullständigt.

Experimentet godkänns endast om två körningar i samma process ger samma resultat som två färska subprocesskörningar för:

- samtliga 96 kvartsvärden per sensor;
- fordons- och ruttmängd;
- avgångs- och exit-tider;
- teleporter, lastade och insatta fordon;
- slumpfrö och upprepad determinism;
- alla outputfiler och filnamn.

Eftersom sex samtidiga SUMO-processer redan överlappar uppstarten är 27 × 0,57 ≈ 15 sekunder en möjlig CPU-besparing, inte en styrkt väggtidsbesparing. Claudes separata 0,59-sekundersprov ger som mest cirka 1,8–3,5 sekunder över 3–6 vågor, men det är en rekonstruktion och inte mätt i passageharnessen. Lägg därför först en timer runt varje komplett subprocess och jämför med dess arkiverade `clockDuration`. Persistent drift bör bara införas om samma frysta indata ger minst 5 % reproducerbar medianvinst och exakt samma evidens.

### 5. Gör metrikkorrigeringar återspelbara

Det senaste stängningsfelet uppstod efter SUMO, när absoluta XML-tidsindex försköts en gång till. Den robusta långsiktiga lösningen är att varje kanonisk observation sparar den minsta råa, hashbundna signal som beslutet bygger på: kant × kvart-värden för relevanta stängda kanter, tillsammans med tidsbas, enhet och intervallkonvention.

Då kan en ren metric- eller tidsindexändring räknas om mot gamla råvektorer på sekunder. En ny SUMO-körning krävs fortfarande när nät, efterfrågan, rutter, stängningsgeometri eller SUMO-version ändras. Proveniensschemat måste skilja mellan rå simuleringsidentitet och härledd metricversion.

### 6. Minska lagring utan att förlora bevis

Vid ögonblicksbilden 16:26 var `runs/` cirka 88 GB; storleken ändras medan sökningar skapar och gallrar artefakter. Nätfilen är cirka 15 MB och kopieras in i passagebevis för varje variant. En innehållsadresserad blobstore kan lagra nätet en gång och låta manifest referera till dess SHA-256. Samma princip kan användas för andra byte-identiska indata.

Införandet bör börja med en skrivande dubbelväg där gammal layout och ny referens skapas parallellt och valideras. Gallring ska vara en separat kommandoåtgärd med inventeringsrapport, `--dry-run`, referensräkning och skydd för publicerade manifest. Ingen prestandaclaim bör göras innan filsystemstid och lagringsmängd mäts före och efter.

## Simuleringskvalitet: rekommenderad teknisk väg

### 1. Gör generalisering till huvudmåttet

SUMO framhåller att trafikräkningar inte bestämmer en unik efterfrågan: många ruttmängder kan passa samma mätvärden.^3 Kodbasens egen geometridiagnostik säger samma sak. Alla sex stationer är underidentifierade när en station lämnas ut, och varje station har många ruttgeometrier som de aktiva sensorerna inte styr.

Den äldre LOSO-rapporten har stora avvikelser, men den gäller en annan dag, annan källa och gammal kandidatpool. Den ska inte användas för att döma dagens modell. Den visar däremot varför en ny rapport behövs.

En robust utvärdering bör förregistrera:

- yttre stationer som inte får påverka ruttpool, priorer, regularisering eller hyperparametrar;
- yttre datum från andra veckodagar, årstider och trafiknivåer;
- inre folds där alternativa metoder väljs;
- primära mått: stationskvot, MAE per kvart, hourly GEH, tidsförskjutning och täckning;
- sekundära mått: ruttlängd, detour, OD-/ändamålsstabilitet, teleporter och köer;
- en enda låst slututvärdering efter metodval.

FHWA rekommenderar flera datadagar och jämförelser av flera mått och platser, inklusive volym, hastighet, restid och flaskhalsar. Antalet slumpfrön bör bestämmas från observerad variation och önskad tolerans, inte från ett fast universellt tal.^4 Dagens tre valideringsfrön kan därför fortsätta som snabb regressionsgrind, medan ett separat fryst releaseprotokoll fastställer hur många frön som behövs statistiskt.

### 2. Matcha den fysiska sensorhändelsen

Dagens passagekod använder `edgeData entered`, alltså tidpunkten när fordonet går in på sensorkanten. Det är internt konsekvent, men en fysisk sensor kan ligga långt in på kanten. Vid kvartgränser kan samma fordon därför hamna i olika intervall i verkligheten och modellen.

SUMO E1 placerar en detektor på ett bestämt körfält och en bestämd position och kan aggregera exakt 900 sekunder.^5 För en station över flera körfält skapas en E1 per körfält och resultaten summeras enligt stationens riktning. Sensorregistret bör innehålla:

- `lane_ids` och meterposition från körfältets början;
- riktning och vilka E1-komponenter som bildar stationens mätvärde;
- mätprincip, fordonsklasser och aggregation;
- koordinatkälla, positionsosäkerhet och senaste fysiska kalibrering;
- giltighetsperiod och nätversion.

En dubbelmätning på samma frysta dag ska rapportera hur många fordon som byter kvart mellan edge-entry och E1, per sensor och tid på dygnet. E1 får ersätta produktionsmåttet först om fältplaceringen är verifierad och spatial/temporal holdout förbättras.

### 3. Kalibrera nätets utbudssida separat

Nätbygget använder `--tls.guess true` eftersom signaldata förlorades i GraphML-flödet. Körningen använder meso med begränsad korsningskontroll eftersom full kontroll med de gissade signalplanerna tidigare ströp flödet. Det är ett rationellt skydd, men innebär att simulerade köer och restider vid signaler inte kan betraktas som fullständigt fältkalibrerade.

Insamla i denna ordning:

1. verkliga signalcykler, fasordning, gröntider och tidsprogram vid kritiska korsningar;
2. fälthastighet eller restid på korridorer;
3. kölängd och flaskhalsens urladdningsflöde;
4. incident-, väder- och vägklass för representativa dagar.

SUMO E2 kan rapportera hastighet, occupancy, tidsförlust, antal stannade fordon och kölängd.^6 Dessa signaler skiljer ett efterfrågefel från ett kapacitets- eller signalplansfel. FHWA:s kalibreringsvägledning betonar samma multipla mått.^4

### 4. Regularisera ruttvalet, inte bara sensorfelet

Den aktiva optimeringen minimerar avvikelse från föregående val och tidsförskjutningskostnad samtidigt som hårda marginaler bevaras. Det är en bra lokal stabilisator men garanterar inte att två lika passande lösningar ger naturliga rutter på osedda platser.

Den befintliga längdbaserade path-size-diagnostiken fann 416 unika ruttgeometrier och skulle ändra kostnaden för 92 av dem. Det motiverar ett challenger-experiment, inte omedelbar aktivering. Jämför minst:

- dagens policy;
- dagens policy plus längdbaserad path-size/entropiregularisering;
- SUMO `routeSampler` med samma whitelist och tillgängliga edge-/OD-räkningar;
- eventuellt Cadyts som forskningsbaseline, med mätosäkerhet explicit.

SUMO beskriver `routeSampler` som ett sätt att välja ur en whitelist av plausibla rutter och varnar för att en olämplig kandidatpool är en vanlig orsak till mismatch.^3 Cadyts kan använda standardavvikelse per mätning, men är iterativt och kan ändra ruttval och skala efterfrågan; därför ska det vara en jämförelsemetod och inte få skriva produktionsdemand utan samma OD-, populations- och holdoutkrav.^7

### 5. Använd två modellnivåer för stängningsbeslut

SUMO:s mesomodell kan vara upp till 100 gånger snabbare men har grövre korsnings- och körfältsbeteende.^8 Det passar bred screening. Stängningar kan samtidigt skapa just de köer, körfältsbyten och signalinteraktioner där mikrosimulering ger ett annat resultat.

En robust beslutsstege är:

1. meso på alla kandidater med nuvarande exakta rutt- och accessgrindar;
2. micro på en förregistrerad shortlist och några negativa kontrollkandidater;
3. jämför ranking, regret och om micro-vinnaren fanns i meso-shortlisten;
4. utöka shortlisten automatiskt endast om recall-grinden misslyckas.

Detta gör inte micro till en ny dyr heldagskalibrering. Den används där resultatets beslutsvärde är störst.

### 6. Reparera beteendestrukturen

Aktuell validering varnar för att fritidsresors medianlängd är 2,08 km medan arbetsresors är 2,24 km, i konflikt med den RVU-ordning som projektet använder. Lösningen är inte ett efterhandsfilter. Ändamål, OD, ruttlängd och sensorer bör kalibreras gemensamt inom träningsfolds, och strukturmåttet ska sedan bedömas på både aktiv och yttre data.

Det förväntade resultatet är en demand som förklarar både räknad trafik och rimligt resebeteende. Om sensorfit och struktur står i konflikt ska rapporten visa konflikten i stället för att tyst flytta fordon.

## Snabb men ärlig valideringsstege

| Nivå | Innehåll | Mål för tid | Vad den får bevisa |
|---|---|---:|---|
| A – statisk | Schema, SHA-256, manifest, XML-referenser, dimensions- och populationskontroller | sekunder | Att artefakten är komplett och internt konsistent. |
| B – matematisk replay | Läs sparad numerisk solverrequest, lös om eller kontrollera lösningen, jämför alla hårda ekvationer och marginaler | sekunder till tiotals sekunder | Att optimeringen fortfarande uppfyller samma matematiska kontrakt. |
| C – SUMO-smoke | Liten riktig nät-/sensorfixture plus en fryst full q50-variant när relevant | tiotals sekunder | Att integration, tidsindex och verklig SUMO-parser fungerar. |
| D – release | Tre varianter, statistiskt motiverade frön, spatial och temporal holdout samt closure/micro-finalisttest | minuter eller längre | Enda nivån som får stödja release-, global-vinnar- eller generaliseringsclaim. |

För katalogändringar ska A och B användas vid varje lokal iteration. C körs när SUMO-adapter, tidssemantik, ruttmaterialisering eller nätkoppling ändras. D körs först när en kandidat ska kvalificeras eller publiceras. Detta ger snabb återkoppling utan att späda ut evidenskraven.

## Saker som inte bör göras

- Minska valideringsfrön eller hälsokontroller för att nå en tidsbudget innan variansen mätts.
- Återanvända en dag enbart via kalenderdatum. Samma datum kan ha annan pool, population och ruttmängd.
- Öka solvertrådar i barnprocesser utan resursmätning; HiGHS har redan isolerats till en tråd för stabilitet.
- Behandla en annan optimal heltalslösning som identisk när användarens fordonsartefakter ändras.
- Byta till `libsumo` och anta att nätparsningen försvinner. Dokumenterad huvudvinst är socketfri TraCI-koppling.^1
- Fortsätta kompressionsoptimering som huvudspår; retention är cirka två sekunder per dag.
- Kalibrera efterfrågan hårdare för att kompensera för gissade signalprogram eller fel kapacitet.
- Aktivera path-size, routeSampler eller Cadyts därför att träningssensorerna blir bättre. Yttre holdout är beslutskriteriet.
- Tolka den korrigerade stängningskoden som att den historiska `no_viable`-kampanjen nu innehåller giltiga vinnare. Den måste först köras om efter en avgränsad kandidatkontroll.

## Föreslagen genomförandeordning

### Etapp 1 – mätbarhet och korrekt stängningsgrund

1. Lägg till full stegprofilering.
2. Lägg till rå, hashbunden kvartvektor för stängda kanter i kanonisk observation.
3. Kör en avgränsad varm/kall stängningskandidat efter tidsursprungsfixen.
4. Lägg till tidsursprungs- och metric-replay i snabbnivå A/B.

**Klart när:** varje sekund i passagesteget har en exklusiv post, hela byggkedjan har en separat topptimer, stängningsvektorn kan räknas om utan SUMO och varm/kall kandidat ger samma kanoniska beslut.

### Etapp 2 – största säkra hastighetsvinsten

1. Aktivera den befintliga kandidatpatchen endast i en isolerad fullskalekörning.
2. Mät 1/2/3 variantarbetare med samma globala sexslotsbudget.
3. Verifiera minne, avbrott, timeout och atomisk publicering.
4. Integrera validerad `PassageSystem`-återanvändning och därefter profilerade XML-förbättringar.

**Klart när:** utdata är identiska och den valda konfigurationen förbättrar median och p95 utan hög resursrisk. Målet är högst 35 sekunders median för en kall heldags passagekalibrering med tre varianter.

### Etapp 3 – generaliserbar sensor- och ruttmodell

1. Frys yttre stationer och datum.
2. Kartlägg E1-positioner för minst två stationer med säkra fältkoordinater.
3. Kör edge-entry och E1 sida vid sida.
4. Jämför dagens ruttpolicy, path-size och routeSampler i nästlad holdout.

**Klart när:** en metod vinner på förregistrerad yttre holdout, inte bara på träningssensorer. Om ingen vinner behålls dagens policy.

### Etapp 4 – utbudskalibrering och beslutskvalitet

1. Importera verkliga signalprogram för de mest kritiska korsningarna.
2. Lägg till E2/hastighet/restid/kö som separata mått.
3. Kalibrera meso per trafikregim.
4. Lägg till micro-verifiering av stängningsfinalister och mät shortlist-recall.

**Klart när:** volym, hastighet, restid och köer ligger inom förregistrerade toleranser och meso-shortlisten fångar micro-bästa kandidater med godkänd recall.

### Etapp 5 – persistent SUMO och lagring

1. Kör det avgränsade `simulation.loadState`-experimentet.
2. Fortsätt endast om direkt subprocessinstrumentering visar ett materiellt gap och experimentet sparar minst 5 % av passagefasen med exakt ekvivalens.
3. Inför innehållsadresserade gemensamma indata med dubbel skrivning och dry-run-inventering.

**Klart när:** prestandavinsten är reproducerbar och gamla evidensmanifest kan verifieras oförändrat.

## Beslutspunkter och förväntade slutresultat

Efter etapp 1–2 bör passagekalibreringen för en ny kall heldag kunna köras med samma vetenskapliga kontrakt men väsentligt snabbare. Det konkreta målet är 35 sekunders median för detta steg; hela efterfrågebygget ska redovisas separat. Om parallellisering endast flyttar kostnad till minne eller skapar hög p95 används färre variantarbetare.

Efter etapp 3 ska projektet kunna säga hur väl modellen matchar en sensor som inte användes under kalibrering och en dag som inte användes vid metodval. Det är den centrala definitionen av robusthet för fler sensorer.

Efter etapp 4 ska stängningsresultat bygga på kalibrerade flöden, hastigheter, köer och kritiska signaler. Meso behåller sin screeninghastighet, medan micro används som kontroll av finalister där det grova modellvalet har störst risk.

Efter etapp 5 ska återkommande SUMO-uppstart och artefaktkopiering vara mätta och reducerade där det faktiskt ger effekt. Om persistent tillstånd eller libsumo inte passerar ekvivalens- och hastighetsgrinden lämnas produktionsvägen oförändrad.

## Källor

1. Eclipse SUMO/DLR. [Libsumo](https://sumo.dlr.de/docs/Libsumo.html). Beskriver socketfri TraCI-koppling, Pythonbindning och multiprocessingbegränsning.
2. Eclipse SUMO/DLR. [SaveAndLoad](https://sumo.dlr.de/docs/Simulation/SaveAndLoad.html). Beskriver att `simulation.loadState` undviker nätverksomladdning samt RNG- och fordonsbegränsningar.
3. Eclipse SUMO/DLR. [Routes from Observation Points](https://sumo.dlr.de/docs/Demand/Routes_from_Observation_Points.html) och [Turns / routeSampler](https://sumo.dlr.de/docs/Tools/Turns.html). Beskriver underbestämdheten och betydelsen av en plausibel ruttwhitelist.
4. U.S. Federal Highway Administration. [Guidance on the Level of Effort Required to Conduct Traffic Analysis Using Microsimulation – Chapter 6: Model Calibration](https://www.fhwa.dot.gov/publications/research/operations/13026/007.cfm), 2014. Flera dagar, flera mått, flera platser och statistiskt motiverat antal frön.
5. Eclipse SUMO/DLR. [Induction Loops Detectors (E1)](https://sumo.dlr.de/userdoc/Simulation/Output/Induction_Loops_Detectors_%28E1%29.html). Körfält, position, aggregation, counts, occupancy och speed.
6. Eclipse SUMO/DLR. [Lanearea Detectors (E2)](https://sumo.dlr.de/docs/Simulation/Output/Lanearea_Detectors_%28E2%29.html). Hastighet, occupancy, tidsförlust, halter och kölängd.
7. Eclipse SUMO/DLR. [Cadyts](https://sumo.dlr.de/docs/Contributed/Cadyts.html). Iterativ kalibrering mot mätvärden och mätosäkerhet.
8. Eclipse SUMO/DLR. [Meso](https://sumo.dlr.de/docs/Simulation/Meso.html). Prestanda, grövre korsnings-/körfältsmodell och signalbegränsningar.
9. Eclipse SUMO/DLR. [SUMO FAQ – simulation performance](https://sumo.dlr.de/docs/FAQ.html). Steglängd, köer, insertion checks, parallell routing och meso.
10. Eclipse SUMO/DLR. [Statistic Output](https://sumo.dlr.de/userdoc/Simulation/Output/StatisticOutput.html). Definierar `clockBegin`, `clockEnd`, `clockDuration` och `traciDuration`.
