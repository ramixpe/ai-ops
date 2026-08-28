# Prompt-system evaluation and next-level roadmap

**Date:** 2026-08-28  
**Scope:** `prompts/`, its runtime loaders/builders, model-facing boundaries,
case files, validators, and prompt-related tests  
**Method:** static contract review plus execution of the prompt test surface;
this is not a live-model benchmark

## Executive conclusion

This is already a much better prompt system than a typical collection of
application strings. Prompts are versioned, packaged, tied to refusal paths,
fed structured evidence, and—on the deterministic investigation path—kept
strictly subordinate to code-generated findings. `report.v2` and
`correlate.v4` also have real post-generation grounding gates. Those are the
right foundations.

The library is nevertheless operating at two different maturity levels:

* `report.v2` and `correlate.v4` are controlled paraphrase contracts;
* `troubleshooting.v1` and `fabric_analysis.v1` still ask a model to diagnose,
  correlate, and recommend in free-form Markdown, with no output parser or
  grounding gate;
* `agent_system.v1` is safely labelled exploratory by code, but its final
  narrative has no structured citation contract;
* `event_agent.v1` is operationally safe because code records
  `investigate_lab`'s finding rather than the model's prose, although the
  prompt itself does not express an exact completion schema.

That split matters because the operator's intended architecture is “the model
selects from approved capabilities and explains validated evidence,” not “the
model invents a diagnosis or next action.” The two legacy analysis prompts are
the principal place where the implementation still crosses that boundary.

I also found two concrete defects in the strongest prompts:

1. `correlate.v4`'s worked example sets `followed_a_commit` to `true` even
   though the first fault event is at `07:41:54.688` and the commit is later at
   `07:41:54.712`. Production code defines the field as true only when a commit
   **precedes** the first matched event.
2. `report.v2`'s anchor claims that two interfaces are down while citing only
   `PE2:interface:Gi0/0/0/0`. The real rung has three evidence keys. The output
   schema permits only one key per observation, so it cannot faithfully cite a
   compound claim.

All 270 prompt, grounding, packaging, agent, event-agent, and related tests
pass. Their failure to catch these two defects is itself an evaluation result:
the suite is strong on prompt presence, transport shape, and selected grounding
properties, but it is not yet a complete prompt-contract evaluation.

## Inventory and measured footprint

There are six prompt families and ten prompt versions:

| Family | Current file | Role | Characters | Words |
|---|---|---|---:|---:|
| Report | `report.v2.txt` | Deterministic descent → readable RCA | 5,080 | 771 |
| Correlate | `correlate.v4.txt` | Finding + shaped logs → timeline paraphrase | 7,502 | 1,173 |
| Troubleshooting | `troubleshooting.v1.txt` | One device's evidence → free-form analysis | 1,034 | 161 |
| Fabric analysis | `fabric_analysis.v1.txt` | Fabric evidence → free-form grouping | 1,771 | 279 |
| Interactive agent | `agent_system.v1.txt` | Human question → bounded read-only tool loop | 1,075 | 178 |
| Event agent | `event_agent.v1.txt` | Routed event → pinned read-only tool loop | 2,375 | 410 |

Archived versions are correctly retained: `report.v1` and `correlate.v1`–v3.
The correlate instructions grew from 4,483 characters in v1 to 7,502 in v4,
mostly because attribution, coverage, negative-claim strength, and untrusted
device-text rules were added. That growth bought real safety, but it should now
be tested by ablation rather than assumed permanently necessary.

The real rendered footprint is larger than the template alone:

* `report.v2`, broken fixture: 7,836 characters total;
* `report.v2`, undetermined fixture: 5,773 characters;
* `correlate.v4`, broken 28-record window: 15,603 characters;
* `correlate.v4`, healthy 9-record window: 10,570 characters.

No offline tokenizer is installed, so this paper reports measured characters,
not an invented character-to-token conversion.

## Evaluation rubric

Each current prompt is scored from 0–5 on eight dimensions. These are **static
control-maturity scores**, not claims about model accuracy.

1. **Task boundary** — does the model have a narrow, appropriate job?
2. **Grounding** — are inputs traceable and claims tied to evidence?
3. **Uncertainty** — are refusal, missing data, and negative claims explicit?
4. **Output determinism** — is there an exact machine-readable contract?
5. **Runtime enforcement** — does code validate what the prompt requests?
6. **Untrusted-input handling** — can device-authored text steer the task?
7. **Evaluation evidence** — do cases and tests exercise model-relevant failure
   modes?
