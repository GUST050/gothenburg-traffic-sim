# Konsoliderad dataförfrågan — trafiksimulering Göteborgs innerstad

> **STATUS: SKICKAS INTE (beslut 2026-07-20).** Projektet levereras
> permanent på de levererade 2025 sex-sensorräkningarna; ingen ytterligare
> extern data begärs. Detta dokument behålls ENBART som en anteckning om
> vad som skulle ha stärkt vilket påstående — det är inte en åtgärdspunkt.
> Följdriktiga permanenta, ärliga begränsningar (syntetiska signaler,
> genomfartsandel som prior, ärendeklass ≠ verifierad avsikt) står i
> IMPROVEMENT_PLAN.md, avsnittet "External Data Requests".

*Ursprungligt utkast 2026-07-20 (aldrig skickat). Projektägare: Gustav
Trogen. Engelsk sammanfattning sist i dokumentet.*

Projektet simulerar trafiken i Göteborgs innerstad (kalibrerad mot stadens
sex 15-minuterssensorer, levererade juni 2026) och kan i dag rekommendera
minst störande tidpunkter för vägavstängningar, med varje kandidat
verifierad i simulering. Fyra dataunderlag skulle lyfta systemet från
"kalibrerad nära sensorerna, försiktigt märkt överallt annars" till
försvarbara stadskonfigurerade resultat. Vi ber om dem som ETT paket så att
handläggningen kan samordnas — varje punkt anger exakt vad den låser upp.

---

## 1. Signalplaner för EN utvald korridor (högst prioriterad)

Vi ber **inte** om stadens alla signalplaner — endast en utvald korridor,
som acceptanstest innan någon skalning ens övervägs.

**Föreslagen korridor:** Skånegatan (Scandinavium-området), där projektet
redan har tre kalibrerade mätpunkter (107 Skånegatan, 1074 Valhallagatan,
1076 Skånegatan S) och därmed bäst kalibrerad efterfrågan. Alternativ:
Läraregatan/Gibraltargatan-området (mätpunkter 133, 134, 2276).

För en normal vardag och (om möjligt) en planerad avstängningsperiod:

- styrapparat- och korsnings-ID:n;
- koppling signalgrupp → körfält/rörelse (eller ritningar);
- aktiva tidplaner och tidplansval över dygnet;
- fasföljd; konflikt- och spärrtidsmatriser;
- min-/maxgrönt, gult, allrött, rödgult;
- gång- och cykelkrav (min-tider, anmälan);
- detektor- och kollektivtrafikprioriteringslogik;
- offset/samordning mellan korsningarna;
- regler för tillfälliga arbetsplaner vid vägarbete;
- 5- eller 15-minuters ankomst-/svängräkningar vid tillfarterna;
- matchande länk-/sträckrestider om sådana finns.

Detta motsvarar de handlingar som stadens tekniska handbok (12BH
Trafiksignaler) redan kräver vid signalprojektering, så materialet bör
finnas samlat per anläggning.

**Låser upp:** signaloptimeringsresultat märkta `city-configured` i stället
för dagens ärligt märkta `synthetic` (syntetiska ljusprogram). Utan detta
förblir varje signalresultat ett metodexperiment.

## 2. Kordonräkning för innerstaden (en dag)

En samtidig räkning (även en enda dag) vid innerstadens infarter — broarna
och de stora tillfarterna. Befintliga slangmätningar eller kameradata
duger; vi behöver inga nya installationer om data redan finns.

**Låser upp:** andelen genomfartstrafik, som i dag är ett dokumenterat
antagande med känslighetsanalys — den enda mätning som kan identifiera
E-E/E-I/I-I-kompositionen och därmed stärka OD-matrisen och syftesandelarna
i kalibreringen. Detta är också den återstående spärren för
`purpose_claims_allowed` i valideringen.

## 3. RVU Västra Götaland — mikrodata eller regional OD-matris

Resvaneundersökningens mikrodata för Västra Götaland (via SCB:s
forskningsprövning, alternativt en aggregerad regional OD-matris om sådan
finns hos staden/regionen).

**Låser upp:** lokala syfte×reslängd-fördelningar i stället för dagens
krympta riksgenomsnitt, och en grundsanning att validera
syfteskompatibiliteten mot.

## 4. Tidsstämplade länkrestider eller hastigheter

Historiska restids-/hastighetsobservationer på länknivå (restidskamerorna
som staden/Trafikverket driver gemensamt), för normalperioder och om
möjligt kända störningsperioder.

**Låser upp:** lokal kalibrering av väghastigheter, restidsvalidering och
evidens för kö- och cirkulationsplatspåståenden — de påståenden
simuleringen i dag medvetet avstår från.

---

## Vad staden får tillbaka

- En verifierad, ärligt konfidensmärkt simulering av innerstadens trafik
  som redan i dag kan rangordna avstängningstider med parade
  SUMO-körningar och redovisade osäkerhetsintervall.
- För varje ny mätpunkt staden lägger till: en före/efter-rapport om hur
  mycket noggrannheten förbättras och var (holdout-validering ingår i
  systemet).
- För signalkorridoren: en jämförelse syntetisk vs stadskonfigurerad plan
  på samma scenarier — ett konkret underlag för om metoden är värd att
  skala.

## English summary

We request four data items as one package, each tied to what it unlocks:
(1) the complete signal-controller documentation for ONE corridor
(proposed: Skånegatan, where our calibration is strongest) — unlocks
city-configured signal recommendations, currently honestly labelled
synthetic; (2) a one-day cordon count at the inner-city gates — the only
measurement identifying the through-traffic share, currently a documented
assumption; (3) RVU Västra Götaland microdata or a regional OD matrix —
upgrades purpose×length priors from shrunk national ratios to local
estimates; (4) time-stamped link travel times/speeds — unlocks speed
calibration and queue-claim validation. Items follow the city's own
technical-handbook document set (12BH) so the corridor package should
exist per installation. The project returns honest, confidence-labelled
simulation results and a before/after contribution report for every data
item received.
