# Peer Review Response — Consolidated

Three independent external reviews were commissioned on the MVP-0 architecture, evaluation method and operational fitness. This document records what they found, what is accepted, what is deferred and why, and the order of work.

**Source reviews**

| | Perspective | Attachments |
|---|---|---|
| **A** | Systems architect, adversarial | `design-thinking.md`, `MVP0-REVIEW.md`, `glossary.md`, `lld-investigation-layer.md` |
| **B** | Network operations engineer, "would I use this at 3am" | as above, plus `interfaces.md` and the four-round results |
| **C** | Evaluation methodologist | as above, plus `chaos-harness.md`, `evidence-reduction.md` |

Reviewers worked independently, in separate sessions, and were not told other reviews existed.

---

## 1. The headline

**None of the three said the architecture is wrong.** All three attacked the *claims made about it*.

- A: command containment and the `unevaluated` discipline are the strongest parts of the system.
- B: "you have built a safer diagnostic command than most LLM network demos."
- C: the protocol "substantially reduces hindsight bias, label contamination and result laundering."

And then, in three different vocabularies:

> A — the organising claim is unscoped and the repository contains paths it does not cover.
> B — "you are calling the output more than it currently is."
> C — five specific claims exceed what the evidence supports; one is live in a document today.

That is the result of this exercise. The machinery this project built to stop the system asserting more than its evidence supports **works internally, and the documents describing the system do not hold themselves to it.**

---

## 2. Convergent findings

Where independent reviewers with different framings reach the same defect, that defect is real and not a matter of taste.

### 2.1 Temporal coherence — all three

The highest-cost defect found, and found three ways.

**A, structurally.** Each rung calls `_collect_for_rung()`, which calls `collect_evidence()` afresh. A five-rung descent spans roughly 114–122 seconds across multiple SSH sessions and devices. The walker discards each collection's timestamp and treats the resulting verdicts as a single state. During convergence, those states may never have coexisted.

The consequence, in A's second scenario:

```
t0    original BGP outage observed
t50   original fault recovers
t105  an unrelated physical interface fails
t115  the interface rung reads that new failure
```

The assembled vector makes the newly failed interface appear to explain the earlier outage. Every citation resolves. The chain is deterministic. Grounding passes. The model renders it as fluent causal prose.

**B, operationally.** "The outage is transient and recovers during collection" — named as *one of the most common overnight incidents*, and B independently proposed the same test: start the tool while the fault is propagating rather than after the lab has settled.

**C, methodologically.** "The protocol explicitly waits until propagation settles. It can never establish correctness while the network is changing, no matter how many settled trials are run."

**Why every existing test missed it.** A's analysis is exact and uncomfortable:

- fixtures are static, so every rung reads one frozen epoch;
- **the trial protocol deliberately waits for propagation before invoking the agent**;
- parser tests validate observations independently;
- chain tests validate rung order and citations, not temporal overlap;
- the per-collection timestamp exists but is not preserved in `DescentResult`.

The second bullet is the sharpest thing in any of the three reviews. "Poll until propagation completes, never sleep" was written as a rigour improvement. It removed the failure mode from the test surface.

**The required rule** — A's, and correctly scoped, since CLI cannot provide atomicity:

> A causal finding may be asserted only when the observations have a bounded, recorded skew and the symptom and proposed cause remain stable across the observation interval.

Where that cannot be established the answer is not `transport_blocked` or `interface_line_down`. It is `temporally_incoherent`: real observations were collected, but they do not support a single present-tense causal claim.

That is the same shape as `no_fault_on_path` — a case where the honest answer is "these observations do not support the claim you are asking me to make."

### 2.2 A layer is not a cause — A and B

**A:** rungs are defined by available CLI views rather than by independently falsifiable dependency hypotheses. The session rung reads the BGP FSM from summary; the transport rung reads a BGP-owned view from neighbour detail. No captured fault separates them.

