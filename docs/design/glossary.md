# Glossary

Terms used across the design documents, the build plan, and the code. There is one live collision (`intent`) that will cause real bugs if it is not pinned, and several near-misses.

**Rule: the repository's existing vocabulary wins.** It is spelled into the test suite, generated documentation, CLI subcommands, and evidence section keys. Design documents adapt to it, not the other way round.

---

## The collision

### `intent`

**In this repository:** a vendor-neutral name for a *question* — `facts`, `interfaces`, `bgp`, `lldp`, `isis`, `sr`. Keyed as `PLATFORM_INTENTS[platform][intent] -> tuple[str, ...]`. The same intent maps to different syntax per vendor, which is what lets one check run across a mixed fabric.

**Not:** "intended state." That is the **config axis** — see below.

Earlier drafts of `design-thinking.md` used `domain` for this concept and `intent` for intended state. Both were renamed to match the code. If you find `domain` used as an enum of question names in any document, it means `intent`.

---

## Evidence

### `config` axis
Intended state: what the device *should* be, from a configuration section or a source of truth. One of three evidence axes. Retrieved by `get_config_section`, never by an unqualified `show running-config`.

### `observed` axis
Current state, from status commands. What the device *is*.

### `historical` axis
The timeline: logs, bounded by a window and filtered to the object under investigation. When it changed and how often. Preferred source is the log platform (Loki), with `show logging` as fallback.

### `evidence key`
An identifier attached to a piece of parsed evidence so a downstream claim can cite it. The grounding check verifies every claim's key resolves.

### `envelope`
The uniform result shape every tool returns: `{tool, device, status, timestamp, data, errors}`, plus parse keys. `status: "success"` means *transport* succeeded — never that the device is healthy.

---

## Investigation

### `flow`
An investigation scoped to one **object type** — `interface`, `isis_adjacency`, `bgp_session`, `ldp_session`, `l3vpn_service`, `device_health`, `topology`. Seven of them, not hundreds, because flows are indexed by object rather than by symptom.

**Not:** an n8n workflow. Those are orchestration, upstream of the agent and outside its reach.

### `descent`
The ordered walk down the protocol dependency stack inside one flow. **Continues past a broken rung and reports the *lowest* broken one as the cause**, with the broken rungs above it as the causal chain. Stops only on `unevaluated` — nothing below a rung that could not be read is trustworthy. Fully deterministic — no model call anywhere in it.

**Corrected 2026-08-17.** This entry read *"stops at the first broken rung, which is the root cause"* — the pre-Q-017 specification, which Q-017 established was defective and which the code has never implemented. Under it, four of five findings are unreachable: `RR1 → 10.255.0.12` has rungs 1–3 all broken, so the walk would stop at rung 1 and report the symptom it started from. See D6 and `README.md`'s walk table.

**Not:** the agent loop. `agent_loop.py` is a free-form bounded tool-calling loop and remains in place for questions that map to no flow.

### `rung`
One layer of a descent, e.g. `transport` or `route_to_peer`. Carries the collect steps it needs, the check it runs, and the terminal finding it produces if broken.

### `check`
A pure predicate over parsed records, returning a `CheckResult` of `healthy` / `broken` / `unevaluated`. No I/O, no device access, no inventory reads.

**Related but not identical:** a `health.py` rule. Health rules are per-device and produce severities; checks are per-object and produce rung verdicts. They coexist, and `tests/test_checks_agree_with_health.py` pins that they never contradict each other.

### `finding`
A terminal outcome of a descent, from a closed enum on the flow — `peer_not_established`, `transport_blocked`, `igp_isolated`, `all_layers_healthy`, `undetermined`. Symptoms are findings *inside* a flow; they are never flows of their own.

**Not:** a health `severity` (`ok` / `info` / `warning` / `critical`).

### `unevaluated`
A verdict that could not be reached because the underlying parse did not succeed. Never `healthy`, never `broken`. A failed collection must never look like a verdict. Originates in `health.py` and is adopted verbatim by `checks.py`.

### `gate`
The model's typed decision after a descent: `sufficient` or `narrow`. **Not in MVP-0** — the descent is deterministic and terminal. MVP-1.

### `grounding`
The deterministic check between conclusion and report: every claim cites an evidence key, every interpretation cites observations. A failed grounding check means the report is not emitted.

---

## Prompts

### `GRACE`
The prompt framework: **G**rounding, **R**ole, **A**nchors, **C**onstraints, **E**xpected output. Derived from the book's RACE with Grounding added, Constraints promoted out of Context, and Evaluation deliberately omitted — evaluation is the schema validator and the grounding check, not a clause asking the model to mark its own work.

### `RACE`
The book's framework: Role, Anchors, Context, Expected output. Superseded by GRACE for this project's runtime prompts.

### `P.E.N.E.`
The workshop's framework: Persona, Examples, kNowledge/constraints, Evaluation. Referenced in early slides; superseded by GRACE.

---

## Memory

Three distinct things share the word. Keep them apart.

### `session memory`
Conversation turns within one run. Application layer. Stage 1, minimal. This is what the book's Chapter 5 covers.

### `operational memory`
Schema'd events derived by **code** from validated envelopes, keyed by object — `PE2:GigabitEthernet0/0/0/1`, `PE2:bgp:10.255.0.31`. Historical only; the device always wins on current state. Stage 2.

**Never model-authored.** A model deciding what is "significant" and writing prose into memory creates a system that reads its own hallucinations back as evidence.

### `operator knowledge`
Human-written notes about a device or object, version-controlled and reviewed like code — "PE2 and PE4 have zero IS-IS adjacencies; this is known lab brokenness, not a new fault." Pre-approved by construction. Available from Stage 1 and cheap. Currently living informally in `inventory/lab.yaml` comments.

---

## Safety

### `allowlist`
`platforms.APPROVED_COMMANDS[platform]` — an exact-match frozenset checked against the device's own platform, **before** credentials load or a socket opens.

### `canonicalize by reconstruction`
A caller-supplied parameter is never substituted into a command as text. It is parsed into a typed object and the command is rendered from *that object's* canonical string form. `templates.py`'s security model.

### `active probe`
A command that generates traffic from the device — `ping`, `traceroute`. Non-mutating but distinct from a passive read, with its own gate (`NETTOOLS_ALLOW_ACTIVE_PROBES`).

### `frozen file`
A file the build plan forbids modifying because it encodes the safety boundary. Listed in `docs/build/PROCESS.md` §0.5 (formerly `BUILD-PLAN.md`'s Part 0, moved 2026-08-20).

---

## Stages

### `Stage 1`
Read-only, user-initiated. A human asks; the agent answers with evidence. **Current target.**

### `Stage 2`
Event-driven. Syslog, SNMP or an alert wakes the agent; it runs the checks and reports RCA. Nobody is present at invocation, which is why memory and the property tests in `docs/build/PROCESS.md` are gates on this transition.

### `Stage 3`
Standard procedures only — pre-approved actions to cut MTTR, then hand to a human. There is no Stage 4.

### `MVP-0`
Descent to grounded report. No gate, no narrowing, no config axis, no memory. The subject of `docs/archive/BUILD-PLAN.md` (the executed task list) and `docs/build/PROCESS.md` (the rules it operated under).

### `MVP-1`
Adds the gate, the narrowing pass, and the config axis.
