# Evidence Epoch — design for review

**Item 3 of `peer-review-response.md` §6. Not implemented — this is the design the standing rules require before a shared-contract change is written.**

Addresses the convergent finding (§2.1, all three reviewers), A's device-load concern (§4.6) and B's speed objection (§3.4). One change, three defects.

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

No flow does today, and `SubjectRule` resolves from the subject or the device, never from a prior result. That is a property worth asserting rather than assuming, so the epoch builder raises if a flow declares a collect step it cannot resolve before the walk.

### 2.3 Coherence is established by re-read, not by skew

A's rule: *a causal finding may be asserted only when the observations have a bounded, recorded skew and the symptom and proposed cause remain stable across the observation interval.*

Both halves, and they do different work:

**Skew is recorded, always, and bounds the window in which incoherence could hide.** It does not prove coherence. An interface can change state in under a second, so no non-zero skew is provably safe, and a threshold alone would be theatre.

**The re-read is what establishes stability.** After the walk, re-read two things — the **symptom** (rung 1) and the **cause rung** — and compare the verdicts to the epoch's. Two commands.

| Skew | Re-read | Result |
|---|---|---|
| within bound | agrees | the finding stands |
| within bound | disagrees | **`temporally_incoherent`** |
| over bound | agrees | **`temporally_incoherent`** — stability was sampled at two points across a window too wide to interpolate |
| over bound | disagrees | **`temporally_incoherent`** |

The skew bound's role is to say *how much a two-point re-read is worth*. Over a 20-second epoch, two agreeing reads are strong evidence nothing moved. Over 200 seconds they are two samples from a window in which anything could have happened between them.

### 2.4 `temporally_incoherent`

A new member of the closed finding set, the same shape as `no_fault_on_path`:

> Real observations were collected, and they do not support a single present-tense causal claim.

**Exit code 2.** Under the T-031 scheme exit 1 means *the network is broken* and exit 2 means *no trustworthy answer was produced*. This is the second: the fabric may be fine or broken, and this run cannot say which. The observations and the skew are still reported — the run produced evidence, just not a finding.

---

## 3. Three decisions I need, and what I recommend

### D1 — where the finding is decided

`_finding_for()` in `descent.py` owns every finding today. The coherence check needs the epoch and a re-read, which live in `investigation.py`.

| | Blast radius | Cost |
|---|---|---|
| **(a)** `investigation.py` overrides the finding after the walk | `descent.py` untouched except an optional `epoch` field on `DescentResult` | **Splits finding authority across two modules.** `_finding_for` would no longer be the single place a finding is decided |
| **(b) *recommended*** | `run_descent(..., coherence=...)` takes a coherence verdict; `_finding_for` consults it first | `descent.py` gains one parameter and one branch; `investigation.py` computes the verdict and passes it. Finding authority stays in one function |

**(b).** The extra parameter is cheap; splitting the authority is the kind of thing that is invisible until two places disagree.

### D2 — the skew bound

| Option | |
|---|---|
| Fixed default, e.g. 30 s | Arbitrary, but honest and adjustable |
| **Derived from the flow *(recommended)*** | The slowest protocol timer the ladder depends on, since that is the interval over which a state change could complete unobserved. For `bgp_session`: BGP hold 180 s, IS-IS hold 30 s → bound at the **fastest** relevant timer, 30 s, because the fastest is what can change within the window |
| No bound, re-read only | Simplest; discards A's first half |

**Derived, defaulting to 30 s for this flow, configurable.** It is a number with a reason attached, which is the difference between a bound and a guess. Note today's descent takes **114–122 s**, so it would exceed a 30 s bound *until the epoch change lands* — which is the point: the current implementation cannot satisfy the rule, and the epoch is what makes ~20 s achievable.

### D3 — what the re-read covers

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
