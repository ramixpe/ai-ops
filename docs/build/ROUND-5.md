# Round 5 — invoke *during* propagation

**Item 4 of the peer review's order of work. The test that proves item 3.**

Rounds 1–4 all invoked the agent **after** the fabric had converged. Every one of
them therefore sampled a settled state, which is the one condition under which
reading five rungs at five different instants is safe. This round removes that
condition.

**Protocol tightening in force (OBS-107).** This file is **pushed** before the
fault is applied, not merely committed. Rounds 1–4 used the weaker form and
their sealing rests on trust; this one does not. The seal is checkable by
someone who was not here.

---

## 1. Subject and fault

| | |
|---|---|
| Flow | `bgp_session` |
| Device | `RR1` |
| Subject | `10.255.0.12` (PE2's loopback) |
| Pre-fault state | `all_layers_healthy`, verified live 2026-08-17, skew 5.2 s of 30 s |
| Fault | **shut `Gi0/0/0/0` and `Gi0/0/0/1` on PE2, in one commit** |

Both, in one commit, per Q-016: PE2 has two IS-IS uplinks (`Gi0/0/0/0`→P1,
`Gi0/0/0/1`→P3) and shutting one lets the IGP route around it — that was round
4's fabric, and it produced a *different* finding.

Applied by the operator. §0.11 is absolute: the agent issues no device write.

## 2. Why this fault and not a BGP shutdown

The fault has to be at the **bottom** of the ladder so it propagates **upward**
through it over a measurable interval. Shutting the BGP neighbour would drop
rung 1 immediately and there would be no propagation to invoke during.

The interval is known from this fabric's own first live trial: **154 seconds**
between the interface event and the BGP session dropping — the hold timer
expiring. IS-IS converges in seconds. So the ladder breaks bottom-up over about
two and a half minutes, and for most of that time the top of the ladder still
reads healthy.

## 3. Sampling

**Continuously, with no gap**, from before the fault until well after the
session drops. One descent takes ~6 s, so ~25 samples cover the window and every
instant in it falls inside some run's observation window. A sparse sample would
miss the transition, and the transition is the point.

`--no-model` throughout. A model call adds ~35 s per invocation (OBS-108), which
would make dense sampling impossible, and nothing being tested here is decided
by the model — `temporally_incoherent` is a descent verdict.

```
  1. supervisor confirms RR1 -> 10.255.0.12 is all_layers_healthy
  2. THIS FILE IS PUSHED                                   <- the seal
  3. sampler starts, free-running
  4. operator shuts both PE2 uplinks in one commit         <- t0
  5. sampling continues for 300 s
  6. operator restores, verifies by reading the device
  7. supervisor confirms the fabric matches golden
  8. results scored against section 4, which was pushed at step 2
```

---

## 4. Prediction, sealed

Written before the fault is applied. Each claim carries what would refute it.

### 4.1 Three phases, in this order

| Phase | Window | Rung 1 (BGP/RR1) | Rungs 3–5 | Predicted finding | Exit |
|---|---|---|---|---|---|
| **A** | t0 → ~t+150 s | healthy — hold timer running | broken | `no_fault_on_path` | **0** |
| **B** | the ~6 s straddling the drop | healthy → broken *within one epoch* | broken | **`temporally_incoherent`** | 2 |
| **C** | ~t+155 s onward | broken | broken | `interface_line_down` | 1 |

**Refuted if** the phases appear in a different order, or if phase C's finding
is anything other than `interface_line_down` (`igp_isolated` would mean the
interface rung did not read the shut ports).

### 4.2 The claim this round exists to test

> **Phase B occurs at least once.** Some run's epoch will open with rung 1
> healthy and its re-read will find rung 1 broken, and that run will report
> `temporally_incoherent` rather than a confident causal chain.

**Refuted if** no run in the whole window reports `temporally_incoherent`. Item 3
would then be a mechanism with no demonstrated occurrence — implemented, tested
against a fixture that simulates the transition, and never observed catching a
real one.

Note the honest risk: the straddle window is ~6 s of ~154 s. Free-running
sampling should cover it, but a run that starts 1 s after the drop sees a
consistent broken state and is correctly coherent. **Zero occurrences is a
plausible outcome of sampling, not only of the mechanism failing**, and the two
must not be conflated when scoring. If phase B does not appear, the honest
report is *"not observed in N samples"*, and the retry is a denser or repeated
window — not a conclusion.

### 4.3 The prediction I expect to be the useful one

> **Phase A is a wrong answer, and item 3 will not catch it.**

During phase A the fabric is genuinely broken — PE2 is isolated, RR1's route to
`10.255.0.12` is gone, traffic toward that loopback is blackholing — and the
tool will report `no_fault_on_path` and **exit 0**. The coherence check will
*pass*: rung 1 reads healthy at collection and still healthy at re-read, because
the hold timer has not expired in either instant. Stable, within bound,
coherent, and wrong.

The mechanism, precisely: B-428 says *rung 1 healthy → nothing below it can be a
cause*. That is right when the broken rungs below are **off the path** — round
4's case, a redundant uplink with the IGP reconverged. It is wrong when they are
**on the path and the symptom has not propagated yet**. `no_fault_on_path`
conflates those two, and nothing in the current descent distinguishes them.

**Refuted if** phase A reports anything other than `no_fault_on_path`, or if its
coherence check reports `ok: false`.

If this holds, it is a defect found by round 5 that item 3 does not address, and
it is directly item 7's business — the rung discrimination criterion. It also
qualifies B-428, which is the finding I have been most confident about.

### 4.4 Secondary predictions

- **Rung 3 (`route_to_peer`) breaks before rung 1.** IS-IS withdraws PE2's
  loopback within seconds; BGP holds for 180. *Refuted if* rung 1 and rung 3
  break in the same sample.
- **Skew stays under 10 s throughout**, well inside the 30 s bound, so no run is
  refused for width alone. *Refuted if* any sample exceeds 30 s.
- **No run reports `undetermined`.** Every device stays reachable; only the data
  plane between them breaks. *Refuted if* any sample is `undetermined`, which
  would mean a management-plane effect I did not predict.

---

## 5. Results

*Empty until the run. Filled from `evidence/round5/samples.jsonl`.*
