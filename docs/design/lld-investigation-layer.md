# LLD — Deterministic Investigation Layer

**Target repository:** `ios-xr-nettools` (`agent_nettools` + `mcp_server`)
**Baseline reviewed:** Phase 8 complete — 23 modules, ~4,900 source lines, 554 tests, live fixtures for 9 devices at `t0`/`t1`
**Companion:** `design-thinking.md` (D1–D20), Chapter 1 foundations deck
**Status:** delta specification, pre-build

---

## 0. Premise

This is not a greenfield LLD. The repository already implements a substantial fraction of the design document — in several places more rigorously than the design document specified. The job here is not to design a system; it is to specify the **delta** between what exists and what the design calls for, name what must not be disturbed in the process, and put the work in an order where each step is shippable.

Three things follow from that:

1. Where the repo and the design document disagree on **vocabulary**, the repo wins. It has a large test suite and generated documentation pinned to its terms (554 at the time this was written; 1776 today).
2. Where the repo and the design document disagree on **mechanism**, the repo usually wins too — several existing mechanisms are stronger than what the design specified, and Section 2 records which.
3. New code inherits the existing safety invariant unchanged. Section 3 states it as a hard constraint on every module added below.

---

## 1. Terminology reconciliation — resolve before writing code

There is one collision, and it is load-bearing enough that ignoring it would produce two incompatible vocabularies inside one codebase.

**`intent`.** The repo uses it for *a vendor-neutral name for a question* — `facts`, `interfaces`, `bgp`, `lldp`, `isis`, `sr` — keyed as `PLATFORM_INTENTS[platform][intent] -> tuple[str, ...]`. The design document used it for *intended state*, the configuration axis of evidence.

The repo's usage is older, is spelled into `INTENT_ORDER`, `all_intents()`, `intents_for()`, `run_intent()`, `evidence_intents()`, the CLI subcommands, the MCP tool names, the evidence section keys, and `platforms.py`'s module docstring. It does not move.

| Design document term | Repo term to use | Notes |
|---|---|---|
| `domain` (the closed enum) | **`intent`** | Already exactly this: a closed, per-platform enum of question names |
| `get_intent` (tool) | **`get_config_section`** | Renamed to free `intent` for the repo meaning |
| "intended" evidence axis | **"config" axis** | `config` in code; "intended state" in prose only |
| "flow" | **`flow`** | New; no collision |
| "descent"/"rung" | **`descent` / `rung`** | New; no collision |

Everything below uses the repo's vocabulary. The design document should get a terminology note pointing here rather than being rewritten.

---

## 2. What already exists

Mapped against the twenty design decisions. This section exists so nobody rebuilds something that is already done and tested.

| Decision | Status | Where |
|---|---|---|
| **D2** Structural enforcement, not prompt | **Done, stronger than specified** | `platforms.APPROVED_COMMANDS` exact-match frozenset, checked *before credentials load or a socket opens* |
| **D9** Vendor never reaches the model | **Done** | `PLATFORM_INTENTS` is platform-major; `lab.platform_for()` is credential-free by design; `test_commands_do_not_leak_across_platforms` |
| **D10** Tools parameterised by a closed enum | **Partly** | `agent_loop` exposes 6 tools with JSON-schema `enum` drawn from `all_intents()`. The MCP server does not — see §4.1 |
| **D11** Tool only if the model decides when to call it | **Partly** | True of `agent_loop`; the MCP server exposed ~22 tools at the time of this review (21 today, after B-438 removed two writers and MVP-0 added `investigate_lab_session`) including several a model never needs to choose |
| **D13** Active probes as a separate class | **Done — this was listed "open" and is in fact built** | `Template.active_probe`, `NETTOOLS_ALLOW_ACTIVE_PROBES`, refused before rendering |
| **D14** Memory derived, keyed by object, historical only | **Substantially done** | `evidence_store` (files or SQLite), `save_snapshot`, `save_golden_snapshot`, `detect_flaps`, `diff_evidence` keyed by `parsers.record_key` excluding `volatile_fields` |
| **D15** Template parsing, never model extraction | **Done for static intents, absent for templates** | `parsers.PARSERS[(platform, intent)]` — six entries, `cisco_xr` only. See §4.2 |
| **D16** Context is a budget | **Partly** | `evidence_budget.py` — per-intent 4,000 / total 40,000 chars, middle truncation with an explicit marker, parsed-preferred-over-raw. Config sections are not retrieved at all yet |
| **D17** Source of truth for inventory | **Done** | `inventory/lab.yaml` + pydantic `extra="forbid"`; `expected:` derived by `learn-topology`, never hand-invented |
| **D18** Allowlist in three places | **Done** | `AGENTS`-style guidance in `CLAUDE.md`, `APPROVED_COMMANDS` in code, `tests/test_safety.py` + `tests/test_template_security.py` |
| **D19** Guardrails as tests | **Done, stronger than specified** | 11 safety tests including `test_refuses_unapproved_commands_before_loading_credentials`, which runs with an empty environment |
| Deterministic verdicts | **Done, and not in the design document** | `health.py`: `ROLE_INVARIANT_RULES` / `BASELINE_RULES` / `META_RULES`, `SEVERITY_ORDER`, `unevaluated` bookkeeping |

