# BACKLOG COMPLETION PLAN — **SUPERSEDED by `PLAN-V2.md`**

> **Kept, not deleted.** Its Track A and Track B results are the evidence for why
> PLAN-V2 begins with Gate Zero: Track A found four of five items already done, and
> Track B halted with zero runnable items. Measured afterwards, **this plan scheduled
> 12 items and 11 of them were not schedulable when it was written** — five already
> DONE, six BLOCKED on a prerequisite this document excluded on its own Part 1 page.
> B-459 was the single real item, and it shipped.
>
> Read Part 0 §0.2a for the rule both halves of that produced. Everything below is
> superseded as a *plan* and retained as a *record*.

---

**Goal:** close as much of the remaining backlog as can be closed without an operator, in parallel where the contracts allow, so that one review pass and one MCP re-test leave the repository ready to publish.

**Not a goal:** closing everything. Several items are blocked on the lab, on a second vendor, or on decisions that should be made with the findings in hand rather than in advance. This plan names those explicitly so their absence reads as a decision.

**Companion documents:** `BUILD-PLAN.md` Part 0 (binding), `BACKLOG.md` (item detail), `chaos-harness.md` §3.1 and §6.1 (injection protocol), `peer-review-response.md` §6.

---

# PART 0 — HOW THIS PLAN RUNS

## 0.1 Everything in Part 0 of BUILD-PLAN.md still applies

Nothing here relaxes it. In particular: §0.5's frozen files, §0.9's delegation policy, §0.9a's shared-contract HALT, §0.11's escalation ladder, §0.12's vacuity rule, §0.13's six faces, §0.14's classification rule, and §0.15's protocol-exclusion rule.

Two of those bind this plan more than they bound the last one, because it runs wider and faster:

**§0.9a — a shared-contract change is a HALT, not a decision.** Three tracks run concurrently. A defensible choice made independently on two branches produces two defensible, incompatible contracts, and the incompatibility surfaces at merge with both authors confident.

**§0.13's setup face.** A plan that runs many items quickly is a protocol optimised for throughput. Ask, per track, what condition the speed excludes.

## 0.2 The shared contracts

A change to any of these is a HALT on the track that wants it, escalated to the orchestrator, never decided on a branch. Named so that "shared contract" is not a judgement call:

| Contract | Owner while this plan runs |
|---|---|
| `EvidenceEpoch` / `Observation` / `for_device()` | Track A |
| `Rung` — including `SubjectRule`, `Aggregation`, `device_scope` | Track A |
| `Flow` — registry, findings enum, prewalk precondition | Track A |
| `CheckResult` | Track A |
| `DescentResult` — including `coherence`, `off_path` | Track A |
| The report schema — observations / interpretations / recommendation | Track A |
| `platforms.APPROVED_COMMANDS`, `templates` validators | **Frozen. Additions only.** |
| The MCP tool surface and its return shape | Track C |
| `log_window.ShapedWindow` | Track B |

Track A owns the descent's contracts because most semantic change lands there. B and C consume them and may not alter them.

## 0.2a A plan item cites the last finding that touched it

Added 2026-08-17, after Track A ran.

> **Every item in a plan must cite the most recent `FINDINGS.md` entry that
> touched it, or state explicitly that none has.**

**Four of Track A's five items were already done or already measured false**, and
the plan said otherwise because it was written from `BACKLOG.md`'s item *titles*
rather than from the findings that had closed them:

| Item | The plan said | The record already said |
|---|---|---|
| A2 · B-411 | a read timeout returns `status: success` | **OBS-099** measured the mechanism does not exist in netmiko 4.7 |
| A3 · B-425 | usage not instrumented | done at B-425 |
| A4 · B-403 | consolidation to judge | done on instruction, OBS-104 |
| A5 · B-404 | six parsers predate §0.10 | done, all six report `unaccounted_lines` |

