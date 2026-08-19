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
POLICY_ID ?= 20:10.255.0.13
COUNT ?= 20
QUESTION ?= What, if anything, is wrong with the fabric right now?
KEEP_DAYS ?= 30
KEEP_COUNT ?= 20

.PHONY: help setup test lint inventory facts interfaces bgp lldp isis sr \
        fabric-bgp route bgp-neighbor interface sr-policy logging ping traceroute \
        analyze analyze-fabric agent demo diff capture learn-topology health health-fixtures \
        baseline-pin baseline-show flaps evidence-prune metrics version mcp inspect \
        docker-build clean audit audit-fixtures config-check route-event

# Self-maintaining: derived from the "## description" comment each target
# below carries, in the order they appear in this file, rather than a
# hand-copied second list -- a hand-maintained list drifts out of sync with
# the targets it describes (it did: audit/audit-fixtures/config-check/
# route-event existed and worked for a full wave before help mentioned any
# of them). Add a target, give it a trailing "##" comment, and it appears
# here for free; a target with no "##" comment is intentionally left off
# (there is currently none such). $(MAKEFILE_LIST) rather than a literal
# `Makefile` so this keeps working if the file is ever split/included.
help:  ## Show this help message
	@echo "IOS-XR Read-Only Network Tools"
	@echo ""
	@echo "Common commands (activate the venv first):"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "  make %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup:  ## Create venv and install the package (dev+llm extras)
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && python -m pip install --upgrade pip && pip install -e ".[dev,llm]"

test:  ## Run unit tests
	pytest -q

lint:  ## Run Ruff
	ruff check .

inventory:  ## List devices without credentials
	nettools inventory

facts:  ## Facts on PE1 (or DEVICE=name)
	nettools facts $(DEVICE)

interfaces:  ## Interface status on PE1 (or DEVICE=name)
	nettools interfaces $(DEVICE)

bgp:  ## BGP summary on PE1 (or DEVICE=name)
	nettools bgp $(DEVICE)

lldp:  ## LLDP neighbors on PE1 (or DEVICE=name)
	nettools lldp $(DEVICE)

isis:  ## IS-IS neighbors on PE1 (or DEVICE=name)
	nettools isis $(DEVICE)

sr:  ## SR-TE policies on PE1 (or DEVICE=name)
	nettools sr $(DEVICE)

fabric-bgp:  ## BGP summary across the whole inventory
	nettools fabric bgp

# Validated, parameterized command templates (Phase 5). See CLAUDE.md,
# "Validated, parameterized command templates". ping/traceroute generate
# traffic (active probes) and are gated by NETTOOLS_ALLOW_ACTIVE_PROBES.
route:  ## Look up a route (DEVICE=name PREFIX=10.0.0.0/24)
	nettools route $(DEVICE) $(PREFIX)

bgp-neighbor:  ## Look up a BGP neighbor (DEVICE=name ADDRESS=...)
	nettools bgp-neighbor $(DEVICE) $(ADDRESS)

interface:  ## Look up an interface (DEVICE=name NAME=...)
	nettools interface $(DEVICE) $(NAME)

sr-policy:  ## Look up an SR-TE policy's candidate path/SID detail (DEVICE=name POLICY_ID=colour:endpoint)
	nettools sr-policy $(DEVICE) $(POLICY_ID)

logging:  ## Show recent log lines (DEVICE=name COUNT=...)
	nettools logging $(DEVICE) --count $(COUNT)

ping:  ## Ping from a device (DEVICE=name ADDRESS=...); active probe
	nettools ping $(DEVICE) $(ADDRESS)

traceroute:  ## Traceroute from a device (DEVICE=name ADDRESS=...); active probe
	nettools traceroute $(DEVICE) $(ADDRESS)

analyze:  ## Collect evidence and analyze with the selected LLM
	nettools analyze $(DEVICE)

# Phase 6: cross-device correlation over the whole fabric's evidence + Phase 4
# health verdicts, instead of one device at a time.
analyze-fabric:  ## Analyze the whole fabric together (cross-device correlation)
	nettools analyze --fabric

# Phase 6: bounded, read-only tool-calling agent loop. Anthropic only -- see
# CLAUDE.md, "Bounded agent loop (Phase 6)".
# Gated since B-488: `nettools agent` is the one command where a model
# chooses its own tools and writes its own answer, which is the opposite
# of what the rest of this tool claims. Set NETTOOLS_ENABLE_AGENT=1 to opt in.
agent:  ## Ask the bounded tool-calling agent a question (QUESTION=...; Anthropic only)
	nettools agent "$(QUESTION)"

demo:  ## Run the narrated agent demo (or DEVICE=name)
	nettools demo $(DEVICE)

diff:  ## Diff evidence against the last snapshot (or DEVICE=name)
	nettools diff $(DEVICE)

# Recaptures both halves of the quiet-fabric pair. Review the git diff by eye
# before committing: fixtures are permanent once pushed.
capture:  ## Recapture test fixtures from the whole lab
	nettools capture --all --label t0
	sleep 75
	nettools capture --all --label t1

# Derives expected/ blocks from the committed t0 fixtures and prints the
# fabric anomaly report; pass ARGS=--live to derive from a live collection.
learn-topology:  ## Derive expected topology from fixtures and update inventory/lab.yaml
	nettools learn-topology $(ARGS)

# Deterministic health verdicts (see CLAUDE.md, "Phase 4"). Exit codes:
# 0 ok/info, 1 warning, 2 critical.
health:  ## Evaluate health verdicts across the whole fabric (live)
	nettools health --all

health-fixtures:  ## Evaluate health verdicts against the committed t0 fixtures
	nettools health --all --from-fixtures

baseline-pin:  ## Pin a golden snapshot (or DEVICE=name)
	nettools baseline pin $(DEVICE)

baseline-show:  ## Print a device's pinned golden snapshot (or DEVICE=name)
	nettools baseline show $(DEVICE)

flaps:  ## Detect oscillating fields in snapshot history (or DEVICE=name)
	nettools flaps $(DEVICE)

# Retention/prune (Phase 7): deletes timestamped snapshots outside the
# retention window (never the pinned golden snapshot), against whichever
# NETTOOLS_EVIDENCE_BACKEND selects.
evidence-prune:  ## Prune old snapshots (KEEP_DAYS=30 KEEP_COUNT=20 by default)
	nettools evidence prune --keep-days $(KEEP_DAYS) --keep-count $(KEEP_COUNT)

# Phase 8: operational metrics (per-device collection outcomes/latency/retries,
# health verdict counts by severity). In-memory only unless
# NETTOOLS_METRICS_FILE is set -- see .env.example.
metrics:  ## Report operational metrics (JSON; ARGS=--format=prometheus for text exposition)
	nettools metrics $(ARGS)

version:  ## Print the installed nettools version
	nettools version

mcp:  ## Start the MCP server over stdio
	nettools-mcp

inspect:  ## Smoke-test the MCP server (or DEVICE=name)
	nettools inspect $(DEVICE)

docker-build:  ## Build the MCP server container image
	docker build -t ios-xr-nettools-mcp .

clean:  ## Remove caches and build artifacts
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