**B, with the operational stakes:** `transport_blocked` may mean an ACL, control-plane policing, MD5 mismatch, TTL security, wrong update-source, wrong remote AS, a remote administrative shutdown, BGP process trouble, or loss severe enough to prevent establishment. **"Those have different owners and different next actions."**

This is B-430 with its real cost stated. The reason `last_reset_reason` matters is not tidiness — the layer label does not tell an operator who to page.

**A's criterion for a valid rung**, which should bind every future rung:

- a dependency assertion;
- an observation from a distinct subsystem;
- at least one sealed case where the rung above is broken and this rung is healthy;
- at least one case where this rung is broken and the rung below is healthy;
- the expected upper-layer signature if this rung alone is the cause.

### 2.3 Claims exceed evidence — all three

Enumerated in §4.

---

## 3. Single-reviewer findings that are accepted

### 3.1 The model can overwrite epistemic ground truth — A

`pin_lab_golden_snapshot` is exposed through a decorator named `_read_only_tool` and performs a persistent write. A model can pin an outage state as golden, after which drift comparison suppresses that fault indefinitely. It can also alter snapshot history used for flap detection.

This contradicts D12 (execution is never behind MCP) and D14 (memory is derived, never authored).

> A: "The architecture protects the managed network more carefully than it protects its own source of truth."

Small fix, active hole, first item.

### 3.2 Grounding proves citation topology, not truth — A

`check_grounding` does not inspect the relationship between a claim and the cited result. A report can cite an interface-down key while asserting a chassis power failure and pass. The correlation gate validates timestamp and mnemonic, not the generated event description, recurrence or `followed_a_commit` claim.

**Partly accepted.** Deterministic entailment checking over natural language is not available, so "grounding should verify truth" is not achievable. The honest position is that grounding is weaker than its name implies, and the response is A's recommendation 3 rather than a stronger check: render the authoritative report deterministically from typed fields, and mark any model paraphrase non-authoritative. That makes the limit structural rather than something a check must overcome.

Deferred — see §5.

#### Correction, 2026-08-17 — "only structural" was wrong

The paragraph above says the only available response is the structural one. That was asserted, not measured, and it is **false**. An intermediate check exists short of entailment, and it was found by asking what *else* a report contains besides relations.

**Identifier containment** (B-453): every device name, interface name, IP address and prefix appearing in a report's prose must be an identifier that appears somewhere in the evidence, canonicalised through `interface_kind`'s prefix table so `Gi0/0/0/0` and `GigabitEthernet0/0/0/0` are one identifier. Measured against round 3's real report: **zero false positives**, and it catches A's own counterexample class — a report naming `PE7` or `10.255.0.99` is refused.

It is not entailment, and the boundary is exact: **it catches an invented entity, not a wrong relation between real ones.** A report asserting a chassis power failure while citing an interface-down key still passes, because every identifier in it is real. So this raises the floor and does not replace the deferred deterministic rendering (B-439).

The reviewer position was right about the limit and wrong about the remedy set. Recording it because "no intermediate option exists" is the kind of claim that ends a search, and this one ended it one step early.

### 3.3 The trust-loss scenario — B

The most operationally useful paragraph in any of the three reviews, and it describes this fabric:

> A route-reflector session to a PE goes down during a maintenance window. The PE also has an old, intentionally shut spare interface. The actual failure is a neighbour shutdown or authentication mismatch. The tool reports `interface_line_down`, marks the answer trustworthy, and gives a complete causal chain. The transport team is paged. Twenty minutes later another engineer finds the BGP configuration problem in the neighbour detail that was available from the start.

PE1 and PE3 carry a permanently line-down `Gi0/0/0/2.300`. `EACH_PHYSICAL_INTERFACE` excludes subinterfaces by a string prefix, defined in three places, one differently (B-431). B-428 now catches the healthy-rung-1 case, but an *unrelated down physical interface* alongside a real BGP fault produces exactly this vector, and it is untested.

