# Session Handover

**2026-08-16.** Branch `feat/investigation-layer`. Tree green: **1391 pass, 22 skipped**, lint clean, four frozen files byte-identical against `6629a2c`.

> ## ✅ The HALT is resolved and B-428 has landed
>
> Q-020 answered: **clause 2 dropped** — it was an error, and an empty causal chain with rung 1 as the cause is necessary by construction, not contradictory. B-428 shipped as **clause 1 alone**.
>
> **The most important line in `MVP0-REVIEW.md` has been corrected.** *"It cannot tell you nothing is wrong"* no longer holds.
>
> Nothing else was started. **B-430, B-431, B-432, tracks B and C, and MVP-1 are all untouched.**

---

## 1. What landed: B-428

> After a descent completes and before a finding is emitted: **if rung 1 is healthy, no cause below it explains anything, because there is no symptom to explain.** Emit `no_fault_on_path` with the broken rungs recorded as observations rather than as a cause. Exit 0, not 1. Every other case unchanged.

Nine lines of predicate in `_finding_for`. No model call; `ALL_HEALTHY` aggregation untouched; four frozen files still byte-identical.

| Vector | Before | After |
|---|---|---|
| round 1 `B B B B H` | `igp_isolated` | unchanged |
| round 2 `B B H H H` | `transport_blocked` | unchanged |
| round 3 `B B H H H` | `transport_blocked` | unchanged |
| **round 4 `H H H H B`** | `interface_line_down`, **exit 1** | **`no_fault_on_path`, exit 0** |
| captured `broken` `B B B B B` | `interface_line_down` | unchanged |
| `cause_not_localised` `B H H H H` | `cause_not_localised` | unchanged |

All six pinned in `tests/test_rounds_regression.py` (13 tests) — the four rounds as executable vectors, which is the form a B-427 corpus row should take.

**Exit 0 now covers two findings.** `all_layers_healthy`, and `no_fault_on_path` — the session is fine *and* something else on the device is genuinely down. The broken rungs are reported as observations in all three output formats, never as a cause. **`no_fault_on_path` must never read as "nothing found"**; a test asserts every renderer shows the broken rung.

**Read exit 0 as "not on this path", not as "all clear".** The tool still cannot say *"this device is healthy"* — that was never the question it answers.

### Four judgements from it, now standing rules (OBS-098)

Recorded where someone who never sees B-428 will read them.

**Shape 8 — a fix silently deletes coverage of behaviour that was always correct** (§0.13). Converting `test_a_healthy_rung_does_not_stop_the_walk_either` to the new finding left *"descend past a healthy rung to a broken one below and name it"* — the property round 1 depends on — with **no coverage at all**. The suite went 1377 green → 1378 green; nothing was ever red. **Not §0.13's tests face**: there the test was wrong; here it was right and got repurposed out of existence. **The tell is a test whose *inputs* had to change rather than its expectations.**

**Corpus integrity, binding on B-427.** *Scores are never rewritten after a fix; corrections are appended as new rows referencing it.* Binding rather than a judgement about round 4, because the urge to tidy a corpus is strongest exactly when a fix has just landed and the old score reads as an embarrassment rather than as evidence.

**Deleting a stated limitation asserts a capability** (§0.14). Removing *"it cannot tell you nothing is wrong"* would have read as *the limitation is gone*, which is a different claim from the one B-428 supports. Narrowed instead. **An absence of stated limits is itself a claim, and the one kind nobody reviews.**

**"X rather than Y" contains two obligations.** *"Observations rather than as a cause"* means suppress the cause **and** report the rungs. Half of it passes any `cause is None` test and silently drops a real interface fault — quieter than the defect it replaced, therefore worse.

Three of those four are about what happens *after* a defect is fixed. Every rule before §0.14 is about detection; these cover the moment immediately after detection succeeds, which had no rules and is where the pressure to tidy is highest.

---

## 2. State of the work

### Done and merged to the branch

| | |
|---|---|
| **MVP-0** | Complete. T-001–T-034 plus T-029a/b/c. **M4 reached** |
| **Review** | `MVP0-REVIEW.md`, written at M4 before any MVP-1 work |
| **Injection rounds** | **All four complete.** Scored in OBS-095, pinned in `tests/test_rounds_regression.py` |
| **B-428** | **Landed** (OBS-097). Round 4's false positive is closed |

### The four rounds

| # | Fault | Predicted | Actual | Via | Answer ≥ evidence | Correct |
|---|---|---|---|---|---|---|
| 1 | IS-IS shut, PE3 uplinks | `igp_adjacency` | `igp_adjacency` | primary | ✅ | ✅ |
| 2 | transport block, RR1↔PE1 | `transport` | `transport` | primary | ✅ | ✅ |
| 3 | BGP admin-shut, PE2 | `cause_not_localised` / *`transport_blocked`* | `transport_blocked` | **refutation branch** | ❌ | ⚠️ impoverished |
| 4 | one uplink shut, IGP absorbs | `interface`, empty chain, exit 1 | **identical** | primary *(predicted failure)* | n/a | ❌ **wrong — fixed by B-428** |

