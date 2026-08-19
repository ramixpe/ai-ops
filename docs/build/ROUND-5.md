# Round 5 — invoke *during* propagation

<!-- knowledge-search:exclude -- evaluation material (B-511) -->

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

## 5. Dry run, 2026-08-17 09:29 — one prediction already refuted

The harness was dry-run first (no fault applied, fabric healthy). **Three probes,
all three `temporally_incoherent`, all five rungs healthy, skew 56–61 s against
the 30 s bound.**

> **Prediction 4.4 is refuted.** *"Skew stays under 10 s throughout… refuted if
> any sample exceeds 30 s."* It exceeded it on every sample, before any fault
> existed. Recorded as a refutation rather than amended away.

### What it was, and what it was not

The first explanation was wrong and is recorded because the correction is the
useful part. The unbatched epoch opened **7 SSH sessions, 5 of them to run a
single command**, and during the dry run each login cost ~10 s. That looked
structural, and it is not:

| | Sessions | Skew |
|---|---:|---:|
| Before batching, devices responsive (09:1x) | 7 | **5.2 s** |
| Before batching, devices degraded (09:29–09:35) | 7 | **61–75 s** |
| After batching, devices responsive (09:4x) | 4 | **3.8–4.0 s** |

Isolated afterwards: one `run_template` costs **0.52 s**, not 11 s. So batching
did **not** take 61 s to 3.9 s — the devices recovered. Claiming the fix would
have been OBS-108's error a third time in one session.

**Two separate results, kept separate:**

**Batching is a real fix and was already required by the approved design** —
§2.2 says "one pass per device, in a single session" and the first implementation
did not do it. `run_templates_split` uses the existing, already-reviewed
`run_templates`, so no authorization path changed. Sessions 7 → 4, permanently.

**The 61 s episode is the finding, and it is about availability, not speed.** The
bound is sensitive to device SSH responsiveness, which is not under the tool's
control. When responsiveness degraded, the tool **refused to answer about a
completely healthy fabric** — five rungs healthy, re-read agreeing, refused for
width alone. That is the bound working as designed and it is worth stating
plainly: *this tool's willingness to answer depends on how fast the devices feel
like replying.* Whether refusing is right at 61 s is a real question and not one
this round settles.

**A hypothesis tested and refuted:** that the tool's own repeated use degrades
login cost, so dense sampling would be self-defeating. Twelve back-to-back
epochs, skew flat at 3.8–4.0 s. No feedback loop at 4 sessions per epoch. Dense
sampling is viable.

---

## 6. Re-seal, 2026-08-17 — before the fault

The code changed after §4 was sealed (batching), so §4 is re-sealed here rather
than silently inherited. **Predictions 4.1, 4.2 and 4.3 are unchanged in
substance** — none of them was tested by the dry run, which applied no fault.

**4.4 is replaced**, and the replacement is weaker on purpose, because the
original was written from a single unrepresentative measurement:

> Skew stays **under 10 s** on a responsive fabric and no sample is refused for
> width alone. *Refuted if* any sample exceeds 30 s while every device is
> answering normally.

The condition attached to the falsifier is the honest part: a sample refused for
width during a device slowdown refutes nothing about the ladder, and conflating
the two is what the dry run nearly caused me to do.

---

## 7. Results — 2026-08-17 09:44:26 → 09:51:41 UTC

Fault applied 09:44:42, `sent+committed+exited`. Restored and **verified by
reading the device** on attempt 2; PE2 back to 2 IS-IS adjacencies. 13 probes.

Rung columns are `bgp_session · transport · route_to_peer · igp_adjacency ·
interface`; `.` healthy, `X` broken.

| Probe | T+ | Rungs | Finding | Exit | Skew | Re-read |
|---|---:|:---:|---|:---:|---:|---|
| 00 | −1 s | `.....` | `all_layers_healthy` | 0 | 4.0 s | stable |
| 01 | 6 s | `...X.` | `no_fault_on_path` | 0 | 3.9 s | stable |
| 02 | 16 s | `...X.` | `no_fault_on_path` | 0 | 4.1 s | stable |
| 03 | 24 s | `..XX.` | `no_fault_on_path` | 0 | 4.0 s | stable |
| 04–07 | 32–56 s | `..XX.` | `no_fault_on_path` | **0** | ~4.0 s | stable |
| 08 | 64 s | `..XX.` | `temporally_incoherent` | 2 | **34.0 s** | stable |
| **09** | **117 s** | `.XXX.` | `temporally_incoherent` | 2 | 38.5 s | **`bgp_session` healthy → broken** |
| 10 | 179 s | `XXXX.` | `temporally_incoherent` | 2 | 38.2 s | stable |
| 11 | 235 s | `XXXX.` | `temporally_incoherent` | 2 | 36.3 s | stable |
| 99 | 340 s | `XXXX.` | `temporally_incoherent` | 2 | 34.9 s | stable |

### 7.1 Scoring against the sealed predictions

