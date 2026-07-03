# data_in/ — drop folder for new sensor data

Put new quarterly (15-min) sensor CSV deliveries here and the pipeline picks
them up automatically. This is how the program "takes data and improves its
traffic estimate".

## Adding a new delivery / new sensor

1. **Drop the files here:**
   - the sensor CSVs (same format as the original delivery: Mätplats, Level,
     Datum, Kvart, Antal passager)
   - a coordinates CSV with `koordinat` in the filename (Mätplats, LatX, LongY
     in SWEREF99 12 00)
2. **Check the measured direction** in Göteborgs Stad's trafikmängder
   catalogue (Power BI — link in CLAUDE.md): does the station show ONE
   compass letter = Total (single direction) or two letters + Total
   (genuine two-way)? Add the station to `SENSOR_MEASURED_DIRECTION` in
   `build_data.py`. **Do not skip this** — the delivered "Total" label is
   unreliable (4 of our 5 original "Total" sensors were single-direction).
3. **Run `make refresh`** — rebuilds the network (direction-aware snapping),
   features, forecasts, direction-split predictions, SUMO demand calibration
   and the baseline scenario. New sensors automatically get: an edge on the
   map, a coverage check against the training cloud, their own locally
   weighted direction model (if two-way), and a place in the calibration.

If data_in/ is empty, the pipeline falls back to the original delivery
paths in ~/Downloads (see build_data.py).
