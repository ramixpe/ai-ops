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
make test                   # pytest -q  (373 tests, no network needed)
make lint                   # ruff check .
make help                   # full target list
```

Single test: `pytest tests/test_network_tools.py::test_collect_evidence_uses_one_ssh_session -q`

Runtime targets are thin wrappers over the `nettools` console script and all
hit live devices: `make facts|interfaces|bgp|lldp|isis|sr [DEVICE=RR1]`,
`make fabric-bgp`, `make analyze`, `make demo`, `make diff`, `make mcp`,
`make inspect`. `nettools <check> <DEVICE>` works directly too.
`make route|bgp-neighbor|interface|logging|ping|traceroute` (Phase 5,
validated parameterized templates) take an additional value
(`PREFIX`/`ADDRESS`/`NAME`/`COUNT`), e.g. `nettools route PE1 10.255.0.31`.

CI (`.github/workflows/ci.yml`) runs `ruff check .` then `pytest -q` on Python 3.11.

Requires `DEVICE_USERNAME` / `DEVICE_PASSWORD` (or `DEVICE_SSH_KEYFILE`) for
anything that touches a device; `cp .env.example .env` and see that file for
the full env surface (`LLM_PROVIDER`, `NETTOOLS_LOG`, `NETTOOLS_EVIDENCE_DIR`,
`NETTOOLS_INVENTORY`).

## Architecture

Two packages, two source roots — `src/agent_nettools` and a top-level
`mcp_server` (see `[tool.setuptools.packages.find] where = ["src", "."]`).

Data flows in one direction through five layers:

0. `platforms.py` — the per-platform allowlist and intent table, plus
   (Phase 5) `templates.py`'s validated, parameterized commands, re-exported
   so `platforms.py` stays the single place a reviewer looks to see
   everything that may ever be sent to a device. Depends on nothing;
   everything depends on it.
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
smuggle in a state-changing command, a shell metacharacter, or a non-read-only
verb. Since Phase 5 the checked verb set is
`templates.VERB_ALLOWLIST = {"show", "ping", "traceroute"}`, applied to both
`APPROVED_COMMANDS` and every template's format string — not just `"show "`.

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

### Deterministic health verdicts (Phase 4)

Every tool through Phase 3 reports `status: "success"` for pure transport
success -- a device whose every BGP peer is down still says `success`.
Nothing answered "is this device healthy?" before `health.py`, and at fleet
scale that question has to be answered by cheap deterministic rules *before*
any LLM sees the evidence, so the reasoning layer only looks at anomalies.

**The central design constraint is two independent rule classes**, kept in
separate tables in `health.py` (`ROLE_INVARIANT_RULES`, `BASELINE_RULES`, plus
one `META_RULES` entry) because a single class cannot cover this fabric
honestly:

- `inventory/lab.yaml`'s `expected:` blocks are *derived* from this fabric's
  own current state (`nettools learn-topology`, see the Phase 3 section
  above), and this fabric is partly broken -- the file literally records
  `PE2: isis_adjacencies: 0` and `PE4: isis_adjacencies: 0`. A rule that only
  compares observed counts against that baseline would call both devices
  healthy, because the baseline was measured *from* the brokenness it should
  be catching.
- **Role invariants** therefore encode what must be true of a router *given
  its role*, independent of anything the inventory records: "every router
  must have at least one IS-IS adjacency" (`isis_isolated`) does not care that
  the baseline says zero is expected, and fires on PE2/PE4 regardless.
- **Baseline rules** are the other half: they compare an observed count
  against the recorded `expected:` value (`isis_adjacency_count_drift`,
  `bgp_peer_count_drift`) to catch *drift* from the last known-derived state
  -- a change role invariants cannot see, since dropping from 4 BGP peers to 3
  violates no role rule.
- The one `suspicious_baseline` meta rule keeps the tool honest about its own
  baseline: when the recorded expectation is itself a value a role invariant
  would call unhealthy (`isis_adjacencies == 0`), that is flagged directly, so
  "matches the baseline" is never read as "is healthy" for a device whose
  baseline was learned from a broken fabric.

Rules are table-driven: `Rule(name, severity, fn)` tuples in the three tables
above; `fn` reads a shared `RuleContext` (one device's pre-extracted, already
parse-checked isis/bgp/interfaces/sr records) and returns finding fragments.
Adding a rule is one small function plus one line in a table, never a new
code path through `evaluate_device`.

**`unevaluated` is not decoration.** A rule must never read a missing or
failed intent as healthy: if an intent's parse did not reach
`parsers.PARSE_OK` (command errored, platform `unsupported`, or the parser
itself failed), every rule needing that intent is skipped for that device and
the intent name is listed in the verdict's `unevaluated` array instead of
silently contributing nothing to the finding list. Zero IS-IS records because
the command failed must never look the same as zero IS-IS records because the
device is actually isolated.

Severity is `ok < info < warning < critical`; a device's severity is the max
over its findings, a fabric's is the max over its devices.
`nettools health --all` maps fabric severity to an exit code (0 ok/info, 1
warning, 2 critical) so CI/cron can gate on it.

Two adjacent, smaller additions live in `network_tools.py` rather than
`health.py`, because they operate on saved snapshot history, not a single
evidence collection: a **golden (pinned) snapshot** per device
(`save_golden_snapshot`/`load_golden_snapshot`, stored as the fixed filename
`golden.json` so it is never picked up by -- or confused with -- the
lexicographically-sorted timestamped snapshot glob `load_latest_snapshot`
already used) lets `nettools diff --against golden` compare against a
deliberately chosen known-good state instead of merely "the last run"; and
**flap detection** (`detect_flaps`) reads a device's *entire* snapshot history
and reports `(intent, subject, field)` triples that oscillated at least
`min_transitions` times, because a peer that bounces up/down/up looks clean
in every single pairwise `diff_evidence` call -- each one only ever shows one
change, never the repeating pattern. Both reuse `parsers.record_key` and
`parsers.volatile_fields`, the same identity and noise rules diffing already
established.

### Validated, parameterized command templates (Phase 5)

Through Phase 4, `platforms.APPROVED_COMMANDS` can only express zero-argument
commands, so `show route <prefix>`, `show bgp neighbor <ip>`,
`show interfaces <name>`, `show logging last <n>`, `ping`, and `traceroute`
are all unreachable -- an agent that reads "peer 10.255.0.31 is Idle" cannot
then ask about that peer specifically. **This is the highest-risk change in
the project**: it is the only one that alters the shape of the safety
guarantee, from "an exact string is a member of a frozenset" to "a
caller-supplied value survives typed parsing".

`templates.py` holds `PLATFORM_TEMPLATES[platform][template_name] -> Template`,
structured platform-major like `PLATFORM_INTENTS`, and `platforms.py`
re-exports its public names so it stays the single place a reviewer looks to
see everything that may ever be sent to a device -- both the static allowlist
and every template.

**The security model is canonicalize by reconstruction, never pass-through.**
A caller-supplied value is never substituted into a command as text. Every
parameter is first parsed into a typed object --
`ipaddress.IPv4Address`/`ipaddress.IPv4Network` (via `IPv4AddressParam`/
`IPv4PrefixParam`), a range-checked `int` (`BoundedIntParam`), or a
regex-validated interface name over an anchored `[A-Za-z][A-Za-z0-9_./-]{0,62}`
charset (`InterfaceNameParam`) -- and the command is rendered from *that
object's own canonical string form* (`str(parsed)`), never from the original
text. This is why `"01.1.1.1"` can never reach a device: `ipaddress`
rejects ambiguous leading zeros outright, so there is no code path from
"weird but technically parseable" input to a rendered command. A
"validate-then-pass-through" design (regex-check the raw text, then
interpolate the text itself) was deliberately rejected: a regex broad enough
to accept every legitimate value is also broad enough to admit a lookalike
nobody anticipated.

Five layered, deliberately redundant defenses in `render_command()`:
1. Reject any non-ASCII codepoint first -- kills homoglyph and
   fullwidth-digit bypasses before anything else runs.
2. Reject control characters, whitespace, and an explicit forbidden set
   (`` | ; & > < ` $ { } \n \r \t \0 ``), even though the typed parsers below
   already exclude all of this -- defense in depth, and a far better error
   message.
