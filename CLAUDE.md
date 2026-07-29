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
make test                   # pytest -q  (124 tests, no network needed)
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
the full env surface (`LLM_PROVIDER`, `NETTOOLS_LOG`, `NETTOOLS_EVIDENCE_DIR`,
`NETTOOLS_INVENTORY`).

## Architecture

Two packages, two source roots — `src/agent_nettools` and a top-level
`mcp_server` (see `[tool.setuptools.packages.find] where = ["src", "."]`).

Data flows in one direction through five layers:

0. `platforms.py` — the per-platform allowlist and intent table. Depends on
   nothing; everything depends on it.
1. `inventory_model.py` — the declarative inventory's schema (pydantic v2,
   `extra="forbid"` everywhere) and YAML loader for `inventory/lab.yaml`.
   Reads nothing from the environment; depends only on `platforms.py` (for
   `known_platforms()`).
2. `lab.py` — the credential-free read path onto that inventory:
   `all_devices()`, `DEVICES`, and `platform_for()`. No credentials ever live
   here, which is what lets platform be resolved before the allowlist check.
   `PLATFORMS` is a per-device override dict consulted *before* the YAML
   (tests use it to simulate other vendors without a second inventory file).
3. `inventory.py` — joins the parsed inventory with env credentials (resolved
   per device through its `credential_group`) into device dicts. Raises
   `InventoryError` when required env is missing or the inventory is invalid.
4. `network_tools.py` — the allowlist, SSH transport, evidence collection,
   snapshots, and diffing. All real logic lives here.
5. `cli.py` and `mcp_server/server.py` — two independent front ends that call
   the *same* functions from layer 4. Anything added to layer 4 should usually
   be surfaced in both.

`topology.py` (derived expected topology counts + the fabric anomaly report)
and `devices_doc.py` (renders `docs/devices.md`) sit beside layer 4/5: they
read parsed evidence and the inventory, but nothing depends on them.

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
smuggle in a state-changing command, a shell metacharacter, or a non-`show` verb.

### Intents: the multi-vendor keystone

`platforms.py` is the single source of truth, structured **platform-major** so
everything sendable to one vendor is reviewable in one block:

```
PLATFORM_INTENTS[platform][intent] -> tuple of commands
```

An *intent* is a vendor-neutral name for a question (`bgp`, `isis`). The same
intent resolves to different syntax per vendor — `show bgp summary` on IOS-XR,
`show ip bgp summary` on IOS-XE, `show isis adjacency` on Junos. There is exactly
**one** intent vocabulary, shared by CLI subcommands, `CHECK_TOOLS`, the fabric
runner, and the evidence section keys. (Before Phase 1 there were two: `sr` as a
check name and `sr_policies` as an evidence key.)

Adding a read-only command means: `PLATFORM_INTENTS` → the per-platform README
block (the doc tests parse `### <platform>` headings) → nothing else. The
allowlist derives itself.

**`status: "unsupported"`** is a third status alongside `success`/`error`, for a
platform that has no command for an intent (Junos has no SR-TE policy output).
It is not a failure: `check_fabric` stays green and lists such devices under
`data.unsupported`, and `diff_evidence` treats those commands as neither
`removed` nor `failed`. Evidence always carries one section per *known* intent
regardless of platform, so the shape is identical across a mixed fabric.

Only `cisco_xr` is verified against a live device. `cisco_iosxe` and
`juniper_junos` are declared from vendor docs with no device to test against —
their command strings are unconfirmed, and `juniper_junos` exists mainly to keep
the abstraction honest (it shares no command words with IOS-XR).

### Registries that generate behavior

- `CHECK_TOOLS` (intent → check function) is the single source of truth for the
  per-device checks. `cli.py` generates one subcommand per entry, `check_fabric`
  validates its `check` argument against it, and its keys are the `fabric`
  subcommand's choices. Adding an entry adds a CLI subcommand and a fabric
  option for free — but *not* an MCP tool, which must be written by hand in
  `mcp_server/server.py`, and *not* commands, which must exist in
  `PLATFORM_INTENTS`.
- `run_intent(device, intent)` is the single entry point all six checks delegate
  to; it resolves platform, handles unsupported, and calls
  `_run_approved_commands`.

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

### Parsing and diff semantics (Phase 2)

