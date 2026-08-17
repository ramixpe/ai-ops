# BUILD PLAN — Deterministic Investigation Layer, MVP-0

**Repository:** `ios-xr-nettools`
**Executor:** Claude Code
**Baseline:** Phase 8 complete — 23 modules, ~4,900 source lines, 554 tests green
**Goal of this plan:** ship MVP-0 — a deterministic dependency descent that finds the broken protocol layer, plus a grounded RCA written by a model that never touches the diagnosis.

---

# PART 0 — RULES OF ENGAGEMENT

Read this part completely before starting T-001. It governs every task below.

## 0.1 The plan is strictly sequential

Execute tasks in the order given. **Do not skip ahead, do not batch, do not reorder.** A task may only start when the preceding task is marked `DONE` or `BLOCKED-ACCEPTED`.

If a task looks unnecessary because of something you discovered in an earlier task, do not silently skip it. Record an observation saying why, mark it `SKIPPED` with the reason, and continue.

## 0.2 Every task ends with three actions

1. Set the task's status in this file: `DONE`, `BLOCKED`, `SKIPPED`, or `PARTIAL`.
2. Append at least one entry to `docs/build/FINDINGS.md`.
3. Run `make test` and `make lint`. **Never leave the tree red.** If a task cannot be completed without breaking tests, mark it `BLOCKED` and stop.

## 0.3 The observations log book

`docs/build/FINDINGS.md` is the single record of everything learned during the build. It is not a changelog — git already does that. It is where surprises, wrong assumptions, deferred decisions, and things that smell wrong are written down so they can be reviewed together at the end.

**Write an entry whenever any of these is true:**

- Something in the plan turned out to be wrong, incomplete, or based on a false assumption.
- Real device or fixture output did not match what the plan predicted.
- A decision had to be made that the plan did not specify.
- Something was implemented in a way you are not confident about.
- A test was written that passes but that you suspect does not really test the thing.
- You noticed a defect, smell, or risk outside the current task's scope. **Do not fix it. Log it.**
- A dependency, version, or environment detail differed from expectation.
- You wanted to change a file listed in §0.5 as frozen.

**Entry format** — one block per observation, appended in chronological order:

```markdown
## OBS-nnn · T-xxx · <short title>

- **Kind:** assumption-wrong | surprise | decision-made | risk | defect | deferred | environment
- **What happened:** <2-4 sentences, factual>
- **Evidence:** <file path, command output, test name, or line reference>
- **What I did:** <action taken, or "nothing — logged only">
- **Needs human review:** yes | no
- **Blocks:** <task IDs this affects, or "none">
```

Number entries sequentially from `OBS-001`. Never edit or delete an earlier entry; if it turns out to be wrong, write a new one that corrects it and reference the old ID.

## 0.4 Blockers — stop, do not guess

When a task cannot proceed because a decision belongs to the human:

1. Mark the task `BLOCKED`.
2. Write an observation with `Needs human review: yes`.
3. Add a line to the **Open Questions** table at the bottom of `FINDINGS.md`.
4. **Stop the plan.** Do not continue to the next task.

Guessing at an operator's intent is worse than stopping. This is a network tool.

The exception: tasks explicitly marked `[NON-BLOCKING]` may record a blocker and continue.

**This section is refined by §0.11.** Read the escalation ladder there before applying it — not every ambiguity is a HALT, and treating them all as one would stall an autonomous run on choices that are safe to make.

## 0.5 Frozen files — the safety boundary

These encode the safety invariant. **Do not modify them.** If a change appears necessary, that is a design error — stop and log a blocker.

```
tests/test_safety.py
tests/test_template_security.py
src/agent_nettools/templates.py          (additions only; never relax a validator)
src/agent_nettools/platforms.py          (additions only; never widen an allowlist rule)
```

Specifically, these tests must pass unchanged at every commit:

- `test_refuses_unapproved_commands_before_loading_credentials`
- `test_refuses_another_platforms_command_without_credentials`
- `test_commands_do_not_leak_across_platforms`
- `test_no_generic_run_command_is_exposed`
- `test_unknown_platform_approves_nothing`
- every test in `tests/test_template_security.py`

## 0.6 The four invariants new code inherits

1. Platform resolves through `lab.platform_for()` — static inventory data, **no credentials**.
2. The command allowlist is checked **before** credentials load or a socket opens.
3. No command string is ever built by interpolation. Parameterised commands go through `templates.render_command` — canonicalize by reconstruction, never pass-through.
4. **No unparsed device text is ever passed to a model.** Model input is parsed records, verdicts, and diffs. Raw text reaches a model only via `evidence_budget`'s existing parse-failed fallback.

## 0.7 Git discipline

- One commit per task, message prefixed with the task ID: `T-014: add bgp_neighbor TTP parser`.
- Branch: `feat/investigation-layer`. Do not merge to main during this plan.
- Never commit a secret. `MINIMAX_API_KEY` lives in `.env`, which is gitignored.
- Never commit fixture output containing credentials, keys, or customer-identifying data.

## 0.8 Required reading before T-001

In this order:

| File | Why |
|---|---|
| `CLAUDE.md` | Existing architecture, layers 0–5, the safety boundary |
| `docs/design/design-thinking.md` | Decisions D1–D20 with rationale and growth paths |
| `docs/design/lld-investigation-layer.md` | The delta specification this plan executes |
| `README.md` | CLI surface, env vars, fixtures |
| `src/agent_nettools/platforms.py` | The allowlist and intent table |
| `src/agent_nettools/parsers.py` | The parser contract new parsers must match |
| `src/agent_nettools/health.py` (docstring) | The `unevaluated` discipline |
| `docs/design/interfaces.md` | Only before T-035. The interface ladder and why MVP-0 ships no chat |

Do not start building until these are read. Log an observation if any of them contradicts this plan.

---

## 0.9 Model delegation policy

Three models, three distinct jobs. The separation is not about capability — it is about **who is allowed to decide**.

| Model | Role | Owns |
|---|---|---|
| **Claude Opus 5** | Brain and orchestrator | Reading and sequencing this plan. Every decision. Acceptance judgement. Writing `FINDINGS.md`. Updating `TRACKER.md`. Accepting or rejecting work from the other two. |
| **Claude Sonnet 5** | Heavy lifting | Bulk implementation: parsers, tests, refactors, fixture capture, running suites, mechanical edits across many files. |
| **Claude Fable 5** | Consulting | Second opinion on a hard call. Adversarial review of a safety-relevant decision. Unblocking a stuck design question. |

**Rules, in order of importance:**

1. **Opus 5 owns the plan and never delegates a decision — only work.** A task's acceptance criteria are judged by Opus 5, not by whichever model wrote the code.
2. **Sonnet 5 receives a precise specification and returns code plus tests.** It does not amend the plan, does not choose between design options, and does not decide what "good enough" means. If a spec handed to Sonnet 5 turns out to be ambiguous, that is Opus 5's defect to fix — log it as `assumption-wrong`.
3. **Fable 5 is consulted, never commanded.** Its output is advice, recorded in `FINDINGS.md` as `kind: decision-made` with the question asked and the answer received. It is applied only when Opus 5 explicitly accepts it. Never paste Fable 5 output directly into the repository.
4. **No model may modify a file frozen by §0.5.** If Sonnet 5 proposes a change to `tests/test_safety.py` or a relaxation in `templates.py`, Opus 5 refuses it and logs a `risk` finding.
5. **Every consultation is logged.** Which model, what was asked, what came back, what was done with it. An unlogged consultation is an unreviewable decision.

**Suggested allocation by task:**

| Tasks | Primary | Notes |
|---|---|---|
| T-001 to T-009 (Discovery) | Opus 5 | Judgement-heavy, low volume. Discovery findings shape everything downstream. |
| T-010, T-019, T-022, T-024 (contracts and semantics) | Opus 5 | These define shapes other work depends on. Get them right, not fast. |
| T-011 to T-018 (parsers, capture) | Sonnet 5 | High volume, precisely specifiable, heavily tested. |
| T-020, T-021, T-023 (checks, flow definitions) | Sonnet 5 | Spec is tight; Opus 5 reviews each against the fixtures. |
| T-025 (acceptance test) | Opus 5 | This is the milestone that validates the architecture. Judge it directly. |
| T-026 to T-029 (prompts, grounding) | Opus 5 drafts, Sonnet 5 tests | Prompt wording is a design artifact; test harness is volume work. |
| T-030 to T-034 (runner, CLI, docs) | Sonnet 5 | Wiring against settled contracts. |

**When to consult Fable 5.** Sparingly, and always on a *decision* rather than a task: a safety-boundary judgement, a disagreement between the plan and what the fixtures actually show, or any finding marked `Needs human review: yes` where a second read would sharpen the question before it reaches the human.

---

### 0.9a Concurrent tracks — added 2026-08-16, post-M4

§0.9 was written for one plan running on one branch. Work now runs on **three branches, one agent each** (`BACKLOG.md` → *What happens next*). Everything above holds unchanged; four things are added because concurrency creates failure modes a single track cannot have.

1. **Opus 5 judges acceptance on every track.** Not per branch, not delegated to whichever agent owns the track. Three tracks do not mean three judgements — they mean one judge and three queues.

2. **A change to a shared contract is a HALT on that track, not a decision.** It is escalated to Opus 5 and coordinated across tracks before anything is written. This is deliberately stricter than §0.11's ladder, which would otherwise class most contract edits as DECIDE-AND-LOG: a defensible choice made independently on two branches produces two defensible, incompatible contracts, and the incompatibility surfaces at merge, when both are finished and both authors are confident.

   The contracts are **enumerated by name** in `BACKLOG.md`, because a HALT rule nobody can apply is not a rule.

3. **One agent per track, never two.** A track is a unit of ownership, not a queue that parallelises.

4. **A feeds-into relationship is not a coupling.** Where one track's output will *probably* be consumed by another's — episodes (B-416) feeding forward consistency (B-428) is the live case — the producing track builds it **on its own merit**, against its own acceptance criteria, and the consuming track decides later whether to take it.

   Coupling them feels efficient and is the exact shape rule 2 exists to prevent: it puts two agents in one contract with a shared, unwritten assumption about what the other is doing. **If B-416's episodes turn out to be worth nothing to B-428, B-416 must still stand on correlation quality alone** — and if it cannot, it was not worth building.

**Merge order is part of the design, not scheduling.** A first, because it is the critical path and owns most of the shared contracts. C last, because it lives outside the repository and touches none of them.

---

## 0.10 TTP parsing must cover the complete output

**Requirement.** Every TTP template must account for the *entire* command output. Not the fields the current caller happens to need — everything.

**Why.** A template that extracts three fields and ignores the rest fails silently when the vendor adds a fourth. It also makes the parser's coverage invisible: nobody can tell by reading it whether a missing value means the device did not report it or the template did not look. Both failure modes end with a model reasoning over evidence it believes is complete and is not.

**The mechanism — line accounting.** For any command output, every non-blank line must be exactly one of:

1. matched by a template group and turned into a record or a `meta` field; or
2. matched by a **declared** ignore rule — the IOS-XR timestamp banner, a blank separator, a known decorative header.

