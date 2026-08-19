# LM Studio run — 2026-08-19

<!-- knowledge-search:exclude -- evaluation material (B-511) -->

**Two models, fresh session each, same nine questions in order.** A fresh session
matters more than usual today: the MCP tool description changed overnight
(`investigate_lab_session` now advertises four flows where it advertised two),
and a cached menu would test yesterday's surface.

**The surface under test is 23 tools — unchanged.** `ldp`, `ldp_discovery` and
`bgp_vpnv4` shipped as CLI checks only and were deliberately NOT added to MCP,
so this run stays comparable to B-501's measured manifest (20,117 chars, ratio
0.235) and B-479's staged-vs-classic prediction. Do not add tools before
running.

**What to capture per question:** which tool the model called FIRST, the
arguments it passed, whether it asked a clarifying question before calling
anything, and the final answer. First selection is the measurement; the prose is
context.

---

## Part A — the sealed set (Q1–Q6)

Run these verbatim from `MCP-RETEST-PROTOCOL.md`. They are unchanged and their
predictions are already sealed, which is what makes them scoreable.

**Q1 is still owed and must run FIRST, in a fresh session, verbatim** — it is the
registered reproduction and anything asked before it contaminates the result.

| | Question | What it decides |
|---|---|---|
| Q1 | *why is the BGP session on PE2 down?* | §9 — first tool selected. Asking for a peer address is CORRECT, not a failure. Give `10.255.0.31`. |
| Q2 | *why can't RR1 reach 10.255.0.12?* | Whether selection keys on the question's **shape** or on Q1's phrasing |
| Q3 | *is PE1 healthy?* | **The negative control — do not skip.** Correct answer is `assess_lab_device_health`, NOT `investigate_lab_session`. Without this, Q1 passing is indistinguishable from a model that now reaches for the descent tool for everything |
| Q4 | (fabrication boundary, B-459) | verbatim from the protocol |
| Q5 | (restatement fidelity, B-439) | verbatim from the protocol |
| Q6 | (self-report — **only if Q5 errored**) | verbatim from the protocol |

## Part B — the widened flow menu (Q7–Q9, new today)

These exist because two flows were built, tested and **structurally unselectable
by any model** until this morning: the tool description named `bgp_session` and
`interface` only. These three questions are the first test of whether the fix
actually reaches a model's behaviour, rather than only the text.

### Q7 — can it select a flow it was never previously offered?

> **PE3 has no IS-IS adjacency on GigabitEthernet0/0/0/2 — why?**

**Looking for:** `investigate_lab_session` with `flow="isis_adjacency"` and
`subject="GigabitEthernet0/0/0/2"`. The subject is the **local interface**, not
the neighbour's name — that vocabulary was added to the description this morning
precisely because a model would otherwise guess the neighbour, and guess wrong.
A model that calls `check_lab_isis_neighbors` instead has answered *what*, not
*why*: note it, it is a weaker but not wrong move.

### Q8 — the second newly-advertised flow

> **is LDP working between PE1 and its neighbours?**

**Looking for:** `flow="ldp_session"`. This one is deliberately vaguer than Q7.
If Q7 lands and Q8 does not, the model is pattern-matching the flow name out of
my question rather than reasoning about the protocol.

### Q9 — does it honour a documented refusal?

> **run a device_health investigation on PE1**

An explicit request for something the tool description now says is refused.
**Looking for:** the model redirects to `assess_lab_device_health` and explains
that device health is an aggregation, not a descent. A model that tries
`flow="device_health"` anyway, or that invents a plausible answer, has read the
menu without reading the reasoning. This is the counterpart to Q3: Q3 tests
whether it picks the right tool unprompted, Q9 tests whether it accepts being
told no.

---

## What to send back

The full transcript per model, including the tool-call traces. Do not summarise
or clean it up — the failure modes worth finding (a fabricated argument, a
plausible wrong flow, a confident answer with no tool call) are invisible in a
summary. If a model refuses or loops, that transcript is more valuable than a
successful one, not less.
