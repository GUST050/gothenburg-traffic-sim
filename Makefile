# Gothenburg traffic pipeline — run steps in order with `make all`.
# Individual steps: make data / features / agent1 / forecast / test / serve
#
# Override raw-data paths if they move:
#   make data DATA_DIR="/path/to/csvs" COORDS="/path/to/koordinater.csv"

DATA_DIR ?= $(HOME)/Downloads/Data till Chalmers_20260618
COORDS   ?= $(HOME)/Downloads/Mätpunkter_koordinater.csv

.PHONY: all data features agent1 forecast test serve sumo-net demand scenario

all: data features agent1 forecast test

# ── Phase 3: SUMO ──────────────────────────────────────────────────────────
# make sumo-net && make demand && make scenario
# Custom closure: python3 run_scenario.py --close <edgeId>

sumo-net:
	python3 build_sumo_net.py

demand:
	python3 estimate_directions.py
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

data:
	python3 build_data.py --data_dir "$(DATA_DIR)" --coords "$(COORDS)"

features:
	python3 build_features.py

agent1:
	python3 train_agent1.py

forecast:
	python3 build_agent1_flows.py

test:
	python3 -m pytest tests/ -q

serve:
	cd web && python3 -m http.server 8000