Anything else is *unaccounted*, and unaccounted lines are surfaced, never dropped:

```python
{
  "meta": {
    ...,
    "unaccounted_lines": [],      # MUST be empty for every committed fixture
    "unparsed_rows": 0            # existing convention: malformed rows that should have matched
  },
  "records": [...]
}
```

`unaccounted_lines` and `unparsed_rows` are different failures and must stay separate. The first means "the template does not know what this line is." The second means "the template knows what this line should be and it did not fit."

**The test, required for every parser:**

```
For every committed fixture for this (platform, template):
    parsed = parse(raw)
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
```

**Ignore rules must be declared, not implicit.** A regex that quietly swallows unrecognised lines defeats the whole mechanism. Keep ignore patterns in one named constant per template with a comment explaining what each one is, so a reviewer can see the full accounting in one place.

**What this buys.** When XRd is upgraded and `show bgp neighbor` gains a field, the test fails loudly on the next fixture recapture instead of the field vanishing from evidence with nobody noticing. That is the same reasoning as `health.py`'s `unevaluated` discipline, applied one layer down.

**Applies to.** All new TTP templates (T-012 to T-017). It does **not** apply retroactively to the six hand-written parsers in `parsers.py` — do not rewrite working, tested code. Log a finding noting the inconsistency so it can be scheduled deliberately.

---

## 0.11 Autonomous operation, and the escalation ladder

This plan is executed autonomously. That does not mean unsupervised judgement on anything that matters — it means **decide where it is safe to decide, halt where it is not.**

Three levels. Every ambiguity resolves to exactly one of them.

### HALT — stop the plan and wait for the human

Only these. They are absolute.

- Anything touching the safety boundary in §0.5 or the four invariants in §0.6.
- Anything that would write to, configure, or change the state of a network device.
- Anything involving credentials, keys, or secrets beyond reading a documented environment variable.
- A baseline test failure that cannot be traced to the current task.
- A fixture or capture that would commit sensitive data.
- `T-021`'s agreement test failing — a genuine disagreement between `checks.py` and `health.py` is a design finding, not a test to loosen.

A tool's report of failure is not evidence of failure. Before acting on either a success or a failure report from anything that touched a device, verify the device's actual state directly and cross-check against a second source. Never retry a non-idempotent action on the basis of a failure report alone — re-applying a change to a device that is already correct is itself the harm this section exists to prevent.

**This has now happened three times, the third time inside the function written to prevent it (OBS-075, B-412).** The T-033 fault-injection harness was built around exactly the rule above — *verify by reading, never trust the write* — and on an exception it retried three times without reading the device once. The restore had succeeded on the first attempt, 252 ms after the fault. The script raised a manual-intervention alarm on a healthy fabric. Two consequences of that, both binding:

- **The verification must run on every path, including the error path.** A verification reachable only on the success path verifies nothing in the one case it exists for. This is not §0.12's vacuous guardrail — the check was correct and would have passed; it was simply never reached.
- **Never wrap your own logic in the broad `except Exception` used at the device boundary.** The exception that triggered all this was a `TypeError` — a code defect — swallowed and rendered as a network failure. `CLAUDE.md` documents broad catching as the established idiom at the SSH boundary, and it is right *there*, around the call and nothing else. Widened by one line past it, **it converts your own bugs into false reports about the network**, which is the most expensive kind of wrong this project can produce.

The independent check that caught it was a human watching the device console. **Nothing inside the tool would have** — which is the standing argument for a witness outside the thing being verified, and the same reasoning as §0.13's setup face.

### The agent never runs a fault injector

**Binding, and for two independent reasons.** The second is the one that is easy to forget, because it is not a safety rule.

1. **An injector writes to devices.** §0.11 applies unchanged and is not waived by a drill being planned, approved, or reversible.
2. **Running it would put the fault identity in the agent's context, destroying the blinding the trial depends on.** That is a different kind of prohibition: not *"this action is dangerous"* but **"performing this action makes me a worse witness."**

The distinction matters because the two failure modes look nothing alike. A §0.11 violation damages the fabric and is visible. This one damages only the *evidence*, and it is **silent** — a contaminated trial produces exactly the same confident agreement a clean one does, and neither the agent nor the reader can tell them apart afterwards. Q-006 is worth something only because OBS-076 was written and committed before OBS-077 existed.

Generalised, because it will recur wherever the agent is both actor and assessor:

> **Some actions are forbidden not because they are unsafe but because taking them makes you unable to judge the result. Where you are both the actor and the assessor, refraining is part of the method, not caution about it.**

The injector is operated by a human or by a separate process (**B-426**).

On HALT: mark the task `BLOCKED`, write a finding with `Needs human review: yes`, add a row to the Open Questions table in `FINDINGS.md`, update `TRACKER.md`, and **stop**.

### DECIDE-AND-LOG — choose, record, continue

Technical choices with a defensible default and no safety consequence. Make the call, write it down, keep moving.

Examples: an error-counter threshold, a fixture filename convention, whether a `meta` field is a string or an int, which of two TTP structures to use, how to name an internal helper.

On DECIDE-AND-LOG: implement the choice, write a finding with `kind: decision-made` stating the options considered and why this one, set `Needs human review: yes` if it is load-bearing, and continue.

### NOTE — record, continue

Observations outside the current task's scope. A smell in existing code, a docstring that is now wrong, a test that passes but looks weak, a dependency version worth watching.

On NOTE: **do not fix it.** Write the finding, continue. Out-of-scope fixes are how a sequential plan turns into an unreviewable diff.

## 0.12 Guardrails must not pass vacuously

**A guardrail that can pass by measuring nothing needs a companion test that fails the moment the empty set ends. A test that passes vacuously converts an unverified property into a green tick — the same silent-degradation failure this build has hit three times.**

The shape to watch for is a test that iterates a collection, a registry, a fixture glob or a parametrised set that is currently empty or currently uniform. pytest reports "empty parameter set" as a skip and moves on; an `all()` over nothing is `True`; a comparison across a corpus that only contains one verdict compares nothing.

Three instances so far, each caught only because something else was watching:

- **T-010 → T-012.** The template-parser registry was empty by design. `test_registry_is_empty_until_the_parsers_land` failed the moment the first parser landed, which was the signal to replace it with the real expectation.
- **T-021.** The `checks`/`health` agreement test passed on its first run with 36 green comparisons — because credentials were absent, every check returned `unevaluated`, and every assertion was trivially satisfied. Caught by an anti-vacuity test in the same file asserting the corpus actually produces both `healthy` and `broken`.
- **T-026.** Two prompt-rule tests skip with no prompts to run against.

The companion takes one of two forms: assert the collection is currently empty (so it fails when populated), or assert the corpus exercises every outcome the test discriminates between. Either is cheap. Neither is optional on a test whose whole job is to catch a regression that has not happened yet.

---

## 0.13 Evidence bounds conclusion, and the bound is invisible from inside

> **Evidence bounds conclusion, and the bound is invisible from inside.**
>
> - a survey is a sample — *(data)*
> - a rule generalised from one instance fits one instance — *(rules)*
> - a test sharing the implementation's premise confirms it — *(tests)*
> - a corpus shows width only in dimensions where it varies — *(identity)*
> - a demo verified in the developer's environment verifies the environment — *(setup)*
>
> **Before trusting any of the five, ask what the evidence could not have shown you.**

One family, five faces. Each has cost this build real time, and in every case the artefact was internally consistent — which is why the question has to be asked deliberately rather than noticed.

**The buffer-level trap is the same family.** `show logging` returns the device *buffer* (level `debugging`, severities 0–7); the *trap* level governs what is shipped to the collector (`informational`, 0–6). Reading the trap level to describe local coverage **understates the source by exactly the class in question**, while looking entirely correct. The evidence — a header line stating a level — cannot show you that you read the wrong line.

### The four faces, with their standing examples

| Face | Standing example | Fix |
|---|---|---|
| **Data** — a survey is a sample | T-013: 44 route fixtures grouped by line 4, which is identical across all three route shapes. A directly-connected route has no next hop at all | Survey for the *shape*, then let §0.10 line accounting fail the spec that missed one |
| **Rules** — one instance fits one instance | T-026: "every prompt names its refusal path as `undetermined`" — `report`'s refusal, not `correlate`'s | Move the value into data the instance declares |
| **Tests** — a shared premise confirms itself | T-028: **sixteen green tests** over a filter deleting eight severity-3 records unattributed. The suite and the defect shared an author and a premise | Specify independently of the code, and read the specification against the implementation |
| **Identity** — width only where the corpus varies | T-029a: `refusal_marker` moved into data and keyed by *prompt family*, then failed again when a second **version** appeared | Key by the finest identity the thing has — a filename, not a family name |
| **Setup** — the environment verifies itself | T-031: `--from-fixtures` was checked by hand a dozen times and "needed no credentials", because `main()` loads this repo's `.env` and it holds real ones. The verification environment was contaminated by the thing being verified | Strip the environment in a test, and in CI. A person cannot easily un-know their own `.env`; a clean container can (**B-423**) |

The **setup** face is the one most likely to be dismissed as an operations detail. It is not: it is the only face where the contaminating evidence is *outside the repository*, so no amount of reading the code or the tests reveals it. The fix has to be an environment, not an inspection.

The **tests** face deserves the extra sentence, because it is the one that cannot be caught by looking harder at the artefact: *green tests are not by themselves evidence that a component is correct — only that it agrees with the assumption it was built on.* Where a component encodes a judgement about the world, specify it independently.

Two practical consequences:

- **When a design document arrives for code that already exists, read the document against the code.** The other direction finds nothing — every line of code justifies itself, and the reading converges on "yes, that is what it does".
- **Do not treat a passing suite as acceptance for a component that encodes a judgement.** Parsers, filters, checks and thresholds all make a claim about the world.

### The eight silent-failure shapes

A different taxonomy, and worth keeping beside the family: these are the *symptoms*, §0.13 is a *cause* several of them share. Shapes 1–6 are green things that verify nothing. Shape 7 verifies correctly and **under-reports**. Shape 8 is a green suite that **stopped verifying something it used to**.

| # | Shape | Instances |
|---|---|---|
| 1 | Green flag over a degraded read | OBS-006, OBS-043, OBS-044 |
| 2 | Absence read as a healthy value | OBS-044, OBS-051 — answered by `unevaluated` |
| 3 | A guardrail passing over an empty set | §0.12 |
| 4 | A rule generalised from one instance | OBS-021, OBS-062, OBS-063, OBS-069 |
| 5 | A test agreeing with the code by construction | OBS-064 |
| 6 | **Wrong evidence read as right evidence** | OBS-071, OBS-072, OBS-089, and the buffer/trap trap |
| 7 | **Evidence collected, parsed, carried, and never read** | OBS-092 — see below |
| 8 | **A fix silently deletes coverage of behaviour that was always correct** | OBS-097 — see below |

**Shape 6 is not a variant of the others.** Shapes 1–5 are all *absence* presented as presence: something was not measured and the gap is invisible. Shape 6 is *presence of the wrong thing* — the evidence is real, correctly read, internally consistent, and **answers a different question than the one being asked of it.**

