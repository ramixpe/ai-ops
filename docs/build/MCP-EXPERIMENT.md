# The MCP Experiment

**2026-08-17.** A local model, connected to this project's MCP server, asked to
diagnose a real fabric.

The purpose was not to see whether it could. It was to find out what breaks at a
boundary the build had never crossed: until this session, every consumer of a
tool envelope was our own code. An MCP client hands the **whole** return to a
model.

> **A note on this document's own reliability — keep this paragraph if the
> document is ever tidied.** A write-up about confident accounts of prior
> exchanges should say how its own account was assembled.
>
> The tool-call sequence in §5 is
> reconstructed from the operator's reports, not transcribed from a session log
> I hold. Where a quote appears it was supplied verbatim; where a sequence
> appears it is an ordering I was told, not one I observed. Finding §6.1 is
> precisely about a model producing a confident account of a prior exchange, and
> this document would be a poor place to do the same thing silently.

---

## 1. Setup

| | |
|---|---|
| Client | LM Studio on a Mac, over SSH stdio |
| Model | `gemma-4-e4b` |
| Server | `nettools-mcp` (console script → `mcp_server.server:main`) |
| Surface | **21 tools** — the 20 existing, plus `investigate_lab_session` |
| Fabric | the live 9-device IOS-XR lab |

The console script was chosen over `python -m mcp_server.server` because it does
not depend on the working directory, which an IDE launcher does not guarantee.

**Three pieces of work landed before the model was connected**, and the order
matters: the audit came first and changed what the other two looked like.

---

## 2. The invariant-4 audit, and what it found

Invariant 4 is *no unparsed device text ever reaches a model*. It is enforced
structurally in `prompt_library`, which takes a `DescentResult` and therefore
**cannot** hold raw text (OBS-061).

The audit asked one question: does that hold at the MCP boundary? Measured on
actual return shapes, not on what the tools were meant to do.

**It did not. 14 of 20 tools returned raw device output under `data.commands`.**

| Tool | Raw chars returned |
|---|---:|
| `get_lab_logging` (count=200) | **37,962** |
| `get_lab_bgp_neighbor` | 6,139 |
| `check_lab_fabric` (9 devices, live) | 4,239 |
| `collect_lab_evidence` | 2,629 |
| `get_lab_interface` | 1,403 |
| `check_lab_bgp_neighbors` | 985 |
| `check_lab_interfaces` | 909 |
| `get_lab_device_facts` | 537 |
| `check_lab_lldp_neighbors` | 284 |
| `get_lab_ping` | 202 |
| `get_lab_traceroute` | 190 |
| `check_lab_isis_neighbors` | 128 |
| `get_lab_route` | 53 |
| `check_lab_sr_policies` | 29 |

Clean: `list_lab_devices`, both `diff_lab_*`, both `assess_lab_*_health`,
`detect_lab_flaps`.

`get_lab_logging` is the one that matters. 38 kB of unshaped device log buffer —
exactly the input `log_window.py` exists to filter by attribution and
`coverage.py` exists to bound. The `investigate` path reduces it to a few
hundred attributed records with a coverage record attached. The MCP path handed
a model the whole thing.

### What the finding actually is

**Nothing was broken in the library.** Every one of those envelopes also carried
`data.parsed`, and every internal consumer read the parsed half and ignored the
text. The code had been correct for eight phases.

> **An invariant that holds for every internal caller is not an invariant. It is
> a convention that has not yet met a new consumer.**

`prompt_library`'s guarantee was never weakened. It protects the path it is
built into, and nothing enumerated the paths. This is OBS-106's shape at larger
scale — there, `_read_only_tool` was an accurate *hint* read as a *guarantee*,
false for 3 of 20 tools and a write. Here it is 14 of 20 and the project's
central claim.

### The fix

`mcp_server/boundary.py`, applied by the **registration decorator**: registering
a tool *is* sanitising it. Not an argument and not a per-tool wrapper — both
make the guarantee depend on the caller remembering, and here the caller is a
model. A tool added later inherits it with no diff to the server file.

