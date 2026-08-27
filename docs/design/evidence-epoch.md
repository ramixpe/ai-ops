# Evidence Epoch

**Approved 2026-08-17 — D1, D2 and D3 as recommended, with one addition to D2.**

Addresses the convergent finding (§2.1, all three reviewers) and A's device-load concern (§4.6).

> **Correction, 2026-08-17, after implementation.** This document originally claimed the change also answers B's speed objection (§3.4) — "one change, three defects". **Measured, it does not.** The deterministic descent went from **8.5 s to 5.8 s** live; the 114–122 s an operator experiences is ~105 s of model latency from two calls in the interactive path, which this change does not touch. The claim was inherited from the review and never measured by anyone, including me. B's objection is answered by **B-439** (deterministic report rendering), not by this. See OBS-108 in `docs/build/FINDINGS.md`; the point-in-time peer response remains in Git history.

---

## 1. What is wrong today, measured

Each rung calls `_collect_for_rung()`, which calls `collect_evidence()` afresh:

| | Today | With an epoch |
|---|---|---|
| `collect_evidence()` calls | **5** (one per rung) | **2** (one per device) |
| Intent commands | 35 | 14 |
| Template commands | 5 | 5 |
| **Total commands** | **~40** | **19** + 2 re-reads |
| SSH sessions | 5 | 2 |

RR1 is fully re-collected three times and PE2 twice, for data that does not change between rungs. **Roughly 54% of the commands are redundant** — and the redundancy is not merely waste, it is the mechanism of the defect: each re-collection observes a *different instant*, and the walker treats the resulting verdicts as one state.

A's scenario, which every existing test misses:

```
t0    original BGP outage observed          <- rung 1 reads this
t50   original fault recovers
t105  an unrelated physical interface fails
t115  the interface rung reads that new failure
```

Every citation resolves. The chain is deterministic. Grounding passes. The model renders fluent causal prose about a state that never existed.

---

## 2. The contract

### 2.1 `Observation` and `EvidenceEpoch`

```python
@dataclass(frozen=True)
class Observation:
    key: str            # "bgp", or "bgp_neighbor:10.255.0.12" -- today's convention
    device: str
    started: float      # monotonic, at dispatch
    completed: float    # monotonic, at response
    envelope: dict      # the existing tool envelope, byte-identical

@dataclass(frozen=True)
class EvidenceEpoch:
    observations: dict[str, Observation]
    opened: float
    closed: float

    @property
    def skew_seconds(self) -> float: ...
    def for_device(self, device: str) -> dict[str, dict]: ...   # {key: envelope}
```

**`for_device()` returns exactly the dict shape `checks.py` reads today.** No check changes. No evidence key changes. That is deliberate: it keeps this a change to *when* evidence is gathered, not to *what a check sees*.

### 2.2 Collection is one pass per device

Before the walk, resolve every device the flow will touch and collect each once, in a single session.

**This is only sound because every collection target is knowable upfront.** For `bgp_session`: `route:<subject>/32` and `bgp_neighbor:<subject>` come from the subject; the `interface:<member>` set comes from the `interfaces` intent on the same device, in the same pass.

> **A flow whose collection depends on an earlier rung's *verdict* cannot use a single epoch.**

No flow does today, and `SubjectRule` resolves from the subject or the device, never from a prior result.

**This is a documented precondition on `Flow`, not an internal guard.** The distinction matters and was called out in review: a guard inside the epoch builder tells a flow author they were wrong *after* they have written the flow, and it tells them in a stack trace. A precondition on the type they are writing tells them the constraint exists *before* they design around it. So it is stated in `Flow`'s docstring, next to `Rung` and `SubjectRule`, in the place someone reads while writing a new ladder — and the builder still raises, as the enforcement of a stated rule rather than as the only place the rule appears.

The rule in the form a flow author needs it:

> Every collect step in every rung must be resolvable from the **subject and the device alone**, before the walk begins. A rung may not collect something whose identity depends on what an earlier rung concluded.

### 2.3 Coherence is established by the re-read, not by the skew

This is the design's reason, not a caveat on it, so it is stated before the mechanism.

A's rule reads: *a causal finding may be asserted only when the observations have a bounded, recorded skew and the symptom and proposed cause remain stable across the observation interval.* A natural implementation reads that as two thresholds and checks both. **That implementation would be theatre**, and here is why.

> **Skew does not establish coherence. The re-read does.**
>
> An interface can change state in under a second. There is therefore **no non-zero skew that is provably safe** — a bound of 30 s and a bound of 3 s differ in how likely they are to hide a transition, not in whether they can. A design that passed a run because its skew came in under a threshold would be asserting a guarantee that no threshold can supply.
>
> **The bound's job is to say what a two-point re-read is worth.** Two agreeing reads across 20 seconds are strong evidence that nothing moved. The same two reads across 200 seconds are two samples from a window in which anything could have happened. The bound does not certify the interval; it calibrates the only instrument that says anything about the interval at all.

So the skew is a **precondition on trusting the re-read**, and the re-read is the check. Both halves of A's rule are kept, with the work assigned to the half that can do it.

**Mechanism.** After the walk, re-read two things — the **symptom** (rung 1) and the **cause rung** — and compare the verdicts to the epoch's. Two commands.

| Skew | Re-read | Result |
|---|---|---|
| within bound | agrees | `coherent` — the finding stands |
| within bound | disagrees | **`fabric_moved`** → `temporally_incoherent`, exit 2 |
| over bound | agrees | **`window_limited`** → the finding stands, **qualified** |
| over bound | disagrees | **`fabric_moved`** → `temporally_incoherent`, exit 2 |

