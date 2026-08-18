# Stage 2 architecture — brainstorm and decision record

**Status: BRAINSTORM. Nothing here is built or committed as a decision unless a
line says so explicitly.** This document exists to (a) capture a live design
discussion so it is not lost, and (b) be readable cold by an external reviewer
who has never seen the whiteboard. It records the operator's proposals, the
orchestrator's analysis, and what is decided, proposed, or still pending —
kept apart on purpose.

Source: two whiteboard photographs and a working session, 2026-08-18.
Provenance convention: **[OPERATOR]** = the operator's proposal; **[ANALYSIS]**
= the orchestrator's read; **[DECIDED]** / **[PROPOSED]** / **[PENDING]** =
status. The existing system this builds on is `ios-xr-nettools` as of commit on
`feat/investigation-layer` — 68 backlog items DONE, MVP-0 shipped, one blind
model trial and two MCP model trials on record.

---

## 0. The one-paragraph picture

The human talks to an LLM (local via LM Studio, or hosted). The LLM has one
**MCP hub**, and the hub fans out to a set of dockerised backends: the built
`nettools`, plus `n8n` flows, a graph DB (neo4j), an inventory source
(NetBox), an observability face (Grafana + Loki/syslog), and a documentation
store. A **new cache layer** (Redis / SQL / neo4j) sits between the tools and
the live network so that expensive, slow-changing state is not re-collected on
every question. Syslog is the cache-invalidation signal. The network fabric is the thing
being observed — and, new as of 2026-08-18, **history is read as evidence too**:
utilisation, CPU and packet-drop trends from Grafana/InfluxDB and historical
logs, feeding the wide context step (§2.4a). The whole thing is targeted at "first TS agent → MCP
→ production ready," with a Stage II beyond it.

---

## 1. The orchestration question — who sits on top

### 1.1 The three options as drawn [OPERATOR]

- **Option A** — the agent picks `nettools` directly.
- **Option B** — the agent picks an `n8n` flow; only flows touch `nettools`.
- **Option C** — the agent picks *either* an `n8n` flow *or* a `nettool`, case
  by case.

Marked on the board: **not decided**, three stars.

### 1.2 The reframing the second photo forces [ANALYSIS]

In the full architecture, **`n8n` is not above or below `nettools` — it is a
peer backend behind the same MCP hub, one of seven.** The agent picks from a
single menu that contains both. So the live question is not a stack ordering;
it is **what belongs on that menu, and what rule decides which entry answers a
given question.** That is Option C — but Option C is only safe if the choice
between a flow and a tool is *principled and encoded*, not a free weighing the
model does per call. A free choice re-opens exactly the "the model authors
control flow" surface the whole build closes.

### 1.3 The dimension that makes Option C principled [OPERATOR, 2026-08-18]

> **Flows are the WIDE / first step of an investigation. Special tools are
> called at the NARROW path — the specific, deep, special investigation.**

This is the missing axis, and it resolves the ambiguity in "flow OR tool."
They are not competitors chosen by preference; they occupy **different phases
of the same investigation**:

| | FLOW (wide) | TOOL (narrow) |
|---|---|---|
| Phase | First step — frame the problem | Deep step — answer a specific rung |
| Question | "What is the shape of this? How many sessions, which are down?" | "Why is *this* one down?" |
| Existing analogue | `audit`, `analyze --fabric`, `health --all`, breadth-first collection | `investigate`, `get_bgp_neighbor`, a single rung check |
| Output | Facts gathered and framed for the model | One verdict from parsed fields |

**Why this is more than a labelling.** The descent this project already runs
is *itself* wide→narrow: collect the whole evidence epoch broadly, then walk
one specific dependency ladder. The new dimension makes the orchestration layer
**fractal with the core** — the same wide-then-narrow shape at the agent's menu
that already exists inside a single `investigate`. A flow gathers and frames; a
tool descends. The agent's job stays *select and fill*: it selects the wide
flow first, reads what it returns, and then selects the narrow tool the
evidence points to. It never authors either.

### 1.4 The orchestrator's recommendation [ANALYSIS — PROPOSED, not decided]

Adopt **Option C, split by the wide/narrow dimension**, with one addition drawn
from this week's measurements:

