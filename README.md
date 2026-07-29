# IOS-XR Read-Only Network Tools

Safe, read-only network automation for a single-user Cisco IOS-XR lab, driven by
Python, Netmiko, an LLM reasoning layer, and MCP.

> Good agents are built on boring tools that work.

## What This Does

- Loads a declarative inventory of devices from `inventory/lab.yaml`.
- Takes device credentials from the environment, named indirectly through the
  inventory's `credential_groups` (`DEVICE_USERNAME`, `DEVICE_PASSWORD` for this lab).
- Runs only a small allowlist of approved read-only `show` commands.
- Returns structured evidence and, optionally, grounded LLM analysis.
- Derives expected per-device topology counts from evidence, and reports where
  the fabric's own data disagrees with itself, rather than assuming a clean
  topology (`nettools learn-topology`).
- Evaluates deterministic health verdicts (`nettools health`) from role
  invariants and baseline drift, so an LLM never has to look at a healthy
  device -- only anomalies.
- Exposes the same narrow tools through MCP, with no shell or config access --
  including snapshot diffing, health verdicts, and flap detection (Phase 8),
  plus read-only-hinted tools, inventory/topology resources, and a
  troubleshooting prompt (see `mcp_server/README.md`).
- Reports operational metrics (`nettools metrics`) and supports `--format
  table|summary`/`--quiet` and consistent exit codes on top of the default
  JSON output (Phase 8).

## Environment

Two variables must be set (locally or in an ignored `.env`):

```text
DEVICE_USERNAME
DEVICE_PASSWORD
```

There is no per-user selection. The whole IOS-XR inventory is
available. `PE1` is the default device when none is given.

## Inventory

Devices, roles, credential groups, and (derived) expected topology counts live
in [`inventory/lab.yaml`](inventory/lab.yaml), validated by pydantic models in
`agent_nettools.inventory_model`. It is topology, not secrets: credentials are
never stored in the file, only the *names* of the environment variables that
supply them (`credential_groups.<name>.username_env` etc.) -- resolved from
the environment at `inventory.load_inventory()` call time.

Management network `172.20.250.0/24`:

| Name | Management IP |
|------|---------------|
| P1   | 172.20.250.11 |
| P2   | 172.20.250.12 |
| P3   | 172.20.250.13 |
| P4   | 172.20.250.14 |
| PE1  | 172.20.250.21 |
| PE2  | 172.20.250.22 |
| PE3  | 172.20.250.23 |
| PE4  | 172.20.250.24 |
| RR1  | 172.20.250.31 |

The full device table (with roles) lives in [docs/devices.md](docs/devices.md),
generated from the inventory by `agent_nettools.devices_doc.render_devices_doc`
-- `tests/test_docs.py` pins that it cannot drift. The linux CE nodes are not
IOS-XR and are intentionally excluded.

The inventory file location follows the same env-then-default pattern as
snapshots and fixtures: the `NETTOOLS_INVENTORY` environment variable, else
`inventory/lab.yaml` relative to the current directory, else a path resolved
from the installed package. `lab.platform_for()` reads this file directly and
never touches an environment variable -- that credential-free read is what
lets the command allowlist be checked before any credential access (see
CLAUDE.md, "The safety boundary").

### Expected topology (derived, not invented)

```bash
nettools learn-topology --from-fixtures   # or --live
```

