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

## 4. Results

*Empty until the run.*
