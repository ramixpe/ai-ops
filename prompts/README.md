# Prompts

Version-controlled, tested artifacts — the same discipline as the command
allowlist. A prompt is not a string someone tweaks until the output looks
right; it is a file with a version, a golden test case, and a review history.

```
prompts/
├── README.md          this file — GRACE, and the rules below
├── report.v1.txt      T-027 — superseded, kept (see Version history)
├── report.v2.txt      B-490 — current: descent result -> grounded report
├── correlate.v1.txt   T-028 — superseded, kept (see Version history)
├── correlate.v2.txt   T-029a — superseded, kept
├── correlate.v3.txt   T-029a — superseded, kept
├── correlate.v4.txt   B-467/B-470 — current: finding + log window -> timeline
└── tests/cases/       golden input -> expected output shape
```

---

## GRACE

Derived from the book's RACE, with Grounding added and Constraints promoted out
of Context.

| | | |
|---|---|---|
| **G** | Grounding | The validated evidence, each item with an evidence key. Every claim must cite one. |
| **R** | Role | The operational perspective. Narrow. |
| **A** | Anchors | One worked example: real input, exact output. |
| **C** | Constraints | Prohibitions, **and the named refusal path** — "if the evidence does not support a conclusion, return `undetermined`". |
| **E** | Expected output | The exact schema. Parseable at temperature 0. No prose wrapper. |

### There is deliberately no Evaluation slot

The book's P.E.N.E. framework ends with Evaluation, and `llm_analysis.py`'s
existing `TROUBLESHOOTING_PROMPT` still carries one: *"Before responding,
verify that every claim is supported by the provided data."*

**That clause is asking the model to mark its own work, and it is not what
makes the output trustworthy here.** Evaluation in this project is code:
`grounding.py`'s citation check (T-029) and the JSON schema validator. Both run
every time, on every response, without anyone's attention — which is the
property a prompt clause cannot have. Leaving the clause in would suggest the
model's self-assessment is part of the guarantee. It is not, and stating that
plainly is the point of recording this as a choice rather than an omission.

#### The mechanism, added 2026-08-17 (OBS-114)

The rule above was an argument. It now has a mechanism, and the mechanism makes
it stronger than "self-marking is unreliable".

Observed in the MCP experiment: asked about an earlier statement, a model said
*"I did not explicitly say it was 'not established'"* — **it had, verbatim, one
message earlier.**

> **Asking a model to verify its own earlier claim measures nothing, because one
> side of the comparison is regenerated at check time.**
>
> Recall and generation are one operation. When a model "checks what it said",
> it does not retrieve the earlier claim — it produces a fresh one, conditioned
> on the same context that produced the first. A plausible reconstruction is
> indistinguishable, to the model, from the thing itself. So the check compares
> a claim against a *re-derivation of that claim*, which agrees with it for
> reasons that have nothing to do with whether either is true.

Same principle as the missing E, one level deeper. The Evaluation slot fails not
merely because a model is a lenient marker, but because **the artefact it is
asked to mark is not available to it** — only a regeneration of that artefact
is. A prompt clause cannot fix that, and neither can a better-worded one.

The practical rule for anything in this directory: **a prompt that references
earlier content must contain that content**, supplied from the record. Never
*"check your previous answer"*, always *"here is what was produced; check it"*.
`prompt_library` already works this way for the descent — `build_report_prompt`
takes a `DescentResult` rather than asking the model to recall one — and this is
the general form of why that shape is right.

**And it is not a rule about models.** The mechanism above is stated in terms of
generation because that is where it was first observed, but the failure is
wider: *anyone* reasoning about a prior exchange without the record in front of
them is reconstructing it, and a confident reconstruction is indistinguishable
from a memory. This was demonstrated twice in one session by the person who
directed the rule be written, an hour after writing it — which is the evidence
that knowing the rule does not protect against it. **The remedy has to be
mechanical: supply the record, never recall it.** The prompt case is one
instance of a general one.

---

## Rules

1. **Every prompt is a versioned file**, `name.vN.txt`.
2. **A prompt change is a version bump, never an in-place edit.** A prompt is
   an input to a system whose output someone acts on; silently changing one
   makes every earlier report unreproducible. `report.v2.txt` sits beside
   `report.v1.txt`; the caller names the version.
3. **Every prompt has at least one golden case** in `tests/cases/`, drawn from
   `tests/fixtures/` — never hand-written input.
4. **Golden tests assert shape and citation integrity, never prose.** Prose
   varies at any temperature; structure must not. A test that pins wording
   fails on a harmless rewording and passes on a fabricated citation, which is
   exactly backwards.

---

## What the report prompt must produce

Recorded here because it is a requirement on the prompt, not an implementation
detail of T-027.