Two mechanisms in the repo are better than what the design document specified and should be treated as the reference, not the other way round:

**Canonicalize by reconstruction.** `templates.py` never substitutes a caller value into a command as text. It parses to `ipaddress.IPv4Address` / `IPv4Network` / a range-checked `int` / a regex-validated interface name, then renders from *that object's own canonical form*, with five layered defenses and a `VERB_ALLOWLIST` of `{show, ping, traceroute}`. The design document's D16 rule "the pipe filter is never model-supplied" is a weaker statement of the same principle. Config-section retrieval (§5.5) must be built inside this mechanism, not beside it.

**`unevaluated`, not silently "ok".** `health.py` refuses to read a failed or unsupported intent as healthy; it lists the intent under `unevaluated` instead. This is exactly the discipline the descent needs at every rung, and §5.2 adopts it verbatim.

---

## 3. The invariant that does not move

Every module specified below is bound by the existing safety boundary, restated here as an acceptance criterion:

> Platform resolves through `lab.platform_for()`, which reads static inventory data only. The command allowlist is checked against *that device's own platform* **before** credentials are loaded or a socket is opened. There is no `run_command`, no configuration mode, no shell.

Concretely, for new code:

- No new module may call `inventory.get_device()` (or anything that resolves credentials) before its command has been validated.
- No new module may construct a command string by interpolation. Every parameterised command goes through `templates.render_command`.
- Any new command reaches a device only via `network_tools._run_approved_commands` or `network_tools.run_template`. There is no third path.
- `tests/test_safety.py::test_no_generic_run_command_is_exposed` must continue to pass with the new tool surface in scope.

A fourth constraint is added by this LLD:

- **No new module may pass unparsed device text to a model.** Model input is parsed records, verdicts, and diffs. Raw text reaches a model only through `evidence_budget`'s existing parsed-failed fallback path.

---

## 4. Gap analysis

Four gaps stand between the current repo and the design. They are listed in dependency order — each later one needs the earlier ones.

### 4.1 Tool surface bloat on the MCP server

`mcp_server/server.py` exposes roughly 22 tools: `check_lab_interfaces`, `check_lab_bgp_neighbors`, `check_lab_lldp_neighbors`, `check_lab_isis_neighbors`, `check_lab_sr_policies`, `get_lab_route`, `get_lab_bgp_neighbor`, `get_lab_interface`, `get_lab_logging`, `get_lab_ping`, `get_lab_traceroute`, `diff_lab_device_against_latest`, `diff_lab_device_against_golden`, `save_lab_snapshot`, `pin_lab_golden_snapshot`, `assess_lab_device_health`, `assess_lab_fabric_health`, `detect_lab_flaps`, and more.

> **Note added 2026-08-17.** Two tools in that list no longer exist: `save_lab_snapshot` and `pin_lab_golden_snapshot` were **removed by B-438** because both performed a persistent write from behind a decorator named `_read_only_tool`. The list is left as written — it is the baseline this delta spec was measured against — but do not read it as the current surface. `mcp_server/README.md` is authoritative and is pinned to the code by `test_mcp_readme_lists_exactly_the_exposed_tools`.

This is D10 and D11's failure mode made concrete: one tool per command, so the manifest grows with the catalogue, and several tools represent decisions a model never makes (`save_lab_snapshot`, `pin_lab_golden_snapshot` are operator actions, not investigative ones).

`agent_loop.py` already demonstrates the correct shape — six tools with `enum`-constrained arguments. The MCP surface should converge on it.

### 4.2 Template output is never parsed — **the blocking gap**