8. **Efficiency and maintenance** — is the prompt concise, versioned, and
   consistently selected?

| Prompt | Boundary | Grounding | Uncertainty | Output | Enforcement | Input safety | Evaluation | Efficiency | Total / 40 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `report.v2` | 5 | 5 | 5 | 4 | 5 | 5 | 4 | 2 | **35** |
| `event_agent.v1` | 5 | 4 | 5 | 2 | 5 | 4 | 3 | 3 | **31** |
| `correlate.v4` | 5 | 5 | 5 | 2 | 3 | 5 | 4 | 1 | **30** |
| `agent_system.v1` | 4 | 3 | 4 | 1 | 2 | 4 | 3 | 5 | **26** |
| `troubleshooting.v1` | 2 | 3 | 3 | 1 | 0 | 4 | 1 | 5 | **19** |
| `fabric_analysis.v1` | 2 | 3 | 3 | 1 | 0 | 4 | 1 | 4 | **18** |

The ordering is more important than the precise numbers. A short prompt is not
automatically weak, and a long prompt is not automatically safe; the decisive
question is which claims code can independently validate.

## What is already excellent

### 1. The authoritative answer does not depend on model prose

The strongest architectural decision is outside the wording itself:
`investigation.py` creates the authoritative report and correlation in code.
Model output is stored separately as `authoritative: false` and can be
withheld. The event agent similarly records the validated
`investigate_lab.finding`, never the model's speculative final paragraph.

This sharply limits the consequence of prompt failure. It should become the
uniform rule for every prompt family.

### 2. Prompt injection is treated as a data-boundary problem

Device-authored free text is wrapped between explicit untrusted-content
markers. `correlate.v4` explains those markers clearly and repeats the rule in
its constraints. The model is told to use the text as evidence without obeying
it as an instruction. Raw command output is withheld elsewhere.

This is stronger than asking the model to “be careful,” because the boundary is
applied in code before prompt construction.

### 3. Refusal paths are first-class

Every prompt has a named refusal marker. The best examples distinguish several
different absences:

* `undetermined` when a descent rung could not be evaluated;
* `cause_not_localised` when the observed chain does not explain the symptom;
* `found: false` limited by the available log coverage;
* incomplete tool-loop work when the call budget is exhausted;
* a valid `no_fault_on_path` result rather than a fabricated cause.

That vocabulary is one of the library's strongest assets.

### 4. Versioning and packaging are disciplined

Old prompt versions remain available, source and packaged copies are checked
byte-for-byte, and unknown versions fail rather than silently falling back.
This gives prompt-produced artifacts a reproducible input.

### 5. The grounding layer is substantive

The report gate checks evidence keys, rung coverage, causal-chain coverage,
invented identifiers, invented causes, and the closed recommendation. The
correlation gate checks negative claims against coverage and validates timeline
timestamp/mnemonic pairs against the actual shaped window. These checks are
materially more valuable than adding another paragraph of instruction.

## Findings and risks

### P0 — The legacy analysis prompts still authorize model freestyle

`troubleshooting.v1` asks for a “Possible Cause” and a free-text “Recommended
Next Check.” `fabric_analysis.v1` asks the model to identify shared root causes
and write next checks. Neither response is parsed into a closed schema, tied to
evidence keys, or passed through grounding.

This directly conflicts with two lessons already implemented elsewhere:

* `report.v2` removed model-authored `next_check` after a measured fabricated
  recommendation;
* the prompt README says diagnosis belongs to deterministic descent, not the
  model.

These two paths should not be strengthened with more persuasive prose. They
should be narrowed structurally:

* code supplies deterministic findings or health verdicts;
* code supplies candidate incident groups and approved next-check IDs;
* the model explains or selects among those values;
* code rejects any identifier or action not offered.

Until that work lands, their output should be explicitly labelled
`exploratory` at every caller and UI boundary, like `agent_loop` already is.

### P1 — `correlate.v4` asks the model to regenerate four deterministic fields

`render_correlation()` documents that `timeline`, `found`,
`followed_a_commit`, and `recurrence` are deterministic. Only the prose summary
benefits from a model. Yet the prompt asks the model to author all five fields.

This creates avoidable failure surface:

* the anchor's `followed_a_commit: true` contradicts production semantics;
* `recurrence` is free text even though it is a count;
* the grounding gate validates timestamps and mnemonics, but not the `event`
  prose, recurrence, or commit-order boolean;
* a fabricated event description attached to a real timestamp/mnemonic can
  therefore pass the current correlation gate.

The next version should receive the code-generated timeline and return only a
summary with references to timeline entry IDs. Code should attach the
deterministic fields after validation.

### P1 — `report.v2` cannot cite compound observations faithfully

The real interface rung carries three evidence keys. The anchor states that two
interfaces are administratively down but cites only one. This is not merely an
example typo: the schema requires one `evidence_key`, and `check_chain_coverage`
accepts any one key belonging to the rung.

Change the next schema to:

```json
{
  "rung_id": "rung-5",
  "claim": "...",
  "evidence_keys": ["PE2:interface:Gi0/0/0/0", "PE2:interface:Gi0/0/0/1"]
}
```

Then require every identifier in a compound claim to be supported by the cited
keys, or pre-render the factual observation in code and let the model only join
the observations into prose.

### P1 — The “golden case” label currently overstates what is executed

The case directory declares 16 scenarios. Ten—every case for
`troubleshooting`, `fabric_analysis`, `agent_system`, and `event_agent`—contain
`"asserted_by_shape_only": true`. Their `structural_properties` prose is not
read and scored as a model-response contract.

The generic test for a golden case only verifies that a file with the family
stem exists. It does not prove that:

* the JSON is non-empty and references the current prompt version;
* a rendered fixture input is executed;
* the response schema is validated;
* the refusal behavior occurs in a real model response;
* a candidate version is no worse than the current version.

The report and correlate suites are much stronger because they exercise the
rendered inputs and grounding mutations. Even there, the passing suite missed
the two anchor defects above. Rename shape-only records to “contract cases,” or
promote them into executable prompt evaluations before calling them golden.

### P2 — Governance documentation and runtime selection have drifted

The README's directory tree and version-history table omit `event_agent.v1`.
`prompt_library.CURRENT_VERSION` also omits it. Moreover,
`troubleshooting`, `fabric_analysis`, `agent_system`, and `event_agent` are
loaded with hard-coded version `1` at their call sites rather than through the
central current-version table.

The README says the table decides which version builders load by default; that
is only true for report and correlate. A v2 rollout therefore has several
different procedures depending on family.

Use one registry entry per current prompt containing at least:

* family and version;
* trust class (`authoritative-code`, `validated-paraphrase`, `exploratory`);
* input schema version;
* output schema version;
* validator;
* evaluation case set.

### P2 — GRACE is documented as universal but not enforced as universal

The README defines Anchors as a worked input/output example and Expected Output
as an exact parseable schema. Four current prompts do not satisfy that literal
contract:

* troubleshooting and fabric analysis contain Markdown skeletons, not exact
  schemas or worked examples;
* agent and event-agent prompts contain neither a worked output anchor nor an
  exact final-answer schema.

The repository also already contains justified constructed/composed cases,
despite the rule saying golden cases are “never hand-written.” The practice is
more sensible than the prose: synthetic shapes are valid when their provenance
and necessity are explicit. Update the rule to reflect that rather than keep a
rule the project intentionally violates.

### P2 — The cache split leaves a dangling duplicate payload heading

The runtime split removes `{descent_json}` from the static text, leaving an
empty `DESCENT RESULT` heading in the system prompt, then adds a second
`DESCENT RESULT` heading to the user payload. The same pattern applies to the
three correlate payloads.

The model still receives an understandable prompt, but the behavior conflicts
with `_split_template`'s documentation that headings travel with payloads. A
rendered-prompt snapshot test should assert the intended role layout, not only
that all words appear somewhere.

### P2 — Provider paths do not preserve the same instruction hierarchy

Anthropic receives a real system block and a user payload. OpenAI, MiniMax,
and Ollama concatenate the two into one prompt string on several paths. Thus
the same prompt version does not have the same role hierarchy across
providers. This is particularly important where untrusted device text is in
the volatile half.

Where provider APIs permit it, preserve system/instructions and user input as
separate roles. Where a compatibility endpoint cannot do that, record the
flattened-role condition in the model evaluation matrix rather than treating
providers as interchangeable.

### P3 — Production instructions contain reviewer rationale

