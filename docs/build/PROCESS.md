# Process — Rules of Engagement

Extracted verbatim from `BUILD-PLAN.md`'s **PART 0** on 2026-08-20, ahead of the
v1.0.0 tag, in the same commit that archived the rest of that file. Parts 1–8 of
that plan — the 34-task build list, long since executed — are historical and now
live at [`docs/archive/BUILD-PLAN.md`](../archive/BUILD-PLAN.md). This part is not
historical: it is the process contract the build has operated under since
2026-08-15 — the OBS-numbering convention, the frozen-files list (§0.5), the
escalation ladder (§0.11), the FINDINGS.md entry format (§0.3) — and it stays
live and binding for as long as this repository is under active development.

**Section numbers are unchanged on purpose.** Dozens of comments and docstrings
across `src/`, `tests/` and `scripts/` cite this material as `` `BUILD-PLAN.md`
§0.N`` by number — moving the words to a new file was not an occasion to
renumber them, since every one of those citations would otherwise go stale
along with the file path. Only the location changed; §0.1 is still §0.1.

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

### The agent and the fault injector — amended 2026-08-18 by the operator

**The blanket prohibition is lifted. The agent operates the injector on the lab
fabric.** Authorised explicitly by the operator on 2026-08-18, so that the agent
watches a round's output as it happens rather than reading it afterwards, and
can tighten the acceptance criteria from what it sees.

**What is NOT changed, and must not be read as changed.** `nettools` still never
writes to a device. §0.5's frozen files, §0.6's four invariants and §0.11's HALT
on "anything that would write to, configure, or change the state of a network
device" all apply to the product, unwaived and unweakened. The injector is a
*separate script outside this repository*, operated deliberately against a lab
fabric that exists to be broken. Nothing here licenses a write path in the tool.

**What is genuinely given up, stated plainly.** The original rule had a second
reason that was never about safety:

> Running it puts the fault identity in the agent's context, destroying the
> blinding the trial depends on — not *"this action is dangerous"* but
> **"performing this action makes me a worse witness."** That failure is
> **silent**: a contaminated trial produces exactly the same confident
> agreement a clean one does, and neither the agent nor the reader can tell
> them apart afterwards.

That reason still holds, and it is now **scoped rather than deleted**:

* **Mechanism rounds — the agent may run them.** Rounds 8b and 6 measure whether
  an observable behaves as predicted, and their seals *name the fault in
  advance* (option 7 byte-identical; option 8 with `--max-hold 45`). The agent
  has already read those seals. **There is no blinding left to spend**, so
  running the injector costs the evidence nothing. This is the class the
  amendment was requested for and it is the class it fits.

* **Blind diagnostic trials — a human or a separate process still applies the
  fault.** Where the measurement *is* whether the agent identifies a fault it
  was not told about (the T-033 class that produced Q-006, OBS-076 and OBS-077),
  an agent-operated injector destroys the only thing being measured, and no
  amount of care afterwards recovers it. **An agent cannot un-know a fault.**
  If a future round needs a blind witness, the injector goes back to a human for
  that round — not as a rule being reinstated, but as the measurement's
  precondition, stated in its seal like any other.

**Recorded honestly:** §0.13's own table filed this rule as *"a rule of method,
not a §0.11 safety rule"*, and predicted that a safety framing *"would have been
waived by sound reasoning"*. It has now been waived — by the operator, on the
record, for a reason that is sound for mechanism rounds and would not be for
blind ones. The distinction the table drew is the reason this amendment can be
narrow instead of total.

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

> **Added 2026-08-17 — the same rule, turned on the auditor (OBS-147).** A verification
> pass that reports no findings has two explanations its own output cannot distinguish:
> the thing is sound, or the check did not look. **The discriminator is what the pass
> found wrong with itself.** A real pass exercises its own instruments and finds them
> imperfect, because instruments are. So state what was checked, what the check cannot
> see, and what the pass found wrong with its own apparatus — and if that last answer is
> "nothing", say so explicitly and treat it as grounds for suspicion rather than comfort.
>
> Coverage can be inflated deliberately; finding your own instrument wrong cannot. It is a
> costly signal, which is what makes it worth reading. `BACKLOG-STATUS.md` §7 is the worked
> example: nothing changed state, and the pass is readable only because all three defects
> it found were in the verifier.