`parsers.PARSERS` has six entries, all `("cisco_xr", <static intent>)`. `run_template` attaches no parsed data at all. So `show route <prefix>`, `show bgp neighbor <ip>`, `show interfaces <name>` and `show logging last <n>` return raw text only.

Every rung of the dependency descent below the top one reads exactly those commands. **Without template parsers there is no descent** — only a model reading raw text, which §3 forbids. This is the first piece of work.

### 4.3 No configuration axis

The only config command on the allowlist is `show running-config hostname`. The design's three evidence axes are therefore two: observed and historical. D16's five reductions have nothing to reduce yet.

### 4.4 No flow, descent, gate, or grounding

`agent_loop.py` is a free-form bounded loop: the model picks any of six tools in any order until `end_turn`, `max_iterations`, or `time_budget`. It is safe — every call is validated — but it is not the designed investigation. There is no object-indexed flow, no ordered dependency descent, no typed gate decision, and no check that a reported claim cites evidence.

---

## 5. New modules

Six new modules and four modified ones. All new modules live in `src/agent_nettools/`.

### 5.1 `template_parsers.py` — parsers for parameterised output

Extends the existing parser contract to templates rather than replacing it.

```
TEMPLATE_PARSERS: dict[tuple[str, str], Callable[[str], dict[str, Any]]]
    # keyed by (platform, template_name), mirroring parsers.PARSERS' shape
```

Initial entries, `cisco_xr` only:

| Template | Parsed output |
|---|---|
| `bgp_neighbor` | `{meta: {state, connection_state, last_reset_reason, hold_time, keepalive}, records: [...]}` |
| `route` | `{meta: {found: bool, protocol, distance, metric}, records: [{next_hop, interface}]}` |
| `interface` | `{meta: {admin_state, line_state, mtu, description}, records: [{counter, value}]}` |
| `logging` | `{meta: {lines: int, window}, records: [{timestamp, severity, mnemonic, text}]}` |
| `ping` | `{meta: {sent, received, loss_pct, rtt_min, rtt_avg, rtt_max}}` |
| `traceroute` | `{meta: {hops: int, completed: bool}, records: [{hop, address, rtt}]}` |

Rules inherited from `parsers.py` without exception: raise `ParseError` on unreadable non-empty output; count rather than silently drop malformed rows; expose `PARSE_OK` / `PARSE_UNAVAILABLE` / `PARSE_FAILED`; define `record_key` and `volatile_fields` per template so `diff_evidence` and `detect_flaps` work on template output too.

`network_tools.run_template` gains an `_attach_parsed`-equivalent call, mirroring `run_intent` exactly.

**The `logging` parser matters beyond the descent.** `mnemonic` is the field that makes Stage 2 trigger→flow routing a lookup rather than a judgement (D5, growth section). Parse it now even though nothing consumes it yet.

### 5.2 `checks.py` — rung predicates, shared with health

Pure functions over parsed records. No I/O, no device access, no inventory.

```
@dataclass(frozen=True)
class CheckResult:
    status: str          # "healthy" | "broken" | "unevaluated"
    reason: str | None   # why unevaluated, or what is broken
    subject: str | None  # the object this verdict is about
    evidence_keys: tuple[str, ...]   # what it read; feeds grounding
```

`status="unevaluated"` is mandatory when the underlying intent's `parse_status` is not `PARSE_OK`, adopting `health.py`'s discipline verbatim: a failed collection must never read as healthy, and must never read as broken either — a rung that cannot be evaluated stops the descent with an explicit "cannot determine", not a false root cause.

Initial checks:

| Function | Reads | Broken when |
|---|---|---|
| `bgp_session_state(evidence, peer)` | `bgp` intent | `state_pfx_rcd` is non-numeric (Idle/Active/Connect) |
| `bgp_transport(evidence, peer)` | `bgp_neighbor` template | connection state not Established |
| `route_present(evidence, prefix)` | `route` template | `meta.found` is false |
| `isis_adjacency(evidence, interface?)` | `isis` intent | zero adjacencies, or named adjacency not Up |
| `interface_state(evidence, name)` | `interfaces` intent / `interface` template | admin up + line down, or error counters above threshold |

**Do not refactor `health.py` into these initially.** Its rules are pinned by tests and are per-device rather than per-object. Instead add a cross-check:

```
tests/test_checks_agree_with_health.py
    For every fixture device at t0 and t1:
      checks.* and the corresponding health rule must not disagree
      (one may be unevaluated where the other is not; neither may say
       healthy where the other says broken)
```