> **Shape 6 is a property of inference from partial evidence, not a property of filtered sources. Tools have nothing to do with it.**

That correction is worth the space, because the shape was first filed from a log-platform instance and reads as a data-pipeline concern. It is not. Four instances, only one of which involves a filter:

| Instance | The evidence | The question it actually answered | The question being asked |
|---|---|---|---|
| **OBS-071** — Loki severity filter | two genuine severity-3 link events | *what happened that the platform carries* | what happened |
| **OBS-089** — the loopback caveat | `10.0.1.17`, a correct next-hop | *which neighbour is the transit hop* | which device owns the destination |
| **OBS-072** — the `.env` contamination | a dozen successful manual runs | *does this work in my environment* | does this work with nothing |
| buffer vs trap level | `Trap logging: informational` | *what is shipped to the collector* | what does the buffer hold |

Two of those four are human inferences with no tool involved, and one — OBS-089 — occurred in a hand diagnosis written specifically to be an independent check on a tool.

**Why nothing downstream detects it.** Every consistency check it could face, it passes: the data is real, the timestamps are ordered, the address is correctly formatted, the runs genuinely succeeded. An empty result announces its own incompleteness; a *wrong-question* result does not. The only defences are stating what the source could not have carried (coverage metadata, T-029a) and, for the human case, the hedging protocol in `chaos-harness.md` §6.

Note the relationship to §0.13's **setup** face without collapsing them: they are orthogonal axes. The faces classify *what the evidence could not show you*; the shapes classify *what the failure looks like*. OBS-072 appears under both, and that is correct rather than duplication.

### Shape 7 — evidence collected, parsed, carried, and never read

| # | Shape | Instances |
|---|---|---|
| 7 | **The system held the answer and reported something weaker** | OBS-092 (B-430) |

**Every shape above concerns what the evidence could not tell you. This one is the opposite: the evidence told you, and nothing listened.**

The instance. Round 3's fault was a BGP neighbour administratively shut on the far end. `bgp_transport` reads `meta["connection_state"]` — `Active` — and reports `transport_blocked`. The same parsed record, in the same envelope, from the same command on the same device, also carried:

```
last_reset_reason : BGP Notification received: administrative shutdown
```

The far end had said why. The parser captured it. The check read the field next to it. **The independent diagnostician logged into the far device to learn what the local device had already reported.**

#### Why it is invisible to every check in this build

> **It cannot be caught by grading the output, because the output is correct.**

`transport_blocked` is true. The citations resolve. The chain is sound. Grounding passes it, and should — nothing is fabricated, nothing is uncited, nothing is overstated. Every mechanism in §0.12, §0.13 and the grounding gate is aimed at output that claims *too much*, and this is output that claims **too little**.

#### Detection — and it is mechanizable

> **Audit what a check reads against what its inputs contain.**

Not a discipline; a script. Enumerate the fields each parser emits from a real fixture, and grep the check module for each one. Measured on this codebase:

| Template | Fields parsed | Fields any check reads |
|---|---|---|
| `bgp_neighbor` | 23 | **5** |
| `interface` | 14 | **5** |
| `route` | 8 | **2** |

**Not all 33 unread fields are defects** — `mac_address` and `bandwidth_kbps` are not diagnostic here, and saying otherwise would turn this shape into the same noise-generating over-correction T-029c refused. But the unread list on `bgp_neighbor` alone includes `state_reason`, `previous_state`, `remote_as`, `hold_time` and `keepalive` — an AS mismatch and a timer mismatch both produce exactly the `Active` state seen in round 3, and **the tool currently cannot distinguish either of them from an administrative shutdown**, while holding the fields that would.

Tracked as **B-433**. The audit is the deliverable; deciding which fields are load-bearing is per-check judgement and belongs with whoever owns the check.

### Shape 8 — a fix silently deletes coverage of behaviour that was always correct

> **Converting a test to a new case removes the old case. The suite stays green, and the deletion looks like a routine update.**

**This is not §0.13's tests face, and filing it there would lose what makes it findable.** In the tests face the test was *wrong* — it encoded the same premise as the code, so it never verified anything. Here **the original test was correct**. It covered real behaviour, it would have caught a real regression, and it was repurposed out of existence by a change that was itself right.

The instance (OBS-097). `test_a_healthy_rung_does_not_stop_the_walk_either` used a rung-1-healthy ladder and asserted the descent named the lowest broken rung. B-428 made that ladder produce `no_fault_on_path`, so the test was updated to expect the new finding — correctly. **That left "descend past a healthy rung to a broken one below and name it" with no coverage at all**, because its only test had just become a test of something else. That property is what round 1 depends on.

Nothing was red at any point. The suite went from 1377 green to 1378 green.

#### The tell

> **A test whose *inputs* had to change, rather than its expectations.**

An expectation changing is the normal shape of a fix: the same scenario now yields a different answer. **Inputs changing means the scenario itself moved**, and the scenario that left is no longer covered by anything unless someone notices.

When it happens, one question: **what were the old inputs covering, and does anything still cover it?** If not, the companion is part of the fix, not a follow-up. In OBS-097 the companion was three lines.

#### Why it belongs in this list rather than in a style guide

It produces the same end state as every other shape here — a green suite over an unverified property — by a route none of the others take. §0.12's vacuity companions do not catch it, because nothing is empty. §0.13's independent specification does not catch it, because the specification is satisfied. Only the diff catches it, and only if someone reads it asking this question.

---

## 0.14 Ask what kind of claim you are acting on

> **Four corrections in one session shared a shape: a rule of method filed as a rule of safety, an incompleteness filed as a detectability problem, a partial signal filed as primary, an impossibility filed as a property of fabrics. Every original statement was true. The classification is what misled.**
>
> **Before acting on a statement, ask what kind of claim it is** — a rule of method and a rule of safety are waived on different grounds, and a primary signal and a corner detector are trusted to different degrees.

A separate section from §0.13 because **the remedy is different**. §0.13 asks *what could the evidence not have shown you* — it is about the reach of the observation. This asks *what kind of thing is this* — it is about the category of the claim, and it bites even when the observation is complete and correct.

### The four, and what the misclassification cost

| Claim | Filed as | Actually | What the misfiling would have caused |
|---|---|---|---|
| "The agent must never run the injector" | a §0.11 safety rule | **a rule of method** | §0.11 rules get waived on reversibility and supervision — twice in this build, both times correctly. Every one of those arguments applies to a drill, and every one is beside the point. The rule would have been waived by sound reasoning |
| Two-fault output is "true but incomplete" | a completeness problem | **a detectability problem** — the rung tables are byte-identical | "Incomplete" invites a fix inside the descent. Nothing inside it can help: the signal is not in the rung verdicts |
| Non-contiguity of broken rungs | the primary two-fault signal | **a corner detector** | It cannot fire on the canonical case. Pointing the harness at it first would have produced a clean result and no information |
| "No consistently-behaving fabric can produce `cause_not_localised`" | a fact about fabrics | **a fact about *single* faults** | Left the build's only synthetic fixture permanently synthetic. An administratively shut session with a healthy underlay produces it directly |

Note what all four have in common: **the statement was accurate and the action it licensed was wrong.** No amount of re-checking the evidence corrects a misclassification, because the evidence supports the statement. Only asking what kind of claim it is does.

### The checks, cheap enough to be habits

- **A rule** — is it waived on safety grounds, or does waiving it destroy something that is not safety? A rule of method survives every reversibility argument, because reversibility was never what it was about.
- **A defect** — is it wrong, or is it *undetectably* wrong? The second is strictly worse and usually needs a signal from outside the component.
- **A signal** — does it fire on the central case, or on a corner of it? A detector's value is where it fires, not that it fires.
- **An impossibility** — is it impossible, or impossible *under the conditions you have so far*? This one is the most common and the easiest to check: name the condition and ask whether it can be lifted.

### Deleting a stated limitation asserts a capability

A corollary, and the case that prompted it (OBS-097). `MVP0-REVIEW.md` §5 said *"it cannot tell you nothing is wrong."* B-428 made that false. The tempting edit is to delete the sentence.

**Deleting it is not neutral.** A review that once named a limitation and no longer does is read as saying the limitation is gone — which is a *different claim* from the one the fix supports. B-428 lets the tool say *"no fault on the path between these two endpoints"*. It does not let it say *"this device is healthy"*, which was never the question a flow asks.

> **When a fix invalidates a stated limitation, narrow the statement to what is still true. Deleting it swaps a true limitation for an implied capability, and the implied one is never written down where anyone can check it.**

So §5 now reads *"read exit 0 as **not on this path**, not as **all clear**"* — shorter than the original warning and still a warning. The general form: **an absence of stated limits is itself a claim**, and it is the one kind of claim nobody reviews, because there is no sentence to review.

---

## 0.15 A protocol tightened for rigour excludes something

> **A test protocol designed for clean measurement can systematically exclude the messiest and most common real case, and its cleanliness is what makes the exclusion invisible. Our trial protocol polls until propagation settles — written as an improvement over a fixed sleep — and in doing so removed the dominant production failure mode from the test surface. When a protocol is tightened for rigour, ask what condition the tightening now excludes.**

Three external reviewers found this independently (`docs/design/peer-review-response.md` §2.1, §7). It is the sharpest self-criticism this project has produced, and it is worth being precise about why it landed.

**The protocol was improved, correctly, and the improvement caused the gap.** "Poll until propagation completes, never sleep" is better methodology than a fixed wait: it removes a timing guess, makes trials reproducible, and stops a slow fabric producing a spurious failure. Every one of those is true. And the condition it removes — *the network is still changing while the tool runs* — is, per reviewer B, **one of the most common overnight incidents**.

So the defect was not introduced by carelessness. It was introduced by rigour, which is why no amount of looking harder at the tests would have surfaced it: the tests were doing exactly what they were designed to do.

### Why this is not §0.13

§0.13 asks *what could the evidence not have shown you* — it is about the reach of an observation. This is narrower and more specific: **the act of making a measurement clean is itself a filter on what can be measured.** A noisy protocol admits the messy case by accident. A clean one excludes it by construction, and reports success more confidently for having done so.

The two compose badly. A protocol that excludes a condition produces a corpus with no instance of it, and §0.13's data face then reads that corpus as evidence. Clean measurement plus a corpus that inherits its cleanliness is how a whole failure mode stays invisible through 1,689 passing tests.

### The check

When tightening a protocol — a test harness, a fixture capture, a trial procedure, a benchmark:

1. **Name the condition being removed.** Not the noise; the *condition*. "Waiting for convergence" removes *mid-convergence*.
2. **Ask whether production has that condition.** If it does, the tightening has moved a real case out of scope, and scope is now a claim requiring its own evidence.
3. **Keep one deliberately untightened variant.** The messy trial is not a worse version of the clean one — it is the only one covering the excluded condition.

The corollary, which is the operational form:

> **Every "we control for X" is also "we do not measure X". Write down which X, next to the protocol, at the time you tighten it.**

---

### The three tracking documents

All three live in `docs/build/` and are maintained continuously, not at the end.

