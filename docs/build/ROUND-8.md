# Round 8 — AS mismatch, and whether rungs 1 and 2 can separate at all

**B-463.** The last unseparated rung boundary: `bgp_session` | `transport`.

> **Re-sealed 2026-08-17. The fault changed from MD5 to an AS mismatch**, on the
> analysis in §1.1 below: MD5 provably cannot close this boundary, and running it
> would spend a lab window confirming reasoning that is already sound. The MD5
> prediction is **kept unaltered in §3** as a recorded unrun claim, and the round
> it belonged to is filed as **B-464**.

---

## 0. The stanza, read — and it ruled out the fault as first written

**Settled by `show running-config router bgp 65000` on PE2, before any window
was spent.** §6.1b's second catch in two rounds.

| Finding | Consequence |
|---|---|
| `remote-as` is **direct on the neighbour**, no `use neighbor-group` | Blast radius is one session. The change cannot propagate to other peers |
| **No `next-hop-self`** | One less iBGP-only sub-command to be rejected at commit |
| **`update-source Loopback0`** | **This is the problem.** The AS change makes it a loopback-sourced *eBGP* session, and eBGP defaults to **TTL 1** while PE2 reaches RR1 via P1 — **two hops** |
| **`bfd fast-detect` at 100 ms × 3** | A new risk, sealed in §2a.6 |

**Why `update-source` mattered more than the syntax question.** Either XR rejects
the commit, or it accepts and **TCP never establishes** — giving
`socket_armed_read: false` and `B B H H H`.

That vector is *what this round predicts* (§2a.1). It would have arrived **for
the wrong reason and been indistinguishable from a confirmation.** A round that
cannot tell its predicted outcome from an unrelated failure of its own setup is
not a test; it is a coin that lands the same way up either side.

**Fixed by two lines rather than one.** `fault_lab.py` option 7 now applies
`remote-as 65001` **and** `ebgp-multihop 5`, reverting with `remote-as 65000`
and `no ebgp-multihop`.

### 0.1 The revert's end state, named rather than assumed

Per §0.13's procedure face: *"revert was pushed"* is a step, not a state.

> **End state: `diff_keys(baseline, snapshot)` returns empty.** Specifically the
> `ebgp-multihop` line must be **absent** from the neighbour stanza afterwards,
> not merely un-pushed. `no ebgp-multihop` should remove it; the snapshot
> comparison is what proves it did.

A lingering `ebgp-multihop 5` on a restored iBGP session is inert — multihop is
meaningless for an internal peer — which is exactly why it could sit there
unnoticed. **Harmless and undetectable is the combination worth checking for.**

## 1. §6.1b — the fault, read against what this prediction assumes

`fault_lab.py` option 7, verbatim:

```
router bgp 65000
 neighbor 10.255.0.31
  remote-as 65001
  ebgp-multihop 5
```
revert: `remote-as 65000` + `no ebgp-multihop`.

**The APPLY block matches the fault this prediction assumes**: PE2 believes its
peer is in AS 65001; RR1 announces 65000. TCP establishes normally — nothing in
this fault touches the transport — and the OPEN is rejected on the AS check.

That is the property the round needs and the property MD5 lacks.

### 1.1 Why MD5 was dropped — the analysis that changed the fault

Separating rungs 1 and 2 requires **rung 1 broken and rung 2 healthy** — BGP not
Established while the TCP transport is up.

Since B-432, `bgp_transport` reads **`socket_armed_read`** from
`show bgp neighbor`, falling back to `connection_state` only when no socket line
exists. On IOS-XR the socket line is always present, so **the fallback will not
be taken and rung 2 is a genuine TCP-layer observation.** That much is what
B-432 intended.

And that is exactly why MD5 fails here. **TCP MD5 breaks TCP.** PE2 signs its
segments, RR1 has no key and discards them (RFC 2385), the connection dies, and
on every reconnect PE2's SYN is discarded before a session exists. The socket
never arms.

> **A fault that breaks the thing rung 2 observes cannot leave rung 2 healthy.**

So MD5 produces `B B H H H` — the same vector as round 3, from a different
mechanism. It does not close the boundary.

### 1.2 The wider claim, which this round can test

Generalising the above: for BGP, **rung 2's observable is a prerequisite for
rung 1's.** TCP up is necessary for Established. So any *stable* fault either
breaks both or breaks neither:

