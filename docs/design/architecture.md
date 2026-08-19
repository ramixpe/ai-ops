# Architecture

**What this is:** the reference for how `ios-xr-nettools` is built, layer by layer.
It was extracted from `CLAUDE.md` on 2026-08-17, which had grown to 941 lines and
was the largest document in the repository — an agent-instruction manual that a
newcomer met before the tool itself, and simultaneously the only place the
architecture was written down. `docs/README.md` had pointed at this file for some
time before it existed.

**What stayed in `CLAUDE.md`:** the rules an agent must not break — the safety
boundary and its ordering invariant, the investigation layer's four invariants,
the testing seams, the doc-sync markers, and the frozen-file list. **This file
describes; that file constrains.** Where a section here touches something
load-bearing it says so and points back rather than restating it, because two
copies of an invariant is how they diverge (`BUILD-PLAN.md` §0.13, the
duplication face).

**Read `docs/design/glossary.md` first** if any term here is unfamiliar. `intent`
in particular means *a question name*, not intended state.

---

## The layer stack

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
3. `inventory.py` — joins the parsed inventory with credentials (resolved per
   device through its `credential_group`, via whichever pluggable provider
   `credential_resolver.get_resolver()` selects -- see Phase 8) into device
   dicts. Raises `InventoryError` when required env is missing or the
   inventory is invalid.
4. `network_tools.py` — the allowlist, SSH transport, evidence collection,
   snapshots, and diffing. All real logic lives here.
5. `cli.py` and `mcp_server/server.py` — two independent front ends that call
   the *same* functions from layer 4. Anything added to layer 4 should usually
   be surfaced in both.

`topology.py` (derived expected topology counts + the fabric anomaly report)
and `devices_doc.py` (renders the device table on demand; the committed
`docs/devices.md` was retired at M0) sit beside layer 4/5: they
read parsed evidence and the inventory, but nothing depends on them.
`credential_resolver.py` (Phase 8) sits beside layer 3, called only from
`inventory.py`; `metrics.py` (Phase 8) sits beside layer 4, called from
`network_tools.py`/`health.py`; `output.py` (Phase 8) sits beside layer 5,
called only from `cli.py`.

**The investigation layer (MVP-0)** sits between layers 4 and 5 as a stack of
its own, each module depending only on the ones above it in this list:

| Module | What it is | Depends on |
|---|---|---|
| `template_parsers.py` | TTP parsers for the Phase 5 templates, plus §0.10 line accounting (`IgnoreRule`, `account_lines`, `finalize`) | nothing in this layer |
| `checks.py` | Pure predicates over parsed records. `healthy`/`broken`/`unevaluated`, and a check may only answer `healthy` about a field it actually read | `template_parsers` |
| `flows.py` | The ladder: `Rung`, `DeviceScope`, `SubjectRule`, `Aggregation`, and the `bgp_session` flow | `checks` |
| `descent.py` | `run_descent()` — the deterministic walk. **No model call anywhere in this module**, and that is the claim the layer rests on | `flows`, `checks` |
| `epoch.py` | One observation window: collect once per device, reuse across rungs, re-read the symptom and the cause at the end. `temporally_incoherent` when the skew exceeds its bound or the fabric moved | `descent`, `flows`, `network_tools` |
| `coverage.py` | What an evidence source was able to tell us; `gaps()` is why a negative may not be assertable | nothing |
| `log_window.py` | Shapes a log window by *attribution* (`NoiseRule`), never by content; builds the coverage record | `coverage` |
| `prompt_library.py` | Loads versioned prompts from `prompts/` and renders them. **Structurally cannot receive device text** — it takes a `DescentResult` | `descent`, `log_window` |
| `grounding.py` | The gate. Citation integrity, chain coverage, timeline citations, absence coverage | `descent`, `coverage`, `log_window` |
| `investigation.py` | `investigate()` — the runner that wires the above together | all of the above, `network_tools` |

`cli.py`'s `investigate` subcommand is the only front end so far; MCP parity is
backlog (B-113).

> The four invariants this layer inherits, the two modules that look like
> ordinary code and are not, and the precondition every flow must satisfy are
> **rules**, and they live in `CLAUDE.md` rather than here.

