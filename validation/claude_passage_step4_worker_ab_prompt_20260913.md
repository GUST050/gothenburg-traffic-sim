# Prompt till Claude: korrigera retentionrapporten och avsluta steg 4 med worker-A/B

Fortsätt på `claude/exciting-rubin-1e6k5m` från `760fa08`. Arbeta i isolerad
worktree och rör inte huvudcheckoutens orelaterade ändringar. Starta ingen SUMO-
körning, månadssökning, kataloggenerering, demand-byggnad eller uppvärmning.

Steg 0–3 är klara. Steg 4:s instrumentering, q50-replay och fulla
trevariants-retention finns. Innan optimering måste tre rapporteringsfel
korrigeras, därefter ska ett enda avgränsat experiment avgöra om retentionens
worker-tak ska ändras.

## Del A — applicera rapporteringsfixen

Applicera `validation/claude_step4_retention_review_fix_20260913.patch` om den
finns. Om den saknas, gör exakt detta:

1. Uppdatera alla sammanfattningar till den committade evidensens verkliga
   tider: retention root wall 5,137247 / 5,811303 / 5,708821 s; median för
   repeat 2–3 är 5,760062 s. Kopiering är 0,677043 / 0,446238 / 0,464685 s och
   ska fortsatt ligga utanför retentionstiden.
2. `compression_ratio=0,192312` är hela trädets kvarvarande bytes efter/före,
   inte gzip-payloadens kompressionsgrad. Behåll fältet endast med explicit
   basis och lägg till:
   - `retained_tree_ratio = bytes_on_disk_after / bytes_on_disk_before`
   - `gzip_payload_ratio = compressed_payload_bytes / compressed_source_bytes`
   - `compressed_source_bytes`
   - `compressed_payload_bytes`
3. För den verkliga roten är `compressed_source_bytes=805331251`,
   `compressed_payload_bytes=139865918` och `gzip_payload_ratio=0,173675`.
4. Uppdatera `TASKS.md`, `AGENT_NOTES.md` och `IMPROVEMENT_PLAN.md`; deras
   current/status-text får inte säga att concurrent-fixen, retentionmätningen
   eller `_tracked_main`-instrumenteringen fortfarande saknas.

Kör de fokuserade testerna och `git diff --check`. Codex referens är 60
passerade fokuserade tester och 598 passerade i de sexton relevanta modulerna.
Committa och pusha Del A separat när allt är grönt.

## Del B — A/B/B/A för tre mot sex retention-workers

Hypotesen är begränsad: `prune_evidence` använder högst tre workers trots tio
logiska kärnor. Den uppmätta samtidighetsfaktorn var 2,96 på tre workers, men
det bevisar inte att sex blir snabbare. Mät innan produktionsvärdet ändras.

Gör worker-taket till en tydlig intern konstant eller privat policyfunktion.
Baslinjen ska vara exakt 3 och kandidaten exakt 6, fortsatt begränsad av
`len(paths)` och `os.cpu_count()`. Lägg inte till ett dolt miljövariabelkontrakt.
Rapportera begärt och faktiskt worker-antal.

Använd samma oförändrade källrot i alla armar:

```text
runs/automatic-passage-97f1ab116a8e48d8a05621247431715a
```

Den ska först verifieras som tre varianter, 156 filer, 1 075 071 455 byte,
118 råa XML och 877 499 944 råa XML-byte. Om den saknas i din miljö ska du inte
bygga en ersättare och inte fabricera tider.

Experimentdesign:

- ordning A1/B1/B2/A2 där A=3 workers och B=6 workers,
- tre repeats per arm i samma process,
- separat process och separat outputrot per arm,
- vanlig ägd kopia per repeat, aldrig hårdlänk,
- kopiering redovisas separat och ingår inte i retention wall,
- ingen arm delar muterbar output med en annan,
- redovisa faktisk root wall, concurrent-regionens wall, child thread sum,
  bytes, filantal och peak RSS per arm,
- använd root wall för prestandabeslut; summerad trådtid är diagnostik.

Exakthetskrav:

1. Originalrotens paths, storlekar och SHA-256 är identiska före/efter varje
   arm.
2. Alla 114 gzipfiler dekomprimeras till samma digest som originalet.
3. Hela resulterande filträdet efter pruning är byteidentiskt mellan worker 3
   och worker 6, inklusive deterministiska gzipbytes.
4. Samma raw/compressed counts, retained-tree ratio och gzip-payload ratio.
5. Candidate-mappar, original-backuper och source_reports behandlas identiskt.
6. Rankad väggtid överstiger aldrig retention-rootens wall utanför liten
   mättolerans, och positiva andelar summerar till cirka 100 %.

Acceptans:

- kandidatens median för retention root wall är minst 15 % lägre än
  baslinjens,
- kandidatens sämsta relevanta mätning ska helst ligga under baslinjens bästa;
  om fördelningarna överlappar ska resultatet bedömas försiktigt,
- inga bytes eller kontraktsutfall skiljer sig,
- peak RSS och systembelastning får ingen stor oförklarad ökning,
- inga relevanta tester faller.

Om kraven passerar: behåll worker 6, skriv RED/GREEN-test som låser
policyfunktionen och committa optimeringen. Om de inte passerar: återställ
produktionsvärdet till 3 och committa endast experimentets test/evidens. Ändra
inte gzipnivå, mtime, verifiering, tempfil/publicering eller städsemantik.

Skapa `validation/passage_step4_retention_workers_ab_20260913.json` med fulla
revisioner, A/B/B/A-råvärden, medianer, spridning, resursmått, alla digests och
`verdict: accepted|rejected`. Sätt `release_evidence: false`.

## Efter experimentet

Stäng steg 4 om rapporteringsfixen och worker-experimentet är kompletta.
`_tracked_main` är instrumenterad men får stå som produktionstid uppskjuten till
nästa redan motiverade demand-canary; starta inte en separat dyr körning bara
för den timern.

Implementera inte en processcache för `source_trace_xml`. De tre spåren har
olika digest och läses en gång vardera i normal `_refine`; replay-repeats
bevisar ingen produktionscacheträff. Börja inte steg 5 i samma commitserie.

Rapportera separata commit-SHA:n, exakt testutfall, A/B-resultat, accept/reject,
beräknad vinst per färdig trevariantsrot och vad som återstår i planen. Pusha
endast gröna commits till samma Claude-gren.
