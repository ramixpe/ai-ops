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
make test                   # pytest -q  (39 tests, no network needed)
make lint                   # ruff check .
make help                   # full target list
```

Single test: `pytest tests/test_network_tools.py::test_collect_evidence_uses_one_ssh_session -q`

Runtime targets are thin wrappers over the `nettools` console script and all
hit live devices: `make facts|interfaces|bgp|lldp|isis|sr [DEVICE=RR1]`,
`make fabric-bgp`, `make analyze`, `make demo`, `make diff`, `make mcp`,
`make inspect`. `nettools <check> <DEVICE>` works directly too.

CI (`.github/workflows/ci.yml`) runs `ruff check .` then `pytest -q` on Python 3.11.

Requires `DEVICE_USERNAME` / `DEVICE_PASSWORD` (or `DEVICE_SSH_KEYFILE`) for
anything that touches a device; `cp .env.example .env` and see that file for
the full env surface (`LLM_PROVIDER`, `NETTOOLS_LOG`, `NETTOOLS_EVIDENCE_DIR`).

## Architecture

Two packages, two source roots — `src/agent_nettools` and a top-level
`mcp_server` (see `[tool.setuptools.packages.find] where = ["src", "."]`).

Data flows in one direction through four layers:

1. `lab.py` — static `{device_name: management_ip}`, nothing else. No credentials
   ever live here.
2. `inventory.py` — joins the lab map with env credentials into device dicts.
   Raises `InventoryError` when required env is missing.
3. `network_tools.py` — the allowlist, SSH transport, evidence collection,
   snapshots, and diffing. All real logic lives here.
4. `cli.py` and `mcp_server/server.py` — two independent front ends that call
   the *same* functions from layer 3. Anything added to layer 3 should usually
   be surfaced in both.

`build/lib/` and `agent_nettools.egg-info/` are stale build artifacts. Never edit
those copies; `make clean` removes them.

### The safety boundary (the central invariant)

`APPROVED_COMMANDS` is an exact-match set, checked in `_run_approved_commands`
*before* credentials are loaded or a connection is opened — so there is no
injection path and a bad command never reaches the network. There is
deliberately no `run_command(device, command)`, no config mode, and no shell.
`tests/test_safety.py` asserts that banned verbs stay out of the allowlist and
that no generic executor appears in the MCP module's public surface.

Preserve this shape when extending. Adding a read-only command means:
`APPROVED_COMMANDS` → a section in `EVIDENCE_COMMANDS` → the README list
(the doc tests below will fail otherwise).

### Registries that generate behavior

- `CHECK_TOOLS` (name → check function) is the single source of truth for the
  per-device checks. `cli.py` generates one subcommand per entry, `check_fabric`
  validates its `check` argument against it, and its keys are the `fabric`
  subcommand's choices. Adding an entry adds a CLI subcommand and a fabric
  option for free — but *not* an MCP tool, which must be written by hand in
  `mcp_server/server.py`.
- `EVIDENCE_COMMANDS` (section → commands) drives `collect_evidence` and the
  failure accounting in `diff_evidence`.

### One SSH session per collection

`_netmiko_send_commands` opens a single `ConnectHandler` and runs every command
over it — IOS-XR rate-limits repeated logins and a full evidence collection is
seven commands. `collect_evidence` therefore runs all commands in one call and
then *slices* the combined result into per-section results via
`_section_from_combined`, rather than calling each check separately.
`check_fabric` parallelizes across devices with a thread pool but always emits
results in inventory order.

`netmiko` is imported lazily inside the function, not at module scope. That is
what lets unit tests run with no SSH and no netmiko behavior stubbing at import
time — don't hoist it.

### Uniform result envelope

Every tool returns `{tool, device, status, timestamp, data, errors}` with
`status` in `{"success", "error"}`. Failures are structured values, not
exceptions — broad `except Exception` with a `# noqa: BLE001` comment is the
established idiom at the SSH boundary. Errors are formatted as
`"<command>: <detail>"` for per-command failures and
`"connection to <host> failed: <detail>"` for session failures; `diff_evidence`
and `_section_from_combined` parse those prefixes, so keep the shapes.

### Diff semantics

`diff_evidence` separates *transient failure* from *real change*: a command
missing because its section errored lands in `failed` / `recovered`, never in
`added` / `removed`. Snapshots are timestamped JSON under
`NETTOOLS_EVIDENCE_DIR` (default `./evidence/<device>/`), and
`load_latest_snapshot` relies on the ISO timestamp filenames sorting
lexicographically.

**Known defect (Phase 2 fixes this).** `diff_evidence` compares whole command
output strings, and every IOS-XR `show` command prefixes its output with the
current timestamp. Measured against the committed t0/t1 fixture pair: all 63
command outputs across all 9 devices report `changed` on a quiet fabric — a 100%
false-positive rate with no true negatives. `show version` (uptime),
`show bgp summary` (`MsgRcvd`/`MsgSent`, `Up/Down`), `show isis neighbors`
(`Holdtime`), and the SR-TE policy `up for`/`down for` durations add their own
moving fields; `show interfaces brief`, `show lldp neighbors`, and
`show running-config hostname` differ *only* by the timestamp line. Note LLDP
`Hold-time` is the advertised TTL and is **stable** — do not treat it as
volatile. `test_quiet_fabric_pair_diffs_every_command_today` pins this and is
written to fail loudly when the fix lands.

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

`tests/fixtures/<platform>/<device>/<label>/<command-slug>.txt` holds two
captures ~90s apart (`t0`, `t1`) from all nine devices. Refresh with
`nettools capture --all --label t0`. Fixtures are committed, so review the diff
by eye — `scrub_output` covers credential- and serial-shaped material, but the
current XRd output contains none, so the scrubber's only coverage is its unit
test.

### Doc-sync tests

`tests/test_docs.py` parses `README.md` and `mcp_server/README.md` by *literal
marker sentences* and asserts the backticked lists match the code exactly:

- README: between `Approved read-only commands:` and `There is no configuration mode`
- MCP README: between `## Exposed Tools` and `There is no shell`

Rewording those sentences, or adding backticked text inside those spans, breaks
the tests. Update code and docs in the same change.

### LLM layer

`llm_analysis.py` supports Anthropic, OpenAI, and local keyless Ollama.
`get_provider()` raises `ValueError` on misconfiguration while the analyze
functions raise `LLMAnalysisError`; CLI callers must catch both (a past
regression). `auto` never selects Ollama — it must be requested explicitly.
Provider SDKs are imported lazily and live in the optional `llm` extra, so the
core package and the Docker image install without them.

### .env loading

Both entry points load `.env` themselves — `cli.main()` and `mcp_server/server.py`
at import time — using `load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()`
so it resolves from the cwd upward *or* next to an editable install. The MCP
server needs its own call because clients spawn it directly.