| Fault | TCP | BGP | Vector |
|---|---|---|---|
| admin shutdown (round 3) | down | Idle | `B B H H H` |
| MD5 mismatch (this round) | down | Idle | `B B H H H` |
| route-policy denying all | up | Established | `H H H H H` |
| **AS mismatch (this round)** | **up** | **never Established** | **`B H H H H`, transiently** |

**The only states with TCP up and BGP not Established are `OpenSent` and
`OpenConfirm`, which last seconds**, and the retry window of a peer whose OPEN
is rejected.

> **So the `bgp_session | transport` boundary may not be separable by any stable
> fault. If it is separable at all, the separation is transient.**

That is a substantive claim about B-432 and B-437 rather than a property of this
round, and it is testable here: **sample densely through the reconnect cycle and
look for a sample with `socket_armed_read: true` and the session not
Established.**

---

## 2. Propagation under MD5 — kept, because it is why the fault changed

Measured from the corpus: negotiated **hold 180 s, keepalive 60 s**.

Adding MD5 to an **already Established** session does not necessarily tear it
down. Two possibilities, and they demand different things of the operator:

**(a) IOS-XR resets the session on the password change.** Some releases do. The
reconnect cycle begins at once and the round runs unattended.

**(b) It does not reset.** PE2 starts signing, RR1 discards, keepalives go
unacked, and nothing visible happens **until the hold timer expires — up to
180 seconds.** The peer then drops with `last_reset_reason: BGP Notification
sent: hold time expired`, and only then does the reconnect cycle start.

**I do not know which, and I am not guessing.** Under (b) the round needs ~4
minutes of sampling before the interesting window opens, and if the operator
wants it sooner it needs a session bounce — `clear bgp neighbor`, which is a
**further device action** beyond the sealed fault and would have to be
authorised separately.

**Stated before the first seal, and it is what changed the fault.** MD5's target
vector is unreachable and its timing is probably hold-timer bound — up to three
minutes before anything is observable. The AS mismatch has neither problem: it
leaves the transport alone and it renegotiates at once (§2a.4).

---

## 2a. Prediction for the AS fault, sealed

### 2a.1 The steady-state rung vector

> **Expected: `B B H H H`** at steady state — and that is a prediction *against*
> the round's own purpose.
>
> An AS mismatch leaves the peer cycling: TCP connects, OPEN is rejected, TCP
> closes, back off, retry. **Between attempts the socket is not armed**, so a
> single `investigate` most likely samples a gap and reads rung 2 broken.

**Refuted if** the steady-state vector is `B H H H H`, which would mean the
socket stays armed across the back-off — the boundary separates stably and
B-463 closes outright.

### 2a.2 The transient separation — the claim this round exists for

> **At least one densely-sampled observation shows `socket_armed_read: true`
> with the session not Established.** That sample *is* the separation, captured
> rather than composed, and it closes B-463.

**Refuted if** no sample in the whole window shows that combination.

**Round 7's caveat applies and is the reason for dense sampling from the
commit:** zero occurrences is a plausible outcome of *sampling* as well as of
the mechanism. A TCP handshake plus OPEN plus rejection may complete inside one
~1 s sample. **A zero establishes that this instrument did not see it, not that
the boundary is unseparable** — and by the argument in §1.2 the window may be
genuinely sub-second.

**If a zero:** that is evidence for B-432's leading hypothesis — that
`bgp_session` has four rungs and rung 2 needs an observable not downstream of
rung 1's — rather than a failed round.

### 2a.3 What rung 2 reads

> `socket_armed_read`, **not** the `connection_state` fallback.
>
> **Refuted if** rung 2's reason contains *"no socket state reported"*. That
> would mean rung 2 read the FSM — the same state machine as rung 1 — and
> **B-432 did not ship what it thinks it shipped.**

Unchanged from the MD5 seal, and still the more valuable of the two claims:
§2a.1 tests the fabric, this tests our own code.

### 2a.4 Timing

> **Immediate, not hold-timer bound.** A `remote-as` change is a session-level
> reconfiguration and IOS-XR tears the session down to re-negotiate; the first
> retry follows within seconds.
>
> **Refuted if** nothing changes for more than 30 s after the commit.

This is the one clear improvement over MD5, whose timing was unknown and
possibly 180 s.

