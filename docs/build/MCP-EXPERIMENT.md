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

### 6.2 A model restatement dropped a rung and misattributed a device — the live case for B-439

The tool returned five rungs with their devices. The model's prose restatement:

* **listed four**, omitting `route_to_peer` — which it had reported correctly
  one message earlier;
* **attributed IS-IS and interface health to RR1**, when both rungs resolve to
  **PE2**.

Three messages. No probing. Nobody was testing for it.

Both errors are the reviewers' scenario in miniature. Dropping `route_to_peer`
removes a link from the chain, and the remaining four still read as a coherent
explanation — of a path that was never checked. Misattributing IS-IS and
interface health to RR1 inverts the single most important thing the descent
establishes: **those rungs resolve to PE2 because the far end is where the fault
lives** (Q-013). An engineer acting on it goes to the wrong device with a
confident, specific, fully-sourced answer.

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
rung).