**This is §0.13's data face applied to a plan.** A backlog entry records *what
someone thought at filing time*. Reading it as a record of *what is* is the same
error as reading a survey as a census — the entry was accurate when written and
the conclusion drawn from it was not.

It is worth a mechanical rule rather than more care for the usual reason: the
plan's author cannot see the gap from inside, because a stale entry and a
current one are indistinguishable in `BACKLOG.md`. A citation is checkable by
someone who was not there.

**In practice:** an item reads `B-4xx — description (last touched: OBS-nnn)` or
`(no findings entry)`. Writing the second is itself informative — an item nobody
has measured is a different risk from one measured and left open.

**And the citation is not sufficient on its own.** Track B (OBS-124) produced
the other half: six of its seven items were blocked on **B-206**, which this
plan's Part 1 excludes on the previous page. None of them had a stale finding —
most had none at all. They were simply unbuildable, and the backlog's dependency
column said so.

> **A plan item cites its last finding, *and* its dependencies are checked
> against the plan's own exclusion list.**

Track A's version of this cost four items of restated work. Track B's cost the
whole track. Both came from writing a plan out of item *titles* rather than item
*metadata*, and both are invisible to a reader who trusts the plan.

## 0.3 Model delegation

Unchanged from §0.9. Opus 5 orchestrates and owns every decision and every acceptance judgement. Sonnet 5 implements against a precise specification. Fable 5 is consulted on a hard call and logged as `consultation`.

**One agent per track. Never two on one track.** The failure mode is not a merge conflict; it is two agents settling incompatible readings of one contract.

**Acceptance is serial even when implementation is parallel.** A track's work is not `DONE` until Opus 5 has judged it against the item's criteria.

## 0.4 The loop

Each track runs the same cycle until its list is exhausted or it halts:

```
  read the item in BACKLOG.md
  read anything it depends on — do not reconstruct it
  decide who implements
  implement
  make test && make lint
  judge acceptance
  append to FINDINGS.md
  update TRACKER.md
  commit with the item ID
```

**A turn ends with a commit hash or with an explicit statement that nothing was done. Never with an intention.** This is not stylistic — in a transcript, a turn ending on an intention is indistinguishable from one ending on completion, and `TRACKER.md` will not catch it because nothing was marked done.

## 0.5 Halting

Per §0.11's ladder. On this plan, additionally:

- **HALT** the track and the plan: any device write, any frozen-file change, any shared-contract change, a baseline test failure not traceable to the current item.
- **HALT the track only**, continue the others: an item turns out to depend on something not yet built, or its specification is wrong on contact with the code.
- **DECIDE-AND-LOG**: anything else with a defensible default.

A track that halts does not silently become idle. Record it, then move to the next item in that track's list.

## 0.6 The review gate

**Nothing in Part 4 starts until the review pass is complete.** The point of running wide is to produce findings; the point of the review is to read them together before deciding what closing the backlog means.

---

# PART 1 — WHAT DOES NOT RUN

Recorded first, so their absence is a decision rather than an oversight.

| Item | Why not | Unblocked by |
|---|---|---|
| **B-440, B-462, B-463** | Injection rounds. Require a device write, which is an absolute HALT under §0.11 and a rule of method under `chaos-harness.md` §3.1 | The operator running `fault_lab.py` |
| **B-401** Juniper | No Junos device exists. Templates written against invented output would violate the discipline that has caught most of this build's defects | A cRPD container or hardware |
| **B-452** audit governance | Deferred by decision. The one-shot set is specified and not established, with its two live leakage routes named | A decision to establish it |
| **Stage 3, all of it** | B-301 identity gates every other item, and identity is not a coding task | An identity provider decision |
| **B-207** variance experiment | Needs Stage 2's trigger path to exist | B-201 |
| **B-409** scale test | Needs an estate an order of magnitude larger | Not available |
| **B-410** runbook | Written after the failure envelope is characterised, not before | A soak run |