**4.2 — CONFIRMED. The mechanism fired on a real transition.**

> Probe 09: rung 1 read **healthy** when the epoch opened and **broken** when it
> was re-read 38 s later. `stable: false`, `agrees: false`.

This is item 3 doing exactly what it was built to do, on a live fabric, on the
first attempt. Before B-436 that probe would have reported a confident causal
chain assembled from a symptom that was absent when it was read and present when
the walk finished. It is the first observation in this build of the coherence
check catching a real state change rather than a simulated one.

**4.3 — CONFIRMED, and it is the most serious defect this build has found.**

> Probes 01–07, **seven consecutive samples over 50 seconds**:
> `no_fault_on_path`, **exit 0**, `trustworthy: true`, and the coherence check
> **passing** — within bound, stable, `ok: true`.

From probe 03 onward the rungs read `..XX.`: **RR1 has no route to
`10.255.0.12`** and PE2's IS-IS is down. Traffic to that loopback is
blackholing. The tool reports *no fault on the dependency path* and exits 0.

The mechanism is exactly as predicted. B-428 says *rung 1 healthy → nothing
below it can be a cause*, which is right when the broken rungs are **off the
path** (round 4: a redundant uplink, IGP reconverged, session carrying traffic)
and wrong when they are **on the path and the symptom has not propagated yet**.
The BGP hold timer is 180 s; for most of that window the top of the ladder still
reads Established while everything under it is gone.

**Item 3 does not catch it, and could not.** The coherence check asks whether the
observations describe one state. They do: rung 1 really was healthy at both
reads. The epoch is coherent and the answer is wrong. Temporal coherence and
causal correctness are independent properties, and this round separates them
experimentally rather than by argument.

**4.1 — partly refuted, and the refutation is my error, not the tool's.**

Phase order held: A (01–07) → B (09) → C (10, 11, 99). But I predicted phase C
would report `interface_line_down`, and its own falsifier fired: *"`igp_isolated`
would mean the interface rung did not read the shut ports."*

The interface rung reads **healthy in every single probe**, because **the ports
were never shut.** §1 of this document says "shut `Gi0/0/0/0` and `Gi0/0/0/1`",
and the harness actually applies `router isis CORE / interface … / shutdown` —
which disables IS-IS *on* the interfaces and leaves the interfaces up. I wrote
the prediction from my own §1 without reading the harness's `APPLY` block
closely enough. The tool was right and my description of the fault was wrong.

Worth keeping because of what it nearly cost: had the phase-C finding not been
masked by the bound, I would have scored a correct `igp_isolated` as a
prediction failure. **A falsifier written against a misdescribed setup fires on
the truth.** §0.13's fifth face — the setup — with the harness as the part I did
not read.

**4.4 (re-sealed) — refuted, and this time not by a device slowdown I can wave
at.** Skew was 3.9–4.1 s for probes 00–07 and jumped to 34–38 s from probe 08
onward, holding there for the rest of the run. The jump is *inside* the fault
window and coincides with the BGP session beginning to fail. I have a
correlation and not a mechanism: nothing recorded per-observation timings in
these payloads, so which command slowed is not recoverable from this run.

### 7.2 The finding this round produced that nobody predicted

**`temporally_incoherent` conflates two different failures, and in phases B and C
it destroyed a correct answer.**

Probe 09 was incoherent because **the fabric moved** — the real signal, the thing
the finding exists for. Probes 08, 10, 11 and 99 were incoherent because
**collection was slow**, with the re-read agreeing every time. Same finding
string, same exit 2, and an operator cannot tell them apart.

The cost is concrete. Probes 10, 11 and 99 had rungs `XXXX.` — a settled, fully
converged broken fabric whose lowest broken rung is `igp_adjacency`. **The
correct answer was `igp_isolated`, and the bound replaced it with a refusal.**
The tool knew the answer and threw it away because it had taken 36 seconds to
collect it, on a fabric that had been in that state for four minutes.

This build already has the right pattern for this and did not apply it here.
`COVERAGE_LIMITED` keeps the correlation and labels it, on the reasoning that
*"a real answer at the wrong strength"* is worth more than no answer. A
width-only breach is the same shape: the finding stands, qualified. Instability
is different — there the finding genuinely is unsupportable.

So the current design is wrong in one specific way: **skew-breach and
re-read-disagreement should not produce the same finding.** Filed as **B-454**.

### 7.3 What round 5 establishes

| Claim | Status |
|---|---|
| The coherence check catches a real transition on a live fabric | **demonstrated once**, probe 09 |
| `no_fault_on_path` is wrong during upward propagation | **demonstrated, 7 consecutive samples** |
| Temporal coherence and causal correctness are independent | **demonstrated** — 01–07 are coherent and wrong |
| The bound destroys correct answers on a slow collection | **demonstrated**, probes 10/11/99 |
| Skew degrades during a fault, cause unknown | correlation only |

One trial, one fault, one fabric. None of this is a rate.
