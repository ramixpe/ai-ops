# Fault Injection and the Chaos Harness

How the agent's diagnostic accuracy is measured rather than believed.

Companion to `design-thinking.md` (D6, the dependency descent), `BUILD-PLAN.md` §0.13 (the evidence-bounds-conclusion family), and `interfaces.md`. Backlog items **B-426** (the harness) and **B-427** (the evaluation corpus).

---

## 1. Why this exists

Most agent projects evaluate by looking at outputs and forming an impression. That produces a feeling about quality and no number.

This project can do better, because it has something rare: **a lab where the ground truth of a fault is known by construction.** If a change is applied deliberately, the correct diagnosis is not a matter of opinion. The agent's answer can be scored.

That turns evaluation from a judgement into a measurement, and it makes three things possible that are otherwise unavailable:

| | Without injection | With injection |
|---|---|---|
| Accuracy | "it seems to work" | rung-level confusion matrix over *n* trials |
| Regression | notice when something breaks | re-run the corpus after every change |
| Coverage | "we tested BGP" | an enumerated fault set with per-class results |

### The target is characterised, not error-free

"Almost error free" is the wrong goal and aiming at it will mislead the work. The system will not be error-free — the first live trial produced a fabricated device timestamp that passed grounding.

The right goal is **characterised**:

> We know its failure modes, their rates, and their shapes.

An operations team will extend more trust to *"it identifies the correct rung 94% of the time, fails toward `undetermined` rather than toward a wrong answer, and has never been wrong about which device"* than to any claim of near-perfection. The second sentence is what matters: **the direction of failure is more important than its rate.** A system that says "I cannot determine this" when uncertain is usable at a 70% hit rate. A system that guesses confidently is not usable at 95%.

The harness measures both.

---

## 2. The first result, and what it established

One manual trial, 2026-08-16, PE3, IS-IS shutdown on both core interfaces.

| | Hand diagnosis | Agent |
|---|---|---|
| Rung | `igp_adjacency` | `igp_adjacency` |
| Device | PE3 | PE3 |
| Finding | `igp_isolated` | `igp_isolated` |
| Rung 5 | healthy, subinterface excluded | healthy, 3 of 3 members |
| Chain | rungs 1 and 3 named | `bgp_session → transport → route_to_peer` |

Hand diagnosis committed to git at 14:11:50 UTC; the agent ran at 14:12:02. The ordering is verifiable in history rather than asserted — **a protocol requirement, not a courtesy.**

Three things this established, and they define what the harness must preserve.

**The case was discriminating.** From RR1 the symptom was indistinguishable from the previously captured `broken` label — BGP Idle, no route to the loopback — where the cause was a *different rung*. Anything pattern-matching "peer unreachable means the far end's interfaces are down" answers wrongly here, because they were up. **A trial that cannot be failed proves nothing.**

**The separation held under a real model error.** The model corrupted a device timestamp in the correlation narrative — `Aug 14 04:28.238` for a record reading `Aug 16 14:04:28.238` — and grounding passed it. The diagnosis was unaffected, because the diagnosis was never the model's to make. That is the architecture's central claim, and this is the first evidence for it rather than an argument.

**Prompt constraints are priors, not gates.** The prompt said *quote exactly*. The model did not. Only code enforces.

---

## 3. Non-negotiable constraints

Five. Violating any of them makes the results either unsafe or meaningless.

### 3.1 The injector is never the agent

The process that applies faults must share no context with the process that diagnoses them.

**There are two reasons, and they are rules of different kinds. Filing them together is a mistake — this section previously did, and the error is worth naming, because collapsing them is how the weaker one gets waived along with the stronger.**

#### Reason one: it is a device write

An injector configures devices, so `BUILD-PLAN.md` §0.11 applies and it is an absolute HALT. Granting the diagnostician config-write capability for testing convenience is precisely the erosion the pre-commitment exists to resist, and it would falsify the claim that `ios-xr-nettools` cannot configure a device.

#### Reason two: it destroys the blinding — and this is *not* a §0.11 rule

**§0.11 rules get waived, correctly, on grounds of reversibility and supervision.** They have been waived twice in this build on exactly those grounds, and both waivers were the right call. **The same reasoning applied here would waive this one** — a drill is reversible by construction, a supervisor is mandatory (§3.2), and the fault is going to be restored anyway. Every argument that justified the capture windows applies, and every one of them is beside the point.

