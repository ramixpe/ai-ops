# Backlog

Work beyond MVP-0, staged. Nothing here is scheduled — this is the ordered list of what exists to be done, so that finishing MVP-0 does not end in an empty room.

**MVP-0 is not in this file.** It lives in `BUILD-PLAN.md` as tasks T-001 to T-035. Nothing below starts until the findings log from that build has been reviewed.

**Sizes** are rough: `S` a day or less · `M` a few days · `L` a week or more · `?` unknown until a discovery item resolves.

**Every item names the decision it comes from**, so its rationale is one lookup away in `docs/design/design-thinking.md`.

---

## The governing rule

> Over-engineering is building *N* of something before validating one.

Which is why the backlog is deliberately shallow in places it could be deep. There is one flow, not seven. One platform, not three. One prompt library, not a prompt framework. Each of those becomes N only after its one has been proven against the real fabric.

---

# MVP-1 — the adaptive layer

MVP-0 proves the deterministic descent. MVP-1 adds the parts where the model gets to influence what happens next, each fenced by a typed contract.

| ID | Item | Why | Depends on | Size | Decision |
|---|---|---|---|---|---|
| **B-101** | **The reasoning gate** — typed decision object, two shapes only | The model asks for more evidence without being able to invent a target | MVP-0 complete | M | D7 |
| **B-102** | **Candidate enumeration** — code derives narrowing targets from observed objects | The half of the gate that stops "the model chooses" becoming "the model invents" | B-101 | S | D7 |
| **B-103** | **Narrowing pass** — collect detail for the named object, re-enter reasoning | Completes the funnel; bounded at one pass until evidence says otherwise | B-101, B-102 | M | D6 |
| **B-104** | **Config axis** — `config_section.py`, section templates, projection | Observed-vs-intended is what turns a finding into "this is wrong" | MVP-0 complete | M | D16 |
| **B-105** | **Inheritance resolution** — expand `neighbor-group` / `session-group` / `af-group` | Unresolved inheritance forces the model to guess an expansion from its majority-dialect prior | B-104 | M | D16 |
| **B-106** | **Intent-vs-observed diff** — reconcile the two axes in code, hand the model the diff | The real answer to the context problem: the model reads the reconciliation, not the documents | B-104 | M | D16 |
| **B-107** | **Flow: `isis_adjacency`** | The most-shared dependency — every protocol above it descends through it | MVP-0 complete | S | D5 |
| **B-108** | **Flow: `device_health`** | Wraps the existing `health.py` verdicts as a flow, so "is PE1 ok" has an entry point | B-107 | S | D5 |
| **B-109** | **Flow: `ldp_session`** | Completes the MPLS control plane | B-107 | M | D5 |
| **B-110** | **Flow: `l3vpn_service`** | The one object type whose identity is not obvious — needs the naming scheme first | T-006 finding | M | D5 |
| **B-111** | **Flow: `topology`** | Multi-device; the fabric's LLDP data is self-contradictory, so this one must report disagreement rather than assert links | T-006 | M | D5 |
| **B-112** | **Free-text flow selection** | Lets a human ask "why is BGP down on PE2" instead of naming the flow | B-101 | S | D5 |
| **B-113** | **MCP tool consolidation** — ~22 tools to five stage-shaped ones | The manifest currently grows with the catalogue; this is D10/D11's failure mode live in the repo | MVP-0 complete | M | D10, D11, D12 |
| **B-114** | **Gate model evaluation** — can a local model emit the typed decision reliably? | Decides whether the reasoning path can be fully local. Grammar-constrained decoding is the fallback | B-101 | M | D7 |
| **B-115** | **Comprehensive health-check pipeline** | Breadth-first collect, no diagnostic question, summarise. The safest possible use of a model — pure summarisation of validated evidence | MVP-0 complete | M | Part 7 open item 4 |

**Sequencing note.** B-104 to B-106 (the config axis) and B-101 to B-103 (the gate) are independent. If the MVP-0 review shows the descent frequently ending in `undetermined`, do the config axis first — the missing evidence is more likely intent than depth.

---

# Stage 2 — event-driven

The agent stops being asked and starts being woken. The architectural change is small; the assurance change is not, because the boundaries now hold with nobody present.

| ID | Item | Why | Depends on | Size | Decision |
|---|---|---|---|---|---|
| **B-201** | **Trigger intake** — Alertmanager webhook receiver | Alertmanager already does grouping, dedupe, inhibition and silencing better than we would build it | T-005 finding | M | D4 |
| **B-202** | **Mnemonic → flow lookup** | Flow selection for event-driven runs is a table lookup, not a model judgement. Syslog mnemonics are stable, enumerable identifiers | T-004, T-015 | S | D5 |
| **B-203** | **Operational memory: schema and writer** | `{timestamp, device, object, event_type, from_state, to_state, evidence_ref}`, written by code from validated envelopes | MVP-1 complete | M | D14 |
| **B-204** | **Operational memory: query surface** | "Has this happened before, how often, when last" — the fourth evidence axis | B-203 | M | D14 |
| **B-205** | **SQLite as the memory backend** | Files cannot answer "every interface that flapped five times this week". The flag already exists; this makes it the default for memory | B-203 | S | D14 |
| **B-206** | **Loki-backed `get_logs`** | Time-bounded, label-filtered, cross-device, and it works when the device is unreachable — which is when the timeline matters most | T-004 finding | M | D8 |
| **B-207** | **Variance experiment** — fire the same event ten times, measure tool-sequence variance | Decides whether the agent loop is safe on top or investigations need constraining sooner than planned. **Run before Stage 2 ships, not after** | B-201 | S | D4 |
| **B-208** | **Stage 2 property suite** | The full guardrail list must hold with no human present. This is the gate on the transition, not a follow-up | B-201 | M | D19 |
| **B-209** | **Report relay hardening** — threading, per-object grouping, silence windows | A relay that posts every event unfiltered gets muted within a week | T-035 | S | interfaces.md |
| **B-210** | **Operator knowledge in the descent** | Known conditions stop being rediscovered on every run — "PE2's zero adjacencies are baked-in lab brokenness" | B-402 | S | D14 |

