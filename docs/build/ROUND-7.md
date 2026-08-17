# Round 7 — does a down port persist as an LFA backup?

**B-462. The known soft spot in code that shipped this week.**

`EACH_PATH_INTERFACE` (B-456) takes the interface rung's member set from the
paths the route names. That is only sound if a port leaving service also leaves
the route table promptly. If a down port lingers as `Backup (Local-LFA)`, the
member set contains a dead port and the rung reports a degradation that is
really a stale route entry.

**The corpus cannot settle it (OBS-118).** 70 routes with a path, zero naming a
down interface — and **zero devices with a down physical port and a surviving
route**, so the zero is what a corpus that cannot contain the case returns.
Round 5 established this ladder gets read mid-convergence, so the question is
live rather than theoretical.

---

## 1. Fault assumed — §6.1b, and the harness disagrees with the plan

> **Fault assumed: `fault_lab.py` fault 3, `phys_shut_one` — "Physical shutdown
> on one core interface", `interface Gi0/0/0/0 / shutdown` on PE2, whose note
> reads "the IGP has an alternate path".**

**`round5.py` cannot run this round unchanged.** Its `APPLY` block is hardcoded
to fault 1, `isis_shut_both` — `router isis CORE / interface … / shutdown`,
whose own note says *"physical interfaces stay up/up"*. That produces **zero
down ports** and would answer nothing about this question.

This is precisely the check §6.1b exists for, added after round 5's prediction
assumed a fault the harness did not apply. It cost that round a scored
prediction; here it was caught before the seal.

**So the operator applies fault 3**, either by editing `round5.py`'s `APPLY`/
`REVERT` to fault 3's lines or by running `fault_lab.py` and selecting 3.

| | |
|---|---|
| Device | **PE2** |
| Fault | `interface Gi0/0/0/0` → `shutdown` (one uplink of two) |
| Surviving path | `Gi0/0/0/1` toward P3 |
| Restore | `interface Gi0/0/0/0` → `no shutdown`, verified by reading |

## 2. What is sampled

`scripts/round7_sampler.py`, free-running, reading **PE2** directly:

- `show route 10.255.0.31/32` — every named path, its interface and `path_role`
- `show interfaces brief` — every physical port's admin and line state
- the derived member set `descent.path_interfaces` would produce

Read-only throughout. The injector is the operator; the sampler never writes.

**Full payload archived per sample (§6.1d)**, not the derived answer — a finding
cannot validate a change to the layer that produced it.

---

## 3. Prediction, sealed

Written and **pushed** before the fault is applied.

### 3.1 The primary claim

> **A down port disappears from the route table within one IGP reconvergence and
> does not persist as an LFA backup.** Concretely: within 10 seconds of the
> shutdown, `show route 10.255.0.31/32` on PE2 names **only** `Gi0/0/0/1`, and
> `Gi0/0/0/0` appears in no path record at any sample thereafter.

**Refuted if** `Gi0/0/0/0` appears as `Backup (Local-LFA)` in any sample taken
after the interface reads `admin-down`. Any single such sample refutes it —
this is not a rate.

**If refuted, `EACH_PATH_INTERFACE` needs a liveness cross-check** against
`show interfaces` before a named path counts as a member, which is a different
rung rather than a tweak, and B-456 ships with a known defect until it lands.

### 3.2 The secondary claim, and it is the one I am less sure of

> **There is a window in which the route names `Gi0/0/0/0` while the interface
> already reads down.** IS-IS must notice, run SPF and reinstall; the port state
> changes at once. So I expect a **non-zero but sub-second to low-single-digit**
> gap, probably invisible at a ~2 s sample interval.

**Refuted if** the gap exceeds 10 seconds — which would make the race routinely
reachable by a real investigation rather than a corner, and would promote the
liveness cross-check from prudent to required even if §3.1 holds.

**Uninterpretable if** the sampler's own interval is coarser than the gap, which
is why it free-runs rather than sleeping.

### 3.3 What this round cannot establish

One device, one topology, one IGP with LFA configured. It says nothing about a
fabric without LFA, about BGP PIC, or about a platform whose RIB behaves
differently. A negative here is *"not observed on this fabric"*, not
*"cannot happen"* — and the difference matters, because B-456 ships to whoever
runs it.

---

## 4. Results — scored, B-462 closed

Two runs are archived under `evidence-archive/round7/`. The operator reports a
third; **I can only find two in `faultlab/round7/`, and I am not reconciling the
count by inference** — see §4.5.

| Run | Samples | Interval | Port-down samples | Route naming a down port |
|---|---:|---:|---:|---:|
| `151001` (dry-run; fault never applied) | 162 | 1.04 s | **0** | 0 |
| `151420` (fault applied, restore verified) | 163 | 1.04 s | **99** | **0** |

### 4.1 Primary claim — CONFIRMED

> A down port does not persist as `Backup (Local-LFA)`.

**99 samples with `Gi0/0/0/0` admin-down, and not one names it in PE2's route to
`10.255.0.31`.** `EACH_PATH_INTERFACE` cannot derive a member set containing a
dead port, so **B-456's premise is sound and the soft spot in shipped code is
closed.**

### 4.2 Secondary claim — SURVIVES, AND IS UNMEASURED

The 10-second falsifier did not fire, so the claim stands. **What was
established is "under 1.04 s", not a measured window**, and the harness said so
itself:

> *"No sample caught the route naming an already-down port. The window is
> bounded above by the sampling resolution (~1.035s), not measured as zero."*

**A method cannot measure a window shorter than its own resolution.** Reporting
`estimated_window_seconds: 0.0` as a result would be absence read as presence —
the same error the whole `unevaluated` discipline exists to prevent, arriving in
a measurement rather than a check.

### 4.3 The restore transition — scored separately, and it is a positive control

Free, because the harness sampled through the restore. Same question, sign
reversed: after `no shutdown`, does the route name the port before it is up?

- **0 samples** name the port while it is not up. Same answer as the shutdown
  side, in the other direction.
- **1 sample** (n=105) has the port `up/up` with the route **not yet** naming
  it; n=106 names it.

That single sample is worth more than it looks. **It is evidence that the
instrument can resolve a transition of this size.** A bare zero on the shutdown
side is consistent with "nothing happened" *and* with "the sampler is too coarse
to see it"; catching a ≤1.04 s lag on the restore side rules out the second for
lags of that scale. The shutdown-side zero is therefore a stronger negative than
an unaccompanied zero would be — and it is the only reason §4.2's bound means
anything.

### 4.4 The first run counts, with a caveat

Run `151001` claimed `--dry-run` and its port stayed `up` across all 162
samples, so it contributed **no down-port observations**. Its value is as an
independent replication of the *baseline*: 162 samples in which the route names
both ports and both are up.

**Its restore was never verified** — `RESTORE verified (dry-run: nothing
written)` is the same defective guard described in §5. The fabric was healthy at
the next run's baseline, which is **luck rather than evidence**: nothing read the
device to establish it.

### 4.5 A count I cannot reconcile, recorded rather than resolved

The operator reports **three runs and 158 + 99 down-port samples**. In
`faultlab/round7/` there are **two** run directories, with **0** and **99**
down-port samples. A run with 158 is not present.

I am not inferring where it went. Either a third run exists outside that
directory, or the 158 is from a source I have not been shown. **The conclusions
are unaffected** — every sample in every run I can read has zero persisting
observations — but the count in this document is the count I measured, and the
discrepancy is a question for the operator rather than something to average out.