Stripped keys are replaced with a **withheld record** (`chars`, `lines`) rather
than deleted. Silently dropping them would make a command that produced 6 kB
indistinguishable from one that produced nothing.

### Two corrections against the audit itself

Both came from measuring rather than reasoning, and they point opposite ways.

**It understated the leak.** The first pass probed
`parsed["unaccounted_lines"]` and measured zero. The key lives at
`parsed["meta"]["unaccounted_lines"]`, one level deeper. The test found it, not
the audit.

**It overstated the leak.** `unparsed_rows` is named like
`unaccounted_lines`'s sibling and documented beside it — and is an **`int`**.
Stripping it would have destroyed a diagnostic for no safety gain. *A rule
written from a name walks into that; a rule written from a measured type does
not.*

### One vacuous check found on the way

`test_mcp_readme_lists_exactly_the_exposed_tools` filtered by a hand-maintained
prefix allowlist. `investigate_lab_session` was added and the doc-sync test
**passed** — silently not covering the newest tool, which is the one most likely
to be undocumented. Now derived from the registry. Same lesson as the audit, an
hour later.

Recorded as **OBS-111**. Residual: a transport exception can embed device output
in its message, so error strings are truncated at 400 characters — bounded, not
closed (**B-458**).

---

## 3. The new tool

`investigate_lab_session(device, subject, flow="bgp_session")`. Returns the
deterministic report and the descent result — both derived, so it passed the new
boundary unchanged. No model called, no paraphrase, CLI defaults honoured.

The other twenty tools were **deliberately not consolidated**; the 21-tool
surface was the thing being measured.

Its description differs from every other tool's in one specific way. Twenty say
some form of *"collect read-only X"*. This one says what it achieves and when to
prefer it:

> *"**Prefer this over calling the individual check tools yourself** when the
> question is 'why is this broken?'."*

That turned out to be the experiment's first result.

---

## 4. Coverage added before connecting

`tests/test_mcp_live_lab.py` — every tool called once, over a real
`ClientSession`, against real devices. 24 tests, 6m51s, all passing.

It asserts **shape and the boundary property, never the fabric's state**. A
smoke test that asserted BGP was healthy would fail during an injection round
and be read as a tool failure.

`protect_stdio()` — redirects any root log handler already pointed at stdout to
stderr at startup, and installs an explicit stderr handler on an unconfigured
root logger so a later `basicConfig()` cannot install a stdout one. Nothing in
this package writes to stdout; the process is not only this package, and one
stray line turns a log message into a JSON-RPC framing error debugged from the
wrong end.

---

## 5. The session

Reconstructed, per the note at the top.

**Question:** *"why is the BGP session on PE2 down?"*

**1. The model selected `investigate_lab_session`.** Its reasoning trace named
the description as the reason: *"preferred when the question is 'why is this
broken?'"*.

**2. It needed a peer address the question did not contain — and asked for it**
rather than inventing one. Correct behaviour. §6.3 is about why that is not a
guarantee.

**3. The investigation ran** and returned a deterministic report: five rungs,
each with the device it was evaluated against.

**4. Nine parallel `get_lab_device_facts` calls took 70 s**, with connection
retries on two devices. §6.4.

**5. The model restated the result in conversation** — and the restatement
dropped a rung and misattributed a device. §6.2.

**6. Asked about an earlier statement, the model contradicted its own
transcript.** §6.1.

---

## 6. Findings

### 6.1 A model is not a reliable source about its own prior output

The model said *"I did not explicitly say it was 'not established'"*. **It had,
verbatim, one message earlier.**

Small on its own; it bears on a class of design. Anywhere a model is asked to
refer to, summarise, or check a previous turn, the referenced content must be
**re-supplied from the record**, not recalled — recall and generation are the
same operation, and a plausible reconstruction is indistinguishable, to the
model, from the thing itself.

