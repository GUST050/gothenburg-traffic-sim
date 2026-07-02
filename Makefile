# Gothenburg traffic pipeline — run steps in order with `make all`.
# Individual steps: make data / features / agent1 / forecast / test / serve
#
# Override raw-data paths if they move:
#   make data DATA_DIR="/path/to/csvs" COORDS="/path/to/koordinater.csv"

DATA_DIR ?= $(HOME)/Downloads/Data till Chalmers_20260618
COORDS   ?= $(HOME)/Downloads/Mätpunkter_koordinater.csv

.PHONY: all data features agent1 forecast test serve

all: data features agent1 forecast test

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
