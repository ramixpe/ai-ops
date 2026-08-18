PYTHON ?= python3
# PE1 matches agent_nettools.inventory.get_default_device_name()'s own
# fallback, so this is an explicit spelling of the same default, not a new
# one. Made explicit (rather than left empty) because the Phase 5 template
# targets below take a *second* positional argument (PREFIX/ADDRESS/NAME);
# with DEVICE left empty, `nettools route  10.255.0.31` collapses under
# shell word-splitting to a single argument and the required second
# positional goes missing.
DEVICE ?= PE1
PREFIX ?= 10.255.0.31
ADDRESS ?= 10.255.0.31
NAME ?= GigabitEthernet0/0/0/1
COUNT ?= 20
QUESTION ?= What, if anything, is wrong with the fabric right now?
KEEP_DAYS ?= 30
KEEP_COUNT ?= 20

.PHONY: help setup test lint inventory facts interfaces bgp lldp isis sr \
        fabric-bgp route bgp-neighbor interface logging ping traceroute \
        analyze analyze-fabric agent demo diff capture learn-topology health health-fixtures \
        baseline-pin baseline-show flaps evidence-prune metrics version mcp inspect \
        docker-build clean

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
	@echo "  make route         Look up a route (DEVICE=name PREFIX=10.0.0.0/24)"
	@echo "  make bgp-neighbor  Look up a BGP neighbor (DEVICE=name ADDRESS=...)"
	@echo "  make interface     Look up an interface (DEVICE=name NAME=...)"
	@echo "  make logging       Show recent log lines (DEVICE=name COUNT=...)"
	@echo "  make ping          Ping from a device (DEVICE=name ADDRESS=...); active probe"
	@echo "  make traceroute    Traceroute from a device (DEVICE=name ADDRESS=...); active probe"
	@echo "  make analyze       Collect evidence and analyze with the selected LLM"
	@echo "  make analyze-fabric  Analyze the whole fabric together (cross-device correlation)"
	@echo "  make agent         Ask the bounded tool-calling agent a question (QUESTION=...; Anthropic only)"
	@echo "  make demo          Run the narrated agent demo (or DEVICE=name)"
	@echo "  make diff          Diff evidence against the last snapshot (or DEVICE=name)"
	@echo "  make capture       Recapture test fixtures from the whole lab"
	@echo "  make learn-topology  Derive expected topology from fixtures and update inventory/lab.yaml"
	@echo "  make health        Evaluate health verdicts across the whole fabric (live)"
	@echo "  make health-fixtures  Evaluate health verdicts against the committed t0 fixtures"
	@echo "  make baseline-pin  Pin a golden snapshot (or DEVICE=name)"
	@echo "  make baseline-show Print a device's pinned golden snapshot (or DEVICE=name)"
	@echo "  make flaps         Detect oscillating fields in snapshot history (or DEVICE=name)"
	@echo "  make evidence-prune  Prune old snapshots (KEEP_DAYS=$(KEEP_DAYS) KEEP_COUNT=$(KEEP_COUNT))"
	@echo "  make metrics       Report operational metrics (JSON; ARGS=--format=prometheus for text exposition)"
	@echo "  make version       Print the installed nettools version"
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

# Validated, parameterized command templates (Phase 5). See CLAUDE.md,
# "Validated, parameterized command templates". ping/traceroute generate
# traffic (active probes) and are gated by NETTOOLS_ALLOW_ACTIVE_PROBES.
route:
	nettools route $(DEVICE) $(PREFIX)

bgp-neighbor:
	nettools bgp-neighbor $(DEVICE) $(ADDRESS)

interface:
	nettools interface $(DEVICE) $(NAME)

logging:
	nettools logging $(DEVICE) --count $(COUNT)

ping:
	nettools ping $(DEVICE) $(ADDRESS)

traceroute:
	nettools traceroute $(DEVICE) $(ADDRESS)

analyze:
	nettools analyze $(DEVICE)

# Phase 6: cross-device correlation over the whole fabric's evidence + Phase 4
# health verdicts, instead of one device at a time.
analyze-fabric:
	nettools analyze --fabric

# Phase 6: bounded, read-only tool-calling agent loop. Anthropic only -- see
# CLAUDE.md, "Bounded agent loop (Phase 6)".
agent:
	nettools agent "$(QUESTION)"

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

# Retention/prune (Phase 7): deletes timestamped snapshots outside the
# retention window (never the pinned golden snapshot), against whichever
# NETTOOLS_EVIDENCE_BACKEND selects.
evidence-prune:
	nettools evidence prune --keep-days $(KEEP_DAYS) --keep-count $(KEEP_COUNT)

# Phase 8: operational metrics (per-device collection outcomes/latency/retries,
# health verdict counts by severity). In-memory only unless
# NETTOOLS_METRICS_FILE is set -- see .env.example.
metrics:
	nettools metrics $(ARGS)

version:
	nettools version

mcp:
	nettools-mcp

inspect:
	nettools inspect $(DEVICE)

docker-build:
	docker build -t ios-xr-nettools-mcp .

clean:
	rm -rf .pytest_cache .ruff_cache build *.egg-info src/*.egg-info
	find . -path ./.venv -prune -o -name __pycache__ -type d -print0 | xargs -0 rm -rf

# --- OPS wave (B-477/B-480/B-476) -------------------------------------------
audit:  ## Deterministic fabric audit (exit 0 ok/info, 1 warning, 2 critical)
	nettools audit

audit-fixtures:  ## The same audit against committed captures (no lab needed)
	nettools audit --from-fixtures --label healthy

config-check:  ## Validate every NETTOOLS_*/provider env var; exit 1 on problems
	nettools config check

route-event:  ## Route an event from stdin (pipe an Alertmanager JSON or syslog line in)
	nettools route-event