---

# Stage 3 — standard procedures

The write path. Everything here is gated on identity, and identity does not exist yet.

| ID | Item | Why | Depends on | Size | Decision |
|---|---|---|---|---|---|
| **B-301** | **Identity provider integration** | Verified SSO/OIDC or a signed client certificate — something a caller cannot set an environment variable to become. **Nothing else in Stage 3 starts without this** | — | L | D2, interfaces.md |
| **B-302** | **Procedure definitions** — ordered steps, preconditions, rollback plan | The human-authored artifact the model may select but never compose | B-301 | M | D2, D4 |
| **B-303** | **`propose_procedure`** — MCP tool returning a plan, never a result | Anything behind MCP is callable by the model, so MCP carries proposals only | B-302 | M | D12 |
| **B-304** | **Approval gate** | Execution triggered by a human or a deterministic policy engine reading an approved proposal — outside the model's reach entirely | B-301, B-303 | M | D2, D12 |
| **B-305** | **Execution engine** | Runs the approved procedure, verifies preconditions, executes rollback on failure | B-304 | L | D4 |
| **B-306** | **Post-action verification** | Re-run the descent after execution and confirm the finding cleared. An action whose effect is unverified is not an action, it is a hope | B-305 | M | D6 |
| **B-307** | **Write-path audit** | Who proposed, who approved, what ran, what changed, what rolled back | B-305 | M | D19 |

**The ceiling stands.** There is no Stage 4. The agent executes known procedures and hands over; it does not diagnose novel failures and act on them.

---

# Cross-cutting

Not tied to a stage. Several are cheap enough to slot into any gap.

| ID | Item | Why | Depends on | Size | Decision |
|---|---|---|---|---|---|
| **B-401** | **Juniper platform** — command lists, TTP templates, schema conformance tests | The payoff test for the whole architecture: it must touch command lists, templates and tests, and must not touch flows, prompts, the tool surface or the model | MVP-1 | M | D9, Part 6 |
| **B-402** | **Operator knowledge notes** — structured `notes:` per device/object | Human-authored, so pre-approved by construction. Already exists informally in `inventory/lab.yaml` comments where no code can read it | — | **S — do this early** | D14 |
| **B-403** | **`checks.py` / `health.py` consolidation** | The agreement test makes coexistence safe; merging is a later decision with real risk to a large passing suite | B-107 | M | LLD §5.2 |
| **B-404** | **Line accounting retrofit for `parsers.py`** | The six hand-written parsers predate the completeness rule. Deliberate inconsistency, scheduled rather than ignored | MVP-0 | M | BUILD-PLAN §0.10 |
| **B-405** | **Prompt library expansion** | `flow_select`, `health_summary`, and whatever MVP-1 needs. Each versioned, each with golden tests | MVP-1 | S | D18 |
| **B-406** | **gNMI telemetry as an evidence source** | Streaming counters give trend where `show` gives a point. Enrichment, never a replacement — the device wins on current state | Stage 2 | L | D8 |
| **B-407** | **Session memory** — multi-turn follow-up | Only useful at Stage 1 where a human is present. Genuinely small | MVP-1 | S | D14 |
| **B-408** | **Active probe budgeting** | Ping and TCP checks are already gated; at Stage 2 an event storm needs a rate limit, not just an on/off switch | Stage 2 | S | D13 |
| **B-409** | **Scale test** — descent against a fabric an order of magnitude larger | The tool surface and flow count are device-count independent by construction. Prove it | MVP-1 | M | Part 6 |
| **B-410** | **Runbook and on-call handover** | What an engineer does when the agent is wrong, and how they turn it off | Stage 2 | S | D3 |

---

# What is deliberately absent

Recorded so their absence reads as a decision.

| Not here | Why |
|---|---|
| Vector search over vendor documentation | Chunking destroys the submode hierarchy that *is* the information, and the retriever becomes a contamination vector |
| A generic `run_command` tool, in any form | Moves the safety decision to runtime. The whole architecture exists to avoid this |
| Model-written memory | Reads its own hallucinations back as evidence and compounds them |
| Autonomous remediation of novel failures | There is no Stage 4 |
| Fine-tuning | Not a fix for an output-space control problem. Revisit only if grammar-constrained decoding also fails |
| A workflow engine on top of the read path | Diagnosis is adaptive; a deterministic graph is an automation script |
| Multi-tenancy / per-user scoping | Single-operator deployment. Would need B-301 first anyway |

---

# The next three

If the MVP-0 review goes well, these are the three to start — chosen because each is small, independently valuable, and unblocks something larger.

1. **B-402 — operator knowledge notes.** Smallest item in the backlog, human-authored so zero risk, and it stops the agent rediscovering known lab brokenness on every single run.
2. **B-107 — the `isis_adjacency` flow.** The second flow is where the flow abstraction is proved or found wanting, and IS-IS is the layer every other descent passes through.
3. **B-101/B-102 — the gate.** The first place the model influences control flow. Small in code, large in what it validates.

Deliberately not first: B-113 (MCP consolidation) has the highest regression risk in the backlog, and B-401 (Juniper) is the payoff test — run it when there is something worth proving, not while the shape is still moving.