That test is the guardrail that lets the two coexist, and it is what makes a later consolidation safe.

### 5.3 `flows.py` — object-indexed flow definitions

```
@dataclass(frozen=True)
class Rung:
    name: str
    check: Callable[..., CheckResult]
    collect: tuple[CollectStep, ...]   # intents/templates needed before checking
    finding: str                        # terminal finding name if this rung is broken

@dataclass(frozen=True)
class Flow:
    object_type: str          # "bgp_session", "isis_adjacency", "interface", ...
    subject_schema: str       # how to name an object of this type
    descent: tuple[Rung, ...] # ordered, top of stack first
    findings: frozenset[str]  # every terminal finding this flow can produce
```

Seven flows, per D5: `interface`, `isis_adjacency`, `bgp_session`, `ldp_session`, `l3vpn_service`, `device_health`, `topology`. **Build `bgp_session` and `interface` first; declare the others as stubs raising `NotImplementedError`** so the registry shape is fixed without pretending coverage exists.

The `bgp_session` descent, as specified in the deck:

```
bgp_session → transport → route_to_peer → igp_adjacency → interface
```

Terminal findings are values in a closed enum (`peer_idle_transport_blocked`, `peer_unreachable_no_route`, `igp_isolated`, `interface_line_down`, `all_layers_healthy`). Symptoms live here, not as separate flows.

A safety test asserts every `Rung.collect` step names an intent in `platforms.all_intents()` or a template in `PLATFORM_TEMPLATES`, mirroring the existing `test_check_tool_intents_exist_in_the_platform_table`.

### 5.4 `descent.py` — the deterministic walker

```
def run_descent(flow: Flow, device: str, subject: str, *, collector) -> DescentResult
```

Walks `flow.descent` in order. For each rung: run its `collect` steps, evaluate its `check`.

- `healthy` → continue to the next rung
- `broken` → ~~**stop.** This rung's `finding` is the result. No deeper rung runs.~~ **Superseded by Q-017 (OBS-056): continue.** The result is the *lowest* broken rung; the broken rungs above it are the causal chain.
- `unevaluated` → **stop** with `finding="undetermined"` and the reason recorded.

> **This spec was written before Q-017 and was wrong.** Stopping at the first broken rung makes four of five findings unreachable — `RR1 → 10.255.0.12` breaks rungs 1–3, so the walk halts at rung 1 and restates the symptom. The operator confirmed the plan was defective; `cause_not_localised` was added at the same time. **Left visible rather than silently rewritten**, because this document is a pre-build delta spec and what it got wrong is part of what the build learned.

Returns the rung path taken, each rung's `CheckResult`, and the accumulated evidence keys.

**No model is involved.** This is the module that makes the deck's claim true — "every check on that ladder is parse-and-compare."

The collector is injected so a descent can run against `fixtures.load_fixture_evidence` with no lab access. That is what makes §8's acceptance test possible today.

### 5.5 `config_section.py` — the config axis (D16)

Retrieval of intended state, built inside the existing template mechanism.

New templates in `PLATFORM_TEMPLATES["cisco_xr"]`, each with the same reconstruction discipline as the existing ones:

| Template | Rendered command | Parameter type |
|---|---|---|
| `config_bgp` | `show running-config router bgp` | none |
| `config_bgp_neighbor` | `show running-config router bgp neighbor <ip>` | `IPv4AddressParam` |
| `config_isis` | `show running-config router isis` | none |
| `config_interface` | `show running-config interface <name>` | `InterfaceNameParam` |

`show running-config` with no qualifier is **never** added. That prohibition gets its own safety test alongside `test_no_generic_run_command_is_exposed`:

```
test_no_unqualified_running_config_template_exists
```

Device-side filters, if used at all, are literal text inside a template's format string — never a parameter. `tests/test_safety.py::test_every_template_format_string_is_free_of_banned_snippets` already forbids `|` in a format string, so a pipe cannot be introduced without deliberately changing a safety test. Leave it that way: prefer section scoping over piping. IOS-XR's `| file disk0:/...` is a write, which is precisely why the existing test bans the character.

Two functions beyond retrieval:

```
resolve_inheritance(parsed_config) -> dict   # expand neighbor-group / session-group / af-group
project(parsed_config, subject) -> dict      # global attrs + this subject + referenced policy names
```

