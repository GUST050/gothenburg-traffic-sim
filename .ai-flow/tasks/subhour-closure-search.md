# Automatiskt AI-uppdrag: exakt cost-ordered månadssökning under en timme

Implementera och verifiera nästa säkra del av
`docs/plans/Q10_Q90_AND_SUB_HOUR_MONTHLY_SEARCH_PLAN_2026-08-30.md` autonomt
genom planner → implementation → checks → oberoende review → begränsade
reparationer.

## Obligatoriskt utfall för denna körning

Färdigställ Fas 0–4 i planen:

1. Frys en utfallsblind preregistrering för samma-code-path-jämförelsen.
2. Gör en olöst timeout terminal som `INCONCLUSIVE_TIMEOUT`; inga senare
   kandidater eller exhaustiv fallback får starta.
3. Kör worker/fixer genom samma cost-ordered exekveringskärna som
   ordered-exhaustive. Endast `disable_early_stop` får skilja armarna.
4. Reparera de v5-avvikelser som fortfarande reproducerar i kostnadsfält,
   hard failures, health, selected IDs och restart. Lös aldrig upp en gate.
5. Lägg till och kör de syntetiska tie-, backfill-, no-detour-, timeout-,
   cancel/resume-, korrupt-evidens- och no-viable-fallen i planen.
6. Skapa utfallsblint den bounded real-SUMO-registrering som planen kräver och
   kör den endast när dess inputs, outputs och kostnadsgräns är bundna.
7. Profilera därefter hela den SUMO-fria 1 950 × 3-ledgern cold och redovisa
   fasvis väggtid, cache hits, RSS och disk.
8. Implementera `WindowCostIndex` endast om den uppmätta ledgerfasen tar mer
   än 10 minuter eller mer än 20 procent av totalbudgeten. Om det behövs krävs
   fältidentisk 1 950 × 3-oracle innan adoption.

## Oföränderliga kontrakt

- Exakt 30 datum, 65 fönster och fem sammanhängande dagar med samma fönster.
- Oförändrat `closure_cost_v1`, robust q10/q50/q90-aggregation, tie-band,
  no-detour, matched baseline, seeds, routing, closure integrity, health,
  recovery och provenance.
- Original origin → destination på snabbaste lagliga väg utan stängda edges.
- Skippade kandidater är `not_run_decision_irrelevant`, aldrig friska.
- Varje `READY` kräver ett maskinverifierbart stop proof.
- Timeout, capacity eller tidsbudget utan stop proof ger endast ett explicit
  `INCONCLUSIVE_*`, aldrig en vinnare.

## Grindar

- Samma status, selected IDs och slutbeslut som ordered-exhaustive i varje
  beslutsbart preregistrerat fall.
- Fältidentisk deterministisk ledger för hela kandidatpopulationen.
- Identiska failures/health för den faktiskt verifierade prefixen.
- Restart/cancel-ekvivalens och fail-closed korruptionskontroller.
- Minst 30 procent färre exakta kandidatverifieringar och minst 30 procent
  lägre aktiv tid i den bounded paired-sviten.
- `git diff --check` och fokuserade ai-flow/cost-ordered/monthly tester gröna.

## Säkerhets- och scopegräns

Starta inte en full månadskampanj i detta uppdrag. Starta inte Gate S för att
ta bort q10/q90. Ändra inte vetenskapliga trösklar, timeouts, routingpolicy,
teleportpolicy, evidensgrindar eller frozen artifacts för att få grönt.
Preservera alla orelaterade dirty changes. Radera inget, och commit/pusha/
deploya inte.

Om Fas 0–4 blir godkända ska slutrapporten säga exakt vilka grindar som passerat,
vilka bounded SUMO-fall som körts, cold ledger-tiden och om Fas 5 behövs. Den
ska lämna fullmånadskörningen som nästa uttryckliga operatörssteg.