Three instances so far, each caught only because something else was watching:

- **T-010 → T-012.** The template-parser registry was empty by design. `test_registry_is_empty_until_the_parsers_land` failed the moment the first parser landed, which was the signal to replace it with the real expectation.
- **T-021.** The `checks`/`health` agreement test passed on its first run with 36 green comparisons — because credentials were absent, every check returned `unevaluated`, and every assertion was trivially satisfied. Caught by an anti-vacuity test in the same file asserting the corpus actually produces both `healthy` and `broken`.
- **T-026.** Two prompt-rule tests skip with no prompts to run against.

The companion takes one of two forms: assert the collection is currently empty (so it fails when populated), or assert the corpus exercises every outcome the test discriminates between. Either is cheap. Neither is optional on a test whose whole job is to catch a regression that has not happened yet.

### A fourth shape: the parameterised test whose parameters cannot disagree

Added 2026-08-17. **Parameterising a test is not the same as widening it**, and the difference is invisible in a green run.

> **A parameterised test is vacuous if all of its parameters can produce the same result.**

The instance. A test asserted that subject-scoped rungs resolve to *the subject's* device, parameterised over both directions of one session — `RR1 → PE2` and `PE2 → RR1`. That looks like coverage of the resolution rule, and it is not: **a resolver that returned the local device for everything satisfies both parameters.** Each case would compute its own "expected" from the same broken source and agree with itself.

The parameterisation widened the *inputs* and not the *discrimination*. What made it mean something was a companion asserting the two directions **genuinely disagree** — that `igp_adjacency` is `PE2` one way and `RR1` the other — which is false under the degenerate resolver and true only when resolution actually resolves.

This generalises past resolvers. Whenever a test is parameterised over cases that are *supposed* to differ, ask what a degenerate implementation would return for all of them. If a single constant satisfies every case, the parameter list is decoration:

- a check parameterised over `healthy`/`broken` fixtures, where a stub returning `unevaluated` passes both;
- a parser parameterised over platforms, where returning an empty parse satisfies each;
- a diff parameterised over intents, where "no change" is correct for every one on a quiet corpus.

**The companion is the same shape as the existing two, and it is the cheap half:** assert that the parameters produce *different* results, not merely that each produces the expected one. One extra test, and it is the one that fails when the discrimination is lost.

---

## 0.13 Evidence bounds conclusion, and the bound is invisible from inside

> **Evidence bounds conclusion, and the bound is invisible from inside.**
>
> - a survey is a sample — *(data)*
> - a rule generalised from one instance fits one instance — *(rules)*
> - a test sharing the implementation's premise confirms it — *(tests, and see the two forms below)*
> - a corpus shows width only in dimensions where it varies — *(identity)*
> - a demo verified in the developer's environment verifies the environment — *(setup)*
> - **agreement among re-derivations is not evidence that re-deriving was
>   unnecessary** — *(duplication)*
> - **a procedure can be followed exactly and produce nothing, when one word
>   in it is underspecified** — *(procedure)*
>
> **Before trusting any of the seven, ask what the evidence could not have shown you.**

One family, seven faces. Each has cost this build real time, and in every case the artefact was internally consistent — which is why the question has to be asked deliberately rather than noticed.

**The buffer-level trap is the same family.** `show logging` returns the device *buffer* (level `debugging`, severities 0–7); the *trap* level governs what is shipped to the collector (`informational`, 0–6). Reading the trap level to describe local coverage **understates the source by exactly the class in question**, while looking entirely correct. The evidence — a header line stating a level — cannot show you that you read the wrong line.

### The seven faces, with their standing examples

