# PLAN — TO PUBLICATION, THEN MVP-1

**Supersedes** `BACKLOG-COMPLETION-PLAN.md`. That plan's Track A found four of five items already done, and Track B halted with zero runnable items. This one starts by fixing the reason.

**Split at publication.** Part 1 makes what exists legible, validated and publishable. Part 2 builds MVP-1. Different work, different risk, and only Part 2 has real parallelism in it.

---

# PART 0 — WHY THE LAST PLAN FAILED, AND WHAT CHANGES

## 0.1 The diagnosis

Both failures had one cause: **the plan was written from item titles rather than item metadata.**

- **Track A** restated four items as open that findings had already measured closed. A backlog entry records what someone thought at filing time; it was read as a record of what is.
- **Track B** scheduled six items whose prerequisite the same document excluded in Part 1. The exclusion list and the track lists were never checked against each other.

Track A cost four items of restated work. Track B cost a whole track. **Both are invisible to a reader who trusts the plan**, which is why the fix has to be mechanical rather than attentional.

## 0.2 The rule, both halves

> **A plan item cites the last finding that touched it, or states that none has. And every item's dependencies are checked against the plan's own exclusion list.**
>
> A plan that excludes a prerequisite in one section and schedules its dependents in another is internally inconsistent in a way no reader can detect without opening the backlog.

The second half is the one the first would not have caught: Track B's items had no stale findings — most had none at all. They were simply unbuildable.

## 0.3 What this means for planning at all

The wide pass measured something real, and it is not what it set out to measure: **the backlog has drifted from the repository.** Nobody would have found that by reading it.

So this plan does not schedule work from the backlog until the backlog has been reconciled against the code. That reconciliation is Gate Zero, and nothing else starts until it is done.

## 0.4 Everything in `BUILD-PLAN.md` Part 0 still binds

Unchanged: frozen files, the delegation policy, the shared-contract HALT, the escalation ladder, §0.12's vacuity rule, §0.13's six faces, §0.14's classification rule, §0.15's protocol exclusion, §0.16's no-turn-ends-on-an-intention.

---

# GATE ZERO — RECONCILE THE BACKLOG

**Blocking. Nothing in Part 1 or Part 2 starts until this is complete.**

For every item in `BACKLOG.md`, produce four fields:

| Field | Content |
|---|---|
| **State** | `OPEN` · `DONE` · `BLOCKED` · `SUPERSEDED` · `CLOSED-AS-MEASURED` |
| **Evidence** | A commit hash, a finding ID, or the word `unverified` |
| **Depends on** | Every prerequisite, with each one's current state |
| **Last touched** | The most recent finding that concerns it, or `none` |

**Three rules for filling it in:**

**An item with no evidence line is `unverified`, not `OPEN`.** That distinction is the whole point — "nobody has checked" and "checked and still open" are different states and were being conflated.

**Verify a tick rather than accepting it.** B-420 was recorded as done and verifying it found it true — which is the outcome that makes the habit look unnecessary and is exactly when it is worth doing.

**Where an item's premise has been measured false, mark it `CLOSED-AS-MEASURED` and keep the measurement.** B-411 and B-437 both shrank on contact; that is a result, not an embarrassment.

**Output:** `BACKLOG.md` rewritten with the four fields, plus a short summary of how many items changed state. That number is itself a finding — it measures how far the backlog had drifted.

---

# PART 1 — TO PUBLICATION

Largely serial. Two things can overlap, and they are named.

## P1.1 · Repository hygiene `[Sonnet 5, can overlap P1.2]`

The former Track C. These were written from what the repository does rather than from backlog titles, so they should survive contact.

| | Item | Note |
|---|---|---|
| **C1** | B-402 operator knowledge notes | Smallest item in the backlog, human-authored so pre-approved by construction. Already exists informally in `lab.yaml` comments where no code can read it |
| **C2** | B-413 remove the banned clause from `TROUBLESHOOTING_PROMPT` | Version bump under the prompt library's own rules |
| **C3** | B-421 clean-environment CI | `--from-fixtures` in a job with no `.env`, no credentials, no network. The demo's value is that it needs nothing, so nothing is the only environment that tests it |
| **C4** | B-457 withheld-paraphrase metric | A field nobody aggregates is not detection. Tool-health signal, never the network pager |
| **C5** | B-458 error-string residual | As filed |
| **C6** | B-461 numbered-rung detectability | **Shipped. Do not extend it into anything describable as enforcement** — at a boundary we do not own, detectability is the whole available defence |