**B-113's remaining half — the 21→5 consolidation — is deliberately excluded**, and this is the one worth arguing. It is the largest single change to a surface that is about to be re-tested with LM Studio, and the pre-registered prediction in `MCP-EXPERIMENT.md` §9 is about the *rewording*. Consolidating before the re-test confounds it: selection would change for two reasons at once and neither would be separable.

Re-test first. Consolidate after, with the measurement in hand.

---

# PART 2 — THE THREE TRACKS

Each runs on its own branch. Merge order is **A, then B, then C** — A may change semantics the others assume.

---

## TRACK A — `feat/descent-hardening`

**Owns:** the descent's contracts. **Model:** Opus 5 for contracts, Sonnet 5 for implementation.

This track carries the items that change what the descent means, so it runs first and alone in its area.

### A1 · B-459 — argument fabrication `[LARGEST OPEN SAFETY GAP]`

The uncovered boundary. Every containment mechanism operates on what a tool *returns*; nothing constrains what a model *supplies*. A fabricated peer address produces a fully grounded, correctly cited investigation of a session that does not exist, and every gate passes.

**Design, and it is B-453 pointed the other way:** B-453 checks that every identifier in a model's *output* appears in the evidence. This checks that every identifier in its *input* appears on the device. Same mechanism, same canonicalisation table, opposite direction.

**Home:** the epoch, because it already collects the device's state before the walk. That is why this item waited.

**Two limits to record, so it cannot be over-scoped later:**
- It catches an invented object, not a wrong one. A real peer address that is not the one you meant passes — exactly as B-453 catches an invented entity and not a wrong relation.
- Validation needs a read before the read. On this fabric that is ~8s of login. The epoch is the only place where that read already happens.

**Acceptance:** an investigation naming a peer absent from the device's BGP summary is refused before the descent walks, with a distinct finding — not `undetermined`, which means *could not read*. Test with a real device fixture and a fabricated address.

### A2 · B-411 — partial read returns `status: success`

A read timeout returns partial output with `errors: []` and `status: "success"`. Third instance of the silent-degradation shape; filed and never fixed.

**Acceptance:** a truncated read is `status: partial` or an error, never `success`. Every consumer of `status` audited — a caller reading `success` as "complete" is the defect, and there may be more than one.

### A3 · B-425 — usage instrumentation

`complete_prompt` returns text only. The character proxy is honest but it is not a measurement, and B-207 and the harness will both want real numbers.

**Acceptance:** token usage recorded per call where the provider returns it, absent where it does not, never estimated and presented as measured.

### A4 · B-403 — `checks.py` / `health.py` consolidation `[JUDGEMENT REQUIRED]`

The agreement test has been green throughout. Consolidation is a later decision with real risk to a large passing suite.

**Do not assume it should happen.** Measure first: how many checks have a corresponding health rule, how much logic is genuinely duplicated, and what the agreement test would lose. If the answer is "less duplication than the item assumes", close it as measured rather than merging for tidiness.

### A5 · B-404 — line-accounting retrofit for `parsers.py`

The six hand-written parsers predate §0.10. Deliberate inconsistency, scheduled rather than ignored.

**Acceptance:** every parser reports `unaccounted_lines`, empty for every committed fixture. Expect this to find defects — §0.10 caught three wrong specs when it was introduced.

---

## TRACK B — `feat/evidence-reduction` · **HALTED 2026-08-17 (OBS-124)**

> **Six of seven items are blocked on dependencies this plan's own Part 1
> excludes; the seventh (B-420) is already done.** B-414, B-415 depend on
> **B-206**, which is Stage 2. B-418, B-416, B-419 depend on B-414. B-417
> depends on **B-107**, an unimplemented flow. Nothing here is runnable, and it
> was visible from the backlog's dependency column before any code was read.
>
> The rule this produced is in §0.2a, extended: a plan item cites its last
> finding **and** its dependencies are checked against the plan's own exclusion
> list.

**Owns:** `ShapedWindow` and the reduction pipeline. **Model:** Sonnet 5, Opus 5 judging.

