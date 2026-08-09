# Sensorvalidering för presentation

Utvärderingsfönster: 2025-09-17 → 2025-09-18 (exclusive). Resultaten är temporal leave-one-station-out: sensorns egna värden används endast för jämförelse, inte för att passa den folden.

## Tydligt huvudtest: total trafikvolym, alla sensorer

**Resultat: PASS**

Alla 6 sensorer ingår: 107, 1074, 1076, 133, 134, 2276. Ingen sensor har tagits bort ur testet.

Mätt totalvolym är 25761 fordon och simulerad totalvolym är 24761. Skillnaden är 3.9 procent mot projektets testgräns högst 10 procent.

Detta är ett enkelt projekttest, inte en separat TAG-standard. Det svarar på om simuleringen får rätt samlad trafikmängd i sensornätet. Över- och underskattningar kan ta ut varandra, så sensortabellen och det strikta TAG-testet redovisas fortfarande separat nedan.

| Sensor | Timmar | Mätt dygn | Simulerat dygn | Ratio | Dygnsavvikelse | GEH < 5 | Medel-GEH | Max-GEH | TAG flödesskillnad | Kombinerat projekttest | Rank gain | Tolkning |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 107 | 24/24 | 6770 | 8532 | 1.260 | 26.0 % | 66.7 % | 3.88 | 8.47 | 66.7 % | 70.8 % | 1 | överskattar; underidentifierad |
| 1074 | 24/24 | 2926 | 2066 | 0.706 | -29.4 % | 62.5 % | 3.21 | 7.87 | 95.8 % | 95.8 % | 1 | underskattar; underidentifierad |
| 1076 | 24/24 | 4799 | 2701 | 0.563 | -43.7 % | 29.2 % | 6.53 | 13.03 | 58.3 % | 58.3 % | 1 | underskattar; underidentifierad |
| 133 | 23/24 | 3643 | 2654 | 0.729 | -27.1 % | 60.9 % | 4.35 | 10.10 | 78.3 % | 78.3 % | 1 | underskattar; underidentifierad |
| 134 | 24/24 | 3053 | 3099 | 1.015 | 1.5 % | 95.8 % | 1.89 | 6.02 | 100.0 % | 100.0 % | 1 | bra holdout-fit, men underidentifierad |
| 2276 | 24/24 | 4570 | 5709 | 1.249 | 24.9 % | 75.0 % | 3.68 | 6.05 | 91.7 % | 91.7 % | 1 | överskattar; underidentifierad |

## Samtliga relevanta testresultat

| Test | Resultat | Uppmätt utfall | Vad testet visar |
| --- | --- | --- | --- |
| Totalvolym, alla sensorer | **PASS** | 3.9 % avvikelse; ratio 0.961 | Samlad trafikmängd i hela sensornätet; 1076 ingår |
| Kalibrering mot sensorintervall | **PASS** | 100.0 % GEH < 5; 0 olösliga intervall | Efterfrågan kan reproduceras mot givna sensormål |
| Rå SUMO-sensorutdata | **PASS** | 100.0 % GEH < 5; MAE 3.17 fordon/timme; maxfel 14.86 | Att trafiken verkligen passerar sensorerna i SUMO, inte bara i efterfrågefilen |
| SUMO-körhälsa | **PASS** | 62 430 insatta; 0 oavslutade; 0 teleporteringar | Teknisk stabilitet över 3 varianter |
| Resestruktur | **PASS** | 3.8 % destinationer inom 200 m från sensor mot 1.9 % slumpbaslinje; median fortsatt resa 2759.5 m | Resor avslutas inte artificiellt vid sensorerna |
| Ändamål och proveniens | **PASS** | 20 836 resor; 0 inkompatibla kvart; ordningsbrott: false | Resändamål och rutternas ursprung är bevarade |
| Flerdagstest | **EJ TILLÄMPLIGT** | 1 sammanhängande dag | Kräver fler sammanhängande mätdagar |
| Samma-dag LOSO | **INFO** | Sensorratio 0.685–2.613 | Karakteriserar återskapande när en sensor tas bort; ingen grind |
| Temporalt holdout, TAG | **FAIL** | Flöde 81.8 %; GEH 65.0 %; båda bedöms mot >85 % | Oberoende test på en annan historisk dag |
| Observabilitet | **INFO** | 6/6 sensorer har rank gain 1 | Visar hur mycket ny information varje utelämnad sensor tillför; det är inte ett kvalitetsbetyg |
| Avstängningsstresstest | **PASS** | 0 teleporteringar; 0 kollisioner; 0 inträden på stängda kanter | Robust omledning och korrekt avstängningsintegritet |