It also rules out a tempting cheap gate: asking a model to check its own earlier
claim measures nothing, because one side of the comparison is regenerated.

Recorded as **OBS-114**.

### 6.2 A model restatement dropped a rung — the live case for B-439

The tool returned five rungs with their devices. The model's **first** report
listed all five correctly. Its **recovery message**, restating the same result,
**listed four — omitting `route_to_peer`**.

No probing. Nobody was testing for it.

It is the reviewers' scenario in miniature. Dropping `route_to_peer` removes a
link from the chain, and the remaining four still read as a coherent explanation
— of a path that was never checked. What matters is not the size of the loss but
that it happened *across a restatement*: the model had the correct answer and
lost part of it while saying it again.

> **A second claim was made here and withdrawn: that the model misattributed IS-IS
> and interface health to RR1. It did not — it read the payload correctly.** The
> investigation was `PE2 → 10.255.0.31`, and `10.255.0.31` is RR1's router ID, so
> those subject-scoped rungs *did* resolve to RR1. The claim came from carrying
> round 2's mapping (`RR1 → 10.255.0.12`, where the same rungs resolve to PE2) —
> same fact, reversed direction, wrong investigation. See §6.6.

**This was not our `paraphrase` field** — the MCP tool produces none, by design.
It was a chat model restating a *correct* deterministic report in ordinary
conversation, **downstream of every gate this build has**, on a surface we do
not control and cannot instrument.

So B-439 is validated and its scope was too narrow. Rendering the authoritative
report deterministically was right; marking a model paraphrase non-authoritative
only governs *our* paraphrase field, and a chat client's prose is a paraphrase
nothing labels. The defence available is the report's *shape* — make an omission
visibly an omission, so a restatement can be checked against the tool output
directly above it. Weaker than enforcement, and it is what exists at a boundary
we do not own.

**B-439 was justified by argument before this. It now has an observation.**
Recorded as **OBS-115**.

**Filed as a distinct boundary — B-461, not an instance of B-439.** B-439
governs *our* paraphrase field: produced here, graded here, marked here. This is
a surface with no field to mark, no grounding hook and no instrumentation, and
collapsing the two would hide which half is fixable.

**The minimal defence, taken:** the report states `rungs_examined` and numbers
every observation `1/5 … 5/5`, each naming its device. A four-item restatement
is then visibly short to a human reading both. **This is not enforcement and is
not described as such** — it makes an omission detectable where nothing can be
enforced.

### 6.6 The correction: shape 6, committed about this experiment

Recorded here rather than quietly amended, because it is the most instructive
thing in the document and it happened *while writing it up*.

The misattribution claim in §6.2 was false. The device mapping was carried from
a different investigation running in the opposite direction. **Neither the model
nor the code was wrong; the account of them was.**

Three things follow.

**It is the failure §6.1 describes, from the other side of the conversation.**
OBS-114 was recorded as a property of models: recall and generation are one
operation, so a model checking what it said produces a fresh claim rather than
retrieving an old one. The mechanism is narrower than the failure. **Anyone
reasoning about a prior exchange without the record in front of them is
reconstructing it**, and a confident reconstruction is indistinguishable from a
memory. The rule already written into `prompts/README.md` is not model-specific.

**It nearly became a test.** A test asserting `igp_adjacency == "PE2"` was
written from the mistaken description — true for round 2's direction, false for
this one — and it **passed**, because the code was never wrong and the test
agreed with a wrong account of it. A passing test would have pinned the error as
expected behaviour, and nothing in the suite could have distinguished the two.
That is §0.13's tests face arriving through a *specification* rather than an
implementation, which is the harder direction to catch: reviewing the code would
not have found it.

The test now parameterises over both directions and asserts *resolution* rather
than a device name, with a falsification check confirming it fails when
resolution is hardcoded.

