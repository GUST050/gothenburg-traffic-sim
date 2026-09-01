# Kompakt granskad reparationsfortsättning

Återanvänd den hashbundna READY-planen från ai-flow-run
`20260901-105635-73537`. Den planen är fortsatt auktoritativ för Fas 0–7 och
ska köras vidare efter CODE_APPROVED. Gör ingen ny planering och återöppna inte
Fas 0–2.

Den reserverade verifieringsreviewn i samma run stoppade korrekt före evidens.
Läs exakt:

- `.ai-flow/runs/20260901-105635-73537/code-review-02.json`
- `.ai-flow/runs/20260901-105635-73537/code-review-fix-01.json`
- `.ai-flow/runs/20260901-105635-73537/checks-02.json`

Reparera reviewns två återstående findings i en sammanhängande kodbatch:

1. Verifiera den nu implementerade fullständiga valideringen av canonical
   `plan.json` och staged-kompatibel source state genom hela kedjan av
   `--reuse-plan-from`. Avvisa saknade fält, fel typer, extra fält, saknad eller
   malformed state samt source/target/ancestor drift.
2. Ersätt de sista fabricerade kontraktsfälten i Phase 3- och Phase 6-testerna
   med kontrollerade verkliga registration-, restart/cancellation-,
   monthly-search- och terminalproducenter. Mata de exakta publicerade byten
   genom Gate S och `validate_post_review_terminal_artifacts` för bounded
   PASS/performance miss samt Phase 6 PASS, giltiga INCONCLUSIVE-varianter och
   NOT_ALLOWED.

Verifiera även att `--reuse-plan-from` avvisar drift eller ogiltig READY-
proveniens, och att den nya uppgiften fortfarande skickas till worker och alla
reviewer. Preservera alla orelaterade dirty changes. Skapa eller ändra ingen
evidens före CODE_APPROVED. Ändra inga vetenskapliga trösklar, timeouts,
routing-, teleport-, health-, provenance- eller releasegrindar. Radera inget
och commit/pusha/deploya inte.

Efter kodgodkännande fortsätter controllern den återanvända huvudplanen genom
fresh Fas 3–5-evidens, checkpoint-review, villkorad Fas 6, Fas 7 och den
fullständiga terminalrapporten.