---

## Intents: the multi-vendor keystone

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

---

## Registries that generate behavior

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

---

## One SSH session per collection

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

---

## Uniform result envelope

Every tool returns `{tool, device, status, timestamp, data, errors}` with
`status` in `{"success", "error"}`. Failures are structured values, not
exceptions — broad `except Exception` with a `# noqa: BLE001` comment is the
established idiom at the SSH boundary. Errors are formatted as
`"<command>: <detail>"` for per-command failures and
`"connection to <host> failed: <detail>"` for session failures; `diff_evidence`
and `_section_from_combined` parse those prefixes, so keep the shapes.

---

## Parsing and diff semantics (Phase 2)

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

---

## Declarative inventory and topology (Phase 3)

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
connects to B") topology, because ~~this fabric's own LLDP data is self-contradictory~~ **-- corrected
2026-08-16, see OBS-103.** It is not. At the `t0`/`t1` captures three devices
were configured with hostnames that differ from their inventory labels: P1 is
`LEAF05_DHCP_SERVER`, P3 is `Lab-leaf01`, PE4 is `SDWAN-Edge01`. So P1 reporting
its Gi0/0/0/0 facing P2, and P2 reporting that same port facing
`LEAF05_DHCP_SERVER`, are **the same statement** -- LLDP was correct at both
ends, and the disagreement was between LLDP's device-reported names and the
inventory's labels. The hostnames were aligned by the `healthy`/`broken`
captures. The count-only rule below is still right, for the reasons given, but
not for this reason. Asserting a
specific link would silently pick a side of a real disagreement; a count
survives it. `learn-topology` always exits `0` but prints an anomaly report
covering three classes — LLDP links where the two ends disagree, LLDP neighbors
not in this inventory, and devices with zero adjacencies — so the report, not a
clean inventory file, is where the specifics live.

**Two of those three classes are now empty on this fabric, and that is the
correction landing (B-435).** They used to hold one disagreement and three
unknown neighbours (`Lab-leaf01`, `LEAF05_DHCP_SERVER`, `SDWAN-Edge01`), and
every one was an artefact of comparing LLDP's device-reported names against
inventory labels. `topology.hostname_map` resolves a reported name to its
inventory name via the `facts` intent's parsed hostname — already collected, so
it costs no command — and both classes go to zero.

**The classes were not deleted.** A genuine wiring disagreement and a genuinely
foreign neighbour still exist as concepts and are still reported; they are
covered by synthetic tests, because the real fixtures no longer contain either
and a fix that removes the only coverage of behaviour that is still correct is
its own defect. What remains real here is the third class: PE2 (0 LLDP / 0
IS-IS, despite an active BGP peer toward RR1) and PE4 (an LLDP neighbor, 0
IS-IS).

`devices_doc.render_devices_doc()` renders the device table from the same
inventory, on demand. The committed copy, `docs/devices.md`, was retired at M0
(2026-08-18) as a second source that could drift from `inventory/lab.yaml`;
`test_the_devices_doc_generator_still_renders_from_the_inventory` now pins the
weaker, still-true claim that the generator itself works and reads the
inventory.

---

## Deterministic health verdicts (Phase 4)

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

---

## Validated, parameterized command templates (Phase 5)

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

---

## Bounded agent loop, fabric analysis, and prompt caching (Phase 6)

Three additions, all downstream of `llm_analysis.py`'s provider layer, none
touching the safety boundary itself:

**Evidence budget (`evidence_budget.py`).** A single device's evidence is
always small enough to send whole; a fabric-wide bundle (Phase 6's
`analyze_fabric`, one evidence collection per inventory device) has no such
guarantee, and nothing before this module ever capped it. Every budget is
measured in *characters*, a cheap dependency-free proxy for tokens -- no
tokenizer call needed, and a slightly generous character bound is a safe
conservative token bound for the mostly-ASCII CLI output this tool handles.
Long sections are truncated in the *middle*, not the tail (a route table or
log dump is often most informative at both ends), with an explicit
`[TRUNCATED: N characters omitted]` marker so the model is told evidence is
partial rather than reading a short section as a complete, uneventful one --
the analysis prompts already instruct the model to say what is missing.
Parsed structures are sent instead of raw command text whenever
`parse_status == parsers.PARSE_OK`: parsed JSON is far more information-dense
per character, and a raw CLI table's column-aligned whitespace is exactly
what a model tends to misread or waste tokens re-deriving structure from.
Per-intent truncation runs first so no single pathological section (a huge
route table) can dominate; a fabric-wide total ceiling is enforced after,
proportionally shrinking every already-budgeted section if the sum still
exceeds it. Tunable via `NETTOOLS_EVIDENCE_PER_INTENT_CHARS` /
`NETTOOLS_EVIDENCE_TOTAL_CHARS` (see `.env.example`).

