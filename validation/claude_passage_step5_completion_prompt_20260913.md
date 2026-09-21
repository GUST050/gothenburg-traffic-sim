# Prompt till Claude: reparera och slutför steg 5

Fortsätt på `claude/exciting-rubin-1e6k5m` från `294c75a`. Arbeta i den
isolerade worktree:n och bevara huvudcheckoutens orelaterade ändringar.

Steg 4 är klart. Steg 5 är **inte** klart: arkivinstrumenteringen finns, men
två cachepåståenden är fel och metric groups 5-7 saknas. Börja inte steg 6 och
implementera ingen prestandaoptimering i denna omgång.

Starta ingen SUMO-körning, månadssökning, demand-byggnad,
kataloggenerering eller uppvärmning. Noll av 142 arkiv matchar fortfarande den
aktuella demand-källidentiteten. Produktionsstatusen ska därför fortsätta vara
`instrumented_unmeasured` tills ett current-source qualified archive finns.

## Del A — applicera cache- och evidensreparationen

Applicera `validation/claude_step5_review_fix_20260913.patch` om filen finns.
Om den saknas, implementera exakt följande med RED/GREEN:

1. I `tests/test_step5_archive_cost_measurement.py`, återställ filtid med
   `os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))` och jämför
   `st_mtime_ns`. Flyttalsformen återställde inte samma nanosekunder och testade
   därför aldrig cacheträffen som rapporten påstod.
2. Visa RED för båda verkliga felen:
   - ändrade bytes i `calibrated.rou.xml`, med samma storlek och exakt samma
     `st_mtime_ns`, serveras från `_VALIDATED_ARCHIVE_CACHE`;
   - en ändrad `demand_build_key` i `demand_meta.json`, med samma storlek och
     exakt samma `st_mtime_ns`, förblir under gamla nyckeln i
     `_ARCHIVE_METADATA_INDEX`.
3. Ta bort båda processglobala stat-auktoriserade cacherna. De är de former
   `IMPROVEMENT_PLAN.md` uttryckligen förbjuder: mtime och storlek är inte ett
   innehållsbevis.
4. `_archives_for_build_key` ska läsa metadata vid varje ny diskentré.
   `_resolve_new_release` får skapa ett index en gång inom sitt låsta
   operationstillfälle, skicka det privat till `find_demand_archives` för flera
   build-keys och bygga om det efter varje `demand_builder`-anrop.
5. `find_demand_archives` ska fullvalidera varje kandidat även när det får det
   operationslokala indexet. Återanvänd aldrig ett valideringsresultat över en
   ny läsgräns.
6. Pinna att två separata publika discovery-anrop validerar samma arkiv två
   gånger, medan ett delat operationslokalt index endast undviker ny
   metadataindexering och fortfarande fullvaliderar två gånger.

Korrigera
`validation/passage_step5_archive_cost_measurement_20260913.json`,
`IMPROVEMENT_PLAN.md`, `TASKS.md` och `AGENT_NOTES.md`:

- den gamla varma raden `0 validations / 0 reads / 0 digests` är ogiltig,
- på tre hermetiska arkiv ger reparationen:
  - kall ny diskentré: 3 valideringar, 12 JSON-läsningar, 30 digests,
  - tre nya diskentréer: 9 valideringar, 36 JSON-läsningar, 90 digests,
  - tre uppslag med ett operationslokalt index: 9 valideringar,
    27 JSON-läsningar, 90 digests,
- detta är fixturräkningar, inga produktionstider,
- `294c75a` får inte beskrivas som ett komplett steg 5,
- ingen optimering är accepterad.

Codex RED: 2 tester föll av exakt de två felen. Codex GREEN: 59 tester för
`test_step5_archive_cost_measurement.py` + `test_monthly_demand.py`, och 537
passerade i det bredare steg 5-setet. Kör om relevanta tester och
`git diff --check`. Committa och pusha Del A separat och rapportera SHA.

Ändringen ligger i `monthly_demand.py`, som binds av
`tools/profile_monthly_cost_ledger.py` producentmanifest. Äldre profiler får
inte tillskrivas de nya bytesen. Den ändrar däremot inte demandbyggarens
`demand_source_paths`; starta ingen re-warm.

## Del B — slutför steg 5:s saknade mätgrupper

Fortsätt med mätinstrumentering och tester. Ingen optimering ska implementeras.
Återanvänd `traffic_sim.ops.io_phases` och
`tools/profile_monthly_cost_ledger.py`; skapa inte ett parallellt
profileringssystem.

### Metric group 5: `day_library.assemble_window`

Mät och rapportera utan att ändra dess output:

- antal dagar i ordning,
- antal route-rader och agents per dag och totalt,
- bytes lästa och skrivna för route- och agentfiler,
- exklusiv väggtid för route-läsning, radtransformering, agent-JSON,
  route-publicering och agent-publicering,
- slutliga vehicles/days samt SHA-256 för route- och agentoutput.

Testa råa och gzipade dagfiler, flera dagar, ID-sekvens,
`day_index * 86400`-förskjutning, agentkoppling, mismatch-fel och exception där
observeraren alltid återställs. Plain och instrumenterad körning ska ge
byteidentiska outputs och samma fel.

### Metric group 6: cost ledger och `ClosureRouteResolver`

Instrumentera den faktiska produktionskedjan genom
`cost_ordered_execution.build_cost_ledger`, den använda deterministic cost
source och `disruption.ClosureRouteResolver`:

- parent candidates och daily units,
- memory/disk cache hits och misses,
- antal resolverinstanser,
- `resolve`-anrop totalt och per unik full routing/closure identity,
- unika ruttfiler, route digests och unika edge-tupler,
- XML-parseanrop och bytes,
- exklusiv tid för parsing, resolveruppslag och verklig ruttberäkning,
- residual och uttryckligen omätta delar.

En route eller resolverträff får inte nycklas enbart på sökväg. Mätningen ska
redovisa den fulla befintliga identity som scorer och writer delar. Ändra inte
cachepolicy eller resolverlogik nu.

### Metric group 7: resultatidentitet

Kör samma hermetiska cost-ledger-underlag med och utan observerare och bevisa:

- byteidentisk serialiserad cost ledger/content key,
- identisk sorterad kostnadslista,
- identisk vinnare,
- identiska disqualifications,
- identiskt stop proof/cursor,
- identisk provider- och routing identity.

Använd befintliga profilerfixturer och artifacts. Om ett befintligt
kvalificerat manifest kan konsumeras utan source-konflikt får det användas
read-only. Re-binda aldrig ett gammalt arkiv och bygg inget nytt.

Uppdatera
`validation/passage_step5_archive_cost_measurement_20260913.json` med de nya
fixturräkningarna och mätgrupperna. Behåll:

```json
{
  "status": "instrumented_unmeasured",
  "missing_precondition": "current-source qualified archive",
  "release_evidence": false
}
```

Fixturtider ska märkas `not_production_timing`. Produktionsfält ska vara
`null`, aldrig uppskattade från syntetiska data.

Kör minst testerna för step5-mätningen, `monthly_demand`, `day_library`,
`independent_daily`, `monthly_search`, `cost_ordered_execution`,
`deterministic_disruption`, `disruption`, `monthly_sumo`,
`profile_monthly_cost_ledger`, provenance och `io_phases`. Kör
`git diff --check`.

Committa och pusha Del B separat. Rapportera exakta testresultat och vilka
produktionsmätningar som fortfarande saknas. Avsluta steg 5 som
**instrumentering komplett, produktion omätt**; välj ingen optimeringshypotes
förrän ett current-source-arkiv kan mätas. Börja inte steg 6.