**One smaller correction in the same direction.** The B-439 sub-item was partly
already satisfied before it was requested — every rung already carried its own
`device` field. Only the count was missing, so what landed is narrower than it
was specified as.


### 6.3 Argument fabrication — the uncovered boundary (B-459)

**Every containment mechanism in this build operates on what a tool returns.
Nothing constrains what a model supplies.**

The allowlist governs what may be *sent*. `render_command` governs how a
parameter is *rendered*. `boundary.sanitize` governs what may be *returned*.
Grounding governs what a model may *claim*. Nothing asks whether the **subject
is real**.

Walk a fabricated address through:
`investigate_lab_session("PE2", "10.255.0.99")`.

1. `render_command` accepts it — a well-formed IPv4 address. Canonicalisation by
   reconstruction is about **syntax**, which is right, and is precisely not
   this.
2. The descent walks. Rung 1 reads `show bgp summary`, finds no such peer, and
   answers honestly.
3. The report renders from real typed fields. Every citation resolves.
4. Grounding passes.

**The output is a fully grounded, correctly cited, deterministically derived
investigation of a session that does not exist.** Nothing malfunctions anywhere:
every component does its job correctly on the input it was given. That is
silent-failure shape 6 moved upstream of the evidence entirely, to the
*question*.

The model asked rather than inventing. **That is one model on one occasion, not
a property.**

The defence has the same shape as the rest: validate an argument naming a
network object against that device's observed state **before executing**. A peer
address appearing in no BGP summary on that device is not a peer; a prefix, an
interface name and a ping target are the same class.

This is **B-453 pointed the other way** — B-453 checks that every identifier in a
model's *output* appears in the evidence; this checks that every identifier in
its *input* appears in the device. Same mechanism, same canonicalisation table,
opposite direction.

Two limits, stated now so the item is not over-scoped later: it catches an
invented object, **not a wrong one** — a real peer address that is not the one
the operator meant passes cleanly, exactly as B-453 catches an invented entity
and not a wrong relation. And validating requires a read *before* the read,
which on this fabric costs a session and an ~8 s login, so **where** the check
runs is a design question.

Filed as **B-459** — not B-458 as instructed, because B-458 was taken an hour
earlier by the error-string residual in the same work. Flagged rather than
renumbered.

### 6.4 The MCP path re-introduces the session cost the epoch removed

**Nine parallel `get_lab_device_facts` calls: 70 s, with connection retries on
two devices.**

This is B-455's login penalty — measured on this fabric as ~0.6 s for the first
login after a gap and ~8 s for each consecutive one, resetting after roughly
20 s of quiet.

**It is structural, not a missing optimisation.** The epoch works because a
*flow* declares every device and command up front, so collection can be planned:
that took a `bgp_session` descent from 10 sessions to 3. On the MCP surface **the
model decides the collection**, one tool call at a time, and a plan cannot be
made for calls that have not been requested yet.

So it is the same defect the epoch fixed, in a place the epoch's fix cannot
reach. `investigate_lab_session` is the only tool on the surface that escapes
it, because it is the only one that plans.

Two consequences worth having in writing:

* a model fanning out across a fabric will hit retries, and may read a timeout
  as a device fault;
* **any per-call latency measured through a model-driven surface is measuring
  the login penalty, not the tool.**

### 6.5 The sealed prediction, refuted

The operator's sealed prediction about the 21-tool surface is **refuted**.

`gemma-4-e4b` selected `investigate_lab_session` correctly, and its reasoning
trace named the description as the reason. Twenty tools say *"collect read-only
X"*; one says what it achieves and when to prefer it.

> **A tool's description is not documentation. It is the selection mechanism** —
> the only part of a tool a model reads before deciding, and the only part it
> can reason about.

**This reframes B-113.** Consolidation (~22 tools → five stage-shaped ones) was
filed as the fix for D10/D11's failure mode. The measurement says **the count
was not the binding constraint; the wording was.** A smaller manifest is still
worth having, but the work that changes selection is rewriting each surviving
description in terms of *what question it answers and when to prefer it* — and
that pays off whether or not consolidation happens.

