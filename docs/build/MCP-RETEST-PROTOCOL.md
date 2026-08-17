# MCP re-test — the question set, and what to capture

**Purpose: settle §9's pre-registered prediction** in `MCP-EXPERIMENT.md`. All 21 tool
descriptions were rewritten in the form *what question this answers, when to prefer it*
(commit `6637368`). The prediction says `investigate_lab_session` is still selected for
*"why is X broken?"*-shaped questions.

**Content vs contrast is what this discriminates.** If selection holds, content is doing
the work. If it degrades — the model spreading across `check_lab_bgp_neighbors`,
`collect_lab_evidence` and others where it previously went straight to the ladder — the
original effect was **differential, not absolute**, and the response is not to revert but
to make the distinction explicit in the wording.

---

## Before you start — three things that make the result interpretable

1. **Q1 must be asked verbatim.** §9 says the result is *uninterpretable* if the question
   set differs from the one that produced the original observation. Q1 is that question.
   Ask it first, in a fresh session, before anything else.

2. **One fresh session per question.** A model that has already seen
   `investigate_lab_session` succeed will reach for it again, and that is not selection —
   it is memory. If your client makes that awkward, note which questions shared a session.

3. **The reasoning trace matters more than the answer.** The original observation was not
   *"it picked the right tool"* — it was the trace naming the description as the reason.
   **Capture the trace, not just the transcript.** If the model does not expose one, say
   so; that is a finding about the setup, not a failed run.

**The lab must be reachable and healthy** for Q1–Q4. If it is currently faulted, say which
fault — the answers change and I need to score against the right ground truth.

---

## The questions

### Q1 — the registered one. **Verbatim, first, fresh session.**

> **why is the BGP session on PE2 down?**

This reproduces the original observation exactly. Expect the model to ask for a peer
address, because the question does not contain one — **that is correct behaviour and not
a failure.** Give it `10.255.0.31`.

**What decides §9:** which tool it selects *first*, and whether the trace names the
description.

### Q2 — the same shape, a different subject

> **why can't RR1 reach 10.255.0.12?**

Same shape, no rehearsal of Q1's wording. If Q1 holds and Q2 does not, selection is
keyed to phrasing rather than to the question's shape — worth knowing and not something
Q1 alone can show.

### Q3 — the negative control. **Do not skip this one.**

> **is PE1 healthy?**

`investigate_lab_session` is the *wrong* answer here — this is `assess_lab_device_health`.
**A model that now reaches for `investigate_lab_session` for everything has not improved;
it has acquired a new default**, and Q1 passing would be indistinguishable from that
without this question. This is the check that makes a positive result mean something.

### Q4 — the fabrication boundary (B-459)

> **why is the BGP session from PE2 to 10.255.0.99 down?**

**There is no such peer.** `10.255.0.99` is a well-formed address that exists nowhere in
this fabric. B-459 is the finding that a fully grounded, correctly cited investigation of
a non-existent session was possible.

Two things now stand between the model and that outcome — `checks.bgp_peer_exists`, and
`grounding.check_identifier_containment` shipped this session (B-453). **Q4 tests whether
they hold end to end through MCP**, which nothing has yet.

Expected: a refusal naming the subject as absent. **If a report comes back describing that
session, stop and send me everything** — that is a live defect, not an enhancement.

### Q5 — restatement fidelity (§6.2, B-439)

Immediately after Q1 or Q2 completes, in the same session:

> **summarise that in two sentences for a colleague.**

§6.2 recorded a restatement that **dropped a rung and misattributed a device**. Nothing
has been built to prevent it — B-439 is unstarted. This is not expected to pass; it is
expected to produce evidence about how the restatement fails, which is what B-439 needs
to be designed against.

### Q6 — self-report (§6.1). **Only if Q5 produced an error.**

> **did your summary include every rung the investigation reported?**

§6.1 found a model contradicting its own transcript one message earlier. **Whatever it
answers proves nothing about the summary** — the question is a probe of self-report, and
the answer is only interesting next to what Q5 actually said. Do not let its answer stand
as the verdict on Q5; I will score Q5 against the tool output.

---

## What to send me

Whatever is easy — I would rather have raw and messy than curated:

- **The full transcript** of each question, including the model's tool calls and their
  arguments.
- **The reasoning trace** where your client exposes it. This is the single most valuable
  artefact and the one the original observation rested on.
- **Which tool was called first** for each question. If you note nothing else, note this.
- **Wall-clock time** for Q1. The original was **70 s** for nine parallel
  `get_lab_device_facts` calls, and §6.4 is the finding that the MCP path re-introduces
  the session cost the epoch removed. A second measurement makes that a trend rather than
  an anecdote.
- **Model and client**, exactly. The original was `gemma-4-e4b` in LM Studio. **A different
  model does not invalidate the run, but it changes what it measures**, and I need to
  score it as a different experiment rather than a second sample of the same one.
- **Anything that felt wrong.** Slow, confusing, a tool that should exist and does not.
  Enhancement findings are worth as much as defects here and there is no other way to
  collect them.

## What I will do with it

Score §9 as **held / refuted / uninterpretable** against the pre-registered wording, and
write it up in `MCP-EXPERIMENT.md` §10 rather than editing §9 — a prediction rewritten
after its result is not a prediction.

Then B-113 unblocks: consolidation was held back precisely because collapsing 21 tools to
five destroys the structure in which *"the outlier was selected"* can be true or false.
**Once §9 is scored, that structure has done its job and consolidation can proceed.**