**Superseded in part by §2a.6.** This claim was written against the corpus's
180 s hold timer. With `bfd fast-detect` at 300 ms the teardown is sub-second
and the 30 s falsifier is far looser than it needs to be — it will not fire, and
its not firing is therefore weak evidence. **Kept as sealed rather than
rewritten**, with the weakness stated: a falsifier loosened by a fact discovered
after sealing is a falsifier that has stopped discriminating.

### 2a.5 The expected `last_reset_reason`

> Something naming the AS — IOS-XR typically reports a notification of
> `BGP Notification received/sent: bad peer AS` or similar.
>
> **Not a scored claim**, because I do not know this platform's exact wording
> and inventing one to score against would be a falsifier written from a guess.
> Recorded as an observation to capture.

---

### 2a.6 BFD under eBGP multihop — the fifth claim, and it is a real unknown

`bfd fast-detect` is configured on this neighbour at **100 ms × 3 = 300 ms
detection**. Under eBGP multihop, a single-hop BFD session will not come up —
IOS-XR needs `bfd multihop` configured for that, and it is not.

**I cannot tell from configuration whether XR lets BGP establish while BFD
fails, or holds the session down.** Both are defensible implementations and I am
not guessing which this release does.

> **Sealed claim: BGP's failure to establish is attributable to the AS check,
> not to BFD.**
>
> **Refuted if** `last_reset_reason` or the log names BFD, or if `show bfd
> session` shows the session down while BGP never leaves Idle — i.e. BGP is
> being held down by BFD rather than rejected on the OPEN.

**And it confounds the timing claim.** §2a.4 predicted an immediate
renegotiation against a 180 s hold timer. **A neighbour with 300 ms BFD
detection does not have the corpus's hold-timer semantics at all** — teardown
and retry are sub-second, so the retry cycle is far faster than §2a.4 assumed.
That makes the transient window *more* frequent (good for §2a.2) and the
individual window *shorter* (bad for it). Net effect unknown; recorded rather
than predicted.

### 2a.7 Three mechanisms, one vector — and the discriminator

This is the round's central methodological risk and it must be stated before it
is run.

**Three independent failures now produce `B B H H H`:**

1. **the intended one** — TCP establishes, OPEN rejected on the AS check;
2. **TTL/multihop** — TCP never establishes (mitigated by `ebgp-multihop 5`,
   not eliminated);
3. **BFD** — the session is held down by a BFD session that cannot come up.

**The rung vector cannot distinguish them.** All three give the same five
letters, and a round scored on the vector alone would confirm its prediction in
all three cases — including the two where the prediction's *mechanism* never
occurred.

> **So the vector is not the result. The discriminators are:**
>
> - **the transient socket sample** (§2a.2) — armed means TCP established, which
>   rules out (2) outright;
> - **`last_reset_reason`** — an AS-related notification rules in (1); a BFD
>   mention rules in (3);
> - **`show bfd session`** on PE2, if (3) is suspected.

That makes §2a.2 **load-bearing rather than a bonus**: it is simultaneously the
claim the round exists to test *and* the only cheap evidence that the round
tested what it meant to. Dense sampling from the commit is therefore not an
optimisation.

**Favourably, the operator's point stands:** the OpenSent window recurs on
**every connect retry**, so the round gets repeated chances rather than one — and
with BFD-speed retries, many of them.

---

## 3. The MD5 prediction — RECORDED, UNRUN

Kept verbatim from the first seal. It was sound analysis and it stands as a
claim, but the fault it describes is now **B-464** and this round does not
apply it. Nothing below was measured.

### 3.1 The rung vector

> **Expected: `B B H H H`** — `bgp_session` broken, `transport` broken,
> `route_to_peer`, `igp_adjacency`, `interface` healthy. Finding
> `transport_blocked`, exit 1.
>
> **Rungs 1 and 2 do NOT separate.** The boundary stays open and B-463 is not
> closed by this round.

**Refuted if the vector is anything other than `B B H H H`** at steady state.
Specifically:

- `B H H H H` → **the boundary separates**, §1.1's reasoning is wrong, and
  B-463 closes. The best outcome and the one I am predicting against.
- `H H H H H` → the fault did not take effect, or MD5 is not enforced on an
  established session and never triggered a reconnect.
- `B B B H H` or deeper → the fault reached below the transport layer, which it
  has no mechanism to do.

### 3.2 What rung 2 will actually read

> `socket_armed_read: false`, and **the `connection_state` fallback will not be
> taken**, because IOS-XR emits the socket line on every `show bgp neighbor`.
>
> **Refuted if** rung 2's reason contains *"no socket state reported"*, which is
> the fallback's wording. That would mean rung 2 read the FSM — the same state
> machine as rung 1 — and B-432's separation is not real on this platform.

