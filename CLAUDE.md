# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Read-only network inspection for a single-user Cisco IOS-XR lab (9 devices on
`172.20.250.0/24`, default device `PE1`). Netmiko for SSH, an optional LLM
reasoning layer, and an MCP server — all over the same narrow allowlist of
`show` commands.

## Commands

```bash
make setup                  # python -m venv .venv + pip install -e ".[dev,llm]"
source .venv/bin/activate
make test                   # pytest -q  (the full suite, about 3,200 tests; the exact count moves — CI is authoritative; no network, no credentials, no API key needed)
make lint                   # ruff check .
make help                   # full target list
```

Single test: `pytest tests/test_network_tools.py::test_collect_evidence_uses_one_ssh_session -q`

Runtime targets are thin wrappers over the `nettools` console script and all
hit live devices: `make facts|interfaces|bgp|lldp|isis|sr [DEVICE=RR1]`,
`make fabric-bgp`, `make analyze`, `make analyze-fabric`, `make agent
QUESTION="..."` (**opt-in since B-488**: set `NETTOOLS_ENABLE_AGENT=1`; it exits 2 with an explanation otherwise, and points at `investigate`), `make demo`, `make diff`, `make mcp`, `make inspect`.
`nettools <check> <DEVICE>` works directly too.
`nettools audit` (fabric consistency), `nettools route-event` (event → flow
routing), and `nettools config show|check` (settings validation) shipped in
the 2026-08-18 OPS wave — `make audit|route-event|config-check` wrap them.
`make route|bgp-neighbor|interface|sr-policy|logging|ping|traceroute` (Phase 5,
validated parameterized templates) take an additional value
(`PREFIX`/`ADDRESS`/`NAME`/`POLICY_ID`/`COUNT`), e.g. `nettools route PE1
10.255.0.31`. `sr-policy` (B-515) takes a `POLICY_ID` as `<colour>:<endpoint>`,
e.g. `nettools sr-policy PE1 20:10.255.0.13` -- the candidate-path/SID detail
`nettools sr`'s own `policy` field does not expose.
`nettools analyze --fabric` (Phase 6) correlates evidence + health verdicts
across every device instead of one at a time; `nettools agent "QUESTION"
[--device D] [--max-iterations N] [--time-budget SECONDS]` (Phase 6,
Anthropic only) runs a bounded, read-only tool-calling loop.
`nettools investigate DEVICE SUBJECT [--flow bgp_session|interface|
isis_adjacency|ldp_session] [--from-fixtures [--label LABEL]] [--no-model]`
(MVP-0, `--flow` set widened by B-107/B-109 -- `device_health`, B-108, was
investigated and refused: it is an aggregation over independent per-protocol
signals, not a dependency descent, so `nettools health DEVICE` is the entry
point for "is DEVICE ok", not a flow; see `flows.py`'s comment above `FLOWS`)
runs the deterministic
dependency descent and reports the lowest broken rung plus its causal chain;
`--from-fixtures` replays committed captures and needs no lab, credentials or
API key. Every run appends what it diagnosed to the diagnosis accuracy
ledger (`nettools ledger summary` / `nettools ledger verdict DIAGNOSIS_ID
confirmed_correct|incorrect|unknown [--by NAME]`, B-485 -- the tool records
what it diagnosed, only a human records whether it was right) and writes a
per-run ticket, an append-only Markdown flight recorder, to `NETTOOLS_TICKET_DIR`
(default `./tickets`; B-446). `nettools evidence prune --keep-days N --keep-count M [--device D]` and
`nettools evidence history [DEVICE]` (Phase 7) manage stored snapshots
against whichever backend `NETTOOLS_EVIDENCE_BACKEND` selects.
`nettools metrics [--format json|prometheus] [--quiet]` (Phase 8) reports
per-device collection/latency/retry metrics and health verdict counts;
`nettools version` prints the installed package version. Most other commands
now accept `--format json|table|summary` (default `json`) and `--quiet`; see
"Output formats and exit codes (Phase 8)" below.

CI (`.github/workflows/ci.yml`) runs `ruff check .` then `pytest -q` on a
**matrix of Python 3.11 and 3.12** — not one version, and **not whatever
`.venv` happens to be**. A local `pytest -q` is evidence about your
interpreter, not a prediction about CI; OBS-695 records a session that
reported green all night from a 3.13 venv while CI gated on 3.11/3.12.
**Resolved 2026-08-21 (B-697):** `make setup` now defaults to
`PYTHON ?= python3.12`, so the default venv is a version CI tests. A bare
`python3` was what produced the mismatch — it is whatever the host ships.
`tests/test_live_lab.py` (marker `live_lab`, registered in `pyproject.toml`) is
excluded from that by default -- every test in it self-skips unless
`NETTOOLS_LIVE_LAB=1` is set, so CI/`make test` never needs a reachable lab.

Requires `DEVICE_USERNAME` / `DEVICE_PASSWORD` (or `DEVICE_SSH_KEYFILE`) for
anything that touches a device; `cp .env.example .env` and see that file for
the full env surface (`LLM_PROVIDER`, `NETTOOLS_LOG`, `NETTOOLS_EVIDENCE_DIR`,
`NETTOOLS_INVENTORY`, `NETTOOLS_LLM_FALLBACKS`,
`NETTOOLS_EVIDENCE_PER_INTENT_CHARS`, `NETTOOLS_EVIDENCE_TOTAL_CHARS`,
`NETTOOLS_CONNECT_TIMEOUT_SECONDS`, `NETTOOLS_READ_TIMEOUT_SECONDS`,
`NETTOOLS_BANNER_TIMEOUT_SECONDS`, `NETTOOLS_COMMAND_RETRIES`,
`NETTOOLS_RETRY_BACKOFF_SECONDS`, `NETTOOLS_EVIDENCE_BACKEND`,
`NETTOOLS_LOG_MAX_BYTES`, `NETTOOLS_LOG_BACKUP_COUNT`,
`NETTOOLS_CREDENTIAL_PROVIDER`, `NETTOOLS_ACTOR`, `NETTOOLS_METRICS_FILE`,
`NETTOOLS_DIAGNOSIS_LEDGER_FILE`, `NETTOOLS_TICKET_DIR`).

## Architecture

**The full architecture reference is [docs/design/architecture.md](docs/design/architecture.md)** —
the layer stack, the intent table, and every phase's design notes. It was split
out of this file on 2026-08-17. What remains below is only what an agent working
in this repository **must not break**.

**Four invariants this layer inherits and must not break.** Platform resolves
credential-free; the allowlist is checked before credentials load; no command is
built by interpolation; and **no unparsed device text ever reaches a model** —
enforced structurally in `prompt_library`, which never holds the text, rather
than by filtering.

**Invariant 4 has two paths to a model, not one.** `prompt_library` is the
first. `mcp_server/server.py` is the second, and it went eight phases without
the guarantee because when those tools were written the only consumer was our
own code, which reads `data.parsed` and ignores `data.commands` — 14 of 20 tools
returned raw device output, up to 38 kB (OBS-111). `mcp_server/boundary.py`
closes it, applied by the *registration decorator* so a tool is sanitised by the
act of being registered. **An invariant that holds for every internal caller is
not an invariant; it is a convention that has not yet met a new consumer** — so
a new path to a model needs the guarantee built into it, not inherited.

**Two things that look like ordinary code and are not.** `descent.py` has no
model call, deliberately and permanently — if it ever needs one, something above
it has been designed wrong. And `grounding.py`'s failure objects have no field a
model's prose can occupy, so "a failed report is not emitted" cannot be defeated
by forgetting to redact.

**One precondition every flow must satisfy.** Every collect step in every rung
must be resolvable from the subject and the device alone, *before* the walk
begins — a rung may not collect something whose identity depends on what an
earlier rung concluded. Evidence is gathered once per device into a single
observation window, and a rung that could not be collected in that window would
silently fall back to being read at its own instant, which is the defect
`epoch.py` exists to remove. Stated on `flows.Flow`, enforced by
`epoch.validate_prewalk_collection`.

`build/lib/` and `agent_nettools.egg-info/` are stale build artifacts. Never edit
those copies; `make clean` removes them.

### The safety boundary (the central invariant)

`platforms.APPROVED_COMMANDS[platform]` is an exact-match frozenset, checked in
`_run_approved_commands` *against the device's own platform* and *before*
credentials are loaded or a connection is opened. So there is no injection path,
a bad command never reaches the network, and one vendor's syntax can never reach
another vendor's device. There is deliberately no `run_command(device, command)`,
no config mode, and no shell.

The ordering matters and is load-bearing: platform resolves via
`lab.platform_for()`, which reads static data only. **Never make platform
resolution require credentials** — that would silently move the allowlist check
after credential access. `test_refuses_unapproved_commands_before_loading_credentials`
pins it by running with no credentials set at all.

This still holds after Phase 3's declarative inventory: `platform_for()` parses
`inventory/lab.yaml` through `inventory_model.load_inventory_file()`, and that
module never imports `os.environ` for anything except locating the YAML file
itself (`NETTOOLS_INVENTORY`, a path, not a credential). Credentials are joined
in only by `inventory.load_inventory()`, which is a separate, later step. If a
future change to the inventory loader ever needs an environment variable to
*parse* the file (not just to find it), that variable is a credential leaking
into the credential-free layer — don't do it; keep resolving credentials in
`inventory.py` only, at `load_inventory()` time.

`tests/test_safety.py` iterates *every* platform, so adding a vendor cannot
smuggle in a state-changing command, a shell metacharacter, or a non-read-only
verb. Since Phase 5 the checked verb set is
`templates.VERB_ALLOWLIST = {"show", "ping", "traceroute"}`, applied to both
`APPROVED_COMMANDS` and every template's format string — not just `"show "`.

### Testing seams

Three mechanisms, all SSH-free — prefer them over mocking netmiko internals.
Shared helpers live in `tests/helpers.py` (not `test_*`, so pytest does not
collect it; test modules `from helpers import ...`).

- `sender=` — every check, `collect_evidence`, and `check_fabric` accept an
  optional `sender(device, command) -> str`, which short-circuits the transport
  entirely. Best for exercising tool logic and asserting exact commands.
- **Fixture replay** — `load_fixture_evidence(device, label=...)` in
  `fixtures.py` replays real captured output from `tests/fixtures/`. It is built
  *on* the `sender=` seam, so it reuses the real envelope construction, section
  slicing, and error attribution rather than a parallel implementation; a missing
  fixture file surfaces as the same structured error a failed command would. Use
  this for anything that needs realistic output: parsers, diff, health rules.
- A fake `netmiko` module installed with
  `monkeypatch.setitem(sys.modules, "netmiko", fake)` (`install_fake_netmiko` in
  `tests/helpers.py`, with `fail_commands=` and `fail_connect=`). Use this when
  the test cares about transport behavior — session count, connection params,
  per-command failures.

`tests/test_live_lab.py` (Phase 8) is the one deliberate exception to "all
SSH-free": it needs a real, reachable lab, is marked `live_lab`, and every
test in it self-skips unless `NETTOOLS_LIVE_LAB=1` is set — see the Phase 8
section above.

`tests/fixtures/<platform>/<device>/<label>/<command-slug>.txt` holds two
captures ~90s apart (`t0`, `t1`) from all nine devices. Refresh with
`nettools capture --all --label t0`. Fixtures are committed, so review the diff
by eye — `scrub_output` covers credential- and serial-shaped material, but the
current XRd output contains none, so the scrubber's only coverage is its unit
test.

### Doc-sync tests

`tests/test_docs.py` parses `README.md` and `mcp_server/README.md` by *literal
marker sentences* and asserts the backticked lists match the code exactly:

- **README, per platform.** For every platform in `known_platforms()`, the
  block from its own `### {platform}` heading to the next platform heading (or
  to the closing prose `Only \`cisco_xr\` is verified` for the last one) must
  backtick exactly `APPROVED_COMMANDS[platform]` — a separate assertion per
  platform, not one span covering all of them, so a platform added to the code
  without its own README section fails immediately (`test_readme_documents_
  every_known_platform` catches the missing heading itself).
- **MCP README.** Between `## Exposed Tools` and `There is no shell`, the
  backticked names must equal the MCP server's own tool **registry**, read at
  test time rather than compared against a list frozen in the test — a prefix
  allowlist here once let a newly-added tool go undocumented and the test still
  pass; reading the registry removes that failure mode rather than widening the
  list by hand each time.

Rewording those marker sentences, or adding backticked text inside those spans,
breaks the tests. Update code and docs in the same change.

### .env loading

Both entry points load `.env` themselves — `cli.main()` and `mcp_server/server.py`
at import time — using `load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()`
so it resolves from the cwd upward *or* next to an editable install. The MCP
server needs its own call because clients spawn it directly.

<!-- design-docs:begin -->

## Design documents

Read these before changing anything in the investigation layer.

| File | What it is |
|---|---|
| [docs/design/architecture.md](docs/design/architecture.md) | **How this repo is built** — the layer stack and every phase's design notes. Split out of this file 2026-08-17 |
| [docs/design/glossary.md](docs/design/glossary.md) | **Read first.** Pinned terminology — `intent` means a question name here, not intended state |
| [docs/design/design-thinking.md](docs/design/design-thinking.md) | Decisions D1-D20 with options considered, rationale, and growth path |
| [docs/design/lld-investigation-layer.md](docs/design/lld-investigation-layer.md) | Delta spec: what this repo is missing and where it goes |
| [docs/design/interfaces.md](docs/design/interfaces.md) | How humans interact with the agent, staged. T-035 (the outbound report relay) shipped; this is the interaction ladder for what comes after it |
| [docs/design/evidence-reduction.md](docs/design/evidence-reduction.md) | How large evidence sources are made model-readable **without a model reading them** |
| [docs/design/chaos-harness.md](docs/design/chaos-harness.md) | Fault injection, framed as the Stage 2 acceptance vehicle. §3.1's operating rule is binding |
| [docs/design/evidence-epoch.md](docs/design/evidence-epoch.md) | One observation window; why skew is not coherence (B-436) |
| [docs/design/next-level.md](docs/design/next-level.md) | Proposal: the tiers + the local model; ranked candidates |
| [docs/design/stage-2-architecture.md](docs/design/stage-2-architecture.md) | **The Stage 2 brainstorm** — MCP hub, the cache+syslog-invalidation layer, the wide/narrow flow-vs-tool dimension. Brainstorm and decision record, for an external eye |
| [docs/design/peer-review-response.md](docs/design/peer-review-response.md) | Three external reviews and what was accepted, corrected or deferred |
| [docs/build/PROCESS.md](docs/build/PROCESS.md) | **Binding.** The rules of engagement extracted from `BUILD-PLAN.md`'s Part 0 on 2026-08-20 — model roles, escalation ladder, frozen files, same section numbers (§0.1-§0.16) as before the move |
| [docs/archive/BUILD-PLAN.md](docs/archive/BUILD-PLAN.md) | The 34-task build plan (Parts 1-8), all long since executed. Archived 2026-08-20 alongside its Part 0's extraction into `PROCESS.md` above |
| [docs/archive/TRACKER.md](docs/archive/TRACKER.md) | Progress on that same 34-task plan. Authoritative on *its* task status, not on the project's current state — that is `BACKLOG.md`. Archived 2026-08-20 |
| [docs/build/FINDINGS.md](docs/build/FINDINGS.md) | Append-only findings log. **Never rewrite an entry** — corrections are appended |
| [docs/build/BACKLOG.md](docs/build/BACKLOG.md) | Every open item, with its reconciled state. `DEFERRED` carries its unblocking condition |
| [docs/archive/SESSION-HANDOVER.md](docs/archive/SESSION-HANDOVER.md) | A build-state snapshot from 2026-08-18/19. Archived 2026-08-20 — its own text already records Q-020 as answered ("clause 2 dropped"); for current state read `BACKLOG.md` |
| [docs/build/MCP-EXPERIMENT.md](docs/build/MCP-EXPERIMENT.md) | The 2026-08-17 MCP experiment: the invariant-4 audit, the refuted prediction, and argument fabrication (B-459) |
| [docs/build/MVP0-REVIEW.md](docs/build/MVP0-REVIEW.md) | **The M4 review.** What the build changed about the design, what is still unknown, and what MVP-0 can and cannot do |
| [docs/archive/VERIFICATION.md](docs/archive/VERIFICATION.md) | Every claim with its evidence — frozen-file hashes, guardrails by name, rounds by payload |
| [docs/archive/BACKLOG-STATUS.md](docs/archive/BACKLOG-STATUS.md) | All 98 backlog items by state, every DONE claim verified, 12 guardrails mutation-tested |
| [docs/build/HOLISTIC-REVIEW.md](docs/build/HOLISTIC-REVIEW.md) | **The 2026-08-18 five-perspective review.** Two security defects fixed (B-481/B-482), 18 UX fixes, the two-lens-convergence meta-finding (OBS-156) |
| [docs/archive/PEER-REVIEW-BRIEF.md](docs/archive/PEER-REVIEW-BRIEF.md) | **Give this to a reviewer.** What to read, what is already known wrong, where to attack |
| [docs/archive/OPERATOR-RUNBOOK.md](docs/archive/OPERATOR-RUNBOOK.md) | A lab-window checklist from 2026-08-19. Archived 2026-08-20; not all of it is done — e.g. B-440/round 6 is still `BLOCKED` in `BACKLOG.md`, which is authoritative on what remains |
| [docs/diagrams/design/](docs/diagrams/design/) | **Ten hand-authored presentation diagrams** of the whole stack — the tool, the lab platform beside it, and the fabric it reads. A *second layer*, added 2026-08-21: nicer to read, and able to go stale in a way the generated set cannot. **When the two disagree, the generated ones are right.** Start at `design/index.html` |
| [docs/diagrams/](docs/diagrams/) | **Nine SVGs of the system as built** — layer stack, one call end to end, the trust boundary, capabilities, current state, the descent, Stage 2, **the event-driven loop** (and where it stops), and **the model boundary** (what a model may and may not do, with each enforcement point named). Generated from the tree and byte-pinned; regenerate, never hand-edit. Two carry self-invalidating guards: `d8` refuses to regenerate if any Stage-3 write-path row stops being DEFERRED, `d9` if `reasoning_gate` gains a live caller |
| [docs/README.md](docs/README.md) | Map of the docs tree and reading order |

**Non-negotiable while the build plan is active:** `tests/test_safety.py` and
`tests/test_template_security.py` are frozen. `platforms.py` and `templates.py`
take additions only — never a relaxed validator. See `docs/build/PROCESS.md` §0.5.
<!-- design-docs:end -->