1. **Reads and diagnosis stay deterministic.** A flow may gather and an
   `investigate` may descend, but neither the flow nor the model composes the
   diagnosis. This is unchanged from MVP-0 and is the trust story's floor.
2. **Anything that ACTS on the world goes behind a declared flow, always** —
   notify, ticket, annotate Grafana, schedule, escalate. The agent may *select*
   "open an incident for this finding" as a menu item; it never composes the
   steps. Rationale is measured, not stylistic: **B-495 showed capability is not
   monotonic** — the 31B model fixed the 4B's three failure modes and introduced
   a new one, firing an *unprompted active probe* (§12.3). Initiative scales
   with capability, and the model that probes unprompted today composes an
   unprompted notification tomorrow. Side effects belong behind declared flows
   for the same reason probes went behind a gate (B-493).
3. **Nobody is "on top."** The core (flows + nettools) is in the middle; the
   agent and n8n are both doors into it, and n8n is additionally the *hands*
   that act on the world. The `route-event` path already proves a door that
   reaches the core with no model in it at all — that shape is kept.

**This is testable before it is committed.** B-494's model-eval harness scores
a menu offline against fixtures. A mixed wide/narrow menu (Option C) can be
scored against a flat tool-only menu (Option A) with no lab, and the
staged-vs-classic A/B (B-479 / B-113, Q1 still owed during round 6) is the same
question wearing MCP clothes. **Recommendation: seal a prediction and measure,
rather than decide on taste.**

---

## 2. The full Stage 2 architecture

### 2.1 The MCP hub and its backends [OPERATOR]

The LLM holds one MCP client. MCP fans out to (each a docker container):

| Backend | Status on the board | What it is |
|---|---|---|
| `nettools` | built | the deterministic read/diagnose core |
| `neo4j` | roadmap | topology as a graph — path and blast-radius queries |
| inventory (NetBox) | roadmap | the source-of-truth device/interface inventory |
| `n8n` flows | roadmap | declared workflows; wide steps and all side effects |
| Grafana | roadmap | dashboards / metrics face |
| Loki + syslog | roadmap | log aggregation and the event stream |
| documentation | roadmap | knowledge store (vector DB **or** normal DB — undecided) |

### 2.2 The new cache layer [OPERATOR — the biggest new element]

A **Cache DB** layer (Redis + SQL + neo4j, dockerised) sits between the tools
and the live network. The strategy, from the intent note on the board:

> A user's question plus the device's **running config** tells us *how many*
> BGP sessions, IS-IS adjacencies, or anything else there *should* be. If that
> config was **collected recently AND no syslog shows a config change since,
> then no re-collection is needed** — the cache serves it. **Operational status
> is still collected at every trigger.**

[ANALYSIS] **This is the strongest idea on the board, and it closes three open
problems at once:**

- **D16's context-budget problem.** Config is the single largest, slowest-
  changing, most expensive thing to collect. Caching it and invalidating on a
  syslog config-change event is exactly the right shape for network state:
  config changes are discrete, logged events; status changes continuously.
- **§6.4's MCP latency finding.** The MCP path measured ~84 s where the direct
  library took ~33.5 s for the same nine devices. A cache serving unchanged
  config removes most of that cost from the common path.
- **B-106 (intent-vs-observed diff) falls out for free.** The config gives the
  *expected* set — how many sessions should exist. Status collection gives the
  *observed* set. The diff between them is the reconciliation D16 called "the
  real answer to the context problem," and here it emerges from the cache
  architecture rather than being built as a separate feature.

[ANALYSIS] **The one thing that must not be gotten wrong.** A cache re-opens the
exact trust problem the evidence-epoch (B-436) was built to close: **stale data
presented as fresh.** The epoch exists so symptom and cause are read in the same
window; a cached config served alongside fresh status is, by definition, two
reads from two different times. The rule that keeps the cache safe:

> **A cached value carries its collection timestamp, and its age counts against
> the coherence skew bound exactly as a live read's staleness would. The cache
> is epoch-aware or it is a silent incoherence generator.**

Get this right and the cache is free. Get it wrong and it reintroduces, without
any alarm, the precise failure `epoch.py` was written to detect. This is the
first thing to specify and the first thing to test.