> **Some actions are forbidden not because they are unsafe but because taking them makes you unable to judge the result. Where you are both the actor and the assessor, refraining is part of the method, not caution about it.**

An agent that invokes the injector has the fault identity in its context — in stdout, in the log path, in the fact that it chose. Instructing it not to look is prompt-level enforcement of a property the architecture refuses to enforce that way everywhere else.

#### The visibility asymmetry, which is why this must not be filed under §0.11

| | §0.11 violation | Blinding violation |
|---|---|---|
| What is damaged | the fabric | the evidence |
| How it announces itself | **loudly** — a device is in the wrong state, and something breaks | **not at all** |
| Detectable afterwards | yes, by reading the device | **no** |

A contaminated trial produces **the same confident agreement a clean one does**, and afterwards nobody can tell which they had. There is no device to read, no diff to take, no log line. The only defence is the pre-commitment, which is why it is a rule of method rather than a rule of safety — and why a reversibility argument, however sound, is not an argument about this at all.

Q-006 is worth something only because the hand diagnosis (OBS-076) was committed at 14:11:50 UTC, before the agent ran at 14:12:02. Had the ordering been reversed, the entry would read identically.

**The injector runs as a separate process, operated by a human or a scheduler. It lives outside the repository.**

### 3.2 An independent supervisor

The injector cannot be trusted to clean up after itself. Evidence, from two hours of development:

- a restore reported failure three times while having succeeded on the first attempt;
- a config comparison that could never match, because it included a per-read timestamp;
- a `TypeError` in the error path rendered as a network failure.

Over an unattended 24-hour run, a stuck fault approaches certainty.

The supervisor is a **separate process** that periodically diffs the entire fabric against a golden snapshot and force-restores anything that has drifted. It uses `save_golden_snapshot` and `diff_evidence`, which already exist.

Its authority exceeds the injector's: **the supervisor may restore at any time, including mid-trial.** A trial lost to an over-eager supervisor is cheap; a fabric left broken overnight is not.

### 3.3 One fault at a time, with ceilings

