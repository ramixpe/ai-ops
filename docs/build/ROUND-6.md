# Round 6 — the trust-loss scenario, and whether B-456 already prevents it

**B-440.** Reviewer B, §3.3. The most operationally useful paragraph in any of the three
reviews, and it describes this fabric:

> A route-reflector session to a PE goes down during a maintenance window. The PE also has
> an old, intentionally shut spare interface. The actual failure is a neighbour shutdown or
> authentication mismatch. The tool reports `interface_line_down`, marks the answer
> trustworthy, and gives a complete causal chain. The transport team is paged. Twenty
> minutes later another engineer finds the BGP configuration problem in the neighbour
> detail that was available from the start.

> B: *"This is how operational tools lose adoption: not through a dramatic outage, but by
> wasting one bad night with a confident, specific, plausible answer."*

**This round exists because two faults are involved and every trial so far has injected
one.** It is the only round whose failure mode is a *correct-looking* answer.

---

## 1. §6.1b — the fault, and what this prediction assumes about it

**Two changes, applied together, and the second is the point.**

```
option 8   router bgp 65000 / neighbor 10.255.0.31 / shutdown       <- the real fault
           interface GigabitEthernet0/0/0/2 / shutdown              <- the spare, unrelated
```
revert: `no shutdown` on both.

> **Amended before the run — this said "option 5 plus a shut `Gi0/0/0/2`", and that
> was not a runnable fault.** `fault_lab.py`'s option 5 is the BGP shutdown alone;
> nothing in the table applied both lines, so the seal described a fault the injector
> could not produce. **Option 8 now exists and applies both in one commit**, which is
> also what makes them concurrent rather than sequential.
>
> **And the second half of the amendment matters more than the first.**
> `SNAPSHOT_SECTIONS` did not include `Gi0/0/0/2`, and restore verification is an
> exact comparison of *those sections only*. A section that is not listed **cannot
> fail the check no matter what is left in it** — so a `shutdown` lingering on the
> spare port would have been inert on a restored fabric *and* invisible to the
> verification built to catch exactly that.
>
> §0.1 of this document already names *"harmless and undetectable is the combination
> worth checking for"*. It was written about the fault. It arrived in the machinery
> that checks the fault, one layer out, and the sealed text is what pointed at it.

**Why `Gi0/0/0/2` and not the subinterface B named.** PE1 and PE3 carry a permanently
line-down `Gi0/0/0/2.300`, and `EACH_PATH_INTERFACE` excludes subinterfaces **by
construction** — so using it would test a filter that is already known to hold and would
report a pass for the wrong reason. `Gi0/0/0/2` on PE2 is a **physical** port, up today,
and not on the path from PE2 back toward RR1. It is reviewer B's "old, intentionally shut
spare interface" made real.

**This raises the concurrent-fault ceiling to 2**, which `chaos-harness.md` §3.3 permits
only under explicit configuration. Stated here, in the seal, because it is the first round
to do it.

**What this round assumes about the fault:** that a `neighbor shutdown` breaks the BGP
session **without disturbing the IGP or the route to the peer**. If IS-IS or the route
also drops, this is a different round and §2.3 below applies instead.

---

## 2. Prediction, sealed

Subject: `nettools investigate RR1 10.255.0.12`.

### 2.1 The rung vector

> **Expected: `B B H H H` → `transport_blocked`.**

| Rung | Device | Expected | Why |
|---|---|---|---|
| 1 `bgp_session` | RR1 | **broken** | the neighbour is shut; the session is Idle |
| 2 `transport` | RR1 | **broken** | an admin-shut neighbour tears the socket down |
| 3 `route_to_peer` | RR1 | healthy | IGP untouched; RR1 still has a route to `10.255.0.12/32` |
| 4 `igp_adjacency` | PE2 | healthy | PE2's uplinks are up |
| 5 `interface` | PE2 | **healthy** | **the claim** |

### 2.2 The claim this round exists for

> **Rung 5 reports healthy, and the shut `Gi0/0/0/2` is not named as the cause.**
>
> Since B-456, `EACH_PATH_INTERFACE` takes its member set from **the route the subject
> device holds back toward the origin**, aggregated `ANY_HEALTHY`. `Gi0/0/0/2` is not on
> that route, so it is not in the member set, so it is not evaluated.