| Face | Standing example | Fix |
|---|---|---|
| **Data** — a survey is a sample | T-013: 44 route fixtures grouped by line 4, which is identical across all three route shapes. A directly-connected route has no next hop at all | Survey for the *shape*, then let §0.10 line accounting fail the spec that missed one |
| **Rules** — one instance fits one instance | T-026: "every prompt names its refusal path as `undetermined`" — `report`'s refusal, not `correlate`'s | Move the value into data the instance declares |
| **Tests** — a shared premise confirms itself | T-028: **sixteen green tests** over a filter deleting eight severity-3 records unattributed. The suite and the defect shared an author and a premise | Specify independently of the code, and read the specification against the implementation |
| **Identity** — width only where the corpus varies | T-029a: `refusal_marker` moved into data and keyed by *prompt family*, then failed again when a second **version** appeared | Key by the finest identity the thing has — a filename, not a family name |
| **Setup** — the environment verifies itself | T-031: `--from-fixtures` was checked by hand a dozen times and "needed no credentials", because `main()` loads this repo's `.env` and it holds real ones. The verification environment was contaminated by the thing being verified | Strip the environment in a test, and in CI. A person cannot easily un-know their own `.env`; a clean container can (**B-423**) |
| **Duplication** — consistent duplicates read as correctness | B-460: `St/PfxRcd` holds either a prefix count or a session state, and three consumers each recovered the discriminator with `_is_numeric` — identically, agreeing, for eight phases | Count the sites reconstructing a fact, not the ones disagreeing. Split at the last point the discarded information is still observable |
| **Procedure** — every step succeeds and the outcome is void | OBS-131: §6.1d said *archive the full payload*. Files written, directory created, copy made, commit run — and `.gitignore`'s `*.jsonl` dropped every sample. The archive was empty | Check for the **end state**, not the steps. Name the state in the rule: `committed`, not `archived` |

The **setup** face is the one most likely to be dismissed as an operations detail. It is not: it is the only face where the contaminating evidence is *outside the repository*, so no amount of reading the code or the tests reveals it. The fix has to be an environment, not an inspection.

##### A second route into the setup face: the correction that confounds its own experiment

Added 2026-08-17. The setup face was filed as a property of *environments* — a contaminated `.env`, a developer's machine. It has a second route, and it arrives from the opposite direction.

> **A fix can confound the experiment that justifies it.**

The case. One tool description was written differently from the other twenty, a model selected it, and its trace named the description as the reason. The remedy was to reword all twenty-one — and the instinct while doing it was to **leave the original one untouched**, on the reasoning that it is the evidence and should not be disturbed.

That instinct is exactly backwards. The prediction being tested is *"selection survives rewording all 21"*. Leaving one in a structurally distinct form preserves the very contrast the prediction exists to distinguish from content, so the measurement would have been confounded **by the fix**, not by the environment and not by the measurement design.

It is the setup face because the contaminating condition is neither in the code nor in the test nor in the data — it is in the *state of the world the measurement will be taken in*, arranged by the repair. And it is invisible for the familiar reason: preserving evidence is normally correct, so the instinct that produces it feels like rigour.

**The check:** after designing a fix, ask what the fix does to the conditions of the measurement that will judge it. If the fix changes the thing being measured, sequence them — measure, then fix, then measure again — or accept that the second measurement answers a different question.

#### The procedure face — a word that names an intent rather than a state

Added 2026-08-17 from OBS-131.

> **A procedure can be followed exactly and produce nothing, when one word in it is underspecified. Every step is performed, every step succeeds, and the outcome is void. The word looks unambiguous to whoever wrote it, which is why it survives review — *archive*, *save*, *record*, *publish* all name an intent rather than a state.**
>
> **Specify the observable end state, not the action: *committed*, not *archived*.**
>
> **Detector: after a procedure completes, check for the END STATE, not for the steps.**

The case. §6.1d required *"archive the full payload"* and it was followed — the harness wrote its files, the directory was created, the copy into `evidence-archive/` was made, the commit ran and reported success. Every step performed, every step succeeded. The archive contained two `verdict.json` files and **zero samples**, because a blanket `*.jsonl` excluded them.

Nothing detected it, and nothing could have, because the detector everyone reaches for is *"did the steps run"* — and they did.

**Why the word survives review.** *Archive* felt precise when it was written, and it is precise about the *intent*. It says nothing about which of several end states counts — written to disk, copied to a directory, tracked by git, pushed to a remote — and the author had one in mind while the reader had another. The same is true of *save* (to memory? to disk? durably?), *record* (in a log nobody reads?) and *publish* (built? deployed? reachable?).

**The remedy is one word.** A rule that says `committed` can be checked with `git ls-files`; a rule that says `archived` can only be checked by asking what someone meant.

#### And a detector note, because this one is unlike the rest of the family

> **A `.gitignore` match is not an event, it is the absence of one. Configuration that works by *refusing to act* produces no signal when it refuses wrongly. Nothing can warn, because nothing happens.**
>
> **The only detection is to check what is *tracked* rather than what was *written* — and that check has to be deliberate, because the failure looks identical to success at every point before it.**

