# Round 8 — AS mismatch, and whether rungs 1 and 2 can separate at all

**B-463.** The last unseparated rung boundary: `bgp_session` | `transport`.

> **Re-sealed 2026-08-17. The fault changed from MD5 to an AS mismatch**, on the
> analysis in §1.1 below: MD5 provably cannot close this boundary, and running it
> would spend a lab window confirming reasoning that is already sound. The MD5
> prediction is **kept unaltered in §3** as a recorded unrun claim, and the round
> it belonged to is filed as **B-464**.

---

## 0. Abort condition — settle this before applying anything

**The corpus cannot tell me whether IOS-XR accepts a `remote-as` change on an
existing neighbour in place.** `fault_lab.py`'s snapshot does run
`show running-config router bgp 65000`, but no stored capture retains the
neighbour stanza — the run records hold the *apply* lines of past faults, not the
config they were applied to. I checked; it is not there.

**And the syntactic question hides a semantic one that matters more.** PE2's
neighbour toward RR1 is **iBGP** (both in 65000, RR1 a route reflector).
Changing `remote-as` to 65001 converts it to **eBGP**, and IOS-XR rejects that
at commit if the stanza carries iBGP-only configuration — `next-hop-self`,
`update-source` combinations, or anything inherited from a neighbour-group that
fixes the AS.

> **One read-only command settles both:** `show running-config router bgp 65000`
> on PE2. If the neighbour stanza is bare — `remote-as`, `update-source`,
> `address-family` — the change will take. If it carries iBGP-specific
> sub-commands, the fault needs a different shape and it is better known now.

**Abort condition:** if the commit is rejected, the round does not run, the
rejection is recorded as the result, and B-463 stays open pending a fault that
can produce the vector. **A rejected commit is a finding, not a failed round.**

---

## 1. §6.1b — the fault, read against what this prediction assumes

`fault_lab.py` option 7, verbatim:

```
router bgp 65000
 neighbor 10.255.0.31
  remote-as 65001
```
revert: `remote-as 65000`.

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

### 2a.5 The expected `last_reset_reason`

> Something naming the AS — IOS-XR typically reports a notification of
> `BGP Notification received/sent: bad peer AS` or similar.
>
> **Not a scored claim**, because I do not know this platform's exact wording
> and inventing one to score against would be a falsifier written from a guess.
> Recorded as an observation to capture.

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

*Empty until the run.*