3. A hard per-parameter length bound (`MAX_PARAM_LENGTH`), checked before any
   parser runs.
4. Re-validate the *assembled* command after rendering: no forbidden
   character, and it must fullmatch the template's own shape regex
   (`_shape_pattern`, placeholders widened to wildcards, everything else
   literal) -- this catches a badly written template, not just bad input.
5. The rendered command's first word must be in `VERB_ALLOWLIST =
   {"show", "ping", "traceroute"}`. Nothing else is ever rendered, by any
   template, on any platform.

`|` gets called out specifically: IOS-XR's CLI supports piping a `show`
command's output to `| file disk0:/...`, which *writes a file to the
device* -- a pipe reaching the device is a state change, not just an
information leak, so it must be structurally impossible, not merely
discouraged.

Every parameter is parsed in one pass *before* `str.format` is ever called
once, so a rejection always means nothing was rendered at all -- there is no
partially assembled command, even transiently, even with more than one
parameter (today's templates each take exactly one, but this invariant is
what protects a future multi-parameter template too;
`tests/test_templates.py::test_render_command_attempts_every_parameter_before_ever_assembling_a_command`
pins it against a synthetic two-parameter template built just for that test).

`network_tools.run_template()` mirrors `run_intent()`'s ordering invariant
exactly: platform resolves via `platform_for()` (no credentials), the
template is validated by reconstruction, and *only then* is
`get_device()`/credentials/a socket touched --
`test_run_template_refuses_a_bad_parameter_before_loading_credentials` pins
it with no credentials set at all, exactly like the static-allowlist test it
sits beside. Unknown template for a platform returns `status: "unsupported"`,
matching `run_intent`, not an error. `is_safe_rendered_command()` is a second,
template-agnostic gate re-checked immediately before a rendered command
reaches the transport layer -- the same "check right at the boundary
regardless of what the caller supposedly already filtered" pattern
`_run_approved_commands` uses for `is_approved()`.

`ping`/`traceroute` are marked `active_probe=True` on their `Template`: they
generate traffic (ICMP echoes / UDP-or-ICMP probes), unlike every `show`
template, even though they change no device state. Gated by
`NETTOOLS_ALLOW_ACTIVE_PROBES` (default enabled -- they are table stakes for
troubleshooting); set to `0`/`false`/`no`/`off` to refuse them with a
structured error instead of running. A `Template` also carries a suggested
`read_timeout`, since `ping`/`traceroute` legitimately run much longer than a
`show` command; `network_tools._netmiko_send_commands()` takes an optional
`read_timeout` for exactly this, unused (and behavior-identical to before
Phase 5) by every other caller.

`tests/test_template_security.py` is the adversarial suite: every template
and every one of its parameters is checked against a table of attack strings
(pipes, `;`/`&&`, newlines/CR, backtick/`$()` substitution, `${IFS}`,
null bytes, leading zeros, malformed/overlong octets, an empty string, a
10,000-character string, fullwidth-digit and Cyrillic-homoglyph spellings of
an IPv4 address, and a path-traversal string), plus `count`-specific bad
values (`0`, `501`, `-1`, `1.5`, `1e3`, `0x10`). It also asserts no command is
ever rendered for a rejected input, and property-tests that a rendered
command built from many valid inputs never contains a forbidden character.
`tests/test_safety.py` was widened, not weakened: the "read-only verb" check
now covers `VERB_ALLOWLIST` instead of a hardcoded `"show "`, and applies to
every template's format string, not just the static allowlist.

CLI: `nettools route|bgp-neighbor|interface|logging|ping|traceroute DEVICE
...`. MCP: `get_lab_route`/`get_lab_bgp_neighbor`/`get_lab_interface`/
`get_lab_logging`/`get_lab_ping`/`get_lab_traceroute`, named to keep the
`get_lab_*` prefix `test_mcp_readme_lists_exactly_the_exposed_tools` already
filters on, each with a docstring stating the accepted parameter form so an
MCP client can narrow iteratively.

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