**The order matters, and getting it wrong is self-concealing.** Consolidate first
and the descriptions get rewritten as a side effect of merging; selection
improves; consolidation is credited with a fix the rewording made. Do the
wording first and the two effects stay separable — and it is the cheaper half.

The controlled comparison has therefore **not** been run. It belongs after the
rewording.

Recorded as **OBS-112**.

---

## 7. What this establishes, and what it does not

### Established

**The audit findings are facts about the code**, independent of any model: 14 of
20 tools returned raw device text, measured; `get_lab_logging` returned 38 kB;
the doc-sync check had stopped covering new tools. Those would have been true
whether or not anyone connected a client, and they are now fixed and tested.

**The argument-fabrication gap is structural**, not a model behaviour. It is
demonstrable by reading the code path, and the walk-through in §6.3 does not
depend on what any model did.

**The session-cost measurement is a property of the surface**, for a reason that
is architectural rather than incidental: unplanned collection cannot be batched.

### Not established

**Everything about the model is one model, one session, a handful of questions.**

* That a well-worded description drives selection: shown **once**. It shows a
  description *can*, not that this one reliably does, and not that a 21-tool
  surface is safe.
* That a model will ask rather than fabricate an argument: shown **once**, and
  §6.3 exists precisely because one occasion is not a property.
* That restatements drop rungs: **one occurrence**, unprompted. It proves the
  failure is reachable without adversarial pressure. It is not a rate.

**No rates. No sampling frame.** These were questions asked by an operator
following a hunch, not trials drawn from a defined population — the same caveat
that governs the four injection rounds, and the same reason none of this is a
number.

**The strongest single result is the one that needed no model at all.** The
audit found a real hole in the project's central invariant, and it found it
*before the first question was asked* — because the act of preparing to connect
a model was what made anyone check.

---

## 8. Backlog produced

| Item | |
|---|---|
| **B-458** | Residual: a transport exception can embed device output in an error string. Bounded at 400 chars, not closed |
| **B-459** | Argument fabrication — validate arguments against observed reality before executing |
| **B-460** | `state_pfx_rcd` holds *either* a prefix count *or* a state string. Split into `session_state` and `prefixes_received` |
| **B-113** | Reframed: wording before consolidation |
| **B-439** | Validated by observation. Numbered rungs + stated count landed as its sub-item |
| **B-461** | The unowned surface, filed as a boundary in its own right rather than folded into B-439 |
| **B-455** | Third measurement added: the MCP surface cannot batch |

Findings: **OBS-111** (the audit), **OBS-112** (selection), **OBS-113**
(argument fabrication), **OBS-114** (self-report), **OBS-115** (the dropped
rung, with its misattribution half withdrawn — §6.6).

---

## 9. The control arm question, decided before rewording

The operator raised it and left the call to me: rewriting 21 descriptions on
**one** observation is §0.13's data face applied to a fix. Should two or three
be left in the original form as a control?

**Decision: no control arm. All 21 are reworded.** The reasoning, recorded
because the alternative was reasonable and the decision should be checkable.

### Why the proposed arm would not measure what it looks like it measures

A control arm isolates a treatment when the arms differ **only** in the
treatment. These arms would differ in *what the tools do*.

`list_lab_devices` is not the right answer to *"why is the BGP session down?"*
under any wording. If it keeps its old description and is not selected, that
measures nothing — it should not be selected. The tools are not interchangeable,
so "reworded" and "not reworded" are not two conditions the same choice can be
made under. A negative result would be uninterpretable and a positive one would
be luck.

### The uniformity hypothesis is already controlled for, and points the other way

The worry is that a fully reworded surface confounds *content* with
*uniformity*. But look at what the baseline actually is: **twenty tools phrased
alike, one phrased differently.**