Every other face in this family is detected by looking harder at something that exists: a corpus, a test, a rule, a duplicate. This one has nothing to look at. `git add` reported success, the commit reported success, the files were on disk. The only difference between the working and broken cases is a set of paths that were **never mentioned**, and absence has no line number.

The class is wider than `.gitignore`: an allowlist that silently drops an entry, a filter matching more than intended, a `.dockerignore`, a `MANIFEST.in`, a log level suppressing the line that mattered. **Anything whose contribution is a non-action cannot report a mistaken non-action.**

#### A second instance, from round 8 — the underspecified word can be a *noun*

Added 2026-08-17. OBS-131 was fixed and the fix held: round 8's payload was committed, `git ls-files` lists it, the end state was checked rather than the steps. **And the round still could not be rescored.**

The remedy above replaced *archive* with *committed* — it specified the **verb**. §6.1d's other underspecified word is the **noun**: *"archive the full payload"*. Round 8 archived the `investigate` payload, which is a structure of already-parsed fields. When the disputed field turned out to be a parser defect, the archive held 355 copies of the broken parse's output and not one copy of the line it was computed from.

> **Specifying the end state fixes *whether* the artefact exists. It says nothing about *what the artefact is*, and a procedure has two ways to be void.**

Both are the same face: a word that looked unambiguous to its author, every step performed, every step successful, the outcome void. What makes this instance worth recording separately is that **the first remedy was in place and working**. `git ls-files` answered its question correctly; the question was the wrong one.

**The generalisation, and the detector it needs.** *Payload*, *evidence*, *result*, *record*, *sample* all name a boundary that the author fixes implicitly and the reader re-fixes on their own terms. And the boundary that matters is not where any module draws it — it is **wherever the next dispute lands**, which by definition is not known when the rule is written.

> **Detector: for a stored artefact, ask what a future dispute would need — not what the current consumer reads. If the answer is "the thing this was derived from", the artefact is one layer too high.**

The cheap general form: **when in doubt store one layer lower than seems necessary.** A raw line beside a boolean costs bytes; the round it would have saved costs a lab window and an operator's evening.

#### The duplication face — a detection blind spot rather than an instance of another

Added 2026-08-17 from B-460, and filed as its own face because the *detector* differs from every other one here.

> **Agreement among re-derivations is not evidence that re-deriving was unnecessary. Consistent duplicates read as correctness, and the more sites that re-derive the same discriminator, the more consistent and the more wrong. Look for a value being reconstructed in more than one place, not for the places disagreeing.**

The case. `show bgp summary`'s `St/PfxRcd` column holds *either* a prefix count *or* a session state, and the parser stored whichever appeared in one field. Three consumers in `checks.py` each recovered the discriminator with `_is_numeric(state)` — **identically, and they agreed.** Nothing looked wrong for eight phases, and the agreement is exactly why.

**Why the usual instruction fails here.** B-431's rule was *a filter defined three times is three filters*, and its detector was divergence — "these two copies will disagree the first time someone edits one." That is true and it is not what happens first. What happens first is that they *keep* agreeing, indefinitely, because they were written from the same understanding on the same afternoon. Waiting for divergence is waiting for the second defect to reveal the first.

So the check is structural rather than comparative: **count the sites that reconstruct the same fact.** More than one is the finding, whatever they currently return. And the fix has a canonical location — *the last point at which the discarded information is still observable*, which for a parsed field is the parser.

**A second form, and it is the dangerous one: a *safety* flag duplicated across modules.**

`round5.py` and `round7.py` each define `_dry = False` and set it from `--dry-run`. `fault_lab.push()` guards on `fault_lab._dry_run`, which they never touch. At the call site the two are indistinguishable:

```python
_dry = args.dry_run          # the caller's flag
...
status = push(conn, APPLY, "apply")   # reads the callee's, still False
```

**Both modules hold what should be one value, and they agreed for as long as nobody looked** — which is the duplication face exactly. What makes this instance worse than three `_is_numeric` calls is the direction of failure: a re-derived discriminator that diverges gives a wrong answer, while a safety flag that diverges **pushes configuration to a production device during a run labelled "dry".**

The tell is the same and so is the remedy: **count the places holding the value.** One flag, owned by the module that acts on it, read by everything else through a function — never a module global set by a caller who does not own it.