| Document | Nature | Updated |
|---|---|---|
| `BUILD-PLAN.md` | The plan. Task specs. | Status field only, in place, per task |
| `TRACKER.md` | Progress. What is done, what is running, what it cost. | After every task |
| `FINDINGS.md` | Everything learned. Append-only. | Whenever §0.3's triggers fire — often mid-task |

A task is not `DONE` until all three reflect it. If the plan says a task is done and `TRACKER.md` disagrees, `TRACKER.md` is authoritative and the discrepancy is itself a finding.

---

---

# PART 1 — DISCOVERY

No production code changes in this part. The goal is to replace assumptions with facts before building on them.

---

## T-001 · Baseline verification `[STATUS: DONE]`

**Goal.** Establish that the tree is green before anything changes, and record exactly what "green" means today.

**Steps.**
1. `make setup` (or activate the existing `.venv`).
2. `make test` — record the exact pass/skip/fail counts.
3. `make lint` — record output.
4. `git log --oneline -5` and `git status` — record the starting commit and any dirty state.
5. `python -c "import agent_nettools; print(agent_nettools.__version__)"` if a version exists.
6. Record Python version, OS, and the output of `pip list | wc -l`.

**Acceptance.** All tests pass or are explicitly skipped (`live_lab`). Lint is clean.

**On failure.** If tests fail at baseline, **stop**. Log a blocker. Nothing below is valid on a red baseline.

**Observation to record.** Baseline counts, versions, starting commit hash. This is `OBS-001`.

---

## T-002 · MiniMax API contract test `[STATUS: DONE]`

**Goal.** Prove the model endpoint behaves the way the gate and report prompts will require, before any code depends on it.

**Background.** MiniMax-M3 is served over an OpenAI-compatible API. Two behaviours matter and must be verified, not assumed:

- On the OpenAI-compatible Chat Completions route, adaptive thinking is **enabled by default** when `thinking` is omitted, and reasoning content may appear inside `<think>` tags in the `content` field. That would break JSON parsing of a typed decision object.
- `max_completion_tokens` on this route is documented as capped at 2048.

**Preconditions.**
- `MINIMAX_API_KEY` is set in the environment or `.env`. **Never hardcode it, never echo it, never commit it.**
- Network access to `api.minimax.io`.

**Steps.**

Create `scripts/probe_minimax.py` (a throwaway probe, committed, no dependency on `agent_nettools`). It must run six checks and print a table of results:

| # | Check | Method | Pass condition |
|---|---|---|---|
| 1 | Auth and reachability | Minimal `chat/completions` call, `max_completion_tokens: 16` | HTTP 200, non-empty `choices[0].message.content` |
| 2 | Model ID accepted | `"model": "MiniMax-M3"` | No model-not-found error |
| 3 | `<think>` leakage, default | Ask for bare JSON, omit `reasoning_split` | Record whether `content` contains `<think>`. **Either result is informative — record it** |
| 4 | `reasoning_split` behaviour | Same prompt with `"reasoning_split": true` | `content` contains **no** `<think>`; reasoning appears in `reasoning_details` if present |
| 5 | Determinism at temperature 0 | Same prompt 5×, `temperature: 0` | Record how many of 5 responses are byte-identical |
| 6 | Tool calling | One trivial tool definition via `tools`, ask a question that requires it | A `tool_calls` block is returned, or record that it is not |

Prompt for checks 3–5, verbatim:

```
Return only this JSON object and nothing else. No prose, no markdown fences.
{"decision":"narrow","target":{"type":"bgp_neighbor","id":"10.255.0.12"}}
```

Print, for each check: PASS / FAIL / INFO, plus the raw evidence (truncated to 200 chars). **Redact the key from all output.**

**Acceptance.**
- Checks 1 and 2 pass.
- Check 4 passes — this is the one the gate depends on.
- Checks 3, 5, 6 are recorded whatever they show.

**On failure.**
- Auth failure → verify the key is current; the previously shared key must be treated as compromised and rotated. Log a blocker.
- `<think>` still leaking with `reasoning_split: true` → **do not work around it silently.** Log a blocker and record exactly what the response looked like. A stripping step is a possible fix but it is a decision, not an implementation detail.
- Fewer than 5/5 identical at temperature 0 → not a blocker, but record the number. It sets expectations for prompt tests.
- Tool calling unavailable → not a blocker for MVP-0 (the descent needs no tool calling), but it constrains MVP-1.

**Observation to record.** The full result table, the exact `content` shape in checks 3 and 4, and the determinism count. This is the most important observation in Part 1.

---

## T-003 · Wire MiniMax as a provider `[STATUS: DONE]`

**Goal.** Make MiniMax selectable through the existing provider mechanism without touching the other providers.

**Steps.**
1. Read `src/agent_nettools/llm_analysis.py` fully. Note how `get_provider()` selects between `anthropic`, `openai`, and `ollama`.
2. Add a `minimax` provider. It is OpenAI-compatible, so reuse the OpenAI code path with a different `base_url` rather than writing a fourth client.
3. Environment surface, documented in `.env.example`:
   ```
   LLM_PROVIDER=minimax
   MINIMAX_API_KEY=...
   MINIMAX_BASE_URL=https://api.minimax.io/v1
   MINIMAX_MODEL=MiniMax-M3
   ```
4. Set `reasoning_split` and `max_completion_tokens` explicitly on every call, per T-002's findings. Do not rely on endpoint defaults.
5. Add `tests/test_llm_provider.py` cases for provider selection and for a missing-key error — **mocked, no network**.

**Acceptance.** `LLM_PROVIDER=minimax` resolves; a missing key raises the same structured error shape the other providers use; existing provider tests unchanged and green.

**Do not.** Do not change Anthropic/OpenAI/Ollama behaviour. Do not make MiniMax the default in code — it is set via env.

---

## T-004 · Loki discovery `[NON-BLOCKING]` `[STATUS: DONE]`

**Goal.** Determine how device logs are stored and labelled, so `get_logs` can be specified against reality.

**Context.** The operator runs a platform stack in Docker: `syslog-ng 4.5.0`, `grafana/loki 2.9.8`, `prometheus v2.51.2`, `alertmanager v0.27.0`, `telegraf 1.30-alpine` (as gnmic). Containers are named `sota-lab-platform-*`.

**Steps.**
1. Confirm Loki is reachable (default `:3100`). If it is only on a Docker network, record how to reach it from the host.
2. Query the label names: `GET /loki/api/v1/labels`.
3. For each plausible device label (`host`, `hostname`, `device`, `job`, `source`), query `GET /loki/api/v1/label/<name>/values`.
4. Establish whether the nine lab devices appear as label values, and under which label.
5. Run one sample range query for a known device over the last 24h and record the returned line shape.
6. Determine whether syslog-ng actually ships to Loki, or writes files, or both. Read the syslog-ng config if reachable.
7. Record whether IOS-XR syslog **mnemonics** (e.g. `%BGP-5-ADJCHANGE`) survive into the stored line, and whether they are parsed into labels or left in the message body.

**Acceptance.** Either a documented label scheme and a working sample query, or a clear statement that Loki is not currently receiving device logs.

**Why it matters.** Step 7 decides whether Stage 2 flow selection can be a lookup rather than a model judgement. Record it carefully even if nothing else works.

**Output.** Write findings to `docs/build/discovery-loki.md` and summarise in an observation.

---

## T-005 · Alertmanager and Prometheus discovery `[NON-BLOCKING]` `[STATUS: DONE]`

**Goal.** Determine whether the existing stack can be the Stage 2 trigger, removing the need for a separate workflow engine.

**Steps.**
1. Fetch Alertmanager's config (`GET /api/v2/status`) — record receivers, routes, grouping, inhibition rules.
2. Determine whether a webhook receiver exists, and what payload shape it would deliver.
3. Fetch current alerts (`GET /api/v2/alerts`) — record the label set on real alerts.
4. From Prometheus, list metric names matching the lab (`GET /api/v1/label/__name__/values`), filtered to anything gNMI or network-related.
5. Record specifically whether BGP session state, interface oper-state, and interface error counters are available as metrics.

**Acceptance.** A documented alert label shape, and a yes/no on whether a webhook receiver exists.

**Why it matters.** If Alertmanager already does dedupe, grouping, silencing and webhook delivery, Stage 2 needs no n8n at all. Step 5 also tells us whether some descent rungs could read Prometheus instead of the device — **note this only, do not act on it.** The design says the device wins on current state; scraped metrics lag.

**Output.** `docs/build/discovery-alerting.md` plus an observation.

---

## T-006 · L3VPN discovery `[STATUS: DONE]`

**Goal.** Establish what L3VPN objects actually exist, so the `l3vpn_service` flow has a real subject naming scheme.

**Steps.**
1. On one PE (start with PE1), run `show running-config vrf` and `show running-config router bgp` **manually via the existing CLI or an SSH session — do not add commands to the allowlist in this task.**
2. Record: VRF names, RD scheme, import/export RT scheme, which PEs carry which VRFs, and which CE attaches to which VRF.
3. Cross-check against the crossed CE attachment recorded in the topology: CE1→PE1, CE2→PE3, CE3→PE2, CE4→PE4.
4. Propose a subject naming scheme for an L3VPN service object. Candidates: `<vrf-name>`, `<pe>:<vrf>`, `<vrf>:<rd>`. Record the trade-offs; **do not implement**.

**Acceptance.** A written VRF/RT map and a proposed naming scheme.

**Note.** `l3vpn_service` is **not** in MVP-0. This task exists so the flow registry's shape is informed rather than invented. Do not build the flow.

**Output.** `docs/build/discovery-l3vpn.md` plus an observation.

---

## T-007 · Fixture gap analysis `[STATUS: DONE]`

**Goal.** Determine exactly which command outputs are missing for the descent to run offline.

**Context.** `tests/fixtures/cisco_xr/<device>/{t0,t1}/` currently holds seven captures per device, all from static intents. The descent additionally needs template output: `show bgp neighbor <ip>`, `show route <prefix>`, `show interfaces <name>`, `show logging last <n>`.

**Steps.**
1. List exactly what exists per device.
2. For the `bgp_session` descent on **RR1 → 10.255.0.12** (a genuinely Idle peer in the committed `t0` fixture), enumerate every command each rung needs.
3. Do the same for the `interface` descent on one PE.
4. Produce a capture manifest: device, command, template name, why it is needed.
5. Check whether `nettools capture` (`src/agent_nettools/fixtures.py`) can be extended to parameterised templates, or whether that needs new code. Record which.

**Acceptance.** A written capture manifest.

**Critical instruction.** Capture against the **current, broken** state. PE2 and PE4 have zero IS-IS adjacencies; RR1 has two Idle peers. A fixture set covering only healthy devices cannot test a descent whose entire purpose is finding the broken rung. Do not "fix" the lab first.

**Output.** `docs/build/capture-manifest.md` plus an observation.

---

## T-008 · Parser library decision `[STATUS: DONE]`

**Goal.** Confirm TTP is the right parsing library for template output before writing six parsers with it.

**Context.** The LLD recommends TTP as default, with Genie permitted for specific nested outputs, both behind one `TEMPLATE_PARSERS` interface. Verify rather than assume.

