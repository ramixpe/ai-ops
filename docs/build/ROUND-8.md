# Round 8 — MD5 mismatch, and whether rungs 1 and 2 can separate at all

**B-463.** The last unseparated rung boundary: `bgp_session` | `transport`.

---

## 1. §6.1b — the fault, read against what this prediction assumes

`fault_lab.py` option 6, verbatim:

```
router bgp <AS>
 neighbor 10.255.0.31
  password clear FaultLabTrial
```
revert: `no password`.

**The APPLY block matches the operator's description exactly** — an MD5 password
on PE2's side only, RR1 unchanged. No mismatch of the kind that cost round 5 its
prediction.

**But it does not match what the round needs, and that is the finding to record
before anything is applied.**

### 1.1 MD5 cannot separate this boundary, for a structural reason

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
| AS mismatch | **cycles** | never Established | transient |

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

## 2. Propagation — hold-timer bound, and this is what makes the round awkward

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

**Stated before sealing, as asked: this round is harder than it looks.** Its
target vector is probably unreachable by this fault, and its timing is probably
hold-timer bound.

---

## 3. Prediction, sealed

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

**If B-463 is to close, it needs an AS mismatch, not MD5** — TCP establishes, the
OPEN is rejected, and the socket is armed while the FSM is not Established. That
is a different fault and it is not in `fault_lab.py`'s catalogue.

## 5. Results

*Empty until the run.*