## Så läses måtten

- Ratio 1,000 betyder att simulerad och mätt dygnstotal är lika. Exempelvis 1,260 är 26,0 procent för högt och 0,706 är 29,4 procent för lågt. Ratio visar volymbias men inte timprofilen.

- GEH beräknas per timme. 0 är exakt och lägre är bättre. TAG M3.1 anger GEH < 5 för mer än 85 procent av individuella flöden som riktvärde. Kolumnen visar andelen timmar som klarar gränsen; medel och max är diagnostik.

- TAG flödesskillnad är ett separat test: högst 100 fordon/timme under 700, högst 15 procent mellan 700 och 2 700, och högst 400 över 2 700. TAG bedömer detta och GEH-andelen separat. Kolumnen 'Kombinerat projekttest' visar deras union som extra diagnostik.

- Rank gain är inte ett betyg mellan 0 och 1. Rank gain 0 betyder att den hållna sensorns total kan härledas ur kvarvarande mätmarginaler. Rank gain 1 betyder en ny oberoende dimension: flera ruttfördelningar kan passa övriga sensorer men ge olika värde här. Samtliga sex sensorer är därför strukturellt underidentifierade.

## Sammanfattning

- Temporal holdout GEH < 5: 93/143 timmar (65.0 procent).

- TAG flödesskillnad: 81.8 procent.

- Kombinerat projektdiagnostikmått (flödesskillnad eller GEH): 82.5 procent (inte ett separat formellt TAG-kriterium).

- Formellt TAG-anpassat verdict kräver att både flödesskillnad och GEH-andel överstiger 85 procent: **FAIL**.

## Kompletterande kontroller av den aktuella simuleringen

- Kalibreringsdagens råa SUMO edgeData: 100.0 procent GEH < 5, medelabsolutfel 3.169552 fordon/timme och maximalt absolut fel 14.855333. Detta visar reproduktion av kalibreringsdagen, inte oberoende generaliseringsförmåga.

- SUMO-hälsa över 3 demand/seed-varianter: 62 430 insatta fordon totalt  0 oavslutade och 0 teleporteringar.

- Strukturell plausibilitet: 3.8 procent av destinationerna slutar inom 200 meter från en sensor, jämfört med 1.9 procent för slumpmässiga nätverkskanter; median fortsatt resa efter sista sensor är 2759.5 meter. Det motverkar en modell där resor artificiellt börjar eller slutar vid mätpunkterna.

- Vägavstängningens stresstest: integritet `verified_clean`  62 430 insatta fordon  0 teleporteringar  0 kollisioner och 0 fordon som läckte in på stängda kanter under aktiv avstängning.

Presentationstext: *Totalvolymtestet med samtliga sex sensorer klaras: simuleringen ligger 3.9 procent från mätt totaltrafik, under projektgränsen 10 procent. 1076 ingår fullt ut. Det strikta timvisa TAG-testet redovisas separat: 81,8 procent klarar flödesskillnaden och 65,0 procent GEH < 5, mot riktvärdet över 85 procent för respektive mått. Testet visar därför god samlad volym men inte likvärdig precision vid varje enskild sensor.*