### 2.3 The graph DB (neo4j) [OPERATOR: roadmap]

[ANALYSIS] The right tool for the question the flat inventory cannot answer:
*"what depends on this link,"* *"what is downstream of PE2,"* blast-radius and
path queries — which `blast_radius` (next-level.md) and the wide flows both
want. Appears twice on the board (a backend *and* part of the cache layer),
which fits: it is both a queryable topology store and a cache of derived
structure.

**The risk, and the rule.** A graph DB is a *second* store of topological truth
alongside `inventory/lab.yaml` and `learn-topology`'s derived expectations. Two
authored sources of topology that can disagree is OBS-103's shape exactly (the
LLDP-hostname drift). So:

> **neo4j is DERIVED, never authored. It is a projection of `learn-topology`'s
> parsed evidence into a graph — a view, not a database someone edits.** A
> derived graph is a safe convenience; an authored one is a third thing that
> drifts.

### 2.4 The observability face — Grafana + Loki/syslog [OPERATOR: roadmap]

[ANALYSIS] Two directions, and they have different trust profiles:

- **Syslog as INTAKE** — the event stream that triggers investigations and
  invalidates the cache. This is the `route-event` path already built (B-480),
  scaled onto Loki. Deterministic, no model, already the safest door.
- **Grafana as SINK** — annotating dashboards with findings is a side effect,
  so by §1.4 it is an n8n flow the agent *selects*, never composes.
- **Metrics and logs as EVIDENCE — confirmed a requirement, 2026-08-18
  [OPERATOR].** Not just the network's live state: the design reads **history**
  as an input to diagnosis — utilisation trend, a sudden drop or spike, CPU
  history, packet-drop history (from Grafana / InfluxDB), and historical logs
  (from a log store; the operator named one whose exact product is to be
  confirmed). This is developed in §2.4a because it is a genuinely new axis, not
  a variation of the existing state reads.

### 2.4a The temporal evidence axis — history as a first-class input [OPERATOR, ANALYSIS]

Everything the descent reads today is **point-in-time**: "is the interface up
*now*, is the session Established *now*." The operator's requirement adds a
second axis the build has never had — **history**:

> Utilisation over time (sudden drop, sudden increase), CPU history,
> packet-drop history — from Grafana/InfluxDB — and historical logs from a log
> store, read as evidence *for* a diagnosis rather than as dashboards *about*
> one.

[ANALYSIS] This is important and it changes several things, so it is stated
carefully rather than absorbed:

1. **It is a new evidence type, with a different trust model.** A time-series is
   not a device read the tool made — it is a *collector's record* of past reads.
   We are trusting InfluxDB's history, not the device's answer. That is second-
   hand evidence, and it inherits the collector's gaps: a metric that stopped
   being scraped looks identical to a metric that went to zero. **Absence of a
   sample is not a value of zero**, and the parse/trust layer must distinguish
   them the same way `checks.py` already distinguishes `unevaluated` from
   `broken`.
2. **It naturally feeds the WIDE step, not the narrow one.** "Has utilisation
   been climbing on this link" is context that *frames* a diagnosis — it belongs
   in the flow's first sweep, handed to the model as background, not in a rung's
   verdict. A rung is a yes/no dependency check; a trend is a shape. Keeping
   trends in the wide step and verdicts in the narrow step preserves the
   descent's determinism: the narrow rung still compares parsed fields, and the
   history is context around it, never the thing a verdict turns on.
3. **It interacts with the cache and the epoch in a way that is actually
   easier, not harder.** Point-in-time reads have a coherence *skew* problem
   (two reads from two times). A time-series is *explicitly* timestamped and
   *deliberately* read across time — so it does not fight the epoch, it sits
   beside it. The rule: **a historical series is evidence about a window; a
   descent verdict is evidence about an instant; the report must never let the
   first masquerade as the second** ("packet drops spiked at 14:32" is a fact
   about 14:32, not about now).
4. **It is device-authored-adjacent, so the egress discipline still applies.**
   A log line from the history store is exactly the free text B-467 governs; a
   metric label or annotation is attacker-influenceable the same way an
   interface description is. Historical evidence crosses to a model through the
   same projector, not around it.