> **Revised 2026-08-17 after round 5 (B-454).** The third row originally read
> `temporally_incoherent`, and round 5 measured what that costs: probes 10, 11
> and 99 saw a settled, fully converged broken fabric whose correct finding was
> `igp_isolated`, and the bound threw it away four minutes after the fabric
> stopped changing.
>
> **The asymmetry is the point.** A disagreeing re-read is *positive evidence*
> that the premise of a causal claim is false — refuse. A wide window with both
> ends agreeing is only *absence of evidence* about the middle of an interval
> whose endpoints matched — qualify. Absence of evidence is exactly what this
> layer refuses to convert into a verdict everywhere else, and converting it
> into a refusal is the same mistake in the other direction.
>
> This is the treatment `COVERAGE_LIMITED` already gives a correlation the log
> source could not fully support: *a real answer at the wrong strength is worth
> more than no answer.* The pattern existed and was not applied here.

### 2.3a The skew is recorded when it passes, not only when it fails

**A bound that only speaks when violated says nothing about how close we routinely run.**

Every result carries `skew_seconds` and the bound it was measured against, whatever the outcome. If real epochs land at 25 s against a 30 s bound, that is a finding — the tool is one slow device away from refusing every answer, and nobody would know until it started. A silent pass and a comfortable pass look identical from outside, and only one of them is safe to leave alone.

This is the same discipline as `coverage.gaps()` and `unaccounted_lines`: report the margin, not just the breach. A threshold that is only ever observed at the moment it fails has no observed distribution, and a limit with no distribution behind it cannot be revised on evidence — only on argument.

### 2.4 `temporally_incoherent`

A new member of the closed finding set, the same shape as `no_fault_on_path`:

> Real observations were collected, and they do not support a single present-tense causal claim.

**Exit code 2.** Under the T-031 scheme exit 1 means *the network is broken* and exit 2 means *no trustworthy answer was produced*. This is the second: the fabric may be fine or broken, and this run cannot say which. The observations and the skew are still reported — the run produced evidence, just not a finding.

---

## 3. Three decisions, taken 2026-08-17

All three as recommended. D2 gained one addition — §2.3a, record the skew on a pass as well as a breach.

### D1 — where the finding is decided → (b)

`_finding_for()` in `descent.py` owns every finding today. The coherence check needs the epoch and a re-read, which live in `investigation.py`.

| | Blast radius | Cost |
|---|---|---|
| **(a)** `investigation.py` overrides the finding after the walk | `descent.py` untouched except an optional `epoch` field on `DescentResult` | **Splits finding authority across two modules.** `_finding_for` would no longer be the single place a finding is decided |
| **(b) *recommended*** | `run_descent(..., coherence=...)` takes a coherence verdict; `_finding_for` consults it first | `descent.py` gains one parameter and one branch; `investigation.py` computes the verdict and passes it. Finding authority stays in one function |

**Taken: (b).** The extra parameter is cheap; splitting the authority is the kind of thing that is invisible until two places disagree.

### D2 — the skew bound → derived, plus §2.3a

| Option | |
|---|---|
| Fixed default, e.g. 30 s | Arbitrary, but honest and adjustable |
| **Derived from the flow *(recommended)*** | The slowest protocol timer the ladder depends on, since that is the interval over which a state change could complete unobserved. For `bgp_session`: BGP hold 180 s, IS-IS hold 30 s → bound at the **fastest** relevant timer, 30 s, because the fastest is what can change within the window |
| No bound, re-read only | Simplest; discards A's first half |

**Taken: derived, defaulting to 30 s for this flow, configurable.** It is a number with a reason attached, which is the difference between a bound and a guess. Note today's descent takes **114–122 s**, so it would exceed a 30 s bound *until the epoch change lands* — which is the point: the current implementation cannot satisfy the rule, and the epoch is what makes ~20 s achievable.

### D3 — what the re-read covers → symptom + cause rung

| Option | |
|---|---|
| **Symptom + cause rung *(recommended)*** | Two commands. Covers A's scenario exactly: the symptom recovering, or the cause appearing late |
| Every rung | Doubles the descent's cost and re-introduces the skew problem inside the re-read itself |
| Symptom only | Misses the case where the *cause* appeared after the symptom was read — which is A's scenario |

---

## 4. What this does not fix

Stated so the change is not over-claimed when it lands.

- **It does not make the reads atomic.** Nothing available over CLI can. It bounds and reports the skew, and refuses when the bound is exceeded.
- **A fault that flaps faster than the re-read interval can still produce a coherent-looking vector.** Two agreeing samples do not prove the interval between them was quiet.
- **It says nothing about correctness during propagation** — that is round 5 (B-451), the test, and it is the other half of this work.
- **It does not address §2.2** (a layer is not a cause) or §3.2 (grounding proves topology, not truth). Different defects, separately filed.

---

## 5. Order

1. `EvidenceEpoch` + one-pass collection, with `for_device()` returning today's shape. **No behaviour change** — every existing test must pass untouched, which is the check that the shape is preserved.
2. Skew recording, and `epoch` on `DescentResult`.
3. Re-read, coherence verdict, `temporally_incoherent`, exit 2.
4. Round 5 (B-451) — invoke during propagation. The test that proves it.

Step 1 landing green with no test changes is the evidence that the contract is unchanged; steps 2–3 are where the semantics move.