- maximum concurrent faults: 1 (until §7's combination work begins, which raises it to 2 under explicit configuration);
- maximum hold per fault, enforced by watchdog;
- maximum faults per hour;
- a kill switch that restores everything and halts the session;
- a blackout calendar — no injection during windows the operator marks as protected.

### 3.4 The agent must not know a drill is running

No marker in the environment, no distinct credentials, no telltale in the trigger. An agent that can detect a drill can bias toward "probably injected", and every subsequent number is contaminated.

This is harder than it sounds, and needs auditing: injection commits appear in the device's commit history with an identifiable user, and log records name the injecting host. **If the agent's evidence contains a signature of the injector, the trial is invalid.**

Two mitigations: use the same credentials and source host as routine operations, and — more robustly — measure it. Run a control arm where nothing is injected and confirm the agent's behaviour is statistically indistinguishable.

### 3.5 A holdout set, never used for tuning

The one most often skipped, and the most expensive to skip.

If failures observed during a soak are fixed, and the fixes are validated against the same fault catalogue, the result is an agent excellent at those faults and no better at anything else. That is §0.13's family in its most costly form: *your evidence bounds your conclusion, and the bound is invisible from inside.*

**Split the catalogue:**

| Set | Use | Rule |
|---|---|---|
| **A — development** | injected during soaks; failures analysed and fixed | may be looked at freely |
| **B — holdout** | validation only | never injected during development, never inspected when writing a fix |

A fix that improves A and not B has fixed nothing generalisable. **Report both numbers, always. A paper that reports only the development set is reporting its own training data.**

Set B should be built by someone who is not writing the fixes, and should include fault classes absent from A entirely — not merely different parameters of the same classes.

---

## 4. The fault catalogue

### 4.1 Design rules

**Every fault names its expected rung, and that expectation is sealed.** It exists to score the result, never to inform a run. It must not appear in any file the agent or a human diagnostician reads before answering.

**Every fault is reversible by one command**, and the reversal is verified by config comparison rather than by the write's own report.

**Every fault has a bounded blast radius**, stated explicitly: which adjacency, which session, which service.

**Faults are described factually, never predictively.** "IS-IS shutdown on both core interfaces" — not "isolates the IGP." A predictive description in a menu is a hint.

### 4.2 Classes

Organised by the rung they should surface. **Coverage of every rung is the goal**, because a catalogue that only exercises rungs 1 and 5 measures two-fifths of the descent.

| Class | Example | Expected rung |
|---|---|---|
| **Session-level** | administratively shut a BGP neighbor | `bgp_session` |
| **Authentication** | MD5 password mismatch on one side | `transport` |
| **Reachability** | shut the peer's loopback | `route_to_peer` |
| **IGP** | IS-IS shutdown on core interfaces, physical up | `igp_adjacency` |
| **Physical** | admin-down both uplinks | `interface` |
| **Policy** | a route-policy that filters the peer's prefix | *unclassified — see §8* |
| **MTU** | mismatched MTU on a core link | *unclassified* |
| **Timers** | aggressive hold-time mismatch | *unclassified* |
| **Resource** | interface error injection where the platform allows | `interface` via counters |
| **True negative** | shut one of two redundant uplinks; the IGP reconverges | `all_layers_healthy` |
| **Control** | nothing is applied at all | `all_layers_healthy` |

### 4.3 True negatives and controls are mandatory

**A catalogue with no true negatives measures nothing.** If every trial contains a fault, an agent that always reports *some* fault scores perfectly.

- **True negative:** a real change is applied and the network correctly absorbs it. The right answer is `all_layers_healthy`. This tests that perturbation does not produce a false positive.
- **Control:** nothing is applied. The agent is invoked exactly as in a fault trial. The right answer is `all_layers_healthy`.

Target at least **25% of trials** with a correct answer of "nothing is wrong." A false-positive rate is as important as a hit rate, and cannot be measured without them.

#### The concrete case, found before the harness existed

This section argued from principle until T-029b, when the true-negative scenario was constructed offline and **the descent failed it** (OBS-079). One uplink down on a device with two, IGP reconverging over the survivor:

```
rung 1  bgp_session    HEALTHY     <- Established, carrying traffic
rung 2  transport      HEALTHY
rung 3  route_to_peer  HEALTHY
rung 4  igp_adjacency  HEALTHY
rung 5  interface      BROKEN      <- ALL_HEALTHY over EACH_PHYSICAL_INTERFACE
```

`cause: interface`, `causal_chain: []`. **A cause reported on an Established session carrying traffic.**

That is exactly the outcome a fault-only corpus can never surface — every label in `tests/fixtures/` was produced by a deliberate change, so every one of them *has* an answer, and a system that always finds one scores perfectly against all of them. The failure was invisible to 1,365 passing tests and to two design documents, and it took thirty seconds to find once the question "what does a *correct absorption* look like" was asked.

**Round 4 is therefore a prediction rather than an observation.** The predicted agent output is recorded in `FINDINGS.md` before the round runs (OBS-082), on the same protocol as the T-033 hand diagnosis: **a predicted failure that then occurs is worth more than a discovered one**, because only the first distinguishes understanding the defect from noticing it.

Which sharpens the rule this section states. It is not merely that true negatives let a false-positive rate be *measured*. It is that **the true-negative case is where a dependency-descent architecture is structurally weakest** — the walk is built to find the lowest broken thing, and "nothing that matters is broken" is the one answer it has no mechanism for reaching. Fix tracked as **B-428**.

---

## 5. Scoring

### 5.1 Outcome classes

Every trial resolves to exactly one:

| Outcome | Meaning |
|---|---|
| **Correct** | rung, device and finding all match ground truth |
| **Right rung, wrong device** | the layer was identified, the localisation was not |
| **Wrong rung — too high** | stopped above the true cause. The pre-Q-017 failure mode |
| **Wrong rung — too low** | descended past the true cause |
| **False positive** | reported a fault where the correct answer was healthy |
| **False negative** | reported healthy where a fault existed |
| **Undetermined** | the descent stopped at an unread rung |
| **Error** | the investigation failed to complete |

### 5.2 Not all errors are equal

The single most important asymmetry in this entire document:

```
                     wrong answer, stated confidently     WORST
                     false negative — missed a real fault
                     right rung, wrong device
                     undetermined — honest refusal
                     correct                              BEST
```

**`undetermined` is a good outcome, not a failure.** An agent that refuses when the evidence does not support a conclusion is doing exactly what it was built to do. A rising `undetermined` rate means evidence collection needs work — it does not mean the reasoning is worse.

Scoring must never optimise `undetermined` downward. A change that converts `undetermined` into *correct* is an improvement; a change that converts it into *wrong* is a regression, even if raw accuracy rises. **Track them separately and never in a single headline number.**

### 5.3 Reported metrics

Per fault class, and overall, on **both** the development and holdout sets:

- correct rate; wrong-rung rate split by direction; false positive; false negative; undetermined; error
- **localisation accuracy** — device correct, given the rung was correct
- **chain completeness** — were the broken rungs above the cause reported?
- **citation integrity** — did every claim resolve? (This is where the fabricated timestamp appeared.)
- **time to answer**, distribution not mean, against the 103s healthy baseline
- **coverage completeness** — what fraction of trials had complete evidence coverage
- **repair rate** — how often grounding rejected a report and forced a retry

### 5.4 A confusion matrix, not an accuracy number

Rows: true rung. Columns: reported rung. Plus columns for `undetermined` and `error`.

A single accuracy figure hides the thing worth knowing: **whether failures cluster.** An agent that is 90% accurate overall but 40% on `transport` has one broken check, not a general quality problem — and the matrix says so immediately where a headline number never will.

---

## 6. Protocol

### 6.1 Per trial

```
  1. supervisor confirms fabric matches golden           (abort if not)
  2. injector selects a fault                            (sealed)
  3. injector applies, verifies by reading the device
  4. injector waits for propagation                      (poll, never sleep)
  5. agent is invoked                                    (knows only the subject)
  6. agent's answer is recorded                          (PUSHED before reveal)
  7. injector restores, verifies by reading
  8. supervisor confirms fabric matches golden
  9. trial scored against sealed ground truth
```

**Step 4 polls rather than sleeps.** A fixed wait sometimes captures a half-propagated fabric, and no rung's verdict is trustworthy in that state.

**Steps 3 and 7 verify by reading the device, never by the write's report.** A push that reported failure may have succeeded; a push that raised may still have applied. This rule was written after a restore reported failure three times while having succeeded on the first attempt.

**Step 6 pushes before step 9 reveals.** Not commits — *pushes*.

A local commit is not a seal. `git commit --amend` rewrites it, `git rebase` reorders it, and both leave a history that reads as though the original ordering held. The property the protocol needs is that the record became **unalterable by the person being tested** before the answer was known, and only publishing to a remote does that.

This is one of the two leakage routes reviewer C named (§3.6): *local Git history treated as an immutable seal when it can be amended or rebased.* C aimed it at the fault catalogue. It applies with equal force to the hand diagnosis and to the agent's recorded answer — which is to say, **to this protocol's central guarantee.**

> **Rounds 1–4 used the weaker form.** Their hand diagnoses were committed before the agent ran, not pushed, and `git commit --amend` was in fact used elsewhere in that session. Their sealing therefore rests on **trust rather than mechanism**.
>
> This does not invalidate them. The ordering claimed is the ordering that happened, and round 1's timestamps (14:11:50 diagnosis, 14:12:02 run) are consistent with it. But "you have my word" and "you have a timestamped remote" are different guarantees, and a protocol that cannot tell you which one it gave you is asserting the stronger while providing the weaker. Stated rather than assumed.

An operator reading a future round's result should be able to check the seal without asking anyone. That is the whole difference.

### 6.1b The sealed prediction must state the fault it assumes

Added after round 5 (OBS-109), where it cost a correct answer a passing score.

> **A sealed prediction states the fault it assumes, in the terms the injector
> applies it. Before the round runs, the harness is read and checked against
> that statement.**

Round 5's prediction said *"shut `Gi0/0/0/0` and `Gi0/0/0/1` on PE2"*. The
harness applies `router isis CORE / interface … / shutdown`, which disables
IS-IS **on** those ports and leaves the ports up. Two different faults, and the
prediction was written from the operator's instruction — *"both PE2 uplinks shut
in one commit"* — which is ambiguous between them, without reading the
injector's `APPLY` block.

So the prediction expected `interface_line_down`, and the interface rung
correctly reported healthy in all thirteen probes, because the interfaces
**were** healthy.

**The reason this is a protocol rule and not a note to be careful.** A blind
protocol's entire value is that the prediction cannot be adjusted after the
fact. That property is what makes a mismatch *unrecoverable*: when the sealed
prediction describes fault X and the harness applies fault Y, the tool's correct
answer about Y is scored as a failure against X, and the seal is precisely what
prevents anyone from fixing it afterwards. **The rigour that protects the round
from bias is the same rigour that locks in a setup error** — §0.15 again, and
the cheap remedy is to check the setup *before* sealing, which costs one reading
of the injector.

Round 5 escaped only by accident: the bound masked phase C's finding, so the
`igp_isolated` that would have been marked wrong was never emitted.

**In practice**, the prediction carries a *Fault assumed* line quoting the
injector's own apply block, and step 2 of §6.1 does not happen until that line
has been checked against the script.

### 6.1a The hand diagnosis, and hedging

The comparison diagnosis is written by a human, before the agent runs, and **pushed** before the agent runs (OBS-076, tightened by OBS-105 — see §6.1 step 6). Two rules govern its content, the second learned from round 2.

**It is recorded verbatim and not evaluated before the run.** No commentary, no assessment, no "this looks right". The point of pushing it is that the ordering becomes a fact in a history the tested party cannot rewrite, rather than a claim in a document.

**An alternative reading may be included only if it states what would refute it.**

Round 2's diagnosis carried a caveat — that the subject address might belong to a different device than the resolver would pick — and it was **wrong**. The next-hop it reasoned from was real and correctly read; it was the transit neighbour, not the destination owner (OBS-089, silent-failure shape 6, occurring in a hand diagnosis).

**It cost nothing, and the reason it cost nothing is the protocol property worth keeping.** It was hedged with an explicit *"if so"*, and it named its own falsification condition: *"if the agent finds every rung healthy and reports `all_layers_healthy`, that mismatch is the finding."* The agent found two broken rungs. The condition fired, the caveat was discharged in one step, and the trial was unaffected.

**An unhedged version of the same inference would have contaminated the round.** It would have stood as a competing diagnosis with no stated way to settle it, and settling it *after* seeing the agent's output is exactly the judgement the blind protocol exists to protect.

> **A hand diagnosis may include an alternative reading only if it states what would refute it. An uncertainty with a falsification condition is evidence; the same uncertainty without one is a second opinion that arrives too late to be independent.**

This applies to the agent's side too, and already does: OBS-082 records the round-4 prediction with an explicit list of what would falsify it, written before the round.

### 6.1c Fabric property: an ~8-second consecutive-login penalty, resetting after ~20 s

Measured on RR1, 2026-08-17, after round 5 (B-455). Back-to-back
`connect + show clock`:

```
  0.68s   7.62s   8.52s   8.20s   8.72s   7.56s      <- consecutive
  [20s pause]
  0.54s   8.22s   7.77s                              <- the gap resets it
```

**The first login after a gap is nearly free; every consecutive one costs about
eight seconds.** Whether the penalty is armed at all varies with recent history —
twelve back-to-back epochs earlier the same day showed none of it.

This is a property of the fabric, not of the tool, and it has to be written down
here because it silently sets the cost of everything the harness does. Anything
that opens *n* sessions to a device pays roughly *(n−1) × 8 s*, which is why an
evidence epoch measured 4 s on a quiet device and 34–38 s on an armed one, and
why the tool refused to answer about a healthy fabric during round 5's dry run.

**It argues for session reuse well beyond the epoch.** The epoch reduced a
descent from 10 sessions to 4 and that is the only lever the tool currently has;
the remaining three penalties are ~24 s of a ~36 s observation window. A harness
that probes densely is arming the penalty on itself, and a soak that leaves
20–30 minute gaps between trials (§6.3) never sees it — so **the harness's own
recommended shape hides the cost that dense sampling pays.** Worth knowing
before anyone reads a soak's timings as representative of interactive use.

**And session reuse is only available where a *plan* exists.** Measured again at the MCP surface (2026-08-17): nine parallel `get_lab_device_facts` calls took **70 s with connection retries on two devices**. The epoch avoids that because a flow declares every device and command before collecting; a model calling tools one at a time cannot be planned for, so the penalty returns in full. Anything that measures per-call latency through a model-driven surface is measuring the login penalty rather than the tool — worth knowing before a soak's timings are compared against an interactive session's.

### 6.2 Propagation is protocol-timed

Wait bounds come from protocol timers, never from intuitions about "at the same time":

| Protocol | Bound |
|---|---|
| BGP hold | 180s default |
| IS-IS hold | 30s default |
| BFD | sub-second where configured |

The first live trial measured a **154-second** gap between the interface event and the BGP session dropping — the hold timer expiring. An episode builder using a 30-second proximity window would have split one incident into two.

### 6.3 Session structure

A soak is a sequence of trials with idle gaps. Gaps matter: they let the fabric settle, and they populate the log corpus with normal traffic so the historical baseline has something to be a baseline *of*.

Recommended shape for a 24-hour run: 40–60 trials, 20–30 minutes apart, 25% true negatives and controls, faults drawn from set A only, in randomised order with a recorded seed.

---

## 7. Combination faults — the untested case

Every trial so far has had exactly one cause. **Real incidents frequently have two**, and the design has never answered what happens then.

### The open question in D6

Suppose the interface is down **and** the BGP neighbor is administratively shut.

The descent reports `interface_line_down`, because the lowest broken rung is the interface. That is what D6 specifies — and it is arguably wrong, because **fixing the interface will not bring the session up.**

#### The sharper statement: the rung tables are byte-identical

"True but incomplete" understates it, because it suggests the output is missing something a careful reader might notice. It is not.

> **A single interface fault, and an interface fault plus a BGP shut, produce identical rung verdicts.** Every rung broken, lowest is the interface. One is a complete answer and the other is half of one, and the rung table is byte-identical.

**The masking is structural, not a bug in the walk.** Any fault above the lowest is hidden *precisely because the layer beneath it is also broken* — which is the normal case the ladder was built for and the reason it works at all. The walk is behaving exactly as specified, against a situation the specification did not consider.

Which has a consequence that governs everything below: **no amount of care within the descent can fix this.** The signal is not in the rung verdicts, because the rung verdicts are the same in both cases. It has to come from outside them.

### Why this is the highest-value untested case

Three reasons:

1. It is common in production, especially during change windows and after partial rollbacks.
2. It is invisible to every test written so far, all of which inject one fault.
3. It may require a **semantic change to the descent**, not merely a new check.

### What the descent might need

Options, none yet chosen:

- **Report all broken rungs, not only the lowest.** The chain already carries them; the *finding* discards them.
- **A `multiple_causes` finding** when broken rungs are non-contiguous — a gap of healthy rungs between two broken ones is strong evidence of independent faults.
- **Post-action verification as the arbiter**: fix the lowest, re-descend, and if a fault remains report it. Correct but slow, and it needs Stage 3.

#### Non-contiguity is sound but incomplete — and blind to the case above

The non-contiguity signal is real: with a single cause, broken rungs are **contiguous from the top**, so a healthy rung sandwiched between broken ones does indicate the single-cause assumption has failed.

**It does not fire on the canonical example.** Interface down *and* BGP shut leaves every rung broken — perfectly contiguous. Non-contiguity detects only the two-fault cases where **neither fault propagates far enough to mask the other**: an upper-layer fault that does not reach downward (a policy-blocked session, which leaves TCP up) combined with a lower-layer fault that does not reach upward (one interface down where redundancy keeps the adjacency alive). That is a genuine class and worth testing. It is not the common one, and it is not the one §7 opens with.

Keep it, and stop calling it the primary signal.

#### Forward consistency is the stronger signal, and it is a gap in its own right

After localising, ask whether the rungs **above** the cause look the way that cause **alone** predicts. An interface down predicts a session that timed out; an administratively shut session reports a distinguishable state. A mismatch between predicted and observed upper rungs is positive evidence of a second, masked fault — and unlike non-contiguity it works on the canonical case.

**This is a closed loop the design currently lacks, and its absence is not only a two-fault problem.** The descent reasons strictly downward and **never checks that the cause it found accounts for the symptom it started from.** That gap applies to every single-cause investigation too — see OBS-079, where it is recorded as a general architectural gap with a measured instance the current corpus already contains.

**Both hypotheses are testable by injection and by nothing else** — but forward consistency should be the first thing the harness is pointed at, not non-contiguity.

---

## 8. What a fault class cannot predict

Some faults have no obvious expected rung, and that is informative rather than a gap in the catalogue.

A route-policy filtering the peer's prefix leaves the session Established, every layer healthy, and the prefix absent. The descent as specified finds `all_layers_healthy` — which is **correct for the question asked** and useless for the operator's actual problem.

This is not a failure of the descent. It is a demonstration that **the `bgp_session` flow answers "why is this session down", and not "why is this prefix missing."** Those are different objects, and by D5 they are different flows.

The harness will surface a class of faults that no existing flow covers. **That output is as valuable as the accuracy numbers** — it tells you which flow to write next, from evidence rather than from guessing.

Record these as `no_applicable_flow` rather than scoring them as misses.

---

## 9. Relationship to Stage 2

The harness is not only a test tool. **It is the Stage 2 acceptance vehicle.**

```
inject fault → device syslog → syslog-ng → Alertmanager → webhook
     → flow lookup by mnemonic → agent wakes → investigates → reports
```

That is the entire Stage 2 loop, and injection is the only way to exercise it end to end. Three things it tests that nothing else can:

- **the trigger path** — does the right mnemonic reach the right flow?
- **deduplication under real conditions** — one physical fault generates many alerts;
- **behaviour with no human present**, which is the defining property of Stage 2 and the reason its property suite is a gate rather than a follow-up.

It also produces, as a by-product, the thing operational memory needs: **a corpus of known events with known causes.** Every trial writes a labelled incident. After a 24-hour soak, the baseline is not an estimate.

Note against **B-201**: Stage 2 should not ship without a soak run behind it.

---

## 10. What the harness must never become

| Anti-pattern | Why |
|---|---|
| The agent runs the injector | Destroys blinding and grants device-write capability |
| Tuning against the holdout set | Then there is no holdout set |
| A single headline accuracy number | Hides clustering, which is the actionable signal |
| Optimising `undetermined` downward | Converts honest refusals into confident guesses |
| Faults with no true negatives | An always-report-something agent scores perfectly |
| Predictive fault descriptions | A hint in the menu is a hint in the trial |
| A supervisor that trusts the injector's report | The failure this project has now hit four times |
| Running unattended before the manual rounds pass | Building a measuring instrument for something not yet working |
| Fault selection by a human who then sees the evidence | Unconscious cueing. Prefer script-selected with a recorded seed |

---

## 11. Sequencing

**Four manual rounds first.** One match is one data point. If four rounds across different rungs — including at least one true negative — show that agreement is a pattern, the harness is justified. If they do not, the harness would be measuring something that is not yet working.

Then, in order:

| Phase | Work |
|---|---|
| **1** | Catalogue formalised, split A/B, expected rungs sealed |
| **2** | Supervisor process, independent of the injector, force-restore verified |
| **3** | Scorer and confusion matrix over manual trials |
| **4** | Short supervised soak — 4 hours, attended, 8–10 trials |
| **5** | Combination faults, testing §7's non-contiguity hypothesis |
| **6** | 24-hour unattended soak |
| **7** | Fix cycle against set A; validate every fix against set B |
| **8** | Re-run as a regression suite after every subsequent change |

Phase 4 is the gate on phase 6. **An unattended run is earned by a supervised one, not assumed.**

---

## 12. Backlog

| Item | Scope |
|---|---|
| **B-426** | Fault injection harness — injector, supervisor, catalogue, safety ceilings |
| **B-427** | Evaluation corpus and confusion matrix; regression re-runs |
| **B-428** | Combination faults and the non-contiguity hypothesis for D6 |
| **B-429** | Holdout set, built by someone not writing the fixes |
| **B-201** | Stage 2 triggers — should not ship without a soak behind it |

---

## 13. The claim this makes possible

Without a harness, the strongest honest statement is *"it worked on the cases we tried."*

With one:

> Across 200 trials spanning six fault classes and four devices, the agent identified the correct rung in *n*% of cases and the correct device in *m*% of those. It reported a fault where none existed *p* times. Where it could not determine a cause it said so rather than guessing, in *q*% of trials. On a holdout set never used during development, those figures were *n'*, *m'*, *p'*, *q'*.

That is a claim an operations team can act on — and it is the difference between an interesting prototype and something anyone will let near a production network.