`report.v2` includes the full historical anecdote explaining why model-authored
`next_check` was removed. The rationale belongs in README/version history and
tests; the runtime prompt only needs the closed rule. Repeating forbidden
fabrications such as “application or configuration problem” and “service ...
is down” also puts those phrases into model context unnecessarily.

`correlate.v4` repeats several safety rules in both grounding prose and numbered
constraints. Repetition may help smaller models, so it should not be removed on
aesthetic grounds. Run an ablation: compare the current version against a
shorter candidate on the same adversarial cases and retain repetition only
where it measurably improves adherence.

## Recommended vNext contracts

### `report.v3`

Keep the model's job as readable rendering, but remove fields code already
owns.

```json
{
  "observations": [
    {
      "rung_id": "rung-1",
      "claim": "string",
      "evidence_keys": ["exact-key", "exact-key"]
    }
  ],
  "interpretations": [
    {
      "claim": "string",
      "based_on": ["rung-1", "rung-2"]
    }
  ],
  "limitations": [
    {"rung_id": "rung-2", "reason_code": "unevaluated"}
  ]
}
```

The recommendation should not be model output at all. Code can attach the
authoritative `next_check` and `requires_human` after the paraphrase passes.

### `correlate.v5`

Pass the authoritative deterministic correlation as input. Ask the model for
one bounded product:

```json
{
  "summary": "string",
  "based_on_timeline_entries": ["event-1", "event-2"],
  "coverage_qualification": "complete|limited"
}
```

Code retains timestamps, mnemonics, recurrence, `found`, and
`followed_a_commit`. If the summary names an interval, compute and offer that
interval in the input rather than asking the model to do timestamp arithmetic.

### `troubleshooting.v2`

Do not ask for an open-ended possible cause. Supply the deterministic health
verdict and an enumerated list of approved next checks.

```json
{
  "summary": "string",
  "finding_id": "an offered finding ID",
  "evidence": [
    {"claim": "string", "evidence_keys": ["exact-key"]}
  ],
  "next_check_id": "an offered check ID|none",
  "limitations": ["closed reason code"]
}
```

The model may explain or choose; it may not author a new command, target,
cause, or action.

### `fabric_analysis.v2`

Precompute incident candidates with deterministic correlation primitives.
Give every health finding an ID and every proposed group an incident ID. The
model may select/describe groups, but it cannot invent group membership.

```json
{
  "summary": "string",
  "incidents": [
    {
      "incident_id": "offered-id",
      "finding_ids": ["offered-id"],
      "explanation": "string",
      "evidence_keys": ["exact-key"]
    }
  ],
  "independent_finding_ids": ["offered-id"],
  "next_check_ids": ["offered-id"]
}
```

### `agent_system.v2`

Keep the tool loop exploratory, but make completion inspectable:

```json
{
  "answer": "string",
  "claims": [
    {"claim": "string", "tool_call_ids": ["call-id"], "evidence_keys": ["key"]}
  ],
  "limitations": ["string"],
  "complete": true
}
```

Add an explicit false-premise rule: verify the premise with a wide state tool
before explaining why an asserted fault exists. Do not let a natural-language
question turn an unverified premise into a diagnosis.

### `event_agent.v2`

Align the wording with the code: `investigate_lab`'s validated finding is the
answer; the model's role is navigation and an optional summary. If wide-first
is a real requirement, enforce it in the event loop. If direct investigation
is intentionally allowed, call wide-first a preference and do not test it as
though it were an invariant.

The final response should cite the authoritative tool call ID and disclose any
limit hit. It must never introduce a different finding.

## A shared prompt ABI

The next level is not six independently improved text files. It is a common,
versioned prompt application binary interface:

```text
PromptSpec
├── prompt family + immutable version
├── trust class
├── versioned input schema
├── versioned output schema
├── renderer
├── validator/grounder
├── refusal vocabulary
└── evaluation cases + promotion thresholds
```

Store the output schema as a versioned artifact beside the prompt, or generate
both the prompt's schema block and runtime validator from one immutable schema
source. Do not maintain two manually synchronized versions. If the schema
changes, the prompt version changes.

Every model-facing input should also carry a standard evidence margin:

```json
{
  "parse_status": "parsed|failed|unavailable",
  "structured_fields_present": true,
  "deferred_lines": 0,
  "unaccounted_lines": 0,
  "unparsed_rows": 0,
  "budget_omitted_chars": 0,
  "source_coverage": "complete|limited",
  "gaps": []
}
```