`parsers.py` turns raw command output into structured records for `cisco_xr`
(`parse_intent(platform, intent, outputs) -> (parsed, status)`, `status` one of
`PARSE_OK` / `PARSE_UNAVAILABLE` / `PARSE_FAILED`). It is deliberately strict: a
parse yielding neither records nor meta from non-empty input is a **failure**,
never a silent empty success — the ntc-templates library was measured and
rejected for exactly this failure mode on `show interfaces brief` (see the
module docstring). A parser exception never propagates; `parse_intent` guards
every call. `collect_evidence` and `run_intent` attach `data.parsed` and
`data.parse_status` to every intent's section, independent of transport
`status` — an `error` section still gets a parse attempt over whatever output
exists (usually none), and an `unsupported` section always gets
`PARSE_UNAVAILABLE` with `parsed: None`.

`diff_evidence` compares at the **intent** level, not per-command, and prefers
parsed data over text: when both snapshots have `PARSE_OK` for an intent, rows
are matched by `parsers.record_key` and compared excluding
`parsers.volatile_fields`; otherwise it falls back to
`normalize.normalize_output` (preamble-stripped, volatile-masked text). It
separates *transient failure* from *real change*: an intent missing because its
section errored lands in `failed` / `recovered`, never in `added` / `removed`,
and an `unsupported` intent is its own bucket — never failed, removed, or
changed. `details[intent]` carries `added_records` / `removed_records` /
`changed_records` / `changed_meta` / `compared_via` for every intent actually
compared. Snapshots are timestamped JSON under `NETTOOLS_EVIDENCE_DIR` (default
`./evidence/<device>/`), and `load_latest_snapshot` relies on the ISO timestamp
filenames sorting lexicographically.

**Fixed defect, pinned by test.** Comparing whole command output strings used
to be useless: every IOS-XR `show` command prefixes its output with the current
timestamp, so — measured against the committed t0/t1 fixture pair — all 63
command outputs across all 9 devices reported `changed` on a quiet fabric, a
100% false-positive rate with no true negatives. `show version` (uptime),
`show bgp summary` (`MsgRcvd`/`MsgSent`, `Up/Down`), `show isis neighbors`
(`Holdtime`), and the SR-TE policy `up for`/`down for` durations add their own
moving fields on top of the timestamp. Note LLDP `Hold-time` is the advertised
TTL and is **stable** — it is not excluded from comparison.
`test_quiet_fabric_pair_reports_no_change` (parametrized over all 9 devices) now
pins the fix: `changed` is empty and every parseable intent lands in
`unchanged`.

### Declarative inventory and topology (Phase 3)

`inventory/lab.yaml` replaces the hardcoded device map. Schema (pydantic v2,
`inventory_model.py`): `version`, `defaults` (platform/credential_group/port),
`credential_groups` (each names environment variables, never holds a value),
and `devices` (name, mgmt_ip, role, site, optional platform/router_id/local_as/
tags/expected). Every model uses `extra="forbid"`, so a typo'd key is a load
failure, not a silently ignored no-op — load failures raise `InventoryError`
with a message naming the file and, wherever derivable, the offending
device/field. `router_id`/`local_as` are **absent**, not zero, for the four
devices with no BGP process (P1–P4, PE4) — the fixtures literally answer `%
BGP instance 'default' not active` and asserting a fake router-id would be a
lie the fixtures themselves contradict.

`expected:` blocks (`isis_adjacencies`, `bgp_peers` per device) are *derived*,
never hand-typed: `nettools learn-topology [--from-fixtures|--live]` counts
parsed IS-IS/BGP neighbor records per device and writes them back via
`topology.update_expected_in_yaml`. `bgp_peers` is omitted (not `0`) for a
device with no active BGP process, matching the `router_id`/`local_as` rule
above. Only per-device *counts* are ever written — never link-level ("A
connects to B") topology, because this fabric's own LLDP data is
self-contradictory: P1 reports its Gi0/0/0/0 facing P2's Gi0/0/0/0, while P2
reports that same port facing `LEAF05_DHCP_SERVER` instead. Asserting a
specific link would silently pick a side of a real disagreement; a count
survives it. `learn-topology` always exits `0` but prints an anomaly report
covering exactly this fabric's three verified anomaly classes — LLDP links
where the two ends disagree, LLDP neighbors that are not in this inventory at
all (`Lab-leaf01`, `LEAF05_DHCP_SERVER`, `SDWAN-Edge01`), and devices with zero
adjacencies (PE2: 0 LLDP/0 IS-IS despite having an active BGP peer toward RR1;
PE4: an LLDP neighbor but 0 IS-IS adjacencies) — so the report, not a clean
inventory file, is where those specifics live.

`docs/devices.md` is generated by `devices_doc.render_devices_doc()` from the
same inventory; `test_devices_doc_matches_rendered_inventory` fails if the
committed file and the renderer's output ever diverge.

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