**Fabric-wide analysis (`fabric_analysis.py`).** `check_fabric` only
concatenates -- it runs one check across every device and hands back a dict
keyed by device name, with nothing tying two devices' findings together as
one incident. `analyze_fabric(evidence_by_device, verdicts=None)` feeds the
model both the (budgeted) raw evidence *and* the Phase 4 deterministic
health verdicts for every device, so the model's job is interpretation and
correlation, not detection -- the verdicts are already the anomaly signal.
Ground truth this is measured against (from the committed fixtures, the same
numbers `test_health.py` pins directly against `health.py`): PE2 and PE4 have
zero IS-IS adjacencies, so neither can reach RR1's loopback, so their iBGP
sessions toward RR1 sit Idle -- and RR1 independently reports those same two
sessions (`10.255.0.12`, `10.255.0.14`) as Idle from the other side. That is
one correlated incident with a single root cause, and
`test_fabric_prompt_contains_pe2_pe4_isolation_and_rr1_idle_peers` asserts the
built prompt actually carries what is needed to reach that conclusion --
never asserting on model output, only on the evidence handed to it. Wired to
`nettools analyze --fabric`.

**Bounded agent loop (`agent_loop.py`).** A hand-written, provider-agnostic
loop over `stop_reason`, not `client.beta.messages.tool_runner` -- three
reasons, spelled out fully in the module docstring: the loop needs a hard
`max_iterations` *and* a wall-clock `time_budget_s` in a safety-critical path
(this talks to real lab devices) and the tool runner exposes no wall-clock
budget; the loop shape must stay provider-agnostic for when OpenAI/Ollama
support lands; and `tool_runner` is a beta surface this small loop does not
need. Exactly six read-only tools are exposed
(`list_lab_devices`/`run_lab_intent`/`run_lab_template`/`check_lab_fabric`/
`collect_lab_evidence`/`assess_lab_health`), each a thin wrapper over an
existing, already-safe function -- no generic executor, no new device access
path. `intent`/`template`/`check` are declared with an `enum` in their JSON
schema, drawn live from `platforms.all_intents()`, `templates
.PLATFORM_TEMPLATES`, and `network_tools.CHECK_TOOLS`, so the model cannot
even *name* an unknown one. Critically, every `run_lab_template` call's
parameters flow through the same `run_template` -> `render_command` Phase 5
validation a human CLI/MCP caller goes through -- a malicious or malformed
value from the model (e.g. an `address` of `"10.0.0.1 | reload"`) is refused
with the same structured error, not a special case; this is pinned by
`test_execute_tool_refuses_a_malicious_template_argument_directly` and its
full-loop counterpart, and needs no credentials or fake transport because
`render_command`'s validation runs before any device access, exactly as it
does for `run_template` itself.

Hitting a bound is a normal outcome, not an exception: `run_agent_loop`
returns `{"answer", "iterations", "tool_calls", "stopped_because", "usage"}`
with partial results and `stopped_because` set to why
(`end_turn`/`max_iterations`/`time_budget`/`truncated`), never raising for a
bound. Multiple `tool_use` blocks in one response are all executed, and every
result goes back in a *single* `user` message (splitting them across
messages silently trains the model to stop requesting tools in parallel); a
failed tool call becomes a `tool_result` with `is_error: true` rather than
being dropped -- but a *validation* refusal from `render_command` is a
structured result, not a Python exception, so it comes back with
`is_error: false` and the refusal text as ordinary content, exactly like any
other structured error this package returns. Wired to `nettools agent
"QUESTION" [--device D] [--max-iterations N] [--time-budget SECONDS]`.
Anthropic only for now (Task D); OpenAI and Ollama raise `LLMAnalysisError`
with a clear message (a 9B local Ollama model is not reliable for tool
calling) rather than attempting an unreliable loop. Single-shot
`analyze_evidence` is unaffected and still works on all three providers.