**Refuted if** the finding is `interface_line_down`, or rung 5 is `broken`, or
`Gi0/0/0/2` appears anywhere in the report's prose. **Any one of those is reviewer B's
scenario reproduced**, and it would mean B-456 narrowed the member set without closing
this.

**This is a prediction that the defect is already fixed**, which is a weaker thing to
confirm than a discovery — and it is worth running anyway, because *"we believe B-456
covers this"* and *"B-456 covers this"* are different claims and only one of them has
been measured. B-456 was built for aggregation correctness on an isolated device; that it
also closes B's scenario is an **inference from its member-set rule, not an observation.**

### 2.3 The residual exposure, sealed separately because it is the honest part

> **The scenario stays reachable through the fallback, and this round does not close it.**

`_rung_subjects` falls back to *all physical interfaces* with `ALL_HEALTHY` when the
member set is empty — which is correct, because an isolated device with no route must not
report `ANY_HEALTHY` over nothing. But it means:

**If a fault breaks BGP *and* removes the route to the peer, rung 5 evaluates every
physical interface, and an unrelated shut port makes it broken.** That is exactly B's
paragraph, arriving by a path this round does not exercise.

> **Sealed: the trust-loss scenario is prevented for any fault that leaves rung 3 healthy,
> and remains live for any fault that does not.**

Round 6 tests the first half. **The second half needs its own round** — the same spare
port plus a fault that kills the route — and if this round confirms §2.2, that round is
the more valuable of the two and should be filed before this one is closed.

### 2.4 What the report must not say

Beyond the finding: the four healthy rungs are reported as observations, and **`off_path`
must not list `Gi0/0/0/2` as a broken layer without saying it is off path.** B-428's
`no_fault_on_path` handling established the vocabulary; this checks it is used when the
finding is a real fault rather than a clean bill.

**Refuted if** the prose names the shut port as a contributing cause anywhere, even
hedged. *"There is also a down interface on PE2"* is the sentence that pages the transport
team.

### 2.5 Timing

> **The session drops within seconds.** An administrative `neighbor shutdown` sends a
> notification immediately; there is no hold timer involved.

**Refuted if** nothing changes for more than 30 s after the commit — which would mean the
push did not take, not that the prediction is wrong. Check the config, not the clock.

---

## 3. What this round cannot establish

**It is one trial of one two-fault combination.** Q-019 — *is "lowest broken rung" right
under two simultaneous faults?* — is not answered by a case where the second fault is
**off the path**, because the descent never looks at it. The hard case is two faults *both
on the path*, where the lower one is real and fixing it will not restore the session, and
this round is not that.

**What it does establish**, if it holds, is narrower and still worth having: the specific
scenario an external reviewer identified as the adoption risk does not occur on this
fabric for this class of fault, **by construction rather than by luck** — and the
construction is nameable, which means it can be regression-tested.

---

## 4. Before the window

- **`Gi0/0/0/2` must be up and off-path at baseline.** If PE2's route to RR1 currently
  egresses `Gi0/0/0/2`, the round is void before it starts — check first, and if so pick
  the other unused port. `fault_lab.py --preflight` reads the port; **the off-path half
  it cannot tell you**, and the MCP re-test's incidental observation (rung 5 evaluating
  `Gi0/0/0/0` and `Gi0/0/0/1` only) is evidence for a *healthy* fabric and not a promise
  about this one. Confirm fresh.
- **Two faults, one revert path.** The restore must clear **both**, and be verified by
  reading the device back rather than by trusting the write (B-412). A lingering
  `shutdown` on a spare port is inert and undetectable, which is the combination worth
  checking for (§0.13, the procedure face). `SPARE_IF` is in `SNAPSHOT_SECTIONS` now, so
  the verification can see it — see §1.

- **Run with `--max-hold 45`.** The default is 20 minutes, and this window holds an
  `investigate` run (~110 s), an MCP Q1 exchange, and **a human reading a report carefully
  enough to notice whether it mentions a port it should not**. Twenty minutes is racing a
  watchdog, and a watchdog firing mid-read does not produce a wrong answer — it produces
  **no answer, from a spent window**.

  > The reading is the measurement here. §2.4's falsifier is *"the prose names the shut
  > port as a contributing cause anywhere, even hedged"*, and that is not something to
  > skim for under time pressure. **Budget for the reading, not for the run.**
- **Archive into the tracked repo.** `~/ai-agent-ops/faultlab/` is not a git repository
  (OBS-135). `evidence-archive/round6/`.

---

## 5. Results

*Empty until the run.*