This is the more valuable of the two claims. §3.1 tests the fabric; **this tests
whether B-432 shipped what it thinks it shipped.**

### 3.3 The transient separation

> **Prediction: at least one densely-sampled observation shows
> `socket_armed_read: true` with the session not Established**, during a
> reconnect attempt.
>
> **Refuted if** no sample in the whole window shows that combination.

Honest caveat, and it is the §4.2-shaped one from round 7: **zero occurrences is
a plausible outcome of sampling as well as of the mechanism.** The window is a
TCP handshake against a peer that discards the SYN — it may be shorter than the
~1 s sample resolution, or may never arm the socket at all if the stack arms it
only on a completed connection. **A zero here does not establish that the
boundary is unseparable**; it establishes that this instrument did not see it.

### 3.4 Timing

> **Prediction: hold-timer bound, not immediate.** Nothing changes for up to
> 180 s, then the session drops with `hold time expired`.
>
> **Refuted if** the session drops within 10 s of the commit, which would mean
> IOS-XR resets on password change.

---

## 4. What this round is actually for, restated

Given §1.1, the round's value is **not** closing B-463. It is three other things:

1. **Testing whether B-432's separation is real** (§3.2) — a rung given a
   distinct subsystem fifteen commits before anyone checked the fabric could
   exhibit the distinction (OBS-121).
2. **Testing whether the boundary is separable at all** (§3.3) — which, if the
   answer is no, changes B-437's criterion rather than failing it.
3. A captured `transport_blocked` from a **second, independent mechanism**,
   which is worth having beside round 3's admin shutdown.

**The AS mismatch is now option 7 in `fault_lab.py`**, added by the operator
after this analysis. Whether it closes B-463 depends on whether the separation
is observable at ~1 s resolution, which §2a.2 predicts and §1.2 doubts.

## 5. Results

Scored 2026-08-17 against `evidence-archive/round8/`. Two runs: `20260817-160359`
(dry, 181 samples) and `20260817-161045` (real, 182 samples).

**The fault landed.** Applied 16:10:56.860, first FSM movement 16:10:59.971, and
`peer in wrong AS` named in 156 samples. **The instrument that was to measure the
round's primary claim did not work**, and its own baseline says so.

### 5.1 The instrument, first — because it bounds everything below

`round8.py` carries two defects. Both are the same idiom: **a first-match search
over a string containing more than one candidate token.**

| | |
|---|---|
| `_SOCKET = re.compile(r"socket.*?(armed\|not armed)", re.I)` | The real line is `Socket not armed for io, armed for read, armed for write`. A minimal-width search takes the **io** field, which is `not armed` on a healthy session. `socket_armed` is therefore `False` in every sample ever taken |
| `as_named = any(x in resets for x in ("bad peer as", "notification", ...))` | `_NOTIF` matches `notification` before `hold time expired` in *"due to BGP Notification sent: hold time expired"*, so an ordinary hold-timer reset is classified as naming the AS |

**The baseline refutes the first defect outright, and the dry run refutes the
second.** Across both runs there are **195 samples in which the session is
`Established`** — four pre-fault, eleven post-restore, 181 in the dry run where
the fault never applied. **`socket_armed` is `False` in all 195.** A field that
does not read true on a fully established BGP session is not measuring the
socket. And the dry run reports `discriminator_reset_names_as: true` while
containing **zero** samples naming an AS: all 181 of its resets are
`hold time expired`.

> **A falsifier is only meaningful if the instrument can produce the value that
> would fail it.**
>
> §2a.2's falsifier — *"refuted if no sample shows that combination"* — fired.
> It fired on an instrument that could not have produced the combination in any
> state of the world. **That is a void trial, not a refutation**, and the
> distinction is invisible in `SEPARATION_OBSERVED: false`.

**And the check was already in the payload.** `samples_socket_armed: 0` counts
over the post-fault window; had it counted over the baseline it would have read
`0 of 4` on a healthy session and stopped the round at its own first verdict
line. Round 7's positive control was a design decision; round 8 had one by
accident and did not read it.

**One defect did not spread, and the reason matters.** `socket_reported` is
`bool(match)` — whether the line existed, not what it said — and it is
robust to matching the wrong part of the line. So §2a.3 is scoreable from the
same broken regex that voids §2a.2.

