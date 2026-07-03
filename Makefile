# Gothenburg traffic pipeline — run steps in order with `make all`.
# Individual steps: make data / features / agent1 / forecast / test / serve
#
# New sensor data? Drop it in data_in/ (see data_in/README.md), add the
# station to SENSOR_MEASURED_DIRECTION in build_data.py, then `make refresh`.
# Explicit paths still work: make data DATA_DIR="/path" COORDS="/path.csv"

.PHONY: all refresh data features agent1 forecast test serve sumo-net demand scenario

all: data features agent1 forecast test

# Full re-run after new data: rebuild everything from raw CSVs to scenarios.
refresh: data features agent1 forecast dirsplit-coverage sumo-net demand scenario test
	@echo "Refresh klar — starta med: make serve"

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
	python3 build_sumo_demand.py

scenario:
	python3 run_scenario.py
	python3 run_scenario.py --close 60786979_3575001205_0

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