**The report renders the CAUSAL CHAIN, not just the finding.** A descent
returns the lowest broken rung *and* the broken rungs above it, and the chain
is the product. A report that says `interface_line_down on PE2` without the
four rungs above it has thrown away what makes the answer trustworthy to an
engineer — it is an assertion where the chain is an argument.

Concretely, the measured `broken` descent must render as the second of these,
never the first:

> ❌ "BGP session to 10.255.0.12 is down. Cause: interface_line_down on PE2."

> ✅ "PE2's `Gi0/0/0/0` and `Gi0/0/0/1` are administratively down, so PE2 has
> no IS-IS adjacencies, so RR1 has no route to `10.255.0.12`, so the TCP
> transport cannot establish, so the BGP session is Idle."

The first restates the alert with a label attached. The second is an RCA, and
an engineer can check every link in it.

### How that maps onto the output schema

The observation/interpretation split already specified takes the chain
directly:

- **Every rung in the chain is an `observation`**, and carries the evidence key
  its `CheckResult` read. These are facts read off a device.
- **The lowest broken rung is the `interpretation`**, and it cites the
  observations above it. That is the causal claim, and it is the only part that
  is an inference.
- **The recommendation is the model's**, explicitly labelled, explicitly for a
  human, and exempt from citation.

Grounding (T-029) therefore covers the chain, not just the finding: an
uncited rung is an uncited claim, and the report is not emitted.

---

## What the model is not for

The descent already found the cause, deterministically, with no model call
anywhere in it. The model is not diagnosing — it is doing the three things a
deterministic walk cannot:

- **rendering** the chain as something an engineer absorbs in ten seconds;
- **correlating** the finding with the log timeline — the descent says the
  interface is down, the logs say *when*, how many times, and whether it
  followed a commit;
- **saying "I cannot determine this"** when the evidence genuinely does not
  support a conclusion, which is why every prompt names that refusal path
  explicitly rather than leaving the model to invent one.

If a prompt here ever starts asking the model to decide *what is broken*, the
architecture has leaked and the prompt is the wrong place to fix it.

---

## Version history

`prompt_library.CURRENT_VERSION` is the table deciding which version a builder
loads by default. Superseding a prompt means one line there and one row here —
the point of rule 2 is that the change is a diff someone reads, not a default
that drifted.

| Prompt | Current | History |
|---|---|---|
| `report` | **v2** | v1 (T-027) let the model write `recommendation.next_check` — a free-text guess at what to do next. B-490 (`MCP-EXPERIMENT.md` §11.2): a 4B model handed a `no_fault_on_path` report rewrote it as *"likely an application or configuration problem"* and proposed *"the service running on 10.255.0.12 is down"* — about a router loopback, which runs no service. `next_check` is a **closed field** now: the descent already computes it and the authoritative report already carries it, so v2 forbids the model from writing one at all (`"recommendation": {"requires_human": true}` and nothing else), and `grounding.check_recommendation_closed` fails `recommendation_not_closed` on any difference from `render.next_check_for(finding)`, quoting only the authoritative text. Mutation-verified (guard disabled → 2 tests fail). Golden cases rebound to v2 |
| `correlate` | **v4** | v1 (T-028) described a noise filter that dropped whole facilities. `log_window.py` was corrected to attribute each record before dropping it, which leaves unattributable — and often high-severity — session events in the window. v2 states why they are there and that **retention is not relevance**, and adds constraint 7: severity ranks how loudly a device reports something, not whether it bears on the finding. See OBS-063. v3 (T-029a) adds a COVERAGE slot and constraint 7 — *never state a negative more strongly than the coverage supports* — and moves the refusal marker to "no correlating events in the available coverage", because a negative over an incomplete source is an `unevaluated`, not a `no`. See OBS-068. v4 (B-467/B-470, DEEP-REVIEW-2026-08-17 §2.1) adds a GROUNDING paragraph naming the `<<<DEVICE-TEXT untrusted>>>`/`<<<END-DEVICE-TEXT>>>` markers `prompt_library.build_correlate_prompt` now wraps every record's `text` in, and constraint 9 telling the model that span is data, never instructions — every prior version embedded log text unmarked, which the deep review measured at 28 of 28 shaped records on the `broken` fixture |

Superseded versions stay in the tree. No report was ever produced from
`correlate.v1` or `v2`, so nothing was made unreproducible by superseding
them — the bumps were taken anyway, because carving the first exception to a
rule the day after writing it is how the rule stops meaning anything.

**Each version declares its own refusal marker**, in `tests/cases/*.cases.json`
under `refusal_markers`, keyed by filename. The wording is a property of a
version, not of a prompt family: v1 and v2 say "in window", v3 says "in the
available coverage", and each is right for its own era. Checking a superseded
version against the current wording would fail the historical record for being
historical. See OBS-069.