**Anthropic call plumbing, shared by all three surfaces.** `ANTHROPIC_MODEL`'s
unset-fallback is now `claude-opus-5` (this repo's own `.env` pins an older
model on purpose, unaffected). No call ever sets `temperature`/`top_p`/
`top_k`/`thinking.budget_tokens` -- all four 400 on `claude-opus-5`, and
adaptive thinking is on by default there, so `thinking` is simply left
unset. Because thinking now shares `max_tokens` with the response text, the
Anthropic path streams (`client.messages.stream(...)` /
`get_final_message()`, removing the non-streaming SDK's HTTP-timeout guard)
and the ceiling is raised to `ANTHROPIC_MAX_OUTPUT_TOKENS` (32000).
**Prompt caching is a prefix match** (render order `tools` -> `system` ->
`messages`): every Anthropic call puts its static instructions in `system`
as a single text block carrying `cache_control={"type": "ephemeral"}`, and
all volatile content (evidence, the question, a device name, a timestamp)
in `messages` -- never the other way around, or the cache is invalidated on
every single call. `test_anthropic_cached_prefix_is_byte_identical_across_...`
and the agent loop's equivalent assert this directly on the request a fake
`anthropic` module captured. **Refusal fallbacks are conditional, not
automatic**: `fallbacks="default"` requires the beta endpoint
(`client.beta.messages.stream`, beta header
`server-side-fallback-2026-07-01`) and is only meaningful for the
Opus-5/Fable-5/Mythos-5 family -- `_fallbacks_enabled` gates on both the
resolved model's prefix and `NETTOOLS_LLM_FALLBACKS` (falsy disables it even
for a matching model); every other model uses the plain, non-beta path. A
network-troubleshooting prompt can plausibly trip a cyber-content classifier
even though nothing here is malicious, so `stop_reason == "refusal"` is
always handled, reading `stop_details` only in that branch (it is `null`
otherwise).

---

## Scaling the fabric path, and a second evidence backend (Phase 7)

A fabric-scale measurement found `check_fabric` super-linear (2.9-3.6x time
per 2x devices, not 2x) even with an injected `sender` and zero network I/O.
Root cause: `check_fabric` calls `load_inventory()` once, but each per-device
check calls `get_device(name)` (via `run_intent` -> `_run_approved_commands`),
and `get_device` used to rebuild the *entire* credentialed inventory and
linear-scan it for one name -- O(n) work repeated once per device.

Two independent fixes, deliberately kept separate:

- **O(1) device lookup.** `inventory_model.find_device()` caches a
  `{name: Device}` index alongside the existing YAML-parse cache, invalidated
  by the same `reset_inventory_cache()` -- credential-free, so it carries none
  of the "env changed, cache went stale" risk a credentialed cache would.
  `inventory.get_device()` now does an indexed lookup plus resolving *one*
  device's own credential group, not the whole inventory's; `lab.platform_for()`
  uses the same index. `load_inventory()`'s behavior is unchanged.
- **Resolve once, thread down.** `check_fabric`/`iter_fabric` resolve every
  device exactly once (the existing single `load_inventory()` call) and pass
  each record straight through `run_intent`/`_run_approved_commands` via an
  optional `device=` parameter, so a whole-fabric check never re-resolves a
  device by name at all. `device=` is read *only after* the allowlist check in
  `_run_approved_commands` -- never before -- so the safety ordering invariant
  holds regardless of whether a caller supplies it; every caller except
  `check_fabric` leaves it `None` and gets the exact previous behavior,
  including the two tests that call `_run_approved_commands` with no
  credentials in the environment at all.