### 5.2 Scored

| Claim | Verdict | Evidence |
|---|---|---|
| **§2a.3** rung 2 reads the socket, not the `connection_state` fallback | **CONFIRMED** | `samples_socket_not_reported: 0` across all 363 samples; `socket_reported: true` everywhere. IOS-XR emits the line on this platform and release. **B-432 shipped what it thought it shipped** — the falsifier was *"rung 2's reason contains 'no socket state reported'"*, and that string never occurred |
| **§2a.4** timing is immediate, not hold-timer bound | **CONFIRMED, weakly** | 3.11 s from commit to first FSM movement, against a 30 s falsifier. §2a.4 predicted its own weakness: the falsifier was loosened by BFD's 300 ms detection discovered after sealing, so its not firing is weak evidence. It is recorded as sealed, and read as sealed |
| **§2a.6** BFD did not hold the session down | **CONFIRMED, both disjuncts** | `bfd_states_seen: ['No']`; no reset names BFD; and BGP **left Idle** — `Connect` in nine retry cycles and `OpenSent` once. The falsifier required BFD named *or* BGP never leaving Idle. Neither |
| **§2a.7** the three mechanisms are distinguishable | **CONFIRMED as a design, refuted as an implementation** | See §5.3 |
| **§2a.5** the exact `last_reset_reason` wording | **CAPTURED** (unscored by seal) | `BGP Notification sent: peer in wrong AS` — 156 samples. The immediate config event is separately visible as `due to Remote AS configuration changed`, 14 samples |
| **§2a.1** the steady-state rung vector | **NOT MEASURED** | The round sampled fields directly and never ran `investigate`, so no rung vector was produced. Neither confirmed nor refuted |
| **§2a.2** the transient separation | **OPEN — unresolvable from this run** | §5.4 |

### 5.3 The three mechanisms were separated — by the raw text, not the discriminator

§2a.7 named three failures that produce the same rung vector and said the
discriminators, not the vector, are the result. That was right. Two of the three
discriminators were then implemented wrongly, and the mechanisms were separated
anyway:

- **TTL/multihop — ruled out.** `OpenSent` requires a completed TCP connection
  and a sent OPEN. `peer in wrong AS` requires the *peer's* OPEN to have been
  received and evaluated. Either alone rules it out; both occurred.
- **BFD — ruled out.** §2a.6.
- **AS rejection — ruled in.** Named explicitly, 156 samples.

What did the ruling out is the **raw `last_reset` string**, stored verbatim in
every sample. What failed is `discriminator_reset_names_as`, a boolean derived
from it.

> **The round demonstrates the amendment it produced, inside its own payload.**
> Two fields from the same command in the same file: one stored as text and one
> stored as a conclusion. The parser was wrong about both. **The text survived
> the defect and the boolean did not**, and the recovery of §2a.5, §2a.6 and
> half of §2a.7 rests entirely on which of the two a field happened to be.
>
> This was not foresight. `last_reset` was stored raw because a string is
> awkward to reduce, and `socket_armed` was reduced because a boolean is tidy.
> The tidier choice is the one that lost the round.

See `chaos-harness.md` §6.1d and `BUILD-PLAN.md` §0.13's procedure face.

### 5.4 §2a.2 — what is actually known about the separation

**The fabric produced the separating condition. The round did not measure the
observable.** Those are two claims and only the first is settled.

`OpenSent` was observed once, at 16:14:59.191, with the summary state still
`Idle`. In `OpenSent` the TCP connection is established and BGP has sent its
OPEN and is not Established — which is, by the definition of the state, rung 2's
condition healthy while rung 3's is broken. **The separation occurred.**

Whether `bgp_transport` *reports* it is what §2a.2 asks, and that depends on the
socket flags, which were never validly read. Rung 2 would almost certainly have
read healthy there — a socket in `OpenSent` is armed — but that is an inference
from the protocol's definition, not a measurement of the rung. **This build does
not score inferences as measurements**, so the claim stays open and B-463 stays
open with it.

**The round did bound the difficulty, which is the useful part.** `OpenSent`
appears in **1 sample of 182**, across roughly **ten connect-retry cycles**, at
**~1.56 s** resolution. One catch in ten cycles puts the window at order **150 ms**
— consistent with §2a.6's note that BFD-speed retries make the window more
frequent and shorter, and with §1.2's doubt that it is visible at ~1 s at all.

