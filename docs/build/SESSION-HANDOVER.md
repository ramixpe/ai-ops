# Session Handover

**2026-08-16.** Branch `feat/investigation-layer`. Tree green: **1377 pass, 22 skipped**, lint clean, four frozen files byte-identical against `6629a2c`.

> ## ⛔ Read this first: the session ended on a HALT
>
> **B-428 was specified and not implemented.** Clause 2 of the specification conflicts with the code, and the instruction accompanying it was *"HALT and record it. Do not improvise a different rule."*
>
> **Nothing was written to `src/` or `tests/` this session.** `git diff` against the last code commit is empty for both.
>
> The decision needed is **Q-020**, and it is small. Details in §1.

---

## 1. The HALT — Q-020, and what it needs

### The specification, as given

| Clause | Rule | Status |
|---|---|---|
| 1 | rung 1 `HEALTHY` → emit `no_fault_on_path`, broken rungs as observations not a cause, **exit 0** | ✅ correct, unimplemented |
| 2 | rung 1 `BROKEN` **and** causal chain empty → contradictory, emit `undetermined` | ❌ **conflicts** |
| 3 | every other case unchanged | ✅ trivially satisfied |

### Why clause 2 conflicts

`DescentResult.causal_chain` is defined as *the broken rungs **above** the cause*. Rung 1 is the top of the ladder. **When rung 1 is the cause, there is nothing above it, so the chain is empty by construction — necessarily, not contradictorily.**

Verified offline against the real `bgp_session` ladder:

```
rung 1 BROKEN, rungs 2-5 HEALTHY
  -> cause = bgp_session   chain = []   finding = cause_not_localised
```

That matches clause 2's antecedent exactly, and it is the **only** reachable state that does. The state clause 2 appears to reach for — rung 1 broken, cause *below* rung 1, chain empty — cannot occur, because a broken rung 1 with a lower cause **is** the chain:

```
[B,H,H,H,B] -> cause = interface   chain = ['bgp_session']
[B,B,H,H,H] -> cause = transport   chain = ['bgp_session']
```

So applying clause 2 would replace `cause_not_localised` — a correct, well-defined, golden-tested finding — with `undetermined`, which asserts something different and false: *"a rung could not be read"* instead of *"the symptom is confirmed and nothing beneath explains it"*.

### The two resolutions, neither chosen

**(i) Drop clause 2.** Its only reachable case is already handled correctly. Clause 1 plus "everything else unchanged" is then the entire rule.

**(ii) Re-scope it** to the genuinely impossible state (cause below rung 1 with an empty chain) as a defensive assertion that should never fire. This is a *different rule* from the one specified, which is why it was not written.

### One interaction to decide alongside it

**B-432** records that `cause_not_localised` may be unreachable in practice for `bgp_session`, because rung 2 restates rung 1's state machine. If clause 2 was a step toward **retiring** that finding, it is coherent — but it resolves B-432, which the same instruction deferred. If it was not, clause 2 and B-432 pull in opposite directions on the same finding.

### Why clause 1 was not implemented on its own

Tempting, and judged wrong. The clauses are mutually exclusive conditions so they look separable, but they are one change to one function and one closed finding set — `no_fault_on_path`'s place in that set is settled alongside whatever clause 2 becomes. **Applying a HALT selectively on my own judgement that the remainder is safe is the erosion `chaos-harness.md` §3.1 names**: every argument for proceeding is locally reasonable, and the rule's value is that it does not bend to locally reasonable arguments.

---

## 2. State of the work

### Done and merged to the branch

| | |
|---|---|
| **MVP-0** | Complete. T-001–T-034 plus T-029a/b/c. **M4 reached** |
| **Review** | `MVP0-REVIEW.md`, written at M4 before any MVP-1 work |
| **Injection rounds** | **All four complete.** Scored in OBS-095 |

### The four rounds