`resolve_inheritance` is the one the design document flagged as non-negotiable: an unresolved `use neighbor-group RR-CLIENT` forces the model to guess an expansion from its majority-dialect prior. Resolution is deterministic and removes the guess.

`project` is D16's fifth reduction and the reason the model reads a diff rather than a document.

### 5.6 `investigation.py` — gate, grounding, and the runner

> **SUPERSEDED 2026-08-19.** The decision schema below carries a `finding`
> field, which the approved design in `reasoning-gate.md` deliberately makes
> unrepresentable — a model may request more evidence and may never return a
> verdict. This section is retained as the pre-build sketch it was; read
> `docs/design/reasoning-gate.md` for what was actually built.


Three concerns, one module because they share the run record.

**The gate.** Called only after the descent completes.

```
GATE_DECISION_SCHEMA = {
  "oneOf": [
    {"decision": "sufficient", "finding": <enum of flow.findings>, "subject": <str>},
    {"decision": "narrow",     "target": {"type": <enum>, "id": <str>}}
  ]
}
```

Two hard constraints, both enforced in code, not prompt:

1. `target.id` must appear in `candidates(evidence)` — the set enumerated by code from parsed records already collected. A model naming an unobserved interface is refused with a structured error, exactly as a bad template parameter is.
2. `decision="sufficient"` additionally requires that `finding` matches the descent's own terminal finding. If the model claims sufficiency with a finding the deterministic walk did not produce, it is overridden to `narrow`. This is D7's asymmetry: the cheap error is an extra pass; the expensive one is a wrong conclusion.

**Grounding.**

```
def check_grounding(report: Report, evidence_keys: frozenset[str]) -> GroundingResult
```

Every claim in `report.observations` carries an `evidence_key`; every claim in `report.interpretations` cites one or more observations. An uncited claim fails, and a failed grounding check means the report is not emitted — the run returns the descent result and the failure, not the model's prose. Recommendations are exempt from citation but are explicitly labelled as the model's and as requiring a human.

**The runner.**

```
def investigate(question|trigger, *, scope, flow=None, max_narrow=1) -> InvestigationResult
```

Order: resolve scope → select flow (model, or lookup from a syslog mnemonic) → run descent → gate → optional narrow pass → model writes the report → grounding check → emit.

`agent_loop.py` is not deleted. It remains the general-purpose bounded loop for open questions that do not map to a flow. `investigate()` is the constrained path; the CLI should default to it and fall back to `agent_loop` only when flow selection finds no match.

---

## 6. Changes to existing modules

| Module | Change | Risk |
|---|---|---|
| `network_tools.py` | Attach parsed data to `run_template` results, mirroring `run_intent` | Low — additive; existing callers ignore the new key |
| `parsers.py` | Export `ParseError`, `PARSE_*` and the `record_key`/`volatile_fields` protocol for reuse by `template_parsers` | Low — refactor without behaviour change |
| `templates.py` | Four `config_*` templates | Low — the mechanism is unchanged; only new rows |
| `mcp_server/server.py` | Consolidate ~22 tools to the five stage-shaped ones plus `assess_lab_fabric_health` | **Medium — see below** |
| `cli.py` | `nettools investigate <device> <subject> [--flow ...]` | Low — new subcommand |
| `evidence_budget.py` | Apply the budget to config sections; count projected size before send | Low — same mechanism, new input |

**On the MCP consolidation.** Do it last, and do it as an addition followed by a deprecation, not a replacement. Add `get_config_section`, `get_status_summary`, `get_status_detail`, `get_logs`, `get_topology` alongside the existing tools; migrate; then remove the per-command tools in a separate commit with its own test run. `save_lab_snapshot` and `pin_lab_golden_snapshot` should be removed from the MCP surface entirely rather than consolidated — they are operator actions, and by D11 they were never model decisions.

---

## 7. Build order

Each phase is shippable and testable on its own. Phases 9–11 need no lab access at all — the committed fixtures are enough.