**Steps.**
1. `pip install ttp` in the venv. Record version and install size.
2. Write a throwaway TTP template for one captured `show bgp summary` fixture. Confirm it produces the same records the existing hand-written `parse_xr_bgp` produces.
3. Record install weight of `genie`/`pyats` **without installing them** (check PyPI metadata or docs). If installing, do it in a *separate* throwaway venv so the project venv stays light.
4. Decide and record: TTP for all new parsers, or TTP + Genie for `bgp_neighbor` specifically.

**Acceptance.** A decision with recorded reasoning, and `ttp` added to `pyproject.toml` under the right extra.

**Do not.** Do not migrate the existing six parsers in `parsers.py`. They work and are tested.

---

## T-009 · Docs scaffold `[STATUS: DONE]`

**Goal.** Put the reference documents where they can be found, and point `CLAUDE.md` at them.

**Steps.**
1. Create the folder structure in §PART 5 of this document.
2. Move/copy the design documents into `docs/design/`.
3. Create `docs/build/FINDINGS.md` from the template in §PART 6 if it does not already exist.
4. Add a short **Design documents** section to `CLAUDE.md` linking each file and saying in one line what it is for.
5. Add `docs/README.md` — a one-screen map of the docs tree.

**Acceptance.** `CLAUDE.md` links resolve. `docs/README.md` explains the tree.

---

# PART 2 — PARSING

Implements LLD Phase 9. Nothing downstream is possible without this part.

---

## T-010 · `template_parsers.py` skeleton and contract `[STATUS: DONE]`

**Goal.** Establish the module and its contract before writing any parser.

**Steps.**
1. Create `src/agent_nettools/template_parsers.py`.
2. Mirror `parsers.py`'s contract exactly:
   ```python
   TEMPLATE_PARSERS: dict[tuple[str, str], Callable[[str], dict[str, Any]]]
   # keyed by (platform, template_name)
   ```
3. Reuse — do not redefine — `parsers.ParseError`, `PARSE_OK`, `PARSE_UNAVAILABLE`, `PARSE_FAILED`. If they are not importable, refactor `parsers.py` to export them **without behaviour change**, and log an observation.
4. Provide `has_template_parser`, `parse_template_output`, `template_record_key`, `template_volatile_fields` mirroring the `parsers.py` equivalents.
5. Output shape for every parser: `{"meta": {...}, "records": [...]}`.
6. Write `tests/test_template_parsers.py` with contract tests only — no parsers yet, so they should assert the registry is empty-but-well-formed.

**Acceptance.** Module imports; contract tests green; `make lint` clean.

---

## T-011 · Extend capture to templates `[STATUS: TODO]`

**Goal.** Make `nettools capture` able to record parameterised template output, per T-007's manifest.

**Steps.**
1. Read `src/agent_nettools/fixtures.py`.
2. Extend capture so a manifest of `(device, template, params)` can be captured alongside the static intents.
3. Fixture filenames must be deterministic and safe: derive from the **rendered** command, sanitised — e.g. `show-bgp-neighbor-10-255-0-12.txt`.
4. Capture `t0` for the devices and commands in the manifest. **Against the current broken state.**
5. Review every captured file by eye before committing. Fixtures are permanent once pushed.

**Acceptance.** New fixtures exist under `tests/fixtures/cisco_xr/<device>/t0/`, and `tests/test_fixtures.py` still passes.

**Blocker condition.** If the lab is unreachable, mark `BLOCKED` and stop — Part 2 cannot proceed without real output. Do not write parsers against invented output.

---

## T-012 to T-017 · The parsers `[STATUS: T-012…T-017 ALL DONE]`

One task each, in this order. **Do not start a parser before the previous one is `DONE` and its tests are green.**

| Task | Template | Parsed `meta` (minimum) | Parsed `records` |
|---|---|---|---|
| T-012 | `bgp_neighbor` | `state`, `connection_state`, `last_reset_reason`, `hold_time`, `keepalive`, `local_as`, `remote_as` | address-family rows |
| T-013 | `route` | `found` (bool), `protocol`, `distance`, `metric` | `{next_hop, interface}` |
| T-014 | `interface` | `admin_state`, `line_state`, `mtu`, `description`, `bandwidth` | `{counter, value}` |
| T-015 | `logging` | `lines`, `window` | `{timestamp, severity, mnemonic, text}` |
| T-016 | `ping` | `sent`, `received`, `loss_pct`, `rtt_min`, `rtt_avg`, `rtt_max` | — |
| T-017 | `traceroute` | `hops`, `completed` | `{hop, address, rtt}` |

**Rules for every parser:**

- Raise `ParseError` on non-empty output it cannot read. **Never return an empty result silently.**
- Count malformed rows into `meta["unparsed_rows"]` rather than dropping them — follow `parse_xr_bgp`'s precedent.
- Define `record_key` and `volatile_fields` so `diff_evidence` and `detect_flaps` work on template output too.
- Tests must include: a real fixture round-trip, a truncated-output case, and a garbage-input case that must raise.

**T-015 is more important than it looks.** The `mnemonic` field is what makes Stage 2 trigger→flow routing a lookup rather than a model judgement. Parse it even though nothing consumes it yet. Cross-check the mnemonic format against T-004's Loki findings and log an observation if they disagree.

---

## T-018 · Attach parsed data to `run_template` `[STATUS: DONE]`

**Goal.** Make template results carry parsed data, exactly as `run_intent` does.

**Steps.**
1. Read `network_tools.run_intent` and its `_attach_parsed` call.
2. Add the equivalent to `run_template`, using `template_parsers`.
3. Preserve the envelope shape: `{tool, device, status, timestamp, data, errors}` plus the parse keys. Existing callers must be unaffected.
4. Add tests asserting a template result carries `parse_status` and parsed data, and that a parse failure yields `PARSE_FAILED` **without** turning the whole result into an error.

**Acceptance.** All 554 baseline tests still green, plus the new ones.

**This closes the LLD's blocking gap.** Log an observation confirming it.

---

# PART 3 — CHECKS AND DESCENT

Implements LLD Phases 10 and 11.

---

## T-019 · `checks.py` and `CheckResult` `[STATUS: DONE]`

**Goal.** Pure per-object predicates over parsed records. No I/O, no device access, no inventory reads.

**Contract:**

```python
@dataclass(frozen=True)
class CheckResult:
    status: str                       # "healthy" | "broken" | "unevaluated"
    reason: str | None                # why unevaluated, or what is broken
    subject: str | None               # the object this verdict is about
    evidence_keys: tuple[str, ...]    # what it read — feeds grounding
```

**The `unevaluated` rule is mandatory.** If the underlying intent or template's `parse_status` is not `PARSE_OK`, the check returns `unevaluated` — never `healthy`, never `broken`. This is `health.py`'s discipline, adopted verbatim. A failed collection must never look like a verdict.

**Acceptance.** Module imports; dataclass is frozen; no import of `inventory`, `network_tools`, or anything that touches a device.

---

## T-020 · The five checks `[STATUS: DONE]`

| Function | Reads | `broken` when |
|---|---|---|
| `bgp_session_state(evidence, peer)` | `bgp` intent | `state_pfx_rcd` is non-numeric (Idle / Active / Connect) |
| `bgp_transport(evidence, peer)` | `bgp_neighbor` template | connection state is not Established |
| `route_present(evidence, prefix)` | `route` template | `meta.found` is false |
| `isis_adjacency(evidence, interface=None)` | `isis` intent | zero adjacencies, or a named adjacency is not Up |
| `interface_state(evidence, name)` | `interfaces` intent / `interface` template | admin up + line down, or error counters above threshold |

Each returns a `CheckResult` populated with the evidence keys it read.

**Tests must cover all three outcomes for each check**, including `unevaluated` driven by a synthetic failed-parse envelope.

**Note on `interface_state`.** The error-counter threshold is a decision the plan does not make. Pick a defensible default, make it a module constant, and **log an observation flagging it for human review.**

---

## T-021 · The agreement test `[STATUS: DONE]`

**Goal.** Let `checks.py` and `health.py` coexist safely without refactoring either.

**Steps.**
Create `tests/test_checks_agree_with_health.py`. For every fixture device at `t0` and `t1`, for each check with a corresponding health rule:

- Neither may say `healthy` where the other says `broken`.
- One may be `unevaluated` where the other is not — that is allowed.
- Any genuine disagreement fails the test.

**Acceptance.** Green across all nine devices × two snapshots.

**If it fails**, that is a real finding, not a test to loosen. Log an observation with the exact disagreement and mark `BLOCKED`.

---

## T-022 · `flows.py` — registry and dataclasses `[STATUS: DONE]`

```python
@dataclass(frozen=True)
class Rung:
    name: str
    collect: tuple[CollectStep, ...]   # intents/templates needed before checking
    check: Callable[..., CheckResult]
    finding: str                        # terminal finding if this rung is broken

@dataclass(frozen=True)
class Flow:
    object_type: str
    subject_schema: str
    descent: tuple[Rung, ...]           # ordered, top of stack first
    findings: frozenset[str]
```

Register seven object types: `interface`, `isis_adjacency`, `bgp_session`, `ldp_session`, `l3vpn_service`, `device_health`, `topology`.

**Implement `bgp_session` and `interface` only.** The other five raise `NotImplementedError` with a message naming the task that will implement them. The registry shape is fixed; coverage is not pretended.

**Add a safety test** mirroring `test_check_tool_intents_exist_in_the_platform_table`: every `Rung.collect` step must name an intent in `platforms.all_intents()` or a template in `PLATFORM_TEMPLATES`.

---

## T-023 · The `bgp_session` descent `[STATUS: DONE]`

Ordered rungs, top of stack first:

```
1. bgp_session   → bgp_session_state       → finding: peer_not_established
2. transport     → bgp_transport           → finding: transport_blocked
3. route_to_peer → route_present           → finding: peer_unreachable_no_route
4. igp_adjacency → isis_adjacency          → finding: igp_isolated
5. interface     → interface_state         → finding: interface_line_down
```

Findings are a closed enum on the flow, plus `all_layers_healthy` and `undetermined`. **Symptoms live here as terminal findings — never as separate flows.**

---

## T-024 · `descent.py` — the walker `[STATUS: DONE]`

```python
def run_descent(flow: Flow, device: str, subject: str, *, collector) -> DescentResult
```

Semantics, exactly. **Corrected 2026-08-16 — the original rule was a defect in this plan, found by measuring the ladder against the `broken` label (OBS-055, Q-017).**

- `broken` → **record the finding and CONTINUE descending.**
- `healthy` → **continue descending.** A healthy layer does not prove the ones below it are fine: RR1's own IS-IS was healthy while PE2's was not.
- `unevaluated` → **STOP.** Nothing below a rung that could not be read is trustworthy. `finding="undetermined"`, reason recorded.
- Ladder exhausted with every rung healthy → `all_layers_healthy`.

**The result is the LOWEST broken rung.** Higher broken rungs become the *causal chain* — the evidence that this cause explains the observed symptom. The descent ends at the bottom of the ladder or at an `unevaluated`, never at the first fault.

