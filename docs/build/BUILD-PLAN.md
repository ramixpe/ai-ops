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

On HALT: mark the task `BLOCKED`, write a finding with `Needs human review: yes`, add a row to the Open Questions table in `FINDINGS.md`, update `TRACKER.md`, and **stop**.

### DECIDE-AND-LOG — choose, record, continue

Technical choices with a defensible default and no safety consequence. Make the call, write it down, keep moving.

Examples: an error-counter threshold, a fixture filename convention, whether a `meta` field is a string or an int, which of two TTP structures to use, how to name an internal helper.

On DECIDE-AND-LOG: implement the choice, write a finding with `kind: decision-made` stating the options considered and why this one, set `Needs human review: yes` if it is load-bearing, and continue.

### NOTE — record, continue

Observations outside the current task's scope. A smell in existing code, a docstring that is now wrong, a test that passes but looks weak, a dependency version worth watching.

On NOTE: **do not fix it.** Write the finding, continue. Out-of-scope fixes are how a sequential plan turns into an unreviewable diff.

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

## T-003 · Wire MiniMax as a provider `[STATUS: TODO]`

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

## T-004 · Loki discovery `[NON-BLOCKING]` `[STATUS: TODO]`

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

## T-005 · Alertmanager and Prometheus discovery `[NON-BLOCKING]` `[STATUS: TODO]`

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

## T-006 · L3VPN discovery `[STATUS: TODO]`

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

## T-007 · Fixture gap analysis `[STATUS: TODO]`

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

## T-008 · Parser library decision `[STATUS: TODO]`

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

## T-009 · Docs scaffold `[STATUS: TODO]`

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

## T-010 · `template_parsers.py` skeleton and contract `[STATUS: TODO]`

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

## T-012 to T-017 · The parsers `[STATUS: TODO]`

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

## T-018 · Attach parsed data to `run_template` `[STATUS: TODO]`

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

## T-019 · `checks.py` and `CheckResult` `[STATUS: TODO]`

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

## T-020 · The five checks `[STATUS: TODO]`

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

## T-021 · The agreement test `[STATUS: TODO]`

**Goal.** Let `checks.py` and `health.py` coexist safely without refactoring either.

**Steps.**
Create `tests/test_checks_agree_with_health.py`. For every fixture device at `t0` and `t1`, for each check with a corresponding health rule:

- Neither may say `healthy` where the other says `broken`.
- One may be `unevaluated` where the other is not — that is allowed.
- Any genuine disagreement fails the test.

**Acceptance.** Green across all nine devices × two snapshots.

**If it fails**, that is a real finding, not a test to loosen. Log an observation with the exact disagreement and mark `BLOCKED`.

---

## T-022 · `flows.py` — registry and dataclasses `[STATUS: TODO]`

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

## T-023 · The `bgp_session` descent `[STATUS: TODO]`

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

## T-024 · `descent.py` — the walker `[STATUS: TODO]`

```python
def run_descent(flow: Flow, device: str, subject: str, *, collector) -> DescentResult
```

Semantics, exactly:

- `healthy` → continue to the next rung.
- `broken` → **stop.** That rung's `finding` is the result. No deeper rung is collected or checked.
- `unevaluated` → **stop** with `finding="undetermined"` and the reason recorded.
- Descent exhausted with every rung healthy → `all_layers_healthy`.

Returns the rung path taken, each `CheckResult`, and the accumulated evidence keys.

**No model call anywhere in this module.** If you find yourself wanting one, stop and log a blocker.

The `collector` is injected so a descent can run against `fixtures.load_fixture_evidence` with no lab access. That is what makes T-025 possible.

**Tests:** stops at first broken; never collects below the stop (assert on collector call count); `unevaluated` halts rather than descending.

---

## T-025 · The acceptance test `[STATUS: TODO]`

**This is the milestone that proves the architecture.**

