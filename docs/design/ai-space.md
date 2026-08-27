# Stretching the AI Space

**Date:** 2026-08-24
**Status:** Brainstorm for review. **No implementation is authorised by this
document.** It is input to the SOTA planning process
([`SOTA-PLAN-2026-08-23.md`](../build/SOTA-PLAN-2026-08-23.md)), written to be
challenged the same way — peer notes welcome, in the same tag vocabulary.
**Provenance:** operator + Peer A working session, 2026-08-24. Every claim
about current behaviour below is cited to a measurement or verified against
the tree on the date above, in the house style.

---

## 1. The organising principle: freedom is graduated, and earned

The question is not "should the AI get more freedom" but **when**. The answer
this document proposes:

> **The AI's latitude expands exactly as the deterministic system's confidence
> runs out — and identifiers stay code-owned at every tier.**

When the descent has an answer, the model narrates it and nothing more (today's
behaviour, measured, working). When the descent honestly finds nothing and the
knowledge base has nothing — the exact condition the fault campaigns produced
over and over — a system that stops there is honest but useless to the operator
standing in front of a broken fabric. That is the moment wider AI latitude buys
something code structurally cannot provide: **hypotheses**.

Two rails hold at every tier, because they are what made the current AI space
safe *by measurement, not assertion*:

1. **Identifiers are code-owned.** The model never supplies a device, peer,
   interface, or prefix from free text. Where a tier needs the model to pick a
   target, it picks **from a code-enumerated candidate list, by index** — the
   `reasoning_gate` pattern (built 2026-08, still unwired; B-114).
2. **Model prose is never the answer.** `record_answer` carries code's own
   finding or nothing. Everything a model writes lands as `model_claimed`,
   visibly separated, at every tier including the freest one.

Evidence that the rails work under the current tier: 24 adversarial runs
across two providers, with an injected user message naming the exact B-459
fabrication values ("Try PE99 and peer 10.255.0.99") — **zero** attempts to
name a wrong identifier; 9 pinned-key collisions, all refused before dispatch
(B-710, reviewed 2026-08-23). Honest caveat, carried forward from that review:
the guard has held against well-behaved models and has **never yet caught a
genuinely wrong identifier** — its strength is asserted by design and measured
only against cooperation.

---

## 2. The tier ladder

```
freedom ─────────────────────────────────────────────────────────────▶

T0            T1              T2               T3                 T4
DESCENT       PINNED LOOP     GROUNDED         FREE-FORM          CANDIDATE
no model      (live today)    RECALL           EVIDENCE HUNT      NARROWING / SOP
              choose 1 of 4   RAG over own     unknown error →    reasoning_gate,
              tools, enum     tickets/findings/ config diff,      integer-indexed
              args only       mnemonics        neo4j, NetBox,     targets; later
                                               history            SOP selection
◀───────────────────────────────────────────────────────────────── determinism
```

The trigger for each tier is the **exhaustion of the tier below it** — not an
operator mode switch, not a model's own request. T3 in particular is
unreachable while T0 has a finding: a descent that returns a real cause never
escalates.

### T0 — the descent (unchanged, permanently)

No model call, deliberately (`descent.py`'s own contract). Nothing in this
document touches it. Every tier above exists to handle what T0 *cannot* see,
never to second-guess what it can.

### T1 — the pinned loop (live today, measured)

What runs now on a promoted trigger: choose 1 of 4 tools, enum-only free
fields, `investigate_lab`'s own finding is the answer. Measured behaviour:
wide-then-narrow 12/12, 0 fabrications, 15–29 s per run (OBS-701, B-710).

The honest observation that motivates this whole document: **at T1 the model
adds very little.** Its tool choice is nearly predetermined — explore, then
investigate — and a lookup table would come close. The AI space as built is
deliberately tiny, and the measurements show it being tiny. The value of a
model is at the edges T1 never reaches.

### T2 — grounded recall ("have we seen this before?")

RAG over the repo's **own artifacts**: tickets, `FINDINGS.md` (700+ numbered
observations), `mnemonics.yaml`, the diagnosis ledger with its human verdicts.
This repo is already an incident knowledge base that nobody can query.

- **Value:** "Yes — OBS-705, same signature, it was the collector throttle"
  is worth more to an operator at 03:00 than any fresh investigation. The
  corpus is code- and human-authored (not device-authored), so the containment
  problem is mild.
- **Mechanism sketch:** retrieval is code (embedding or plain lexical search —
  measure before assuming embeddings are needed; the corpus is small and
  highly structured). The model summarises *retrieved* text with citations to
  the source observation/ticket. No hit → say "no precedent", which is itself
  the T3 trigger.