## P1.2 · The three injection rounds `[OPERATOR — needs the lab]`

The highest-value work remaining, and the only work that produces new evidence rather than new code. Run in this order:

**Round 7 — B-462, first.** Does a down port persist as an LFA backup mid-reconvergence? This is the known soft spot in code that shipped this week, and the corpus cannot settle it — the fixtures contain no device with a down physical port and a surviving route. Round 5 established the ladder gets read mid-convergence, so this is live rather than theoretical.

**Round 8 — B-463.** AS, MD5 or hold-timer mismatch on one neighbour. Closes the last rung-separation boundary and replaces the build's last composed fixture with a captured one.

**Round 6 — B-440.** Real BGP fault plus an unrelated down physical interface — reviewer B's trust-loss scenario. Now more interesting than when filed, because B-456 made the interface rung path-scoped and the unrelated interface may no longer be on the path at all.

**Protocol per round, unchanged and binding:** seal the prediction and **push** it before the fault; the prediction states the fault it assumes and the harness is read against that statement; the injector is never the diagnostician; archive the full payload, not the finding.

**Why before publication:** four blind trials is a weak evaluation section. Seven across five rungs, including a predicted failure reproduced exactly, is a materially better one — and B-462 validates shipped code rather than adding a feature.

## P1.3 · MCP re-test `[OPERATOR — needs LM Studio]`

Same three questions against the reworded 21-tool surface, scored against `MCP-EXPERIMENT.md` §9's pre-registered prediction.

**Content versus contrast.** If selection holds, content. If it degrades toward chance, contrast — and the reworded surface destroyed the signal it was built on.

Capture the tool-call sequence, not only the answers.

## P1.4 · B-113 consolidation — 21 tools to 5

**Only after P1.3.** Consolidating first does not merely confound §9, it makes the prediction unfalsifiable: contrast is a property of a 21-tool surface, and at five tools there is no structure in which "the outlier was selected" can be true or false.

The context-cost argument stands on its own and needs no measurement: descriptions went 5,961 → 11,107 characters, +86%, sent on every tool-list call.

## P1.5 · Publication readiness

**H1 secrets audit is complete and CLEAN** — 160 commits, compared by value, no secret in history, `.env` never committed. Two records stand: the transcript exposure is unrevocable so **rotation is still required**, and `clab` in 18 committed fixtures is an H3 matter.

| | Item |
|---|---|
| **H2** | Every claim in `README.md`, `CLAUDE.md` and the design docs audited against what is measured. The peer review found five claims exceeding their evidence; that audit was for MVP-0 and the repository has moved |
| **H3** | Fixture review — permanent once published. Includes the `clab` username question |
| **H4** | Documentation tree. **Do not tidy the build documents into a narrative** — `FINDINGS.md` is append-only and its value is the order things were learned in, wrong turns included |
| **H5** | Licence, `CONTRIBUTING.md` stating the non-negotiables, honest provenance |
| **H6** | The README a stranger reads: what it does, what it cannot do, the offline demo, what the evaluation established. **The limits are not a footnote** |

**`--from-fixtures` stays the first documented command**, with the reason recorded so a later tidy-up does not demote it. The T-033 fabricated timestamp stays in the README — an honest account of the one thing the model got wrong is more convincing than an account with only successes.

## P1.6 · Publish

---

# PART 2 — MVP-1

Starts after publication. This is where parallelism is real, and it arrives in one specific phase.

## P2.1 · The gate `[serial, Opus 5]`

**B-101** typed decision object · **B-102** candidate enumeration from observed objects · **B-103** the narrowing pass.

Semantic change to the descent. One agent, contracts settled before implementation.

**B-459 has already closed the input half** — a fabricated subject is refused before the walk. The gate is the output half: what the model may ask for next, constrained to objects the evidence contains.

## P2.2 · One flow, serially — `isis_adjacency` `[B-107]`

**Do not parallelise until this lands.** The flow pattern has been demonstrated once, on `bgp_session`. Five concurrent implementations off a pattern demonstrated once replicates an unvalidated assumption five times — §0.13's data face applied to project planning.