| Phase | Work | Acceptance |
|---|---|---|
| **9** | `template_parsers.py` + `run_template` parse attachment | Every template parser round-trips real captured output; `PARSE_FAILED` on garbage, never a silent empty parse |
| **10** | `checks.py` + the agreement test with `health.py` | `tests/test_checks_agree_with_health.py` green across all 9 devices × t0/t1 |
| **11** | `flows.py` + `descent.py`, `bgp_session` and `interface` only | The §8 acceptance test passes against fixtures |
| **12** | `config_section.py` — templates, inheritance resolution, projection | `test_no_unqualified_running_config_template_exists`; projection of RR1's BGP config stays under the per-intent budget |
| **13** | `investigation.py` — gate, grounding, runner | Gate refuses an unobserved target; grounding blocks an uncited claim |
| **14** | MCP consolidation | Manifest is five tools; `test_no_generic_run_command_is_exposed` still green |
| **15** | Stage 2 groundwork: `mnemonic → flow` lookup table | Table covers every mnemonic present in captured `show logging` output |

Phase 9 is the unblocker. Nothing downstream is possible without it, and it needs no design decisions — only careful parsing of output that is already captured.

---

## 8. First vertical slice

The lab is genuinely broken, and the committed fixtures capture it. That is an asset, not a defect: it means the first descent can be validated end to end today, offline, against real output.

From `tests/fixtures/cisco_xr/RR1/t0/show-bgp-summary.txt`:

```
10.255.0.11  ...  15:35:34  0
10.255.0.12  ...  02:47:34  Idle
10.255.0.13  ...  15:35:24  0
10.255.0.14  ...  03:35:18  Idle
```

Two peers Idle, two established. And `PE2`/`PE4` carry zero IS-IS adjacencies — recorded in `inventory/lab.yaml` as `expected: {isis_adjacencies: 0}`, with the file's own comment flagging that this is baked-in brokenness rather than a healthy target.

**Acceptance test for Phase 11:**

```
Given  fixture evidence for RR1 at t0
When   investigate(device="RR1", subject="10.255.0.12", flow="bgp_session")
Then   the descent stops at a named rung
And    the finding is a member of the bgp_session flow's finding enum
And    no rung below the stopping rung was collected
And    every CheckResult carries the evidence keys it read
And    no model call occurred
```

The last two clauses are the point. If that test passes, the deck's central claim is demonstrated rather than asserted, and the model has not yet been involved at all.

Note that the descent for `10.255.0.12` will need `bgp_neighbor` and `route` template output that the fixtures do not currently contain. Phase 9 should extend `nettools capture` to include one `bgp_neighbor` and one `route` capture per device so the descent has a complete offline path. Capture against the *current* broken state deliberately — a fixture set that only covers healthy devices cannot test a descent whose entire purpose is finding the broken rung.

---

## 9. Test plan

New test modules, following the existing naming:

| File | Covers |
|---|---|
| `tests/test_template_parsers.py` | Round-trip every template parser against captured output; malformed input raises rather than returning empty |
| `tests/test_checks.py` | Each check's three outcomes, including `unevaluated` on a failed parse |
| `tests/test_checks_agree_with_health.py` | The cross-check in §5.2 |
| `tests/test_flows.py` | Every rung's collect steps resolve; findings are closed; flow registry is complete |
| `tests/test_descent.py` | Stops at first broken; never collects below the stop; `unevaluated` halts rather than descending |
| `tests/test_config_section.py` | Inheritance expansion; projection size; the unqualified-config prohibition |
| `tests/test_gate.py` | Refuses unobserved target; overrides false sufficiency |
| `tests/test_grounding.py` | Uncited claim blocks emission |

Existing tests that pin behaviour the new code must not change — treat a failure in any of these as a design error, not a test to update:

- `test_refuses_unapproved_commands_before_loading_credentials`
- `test_refuses_another_platforms_command_without_credentials`
- `test_commands_do_not_leak_across_platforms`
- `test_no_generic_run_command_is_exposed`
- every test in `tests/test_template_security.py`

---

## 10. Open items this LLD does not resolve

1. **Whether `checks.py` and `health.py` eventually merge.** The agreement test makes coexistence safe; consolidation is a later decision with real risk to the passing suite.
2. **Whether `investigate()` or `agent_loop()` is the CLI default.** Recommendation is `investigate` with fallback, but that should be decided after the first descent runs against the live lab, not before.
3. **Model choice for the gate.** The gate's typed-output requirement is the binding constraint. `agent_loop` is Anthropic-only today because a 9B local model was judged unreliable for tool calling; the gate is a strictly easier task than free tool calling (one schema, two shapes), so it may be within reach of a local model where the loop is not. Worth measuring rather than assuming — and grammar-constrained decoding remains the fallback.
4. **Fixture capture policy for config sections.** Configuration output is more sensitive than status output. Decide what may be committed before Phase 12 captures any.