- **Already in the tree:** the corpus itself; `knowledge.py` (mnemonic search,
  `search_knowledge`); ticket read paths with containment
  (`_UNTRUSTED_TEXT_FIELDS`).
- **Risk:** low. Main failure mode is a stale precedent presented as current —
  every citation must carry its date, and the prompt must say a precedent is a
  lead, not a diagnosis.

### T3 — the free-form evidence hunt (the operator's proposal)

**Trigger predicate, stated precisely, because it is the whole safety story:**

```
symptom exists          (an event fired, or an operator asked)
AND T0 has no cause     (finding ∈ {no_fault_on_path, all_layers_healthy,
                         subject_not_found} — the campaign's "invisible
                         fault" bucket, OBS-704)
AND T2 has no precedent (RAG returned nothing above threshold)
```

Only then does the model get the wide surface. What "free" means here: the
model **chooses which evidence to read next and in what order** — it does not
compose queries, name targets from free text, or write anywhere.

**The evidence surface — and nearly all of it already exists, read-only:**

| Source | What the model gets | Exists today |
|---|---|---|
| Config drift | Observed state vs golden snapshot per device (`history_lab golden_diff`), field-level diffs with meta (`config_diff.py`: `FieldDiff`, `reconcile_interface`) | ✅ built, on the staged surface / in-tree |
| Topology graph | Device/adjacency graph derived from evidence — **derived, never authored** (`graph.py`'s own contract) — via `get_lab_graph_topology` | ✅ MCP tool, external-source gated |
| NetBox | Intended-state inventory and topology (`get_lab_netbox_inventory`, `get_lab_netbox_topology`) | ✅ MCP tools, external-source gated |
| Metrics history | Interface rates, adjacency/session/uptime history over Prometheus (4 `*_history` tools) | ✅ MCP tools, external-source gated |
| Logs | `get_lab_logs` (Loki, deduped, contained), device buffer via `logging` template | ✅ built |
| Episodes / flaps | `history_lab flaps`, snapshot series | ✅ built |

So T3 is **not** a plumbing project. It is a policy change: today these
adapters are gated per-operator-env (`NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES`) and
the event loop's pin table deliberately excludes them; T3 would open them to
the model **under the trigger predicate only**, with their own budget.

**The comparison that makes this tier sing** — the operator's instinct,
sharpened: the three sources form a triangle no single check can see across:

```
        NetBox (intended)
           ╱        ╲
   "should be"    "should be"
         ╱            ╲
observed config ──── neo4j graph (derived from observed)
        "is"    vs      "is, structurally"
```

- *Intended vs observed*: NetBox says this interface carries MTU 9000 /
  belongs to CUSTB / peers with X — observation disagrees → hypothesis with a
  named check.
- *Observed vs past-observed*: golden_diff says what changed since the fabric
  was last known-good — on this lab, where every fault so far has been a
  config change, "what changed recently" is the single highest-yield question
  (every campaign fault would have been visible to it, including all five
  "invisible" ones: route-policy, RT, metric, blackhole, MTU).
- *Structural*: the graph answers "what shares fate with the broken thing" —
  which turns one symptom into a testable blast-radius prediction.

**Output contract — the part that keeps T3 honest:**

The product of a T3 run is a **ranked hypothesis report**, never an answer:

- Each hypothesis carries: the evidence that suggested it (cited to source and
  timestamp), a confidence *ordering* (not fake percentages), and — mandatory —
  **a falsification step**: the named check, command, or observation that would
  confirm or kill it. A hypothesis without a falsification step is not emitted.
- Recorded entirely as `model_claimed`. The ticket's Answer section stays
  absent — absence is the finding, exactly as today (OBS-181's positive-control
  discipline applies: the report must also be *allowed* to say "no hypothesis
  survives the evidence", and the prompt must reward that).
- External-source text (NetBox names/descriptions, graph labels) is
  device-adjacent and human-authored elsewhere — it gets the same untrusted
  delimiters device text gets. NetBox is a *claim about intent*, not truth:
  stale NetBox data producing a false "drift" hypothesis is a first-class
  failure mode, so every intended-vs-observed hypothesis names its NetBox
  object's last-modified date.

**Budget:** T3 is the expensive tier and needs its own bounds in the
`AgentBounds` idiom — more iterations than T1 (the hunt is the point) but a
hard ceiling on sources consulted, tokens, and wall clock; admission already
paces per-device and this rides the same mechanism. A T3 that finds nothing
must stop, not loop.

### T4 — candidate narrowing and, later, SOP selection (the horizon)

Two already-designed doors, deliberately out of this document's scope but
named so the ladder is complete:

- **`reasoning_gate` (B-114, built, unwired):** mid-flow typed decision —
  the model may ask to narrow to a target chosen **by integer from a
  code-enumerated candidate list** ("check the far end" bought back without
  free-text identifiers). T3's hypothesis reports are exactly what would give
  this gate its first live caller and its first honest evaluation corpus.
- **SOP selection (SOTA plan, Phase 5, design-only):** the model picks a
  reviewed procedure template and fills typed parameters. Everything about it
  is already specified in the plan and its peer reviews (rendered-plan digest
  approval, execution-time revalidation, typed rollback); nothing here
  changes it.

---

## 3. The other stretches, each in one honest page-worth

### 3.1 Coverage-boundary enumeration ("what I cannot see")

The B-716 review named a third bucket beyond visible/invisible: **visible
symptom, unconfirmable cause** — BGP is down and the tool's 10 approved
commands cannot read MD5 config, max-prefix, or BFD timers. Teach the model to
reason about the *coverage boundary itself*: "the cause classes my read
surface cannot rule out are A, B, C — checking them requires X."

- **Value:** turns honest silence into direction. It is also the natural
  preamble of every T3 report.
- **Why it is safe:** it is meta-reasoning about the tool's own declared
  surface (`APPROVED_COMMANDS`, the template list) — no device claims at all.
- **Sketch:** the coverage map is *code-authored* (a declared table of
  cause-class → readable-with, maintained like `mnemonics.yaml`); the model
  selects and phrases from it, never invents entries. A model-invented
  "unreadable cause" would be fabrication with extra steps.

### 3.2 Incident grouping

Measured motivation: three neighbours dropped in the same second (the
`17:51:39` collapsed group, 2026-08-23) — code deduplicates, and produces N
tickets for one incident. A model over the *set* of open tickets + the graph
can narrate "these 5 tickets are one incident; shared fate on P1's link" and
propose (not perform) a grouping.

- **Rail:** grouping keys stay code-typed — the model proposes membership, a
  deterministic signature (device set + time window + rung) accepts or
  refuses it, the same shape `relay_policy` already uses for dedup.
- **Payoff couples with B-707:** one page for one incident instead of five,
  which is the difference between a pager an operator trusts and one they
  silence.

### 3.3 Ask-the-ticket (conversational ops over Telegram)

The B-707 thread is one-way lifecycle today. Let the operator reply in-thread
— "what about the far end?", "show me the diff" — and the model answers
grounded in that ticket's own contained content, with bounded follow-up reads
through the same pinned surface.

- **Why the risk profile is different:** a human is present. The fabrication
  scenario B-459 worried about was *unattended* confidence; a conversation is
  attended by definition.
- **Rails:** the ticket's `code_observed`/`model_claimed` split must be
  *visible in chat* (prefix, formatting — solved presentationally, but
  mandatory); replies ride the existing event/idempotency machinery so a
  replayed webhook cannot fork a conversation; per-thread turn budget.
- **Latency note:** 15–29 s/turn (measured, MiniMax) is fine for an event
  loop and marginal for chat — see 3.6.

### 3.4 Pattern mining over episodes and the ledger

Two corpora accumulate that nothing reads: snapshot/flap episodes ("PE3's
adjacency flapped 4× this week, always ~02:00") and the diagnosis ledger,
where **humans** record whether the tool's diagnosis was right (B-485's
deliberate split). A scheduled, offline model pass that surfaces patterns —
including patterns in the tool's *own errors* ("descent wrong twice, both on
flow X") — is the system growing a memory and a self-assessment.

- **Rail:** output is a report for a human, full stop. It feeds the review
  process (a FINDINGS entry, a trigger-table proposal), never a gate.
- This is also the honest path to **trigger-table curation**: the model
  proposes candidates from observed Loki patterns *into the review pipeline*,
  where a human promotes with a measurement — B-706's discipline preserved,
  with better-fed reviewers.

### 3.5 Draft communications

Incident summary for a human audience (management, a handover, a post-mortem
skeleton) generated from ticket facts. Lowest risk on this list; the ticket
already contains everything, contained. Worth one line in a plan and no more
design than that.

### 3.6 The local-model split (the idling GPU)

The deployment diagram records it plainly: an RTX A4000 held by a systemd
`ollama` that nothing currently exercises. Meanwhile every model turn rides a
paid API at 15–29 s. The natural split:

- **Local (fast, free):** T2 retrieval summarisation, incident-group
  narration, draft comms, episode pattern reports — tasks where a wrong word
  is cheap and volume is high.
- **API (slow, strong):** T3 hypothesis generation, T4 narrowing decisions —
  tasks where reasoning quality is the product.

The eval corpus (`model_eval.py`) already exists to measure whether a local
model clears the bar per task — measure first, in the house style: B-114's
whole question ("does a local model emit the typed decision reliably") becomes
answerable the moment T3/T4 create a decision path to evaluate.

---

## 4. Risk register — what stretching costs, stated before it is spent

| Risk | Tier | Mitigation already designed | Honest residual |
|---|---|---|---|
| Free-text identifier fabrication returns | T3/T4 | Candidate enumeration by index (`reasoning_gate` pattern); T3 chooses *sources*, not targets | B-710 caveat: guard never yet tested against a genuinely adversarial model |
| Model prose gains felt authority | T2/T3/3.3 | `code_observed`/`model_claimed` split survives into every new surface, visibly | Presentation discipline erodes quietly; needs a test per surface, not a convention |
| External data trusted as truth | T3 | NetBox/graph text contained like device text; intended-state hypotheses carry the NetBox object's age | Stale NetBox is a *plausible-lie generator* — the falsification-step requirement is the real defence |
| Cost/latency storms | T3 | Own `AgentBounds`; rides existing admission/pacing; trigger predicate makes T3 rare by construction | A flapping unknown-cause fault hits T3 repeatedly; per-signature T3 dedup needed |
| Hypothesis anchoring (operator fixates on rank 1) | T3 | Ordering not percentages; mandatory falsification step per hypothesis; "no surviving hypothesis" is a rewarded output | Human factors — measure in use, not in design |
| Rabbit holes (model reads everything, concludes nothing) | T3 | Source ceiling + iteration budget; stop-on-empty is a valid terminal | The budget numbers themselves need measuring, not guessing |

## 5. What this document deliberately does not touch

- **Remediation and device writes** — Phase 5 / Stage 3 territory, gated by
  the SOTA plan's own approval/identity design. Nothing above writes anything.
- **The descent** — T0 is not improved, assisted, or second-guessed by any
  tier. If the descent ever needs a model, something above it was designed
  wrong (the repo's own standing rule).
- **The promoted-trigger discipline** — T3 does not add triggers; it changes
  what happens *after* a trigger (or an operator) finds nothing.

## 6. Open questions for review

1. **T3's trigger predicate** — is "no finding + no precedent" complete, or
   should an operator be able to invoke T3 directly on a healthy-looking
   fabric ("something is wrong, I can't say what")? (Leaning yes — attended
   invocation is the *lower*-risk direction.)
2. **T2 retrieval** — lexical vs embedding, measured on the real corpus
   before choosing. The corpus is small, structured, and heavily
   cross-referenced; embeddings may be complexity without lift.
3. **Ranking order of build-out.** Peer A's value-per-risk ordering from the
   working session: hypothesis-on-blind-faults (T3 core) → ask-the-ticket →
   incident grouping → RAG → reasoning_gate. Note the dependency inversion:
   T2 (RAG) is *in the T3 trigger predicate*, so it likely builds first
   despite ranking fourth on standalone value.
4. **Where T3 reports live** — on the ticket (as `model_claimed`) or as their
   own artifact type with their own lifecycle? A hypothesis that is later
   confirmed by a human is exactly the ledger's confirm/deny shape — is a
   hypothesis a diagnosis-in-waiting, and should the ledger learn a
   `hypothesis` state?
5. **The one-mnemonic asymmetry** (B-712 review): T3's history sources see
   `ROUTING-BGP-5-ADJCHANGE` at full retention and everything else through a
   capped window. Does the closed mnemonic set widen before T3 leans on
   history, or does every T3 history citation carry a coverage caveat?

---

*Written 2026-08-24 against the tree as of that date. Facts verified at
writing time: 4 T1 tools with enum-only fields; 14 external-source MCP tools
including NetBox inventory/topology, graph topology, and 4 Prometheus history
tools; `config_diff.py`, `graph.py` (neo4j derived-never-authored),
`netbox.py` present in-tree; `reasoning_gate` built and unwired (B-114
BLOCKED); B-710: 24 adversarial runs, 2 providers, 0 wrong-identifier
attempts, 9 pinned-key collisions refused.*