`isis_adjacency` is the right first: its checks already exist, it is the layer every other descent passes through, and it is two rungs rather than five.

**Its broken state is designed alongside the flow, not captured afterwards.** A healthy-only corpus has structurally zero coverage of the case a descent exists for.

## P2.3 · The parallel phase `[3-4 Sonnet 5 agents, Opus 5 judging]`

Only after P2.2 proves the pattern repeats.

| Track | Items |
|---|---|
| **Flows** | B-108 `device_health` · B-109 `ldp_session` · B-110 `l3vpn_service` · B-111 `topology` — one agent per flow, never two |
| **Config axis** | B-104 section retrieval · B-105 inheritance resolution · B-106 intent-vs-observed diff |
| **Model** | B-114 — can a local model emit the typed gate decision? The gate is a strictly easier task than free tool calling, and the MCP experiment suggests it may be within reach |

**Rules:** one agent per track; Opus 5 judges acceptance on all of them; a shared-contract change is a HALT, not a decision; each flow's broken fixture is designed with the flow.

## P2.4 · Decide the evidence-reduction question `[JUDGEMENT — do not schedule until decided]`

B-414 through B-419 are all blocked on **B-206**, which is blocked on **B-206a/b** — platform fixes on the logging pipeline, not code.

Three options, and this needs deciding rather than scheduling:

1. **Fix the platform.** Device logging severity levels and collector-noise filtering. Operator work, unblocks the whole chain.
2. **Descope B-206.** Build normalisation against the local `show logging` buffer, which measurement showed is *better* than the platform for local correlation — every causal event is severity 5 or 6 and none reach the platform.
3. **Defer the chain entirely** until Stage 2 needs it.

Option 2 is the one the evidence points at, but it is a decision, not a default.

## P2.5 · Stage 2, planned separately

B-201 through B-210. Needs its own plan and its own gate — the property suite is the transition gate, not a follow-up, because at Stage 2 the boundaries hold with nobody present.

**B-207's variance experiment runs before Stage 2 ships**, not after: fire the same event ten times and measure how much the tool sequence varies.

---

# PART 3 — WHAT RUNS IN NEITHER

Named so their absence is a decision.

| Item | Why | Unblocked by |
|---|---|---|
| **B-401** Juniper | No Junos device. Templates against invented output violate the discipline that caught most of this build's defects. **The payoff test remains unvalidated, not passed** — a boundary never pushed on is not a boundary that held | A cRPD container |
| **Stage 3** | B-301 identity gates all of it, and identity is not a coding task | An identity provider decision |
| **B-452** audit governance | Deferred by decision; the one-shot set is specified and not established, leakage routes named | A decision to establish it |
| **B-409** scale test | Needs an estate an order of magnitude larger | Not available |
| **B-410** runbook | Written after the failure envelope is characterised | A soak run |
| **B-426/427** chaos harness | Earned by the manual rounds, not assumed. Revisit after P1.2 adds three | Seven scored rounds |

---

# PART 4 — WHAT PARALLELISM ACTUALLY BUYS

Stated plainly, because the last plan assumed it and was wrong.

**Part 1 has almost none.** P1.1 hygiene can overlap P1.2's lab rounds, and that is the whole of it. Everything else is sequential by dependency: reconcile → hygiene → rounds → re-test → consolidate → publish.

**Part 2 has one genuinely parallel phase**, P2.3, and only after P2.2 proves the pattern. Three or four agents on independent flows and the config axis is real compression.

**The bottleneck is judgement, not typing.** 43% of MVP-0's findings were decisions the plan did not specify. Parallel agents multiply implementation; they do not multiply the acceptance judgement that §0.9 puts with Opus 5. Four parallel tracks means four queues converging on one reviewer.

**So the honest expectation:** parallelism helps in P2.3 and roughly nowhere else. Planning as though it helps everywhere is what produced a track with zero runnable items.

---

# PART 5 — SUCCESS

**Part 1 succeeds when** a stranger can clone the repository, run the offline demo with no lab and no key, read an honest account of what it does and does not do, and see seven scored blind trials behind the claim.

**Part 2 succeeds when** the gate exists, two flows have been built rather than one, and the pattern has been demonstrated to repeat rather than argued to.

**Neither succeeds by emptying the backlog.** Gate Zero exists because the backlog had drifted from the repository; the correct outcome is a backlog that says what is true, not a backlog that is short.