**Prediction accuracy 4/4. Diagnostic accuracy 3/4 — do not report 75%.** The classes differ:

- fault **on the dependency path**: **3 / 3**
- no fault on the path: **0 / 1 at the time; the class is now closed** (B-428)

Row 4 keeps its ❌ deliberately. **A corpus records what the system did at the time, not what it does now** — one that silently rewrites its own history cannot show that a fix worked, which is most of what a corpus is for.

**Is agreement a pattern?** Provisionally yes for fault localisation — three rungs, and rounds 1 and 2 put *opposite* pressure on the walk rule (descend past a healthy rung; do not descend into healthy rungs). Three caveats keep it provisional: selection effect (rounds designed by someone who knows the ladder), every fault single (Q-019 untouched), and round 3 matched via its declared branch. **No for concluding health, and more rounds will not change that.**

### Fabric state

**PE2 has one uplink administratively shut** — round 4's fault, still applied and **not restored**. Rounds 1–3's faults were restored by the operator. Verify before any further trial; the round-4 predicate is `2 of 3 members healthy` on PE2. Note that `nettools investigate RR1 10.255.0.12` now correctly exits **0** against that state.

---

## 3. Next actions, in order

**Nothing is blocked.** The next item is a decision about sequencing, not a HALT.

1. **Restore PE2's uplink** before any further trial.
2. **B-430** — `bgp_transport` ignores `last_reset_reason`, which stated round 3's cause verbatim in the same parsed record. Small, and it is silent-failure shape 7's only known instance.
3. **B-431** — `EACH_PHYSICAL_INTERFACE` is a naming filter duplicated in three places with one differing definition, and an empty member set raises `IndexError` in the emit path.
4. **B-432** — `cause_not_localised` is unreachable for `bgp_session`. Two options recorded, neither chosen; do not resolve without the round-2 and round-3 envelopes side by side.
5. **B-433** — audit what every check reads against what its inputs contain. `bgp_neighbor` parses 23 fields and 5 are read. Should precede any new flow, since B-107's checks will be written against the same parsers.
6. Then **tracks B and C**, and the five flows only after `isis_adjacency` runs serially.

**Untouched, deliberately:** everything in 2–6 above, plus MVP-1.

## 4. What a new session most needs to know

**Read in this order:** `BUILD-PLAN.md` Part 0 (§0.9a–§0.14 are the rules this build learned — §0.13 now carries **eight** silent-failure shapes), then `MVP0-REVIEW.md`, then this file.

**The four things most likely to be got wrong by someone picking this up:**

**`descent.py` has no model call, permanently.** That is the claim the layer rests on. `test_the_model_cannot_influence_the_diagnosis` asserts the descent is byte-identical with and without an analyst.

**A failed grounding check means the report is not emitted, structurally.** `InvestigationResult.report` is `None` when grounding failed, and `GroundingFailure` has no field a claim can occupy. Do not "improve" either into a flag beside the prose.

**Exit 2 means the *answer* is untrustworthy; exit 1 means the *network* is broken.** This matches `nettools diff` and deliberately not `nettools health`, where 2 is the worst network outcome. A grounding failure is exit 2 even over a real fault — otherwise a systematic grounding regression hides in the noise of routine faults.

**Exit 1 is now safe to page on — but read exit 0 correctly.** Round 4's false positive (`cause: interface on PE2`, `trustworthy: true`, exit 1, on a session that was Established and carrying traffic) is closed by B-428. Exit 0 now means *no fault on the path between these two endpoints*, which is **not** *this device is healthy*: `no_fault_on_path` reports broken rungs it found off the path, and a caller that ignores them will miss a real interface fault.

### The one thing worth carrying to another project

> **Every defect that mattered in this build was found by evidence from outside the artefact that had it.**

Sixteen passing tests did not find the noise filter; an independently-written specification did. 1,365 passing tests did not find `_log_window`; a live run did. A human watching a device console found the harness defect the harness's own verification missed. Round 3's wasted answer was found by auditing what a check reads against what its input contains — **not** by grading its output, which was correct.

That is §0.12, §0.13 and §0.14 in one sentence, and it is why the seven silent-failure shapes are catalogued rather than merely fixed.

### The uncomfortable number

**We predicted the tool's behaviour more accurately than the tool diagnosed the network — 4/4 against 3/4.** The backlog is currently a more accurate model of this system than the code is. Round 4 is the clearest case: constructed offline, written down with its falsifiers, and reproduced exactly on live hardware long before anyone fixed it.

B-428 is the first item that closes that gap rather than widening it. It is blocked on one small decision.