> B: "This is how operational tools lose adoption: not through a dramatic outage, but by wasting one bad night with a confident, specific, plausible answer."

### 3.4 Speed is a correctness consequence — B

114–122 seconds is "too slow for an interactive command that answers one session question", because an experienced operator runs those five checks faster by hand.

This reframes §2.1's fix. Collecting once and reusing across rungs is not only a correctness measure — it is what makes the tool worth running. One change addresses temporal coherence, device load (A's §4.6) and B's speed objection.

### 3.5 Report the aggregate, and name the sampling frame — C

**This corrects a decision made during the four rounds.** Refusing to report 3/4 was endorsed on the grounds that the denominator averages a working category with one that structurally could not work. C's reasoning is better:

> Refusing the aggregate because it combines a successful category with a structural failure is the wrong rationale. Deployment performance necessarily combines categories. The appropriate combination depends on their production prevalence, which is currently unknown.

Report all three strata — 3/3 on-path, 0/1 no-fault, 3/4 overall — and state that none estimates field accuracy **because the cases were not sampled from a defined population.** The problem was never the denominator. It is the sampling frame.

### 3.6 The holdout cannot be both — C

`chaos-harness.md` §3.5 says the holdout validates fixes; §11 says re-run it as a regression suite after every change. Those are incompatible.

> C: "Once its results influence a fix, model choice, parser or prompt, it is a regression set."

**Three datasets:**

| Set | Visibility | Use |
|---|---|---|
| **Development** | visible | injected during soaks, failures analysed and fixed |
| **Regression** | visible after first failure | re-run after every change, prevents known defects returning |
| **One-shot audit** | inaccessible to developers | evaluated once on a frozen release, then spent |

C also lists eight concrete leakage routes, of which two apply directly today: the same tool-aware person defining both catalogues, and local Git history being treated as an immutable seal when it can be amended or rebased.

### 3.7 Sample sizes derive from the claim — C

The harness had a duration and no target. C supplies the arithmetic:

| Claim | Representative trials required |
|---|---:|
| One-sided 95% upper bound on critical error below 5%, zero observed | **59** per stratum |
| Same, below 1% | **299** per stratum |
| Estimate a proportion to ±10 points at 95% | ~**97** per stratum |
| Estimate to ±5 points | ~**385** per stratum |

With the essential caveat: *"Fifty-nine repetitions of one tool-aware true negative do not bound the false-positive rate on production incidents. Sampling validity comes before sample size."*

---

## 4. Claims to correct now

C identified five. All are accepted and all are cheap to fix, because the fix is wording.

| Claim | Where | Correct form |
|---|---|---|
| "Exit 1 is now safe to page on" | `SESSION-HANDOVER.md` | Delete. One regression test on a known vector does not establish paging safety. |
| "Fault localisation is provisionally a pattern" | `MVP0-REVIEW.md` §4 | "The tool succeeded on these three discriminating cases." No inference beyond them. |
| "The false-positive class is closed" | B-428 notes | "The known round-4 vector now passes its regression test." |
| "The system fails toward `undetermined` rather than a wrong answer" | `MVP0-REVIEW.md` §5 | A design intention, not an observed distribution. One of four trials produced a confident false positive. |
| The organising claim, unscoped | deck, `design-thinking.md` | Scope it to the `investigate` path. `agent_loop` is a separate, explicitly untrusted path and must not share the same trust language. |

The last is A's central point and the cheapest of the five. A's defensible replacement:

> "The model cannot alter device configuration, and one investigation path localises a finding using deterministic predicates."

---

## 5. Accepted but deferred

All correct. None urgent at thirteen devices with one flow. Recorded so their deferral is a decision rather than an oversight.

