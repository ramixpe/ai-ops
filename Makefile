PYTHON ?= python3
DEVICE ?=

.PHONY: help setup test lint inventory facts interfaces bgp lldp isis sr \
        fabric-bgp analyze demo diff capture learn-topology health health-fixtures \
        baseline-pin baseline-show flaps mcp inspect docker-build clean

help:
	@echo "IOS-XR Read-Only Network Tools"
	@echo ""
	@echo "Common commands (activate the venv first):"
	@echo "  make setup         Create venv and install the package (dev+llm extras)"
	@echo "  make test          Run unit tests"
	@echo "  make lint          Run Ruff"
	@echo "  make inventory     List devices without credentials"
	@echo "  make facts         Facts on PE1 (or DEVICE=name)"
	@echo "  make interfaces    Interface status on PE1 (or DEVICE=name)"
	@echo "  make bgp           BGP summary on PE1 (or DEVICE=name)"
	@echo "  make lldp          LLDP neighbors on PE1 (or DEVICE=name)"
	@echo "  make isis          IS-IS neighbors on PE1 (or DEVICE=name)"
	@echo "  make sr            SR-TE policies on PE1 (or DEVICE=name)"
	@echo "  make fabric-bgp    BGP summary across the whole inventory"
	@echo "  make analyze       Collect evidence and analyze with the selected LLM"
	@echo "  make demo          Run the narrated agent demo (or DEVICE=name)"
	@echo "  make diff          Diff evidence against the last snapshot (or DEVICE=name)"
	@echo "  make capture       Recapture test fixtures from the whole lab"
	@echo "  make learn-topology  Derive expected topology from fixtures and update inventory/lab.yaml"
	@echo "  make health        Evaluate health verdicts across the whole fabric (live)"
	@echo "  make health-fixtures  Evaluate health verdicts against the committed t0 fixtures"
	@echo "  make baseline-pin  Pin a golden snapshot (or DEVICE=name)"
	@echo "  make baseline-show Print a device's pinned golden snapshot (or DEVICE=name)"
	@echo "  make flaps         Detect oscillating fields in snapshot history (or DEVICE=name)"
	@echo "  make mcp           Start the MCP server over stdio"
	@echo "  make inspect       Smoke-test the MCP server (or DEVICE=name)"
	@echo "  make docker-build  Build the MCP server container image"

setup:
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && python -m pip install --upgrade pip && pip install -e ".[dev,llm]"

test:
	pytest -q

lint:
	ruff check .

inventory:
	nettools inventory

facts:
	nettools facts $(DEVICE)

interfaces:
	nettools interfaces $(DEVICE)

bgp:
	nettools bgp $(DEVICE)

lldp:
	nettools lldp $(DEVICE)

isis:
	nettools isis $(DEVICE)

sr:
	nettools sr $(DEVICE)

fabric-bgp:
	nettools fabric bgp

analyze:
	nettools analyze $(DEVICE)

demo:
	nettools demo $(DEVICE)

diff:
	nettools diff $(DEVICE)

# Recaptures both halves of the quiet-fabric pair. Review the git diff by eye
# before committing: fixtures are permanent once pushed.
capture:
	nettools capture --all --label t0
	sleep 75
	nettools capture --all --label t1

# Derives expected/ blocks from the committed t0 fixtures and prints the
# fabric anomaly report; pass ARGS=--live to derive from a live collection.
learn-topology:
	nettools learn-topology $(ARGS)

# Deterministic health verdicts (see CLAUDE.md, "Phase 4"). Exit codes:
# 0 ok/info, 1 warning, 2 critical.
health:
	nettools health --all

health-fixtures:
	nettools health --all --from-fixtures

baseline-pin:
	nettools baseline pin $(DEVICE)

baseline-show:
	nettools baseline show $(DEVICE)

flaps:
	nettools flaps $(DEVICE)

mcp:
	nettools-mcp

inspect:
	nettools inspect $(DEVICE)

docker-build:
	docker build -t ios-xr-nettools-mcp .

clean:
	rm -rf .pytest_cache .ruff_cache build *.egg-info src/*.egg-info
	find . -path ./.venv -prune -o -name __pycache__ -type d -print0 | xargs -0 rm -rf