If uniformity drove selection, the twenty uniform ones would have been favoured.
The **outlier** was selected. Uniformity-as-mechanism predicts the opposite of
the observation, so it is not a live confound for the result in hand — the
current surface already served as its control, by accident.

### The real risk is contrast, not uniformity — and it is sharper

There are two mechanisms consistent with the observation, and the operator's
instinct is pointing at the second even though the framing named the first:

| | Mechanism | Prediction after rewording all 21 |
|---|---|---|
| **Content** | descriptions stating what a tool achieves are more selectable, absolutely | selection holds or improves |
| **Contrast** | the description that *differs from its neighbours* draws selection, whatever it says | **selection degrades toward chance** |

**If contrast is the mechanism, rewording all 21 destroys the signal it is built
on.** That is a real and specific risk, it is more worrying than uniformity, and
it is the strongest argument the operator's position has.

It also needs no control arm to test. The two mechanisms make **opposite**
predictions, so measuring after the rewording discriminates them — provided the
prediction is registered first.

### Pre-registered, before any description is rewritten

> **Prediction.** After all 21 descriptions are rewritten in the form *what
> question this answers, when to prefer it*, `investigate_lab_session` is still
> selected for *"why is X broken?"*-shaped questions.
>
> **If selection holds or improves**, contrast is not the mechanism and content
> is doing the work.
>
> **Refuted if** selection degrades — if the model spreads across
> `check_lab_bgp_neighbors`, `collect_lab_evidence` and others where it
> previously went straight to the ladder. That result would mean the observed
> effect was **differential, not absolute**, and the right response is not to
> revert but to make the *distinction* explicit in the wording rather than
> relying on it emerging from contrast.
>
> **Uninterpretable if** the question set differs from the one that produced the
> original observation. The comparison must reuse *"why is the BGP session on
> PE2 down?"* among others, or it measures a different thing.

### What this still cannot separate

Honestly: if selection improves, *"these words are better"* and *"the surface is
now internally coherent"* remain confounded. Separating them needs a third arm —
all 21 rewritten to be **uniform but uninformative** — and that is a surface
nobody would ship to measure a distinction nobody would act on. Recorded as a
known limit rather than pretended away.

### What the baseline capture buys instead

Appendix A preserves all 21 original descriptions verbatim. That makes the
**right** experiment available later at no extra cost: a *between-surface* A/B —
the same question set against two complete surfaces, all-old and all-new — which
isolates the treatment properly because **every tool appears in both arms**.
That is the design a within-surface mix was reaching for, and it does not
require shipping an inconsistent production surface to get it.

### The cost that decided it

The arm's price is inconsistency on a surface whose entire diagnosed problem was
inconsistency, paid for a measurement confounded by tool non-interchangeability.
The alternative costs one pre-registered prediction and preserves a cleaner
experiment for later.

### Accepted, 2026-08-17

The operator accepted the decision and recorded the ground as better than the
one they had proposed it on:

> *"The arm was confounded — the tools are not interchangeable, so non-selection
> of a tool that was never the right answer measures nothing."*

and on the reframing:

> *"Your contrast framing supersedes my uniformity framing, and the observation
> that the current surface was its own control by accident is the part I would
> not have found: twenty alike, one different, and the outlier won. Uniformity
> predicts the opposite of what happened."*

Worth keeping because it is the cheapest of the three arguments and the last one
either of us reached: **the experiment already had a control and nobody had
noticed, because it was the status quo rather than something anyone built.**

---

## 10. The rewording, as landed

All 21 descriptions now open `Answers: *<the question>*` and say when to prefer
the tool over its neighbours. Pinned by a test that reads the registry, so the
next tool added inherits the requirement rather than the shape being a
convention someone copies or does not.