```
Given  fixture evidence for RR1 at t0
When   run_descent(bgp_session, device="RR1", subject="10.255.0.12")
Then   the descent stops at a named rung
And    the finding is a member of bgp_session.findings
And    no rung below the stopping rung was collected
And    every CheckResult carries the evidence keys it read
And    no model call occurred
```

The last two clauses are the point.

Add the mirror case: `subject="10.255.0.11"` — an established peer — must yield `all_layers_healthy` or stop at a rung with a defensible reason.

**Acceptance.** Both pass offline, with no lab and no API key.

**Record in observations:** which rung stopped, and whether that matches what a network engineer would conclude by hand from the same fixtures. If it does not, that is the single most important finding of the whole build.

---

# PART 4 — PROMPTS, GROUNDING, AND MVP-0

---

## T-026 · Prompt library scaffold `[STATUS: TODO]`

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

## T-027 · The `report` prompt `[STATUS: TODO]`

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

## T-028 · The `correlate` prompt `[STATUS: TODO]`

**Input:** a finding plus the log window filtered to the subject.
**Output:** a timeline linking the finding to log events, or an explicit "no correlating events in window".

This is the model's genuine contribution per the design: the descent says *what* is broken; correlation says *when it changed and whether it followed a commit*.

**Blocked on T-004** if logs come from Loki. If Loki is unavailable, use the `logging` template output from T-015 and log an observation that the source is provisional.

---

## T-029 · `grounding.py` `[STATUS: TODO]`

```python
def check_grounding(report: dict, evidence_keys: frozenset[str]) -> GroundingResult
```

- Every observation's `evidence_key` must be in `evidence_keys`.
- Every interpretation's `based_on` must reference existing observations.
- Recommendations are exempt from citation but must carry `requires_human: true`.

**A failed grounding check means the report is not emitted.** The run returns the descent result plus the grounding failure — never the model's prose.

**Tests:** an invented evidence key fails; a dangling `based_on` fails; a valid report passes.

---

## T-030 · `investigation.py` — the MVP-0 runner `[STATUS: TODO]`

```python
def investigate(device: str, subject: str, *, flow: str, collector=None) -> InvestigationResult
```

Order: resolve scope → run descent → correlate (model) → write report (model) → grounding check → emit.

**No gate and no narrowing pass in MVP-0.** The descent is deterministic and terminal. The gate is MVP-1.

`agent_loop.py` is untouched and remains the general-purpose bounded loop for questions that do not map to a flow.

---

## T-031 · CLI wiring `[STATUS: TODO]`

```bash
nettools investigate RR1 10.255.0.12 --flow bgp_session
nettools investigate RR1 10.255.0.12 --flow bgp_session --from-fixtures
nettools investigate PE1 GigabitEthernet0/0/0/1 --flow interface --format summary
```

Follow the existing conventions exactly: `--format json|table|summary`, `--quiet`, and the project-wide exit-code scheme (`0` nothing actionable, `1` reports a problem, `2` could not run / worst outcome).

`--from-fixtures` must work with **no lab and no API key**, skipping the model steps and emitting the descent result alone. That is the demo that proves the deterministic core.

---

## T-032 · End-to-end offline test `[STATUS: TODO]`

Full pipeline against fixtures with the model mocked. Assert: descent runs, report shape is valid, grounding passes, exit code correct, no network calls.

---

## T-033 · Live lab run `[STATUS: TODO]`

Add to `tests/test_live_lab.py` under the existing `live_lab` marker, self-skipping unless `NETTOOLS_LIVE_LAB=1`.

Then run manually against the real fabric with MiniMax configured, and record in observations: the rung reached, wall-clock time, token usage, whether the report's citations all resolved, and — most importantly — **whether a network engineer would agree with the conclusion.**

---

## T-034 · Documentation update `[STATUS: TODO]`

Update `README.md` (new CLI surface, GRACE, the prompts directory), `CLAUDE.md` (new modules, where they sit in the layer model), and `.env.example` (MiniMax variables).

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