**Streaming fabric results.** `check_fabric` used to build
`dict(pool.map(run_one, devices))`, materializing every device's full result
before returning anything -- the memory ceiling at 1000+ devices with real
command output. `iter_fabric(check, *, sender=None, max_workers=...)` is the
streaming twin: it yields `(device_name, result)` via
`concurrent.futures.as_completed` as each device finishes, in completion
order, not inventory order. `check_fabric` is now built on the same shared
runner (`_iter_check_results`) and simply collects + reorders into inventory
order for callers that want one complete envelope back -- behavior, including
the `unsupported` bucket, is unchanged.

**Connection timeouts and bounded retries.** `_netmiko_send_commands` had no
`conn_timeout`/`banner_timeout`, and only ever got a `read_timeout` from a
`Template` (ping/traceroute); a reachable-but-slow device could otherwise
stall a check for however long netmiko's own defaults allow. All three
timeouts are now env-then-default configurable
(`NETTOOLS_CONNECT_TIMEOUT_SECONDS`/`NETTOOLS_READ_TIMEOUT_SECONDS`/
`NETTOOLS_BANNER_TIMEOUT_SECONDS`), and a `Template`'s own `read_timeout`
still wins whenever it is set, since it is always passed explicitly rather
than left `None`. Both the initial connection and each command are retried up
to `NETTOOLS_COMMAND_RETRIES` total attempts (default 2) with exponential
backoff (`NETTOOLS_RETRY_BACKOFF_SECONDS`) on a *transient* failure only --
`_is_transient_failure` never retries a `NetmikoAuthenticationException` (bad
credentials cannot succeed on a later attempt), and nothing upstream of
`_netmiko_send_commands` ever retries a command the allowlist refused, since
that check happens before this function is ever reached. A retry that
happened is never silent: it appears both in the `NETTOOLS_LOG` audit record
and, for the command(s) that needed one, under `data.retries` in the result.

**A second evidence backend.** `evidence/<device>/*.json` (still the default)
grows unbounded and cannot be queried; `load_latest_snapshot` relies on
ISO-timestamp filenames sorting lexicographically. `evidence_store.py` adds a
`EvidenceStore` shape (save a snapshot, load latest, save/load golden, list a
device's history, prune by age and/or count) with two implementations --
`FileEvidenceStore` (the original layout, moved here unchanged) and
`SQLiteEvidenceStore` (stdlib `sqlite3` only, one table indexed on
`(device, timestamp)`). `NETTOOLS_EVIDENCE_BACKEND=sqlite` opts in; unset or
anything else keeps files. `network_tools.py`'s public snapshot functions are
now thin wrappers over `evidence_store.get_store()`, so `diff_evidence` needs
no change -- both backends hand back the identical evidence dict shape.
Pruning (`nettools evidence prune --keep-days N --keep-count M`) keeps a
snapshot that satisfies *either* configured rule (restic/borg-style: the
union of what any rule wants to keep survives); neither rule given is a
no-op, not "prune everything"; the golden snapshot is never touched by
pruning in either backend. `detect_flaps` deliberately keeps reading history
straight off the file store rather than going through this abstraction --
moving flap detection onto a second backend is future work, not something
this phase's fabric-scale measurement asked for.

**Audit log rotation.** `_audit_log`'s single ever-growing JSONL file now
rotates by size (`NETTOOLS_LOG_MAX_BYTES`, default 10 MiB) with a bounded
number of kept backups (`NETTOOLS_LOG_BACKUP_COUNT`, default 5), the same
rename-chain algorithm `logging.handlers.RotatingFileHandler` uses. Logging
remains best-effort: a rotation or write failure is still swallowed, never
raised -- `test_audit_log_failure_never_breaks_a_check` pins that a bad
`NETTOOLS_LOG` path cannot turn a successful check into a reported failure.

---

## Production hardening (Phase 8)

Six mostly-independent additions, none touching the safety boundary itself.

**MCP parity.** Through Phase 7, snapshots, diffing, health, and flap
detection were CLI-only -- the MCP client is an LLM, the primary consumer of
this server, and it could not do drift detection or get a health verdict at
all. `mcp_server/server.py` adds seven tools, each a thin wrapper over an
existing function, no new device-access path:
`diff_lab_device_against_latest`/`diff_lab_device_against_golden`,
`save_lab_snapshot`, `pin_lab_golden_snapshot`,
`assess_lab_device_health`/`assess_lab_fabric_health` (reuses
`health.evaluate_fabric` even for one device -- `evaluate_fabric({name:
evidence})["devices"][name]` -- rather than duplicating device lookup), and
`detect_lab_flaps`. `tests/test_docs.py`'s prefix filter for
`test_mcp_readme_lists_exactly_the_exposed_tools` was widened (`diff_lab`,
`save_lab`, `pin_lab`, `assess_lab`, `detect_lab`) to keep pinning the README
against the server's real surface; that test itself is not in the
do-not-modify list, only its assertion logic in `test_safety.py` is.

Every tool is registered via `_read_only_tool()`, a thin wrapper around
`@mcp.tool()` that adds `annotations=ToolAnnotations(read_only_hint=True)`
*when the installed SDK's `tool()` decorator accepts an `annotations=`
keyword at all* (checked once via `inspect.signature` at import time,
mirroring the existing `FastMCP`/`MCPServer` old/new-SDK import fallback) --
an older SDK without that parameter gets the bare decorator instead of a
`TypeError` on every tool registration. `server.READ_ONLY_ANNOTATIONS_SUPPORTED`
records which path a given install took. Two MCP **resources**
(`lab://inventory`, `lab://topology/expected`) let a client ground itself
without spending a tool call; one MCP **prompt** (`troubleshooting_prompt`)
exposes `llm_analysis.TROUBLESHOOTING_PROMPT` directly. `tests/test_mcp_server.py`
drives real tool/resource/prompt calls through an actual `ClientSession` over
the SDK's in-memory transport (`mcp.shared.memory.create_client_server_memory_streams`,
no subprocess, no `pytest-asyncio` -- each test is a plain function that
builds a small `async def` and drives it with `asyncio.run()`), asserting on
the returned result envelope -- `test_safety.py` only ever asserted tool
names were *absent*; nothing before this actually called one through the
protocol.