**Where this lands in the tiers (§5.1):** history is a *context* source, so it
is read by the wide flows and the focused/context tools — the middle and top of
the gradient — and never by a descent rung. That keeps the one guarantee the
whole build rests on: no verdict is reached from anything but parsed,
point-in-time, device fields.

### 2.5 Documentation store — vector DB or normal DB [OPERATOR: PENDING]

[ANALYSIS] The build already answered a smaller version of this: `knowledge.py`
(B-478) is **grep, not RAG**, for a 1.6 MB corpus, and the reason was explicit
— a retrieval stack adds "an unreviewable, non-deterministic layer between a
question and its source" to save nothing at that size. B-478's own note says
*"if the corpus grows two orders of magnitude, revisit."*

The Stage-2 documentation store may be that larger corpus (vendor docs, RFCs,
runbooks). So the honest position:

> **Normal DB / grep while the corpus is small enough that it answers in
> milliseconds. A vector DB only when measured to be necessary — and even then,
> the citation (`path:line`, or its analogue) must survive retrieval, so the
> model reads *cited sources it can be checked against*, never opaque
> embeddings. Retrieval may rank; the model must still cite what it read, and a
> human must be able to open it.**

Decide with a measurement of the actual corpus, not up front.

### 2.6 The network face and the RR agent [OPERATOR]

The network fabric (a mesh of nodes) is the observed system. The second photo
lists the **nettools protocol expansion** — the narrow adjacency/neighbour
tools to add:

- IS-IS adjacency *(shipped today — B-107)*, LDP, LLDP, RSVP, OSPF, MP-BGP, CDP
- **"RR agent TS"** — a route-reflector-aware troubleshooting agent/flow

[ANALYSIS] This list maps directly onto the four remaining declared-but-
unimplemented flows (B-108–B-111) plus new ones. B-107 having shipped means
**they may now run concurrently** (OBS-167) — but each new adjacency type needs
the same discipline B-107 established: a *real dependency* per rung (LLDP was
correctly refused as a rung because it does not gate adjacency formation), and
a naturally-occurring broken fixture captured before the lab is rebuilt.

---

## 3. What this architecture changes about the existing build [ANALYSIS]

Three items that were "nice measurements" graduate to **load-bearing**:

1. **The staged MCP surface (B-479) and its consolidation (B-113).** Seven
   backends behind one MCP means the manifest a small model reads before it can
   act is enormous. The rewording alone cost +86% (§10); 7× the tools makes the
   staged-vs-classic A/B the difference between a navigable surface and an
   unusable one. Q1, still owed during round 6, is now on the critical path for
   the whole architecture, not just for scoring one prediction.
2. **The cache's epoch-awareness (§2.2).** New work, and the highest-risk work
   in the design, because it can silently defeat the coherence guarantee.
3. **neo4j-as-derived (§2.3).** A discipline to fix before the store exists, not
   after it drifts.

Nothing here weakens the four invariants. The safety boundary, the
credential-free platform resolution, the no-interpolation rule, and "no
unparsed device text reaches a model" all apply unchanged to every new backend
— and the cache, the graph, and the doc store are each a new consumer that must
inherit the guarantee rather than assume it (the lesson CLAUDE.md already
records, and that the MCP boundary re-taught at B-481).

---

## 4. Answered 2026-08-18 — the operator's decisions [DECIDED]

Every §4 pending item was answered in session:

- **"RACE vs P.E.N.E" → resolved: GRACE won.** The prompt-discipline comparison
  is closed, and GRACE is what `prompts/` already implements (Grounding, Role,
  Anchors, Constraints, Expected output). No action; registered as closed.
- **The log and telemetry stack EXISTS and access is acquired.** Discovery
  2026-08-18, verified with live queries, dual-homed on the lab mgmt subnet:
  - `syslog-ng` 4.5.0 (172.20.250.101, 514/udp) → **Loki** 2.9.8
    (172.20.250.103:3100). **All nine routers are logging** — verified via
    `/loki/api/v1/label/host/values`; labels: host/job/severity/source_ip.
  - gNMI → `telegraf` 1.30 (`gnmic` container) → **Prometheus** v2.51.2
    (172.20.250.102:9090) → **Grafana** 10.4.3 (172.20.250.104). **337
    non-internal metrics live today**, including `Cisco_IOS_XR_clns_isis_oper`
    neighbour state (holdtime, uptime, adjacency SIDs) and
    `infra_statsd_oper` interface generic counters — the packet-drop and
    utilisation history of §2.4a is already being collected.
  - **Alertmanager** v0.27.0 (172.20.250.105:9093) — `route_alertmanager`
    (B-480) has a live counterpart on the host.
  - **Postgres 16** runs on the same host — a candidate for the cache layer's
    SQL role rather than a new container.