> **Design input for round 8b: sub-200 ms sampling of the socket field, or the
> round produces another uninterpretable zero.** Sampling at 1.5 s and reporting
> `0` measures the sampler.

This is evidence for **B-432's leading hypothesis** — that `bgp_session` needs a
rung-2 observable not downstream of rung 1's — exactly as §2a.2 anticipated a
zero would be. It is not yet evidence *for* four rungs rather than five, because
the observable that would decide it has not been read.

### 5.5 Round 7's control, repeated and confirmed

The dry run saw `fsm_states_seen: ['Established']` only and 181 samples with the
original hold-timer reset: **the fault never applied**. That is the
`fault_lab._dry_run` fix working, and the second consecutive round in which the
dry run is a usable negative control rather than a wasted window.

### 5.6 What was not archived

**Round 8's payload was not archived when it was written.** Both run directories
were in `~/ai-agent-ops/faultlab/`, which is not a git repository — not
untracked, no repository at all. They survived only because nothing had deleted
them; round 7 lost 158 samples to the same state.

They are committed now (`evidence-archive/round8/`), together with `round8.py`,
because the round's numbers are wrong on account of two regexes in that file and
a payload recording what an instrument concluded, without the instrument, cannot
be re-read against a defect in it.

**This is the OBS-131 procedure face a third time**, and the honest reading is
that §6.1d's end-state check was not applied to this round at all — no one ran
`git ls-files` after it.

---

## 6. Round 8b — re-seal

Sealed 2026-08-17, after §5. **§2a's predictions are carried forward unchanged
and are not restated here** — this section states only what differs. The sealed
text above is not edited; a re-seal that rewrites the prediction it is re-testing
is not a seal.

### 6.1 The fault is unchanged

Option 7 in `fault_lab.py`, byte-identical: `remote-as 65001` plus
`ebgp-multihop 5` on PE2's neighbour `10.255.0.31`, two lines, reverted to a
verified byte-identical config. It landed correctly and there is no reason to
change it. Changing the fault would make 8b a different round rather than a
re-run.

### 6.2 What must be true before it runs

Three preconditions. **The round does not start until all three hold**, because
each one is a way the previous run became unreadable.

1. **The socket regex is anchored and positional.** The shipped
   `template_parsers._BGP_SOCKET` is already correct and is the one to use:

   ```
   ^Socket (?P<io>not armed|armed) for io, (?P<read>not armed|armed) for read, (?P<write>not armed|armed) for write$
   ```

   with a unit test asserting `Socket not armed for io, armed for read, armed
   for write` yields `read=armed`. A first-match search over this line is the
   defect that voided §2a.2 and it must be impossible to reintroduce.

2. **Every sample stores the raw `Socket …` line beside `socket_armed`, and the
   raw `last reset` line beside `reset_names`.** §6.1d as amended. The recovery
   of §2a.5–§2a.7 from run 1 rests entirely on `last_reset` having been stored
   raw; the loss of §2a.2 rests entirely on `socket_armed` not having been.

3. **The verdict reports the baseline separately from the post-fault window.**
   `samples_socket_armed` must be printed as *`n` of `m` baseline* and *`n` of
   `m` post-fault*. A baseline reading `0 of 4 armed on an Established session`
   is an instrument failure and stops the round at its first verdict line. This
   is the check that was available in run 1 and not made.

### 6.3 What changes in the sampling

**Sub-200 ms sampling of the socket field during the connect-retry cycle.**
§5.4 bounds the `OpenSent` window at order 150 ms. At 1.56 s the round has
already been run and produced a zero that measures the sampler.

This need not mean a full 200 ms `investigate` — it means the *socket field*
specifically. A tight loop on `show bgp neighbor <peer>` for the duration of one
retry cycle is enough, and the retry cycles are ~23 s apart with roughly ten of
them in a 240 s window.

> **Sealed: if sampling across enough retry cycles to expect ~11 catches still
> yields zero armed-while-not-Established samples, §2a.2 is refuted rather than
> void, and B-463 closes as "not separable at any resolution reachable over
> CLI".**

That is the outcome §2a.2 could not reach in run 1, and it is the point of 8b:
**to convert a void trial into a result in either direction.**

#### Amended before the run — sub-200 ms is not reachable, and it was never the requirement

