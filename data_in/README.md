# Importera en ny sensor

Programmet kan lägga till en ny fysisk sensor från samma CSV-format som
Göteborgs Stads befintliga leverans. Mätfilen ska innehålla **en** ny Mätplats
med kolumnerna `Mätplats`, `level`, `date`/`Datum`, `Kvart`/`Tid` och
`Antal passager`. Koordinatfilen ska innehålla samma Mätplats samt `LatX` och
`LongY` i SWEREF99 12 00 (EPSG:3007). Den aktuella datapipelinen bygger
2025 års 15-minutersintervall; andra år kan ännu inte importeras.

## 1. Granska sensorposten

Skapa en JSON-fil för den nya sensorn. Ta gärna en befintlig post i
[`sensors.json`](sensors.json) som mall, men använd den nya sensorns egna
uppgifter. Posten måste ha `sensor_id`, `active_from`, `active_to`,
`measurement_semantics` (`directional` eller `two_way_total`),
`measured_bearing` och `permitted_bearings`, `coordinate_reference_system` =
`EPSG:3007`, `coordinates` = `null`, `source`, `source_file`, `notes` samt
granskade fält enligt nedan:

```json
{
  "sensor_id": "NYTT_ID",
  "active_from": "2025-01-01",
  "active_to": null,
  "measurement_semantics": "directional",
  "measured_bearing": "N",
  "permitted_bearings": ["N"],
  "coordinate_reference_system": "EPSG:3007",
  "coordinates": null,
  "source": "Göteborgs Stad trafikmängder",
  "source_file": "koordinater.csv",
  "catalogue_verification": {
    "status": "verified",
    "date": "YYYY-MM-DD",
    "verifier": "namn"
  },
  "quality_status": "accepted",
  "snap_status": "approved",
  "approved_edge_ids": ["GRANSKAD_RIKTAD_VÄGKANT"],
  "snap_distance_m": 0.0,
  "manual_snap": null,
  "notes": "Beskriv mätplats och riktning"
}
```

Kontrollera riktningen i stadens trafikkatalog; `Level=Total` i leveransen
bevisar inte att sensorn mäter båda riktningarna. Granska även nätverkssnappens
vägkant och avstånd. Sätt inte `verified`, `accepted` eller `approved` innan
respektive kontroll verkligen är gjord. En tvåvägssensor behöver sina båda
granskade riktade vägkanter i `approved_edge_ids` och `measured_bearing: null`.

## 2. Importera

Från projektets rot:

```sh
python3 tools/add_sensor.py --measurements /sökväg/ny_sensor.csv \
  --coordinates /sökväg/koordinater.csv --record /sökväg/ny_sensor.json --dry-run
python3 tools/add_sensor.py --measurements /sökväg/ny_sensor.csv \
  --coordinates /sökväg/koordinater.csv --record /sökväg/ny_sensor.json
```

Importen avvisar fel format, dubbla intervall, saknad koordinat, ett ID som
redan finns samt ogranskad metadata. Den sparar sensorns mätfil och
koordinatfil i `data_in/` och lägger till posten i `sensors.json`. Den kör
**inte** om modellen eller skriver över publicerade resultat.

När du vill bygga om efter importen kör du `make refresh`. Bygget läser då
både den ursprungliga leveransen och de nya filerna i `data_in/`; det stoppar
vid överlappande mätintervall eller om den nya sensorn inte får exakt sin
granskade vägnätskoppling. När nätet och riktningsprofilen är klara bygger `make refresh`
automatiskt innehållsadresserade ruttkataloger för vardag och helg med de
aktuella indata. Befintliga katalogposter återanvänds om deras kontrollsummor
stämmer. Rapporten skrivs till `runs/route-catalog-build-auto.json`.
Nya katalogposter tas i bruk först efter projektets separata kvalificering och
adoption; tills dess faller efterföljande efterfrågebygge tillbaka på den
kvalificerade äldre vägen. `make refresh` kan ta tid och skapa nya
simuleringsresultat, så gör det som ett separat steg.
Om `python3` inte pekar på projektets installerade miljö kan du välja tolk med
`make refresh PYTHON=/sökväg/till/python3`.

Om den ursprungliga leveransen inte finns på maskinen måste du ange en komplett
sensorleverans med `make data DATA_DIR="/sökväg/alla_mätningar"` och motsvarande
`COORDS` eller placera den kompletta leveransen i `data_in/`.