**One decision that changed during the work, and it was a confound in my own
experiment.** `investigate_lab_session` was going to keep its original wording —
it is the treatment that produced the result, and changing it felt like
disturbing the evidence. That is backwards. §9's prediction is that selection
survives rewording **all** 21; leaving one in a structurally distinct form
preserves exactly the contrast the prediction exists to discriminate from
content. The experiment would have been confounded by its own setup — §0.13's
setup face arriving through the *fix* rather than through the measurement. Its
content is unchanged; only its opening line was brought into the shared form.

**A cost worth stating.** Descriptions went from **5,961 to 11,107 characters**,
+86%. That is sent on every tool-list call, so the manifest a model reads before
it does anything roughly doubled. If selection improves, some of that is paid
for; if B-113's consolidation later reduces 21 tools to five, the per-tool
budget goes further and this cost mostly disappears. Recorded so a future
measurement of context cost is not surprised by it.

**Not yet measured.** The controlled comparison has not been run. §9's
prediction was pushed before the first description was touched.

---

## Appendix A — the 21 tool descriptions, as they stood for this experiment

**Captured verbatim at `44f5c98`, before B-113's rewording.** These are the
baseline for the controlled comparison; once rewritten they are gone from the
working tree, and a comparison against a remembered version of them would be
the failure §6.6 is about.

Generated from the server's registry rather than transcribed. What appears
here is the text the MCP SDK sends as each tool's description — the whole
docstring, which is what a model actually reads.

**The shape of the baseline, in one line:** twenty describe *what they
collect*; one describes *what it achieves and when to prefer it*.

### `assess_lab_device_health`

```text
Evaluate deterministic health verdicts (role invariants + baseline drift) for one device.

Cheap, rule-based -- not an LLM call -- so a client can get a severity
verdict (``ok``/``info``/``warning``/``critical``) and its findings
without spending a reasoning call. See ``assess_lab_fabric_health`` to
evaluate every device at once.
```

### `assess_lab_fabric_health`

```text
Evaluate deterministic health verdicts across every device in the fabric inventory.

Same rules as ``assess_lab_device_health``, rolled up to one fabric-wide
severity (the max over every device's own severity) -- see
``health.evaluate_fabric``.
```

### `check_lab_bgp_neighbors`

```text
Collect read-only BGP neighbor state from a lab device.
```

### `check_lab_fabric`

```text
Run one read-only check (facts|interfaces|bgp|lldp|isis|sr) across the fabric.
```

### `check_lab_interfaces`

```text
Collect read-only interface status from a lab device.
```

### `check_lab_isis_neighbors`

```text
Collect read-only IS-IS neighbor state from a lab device.
```

### `check_lab_lldp_neighbors`

```text
Collect read-only LLDP neighbor state from a lab device.
```

### `check_lab_sr_policies`

```text
Collect read-only Segment Routing TE policy state from a lab device.
```

### `collect_lab_evidence`

```text
Collect the full read-only evidence bundle from a lab device in one session.
```

### `detect_lab_flaps`

```text
Report fields that oscillated across a device's saved snapshot history.

A peer that bounced up/down/up between collections can look clean in
every single pairwise diff -- this reads the device's *entire* saved
snapshot history instead. Requires prior snapshots (``save_lab_snapshot``,
``diff_lab_device_against_latest``, or ``nettools diff``/``capture``) --
with none saved yet, ``data.flapping`` is simply empty.
```

### `diff_lab_device_against_golden`

```text
Collect fresh evidence and diff it against the device's pinned golden snapshot.

``data.has_previous`` is ``false`` (and ``data.diff`` is ``null``) when no
golden snapshot has ever been pinned for this device -- see
``pin_lab_golden_snapshot``.
```

### `diff_lab_device_against_latest`

```text
Collect fresh evidence and diff it against the device's most recently saved snapshot.

Saves the fresh collection as the new "latest" snapshot, same as
``nettools diff DEVICE``. ``data.has_previous`` is ``false`` (and
``data.diff`` is ``null``) the first time this runs for a device -- there
is nothing to compare against yet, not an error.
```

### `get_lab_bgp_neighbor`