- **Orchestration: Option C, wide→narrow, side-effects behind flows — DECIDED.**
  Execution directives attached: **spin up an n8n docker** and start building
  flows on it; **draft the initial flow list** worth testing first; **build a
  new MCP** (the Stage-2 hub surface) which the operator will test through LM
  Studio and share logs for selection scoring.
- **The three-tier gradient (§5.1) — CONFIRMED.**
- **The cache is epoch-aware — DECIDED**, with a forward note: later, some
  further fields may be *qualified as always-cached*; qualification is a
  reviewed decision per field, not a default.
- **neo4j is derived — DECIDED.** First implementation: a simple script that
  collects LLDP/CDP/IS-IS and builds the graph. Later: an **ontology layer to
  enrich context** — explicitly on the plan, not in v1.
- **Sequencing: measure-first — DECIDED.** Seal a prediction and score the
  wide/narrow menu with B-494's harness before committing the surface.
- **Process: push to GitHub at every major milestone**, and **retire documents
  that are no longer related** — the cleanup executes as milestone 0 of the
  Stage-2 plan with an explicit reviewed list (evidence-class documents —
  FINDINGS, rounds, reviews, archives — are records, not clutter, and stay).

## 5.1 Refinement — a tool is not always the narrow end [OPERATOR clarification, ANALYSIS]

The operator's "more nettools" note names a class that does not fit a clean
flow=wide / tool=narrow split: **focused tools that both gather context for a
domain AND invoke a specific troubleshooting command.** A per-protocol
adjacency tool is the example — asked broadly it reports the state of every
IS-IS/OSPF/LDP adjacency (context), and asked narrowly it drives one specific
check on one neighbour (troubleshooting).

[ANALYSIS] So wide/narrow is a **gradient, not a binary**, and the honest model
is three tiers rather than two:

1. **Flows — widest.** Whole-fabric or whole-domain first sweep. Frame the
   problem, gather broadly, present to the model. (`audit`, `analyze --fabric`,
   `health --all`.)
2. **Focused/context tools — the middle.** One domain, both readings: "state of
   all IS-IS adjacencies on this device" (context) and "this specific
   adjacency's detail" (troubleshoot). This is the class the operator is
   adding, and it is where most of the protocol expansion lands.
3. **Descent tools — narrowest.** One subject, one rung, one verdict from
   parsed fields. (`investigate`, a single rung check.)

The agent still only *selects and fills* across all three. What changes is the
menu is a **gradient the model walks from wide to narrow**, which is exactly
the shape the descent itself has — and it means the protocol-expansion tools
are not just diagnosis leaves, they are also the context-gatherers the wide
step hands to the model. **This should be confirmed with the operator before
the menu is designed, because it decides the tool surface's shape.**

## 6. What is actually decided coming out of this session

- **[DECIDED, OPERATOR]** The flow/tool distinction gains the **wide/narrow
  dimension**: flows are the wide first step, tools are the narrow deep step
  (§1.3). This is a firm design decision and should update D5/D6 in
  `design-thinking.md` when those are next revised.
- **[DECIDED, OPERATOR]** Juniper is **out of scope** (recorded 2026-08-18;
  `platforms.py` entry retained, safety surface unchanged).
- **[DECIDED, OPERATOR]** The agent now operates the fault injector for
  mechanism rounds (BUILD-PLAN §0.11 as amended); blind trials still use a
  human injector.
- **[READY TO DECIDE]** D13 (passive reads vs active probes) has its evidence
  and awaits a decision on rate limits and the CLI default (OBS-164).
- Everything else in §1–§4 is **PROPOSED or PENDING**, deliberately.
