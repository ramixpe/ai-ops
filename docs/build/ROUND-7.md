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

**Corrected 2026-08-17 to what is on disk.** An earlier version of this section
carried a three-run count reported in a transcript. Two of those run directories
were deleted; the record below is the two that exist, and §4.5 explains why the
third is not carried.

| Run | Samples | Interval | Port-down samples | Route naming a down port |
|---|---:|---:|---:|---:|
| `151001` (`--dry-run`) | 162 | 1.04 s | 0 | 0 |
| **`151420`** (fault applied, restore verified) | **163** | **1.04 s** | **99** | **0** |

### 4.1 Primary claim — CONFIRMED, on one run

> A down port does not persist as `Backup (Local-LFA)`.

**99 samples with `Gi0/0/0/0` admin-down, none naming it in PE2's route to
`10.255.0.31`**, at 1.04 s resolution, with a positive control of comparable
magnitude in the same run (§4.3). `EACH_PATH_INTERFACE` cannot derive a member
set containing a dead port. **B-456's premise is sound; the soft spot in shipped
code is closed.**

**One run, and that is stated rather than padded.** It is a sound result: 99
samples spanning the whole outage, a mechanism that either persists or does not,
and an instrument shown in the same run to be able to resolve the transition.
Replication would strengthen it and its absence does not undermine it.

### 4.2 Secondary claim — SURVIVES, AND IS UNMEASURED

The 10-second falsifier did not fire, so the claim stands. **What was
established is "under 1.04 s", not a measured window**, and the harness said so
in its own verdict:

> *"No sample caught the route naming an already-down port. The window is
> bounded above by the sampling resolution (~1.035s), not measured as zero."*

**A method cannot measure a window shorter than its own resolution.** Reporting
`estimated_window_seconds: 0.0` would be absence read as presence.

### 4.3 The restore transition — scored separately, and it is a positive control

Same question, sign reversed: after `no shutdown`, does the route name the port
before it is up?

- **0 samples** name the port while it is not up.
- **1 sample** (n=105) has the port `up/up` with the route **not yet** naming it;
  n=106 names it.

That single sample is what makes the shutdown-side zero mean something. A bare
zero is consistent with *"nothing happened"* **and** with *"the instrument is too
coarse to see it"*; catching a ≤1.04 s lag on the restore side rules out the
second at that scale. **The negative is interpretable only because the same run
produced a positive of comparable size** — and it was free, because the harness
sampled through the restore rather than stopping at it.

### 4.4 The dry run is evidence that the fix works

`151001` ran `--dry-run` and produced **zero writes and zero down-port samples**.
That is not a failed round; it is the control for OBS-130's defect.

`round7.py` line 329 sets the callee's flag explicitly —
`fault_lab._dry_run = _dry` — which is exactly what `round5.py` does not do. The
run is the evidence that the correction holds: the same code path that pushed
configuration during round 5's "dry" run wrote nothing here.

### 4.5 The deleted run is not carried as a replication

A third run produced 158 down-port samples and its numbers were reported. **Its
directory was deleted, so the samples no longer exist.**

> **A count quoted in a transcript is not evidence once the payload is gone.**

This is §6.1d arriving from the other direction. That rule was written because
round 4 archived a *finding* and could not be re-examined when the semantics
changed. The same conclusion follows when inputs were archived and then lost:
**a finding without its inputs cannot be re-examined, and how it came to lack
them does not matter.**

So the 158 is recorded here as a thing that happened and is not counted. The
conclusion is unaffected — B-462 is answered by `151420` alone.