| # | Fault | Predicted | Actual | Via | Answer ≥ evidence | Correct |
|---|---|---|---|---|---|---|
| 1 | IS-IS shut, PE3 uplinks | `igp_adjacency` | `igp_adjacency` | primary | ✅ | ✅ |
| 2 | transport block, RR1↔PE1 | `transport` | `transport` | primary | ✅ | ✅ |
| 3 | BGP admin-shut, PE2 | `cause_not_localised` / *`transport_blocked`* | `transport_blocked` | **refutation branch** | ❌ | ⚠️ impoverished |
| 4 | one uplink shut, IGP absorbs | `interface`, empty chain, exit 1 | **identical** | primary *(predicted failure)* | n/a | ❌ **wrong** |

**Prediction accuracy 4/4. Diagnostic accuracy 3/4 — do not report 75%.** The classes differ:

- fault **on the dependency path**: **3 / 3**
- no fault on the path: **0 / 1**, structurally 0 / *n* until B-428

**Is agreement a pattern?** Provisionally yes for fault localisation — three rungs, and rounds 1 and 2 put *opposite* pressure on the walk rule (descend past a healthy rung; do not descend into healthy rungs). Three caveats keep it provisional: selection effect (rounds designed by someone who knows the ladder), every fault single (Q-019 untouched), and round 3 matched via its declared branch. **No for concluding health, and more rounds will not change that.**

### Fabric state

**PE2 has one uplink administratively shut** — round 4's fault, still applied. Rounds 1–3's faults were restored by the operator. Verify before any further trial; the round-4 predicate is `2 of 3 members healthy` on PE2.

---

## 3. Next actions, in order

1. **Answer Q-020.** Unblocks B-428 and track A. Small.
2. **Implement B-428** once clause 2 is settled. Round 4's payload is the regression (`no_fault_on_path`, exit 0); rounds 1–3's payloads must be unchanged and all three pinned. `no_fault_on_path` joins the closed finding set, and the exit-code documentation in the README and `--help` needs to say what exit 0 now covers.
3. **Re-run the four round payloads**, update the B-427 rows, and **update `MVP0-REVIEW.md` §5** — the *"it cannot tell you nothing is wrong"* warning may no longer be true, and if so it is the single most important line in the review to correct.

**Not started, deliberately:** B-430, B-431, B-432, track B (`feat/log-evidence`), track C (`feat/chaos-harness`), any MVP-1 work.

---

## 4. What a new session most needs to know

**Read in this order:** `BUILD-PLAN.md` Part 0 (§0.9a–§0.14 are the rules this build learned), then `MVP0-REVIEW.md`, then this file.

**The four things most likely to be got wrong by someone picking this up:**

**`descent.py` has no model call, permanently.** That is the claim the layer rests on. `test_the_model_cannot_influence_the_diagnosis` asserts the descent is byte-identical with and without an analyst.

**A failed grounding check means the report is not emitted, structurally.** `InvestigationResult.report` is `None` when grounding failed, and `GroundingFailure` has no field a claim can occupy. Do not "improve" either into a flag beside the prose.

**Exit 2 means the *answer* is untrustworthy; exit 1 means the *network* is broken.** This matches `nettools diff` and deliberately not `nettools health`, where 2 is the worst network outcome. A grounding failure is exit 2 even over a real fault — otherwise a systematic grounding regression hides in the noise of routine faults.

**Do not wire this to anything that pages on exit 1 until B-428 lands.** Measured at round 4: `cause: interface on PE2`, `trustworthy: true`, exit 1, on a BGP session that was Established and carrying traffic.

### The one thing worth carrying to another project

> **Every defect that mattered in this build was found by evidence from outside the artefact that had it.**

Sixteen passing tests did not find the noise filter; an independently-written specification did. 1,365 passing tests did not find `_log_window`; a live run did. A human watching a device console found the harness defect the harness's own verification missed. Round 3's wasted answer was found by auditing what a check reads against what its input contains — **not** by grading its output, which was correct.

That is §0.12, §0.13 and §0.14 in one sentence, and it is why the seven silent-failure shapes are catalogued rather than merely fixed.

### The uncomfortable number

**We predicted the tool's behaviour more accurately than the tool diagnosed the network — 4/4 against 3/4.** The backlog is currently a more accurate model of this system than the code is. Round 4 is the clearest case: constructed offline, written down with its falsifiers, and reproduced exactly on live hardware long before anyone fixed it.

B-428 is the first item that closes that gap rather than widening it. It is blocked on one small decision.
