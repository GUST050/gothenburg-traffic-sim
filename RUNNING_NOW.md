# Pågående körning: årlig warm-state-population 2027

**Startad:** 2026-08-04 15:06 · **Uppdaterad:** 2026-08-04

Det här är ett driftkort för den körning som pågår just nu. Det är inte en
plan eller ett kontrakt — `IMPROVEMENT_PLAN.md` och `ARCHITECTURE.md` gäller
för allt sådant. Radera filen när körningen är klar och avstämd.

## Vad som kör

```
process   : populate_annual_warming.py --execute
plannyckel: de071336ab0e0c5dfd69a1f1052681c02ef9bad5cf46ff2d9099fc0132f203db
root      : runs/annual-warm-2027/de071336…f203db/
omfattning: 104 685 enheter, 367 tre-dagars demand-byggen, 363 dagar
```

Processen är frikopplad (`PPID 1`, adopterad av launchd). Att stänga VS Code,
terminalen eller en Claude-session påverkar den inte. En `caffeinate -i -m -s -w
<pid>` är bunden till processen och avslutar sig själv när den är klar.

## Kolla status

```bash
cd /Users/gt/Documents/gs-project
python3 tools/populate_annual_warming.py --status     # läser bara, säkert under körning
tail -f runs/annual-warm-logs/latest.log
```

Räknaren står stilla i flera minuter åt gången medan ett demand-bygge kör.
Det är normalt: enheterna inom en kedja är snabba (~2,7 s), men PFE:n mellan
kedjorna tar ~9 min. Loggen visar skillnaden direkt.

## Starta om efter avbrott

Allt färdigt arbete är durabelt (SQLite + innehållsadresserad butik).
Färdiga enheter hoppas över; `pending`, `running` och `failed` körs om.

```bash
cd /Users/gt/Documents/gs-project
nohup python3 -u tools/populate_annual_warming.py --execute \
  --plan-key de071336ab0e0c5dfd69a1f1052681c02ef9bad5cf46ff2d9099fc0132f203db \
  --state-workers 3 >> runs/annual-warm-logs/latest.log 2>&1 &
nohup caffeinate -i -m -s -w $! >/dev/null 2>&1 &
```

Det som förloras vid ett avbrott är enbart det halvfärdiga demand-bygget
(som mest ~9 min). Ett halvskrivet arkiv plockas aldrig upp av misstag —
`find_demand_archives` kräver manifest-status `succeeded`.

**Viktigt:** ändra ingen fingeravtryckt källa medan körningen pågår. Källförseglingen
tvingar då fram en ny plannyckel och en ny root, och allt hittills kastas.

## Vad som stoppar den

| | stoppar? |
|---|---|
| stänga VS Code / terminal / Claude-session | nej |
| avstängning, omstart, utloggning | ja |
| fälla ihop locket utan extern skärm + ström | ja (caffeinate skyddar inte mot detta) |
| tomgång / skärmsläckare | nej (caffeinate aktiv) |

## Tidsåtgång

Uppmätt efter 1,19 h och 5 av 367 byggen:

```
väggtid per demand-bygge : 14,3 min
väggtid per state-enhet  :  2,7 s
```

Två oberoende projektioner: via byggen 86 h, via enheter 78,5 h.
**Uppskattat klart: fredag 7 aug kväll till lördag 8 aug morgon.**

Fördelningen per bygge (858 s): PFE 525 s (61 %, använder alla 10 kärnor),
state-enheter 259 s (30 %, använder 3 av 10), kandidater 40 s (5 %).

Ingen gratis acceleration finns. Undersökt och avfärdat:

- **Nätverk/ethernet** — körningen gör noll nätverks-I/O.
- **Dela demand mellan överlappande fönster** (varje kalenderdag räknas i 3
  byggen, redundansfaktor 2,99×). Avfärdad: kandidatpoolens cache-nyckel
  innehåller `real_day_shape` och `day_blocks`, och `multi_day_blocks` behåller
  medvetet varje dags egen uppmätta avgångsprofil. Dag D löses därför mot en
  annan variabeluppsättning i varje fönster — 5 byggen gav 5 olika cache-nycklar.
  Att dela poolen vore en modelländring, inte en optimering.
- **Fler state-workers** (3 → 6 skulle kapa ~13 h) kräver ny benchmark-evidens
  enligt `approved_seed_workers()`, och bara 7,3 GiB minne var ledigt.

Den enda kvarvarande riktiga hävstången är S2/S3 i
`DEMAND_PIPELINE_REVIEW_2026-08-04.md` (4 137 tak varav 2 binder; ingen early
exit i kärnan). De sitter inuti varje kvartslösning och skalar därmed med hela
PFE-tiden — men de kräver omstart plus bit-exakt bevis.

## Efter körningen

Population ger varken release- eller aktiveringsrätt. Artefakterna behöver
egen fullständighetsgranskning, och produktaktivering är en separat grind.

Två saker som väntar på att `sumo/` blir ledigt:

- **Jämförelsevyn** (grön = mindre trafik, röd = mer) är verifierad i webbläsare
  men aldrig körd mot en riktig stängning. Kör en stängning och slå på
  ⇄ Jämför med baslinje.
- **Första riktiga signalstudien.** `signal_closure_combine.py` är byggd och
  enhetstestad men aldrig körd skarpt — enda artefakten på disk är ett
  15-minuters röktest med 25 fordon.