**Why the original rule was wrong.** It said "`broken` → stop, that rung's finding is the result", which stops at the *highest* broken layer. D6 says the lowest broken layer is the root cause. Measured on the `broken` label, rungs 1, 2 and 3 are all broken for `RR1 → 10.255.0.12`, so the old rule returned `peer_not_established` — a restatement of the alert — and never reached the shut interface. **Four of the five findings the flow declares were unreachable.** `t0` hid it, because there the deeper rungs have no template fixtures and stopping at rung 1 looks correct.

New finding required on the flow: **`cause_not_localised`** — rungs broken above, all healthy below, nothing beneath to explain them. That is an honest answer, not a failure.

What this buys, concretely: the report stops being *"BGP is not established"* and becomes *"the interface is admin-down, which isolated IS-IS, which removed the route, which blocked transport, which is why BGP is Idle."* The first restates the alert; the second is an RCA.

Returns the rung path taken, each `CheckResult`, the causal chain, and the accumulated evidence keys.

**No model call anywhere in this module.** If you find yourself wanting one, stop and log a blocker.

The `collector` is injected so a descent can run against `fixtures.load_fixture_evidence` with no lab access. That is what makes T-025 possible.

**Tests:** descends past a broken rung; reports the lowest broken rung as the finding; higher broken rungs appear in the causal chain; `unevaluated` stops the walk and nothing below it is collected (assert on collector call count); an all-healthy ladder yields `all_layers_healthy`; broken-above-healthy-below yields `cause_not_localised`.

---

## T-025 · The acceptance test `[STATUS: DONE]`

**This is the milestone that proves the architecture.**

**Rewritten 2026-08-16.** The original wording said "the descent stops at a named rung / no rung below the stopping rung was collected", which endorsed the T-024 defect: it would have passed against the buggy walker and failed against the correct one. A milestone test that ratifies the bug it should catch is worse than no test.

```
Given  fixture evidence for RR1
When   run_descent(bgp_session, device="RR1", subject="10.255.0.12")
Then   the descent visits every rung until the ladder ends or a rung is unevaluated
And    the reported finding is the LOWEST broken rung
And    higher broken rungs appear as the causal chain
And    every CheckResult carries the evidence keys it read
And    no model call occurred
```

**Two acceptance cases, both required.**

1. **`t0`** — the original partly-broken fabric.
2. **`broken`** — the deliberate PE2 isolation. `RR1 → 10.255.0.12` must descend all the way to **`interface_line_down` on PE2**, with rungs 1–3 (`bgp_session`, `transport`, `route_to_peer`) present in the causal chain. **This is the test that would have caught Q-017**, and it is the reason the `broken` label was captured.

Add the mirror case: `subject="10.255.0.11"` on `healthy` — an established peer — must yield `all_layers_healthy`.

**Acceptance.** All pass offline, with no lab and no API key.

**Record in observations:** which rung stopped, and whether that matches what a network engineer would conclude by hand from the same fixtures. If it does not, that is the single most important finding of the whole build.

---

# PART 4 — PROMPTS, GROUNDING, AND MVP-0

---

## T-026 · Prompt library scaffold `[STATUS: DONE]`

**Goal.** Prompts as version-controlled, tested artifacts — the same discipline as the command allowlist.

**Structure:**

```
prompts/
├── README.md              # GRACE, and the rules below
├── report.v1.txt
├── correlate.v1.txt
└── tests/
    └── cases/             # golden input → expected output shape
```

**The framework is GRACE:**

| | | |
|---|---|---|
| **G** | Grounding | The validated evidence, each item with an evidence key. Every claim must cite one. |
| **R** | Role | The operational perspective. Narrow. |
| **A** | Anchors | One worked example: real input, exact output. |
| **C** | Constraints | Prohibitions, **and the named refusal path** — "if the evidence does not support a conclusion, return `undetermined`". |
| **E** | Expected output | The exact schema. Parseable at temperature 0. No prose wrapper. |

**There is deliberately no Evaluation slot.** Evaluation is the schema validator and the grounding check — code, not prose. Record this in `prompts/README.md` as a stated choice.

**Rules:** every prompt is a versioned file (`name.vN.txt`); every prompt has at least one golden test case drawn from `tests/fixtures/`; a prompt change requires a version bump, never an in-place edit.

---

## T-027 · The `report` prompt `[STATUS: DONE]`

**Input:** a `DescentResult` plus its evidence bundle.
**Output:** JSON with three separated sections:

```json
{
  "observations":    [{"claim": "...", "evidence_key": "..."}],
  "interpretations": [{"claim": "...", "based_on": ["obs-1", "obs-2"]}],
  "recommendation":  {"next_check": "...", "requires_human": true}
}
```

Observations cite evidence keys. Interpretations cite observations. The recommendation is explicitly the model's and explicitly for a human.

**Golden test:** the RR1 `t0` descent result. Assert the **shape and citation integrity**, not the prose. Prose will vary; structure must not.

---

## T-028 · The `correlate` prompt `[STATUS: DONE]`

**Input:** a finding plus the log window filtered to the subject.
**Output:** a timeline linking the finding to log events, or an explicit "no correlating events in window".

This is the model's genuine contribution per the design: the descent says *what* is broken; correlation says *when it changed and whether it followed a commit*.

**Blocked on T-004** if logs come from Loki. If Loki is unavailable, use the `logging` template output from T-015 and log an observation that the source is provisional.

---

## T-029 · `grounding.py` `[STATUS: DONE]`

```python
def check_grounding(report: dict, evidence_keys: frozenset[str]) -> GroundingResult
```

- Every observation's `evidence_key` must be in `evidence_keys`.
- Every interpretation's `based_on` must reference existing observations.
- Recommendations are exempt from citation but must carry `requires_human: true`.

**A failed grounding check means the report is not emitted.** The run returns the descent result plus the grounding failure — never the model's prose.

**Tests:** an invented evidence key fails; a dangling `based_on` fails; a valid report passes.

**Amended 2026-08-16 — grounding covers the causal chain.** The three bullets above are *citation integrity*: is the report internally sound? They cannot see the failure that matters most, because nothing in it is false. A report reading

> "PE2's Gi0/0/0/0 is administratively down." `[obs-1, real key, cited, flagged]`

passes every one of them and is the output `prompts/README.md` rules out — the cause named, the four rungs that explain it dropped, an RCA reduced to an assertion with a label attached.

So there is a second check, and it needs the descent rather than a flat key set:

```python
def check_chain_coverage(report: dict, descent: DescentResult) -> GroundingResult
def ground_report(report: dict, descent: DescentResult) -> GroundingResult   # both
```

- **Every rung the walk read is an observation**, citing one of *that rung's* evidence keys. An uncited rung is an uncited claim. A rung with no keys (`unevaluated`, nothing read) is exempt — there is nothing to cite, and that the report must *say so* is the prompt's refusal case.
- **The causal chain is argued.** The union of all interpretations' `based_on` must cover the cause and every broken rung above it. The union rather than one interpretation: splitting a five-link chain into two sentences is better prose and no weaker an argument; dropping a link is what this forbids.
- `check_grounding` keeps its specified signature and is exported as a component. **`ground_report` is what the emit path calls** — reaching for `check_grounding` alone there turns the chain requirement off silently.

Keys are `descent_evidence_keys(descent)` — what the checks *read*, never what exists in the evidence store. Citing a real key for an interface this descent never looked at is an uncited claim wearing a citation.

`GroundingFailure` carries a *locus* and never a `claim`. "The report is not emitted" is worth nothing if the rejection quotes it, and a rule enforced by remembering to redact eventually is not enforced (OBS-061).

**T-030 acceptance depends on this:** the runner must call `ground_report`. A runner calling `check_grounding` would pass every internally-consistent report.

---

## T-029a · absence claims must be backed by coverage `[STATUS: DONE]`

Pulled forward from **B-420** at the operator's direction, and it lands before T-032.

```python
def check_absence_coverage(claim: dict, coverage: Coverage | None) -> GroundingResult
def ground_correlation(claim: dict, coverage: Coverage | None) -> GroundingResult
```

**The gap.** Grounding enforces citation for claims of **presence**. It enforced nothing for claims of **absence**, so `"no correlating events in window"` passed with nothing behind it — on a source measured to drop severity 5 and 6, which is to say on a source that cannot support the claim at all.

Same asymmetry `check_chain_coverage` exists to close, on a different axis: **a check that inspects only what is present cannot see what was omitted.** A peer rather than a rule inside the existing check, because the input it needs — the coverage record — is not in the report.

**`coverage.py`.** `Coverage` carries what a source was able to tell us and `gaps()` lists every reason it cannot support a negative: an incomplete query, a severity class the source does not carry, a truncated read, records the source reports dropping. Built **by code**, never asserted by a caller and never by a model — `coverage_from_logging` reads every field from the `show logging` header the device itself emitted.

Read the **buffer** level, not the trap level. `show logging` returns the buffer (debugging, 0–7); the trap level governs what is shipped to the collector (informational, 0–6). Reading the trap level here understates the local source by exactly the severity class B-206a is about, while looking correct.

**Two failure kinds, deliberately distinct** so a runner can tell them apart:

| Kind | Meaning | What a runner should do |
|---|---|---|
| `unbacked_absence_claim` | no coverage record at all | construction bug — do not emit |
| `absence_claim_exceeds_coverage` | a record, with gaps | a real answer at the wrong strength — **downgrade the finding**, do not discard it |

**Measured consequence, and it is uncomfortable.** Neither golden correlate case can assert a clean negative. PE2's `healthy` buffer holds **555** messages and `show logging last 200` retrieved 200; whatever is in the other 355 was not read. The remedy is not to weaken the rule — it is to widen the window until the source reports itself exhausted, which is exactly the incentive this should create.

`correlate.v3` carries a COVERAGE slot and constraint 7 (*never state a negative more strongly than the coverage supports*), and its refusal marker becomes **"no correlating events in the available coverage"**.

**Tests:** an unbacked claim fails; a truncated source fails and names the shortfall; an exhausted source passes (the §0.12 companion — without it the check could reject everything and every other test would still pass); a severity-limited source can never pass; **presence is not weakened by a gap**; a malformed `found` is not read as an absence claim.

---

## T-029b · timeline citations — presence checking for correlations `[STATUS: DONE]`

Pulled forward from **B-424** at the operator's direction, after T-033 emitted a fabricated timestamp to a user.

```python
def check_timeline_citations(claim: dict, window: ShapedWindow | None) -> GroundingResult
def ground_correlation(claim, coverage, window=None) -> GroundingResult   # both halves
```

**The gap.** Grounding checked *presence* for reports and *absence* for correlations (T-029a). It checked **nothing** for presence in a correlation: `ground_correlation` returned a **vacuous pass** whenever `found` was not `False`, and printed `correlation grounding: vacuous pass — 0 observations, 0 citations` in every payload.

|  | presence | absence |
|---|---|---|
| **report** | checked | n/a |
| **correlation** | **was unchecked** → T-029b | checked (T-029a) |