| Item | Reviewer | Deferred until |
|---|---|---|
| Deterministic authoritative report rendering | A | after the epoch contract lands |
| BGP object identity: `(device, instance, bgp-instance, peer, afi-safi)` | A, B | before a second address family or VRF-scoped session |
| Passive-read admission control and fan-out limits | A, B | before any multi-device concurrent deployment |
| Both-end session evidence | B | before escalation-grade output |
| Ticket-grade run bundle and handover view | B | before workflow adoption |
| EVPN/SR object cardinality | A | before either flow is designed |
| Vendor semantic equivalence | A | blocked on hardware — B-401 |
| Service-level forwarding validation | A | before any L3VPN or EVPN claim |
| Operator utility study | C | after a characterised failure envelope exists |
| Frozen-release audit governance | C | when the harness matures |

**On C's scale.** C's thirteen trust conditions are correct for publishing a scientific result and disproportionate for deciding whether one flow is worth a second. Three are taken now — estimand definition, three datasets, and stopping the unsupported claims. The remainder is the target for a mature harness, not a precondition for the next round.

### Correction, 2026-08-17 — governance is not tooling (OBS-105)

The row *"Frozen-release audit governance — deferred until the harness matures"* is load-bearing for item 8, and this table missed it. **This document commits §0.14 in its own deferral table.**

The misclassification: governance was filed as **tooling for** the one-shot audit set — a process to be built around it later. It is not. **Governance is the definition.** §3.6's three datasets differ in exactly one respect — who may see the results and how often they may be run — and that is a governance property, not a technical one. A one-shot set with no mechanism restricting access to it is a development set that has been *described* as one-shot. There is nothing else that distinguishes them.

So item 8 cannot establish the third dataset while B-452 is deferred. **Narrow fix taken, on instruction:** item 8 writes development and regression in full and specifies the one-shot set completely, marked **specified but not established**, naming its two live leakage routes (the same tool-aware person defining both catalogues; local Git history treated as an immutable seal). B-452 stays deferred. What the deferral now costs is written down where the harness's reader will meet it, instead of appearing as a dataset the harness has.

The check that would have caught it is §0.14's fourth: *is it impossible, or impossible under the conditions you have so far?* — run in reverse. Not "can this be lifted", but **"is the deferred thing the subject's support, or is it the subject?"**

---

## 6. Order of work

| # | Item | Source | Size |
|---|---|---|---|
| **1** | Remove persistent writes from the model-visible MCP surface | A | S |
| **2** | Correct the five unsupported claims | C | S |
| **3** | Evidence-epoch contract: collect once per device, reuse across rungs, preserve per-command timestamps, re-read symptom and cause at the end, emit `temporally_incoherent` on skew or instability | **A + B + C** | M |
| **4** | Round 5 — invoke during propagation, not after | **A + B + C** | S |
| **5** | B-430: consume `last_reset_reason` as a qualifier, never as a finding | **A + B** | S |
| **6** | B-431 single interface-class definition, then round 6 — a real BGP fault plus an unrelated down physical interface | B | M |
| **7** | Rung discrimination criterion, binding on every future rung | A | S |
| **8** | `chaos-harness.md`: three datasets, estimand plan, stratum sizing | C | M |

**Items 3 and 4 are one piece of work** — the fix and the test that proves it. Item 3 also addresses A's device-load concern and B's speed objection; one change, three defects.

**Nothing below item 8 starts until the four rounds' successors are complete.**

---

## 7. What this changes about the method

Three observations worth keeping beyond the immediate actions.

**Our own rigour hid the defect.** The trial protocol polls until propagation settles, specified as an improvement over a fixed sleep. It optimised away the exact condition production troubleshooting encounters. A test protocol designed for clean measurement can systematically exclude the messiest and most common real case — and its cleanliness is what makes the exclusion invisible.

**The documents were held to a lower standard than the code.** Every mechanism in this build exists to stop the system asserting more than its evidence supports. Five claims in the documents do exactly that. The rules were applied to the artifact and not to the description of it.

**A criticism carrying a falsifier is a trial specification.** A's temporal scenario, B's trust-loss scenario, C's fresh-variant requirement for B-428 — each is a round waiting to be run. That is the most valuable form a review finding can take, and it is worth asking for explicitly next time.
