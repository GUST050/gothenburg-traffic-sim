# Gothenburg traffic pipeline — run steps in order with `make all`.
# Individual steps: make data / features / agent1 / forecast / test / serve
#
# New sensor data? Drop it in data_in/ (see data_in/README.md), add the
# validated station record to data_in/sensors.json, then `make refresh`.
# Explicit paths still work: make data DATA_DIR="/path" COORDS="/path.csv"

.PHONY: all refresh data features agent1 forecast test serve sumo-net demand scenario deso benchmark-speed validate-temporal dirsplit-validate

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

# Direction split: use the TRAINED dirsplit model when it exists,
# fall back to the Gaussian AM/PM estimate otherwise.
demand:
	@if [ -f data/dirsplit/model.pkl ]; then \
		python3 -m dirsplit.predict; \
	else \
		python3 estimate_directions.py; \
	fi
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
# Full fetch takes hours (394 stations, throttled APIs) — run overnight.

dirsplit-stations:
	python3 -m dirsplit.fetch_norway --stations

dirsplit-volumes:
	python3 -m dirsplit.fetch_norway --volumes

dirsplit-match:
	python3 -m dirsplit.match

dirsplit-coverage:
	python3 -m dirsplit.coverage

dirsplit-dataset:
	python3 -m dirsplit.dataset

dirsplit-train:
	python3 -m dirsplit.train

dirsplit-predict:
	python3 -m dirsplit.predict

# Out-of-sample check of the direction model's published claims: nested
# shrinkage lambda (train.py fits lambda on the rows it then scores), measured
# interval coverage, and the per-sensor kernel's position relative to the
# training cloud. ~12 min; writes an evidence artifact to validation/.
dirsplit-validate:
	python3 -m dirsplit.validate --out validation/dirsplit_gate_m.json

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

# Web app + scenario API (click-to-close in the map needs this server)
serve:
	python3 serve.py
