EXAMPLE ?= vehicle_safety
PORT    ?= 8001
ROOT    := $(CURDIR)
OUT     := $(ROOT)/_tmp_output/$(EXAMPLE)
CSV     := $(ROOT)/fake_data/sample.csv
VENV    := $(ROOT)/.venv
BIN     := $(VENV)/bin

.PHONY: help venv data gen serve dev clean nuke test

help:
	@echo "flywheel — cookiecutter labeling template"
	@echo
	@echo "Targets:"
	@echo "  make venv          install dev deps via uv into .venv/"
	@echo "  make data          generate fake CSV ($(CSV))"
	@echo "  make gen           cookiecutter scaffold $(EXAMPLE) into $(OUT)"
	@echo "  make serve         start Datasette on http://localhost:$(PORT)/flywheel"
	@echo "  make dev           clean + data + gen + serve  (one-shot loop)"
	@echo "  make test          run compliance test suite (pytest)"
	@echo "  make clean         wipe _tmp_output/"
	@echo "  make nuke          clean + remove .venv"
	@echo
	@echo "Override: make EXAMPLE=other_example PORT=8002 dev"

venv:
	@command -v uv >/dev/null || { echo "uv not installed"; exit 1; }
	uv sync

data: $(CSV)

$(CSV): fake_data/generate.py
	$(BIN)/python fake_data/generate.py $(CSV)

gen: data
	PATH="$(BIN):$$PATH" bash scripts/generate.sh $(EXAMPLE)

serve:
	PATH="$(BIN):$$PATH" bash scripts/serve.sh $(OUT) $(PORT)

dev: clean gen serve

test: data
	PATH="$(BIN):$$PATH" $(BIN)/pytest tests/compliance -v

clean:
	rm -rf _tmp_output

nuke: clean
	rm -rf .venv