§6.3 originally asked for *"sub-200 ms sampling of the socket field"*. **That is
not achievable over SSH and saying so now is what §6.1b is for** — a setup error
found before the window is spent costs nothing, and round 5 lost a correct answer
to one found afterwards.

Round 8 measured **1.563 s for three `show` commands**, so a single command costs
about **520 ms** of round trip. No loop tuning beats the wire, and dropping to one
command is the whole of the available gain.

But sub-200 ms was a *proxy*, not the requirement. What §2a.2 needs is a good
chance of landing inside the `OpenSent` window at least once, and that is a
function of **cycles**, not resolution:

> expected catches ≈ N_cycles × min(1, W_window ÷ T_sample)

| | Round 8 | Round 8b |
|---|---|---|
| Commands per sample | 3 | **1** |
| T_sample | 1.563 s | ~0.52 s |
| Dense window | 240 s | **900 s** |
| Retry cycles (~23 s) | ~10 | **~39** |
| **Expected catches** | **0.96** | **~11** |

**Round 8 observed exactly one.** The model is calibrated against the only data
that exists, which is a weak validation but not no validation.

At ~11 expected, **P(zero) ≈ 2 × 10⁻⁵**. That is what converts a zero from *"the
instrument did not see it"* into *"it is not there"* — and it is the only reason
to spend the window at all.

**The prediction (§2a.2) is untouched.** Only the method changed, and it changed
because arithmetic showed the sealed method could not deliver the prediction's
own falsifier. A method amended to make a claim *more* falsifiable, before the
run, stated in advance, is the amendment §6.1b exists to permit.

### 6.4 What is already settled and is not re-tested

§2a.3, §2a.4, §2a.5 and §2a.6 are scored in §5 and do not need the window.
8b tests §2a.2 and, through it, §2a.1 — nothing else. If the operator has one
lab window, this is the only claim it needs to buy.

### 6.5 Precondition 1, checked before the window — it did not hold

Checked 2026-08-18, immediately before opening round 8b's window. §6.2 lists
three preconditions and says *"the round does not start until all three hold"*.
**Preconditions 2 and 3 held in `round8b.py`** (`socket_line_raw` is stored per
sample; `baseline_socket_armed_read` and `baseline_trustworthy` are computed
and abort the round at its first verdict line).

**Precondition 1 did not.** It asks for *"a unit test asserting `Socket not
armed for io, armed for read, armed for write` yields `read=armed`"*. No such
test existed. `tests/test_checks.py` passes `socket_armed_read` in as a
**constructed** value to `_neighbor_meta(...)` — it never runs the parser
against the line — and its corpus-wide assertion cannot close the gap either,
because **read == write in all 16 committed fixtures** (14 armed/armed, 2 not
armed/not armed). A parser that swapped the two fields would pass the entire
suite. That is §0.12's shape exactly: a corpus uniform in the dimension the
test discriminates on.

The shipped regex was correct all along — anchored and positional, and §5
already established that by reading it. **The gap was that "the shipped one is
correct" rested on reading rather than on running**, which is the distinction
this whole build keeps re-learning.

Three tests added to `tests/test_template_parsers.py`, driving the real
`parse_xr_bgp_neighbor` path rather than the regex in isolation: the healthy
mixed line yields `read=armed`; read and write are shown non-interchangeable
against synthetic input the corpus cannot supply; the all-unarmed Idle shape
still reads as unarmed.

**Mutation-verified, with the actual round-8 defect as the mutant.** Replacing
`match["read"]` with `match["io"]` — which is what a first-match search
effectively did — fails 2 of the 3. Added to `scripts/mutate_guards.py` as
`ROUND-8-SOCKET`; **21/21 guards hold.**

**Precondition 1 now holds. The round may start.** Cost: about fifteen minutes,
before the window rather than inside it — which is what §6.1b is for, and what
round 5 paid for the other way.

---

## 7. Round 8b — scored. §2a.2 HOLDS, and the way it holds is a defect

Run 2026-08-18T11:54Z, agent-operated (the operator lifted the injector rule
that morning; §0.11 as amended). Payload committed at
`evidence-archive/round8b/20260818-115402/`.

### 7.1 The numbers

| | value |
|---|---|
| baseline samples / armed for read | **4 / 4** — instrument trustworthy |
| `fault_landed` | **true** |
| post-fault samples | **1,725** |
| mean sample interval | **0.543 s** (predicted ~0.52) |
| **separation samples** | **131** |
| `samples_socket_not_reported` | **0** of 1,725 |
| FSM states seen | Closing, Connect, Established, Idle, OpenSent |
| reset kinds seen | config_changed, hold_expired, **wrong_as** |
| restore | verified attempt 1, then re-verified by direct read |

**§2a.2 HOLDS.** The socket read armed for read while the session was not
Established, 131 times, on a baseline-verified instrument. §2a.1 follows: rungs
1 and 2 are separate observables, captured rather than composed.

**B-463 closes** — and in the opposite direction from the sealed fallback,
which contemplated *"not separable at any resolution reachable over CLI"*. It is
separable, abundantly, at 0.54 s.

**§2a.3 confirmed a second time**: 0 of 1,725 samples missing the socket line.
**`discriminator_reset_names_as` is true and real this time** — the fault is a
wrong AS and the device says so — where round 8's dry run reported it from the
`_NOTIF` regex defect (OBS-134).

### 7.2 The prediction was right about the wrong state

The sealed model expected ~11 catches, from `OpenSent` at ~150 ms per cycle.
Observed:

| FSM state during separation | samples |
|---|---|
| `Connect` | **127** |
| `OpenSent` | **4** |

**`OpenSent` delivered 4 against a predicted ~11** — the right order, a sound
model of the mechanism it described. **`Connect` delivered 127, and the model
never considered it.** The effect is an order of magnitude larger than sealed,
because most of it lives in a state the seal did not name.

A prediction that survives while its stated mechanism accounts for 3% of the
observations has been confirmed and not understood, and the difference matters
here — because of what `Connect` means.

### 7.3 The finding the round was not looking for: rung 2 emits a false healthy

`checks.py`'s transport rung is unconditional on the FSM:

```python
armed = meta.get("socket_armed_read")
if armed:
    return healthy(reason=f"TCP transport to {peer} is up (the socket is armed for read)")