Uses the captured log corpus. Touches no descent semantics.

### B1 · B-414 — log normalisation

Template extraction and aggregation as a general capability. `shape_window` is the first instance; this generalises it.

**Syslog needs no clustering algorithm.** IOS-XR has already assigned every event type an identifier — extraction is grouping by `(mnemonic, normalised body)`. Deterministic where clustering is not, and the mnemonic is the same identifier Stage 2's trigger lookup will key on.

**Singletons are never aggregated away.** No minimum-count threshold, ever.

### B2 · B-418 — temporal shape

`max_rate_1m` and burst detection alongside the total. Sixty events over an hour is a chronic condition; sixty in ninety seconds is an incident, and the counts are identical.

### B3 · B-416 — event episodes

Deterministically constructed, time-bounded sequences related by device, object, protocol dependency and temporal proximity.

**Proximity bounds come from protocol timers, never intuition.** Round 1 measured a 154-second interface→BGP gap — the hold timer. A 30-second window would have split one incident into two.

**This is not cosmetic.** It is the mechanism for detecting a masked second fault: two independent faults rarely share an instant.

### B4 · B-420 — coverage metadata

The `unevaluated` discipline applied to evidence sources. The gap exists today — severity 5 and 6 never reach the log platform, so any statement about their absence is currently unfounded.

**Grounding must enforce it**: a claim of absence without coverage backing fails, exactly as an uncited claim of presence does.

### B5 · B-415 — clock skew detection

Detected and reported, never silently ordered. Multi-device correlation is meaningless across disagreeing clocks.

### B6 · B-417 — relationship-aware projection

Projection over a bounded topology neighbourhood rather than identifier match. Depth declared and recorded in the envelope — an unbounded neighbourhood is the whole fabric, which is no projection at all.

### B7 · B-419 — `expand_evidence` and the four tiers

Progressive disclosure. Passes the D11 test: the model genuinely must decide when it needs more proof.

---

## TRACK C — `feat/surface-and-hygiene`

**Owns:** the MCP surface and repository hygiene. **Model:** Sonnet 5, Opus 5 judging.

The publication track. Nothing here changes behaviour.

### C1 · B-402 — operator knowledge notes

Smallest item in the backlog and human-authored, so pre-approved by construction. Already exists informally in `inventory/lab.yaml` comments where no code can read it.

**Acceptance:** structured `notes:` per device or object, injected into the Grounding slot. The agent stops rediscovering known conditions on every run.

### C2 · B-413 — remove the banned clause from `TROUBLESHOOTING_PROMPT`

`llm_analysis.py` carries the exact self-evaluation clause the prompt library bans. A version bump under the library's own rules.

### C3 · B-421 — clean-environment CI

`--from-fixtures` runs in a job with no `.env`, no credentials, no network. The offline demo's entire value is that it needs nothing, so nothing is the only environment that tests it.

This exists because the demo was only ever verified somewhere that had everything.

### C4 · B-457 — withheld-paraphrase metric

A field nobody aggregates is not detection. Reviewer B's framing: wire this to a service-quality metric for the tool, never to the network pager.

### C5 · B-458 — error-string residual

As filed.

### C6 · B-461 — the unowned surface `[DETECTABILITY ONLY]`

A chat client's restatement is a paraphrase nothing labels, downstream of every gate, on a surface not owned.

Numbered rungs shipped. **Do not extend this into anything describable as enforcement** — at a boundary you do not own, detectability is the whole available defence, and describing it otherwise is the over-claiming the peer review was about.

### C7 · Repository hygiene for publication

See Part 3.

---

# PART 3 — PUBLICATION READINESS

Runs on Track C, and the first item is the one that matters.

### H1 · Secrets audit `[BLOCKING — nothing publishes until this is clean]`

**Working tree and full git history.** A key removed in a later commit is still in the history and still compromised.

