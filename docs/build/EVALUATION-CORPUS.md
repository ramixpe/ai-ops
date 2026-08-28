# Evaluation corpus and confusion matrix (B-427)

<!-- knowledge-search:exclude -- evaluation material (B-511) -->

Companion to `docs/design/chaos-harness.md` (fault injection, the scoring
vocabulary, §5's confusion-matrix requirement) and `docs/build/PROCESS.md`
§0.13. This is the retained, concise record of six scored/sealed rounds plus
the pre-round-numbering trials in `FINDINGS.md` (OBS-076 through OBS-098).
Raw payload archives and narrative round reports were intentionally removed
after their executable regression vectors and scored outcomes were preserved
here.

## 0. Three rules this document does not violate

Stated up front because they are binding and because the pressure to violate
each of them arrives at a predictable moment.

1. **Corpus integrity (B-427, OBS-098).** *A corpus records what the system
   did at the time, not what it does now. Scores are never rewritten after a
   fix. Corrections are appended as new rows with the fix referenced.*
   **Round 4 keeps its ❌** (§2.1) even though B-428 later closed that class —
   a new row is appended, not a rewritten one. The same is true of round 5's
   probes 08/10/11/99 and B-454 (§3).
2. **Never a single accuracy number.** Per `chaos-harness.md` §5.2, the
   outcome ordering — wrong-but-confident → false negative → right rung
   wrong device → **undetermined (a good outcome)** → correct — is *"the
   single most important asymmetry in this entire document"*. Nothing below
   computes or reports one headline pass rate.
3. **Per dataset, never aggregated across datasets.** Every trial scored here
   belongs to the same dataset: `chaos-harness.md` §3.5's **development**
   set (visible, injected during soaks, used to find and fix defects — see
   §7 below). There is no regression-set trial independent of it (the
   regression *mechanism*, `tests/test_rounds_regression.py`, replays these
   same cases) and no one-shot audit set (B-452 is `BLOCKED`, deferred by
   decision — the set does not exist and this document does not pretend
   otherwise, per §3.5's own instruction: *"until then a claim about unseen
   performance stops after the first sentence and names the dataset it came
   from"*).

## 1. Scope: what is scored here and what is not

Six sealed rounds exist. Two shapes, not one:

| Round | Shape | In the confusion matrix? |
|---|---|---|
| 1–4 (pre-numbering: T-033/OBS-076-077, OBS-087-090, OBS-091-093, OBS-082/094-098) | single blind diagnosis vs. an independent hand prediction, sealed before the run | **yes — §2** |
| 5 | continuous sampling through a propagating fault; ground truth *changes* mid-round | **no — its own table, §3** |
| 6 | sealed, **never run** | **excluded, §4** |
| 7 | mechanism validation (B-462: does a down port persist as an LFA backup) — never ran `investigate`, produced no rung vector | **no — §5, mechanism validation** |
| 8 / 8b | mechanism validation (B-463: can rungs 1/2 separate) — never ran `investigate`, produced no rung vector | **no — §5, mechanism validation** |

This follows the backlog's own note on round 7, word for word: *"It is also
the first scored round that validates shipped code rather than measuring
diagnostic accuracy, so it belongs in the corpus under a different heading:
mechanism validation, not a blind trial."* The same reasoning applies to
round 8/8b — both sampled protocol fields directly (`socket_armed_read`,
FSM state, route-table entries) and never invoked `nettools investigate`, so
neither produced a `(predicted rung, actual rung)` pair to score. Forcing
them into §2's matrix would be scoring an instrument test as if it were a
diagnostic trial, which is not what happened.

## 2. Rounds 1–4: the confusion matrix

Rows: true rung (or "healthy" for a true negative). Columns: reported rung,
plus `no_fault_on_path`/`all_layers_healthy` bucketed under "healthy",
`undetermined`, `error`. Per `chaos-harness.md` §5.4.

| True ↓ / Reported → | bgp_session | transport | route_to_peer | igp_adjacency | interface | healthy / no_fault | undetermined | error |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| bgp_session | | | | | | | | |
| transport | | **R2** ✅ · **R3** ✅⚠ | | | | | | |
| route_to_peer | | | | | | | | |
| igp_adjacency | | | | **R1** ✅ | | | | |
| interface | | | | | | | | |
| healthy (true negative) | | | | | **R4 (at the time)** ❌ | R4 (now, B-428) ✅ | | |

⚠ = correct rung, weaker answer than the evidence supported (column (c),
§2.3). No cell in this matrix is empty because it "wasn't tried" — every
column exists because `chaos-harness.md` §5.4 requires it, and this corpus
is too small yet to populate most of them. **`right rung, wrong device`
never occurred; `false negative` never occurred as a distinct diagnostic
outcome in this table** (round 5's false-negative-shaped outcome is a
propagation-window effect, scored separately in §3, not a rung-confusion
event). `undetermined` (the literal `flows.UNDETERMINED` finding, produced
only when a rung could not be read at all) **never occurred in any of the
six rounds** — see §6.

### 2.1 Required columns

| # | Fault | Predicted rung | Actual rung | (a) Match | (b) Via | (c) Answer ≥ evidence | Outcome class (§5.1) |
|---|---|---|---|:---:|---|:---:|---|
| 1 | IS-IS shut on PE3's uplinks (`RR1 → 10.255.0.13`) | `igp_adjacency` | `igp_adjacency` | ✅ | primary | ✅ | **correct** |
| 2 | Transport block, `RR1 ↔ PE1` (`RR1 → 10.255.0.11`) | `transport` | `transport` | ✅ | primary | ✅ | **correct** |
| 3 | BGP admin-shut on PE2 (`RR1 → 10.255.0.12`) | `cause_not_localised` (primary) / `transport_blocked` (declared refutation) | `transport_blocked` | ✅ | **refutation branch** | ❌ **weaker** | **correct, impoverished** — not one of §5.1's eight classes; see §2.3 |
| 4 (**as scored 2026-08-16, unchanged**) | One uplink shut on PE2, IGP absorbs (`RR1 → 10.255.0.12`) | `interface`, exit 1, cause named | `interface`, exit 1, cause named | ✅ (rung) | primary *(predicted failure — OBS-082)* | n/a | **false positive** — `trustworthy: true`, exit 1, on an Established session carrying traffic |
| **4b (appended 2026-08-17, current code, B-428)** | same fault, replayed | `no_fault_on_path`, exit 0, no cause named, broken rung reported as observation | same | ✅ | primary | n/a | **correct** — B-428 fixed exactly this class; row 4 is **not edited**, this is a new row |

Source: OBS-076/077 (round 1), OBS-087/088/089 (round 2), OBS-091/092/093
(round 3), OBS-082/094/095/096/097 (round 4), `tests/test_rounds_regression.py`
(round 4b, executable). Every hand prediction was committed to git before its
run (OBS-076/087/091, OBS-082 before the fault even existed); rounds 1–4 used
the pre-§6.1 "committed" seal rather than the stronger "pushed" seal
`chaos-harness.md` §6.1 now requires (OBS-107) — recorded as a weaker
guarantee, not treated as equivalent to the later rounds'.

### 2.2 Prediction accuracy vs. diagnostic accuracy — kept separate (OBS-095)

**Do not report "3 of 4" or "75%".** The four rounds are not one population:

| Class | Rounds | Diagnostic result |
|---|---|---|
| A fault exists **on** the dependency path | 1, 2, 3 | **3 / 3 correct** |
| No fault **on** the dependency path (true negative) | 4 | **0 / 1** at the time, structurally 0/*n* until B-428 — **1/1 after** |

Averaging these produces a number wrong in both directions: it understates
the fault-localisation result and overstates the health result. The
*prediction* record (what the operator predicted the tool would do,
including OBS-082's prediction that round 4 would fail) is **4 of 4**, and
that is a different, stronger claim than the tool's own diagnostic record —
worth keeping separate because it says the understanding of the failure
modes was ahead of the implementation, not that the implementation was good.

### 2.3 Column (c): round 3, read from the envelope, not the output

**Column (c) cannot be graded from the output alone.** Round 3's finding
(`transport_blocked`) is *true*. The archived material that makes it
*impoverished* is a field inside the same parsed envelope the check already
had and did not read (OBS-092/093, "silent-failure shape 7"):

```
connection_state    : Active
last_reset_reason   : BGP Notification received: administrative shutdown
```

`bgp_transport` read `connection_state` and reported "the transport is not
established." The far end had already said *why* in a field the parser
captured and the check discarded. This is not scored as a diagnostic error —
the rung, device and finding all matched the refutation branch exactly — but
it is a real, distinct cost, and B-427's own row names the requirement this
document is satisfying: *a matrix scoring rungs alone would have recorded a
clean pass on a round that was weaker in two separate ways.*

No per-sample raw envelope exists for round 3. The field values above are
quoted from OBS-092, recorded the day of the run; there is no fresher source
to check them against.

### 2.4 Anti-vacuity (OBS-089, BINDING) — checked once, and it failed

> *A trial tests resolution only if the wrongly-resolved device differs in
> observable state from the correctly-resolved one. A corpus that does not
> arrange this reports resolution as passing while measuring nothing.*

**Round 2 is the only round that explicitly checked this, and it is
vacuous.** The hand diagnosis flagged that `10.255.0.11` might resolve to
P1 rather than PE1 (a transit next-hop mistaken for the destination owner).
Resolved by reading both devices directly:

```
PE1  Loopback0   10.255.0.11/32   <- the subject, correctly resolved
P1   Loopback0   10.255.0.1/32    <- the alternative candidate
```

Both P1 and PE1 were **independently healthy** at trial time. Had the
resolver pointed at P1 instead of PE1, the descent would have produced a
**byte-identical result** — the cause sits at rung 2 (`DeviceScope.LOCAL`,
no resolution needed at all), and rungs 4–5 resolve to a device whose state
was indistinguishable from the alternative. **Round 2 scored a pass on
resolution while testing nothing about it.** Recorded as a corpus-design
defect (OBS-089/090), not a system defect — the fix belongs in trial
*construction*, not in code.

**Rounds 1, 3, 4 and 5 never raised this question at all.** Each subject
resolves to a single, unambiguous owner on this fabric (no alternative
next-hop candidate was ever in play), so none of them tests resolution
either — not vacuously-passing, simply *not attempted*. **As of this
document, B-427's binding resolution requirement has never been satisfied by
construction in any round.** A trial deliberately built so two candidate
devices disagree in state (e.g., two loopbacks reachable via next-hops that
diverge in health) does not exist yet in this corpus. Filed as a gap below
(§8).

## 3. Round 5: a propagation trial, scored on its own terms

Round 5 samples continuously through a fault that propagates **bottom-up**
over ~154 s (the BGP hold timer), so — unlike rounds 1–4 — a single round
contains multiple true states and is not one `(true rung, reported rung)`
pair. It belongs in this corpus under its own heading, exactly as round 7
does (§5), but for a different reason: round 5 *did* run `investigate`
repeatedly and *did* produce rung vectors and findings — it simply produced
many, against a moving target, so folding it into §2's matrix would average
five different physical situations into one row.

Four representative probes cover the three distinct mechanisms and are pinned
executably in `tests/test_rounds_regression.py`.

| Probe | T+ | Rungs (`BGP·TX·route·IGP·IF`) | Skew | Re-read | Finding **at the time** | Exit **then** | Finding **now** (current code) | Exit **now** |
|---|---:|:---:|---:|---|---|:---:|---|:---:|
| 00 | −1s | `.....` | 4.0s | agrees | `all_layers_healthy` | 0 | unchanged | 0 |
| 01–02 | 6–16s | `...X.` | ~4.0s | agrees | `no_fault_on_path` | **0** | unchanged | 0 |
| 03–07 | 24–56s | `..XX.` | ~4.0s | agrees | `no_fault_on_path` | **0** | unchanged | 0 |
| 08 | 64s | `..XX.` | **34.0s** | agrees | `temporally_incoherent` | 2 | **`no_fault_on_path`** | **0** |
| 09 | 117s | `.XXX.` | 38.5s | **disagrees** (`bgp_session` H→B) | `temporally_incoherent` | 2 | unchanged | 2 |
| 10, 11, 99 | 179–340s | `XXXX.` | 34–39s | agrees | `temporally_incoherent` | 2 | **`igp_isolated`** | **1** |

### 3.1 Two findings this round produced, kept separate per rule 2

**Phase A (probes 01–07) — a wrong answer, stated confidently, that item 3
(the coherence check) does not and cannot catch.** For 50 continuous
seconds, `RR1 → 10.255.0.12` is genuinely blackholing (PE2's IS-IS and route
to the peer are both down) while rung 1 (`bgp_session`) still reads healthy
— the BGP hold timer has not expired. The tool reports `no_fault_on_path`,
**exit 0**, `trustworthy: true`, coherence `ok: true`. **This is the
round's most serious finding**, per `ROUND-5.md` §7.1: *"Stable, within
bound, coherent, and wrong."* In the §5.2 ordering this sits with the
worst tier — a confidently-stated wrong answer, not a mere false negative,
because `no_fault_on_path` is an affirmative claim ("nothing on this
path"), not silence. It is unaffected by B-454 (§3.2 below is a different
defect) and remains open; it is exactly what B-428's qualification
("rung-1-healthy is right off-path, wrong on-path with the symptom not yet
propagated") describes, per OBS-097's amendment.

**Phase B (probe 09) — the coherence check catching a real transition, the
mechanism working as designed.** `stable: false`, `agrees: false`: rung 1
read healthy at collection and broken 38 s later. `temporally_incoherent`,
exit 2. This is a **good outcome** per §5.2's ordering — an honest refusal
on observations that genuinely do not describe one state — and it is
unchanged by B-454 in either direction: a re-read disagreement refused
before the fix and still refuses after it (`tests/test_rounds_regression.py::
test_round_5_probe_09_is_the_one_case_b454_must_not_change`).

### 3.2 B-454: `undetermined`-class converted to `correct` (the good direction)

Probes 08, 10, 11 and 99 disagreed with nothing — every re-read **agreed**
— and were incoherent only because collection took 34–38 s against the 30 s
bound. **Probes 10/11/99 sampled a settled, fully-converged broken fabric,
stable for four minutes**, whose correct answer (`igp_isolated`) was sitting
in the same payload the whole time. The bound discarded it and reported a
refusal instead.

Scored at the time (2026-08-17) as `temporally_incoherent`/exit 2 — an
honest refusal, and per §5.2 a **good** outcome relative to a confident wrong
answer, but a worse one than it needed to be, since a correct answer was
achievable. B-454 (`docs/build/BACKLOG.md`, `DONE`, `6598df8`) separated "the
fabric moved" (`FABRIC_MOVED`, still refuses) from "collection was slow but
every read agreed" (`WINDOW_LIMITED`, now qualifies the finding instead of
discarding it). Replayed against current code
(`tests/test_rounds_regression.py::test_round_5_width_only_breach_no_longer_destroys_a_settled_answer`):
probe 10's exact skew (38.2 s) and rung vector now produce `igp_isolated`,
exit 1, with the width caveat carried in `Coherence.caveat` rather than the
finding being discarded.

**This is precisely the direction §5.2 calls an improvement**: *"A change
that converts `undetermined` into correct is an improvement... track them
separately and never in a single headline number."* Recorded here as data,
not as a rewrite of round 5's original score — `ROUND-5.md` §7 is untouched,
and the table above carries both numbers side by side rather than replacing
one with the other.

## 4. Round 6: excluded, pending

Round 6 was fully sealed but never run: subject, fault (`fault_lab.py` option
8: BGP neighbor shutdown plus a shut spare interface `Gi0/0/0/2`), prediction,
and preflight existed, but no result/payload exists to score. `BACKLOG.md`
lists B-440 as `BLOCKED`, "needs the lab."
**Excluded from every table above and noted here as the reason.** When it
runs, it is a fifth blind diagnostic trial and belongs in §2's matrix, not
in a new heading — its shape (one subject, one sealed prediction, one true
rung) matches rounds 1–4, not round 5's continuous sampling.

## 5. Mechanism validation: rounds 7, 8, 8b

Not diagnostic trials. Neither ran `nettools investigate`; both sampled
protocol state directly and validated a specific piece of shipped logic
against a live fault. Recorded here for completeness of "what the six
rounds established," not as rows in the confusion matrix.

| Round | Validated | Result | Backlog |
|---|---|---|---|
| 7 | B-462 — does a down port persist as an LFA backup, defeating `EACH_PATH_INTERFACE`'s member-set derivation? | **No.** 99 samples with `Gi0/0/0/0` admin-down, zero naming it in PE2's route, at 1.04 s resolution, with a positive control of comparable magnitude in the same run (the restore-side transition, caught once). | `DONE`, OBS-129 |
| 8 | B-463 — can `bgp_session`/`transport` separate at all (rung 2 armed while rung 1 is not Established)? | **Void on first attempt** — the sampler's own socket regex could not produce the value that would have confirmed or refuted it (a first-match search took the wrong field of a three-field line); caught by the instrument's own baseline (`socket_armed: False` on 195 Established samples). | see 8b |
| 8b | Same claim, re-run with the parser fixed and mutation-guarded (`ROUND-8-SOCKET`) | **HOLDS** — 131 separation samples of 1,725 at 0.54 s. **But 127 of 131 were in FSM state `Connect`**, where TCP is *not* established — rung 2 was reading a false healthy (B-497). Only 4 were the honest case (`OpenSent`). | `DONE`, ROUND-8 §7 |

Round 8's own most important result was not its sealed prediction — it was
an instrument defect (a regex bug) that produced a confident, wrong,
publishable number (`SEPARATION_OBSERVED: false`) before anyone reran it.
Round 8b's most important result was a second defect the round was not
looking for (B-497, rung 2's false healthy in `Connect`), found only by
reading separations by FSM state rather than counting them. Neither of these
is a diagnostic accuracy measurement, which is exactly why they sit here and
not in §2 — but both produced real defects fixed in shipped code, which is
what the development set (§0, rule 3) is for.

## 6. Is the `undetermined` column populated? What does it contain?

**Not by the literal finding `flows.UNDETERMINED`.** That finding fires only
when a rung could not be read at all (`UNEVALUATED` status stops the walk) —
no rung, in any of the six rounds, was ever unreadable. The column is empty
in §2's matrix for a real reason, not an oversight.

**The adjacent "no trustworthy answer" finding, `temporally_incoherent`, is
populated** — five of round 5's thirteen probes (§3), all of them scored
`trustworthy: false`, exit 2. `chaos-harness.md` groups both under the same
severity band in §5.2's ordering (an honest refusal, ranked *better* than a
false negative or a wrong-but-confident answer), and cli.py's own exit
scheme treats them identically (exit 2, "no trustworthy answer produced" —
`_cmd_investigate`'s docstring). This document keeps the two finding strings
distinct because they are diagnostically different failures — `undetermined`
means a collection failure, `temporally_incoherent` means the observations
do not describe one state — even though neither one should ever be
"optimised downward" per §5.2's explicit instruction.

## 7. Regression suite: what is re-runnable, and what is not

`tests/test_rounds_regression.py` now pins:

- **Rounds 1–4**, unchanged from before this task — four rung vectors plus
  three composed corpus shapes, replayed through `_finding_for` directly
  (deterministic, no lab, no model).
- **Round 5**, newly added — four representative probes (00, 03, 08, 09, 10)
  replayed through `_finding_for` **and** `epoch.Coherence`, using the
  probes' own recorded skew and re-read-agreement values from
  `ROUND-5.md` §7. Per rule 1, the historical (at-the-time) finding for each
  probe is recorded in the file's docstring and this document, **not**
  re-derived from current code — only the *current* behaviour is asserted.
  Two tests are explicit regression guards: one pins that a re-read
  disagreement still refuses (B-454 must never regress probe 09's
  mechanism), one pins that a width-only breach no longer discards a settled
  answer (B-454 must never regress toward probe 08/10's old behaviour).

**What is deliberately not added here:**

- **Round 6** — no payload exists yet (§4).
- **Round 7 / 8 / 8b** — produced no rung vector (§5); their own regression
  coverage already lives elsewhere and is out of this lane's ownership —
  the anchored socket-parser tests in `tests/test_template_parsers.py`
  (round 8b precondition 1) and the `ROUND-8-SOCKET` guard in
  `scripts/mutate_guards.py`, neither of which this task touches.
- **Column (c) (weaker-than-evidence) and the anti-vacuity condition** are
  not mechanically regression-tested at all yet — both are corpus-design
  properties (what a trial was built to exercise), not properties of
  `_finding_for`'s output, so a rung-vector replay cannot detect their
  absence. Left as a gap, not silently declared covered.

## 8. Open gaps, named rather than left implicit

- **B-427's own resolution requirement is unmet by construction** (§2.4) —
  no trial exists yet where two candidate devices genuinely disagree in
  state.
- **Column (c) is measured for exactly one round** (round 3). B-433
  (`DONE`) made the underlying defect class — parsed-and-discarded fields —
  measurable and test-covered at the code level, but that is a different
  claim from "this corpus's column (c) is populated for every trial."
  Rounds 1, 2, 4 and 5 are not verified either way for this column; marked
  as such above rather than assumed clean.
- **B-452 (one-shot audit set) is `BLOCKED`, deferred by decision.** No
  number in this document is, or could be, an estimate of unseen
  performance (`chaos-harness.md` §13's second sentence). Everything here
  is development-set evidence: *"these failures are understood," never a
  rate.*
- **Q-019 (two simultaneous faults)** remains untested by any of the six
  rounds — every fault so far was single. `chaos-harness.md` §7's
  non-contiguity/forward-consistency question is still open.
- **The confusion matrix in §2 has four cells filled out of many possible.**
  Sample sizes small enough that `chaos-harness.md` §3.5's own arithmetic
  (59 trials for a 5% bound, zero observed) does not apply yet — this
  corpus has trials, not an estimand, and reporting a rate from it would be
  the exact error §3.5 warns against.

## 9. Operational acceptance: confirmed-commit fault 7

**2026-08-24, PE2, not a blind scored trial.** Faultlab applied the
wrong-remote-AS fault toward RR1 using a direct IOS-XR confirmed commit with a
420-second rollback backstop. Device readback confirmed the BGP configuration
change. An explicitly unblinded, read-only MiniMax event callback then ran
during the hold and returned a deterministic `transport_blocked` finding for
`RR1 -> 10.255.0.12`: BGP session and transport were broken while route,
IS-IS, and interface rungs remained healthy. The event ticket recorded
`INC-20260824-00001` and the code-observed Telegram drill/final diagnosis ran.

Explicit revert restored the config; the declared `clear bgp 10.255.0.31`
recovery restored `Established` on the first attempt; an independent post-run
preflight passed. The injector's sealed receipt is
`faultlab/runs/20260824-085930-pe2/truth.jsonl`; the removed event-agent output
remains available in Git history.

This validates fault containment, event-agent execution, ticket/notification
delivery, deterministic localization, and restore. It does **not** estimate
diagnostic accuracy or replace a sealed blind trial.

## 10. Operational acceptance: confirmed-commit fault 3

**2026-08-24, PE2, not a blind scored trial.** Faultlab shut one PE2 core
interface with the same direct IOS-XR confirmed-commit and coordinated
read-only MiniMax callback. Device readback confirmed the interface config
change. The `RR1 -> 10.255.0.12` BGP-session investigation returned
`all_layers_healthy`: BGP, transport, route, IS-IS, and the *current* routed
interface were healthy after the fabric used its alternate path. This is the
correct absorbed-fault outcome for this subject; it did not fabricate an
interface RCA merely because another PE2 interface was administratively down.

Explicit revert restored the configuration. Faultlab verified the configured
interface up/up, IS-IS adjacency on that interface, and BGP Established on the
first attempt; independent post-run preflight passed. The sealed receipt is
`faultlab/runs/20260824-095044-pe2/truth.jsonl`; the removed event-agent output
remains available in Git history.

This acceptance validates the true-negative/absorbed-fault reporting path and
restore contract. It is operational evidence, not a sealed blind trial or an
accuracy-rate contribution.