Note what the duplication face shares with the others and where it parts company. Like **tests**, the artefact is internally consistent; unlike tests, there is no premise to specify independently, because every copy is correct. Like **rules**, it is about a generalisation; unlike rules, no instance is wrong. It is the one face where *nothing anywhere is incorrect* and the defect is entirely in the shape.

The **tests** face deserves the extra sentence, because it is the one that cannot be caught by looking harder at the artefact: *green tests are not by themselves evidence that a component is correct — only that it agrees with the assumption it was built on.* Where a component encodes a judgement about the world, specify it independently.

#### The tests face has two forms, and code review catches one of them

Added 2026-08-17, after the second form appeared and was very nearly pinned as expected behaviour.

| Form | What is wrong | What a code review sees |
|---|---|---|
| **Test agrees with a defective implementation** | the code | a defect, if the reviewer is careful — the artefact and the test are both in front of them, and the artefact is wrong |
| **Test agrees with a false *description* of a correct implementation** | **nothing in the code** | **a correct implementation and a passing test.** There is no defect to find |

T-028 is the first form: sixteen green tests over a filter that really was deleting records.

The second form is harder and this build has now produced one. A test asserted `igp_adjacency == "PE2"`, written from a mistaken account of which investigation had run — true for `RR1 → 10.255.0.12`, false for the `PE2 → 10.255.0.31` case being discussed. **The code was never wrong.** The descent resolved correctly, the payload said so, and the test agreed with a wrong story about it and passed.

> **When a test encodes a specification rather than an observation, reviewing the code cannot falsify it — there is no defect there. Only re-reading the specification against the record can.**

The tell is a test whose expected value came from a *description* of a run rather than from the run's own output. The remedy is the one this section already prescribes for the other faces, pointed at the specification instead of the implementation: derive the expectation from the artefact (the payload, the fixture, the recorded result), or parameterise so that no single constant can satisfy it — see §0.12's fourth shape, which is how this instance was actually closed.

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

### Absence, not a sentinel, where a downstream check keys on a value

Added 2026-08-17 from B-460. A rule of its own because it is a *repair* hazard: it fires while you are fixing something else.

> **Where a downstream check keys on a specific value, emitting a placeholder in place of "not applicable" manufactures a second defect while fixing the first. Absence is the structural form; a sentinel is the remembered form.**

The worked example. Splitting `St/PfxRcd` into `session_state` and `prefixes_received`, the obvious shape is to always emit both — `prefixes_received: 0` when the session is not Established. It reads as tidy and it is a measurement nobody took.

And it has a consequence, not merely an inaccuracy: `health.py`'s `bgp_no_prefixes` rule fires on `prefixes_received == 0`. A zero for an Idle session would make **every down session also report *"Established with 0 prefixes received"*** — a fabricated second finding stapled to a real fault, on exactly the devices an operator is already looking at.

Absence makes that impossible. A sentinel makes it something the rule has to remember not to do, and this build's whole position on remembered rules is that they are eventually not remembered.

The general check, cheap enough to run while writing a parser: for each field you are about to emit, ask **who keys on a specific value of it**, and whether the value you would emit for "not applicable" is one of those keys. If it is, absence is not the tidier option, it is the only correct one.

This is the same rule Phase 3 already applies without naming it: `router_id` and `local_as` are *absent* for the four devices with no BGP process rather than zero, because the fixtures literally answer `% BGP instance 'default' not active` and asserting a zero would be a lie the evidence contradicts.

### Truncation is a filter; classification is containment

Added 2026-08-17 from B-458, and it is the **third** instance of one move.

> **A filter lets the dangerous value pass through the function and removes some
> of it. A construction assembles the output from values already trusted, so the
> dangerous value is never in it.**

| Instance | The filter that was rejected | The construction that replaced it |
|---|---|---|
| OBS-061 | redact device text from a prompt | `prompt_library` takes a `DescentResult` and **cannot** hold device text |
| OBS-106 | remember not to expose a write tool | `mcp_server` does not **import** a write function |
| **B-458** | truncate an error string at 400 characters | each error is **rebuilt** from a command we rendered plus a phrase from a declared table |