```
  scan for: API keys, passwords, private keys, tokens
  scan: every commit, not only HEAD
  check:  .env is gitignored AND was never committed
  check:  fixtures contain no credentials, banners, customer strings
  check:  no lab credentials in any doc, test, script or findings entry
```

**Known:** a MiniMax key and lab credentials appeared in an operator transcript during this build. They are recorded as needing rotation. Confirm they are not in the repository and state plainly that a transcript is a copy nobody can revoke.

If anything is found in history, **HALT**. History rewriting is an operator decision, not a track decision.

### H2 · What the repository claims

Every claim in `README.md`, `CLAUDE.md` and the design documents audited against what is measured. The peer review found five claims exceeding their evidence; that audit was for MVP-0 and the repository has moved.

**Specifically:** `--from-fixtures` stays the first documented command, with the reason recorded so a later tidy-up does not demote it. The T-033 result stays, including the fabricated timestamp — an honest account of the one thing the model got wrong is more convincing than an account with only successes.

### H3 · Fixture review

Every fixture is permanent once published. Review for anything sensitive, anything identifying, anything that should not leave the estate.

### H4 · The documentation tree

`docs/design/` is the WHY, `docs/build/` is the HOW. `docs/README.md` carries the map and the reading order.

**Do not tidy the build documents into a narrative.** `FINDINGS.md` is append-only and its value is that it records what was learned in the order it was learned, including the wrong turns. A cleaned-up findings log is a different and much less useful document.

### H5 · Licence, contribution, provenance

Licence file. A `CONTRIBUTING.md` stating the non-negotiables — frozen files, the allowlist discipline, guardrails as tests, and that a test written from the same premise as the implementation confirms the premise.

**State the project's provenance honestly:** built alongside a book as a design curriculum, reviewed externally, and evaluated on a lab of thirteen nodes with a small number of blind trials.

### H6 · The README a stranger reads

Someone arriving cold needs, in order: what it does, what it cannot do, how to run the offline demo, and what the evaluation actually established.

**The limits are not a footnote.** One flow, one vendor, read-only, a handful of blind trials. `MVP0-REVIEW.md` §5 already says this well — the README should not say less.

---

# PART 4 — THE REVIEW PASS

**Nothing here starts until all three tracks report.**

### R1 · Read the findings together

The point of running wide is to produce findings. Read them as a set, not as a list:

- What did the tracks discover that the plan did not anticipate?
- Did anything found on one track invalidate work on another?
- Which of the six §0.13 faces appeared, and did any appear in a new form?
- Did any item turn out smaller than filed, as B-437 did? Close it at its measured size rather than padding it.

### R2 · Blockages

Everything halted, with the reason and what would unblock it. A blockage discovered by running is worth more than one predicted in advance.

### R3 · One last iteration

From the findings, not from this plan. The items worth doing after a wide pass are usually not the ones anyone would have listed before it.

### R4 · Then, in this order

1. **MCP re-test** — same three questions, reworded surface, against `MCP-EXPERIMENT.md` §9's pre-registered prediction. Content versus contrast, and the prediction is already sealed.
2. **B-113 consolidation** — 21 → 5, with the selection measurement in hand and the context-cost argument (+86%) standing on its own.
3. **Publication.**

---

# PART 5 — WHAT SUCCESS LOOKS LIKE

Not "the backlog is empty." It will not be, and several items should not be closed by this plan.

Success is:

- Three tracks merged, tests green, four frozen files byte-identical.
- Every item either **done**, **closed at its measured size**, or **halted with a stated reason**.
- `FINDINGS.md` richer than the plan anticipated — a wide pass that produces no surprises was not looking.
- A secrets audit that is clean across the full history.
- A README a stranger can read that does not overstate what this is.
- The three injection rounds still open, because they need the operator, and they are the highest-value work remaining.

**The governing rule has not changed:** over-engineering is building *N* of something before validating one. This plan closes items that are ready. It does not close the backlog, and a plan that claimed to would be the thing this project has spent its whole life avoiding.
