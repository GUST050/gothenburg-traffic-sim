# Gothenburg traffic pipeline — run steps in order with `make all`.
# Individual steps: make data / features / agent1 / forecast / test / serve
#
# New sensor data? Drop it in data_in/ (see data_in/README.md), add the
# validated station record to data_in/sensors.json, then `make refresh`.
# Explicit paths still work: make data DATA_DIR="/path" COORDS="/path.csv"

.PHONY: all refresh data features agent1 forecast test serve sumo-net demand scenario deso benchmark-speed validate-temporal

all: data features agent1 forecast test

# Full re-run after new data: rebuild everything from raw CSVs to scenarios.
# (fetch_deso.py is NOT listed here — build_candidates.py auto-fetches it on
# first use if data_in/deso/ is missing; `make deso` below is for an explicit
# manual re-run, e.g. after the inner-city bbox changes.)
refresh: data features agent1 forecast dirsplit-coverage sumo-net demand scenario test
	@echo "Refresh klar — starta med: make serve"

deso:
	python3 fetch_deso.py

# ── Phase 3: SUMO ──────────────────────────────────────────────────────────
# make sumo-net && make demand && make scenario
# Custom closure: python3 run_scenario.py --close <edgeId> [<edgeId> …]

sumo-net:
	python3 build_sumo_net.py

# Direction split: the deployed hour x day-type D-factor profile (the model
# that won dirsplit/benchmark.py's leakage-free tournament). It needs only the
# tracked training table and the published network geometry.
demand:
	python3 -m dirsplit.predict
	python3 build_sumo_demand.py --begin 00:00 --end 24:00

# Fast variant: morning window only (quicker sims while iterating)
demand-morning:
	python3 build_sumo_demand.py

# Pre-warm the demand day library so viewing a date and searching over it
# stop paying for calibration. FROM/TO default to the 2027 forecast year;
# measured cost is ~100-150 s and ~32 MB per day-slot, so a whole year is a
# background job of roughly 30 h and ~24 GB that is safe to interrupt and
# rerun (it resumes).
FROM ?= 2027-01-01
TO   ?= 2027-12-31
SOURCE ?= forecast
warm-horizon:
	python3 warm_demand_horizon.py --source $(SOURCE) --from $(FROM) --to $(TO)

warm-horizon-plan:
	python3 warm_demand_horizon.py --source $(SOURCE) --from $(FROM) --to $(TO) --dry-run

scenario:
	python3 run_scenario.py
	python3 run_scenario.py --close 60786979_3575001205_0 1455801464_18241874_0

# Frozen cross-date validation for the trusted 2025-09-16 release. The
# evaluation date was not used to select the deployed through-share target.
validate-temporal:
	python3 validate_sim.py --holdout-date 2025-09-17
	python3 validation_report.py

# Standalone speed/semantic benchmark; intentionally not part of `make test`.
benchmark-speed:
	python3 tools/benchmark_speed.py --trials 3 --workers 1 2 3 \
		--write /private/tmp/gs-speed-benchmark.json

# ── Direction-split model (dirsplit/) ─────────────────────────────────────
# The Norwegian acquisition client was removed 2026-08-16 together with the
# LightGBM quantile models it fed; the deployed profile is fitted from the
# tracked training table.

dirsplit-coverage:
	python3 -m dirsplit.coverage

dirsplit-dataset:
	python3 -m dirsplit.dataset

dirsplit-predict:
	python3 -m dirsplit.predict

# Diagnostic: do Gothenburg's OWN sensors carry intraday directional signal for
# a two-way sensor's split, and how large is the location bias that stops a
# neighbour's profile from being read as that split? Writes a non-release
# artifact; no gate may be decided from it.
local-direction-evidence:
	python3 -m tools.measure_local_direction_evidence

# Diagnostic: once sensor 107's published LEVEL is known, does the transferred
# intraday SHAPE beat a flat anchor? This is the baseline the deployed system
# actually poses, which Gate M's 50/50 comparison does not.
anchored-shape-value:
	python3 -m tools.measure_anchored_shape_value

# Diagnostic: should the intraday shape come from the pooled group curve or
# from the nearest aligned counter on the same corridor? Measured on Norwegian
# stations, where direction truth exists. Writes a non-release artifact.
donor-shape-transfer:
	python3 -m tools.measure_donor_shape_transfer

# Gate M: is there a robust conditional direction signal, or is 50/50 the
# honest central estimate? `--table legacy` (the tracked aggregate) is what is
# runnable today and CANNOT decide the gate — the blocked-date fold and the
# count model need raw per-station volumes, which this repository no longer
# fetches.
dirsplit-benchmark:
	python3 -m dirsplit.benchmark

dirsplit-observability:
	python3 -m dirsplit.coverage --evidence-only

# Gate S: does direction variation change a closure decision when the SUMO
# seed is held matched? Needs a calibrated demand build for the frozen date.
direction-sensitivity:
	python3 -m tools.measure_direction_decision_sensitivity run

data:
	python3 build_data.py $(if $(DATA_DIR),--data_dir "$(DATA_DIR)") $(if $(COORDS),--coords "$(COORDS)")

features:
	python3 build_features.py

agent1:
	python3 train_agent1.py

forecast:
	python3 build_agent1_flows.py

test:
	python3 -m pytest tests/ -q

# Web app + scenario API (click-to-close in the map needs this server).
# A busy port 8000 steps to the next free one on its own; set PORT to pin
# a specific one instead:  make serve PORT=8001
serve:
	python3 serve.py $(if $(PORT),--port $(PORT))