**Found live, not by review.** MiniMax emitted `Aug 14 04:28.238 UTC` for a record whose real timestamp is `Aug 16 14:04:28.238 UTC` — characters dropped, producing a malformed date two days earlier, in the one field `correlate.v3` constraint 2 says to quote exactly. One of nine entries did not exist in the evidence.

Two rules, the report's evidence-key rule applied to the other output:

1. **`at` must be a device timestamp present in the window** — verbatim, not approximately. A timeline is an ordering claim, and an ordering built on one wrong instant is wrong in a way no reader can see.
2. **`mnemonic` must match a record at that timestamp.** A real instant attached to an event that did not happen at it is the same fabrication wearing a valid citation, and a timestamp check alone waves it through.

A timeline with **no window** is refused rather than passed: grading against nothing must not look like grading successfully. An **empty** timeline is not a citation problem — that is `check_absence_coverage`'s business.

**Tests:** 15. The live fabrication is pinned verbatim as a regression. `test_the_gate_now_runs_both_halves` is the §0.12 companion — it asserts `check_absence_coverage` alone *passes* the fabricated timeline and is `vacuous`, and that the composed gate does not.

---

## T-029c · a vacuous verdict beside claims is a contradiction `[STATUS: DONE]`

Promoted from **B-429** at the operator's direction, before T-034.

```python
def claims_present(payload: object) -> tuple[str, ...]
# applied inside ground_report() and ground_correlation()
```

**The gap.** §0.12 gave `GroundingResult.vacuous` so a pass over nothing would be *distinguishable* from a real pass. It worked. At T-033 the payload printed

```
correlation grounding: vacuous pass -- 0 observations, 0 citations
```

next to a **nine-entry timeline carrying a fabricated timestamp**. The instrument was correct, reported the gap in every run, and **nobody read it.**

> **Any warning that requires a human to notice it will eventually not be noticed. Where a condition is checkable, check it.**

A vacuous verdict beside a payload that plainly contains claims is not a note about coverage — it is a **contradiction**: the payload asserts things and the gate examined none of them. Contradictions are raised by the code.

**Deliberately narrow, and that is load-bearing.** The audit covered every reader-facing flag in the package:

| Flag | Verdict |
|---|---|
| `GroundingResult.vacuous` + claims | **contradiction → raised** |
| `meta["unaccounted_lines"]` with `PARSE_OK` | borderline; §0.10 specifies these *surface* rather than fail. Left informational, noted |
| `ShapedWindow.unattributed_kept` | a **fact** — the noise filter declined to drop 8 records. Nothing inconsistent |
| `InvestigationResult.repairs` | a fact about model behaviour |
| `data["retries"]` | a fact about the transport |

> **A rule against unread warnings that generates unread warnings has defeated itself. A contradiction is raised; a fact is reported. One flag changed out of five, and that ratio is the point.**

Turning every reader-facing number into an error is the same failure wearing the opposite sign — it trains people to ignore these too, which is precisely how the T-033 warning went unread. The rule fires only where two facts contradict each other.

### Prefer the narrowest true failure name

On the exact T-033 shape the failure raised is `uncited_timeline`, **not** `verified_nothing`. The timeline check *ran*, found no window to grade against, and refused.

> **A gate that refused something did not measure nothing.**

Collapsing a specific refusal into a generic one loses the same information as reporting `cause_not_localised` where the cause is localised to routing: in both cases the system knows more than it says, and the reader cannot recover the difference. **Prefer the narrowest true failure name available** — `verified_nothing` is a backstop for when nothing examined the payload at all, and it is worth less every time it fires somewhere a specific name would have fitted.

**Also caught:** `{"correlation": {"found": true}, "timeline": []}` — a positive correlation with no cited event, previously accepted. The purest form of the defect.

**Tests:** 6, in both directions. `test_a_genuinely_empty_payload_is_still_a_clean_vacuous_pass` is the companion — without it the check could refuse every vacuous result and every other test would still pass.

---

## T-030 · `investigation.py` — the MVP-0 runner `[STATUS: DONE]`

```python
def investigate(device: str, subject: str, *, flow: str, collector=None) -> InvestigationResult
```

Order: resolve scope → run descent → correlate (model) → write report (model) → grounding check → emit.

**No gate and no narrowing pass in MVP-0.** The descent is deterministic and terminal. The gate is MVP-1.

`agent_loop.py` is untouched and remains the general-purpose bounded loop for questions that do not map to a flow.

**Decisions taken during implementation.**

1. **`report` is `None` whenever grounding failed** — not populated with a flag beside it. "The report is not emitted" has to be structural, or a caller writing `result.report or "..."` prints rejected prose. Third application of OBS-061's containment rule.
2. **The log window is read from the device the *cause* is on**, not the device the investigation started from. `RR1 → 10.255.0.12` finds its cause on PE2, and PE2's buffer holds the interface and IS-IS events. Reading RR1's logs would correlate a PE2 event against a device that never saw it and return "no correlating events" with perfect confidence.
3. **`absence_claim_exceeds_coverage` downgrades; it does not discard** (T-029a). The result is kept and labelled `coverage_limited`. Discarding loses a usable answer; emitting it as a negative overstates it. Every other grounding failure withholds.
4. **A markdown fence is stripped and the repair is recorded. Nothing else is repaired.** Stripping a fence cannot change what the JSON says; anything that reaches into the content is a regex second-guessing the model. Recording it makes a model that keeps ignoring an explicit instruction visible as a prompt problem.
5. **No analyst is a mode, not a degraded run.** The descent is a complete result — it is the half with no model in it — and both model outputs report `not_attempted` rather than being absent.
6. **`inventory_resolver` raises on an unknown subject** rather than falling back to the local device, matching `_resolve_devices`. A wrong-device read looks exactly like a healthy one.

**Tests:** 22, against both labels with a scripted analyst — no model, no network. Three carry the weight: `test_the_model_cannot_influence_the_diagnosis` (the descent is byte-identical with and without an analyst, which is the claim the whole layer rests on and which nothing else would catch), `test_the_log_window_comes_from_the_cause_device_not_the_local_one`, and `test_the_runner_grounds_through_ground_report` — asserted by name, bluntly, because a future edit swapping it for `check_grounding` would leave every behavioural test passing. §0.13.

---

## T-031 · CLI wiring `[STATUS: DONE]`

```bash
nettools investigate RR1 10.255.0.12 --flow bgp_session
nettools investigate RR1 10.255.0.12 --flow bgp_session --from-fixtures
nettools investigate PE1 GigabitEthernet0/0/0/1 --flow interface --format summary
```

Follow the existing conventions exactly: `--format json|table|summary`, `--quiet`, and the project-wide exit-code scheme (`0` nothing actionable, `1` reports a problem, `2` could not run / worst outcome).

`--from-fixtures` must work with **no lab and no API key**, skipping the model steps and emitting the descent result alone. That is the demo that proves the deterministic core.

**Exit codes, decided at T-031 and diverging from the generic scheme above.**

| Code | Meaning |
|---|---|
| `0` | the descent completed and found no fault |
| `1` | the descent completed and found a fault — a problem with the **network** |
| `2` | no trustworthy answer was produced — a problem with the **answer** |

Exit 2 covers `undetermined`, a withheld report, a collection failure, and a flow that could not run. **A grounding failure is exit 2 even when the descent found a real fault**, and the argument is not "the caller got nothing": if it were exit 1, a *systematic* grounding regression would hide in the noise of routine faults forever, because faults are normal and exit 1 is normal. The descent's finding stays in the payload either way, so nothing about the network is concealed.

`coverage_limited` follows the descent's own outcome (0 or 1) and carries its caveat as a **payload field**, rendered in every format. The descent is deterministic and reached with no model; only the correlation is qualified.

Matches `nettools diff`. **Does not match `nettools health`**, where 2 is the worst network outcome — documented in the subcommand's `--help` and the README, because a script calling both will otherwise assume one scheme.

**README:** `--from-fixtures` is the first documented command, above "What This Does", with the rendered causal chain inline.

---

## T-032 · End-to-end offline test `[STATUS: DONE]`

Full pipeline against fixtures with the model mocked. Assert: descent runs, report shape is valid, grounding passes, exit code correct, no network calls.

**Assert BOTH labels, as T-025 does** — the same peer (`RR1 → 10.255.0.12`) must give opposite answers on `healthy` and `broken`, all the way through to a grounded report. One label proves the pipeline runs; two prove it discriminates.

**Two decisions taken during implementation.**

1. **The mock model reads its own prompt.** A canned report is correct whatever the pipeline renders, so it cannot detect the pipeline handing the model the *wrong descent* — the single most likely wiring bug in a chain this long, and the one an end-to-end test exists to catch. `ReadsItsPrompt` parses the descent payload out of the rendered prompt and answers from it, so a `healthy` run carrying the `broken` descent produces a report about the wrong finding and `test_the_model_was_handed_the_right_descent_on_each_label` fails.
2. **"No network" is enforced, not assumed.** `netmiko` is replaced with a module that raises on contact — the lazy import inside `_netmiko_send_commands` is the single choke point every real connection passes. `test_the_guard_actually_fires` is the §0.12 companion: without it, a misnamed module or attribute would make the guard decorative while every other test still passed.

**Tests:** 10. Invariant 4 is asserted against the **verbatim rendered prompts**, not the parsed payloads — a parsed payload cannot contain raw command output by construction, so checking it would check nothing. The assertion also pins that the correlate prompt *does* carry device log lines (parsed records re-serialised) while *not* carrying the raw `show logging` header, since distinguishing those two is the entire content of invariant 4 here.

---

## T-033 · Live lab run `[STATUS: DONE]`

Add to `tests/test_live_lab.py` under the existing `live_lab` marker, self-skipping unless `NETTOOLS_LIVE_LAB=1`.

Then run manually against the real fabric with MiniMax configured, and record in observations: the rung reached, wall-clock time, token usage, whether the report's citations all resolved, and — most importantly — **whether a network engineer would agree with the conclusion.**

**Protocol for the last of those, set by the operator and binding on any repeat.** Do not show the descent output and ask whether it looks right — that tests agreement with a stated conclusion, which people give too easily. The engineer diagnoses the same subject independently and by hand; that diagnosis is **recorded verbatim and committed before the agent runs**, so the ordering is a fact in git history rather than a claim. Then compare. If the rungs match, that is evidence. If they differ, which one is wrong and why is worth more than a nod.

**Result: they matched** — `igp_adjacency` on PE3, `igp_isolated`, interface rung healthy, on a fault whose *symptom* from RR1 is indistinguishable from the captured `broken` label where the cause was a different rung. Q-006 resolved. Hand diagnosis OBS-076 (`093d665`, 14:11:50 UTC); agent run OBS-077 (14:12:02 UTC). 114.2 s against a 103 s healthy baseline; exit 1; report grounded with 5/5 rungs cited.

**The run found two defects that 1,348 passing tests could not.** `_log_window` had never worked — `count` passed as an `int` where the template layer requires text, and output read from the wrong key — and no test executed it, because every test injects `window=`. And the correlation path has **no citation check at all**, which let a fabricated timestamp through (**B-424**, recommended as T-029b before T-034). Both are §0.13's tests face: the suite agreed with the premise it was written from.

---

