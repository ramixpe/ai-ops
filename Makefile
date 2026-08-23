# Default to a version CI actually gates on. The matrix is 3.11 and 3.12;
# a bare `python3` is whatever the host happens to ship, which on this box
# was 3.13 -- so `make setup` silently produced a venv no CI leg exercises
# and every local `make test` was evidence about the wrong interpreter
# (OBS-695). Override for a different one: `make setup PYTHON=python3.11`.
PYTHON ?= python3.12

# Resolve dev tools through the local venv when it exists, so every target
# below works whether or not the venv is activated (EER-020). Before this,
# `make lint`/`make test`/any `nettools ...` target failed with a bare
# "command not found" outside an activated shell -- not a product defect
# (CONTRIBUTING.md's own "Before you change anything" says to activate
# first), but a rough enough edge that it has been mistaken for a real
# regression (`make: nettools: No such file or directory` read as a broken
# build rather than a missing `source .venv/bin/activate`). Each variable
# prefers $(VENV_BIN)/<tool> when `make setup` has actually created it --
# `$(wildcard ...)` is evaluated once, at parse time -- and falls back to the
# bare name, resolved through $PATH, when it has not: an activated venv or a
# tool installed elsewhere behaves exactly as before. `make setup` itself is
# untouched; it is what creates the path these variables check for.
VENV := .venv
VENV_BIN := $(VENV)/bin
NETTOOLS := $(if $(wildcard $(VENV_BIN)/nettools),$(VENV_BIN)/nettools,nettools)
NETTOOLS_MCP := $(if $(wildcard $(VENV_BIN)/nettools-mcp),$(VENV_BIN)/nettools-mcp,nettools-mcp)
# ...with one refinement for the two that are DEPENDENCY console scripts
# rather than this package's own. A console script is a generated file with the
# interpreter path baked into its shebang, so it can exist and still be
# unrunnable: rename or rebuild the venv and `$(wildcard ...)` still finds it
# while the shebang points at an interpreter that is gone.
#
# Measured 2026-08-23 by an external review: `.venv/bin/pytest` began
# `#!/.../.venv312/bin/python`, a directory that no longer exists, so `make
# test` failed with a bare "No such file or directory" while
# `.venv/bin/python -m pytest` ran 3755 tests green. That is a confusing
# failure at the worst moment -- `docs/build/MIGRATION.md`'s acceptance gate
# tells an agent rebuilding this repo on a new host to run `make test`, so the
# checkpoint written to prove the build works is the one that breaks.
#
# `$(VENV_BIN)/python -m <tool>` resolves the interpreter directly and never
# reads a generated shebang. `nettools`/`nettools-mcp` above stay as they are:
# they are this package's own entry points, `make setup` regenerates them with
# the editable install, and they have no `-m` form.
PYTEST := $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python -m pytest,pytest)
RUFF := $(if $(wildcard $(VENV_BIN)/python),$(VENV_BIN)/python -m ruff,ruff)

# PE1 matches agent_nettools.inventory.get_default_device_name()'s own
# fallback, so this is an explicit spelling of the same default, not a new
# one. Made explicit (rather than left empty) because the Phase 5 template
# targets below take a *second* positional argument (PREFIX/ADDRESS/NAME/
# SUBJECT); with DEVICE left empty, `nettools route  10.255.0.31` collapses
# under shell word-splitting to a single argument and the required second
# positional goes missing.
DEVICE ?= PE1
PREFIX ?= 10.255.0.31
ADDRESS ?= 10.255.0.31
NAME ?= GigabitEthernet0/0/0/1
POLICY_ID ?= 20:10.255.0.13
COUNT ?= 20
SUBJECT ?= 10.255.0.31
QUESTION ?= What, if anything, is wrong with the fabric right now?
KEEP_DAYS ?= 30
KEEP_COUNT ?= 20

.PHONY: help setup test lint inventory facts interfaces bgp lldp isis sr \
        fabric-bgp route bgp-neighbor interface sr-policy logging ping traceroute \
        analyze analyze-fabric agent demo diff capture learn-topology health health-fixtures \
        baseline-pin baseline-show flaps evidence-prune metrics version mcp inspect \
        docker-build clean audit audit-fixtures config-check route-event \
        investigate ledger-summary ledger-verdict

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
	$(PYTEST) -q