reads parsed evidence (per device: IS-IS neighbor count, BGP neighbor count)
and writes `expected: {isis_adjacencies, bgp_peers}` blocks back into the
inventory. `bgp_peers` is written only for a device with an active BGP
process; a device with none (like this lab's P-routers) gets no `bgp_peers`
key at all, never `0`.

This lab's fabric is genuinely inconsistent -- verified against real captured
output, not hypothetical -- so `learn-topology` also prints an anomaly report
covering LLDP links where the two ends disagree, LLDP neighbors that are not
devices in this inventory, and devices with zero adjacencies. It always exits
`0`; the report is how those specifics stay visible instead of being smoothed
into a clean-looking inventory. Only per-device *counts* are ever written back
-- never a specific link claim, because the LLDP data cannot support one
truthfully (see the comment in `inventory/lab.yaml`).

## Quick Start

```bash
make setup
source .venv/bin/activate
make test
make inventory
make facts
```

`make inventory` lists the nine devices without showing credentials.
`make facts` contacts PE1 using only `show running-config hostname` and
`show version`.

## Safety Boundary

Commands are declared per platform, so a check runs across a mixed fabric without
one vendor's syntax ever reaching another's device. The allowlist is exact-match
and is verified before credentials are loaded or a socket is opened.

Approved read-only commands, by platform:

### cisco_xr

- `show running-config hostname`
- `show version`
- `show interfaces brief`
- `show bgp summary`
- `show lldp neighbors`
- `show isis neighbors`
- `show segment-routing traffic-eng policy`

### cisco_iosxe

- `show version`
- `show ip interface brief`
- `show ip bgp summary`
- `show lldp neighbors`
- `show isis neighbors`

### juniper_junos

- `show version`
- `show interfaces terse`
- `show bgp summary`
- `show lldp neighbors`
- `show isis adjacency`

Only `cisco_xr` is verified against a live device and captured in
`tests/fixtures/`. The other two are declared from vendor documentation and have
no device to verify against yet.

There is no configuration mode, reload, commit, rollback, shell access, or a
generic `run_command(device, command)` tool.

### Parameterized commands (Phase 5)

The allowlist above is exact-match, so it can only express zero-argument
commands. A second, narrower allowlist in `agent_nettools.templates` (see
`PLATFORM_TEMPLATES[platform][template_name]`) adds validated, parameterized
commands that *do* take one caller-supplied value: `show route <prefix>`,
`show bgp neighbor <ip>`, `show interfaces <name>`, `show logging last <n>`,
`ping <ip>`, and `traceroute <ip>` -- exactly the follow-up questions an
operator (or an agent that just saw "peer 10.255.0.31 is Idle") needs to ask
next.

**Canonicalize by reconstruction, never pass-through.** A caller-supplied
value is never substituted into a command as text. It is first parsed into a
typed Python object -- `ipaddress.IPv4Address`, `ipaddress.IPv4Network`, a
range-checked `int`, or a regex-validated interface name -- and the command
is rendered from *that object's own canonical string form*, never from the
original text. `ipaddress.IPv4Address("01.1.1.1")` simply raises (leading
zeros are ambiguous octal/decimal and CPython rejects them), so there is no
representation of that string that could ever reach the rendered command.
Five layered, deliberately redundant defenses apply to every parameter:
reject non-ASCII outright; reject control characters, whitespace, and an
explicit forbidden set (`` | ; & > < ` $ { } \n \r \t \0 ``); enforce a hard
length bound; re-validate the fully assembled command afterward (no
forbidden character, and it must match the template's expected shape); and
require the rendered command's first word to be in the explicit verb set
`{"show", "ping", "traceroute"}` -- nothing else may ever be rendered.
`|` gets special attention: IOS-XR supports piping a `show` command's output
to `| file disk0:/...`, which *writes a file to the device* -- a pipe
reaching the device is a state change, not just an information leak, so it
must be structurally impossible.

`ping` and `traceroute` are *active probes*: they generate traffic (ICMP
echoes / UDP-or-ICMP probes) even though they change no device state, unlike
every other command in this tool. They are gated by
`NETTOOLS_ALLOW_ACTIVE_PROBES` (default enabled, since they are table stakes
for troubleshooting); set it to `0`/`false`/`no`/`off` to disable them.

```bash
nettools route PE1 10.255.0.31
nettools bgp-neighbor PE1 10.255.0.31
nettools interface PE1 GigabitEthernet0/0/0/1
nettools logging PE1 --count 20
nettools ping PE1 10.255.0.31
nettools traceroute PE1 10.255.0.31
```

Only `cisco_xr`'s templates are verified against the live lab; `cisco_iosxe`
has `route`/`bgp_neighbor` declared from vendor documentation, unverified,
kept for the same reason `cisco_iosxe`'s static commands are: to prove the
template abstraction holds across a vendor with different syntax
(`show ip route`/`show ip bgp neighbors`).

## Common Commands

Everything is driven by the `nettools` CLI; the Makefile targets are thin
wrappers. Run `make help` for the full list.

```bash
make help        # Show the command menu
make test        # Run all tests
make lint        # Run Ruff
make inventory   # List devices without credentials
make facts       # hostname + version on PE1
make interfaces  # interface status on PE1
make bgp         # BGP summary on PE1
make lldp        # LLDP neighbors on PE1
make isis        # IS-IS neighbors on PE1
make sr          # SR-TE policies on PE1
make fabric-bgp  # BGP summary across the whole inventory
make route       # Look up a route (or DEVICE=name PREFIX=10.0.0.0/24)
make bgp-neighbor  # Look up a BGP neighbor (or DEVICE=name ADDRESS=...)
make interface   # Look up an interface (or DEVICE=name NAME=...)
make logging     # Show recent log lines (or DEVICE=name COUNT=...)
make ping        # Ping from a device (or DEVICE=name ADDRESS=...); active probe
make traceroute  # Traceroute from a device (or DEVICE=name ADDRESS=...); active probe
make analyze     # Collect evidence and analyze with the selected LLM
make analyze-fabric  # Analyze the whole fabric together (cross-device correlation)
make agent       # Ask the bounded tool-calling agent a question (QUESTION=...; Anthropic only)
make demo        # Narrated agent demo
make diff        # Diff evidence against the last snapshot
make capture     # Recapture test fixtures from the whole lab
make learn-topology  # Derive expected topology + print the fabric anomaly report
make health      # Evaluate deterministic health verdicts across the whole fabric
make baseline-pin   # Pin a golden snapshot (or DEVICE=name)
make baseline-show  # Print a device's pinned golden snapshot (or DEVICE=name)
make flaps       # Detect oscillating fields in a device's snapshot history
make evidence-prune  # Prune old snapshots (KEEP_DAYS=... KEEP_COUNT=...)
make metrics     # Report operational metrics (ARGS=--format=prometheus for text exposition)
make version     # Print the installed nettools version
make mcp         # Start the MCP server over stdio
make inspect     # Smoke-test the MCP server
```

Target another device:

```bash
make facts DEVICE=RR1
# or directly:
nettools facts RR1
```

## Health Verdicts

`status: "success"` everywhere else in this tool means only that the SSH
session and its commands succeeded -- a device whose every BGP peer is down
still reports `success`. `nettools health` answers the actual question, "is
this device healthy?", with deterministic rules instead of an LLM, so an LLM
(or a human) only ever has to look at anomalies, not the whole fleet.

```bash
nettools health --all                      # live collection, every device
nettools health PE1 PE2                    # live collection, specific devices
nettools health --all --from-fixtures      # against the committed t0 fixtures
nettools health --all --min-severity warning   # only print devices at/above warning
```

Rules come in two independent kinds (see `agent_nettools.health` and
CLAUDE.md for the full rationale):

- **Role invariants** -- what must be true of a router *given its role*,
  independent of the inventory's recorded baseline. These catch brokenness
  the baseline would otherwise bless: this lab's `expected: {isis_adjacencies:
  0}` for PE2/PE4 records a fact about a broken fabric, not a healthy target,
  so a role invariant (`isis_isolated`) flags zero adjacencies regardless of
  what the baseline says.
- **Baseline rules** -- drift between an observed count and the inventory's
  derived `expected:` value (`nettools learn-topology`). These catch a fabric
  that changed from its last known-derived state.
- One meta rule, `suspicious_baseline`, flags a recorded baseline that a role
  invariant would itself call unhealthy, so "matches the baseline" is never
  mistaken for "is healthy".

A finding's `severity` is one of `ok < info < warning < critical`; a device's
severity is the max over its findings, and the fabric's is the max over its
devices. An intent that errored, was `unsupported`, or failed to parse is
never read as healthy -- it is listed in that device's `unevaluated` list
instead of silently producing an "ok" finding.

**Exit codes** (for CI/cron gating): `0` when the fabric is ok/info, `1` when
the worst device is `warning`, `2` when the worst device is `critical`.

## Output Formats and Exit Codes (Phase 8)

Every command that prints a structured result accepts `--format
json|table|summary` (default `json`, so nothing that already parses this
tool's output breaks) and `--quiet`/`-q` (suppress all output; only the exit
code carries the outcome):

```bash
nettools health --all --format table     # one row per device
nettools health --all --format summary   # one line: fabric severity + counts
nettools fabric bgp --format table
nettools inventory --quiet; echo $?      # exit code only, no output
```

**A table or summary view never invents or softens data.** The exit code is
always computed from the full, unfiltered result before rendering -- a
critical fabric still exits non-zero under `--format summary` exactly as it
would under `--format json`; the format only changes how many columns of the
same facts get printed (see `agent_nettools.output`).

Exit codes follow one scheme project-wide, the same shape `nettools health`
already used:

- `0` -- success; nothing actionable.
- `1` -- the command ran but reports a problem: a tool/fabric result with
  `status: "error"`, a health verdict at `warning`, or (`diff`) real drift
  was found.
- `2` -- the command could not run at all, or reports the worst possible
  outcome: a missing/invalid inventory or LLM configuration, a health
  verdict at `critical`, or (`diff`) an intent failed to collect on either
  side, so the comparison itself is not trustworthy.

`nettools diff` follows the Unix `diff --exit-code` convention on top of
that: `0` no differences, `1` differences found, `2` could not fully compare.
Run `nettools <command> --help` for the exact codes a given command uses.

## Golden Baseline and Drift

Snapshots (`nettools diff`) normally compare against the most recently saved
one. A pinned "golden" snapshot is a separate, single, known-good reference
per device that does not get overwritten by routine `diff`/`analyze --save`
runs:

```bash
nettools baseline pin PE1                # collect fresh evidence now, pin it
nettools baseline pin PE1 --from-latest  # pin the most recently saved snapshot instead
nettools baseline show PE1               # print the pinned snapshot
nettools diff PE1 --against golden       # diff against the pin instead of "latest"
nettools diff PE1                        # unchanged: diffs against "latest" by default
```

The golden snapshot is stored as `golden.json` in the device's evidence
directory -- a fixed filename, deliberately not timestamp-shaped, so it can
never be picked up by (or confused with) the timestamped snapshot history.

## Flap Detection

A peer that bounced up and down several times between collections can look
clean in every single pairwise `nettools diff` -- each one only ever shows
one change, never the repeating pattern. `nettools flaps` reads a device's
*entire* saved snapshot history instead and reports every `(intent, subject,
field)` whose value oscillated at least `--min-transitions` times (default
3):

```bash
nettools flaps PE1
nettools flaps PE1 --min-transitions 2
```

History is built from parsed records only, keyed by `parsers.record_key` and
excluding `parsers.volatile_fields` -- the same identity and noise rules
`diff_evidence` already uses, so a field that legitimately changes every
collection (e.g. BGP `Up/Down`) is never reported as flapping.

## Scaling to a Larger Fabric (Phase 7)

`check_fabric`/`nettools fabric` resolve every device once (an O(1) name
lookup, not a linear scan) and thread that record down to each per-device
check, so a whole-fabric check scales linearly in the device count instead of
quadratically. `iter_fabric()` is the streaming twin of `check_fabric()`:
it yields `(device_name, result)` as each device finishes instead of waiting
for -- and holding in memory -- every device's full result at once, for
callers (large fleets, verdict-only consumers) that cannot afford to hold the
whole fabric's evidence at the same time.

Connection and read timeouts, and bounded retries for transient transport
failures (never for a command the allowlist refused), are configurable:

```text
NETTOOLS_CONNECT_TIMEOUT_SECONDS=10   # TCP/SSH connect timeout
NETTOOLS_READ_TIMEOUT_SECONDS=10      # per-command read timeout (a template's own
                                       # read_timeout, e.g. ping/traceroute, still wins)
NETTOOLS_BANNER_TIMEOUT_SECONDS=15    # SSH banner timeout
NETTOOLS_COMMAND_RETRIES=2            # total attempts per connection/command (>=1)
NETTOOLS_RETRY_BACKOFF_SECONDS=0.5    # exponential backoff base between attempts
```

A retry that actually happened is never silent: it shows up both in the
`NETTOOLS_LOG` audit record and, for the command(s) that needed one, under
`data.retries` in the returned result.

### Evidence storage: files or SQLite

`evidence/<device>/*.json` (unbounded, unqueryable) is still the default, but
`NETTOOLS_EVIDENCE_BACKEND=sqlite` switches every snapshot read/write to a
single `evidence.db` (stdlib `sqlite3`, no new dependency) indexed on
`(device, timestamp)`, so "this device's history" and "the latest snapshot"
are indexed queries instead of a directory walk. `diff_evidence` is
unaffected either way -- both backends hand back the same evidence dict.

```bash
NETTOOLS_EVIDENCE_BACKEND=sqlite nettools diff PE1
nettools evidence history PE1
nettools evidence prune --keep-days 30 --keep-count 20
nettools evidence prune --keep-count 5 --device PE1
```

A snapshot survives pruning if it satisfies *either* configured rule (among
the most recent `--keep-count`, or younger than `--keep-days`); the pinned
golden snapshot is never pruned. Neither flag given is a no-op, not "prune
everything".

### Audit log rotation

`NETTOOLS_LOG`'s JSONL file now rotates by size instead of growing forever:

```text
NETTOOLS_LOG_MAX_BYTES=10485760   # rotate once the live file reaches this size (10 MiB)
NETTOOLS_LOG_BACKUP_COUNT=5       # bounded number of rotated files kept (app.jsonl.1 .. .5)
```

Logging is still best-effort: a rotation or write failure is swallowed, never
raised, exactly like the un-rotated logger before it -- a bad `NETTOOLS_LOG`
path must never turn a successful check into a reported failure.

### Audit actor: provenance, not authorization

Every `NETTOOLS_LOG` record now also carries an `actor` field: `NETTOOLS_ACTOR`
if set, else the OS login user (`getpass.getuser()`), else `"unknown"`. This
is attribution for a trusted single-operator deployment -- it answers "who
ran this" for someone reviewing the audit log after the fact. **It is not an
authorization check and never gates anything**: the allowlist in
`_run_approved_commands` is the only enforcement this project has, and it
runs regardless of what (or whether) `NETTOOLS_ACTOR` is set. Real RBAC would
need an identity provider this project does not have -- something a caller
cannot simply set an environment variable to become (a verified SSO/OIDC
token, a signed client certificate) -- checked *before* any command runs.

## Pluggable Credential Resolution (Phase 8)

Device credentials resolve through a small, pluggable interface
(`agent_nettools.credential_resolver`) instead of being read from the
environment inline. Two working providers, selected by
`NETTOOLS_CREDENTIAL_PROVIDER` (env-then-default, default `env`):

- **`env`** (default, and this project's only behavior through Phase 7): a
  credential group's `username_env`/`password_env`/`ssh_keyfile_env` name
  environment variables whose *value* is the secret itself.
- **`file`**: the same named environment variables, but their value is a
  *file path* instead -- the Docker/Kubernetes secrets convention (one
  secret mounted per file, e.g. under `/run/secrets/`) -- and the secret is
  that file's content.

```bash
NETTOOLS_CREDENTIAL_PROVIDER=file
DEVICE_USERNAME=/run/secrets/device_username
DEVICE_PASSWORD=/run/secrets/device_password
```

**This does not ship a Vault/AWS Secrets Manager/cloud-secret-manager
backend, on purpose.** There is no such service available in this lab to
test against, and an untested credential path is worse than an honest gap. A
real backend would subclass `CredentialResolver` and implement
`_read_required`/`_read_optional` (fetch-by-name against whatever the
backend actually is, mapping any failure to `InventoryError`, never leaking
a raw secret through logs or an exception message), then register itself
under a new provider name -- see the module docstring in
`credential_resolver.py` for the full contract.

**The safety invariant is unaffected.** Platform resolution and the
allowlist check (`lab.platform_for()` / `_run_approved_commands`) still need
zero credential access regardless of which provider is configured --
`test_refuses_unapproved_commands_before_loading_credentials` and
`test_refuses_another_platforms_command_without_credentials` run with an
empty environment and are unmodified by this feature.

## Operational Metrics (Phase 8)

`nettools metrics` reports per-device collection outcomes (success/failure
counts, total and average latency, retries consumed) and health verdict
counts by severity, aggregated from whatever real device activity has
happened in the current process:

```bash
nettools metrics                     # JSON (default)
nettools metrics --format prometheus # Prometheus text exposition format
```

Metrics are in-memory only unless `NETTOOLS_METRICS_FILE` is set, in which
case they persist across separate `nettools` invocations too (each CLI
command is its own process, so without this, `nettools metrics` run on its
own only ever reports zero -- setting the env var is what makes counters
survive between commands; the long-lived MCP server accumulates in-memory
across its whole session regardless):

```bash
export NETTOOLS_METRICS_FILE=metrics.json
nettools fabric bgp
nettools metrics   # now reports the collection that just happened
```

No new dependency: the Prometheus text format is hand-written stdlib output,
not the `prometheus_client` package.

## Test Fixtures

`tests/fixtures/` holds real IOS-XR output captured from all nine devices as two
snapshots ~90s apart (`t0` and `t1`) on a quiet fabric. Parser, diff, and
health-rule work is developed and tested against these, so it needs no lab
access. `nettools capture` refreshes them; review the git diff by eye before
committing, since fixtures are permanent once pushed.

Replay a capture offline with `load_fixture_evidence("PE1", label="t0")` — it
returns the same structure a live `collect_evidence` call would.

### Live-lab integration tests (Phase 8)

`tests/test_live_lab.py` exercises `nettools` against a real, reachable lab
instead of fixtures or a fake transport -- the one tier of test in this suite
that needs real SSH and real credentials. It is marked `live_lab` (registered
in `pyproject.toml`, so a bare `pytest` never warns about it) and every test
in it self-skips unless `NETTOOLS_LIVE_LAB=1` is set, so it never runs
unattended in CI or on a contributor's machine with no lab reachable:

```bash
NETTOOLS_LIVE_LAB=1 pytest -m live_lab
```

## BYOK Reasoning Provider

`nettools analyze` and `nettools demo` need a reasoning provider: Anthropic,
OpenAI, or a local keyless Ollama model. Keep keys in the local environment or
an ignored `.env` file (`cp .env.example .env` to start).

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your_key_here
```

or:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your_key_here
```

or a local, keyless model served by Ollama:

```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=ornith:9b-q8_0
```

`ANTHROPIC_MODEL` defaults to `claude-opus-5` when unset -- pin an older
model explicitly (e.g. `claude-sonnet-4-5`) if you need to. The Anthropic path
streams, caps output at 32000 tokens (thinking is on by default on
`claude-opus-5`, and `max_tokens` covers thinking plus text together), and
caches the static instructions across calls. Server-side refusal fallbacks
(`fallbacks="default"`) are requested automatically when the resolved model
is in the Opus-5/Fable-5/Mythos-5 family; set `NETTOOLS_LLM_FALLBACKS=0` to
disable them even for those models.

## Fabric-Wide Analysis

`nettools analyze --fabric` collects evidence from every inventory device,
computes Phase 4 health verdicts for all of them, and sends both to the LLM
together -- so it can correlate a finding on one device with a related
finding on another (`check_fabric` only ever concatenates per-device
results; nothing ties them together). The evidence is passed through an
explicit character budget (`evidence_budget.py`) so a fabric-wide bundle
cannot silently blow the model's context: long sections are truncated in the
middle with an explicit `[TRUNCATED: N characters omitted]` marker, and
parsed structures are preferred over raw command text whenever parsing
succeeded (more information per character, and free of the whitespace-table
formatting a model tends to misread).

```bash
nettools analyze --fabric
nettools analyze --fabric --show-evidence --save
```

Tune the evidence budget (characters, a cheap proxy for tokens) with
`NETTOOLS_EVIDENCE_PER_INTENT_CHARS` and `NETTOOLS_EVIDENCE_TOTAL_CHARS` --
see `.env.example`.

## Bounded Agent Loop

`nettools agent "QUESTION"` runs a hand-written, bounded tool-calling loop
(Anthropic only -- OpenAI is not implemented yet, and a 9B local Ollama model
is not reliable enough for tool calling) over exactly six read-only tools,
each a thin wrapper over an already-safe function: listing devices, running
one intent or one validated template, checking the fabric, collecting full
evidence, and evaluating health. Every model-supplied argument goes through
the same validation a human CLI/MCP caller would -- a template parameter is
still canonicalized by reconstruction (Phase 5), not passed through as text.

```bash
nettools agent "Why is RR1 unhappy right now?"
nettools agent "Is PE2 reachable via IS-IS?" --device PE2 --max-iterations 5
```

The loop is bounded on two independent axes -- `--max-iterations` (default
8) and a wall-clock `--time-budget` in seconds (default 120) -- and hitting
either is a normal outcome, not an error: the command still prints whatever
partial answer the model produced, tagged with how it stopped
(`end_turn` / `max_iterations` / `time_budget` / `truncated`).

## Repository Map

```text
inventory/lab.yaml                 Declarative device inventory (topology, not secrets)
src/agent_nettools/platforms.py    Per-platform allowlist and intent table
src/agent_nettools/templates.py    Validated, parameterized command templates (canonicalize-by-reconstruction)
src/agent_nettools/inventory_model.py  Inventory schema (pydantic), YAML loading, credential-free
src/agent_nettools/lab.py          Credential-free reads of the inventory: mgmt IP + platform
src/agent_nettools/inventory.py    Joins the inventory with credentials resolved from the environment
src/agent_nettools/credential_resolver.py  Pluggable credential resolution: env (default) / file providers
src/agent_nettools/network_tools.py  Allowlist, SSH, evidence, fabric, diff, golden snapshots, flaps
src/agent_nettools/evidence_store.py  Snapshot storage backends: JSON files (default) or SQLite
src/agent_nettools/health.py       Deterministic health verdicts: role invariants + baseline rules
src/agent_nettools/topology.py     Derived expected topology + the fabric anomaly report
src/agent_nettools/devices_doc.py  Renders docs/devices.md from the inventory
src/agent_nettools/llm_analysis.py   Provider selection + single-device analysis + Anthropic plumbing
src/agent_nettools/evidence_budget.py  Character budget + middle-truncation for fabric-wide evidence
src/agent_nettools/fabric_analysis.py  Cross-device correlation over evidence + Phase 4 health verdicts
src/agent_nettools/agent_loop.py   Bounded, read-only, tool-calling agent loop (Anthropic only)
src/agent_nettools/metrics.py      Per-device collection/latency/retry and health-verdict metrics
src/agent_nettools/output.py       json (default)/table/summary rendering for CLI results
src/agent_nettools/cli.py          The `nettools` command-line entry point
src/agent_nettools/fixtures.py     Capture real device output; replay it offline
mcp_server/                        Read-only MCP server (tools, resources, and a prompt)
tests/                             Inventory, tool, provider, docs, safety, and CLI tests
tests/fixtures/                    Captured real IOS-XR output (t0/t1 pairs)
tests/test_live_lab.py             Live-lab integration tier (marker `live_lab`, skipped by default)
docs/                              devices.md (generated) and the code review
```