The B-458 case is the clearest because the filter was measurably insufficient
rather than merely fragile: netmiko 4.7 interpolates `output={repr(output)}`
into one exception message, so a 400-character cap passed up to 400 characters
of device output to a model. The cap was not a weak fix, it was **the wrong
kind** of fix.

**The test for which you have:** ask whether the function ever *holds* the
dangerous value. If it does and then trims it, it is a filter. If it never
receives it, or discards it and builds from elsewhere, it is containment.

### A documented limit is a decision that expires, not a state

The second half, and the one to carry to every remaining residual.

B-458 was filed honestly as *"bounded rather than claimed clean"*. That honesty
is why it was findable — and it is also why it sat. **A bound recorded as
acceptable becomes a filter nobody revisits**, because the record reads as a
decision already taken rather than a question still open.

The measurement that made it urgent took four minutes: reading the library's
raise sites for interpolation. Nobody did it for months, because the item said
the limit was known.

> **Audit every stated residual on the same question: is the bound still the
> right *kind* of answer, or was it the honest description of a filter?**

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
| "Frozen-release audit governance" (OBS-105) | **tooling for** the one-shot audit set | **the definition of** the one-shot audit set | Deferring it while building the set would have shipped a development set labelled one-shot. Access control is the *only* thing separating the three datasets |

Note what all four have in common: **the statement was accurate and the action it licensed was wrong.** No amount of re-checking the evidence corrects a misclassification, because the evidence supports the statement. Only asking what kind of claim it is does.

The fifth was found in `peer-review-response.md`'s own deferral table — **a document written to apply this section committed it while doing so**, which is worth more than the entry. A misclassification is not a thing you check for once and clear; the table above is a list of shapes, not a list of closed cases. It also adds a check the other four did not need:

- **A deferral** — is the deferred thing the subject's *support*, or is it the subject? Deferring the second does not delay the work, it deletes it while leaving the name.

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

## 0.16 A turn ends with a commit hash, or with "nothing was done"

> **Never end on an intention.**

Recorded 2026-08-17 after it happened twice in one session. Two turns closed with *"Starting X now"*, no work followed, and it surfaced only because the operator asked whether something was running in the background.

**Why it is worth a rule rather than more care.** In a transcript, a turn ending on an intention is **indistinguishable from a turn ending on completion**. Both read as forward motion; both are followed by the operator's next message. `TRACKER.md` would not have caught it either — nothing was marked done, because nothing was done, and an absent row looks exactly like a row not yet reached.

So the failure has no detector. The reader cannot see it, the tracker cannot see it, and the person who produced it experiences it as having decided to do the work — which is the same confusion OBS-114 describes, an intention recalled as an action.

**The rule is mechanical, for the same reason OBS-114's is.** Attentional rules do not survive their author; this one is checkable by a reader with no context:

- a turn that did work ends with **a commit hash**;
- a turn that did no work says so **explicitly** — "no code changed", "recorded only", "blocked on X";
- *"starting now"*, *"proceeding with"*, *"next I will"* are not endings. If they appear, the work happens in that turn or the turn says it did not.

The corollary matters as much: **planning is work and may legitimately be a whole turn** — a design brought for approval, an audit reported before changes. Those end with what was produced. What is forbidden is not the short turn; it is the turn whose only content is a promise about the next one.

---

### The three tracking documents

> **Note added 2026-08-20, not part of the original extraction.** This table
> describes the MVP-0-era document set, both now archived (`docs/archive/BUILD-PLAN.md`,
> `docs/archive/TRACKER.md`) along with the task list they tracked. Kept verbatim
> below because the rule it states — the plan and the tracker must agree, and the
> tracker wins — is still the right rule; the live equivalent is `BACKLOG.md`
> (status) beside `FINDINGS.md` (append-only log), with no separate task-status
> file since there is no longer a fixed task list to track against.

All three live in `docs/build/` and are maintained continuously, not at the end.

| Document | Nature | Updated |
|---|---|---|
| `BUILD-PLAN.md` | The plan. Task specs. | Status field only, in place, per task |
| `TRACKER.md` | Progress. What is done, what is running, what it cost. | After every task |
| `FINDINGS.md` | Everything learned. Append-only. | Whenever §0.3's triggers fire — often mid-task |

A task is not `DONE` until all three reflect it. If the plan says a task is done and `TRACKER.md` disagrees, `TRACKER.md` is authoritative and the discrepancy is itself a finding.