lint:  ## Run Ruff
	$(RUFF) check .

inventory:  ## List devices without credentials
	$(NETTOOLS) inventory

facts:  ## Facts on PE1 (or DEVICE=name)
	$(NETTOOLS) facts $(DEVICE)

interfaces:  ## Interface status on PE1 (or DEVICE=name)
	$(NETTOOLS) interfaces $(DEVICE)

bgp:  ## BGP summary on PE1 (or DEVICE=name)
	$(NETTOOLS) bgp $(DEVICE)

lldp:  ## LLDP neighbors on PE1 (or DEVICE=name)
	$(NETTOOLS) lldp $(DEVICE)

isis:  ## IS-IS neighbors on PE1 (or DEVICE=name)
	$(NETTOOLS) isis $(DEVICE)

sr:  ## SR-TE policies on PE1 (or DEVICE=name)
	$(NETTOOLS) sr $(DEVICE)

fabric-bgp:  ## BGP summary across the whole inventory
	$(NETTOOLS) fabric bgp

# Validated, parameterized command templates (Phase 5). See CLAUDE.md,
# "Validated, parameterized command templates". ping/traceroute generate
# traffic (active probes) and are gated by NETTOOLS_ALLOW_ACTIVE_PROBES.
route:  ## Look up a route (DEVICE=name PREFIX=10.0.0.0/24)
	$(NETTOOLS) route $(DEVICE) $(PREFIX)

bgp-neighbor:  ## Look up a BGP neighbor (DEVICE=name ADDRESS=...)
	$(NETTOOLS) bgp-neighbor $(DEVICE) $(ADDRESS)

interface:  ## Look up an interface (DEVICE=name NAME=...)
	$(NETTOOLS) interface $(DEVICE) $(NAME)

sr-policy:  ## Look up an SR-TE policy's candidate path/SID detail (DEVICE=name POLICY_ID=colour:endpoint)
	$(NETTOOLS) sr-policy $(DEVICE) $(POLICY_ID)

logging:  ## Show recent log lines (DEVICE=name COUNT=...)
	$(NETTOOLS) logging $(DEVICE) --count $(COUNT)

ping:  ## Ping from a device (DEVICE=name ADDRESS=...); active probe
	$(NETTOOLS) ping $(DEVICE) $(ADDRESS)

traceroute:  ## Traceroute from a device (DEVICE=name ADDRESS=...); active probe
	$(NETTOOLS) traceroute $(DEVICE) $(ADDRESS)

analyze:  ## Collect evidence and analyze with the selected LLM
	$(NETTOOLS) analyze $(DEVICE)

# Phase 6: cross-device correlation over the whole fabric's evidence + Phase 4
# health verdicts, instead of one device at a time.
analyze-fabric:  ## Analyze the whole fabric together (cross-device correlation)
	$(NETTOOLS) analyze --fabric

# Phase 6: bounded, read-only tool-calling agent loop. Anthropic only -- see
# CLAUDE.md, "Bounded agent loop (Phase 6)".
# Gated since B-488: `nettools agent` is the one command where a model
# chooses its own tools and writes its own answer, which is the opposite
# of what the rest of this tool claims. Set NETTOOLS_ENABLE_AGENT=1 to opt in.
agent:  ## Ask the bounded tool-calling agent a question (QUESTION=...; Anthropic only)
	$(NETTOOLS) agent "$(QUESTION)"

demo:  ## Run the narrated agent demo (or DEVICE=name)
	$(NETTOOLS) demo $(DEVICE)

diff:  ## Diff evidence against the last snapshot (or DEVICE=name)
	$(NETTOOLS) diff $(DEVICE)

# Recaptures both halves of the quiet-fabric pair. Review the git diff by eye
# before committing: fixtures are permanent once pushed.
capture:  ## Recapture test fixtures from the whole lab
	$(NETTOOLS) capture --all --label t0
	sleep 75
	$(NETTOOLS) capture --all --label t1