```

In **`Connect`**, RFC 4271's definition is *waiting for the TCP connection to be
completed* — **TCP is not established.** The socket line in those 127 samples is
byte-identical to the healthy baseline:
`Socket not armed for io, armed for read, armed for write`.

So during connect-retry the descent reports **"TCP transport to 10.255.0.31 is
up"** while TCP is demonstrably not up. `socket_armed_read` does not mean *the
transport is established*; it means *the BGP stack has a socket armed for read
events*, which it does while a connection is still being attempted.

**The `OpenSent` samples are the honest case.** There TCP *is* established (the
OPEN was sent over it), so armed-plus-not-Established is a true separation and
rung 2 is right. Four of 131 separations were the thing the rung believes it is
measuring.

**B-432's own comment predicted exactly this and could not test it:**

> *"That the two agree on this corpus is not evidence they are the same field —
> the corpus contains no fault that separates them, which is precisely the gap
> this change opens."*

Round 8b is that fault. The gap opened, and what came through it is that the
field separates from the session state **for two different reasons**, one of
which the rung reads backwards.

**Why it matters operationally.** A genuine transport fault — a filtered TCP
179, an unreachable peer — cycles the session Idle → Connect → Idle. Sampled
during `Connect`, rung 2 clears transport as healthy and the descent blames the
rung above it. **A transport fault would be reported as a BGP-layer fault**,
which is the one failure a dependency descent exists to prevent. Filed as
**B-497**.

Note what this does *not* invalidate: the fixture-replay demo and every scored
round used sessions that were **Idle** (socket not armed) or **Established**
(socket armed, session up). Neither is the ambiguous case. The defect is
reachable only while a session is actively retrying, which is exactly when
someone is looking at it.

### 7.4 What this round cost, and what watching it bought

Two defects were found before the window and two inside it, none by the round's
own prediction:

1. **Precondition 1 was verified on the wrong artefact** — three mutation-tested
   tests written against the *shipped* parser, while the round runs its own copy
   (OBS-160). The round aborted in 40 s.
2. **The sampler could not parse an indented socket line** — pattern copied
   verbatim, the stripping that makes it work not copied.
3. **The verdict could refute §2a.2 from a run where nothing broke** — the dry
   run, fault never applied, printed *"§2a.2 refuted, B-463 closes"*. Fixed with
   a `fault_landed` guard before the real run.
4. **Rung 2's false healthy**, above — visible only by reading the separations
   by FSM state rather than counting them.

Three of the four are instrument defects, and every one of them would have
produced a confident, wrong, publishable number. **The round's own prediction
was the least informative thing it produced.**