```text
Look up a specific BGP neighbor on a lab device.

``address`` must be a plain IPv4 address, e.g. "10.255.0.31". Use this to
narrow in on one peer after ``check_lab_bgp_neighbors`` shows it Idle or
otherwise not Established.
```

### `get_lab_device_facts`

```text
Collect basic read-only facts from a lab device.
```

### `get_lab_interface`

```text
Look up a specific interface's status on a lab device.

``name`` must be a valid interface name, e.g. "GigabitEthernet0/0/0/1",
"Gi0/0/0/2.300", or "Loopback0" -- validated against an anchored
letters/digits/``._/-`` charset, so it can never carry a shell or CLI
metacharacter.
```

### `get_lab_logging`

```text
Show a lab device's most recent log lines.

``count`` must be a plain integer from 1 to 500 (default 20).
```

### `get_lab_ping`

```text
Ping an IPv4 address from a lab device.

``address`` must be a plain IPv4 address, e.g. "10.255.0.31". This is an
active probe: it generates ICMP traffic (unlike every other tool here)
even though it changes no device configuration, and is refused when the
server has ``NETTOOLS_ALLOW_ACTIVE_PROBES`` set to a falsy value.
```

### `get_lab_route`

```text
Look up a specific route on a lab device.

``prefix`` must be an IPv4 address or CIDR prefix, e.g. "10.255.0.31" or
"10.0.0.0/24" -- validated and rendered from its parsed, canonical form
(never passed through as text); anything else is rejected before any
connection is made. Narrow this after seeing a route-related anomaly in
other evidence (e.g. a missing or unexpected next hop).
```

### `get_lab_traceroute`

```text
Traceroute to an IPv4 address from a lab device.

``address`` must be a plain IPv4 address, e.g. "10.255.0.31". An active
probe like ``get_lab_ping``: generates traffic, changes no device state,
and is refused when ``NETTOOLS_ALLOW_ACTIVE_PROBES`` is set to a falsy
value.
```

### `investigate_lab_session`

```text
Localise the cause of a fault by walking a dependency ladder, deterministically.

**Prefer this over calling the individual check tools yourself** when the
question is "why is this broken?". It walks the layers beneath a symptom in
order -- session, transport, route, IGP adjacency, physical interface -- and
reports the *lowest* broken one as the cause, with the broken layers above
it as the causal chain that explains the symptom. Every verdict comes from
code comparing parsed fields, with no model involved.

``device``  the device to investigate *from*, e.g. "RR1".
``subject`` what to investigate, in the flow's own vocabulary. For
            ``bgp_session`` that is the peer's IPv4 address as
            ``show bgp summary`` lists it, e.g. "10.255.0.12".
``flow``    the object type. ``bgp_session`` (default) or ``interface``.

Read ``finding`` first. Values you will see:

``all_layers_healthy``     no fault on the path between these two endpoints.
``no_fault_on_path``       the session is fine; broken layers were found that
                           are **not** on the path -- read ``off_path``, and
                           do not report them as the cause of anything.
``cause_not_localised``    the symptom is real and every layer beneath it is
                           healthy. Look at configuration and policy.
``undetermined``           a layer could not be read, so nothing below it was
                           evaluated. ``reason`` says which.
``temporally_incoherent``  the fabric changed while it was being read. These
                           observations do not describe one state; run again.
otherwise                  the terminal finding for the lowest broken layer,
                           e.g. ``interface_line_down``, ``igp_isolated``.

``trustworthy`` is false when the run did not produce an answer you may act
on -- which is **not** the same as the network being broken. Check it before
reporting a finding.

``coherence.caveat``, when present, must be repeated to the user: the answer
was read over a window wider than the bound, so it is true at both ends of
that window rather than throughout it.

No model is called and no paraphrase is produced. The report is rendered
from the descent's own typed fields.
```

### `list_lab_devices`

```text
List the IOS-XR devices available in the lab inventory.
```