# Derives expected/ blocks from the committed t0 fixtures and prints the
# fabric anomaly report; pass ARGS=--live to derive from a live collection.
learn-topology:  ## Derive expected topology from fixtures and update inventory/lab.yaml
	$(NETTOOLS) learn-topology $(ARGS)

# Deterministic health verdicts (see CLAUDE.md, "Phase 4"). Exit codes:
# 0 ok/info, 1 warning, 2 critical.
health:  ## Evaluate health verdicts across the whole fabric (live)
	$(NETTOOLS) health --all

health-fixtures:  ## Evaluate health verdicts against the committed t0 fixtures
	$(NETTOOLS) health --all --from-fixtures

baseline-pin:  ## Pin a golden snapshot (or DEVICE=name)
	$(NETTOOLS) baseline pin $(DEVICE)

baseline-show:  ## Print a device's pinned golden snapshot (or DEVICE=name)
	$(NETTOOLS) baseline show $(DEVICE)

flaps:  ## Detect oscillating fields in snapshot history (or DEVICE=name)
	$(NETTOOLS) flaps $(DEVICE)

# Retention/prune (Phase 7): deletes timestamped snapshots outside the
# retention window (never the pinned golden snapshot), against whichever
# NETTOOLS_EVIDENCE_BACKEND selects.
evidence-prune:  ## Prune old snapshots (KEEP_DAYS=30 KEEP_COUNT=20 by default)
	$(NETTOOLS) evidence prune --keep-days $(KEEP_DAYS) --keep-count $(KEEP_COUNT)

# Phase 8: operational metrics (per-device collection outcomes/latency/retries,
# health verdict counts by severity). In-memory only unless
# NETTOOLS_METRICS_FILE is set -- see .env.example.
metrics:  ## Report operational metrics (JSON; ARGS=--format=prometheus for text exposition)
	$(NETTOOLS) metrics $(ARGS)

version:  ## Print the installed nettools version
	$(NETTOOLS) version

mcp:  ## Start the MCP server over stdio
	$(NETTOOLS_MCP)

inspect:  ## Smoke-test the MCP server (or DEVICE=name)
	$(NETTOOLS) inspect $(DEVICE)

docker-build:  ## Build the MCP server container image
	docker build -t ios-xr-nettools-mcp .

clean:  ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache build *.egg-info src/*.egg-info
	find . -path ./.venv -prune -o -name __pycache__ -type d -print0 | xargs -0 rm -rf

# --- OPS wave (B-477/B-480/B-476) -------------------------------------------
audit:  ## Deterministic fabric audit (exit 0 ok/info, 1 warning, 2 critical)
	$(NETTOOLS) audit

audit-fixtures:  ## The same audit against committed captures (no lab needed)
	$(NETTOOLS) audit --from-fixtures --label healthy

config-check:  ## Validate every NETTOOLS_*/provider env var; exit 1 on problems
	$(NETTOOLS) config check

route-event:  ## Route an event from stdin (pipe an Alertmanager JSON or syslog line in)
	$(NETTOOLS) route-event

# --- Investigation layer (MVP-0, B-485) -------------------------------------
# investigate and ledger existed on the CLI with no make wrapper until now
# (Wave 3 C5); added here, not spliced into the phase-ordered block above, to
# match how the OPS wave targets above were themselves appended rather than
# reordered in.
investigate:  ## Deterministically descend a flow's dependency stack and report the cause (DEVICE=name SUBJECT=address; ARGS=--flow bgp_session|interface|isis_adjacency|ldp_session, --from-fixtures, etc.)
	$(NETTOOLS) investigate $(DEVICE) $(SUBJECT) $(ARGS)

ledger-summary:  ## Diagnosis accuracy ledger: counts by outcome (unknown always shown)
	$(NETTOOLS) ledger summary

ledger-verdict:  ## Record a human verdict on one diagnosis (DIAGNOSIS_ID=id OUTCOME=confirmed_correct|incorrect|unknown; ARGS=--by NAME optional)
	$(NETTOOLS) ledger verdict $(DIAGNOSIS_ID) $(OUTCOME) $(ARGS)