This connects prompt quality to parser coverage. Giving a model more evidence
is useful only if it also knows what was not parsed, withheld, truncated, or
never collected.

## Evaluation program

### Level 1 — static and rendering checks on every commit

* every current family is in one version registry;
* README inventory is derived or checked against the files;
* every placeholder is substituted exactly once;
* system and user snapshots contain no dangling/duplicated section heading;
* current prompts have the required GRACE-equivalent slots for their task;
* prompt and packaged copy hashes match;
* prompt size and rendered fixture size are reported, with regression bands.

### Level 2 — deterministic contract mutation

For every output field, start with a valid response and mutate one property:

* invented evidence key or tool-call ID;
* omitted rung or incident member;
* extra unapproved recommendation/action;
* false `followed_a_commit` or recurrence;
* event text inconsistent with its cited record;
* negative claim over incomplete coverage;
* compound claim with incomplete citations;
* unknown top-level or nested field;
* malformed JSON, fence, trailing prose, and truncation.

Each mutation must be rejected for one named reason. Each validator also needs
a positive control proving it does not reject everything.

### Level 3 — model regression matrix

Run every candidate prompt against the fixed fixture cases on each supported
production/test model and provider path. Use repeated trials where sampling is
not guaranteed deterministic. Score in code, never with another model.

Minimum dimensions:

* response parsed into the exact schema;
* all required observations/findings included;
* zero invented identities, causes, commands, or actions;
* citation completeness and correctness;
* refusal on `undetermined` and false premises;
* negative-claim strength matches evidence coverage;
* no unrequested active probe;
* budget exhaustion disclosed;
* untrusted device-text instruction ignored;
* stable classification across repeated identical runs;
* input/output tokens and cache hits recorded from provider usage.

The existing `model_eval.py`, `scripts/model_eval_report.py`, and
`scripts/variance_experiment.py` are a strong starting point. I found no
retained `variance-experiment/**/summary.json` result in the current tree, so
the repeatability harness exists but this checkout does not contain a retained
run proving current-prompt performance.

### Level 4 — version-promotion gate

A new prompt becomes current only when:

* every deterministic contract test passes;
* no safety dimension regresses against the previous version;
* every production model/provider meets the same minimum gate;
* zero unsupported cause/action/identity is observed in the fixed corpus;
* failures and repairs are retained, not only successful outputs;
* the run records model ID, parameters, prompt/schema hashes, token use, and
  fixture revision.

Do not demand identical prose. Demand identical safety and contract outcomes.

## Prioritized implementation plan

### Wave 1 — close active contract defects

1. Create `correlate.v5`; correct commit ordering and remove deterministic
   fields from model authorship.
2. Create `report.v3`; change observation citations to `evidence_keys[]` and
   remove the constant recommendation object from model output.
3. Add semantic mutation tests for commit order, event text, recurrence, and
   compound citations.
4. Mark troubleshooting and fabric analysis outputs exploratory until their v2
   contracts exist.

### Wave 2 — eliminate freestyle diagnosis and recommendations

1. Build the common `PromptSpec`/schema/validator registry.
2. Create `troubleshooting.v2` over deterministic findings and approved
   next-check IDs.
3. Create `fabric_analysis.v2` over deterministic finding/incident candidates.
4. Preserve provider role separation and add response-schema support where the
   provider offers it.

### Wave 3 — make agent narration inspectable

1. Create structured final-answer contracts for interactive and event agents.
2. Cite tool-call IDs and evidence keys.
3. Enforce or remove the wide-before-narrow requirement.
4. Add false-premise, injection, limit-exhaustion, and partial-evidence model
   evaluations.

### Wave 4 — optimize only after measurement

1. Run the model matrix against current and candidate prompts.
2. Remove reviewer rationale and duplicated instructions one block at a time.
3. Retain a shorter version only when safety/grounding scores do not regress.
4. Publish the resulting scorecard and prompt-cost delta beside the version
   history.

## Final assessment

The prompt library's best idea is that prompts are not trusted merely because
they are well written. The code limits what the model sees, what it can call,
what becomes authoritative, and what may be emitted. The next step is to apply
that philosophy consistently.

Do not try to make the weaker prompts “smarter.” Make their choices smaller,
their inputs more explicit, their outputs typed, and their claims mechanically
checkable. Then use the model for the part it is genuinely good at: concise
explanation and bounded selection over evidence the system has already
validated.