**Output formats and exit codes.** `output.py` renders any result payload as
`json` (default, so nothing already parsing this tool's output breaks),
`table` (plain padded columns, no dependency), or `summary` (one line) by
recognizing the handful of shapes this package's results actually come in
(a single-device tool envelope, a fabric envelope, a health verdict --
single-device or fabric -- a diff result, an inventory listing) and falling
back to a flat key/value reformat for anything else. **It never invents or
softens data**: every `_cmd_*` in `cli.py` computes its exit code from the
full result *before* calling `_emit()`, so `--format summary` on a critical
fabric still exits non-zero exactly like `--format json` would -- rendering
and exit-code computation are two separate steps, deliberately. Exit codes
follow one scheme everywhere, generalizing the shape `nettools health`
already used (0 ok/info, 1 warning, 2 critical): `0` success, `1` the command
ran but reports a problem (`status: "error"`, a `warning` verdict, or real
`diff` drift), `2` could not run at all or reports the worst outcome
(`InventoryError`, `get_provider()`'s `ValueError`, a `critical` verdict, or a
`diff` where an intent failed to collect). `nettools diff` layers the Unix
`diff --exit-code` convention on top: `0` nothing differs, `1` differences
found, `2` the comparison itself is not trustworthy.

**Metrics.** `metrics.py` is a thread-safe `MetricsCollector` (a lock guards
every mutation, since `check_fabric`'s thread pool can call it concurrently)
recording per-device collection success/failure, latency, and retries --
hooked into `_netmiko_send_commands`, the one function every real device
connection already flows through (same reasoning `_audit_log` uses, and the
same "`sender=` bypasses it entirely" behavior: unit tests using `sender=`
never pollute metrics) -- plus health verdict counts by severity, hooked
into `health.evaluate_device` (covers `evaluate_fabric` too, which calls it
once per device). Exposed via `nettools metrics` as JSON or hand-written
Prometheus text exposition format (stdlib only, no `prometheus_client`
dependency). Counters are in-memory by default (a fresh process every CLI
invocation would otherwise always read zero, but this still accumulates
correctly within one process -- a single `nettools fabric bgp` call, or the
whole lifetime of the long-lived MCP server); `NETTOOLS_METRICS_FILE` opts
into on-disk persistence across separate invocations, read lazily on first
use per process (never at import time, so it cannot race `cli.py`'s own
`.env` loading) and best-effort written back after each mutation -- the same
"swallow a write failure, never raise" idiom `_audit_log`/`evidence_store.py`
already use. No result envelope changes because of this module; it is purely
a side channel.

**Pluggable credential resolution.** `credential_resolver.py` replaces
`inventory.py`'s inline `os.environ` reads with a small `CredentialResolver`
interface, selected by `NETTOOLS_CREDENTIAL_PROVIDER` (`env`, the default and
the exact pre-Phase-8 behavior, or `file`). Both providers share the same
`CredentialGroup` schema -- no inventory YAML change needed to switch --
because the difference is only in *how* a named field becomes a string:
`EnvCredentialResolver` reads the named environment variable's value
directly; `FileCredentialResolver` reads that same variable's value as a
*file path* (the Docker/Kubernetes secrets convention, e.g. one secret
mounted per file under `/run/secrets/`) and returns the file's content. A
real secret-manager backend (Vault, AWS Secrets Manager, ...) is
**deliberately not shipped** -- there is no such service in this lab to test
against, and an untested credential path is worse than an honest gap; the
module docstring spells out exactly what one would need to implement
(subclass `CredentialResolver`, map every failure to `InventoryError`, never
leak a raw secret through logs or an exception, register under a new
provider name). The safety invariant is unaffected regardless of provider:
`test_refuses_unapproved_commands_before_loading_credentials` and
`test_refuses_another_platforms_command_without_credentials` (both
unmodified, both still run with an empty environment) pass because platform
resolution and the allowlist check never construct a resolver at all --
credential resolution is still reached only from `inventory.load_inventory()`/
`get_device()`, at the same point in the call chain as before this module
existed.

**Audit actor.** `_audit_log` records an `actor` field: `NETTOOLS_ACTOR` if
set, else `getpass.getuser()`, else `"unknown"`. **This is provenance, not
authorization** -- attribution for a trusted single-operator deployment,
spelled out explicitly in `_resolve_actor`'s docstring. It is never consulted
before a command runs; the allowlist is the only enforcement mechanism this
project has. Real RBAC needs an identity provider this project does not have
(a verified SSO/OIDC token, a signed client certificate -- something a caller
cannot simply set an environment variable to become), checked *before* any
command runs; no fake authorization check was added anywhere as a
substitute.

**Cross-cutting.** `cli.py` had zero tests despite ~400 lines of argument
wiring, exit codes, and error handling -- `tests/test_cli.py` now drives
`build_parser()`/every `_cmd_*` (monkeypatching the specific `cli`-module-bound
function each one calls, never the network) and `main()` itself, including
the exact regression `docs/archive/REVIEW.md` records (`analyze`/`demo` not catching
the `ValueError` `get_provider()` raises on misconfiguration) and the
top-level `InventoryError` → exit-2 handler. A live-lab integration tier
(`tests/test_live_lab.py`, marker `live_lab` registered in `pyproject.toml`)
exercises real SSH against the real lab; every test in it self-skips unless
`NETTOOLS_LIVE_LAB=1` is set, so a bare `pytest`/CI never needs a reachable
lab and never warns about an unregistered marker. `nettools version` prints
the installed package version (`importlib.metadata.version("agent-nettools")`,
falling back to a placeholder string when run from a source checkout with no
install metadata) plus the Python/platform it is running on.

---

## LLM layer

`llm_analysis.py` supports Anthropic, OpenAI, and local keyless Ollama.
`get_provider()` raises `ValueError` on misconfiguration while the analyze
functions raise `LLMAnalysisError`; CLI callers must catch both (a past
regression). `auto` never selects Ollama — it must be requested explicitly.
Provider SDKs are imported lazily and live in the optional `llm` extra, so the
core package and the Docker image install without them. Phase 6 layers
fabric-wide analysis (`fabric_analysis.py`) and a bounded tool-calling agent
loop (`agent_loop.py`) on top of the same Anthropic call plumbing — see
"Bounded agent loop, fabric analysis, and prompt caching (Phase 6)" above.

---