## T-034 · Documentation update `[STATUS: DONE]`

Update `README.md` (new CLI surface, GRACE, the prompts directory), `CLAUDE.md` (new modules, where they sit in the layer model), and `.env.example` (MiniMax variables).

**Two operator requirements, both binding on any future edit.**

1. **`--from-fixtures` is the README's first documented command**, above "What This Does". It runs with no lab, no API key and no network, and produces a named rung with a causal chain. That is the demonstration that convinces a sceptical engineer the diagnosis is not the model's opinion, and it is the most persuasive artifact this build has produced. Do not demote it below feature lists.

2. **The T-033 result is documented including the fabricated timestamp.** An honest account of the one thing the model got wrong — and of why it could not affect the diagnosis — is more convincing than an account with only successes in it. The discrimination is stated too: the same symptom as the captured `broken` label, a different rung, and both diagnoses walking past a healthy interface rung to report the broken IGP rung above it.

**What was written.** README gains "Try it in ten seconds", "Does it work against a real fabric? One blind trial, reported in full" (with the timestamp failure and the three things about it), and "The investigation layer" — the walk rule, why `unevaluated` stops it, what the model is and is not for, GRACE with its deliberately absent E slot, grounding as a gate, and the exit-code scheme with the `nettools health` divergence. `CLAUDE.md` gains the nine-module investigation-layer dependency table, the four inherited invariants, and the two things that look like ordinary code and are not. `.env.example`'s MiniMax block already existed; what was missing was **which surfaces need a provider at all** — `investigate` needs one only for the report and timeline, and `--no-model`/`--from-fixtures` need none.

---

## T-035 · Report relay — outbound only `[OPTIONAL FAST-FOLLOW]` `[STATUS: TODO]`

**Goal.** Get investigation output in front of the team without building a chat interface.

**Read first.** `docs/design/interfaces.md` — the interface ladder and the three decisions this task defers rather than resolves.

**Why this and not a chat bot.** MVP-0 has no conversation to have. The descent is deterministic and terminal: name a device and a subject, get a report. That is a command, not a dialogue. A chat box promises multi-turn — someone will type "why is the network slow?", flow selection by free text does not exist until MVP-1, and the first thing the team learns about the tool is what it cannot do. This task ships the *delivery* half only.

**Hard constraint: there is no inbound surface.** No command handler, no webhook listener, no polling loop. If this task grows an inbound path, it has become MVP-1 work — stop and log a HALT.

**Design — mirror `credential_resolver.py` exactly.** That module already establishes the pattern for a pluggable provider selected by environment variable; follow it rather than inventing a second shape.

```
src/agent_nettools/notifier.py

    class Notifier(Protocol):
        def send(self, report: dict, *, subject: str, device: str) -> None: ...

    get_notifier() -> Notifier      # env-then-default, same shape as get_resolver()
```

Providers: `none` (default, a no-op), `telegram`, `mattermost`, `webhook`.

```
NETTOOLS_NOTIFIER=none|telegram|mattermost|webhook
NETTOOLS_NOTIFIER_TIMEOUT_SECONDS=10

# telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# mattermost / webhook
NETTOOLS_NOTIFIER_URL=...
NETTOOLS_NOTIFIER_TOKEN=...
```

Implement `none` and **one** real provider. Which one depends on the residency decision below — do not implement both.

**Best-effort, never fatal.** A notification failure is swallowed and logged, never raised. This follows the existing `NETTOOLS_LOG` contract exactly: a bad notifier configuration must never turn a successful investigation into a reported failure. Record the attempt and its outcome in the audit log.

**What may leave the estate — structural, not filtered.** The notifier sends the **report object only**: observations, interpretations, recommendation, and the descent's rung path and finding. It never receives the evidence bundle, so raw command output, configuration fragments, and parsed records cannot leak through it by accident. This bounds egress by construction rather than by a redaction regex that someone will eventually get wrong.

Add a test asserting the notifier is called with the report and **not** with the evidence bundle. That test is the guardrail; write it before the provider.

**CLI surface.**

```bash
nettools investigate RR1 10.255.0.12 --flow bgp_session --notify
```

Absent `--notify`, behaviour is unchanged. `NETTOOLS_NOTIFIER=none` makes `--notify` a no-op rather than an error, so the flag is safe in a cron entry before a provider is configured.

**Tests — all mocked, no network.**
- Provider selection from env; unknown provider fails closed with a structured error.
- `none` is a no-op and returns cleanly.
- A provider raising is swallowed; the investigation result is unaffected and the exit code is unchanged.
- The notifier receives the report and not the evidence bundle.
- No token or chat ID appears in any log line or error message.

**Three decisions this task must record, not silently make.** Each is `DECIDE-AND-LOG` at minimum, and the first is `HALT` if this will ever point at production:

1. **Data residency.** Telegram means device names, management IPs and RCA text land on Telegram's servers. Acceptable for `sota-xrd`; likely a policy problem for production, and hard to walk back once the team is used to it. Mattermost is self-hosted and the operator already runs a Docker stack. Record which was chosen and why.
2. **Identity.** Telegram user IDs are not corporate identity, and a group chat grants whoever is added whatever the bot has. Tolerable while the system is read-only. **Not tolerable at Stage 3**, where a procedure can bounce an interface. Record that this task does not solve authorization — it inherits the repo's existing position that `actor` is provenance, not authorization.
3. **Egress.** The chosen provider needs outbound network from wherever `nettools` runs. Confirm this is available from the management network, not just from the workstation.

**Check T-005 first.** Alertmanager ships a Telegram receiver. If Telegram is the chosen channel, part of the Stage 2 plumbing may be configuration rather than code — that discovery changes what this task is worth building.

**Acceptance.** `--notify` delivers a report to the configured channel; a broken notifier cannot fail an investigation; the evidence bundle provably never reaches the notifier; the three decisions are recorded as findings.

**Not in scope.** Inbound commands, approval buttons, per-user identity, message threading, formatting beyond a readable summary. All of that is MVP-1 or later — see `docs/design/interfaces.md`.

---

---

# PART 5 — REPOSITORY LAYOUT FOR REFERENCE MATERIAL

Create this structure in T-009. It exists so Claude Code always knows where to look.

```
ios-xr-nettools/
├── CLAUDE.md                          # add a "Design documents" section linking docs/design/
├── docs/
│   ├── README.md                      # one-screen map of this tree
│   ├── design/                        # WHY — read-only reference, rarely changes
│   │   ├── design-thinking.md         # D1–D20: options, decisions, rationale, growth
│   │   ├── lld-investigation-layer.md # the delta spec this plan executes
│   │   ├── architecture.md            # high-level architecture (pending)
│   │   ├── interfaces.md              # human interaction ladder + the three decisions
│   │   └── glossary.md                # intent vs domain vs flow vs descent (see below)
│   ├── build/                         # HOW — active during the build
│   │   ├── BUILD-PLAN.md              # this file; task statuses updated in place
│   │   ├── FINDINGS.md            # the log book — append only
│   │   ├── discovery-loki.md          # T-004 output
│   │   ├── discovery-alerting.md      # T-005 output
│   │   ├── discovery-l3vpn.md         # T-006 output
│   │   └── capture-manifest.md        # T-007 output
│   ├── devices.md                     # existing, generated
│   ├── REVIEW.md                      # existing
│   └── architecture.drawio            # existing
├── prompts/                           # versioned prompt artifacts (T-026)
└── scripts/
    └── probe_minimax.py               # T-002
```

**`docs/design/glossary.md` is worth writing early.** There is one live terminology collision and it will cause real bugs if it is not written down:

| Term | Meaning **in this repo** | Do not confuse with |
|---|---|---|
| `intent` | A vendor-neutral name for a question: `facts`, `interfaces`, `bgp`, `lldp`, `isis`, `sr` | "intended state" — that is the **config** axis |
| `config` axis | Intended state, from configuration or source of truth | `intent` above |
| `flow` | An object-type-scoped investigation | n8n workflows |
| `descent` | The ordered walk down the protocol dependency stack | The agent loop |
| `rung` | One layer of a descent | A tool or a check |
| `check` | A pure predicate returning `CheckResult` | A `health.py` rule (related, not identical) |
| `finding` | A terminal outcome of a descent | A health `severity` |

---

# PART 6 — FINDINGS LOG BOOK TEMPLATE

`docs/build/FINDINGS.md` and `docs/build/TRACKER.md` are shipped with this pack. If either is missing, recreate it from the shapes below.

```markdown
# Observations Log Book

Append-only record of everything learned during the build of the investigation layer.
Never edit or delete an entry. To correct one, write a new entry referencing the old ID.

Reviewed by: <human>, after Part 4 completes.

---

## OBS-001 · T-001 · <title>

- **Kind:**
- **What happened:**
- **Evidence:**
- **What I did:**
- **Needs human review:**
- **Blocks:**

---

# Open Questions

| ID | Raised in | Question | Blocking? | Status |
|----|-----------|----------|-----------|--------|
|    |           |          |           |        |
```

---

# PART 7 — DEFINITION OF DONE

MVP-0 is complete when all of these hold:

1. Every task T-001 to T-034 is `DONE`, `SKIPPED` with a reason, or `BLOCKED-ACCEPTED` by the human.
   T-035 is an optional fast-follow and may remain `TODO` without blocking the definition of done.
2. `make test` and `make lint` are green.
3. All six frozen safety tests pass unchanged.
4. `nettools investigate RR1 10.255.0.12 --flow bgp_session --from-fixtures` runs with **no lab, no API key**, and produces a descent result naming a rung.
5. The same command without `--from-fixtures` runs against the live lab and produces a grounded report whose every citation resolves.
6. `docs/build/FINDINGS.md` contains at least one entry per task.
7. The Open Questions table is populated and unresolved items are marked.

**Then stop.** Do not begin the gate, narrowing, operational memory, MCP consolidation, or Stage 2. Those are MVP-1 and beyond, and they should be planned after the observations log has been reviewed with the human.

---

# PART 8 — WHAT THIS PLAN DELIBERATELY DOES NOT DO

Recorded so their absence reads as a decision rather than an oversight:

| Not in MVP-0 | Why | When |
|---|---|---|
| The reasoning gate | The descent is deterministic and terminal; narrowing adds a model decision that MVP-0 does not need | MVP-1 |
| Config / intended-state axis | The BGP descent bottoms out on status alone | MVP-1 |
| Operational memory | D14 — Stage 1 has a human present who knows whether it is new | Stage 2 |
| MCP tool consolidation | The only change with real regression risk; do it after the core is proven | After MVP-1 |
| n8n / event triggers | Alertmanager may make it unnecessary — T-005 decides | Stage 2 |
| A chat interface (inbound commands) | MVP-0's interaction is a command, not a dialogue; free-text flow selection does not exist until the gate does | MVP-1 |
| Approval buttons | Needs corporate identity, which Telegram user IDs are not | Stage 3 |
| The remaining five flows | Build one descent properly before building five | After MVP-0 review |
| Juniper | Deliberately deferred; the abstraction is already proven by the declared-but-unverified platform entries | Later |

The governing rule, from the design document: **over-engineering is building *N* of something before validating one.**
