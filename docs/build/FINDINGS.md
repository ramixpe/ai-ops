# Findings Log Book

Append-only record of everything learned during the build of the investigation layer.

**Rules**

1. Never edit or delete an entry. To correct one, write a new entry that references the old ID.
2. Number entries sequentially from `OBS-001`.
3. Write an entry whenever any of the following is true:
   - Something in `BUILD-PLAN.md` turned out to be wrong, incomplete, or based on a false assumption.
   - Real device or fixture output did not match what the plan predicted.
   - A decision had to be made that the plan did not specify.
   - Something was implemented in a way you are not confident about.
   - A test passes but you suspect it does not really test the thing.
   - You noticed a defect, smell, or risk outside the current task's scope. **Do not fix it — log it.**
   - A dependency, version, or environment detail differed from expectation.
   - You wanted to change a file frozen by `BUILD-PLAN.md` §0.5.
4. Anything with `Needs human review: yes` must also appear in the **Open Questions** table at the bottom.
5. This log is reviewed with the human after Part 4 completes, before any MVP-1 work begins.

**Kinds:** `assumption-wrong` · `surprise` · `decision-made` · `risk` · `defect` · `deferred` · `environment` · `consultation`

`consultation` records a Fable 5 second opinion: the question asked, the answer received, and what Opus 5 did with it. Per `BUILD-PLAN.md` §0.9, an unlogged consultation is an unreviewable decision.

**Escalation.** Every finding carries the level it resolved to under §0.11: `HALT`, `DECIDE-AND-LOG`, or `NOTE`.

---

## OBS-000 · T-000 · Log book created

- **Kind:** environment
- **What happened:** Log book initialised from the template in `BUILD-PLAN.md` §PART 6. No build work has started.
- **Evidence:** This file.
- **What I did:** Created the file. Delete this entry's placeholder status once `OBS-001` is written.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-001 · T-000 · Documentation pack was not installed at first launch

- **Kind:** environment
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `BUILD-PLAN.md` §0.8 and the per-task loop both assume `docs/design/` and `docs/build/` already exist inside the repository. They did not. The pack was staged in a separate folder alongside the checkout, and the first session was launched from that folder rather than from the repo, so the required-reading paths did not resolve and there was no `CLAUDE.md`, no source tree, and no `Makefile` at the working directory. The pack has since been installed into the checkout and committed.
- **Evidence:** Commit `6629a2c docs: add design and build documentation pack`. `docs/design/{glossary,design-thinking,lld-investigation-layer,interfaces}.md` and `docs/build/{BUILD-PLAN,TRACKER,FINDINGS}.md` now resolve. The upstream staging copy remains at `/home/rami/ai-agent-ops/docs-source/` and is not authoritative.
- **What I did:** Nothing to the plan. Verified the installed copies are byte-identical to the upstream pack (`cmp` across all four design docs and `BUILD-PLAN.md` — all identical) so the reading done against the staged copies is valid. Recording the sequencing gap because the plan has no T-000 and therefore no home for "get the plan into the repo".
- **Needs human review:** no
- **Blocks:** none

---

## OBS-002 · T-000 · `.gitignore` silently excluded `docs/build/`

- **Kind:** defect
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `.gitignore` line 17 was an unanchored `build/` rule, intended for the stale `build/lib/` artifact directory described in `CLAUDE.md`. Unanchored gitignore patterns match at *any* depth, so the rule also excluded `docs/build/`. `BUILD-PLAN.md`, `TRACKER.md` and `FINDINGS.md` were silently dropped from the first documentation commit while `git add` reported success and exited zero. The failure is silent by construction: git does not warn when an `add` matches an ignored path unless `-f` is used.
- **Evidence:** `.gitignore` line 17, now reading `/build/`. The three files are present in the tree at commit `6629a2c`.
- **What I did:** Anchored the rule to `/build/` so it matches only the repository-root artifact directory. Logging it rather than treating it as closed, because the same class of silent exclusion could hit `tests/fixtures/` at T-011 — fixture capture writes many new files at once, and an ignored path there would mean parsers are later written against fixtures that exist locally and not in the commit. Mitigation at T-011: verify with `git status --ignored` and confirm the expected file count in the commit rather than trusting `git add`'s exit code.
- **Needs human review:** no
- **Blocks:** none — but see the T-011 mitigation above.

---

## OBS-003 · T-001 · Baseline is green, and matches the documented counts exactly

- **Kind:** environment
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Baseline verified at the repository's current on-disk location after the move and venv rebuild. `make test` → **550 passed, 4 skipped, 0 failed** in 4.42s. `make lint` (`ruff check .`) → **All checks passed!**, exit 0. The 550+4 total of 554 matches `CLAUDE.md`'s documented count exactly, and all four skips are the `live_lab` tier self-skipping for want of `NETTOOLS_LIVE_LAB=1` — the one skip class `BUILD-PLAN.md` T-001 explicitly permits. Nothing is red and nothing is skipped for an undocumented reason.
- **Evidence:**
  - Baseline commit: `6629a2c059d0e848c58b1ef279a063a91f2322c8` (`docs: add design and build documentation pack`), branch `feat/investigation-layer`.
  - Working tree at baseline: clean except `docs/build/FINDINGS.md`, modified by this run's own OBS-001/OBS-002 entries.
  - Skip reason (`pytest -q -rs`): `SKIPPED [4] tests/test_live_lab.py:37: Live-lab tests need NETTOOLS_LIVE_LAB=1 plus a reachable lab and real DEVICE_USERNAME/DEVICE_PASSWORD`.
  - Package version: `agent_nettools.__version__` = `0.2.0`; installed dist `agent-nettools` = `0.2.0`.
  - Repository path: `/home/rami/ai-agent-ops/ios-xr-nettools` (moved before this session; venv rebuilt in place, `which python` resolves inside `.venv`).
  - Environment: Python **3.13.11** (Anaconda build, GCC 14.3.0), `Linux-6.8.0-136-generic-x86_64-with-glibc2.39`, x86_64. `pip list` → 63 packages. ruff 0.16.3, pytest 9.1.1.
- **What I did:** Judged T-001 accepted. Recorded the counts above as the reference the rest of the plan measures against: **any future run showing fewer than 550 passed, or a skip outside `tests/test_live_lab.py`, is a regression introduced by this build and not pre-existing.**
- **Needs human review:** no
- **Blocks:** none

---

## OBS-004 · T-001 · Local interpreter is Python 3.13; CI pins 3.11

- **Kind:** environment
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** The rebuilt venv runs Python 3.13.11, while `.github/workflows/ci.yml` pins `python-version: "3.11"`. `pyproject.toml` declares `requires-python = ">=3.10"`, so both are inside the supported range and neither is wrong. But the green baseline recorded in OBS-003 is evidence about 3.13 only — it is not evidence that the suite is green on the version CI will actually run.
- **Evidence:** `python -VV` → `Python 3.13.11 | packaged by Anaconda, Inc.`; `.github/workflows/ci.yml:14` → `python-version: "3.11"`; `pyproject.toml:10` → `requires-python = ">=3.10"`.
- **What I did:** Nothing — logged only, per §0.11 NOTE. Flagging it now because this build adds a parsing library at T-008 and six parsers at T-012–T-017. A dependency that resolves to different versions on 3.11 and 3.13, or parser behaviour that depends on a stdlib change between them, would surface as a CI-only failure well after the code was judged accepted locally. The cheap mitigation, if it ever bites, is one 3.11 run before the final commit rather than a matrix.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-005 · T-002 · MiniMax contract verified — `reasoning_split` works, Q-001 resolved

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** All six checks ran against the live endpoint. **Acceptance is met: checks 1, 2 and 4 pass.** The plan's central worry is confirmed as real *and* solved: by default the model does leak reasoning into `content` inside `<think>` tags and the result does **not** parse as JSON; with `"reasoning_split": true` the tags are gone, the content parses as JSON, and the reasoning is returned separately.

  | # | Check | Verdict | Result |
  |---|---|---|---|
  | 1 | Auth and reachability | **PASS** | HTTP 200, content returned |
  | 2 | Model ID `MiniMax-M3` accepted | **PASS** | `model='MiniMax-M3'` echoed back |
  | 3 | `<think>` leakage, default | INFO | **`<think>` PRESENT**, `parses_as_json=False` |
  | 4 | `reasoning_split: true` suppresses it | **PASS** | no `<think>`, `parses_as_json=True`, reasoning in `reasoning_content`/`reasoning_details` |
  | 5 | Determinism at temperature 0 | INFO | **5/5 byte-identical**, 1 distinct value, 0 failures |
  | 6 | Tool calling | INFO | `tool_calls` returned: `['get_bgp_neighbor_state']` |

  Check 3's content was literally `'<think>The user wants me to return only the JSON object…</think>\n\n{"decision":"narrow",…}'` — a leading think block followed by the correct JSON. A naive `json.loads` on that raises; a naive "find the first `{`" would work today and break the first time the reasoning text itself contains a brace. Neither is needed, because check 4 passes.
- **Evidence:** `scripts/probe_minimax.py`, exit 0, `ACCEPTANCE: met`. Prompt for checks 3–5 is the verbatim one from `BUILD-PLAN.md` T-002, kept as a module constant so a re-run stays comparable.
- **What I did:** Judged T-002 accepted. **Resolved Q-001: no `<think>`-stripping step is needed** — `reasoning_split: true` is sufficient and must be set explicitly on every call in T-003. Two consequences recorded for downstream tasks: 5/5 determinism at temperature 0 means the gate's typed-decision output is reproducible enough to golden-test directly (T-026/T-027), and working tool calling removes the constraint the LLD §10.3 flagged for MVP-1 — though MVP-0 needs none of it.
- **Needs human review:** no
- **Blocks:** none — unblocks T-003.

---

## OBS-006 · T-002 · Reasoning is billed to `max_completion_tokens`, and a small budget returns empty content with no error

- **Kind:** risk
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Check 1 was written with `max_completion_tokens: 16` per the plan, and its content came back as a *truncated `<think>` fragment* rather than an answer. A follow-up measurement at four budgets, all with `reasoning_split: true`, shows why: reasoning counts against the completion budget even when it is split out of `content`.

  | `max_completion_tokens` | `finish_reason` | `completion_tokens` | `content` |
  |---|---|---|---|
  | 16 | `length` | 16 | **`''` (empty)** |
  | 64 | `stop` | 17 | `'ok'` |
  | 2048 | `stop` | 15 | `'ok'` |
  | 4096 | `stop` | 23 | `'ok'` |

  The 16-token row is the dangerous one. There is **no error, no exception, and no non-zero status** — just `finish_reason: "length"` and an empty string. Code that reads `content` and moves on would treat "the model never got to answer" as "the model answered with nothing." That is the same failure shape `health.py` refuses with `unevaluated`, and the same one this repo already rejected in `parsers.py` ("a parse yielding neither records nor meta from non-empty input is a failure, never a silent empty success").
- **Evidence:** Measurement above, run through `probe_minimax.call()`. Also visible in check 1's recorded evidence: `content='<think>The user is asking me to reply with the single word "ok". This</think>'` — cut mid-sentence at the budget.
- **What I did:** Logged only; no code exists yet to fix. **Binding requirement recorded for T-003:** the MiniMax provider must treat `finish_reason == "length"` with empty or unparseable `content` as a structured error, never as an answer, and must not rely on the endpoint's default budget. This is a spec item for that task, not a suggestion.
- **Needs human review:** no
- **Blocks:** T-003 (as a requirement, not a blocker).

---

## OBS-007 · T-002 · The documented 2048 cap on `max_completion_tokens` did not reproduce

- **Kind:** assumption-wrong
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `BUILD-PLAN.md` T-002 background states that `max_completion_tokens` on the OpenAI-compatible route "is documented as capped at 2048". A request with `max_completion_tokens: 4096` was accepted and answered normally — no error, no warning, no error envelope.
- **Evidence:** Row D of the OBS-006 measurement: `max_completion_tokens=4096` → `finish_reason=stop`, `completion_tokens=23`, content `'ok'`.
- **What I did:** Logged only. **Stating the limit of this evidence honestly: this shows 4096 is not *rejected*, not that it is *honoured*.** The answer was 23 tokens long, far below either ceiling, so silent clamping to 2048 is fully consistent with what was observed and is not ruled out. Distinguishing the two needs a prompt that deliberately generates more than 2048 tokens, which was not worth an API call at this stage. The safe course, and what T-003 should do, is to treat 2048 as the effective ceiling regardless — it costs nothing and is correct under both readings.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-008 · T-002 · The API key was pasted into the session transcript

- **Kind:** risk
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `MINIMAX_API_KEY` was not present in the environment or `.env` at the start of T-002, contradicting the session brief. The operator supplied it by pasting it directly into the chat. It is now in this session's conversation transcript, which is stored outside the repository and is not covered by `.gitignore`, `scrub_output`, or any control this project owns.
- **Evidence:** The key was absent from the environment, `.env`, `.env.example`, all shell profiles, and every repository file except the plan documents that merely name the variable. It now lives at `.env` (confirmed matched by `.gitignore:4` before writing, via `git check-ignore -v`), which is the home `BUILD-PLAN.md` §0.7 prescribes.
- **What I did:** Wrote it to the gitignored `.env` rather than exporting it on a command line, so it is not captured in shell history or a process listing, and verified `git status` does not see `.env`. The key has never been echoed, and `probe_minimax.py` carries a `Redactor` that rewrites it out of any line the script prints, including tracebacks. **Recommending rotation** once this build is finished — not because anything here mishandled it, but because a transcript is a copy nobody in this repository can revoke. Flagging rather than acting: rotating a credential is the operator's call.
- **Needs human review:** yes
- **Blocks:** none

---

## OBS-009 · T-002 · Tracker recorded orphaned commit hashes — `--amend` invalidates the hash you just wrote

- **Kind:** defect
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** The per-task loop needs each task's commit hash in `TRACKER.md`, but a hash only exists *after* the commit. The procedure used for T-001 and T-002 was: commit → read `git rev-parse HEAD` → write it into `TRACKER.md` → `git commit --amend` to fold the tracker edit into the same commit. **The amend rewrites the commit, producing a new hash and orphaning the one just recorded.** Both rows pointed at commits that are not on the branch: T-001 recorded `9ba2bbc` (actual `f309322`) and T-002 recorded `843d5fa` (actual `f1ecb55`).
- **Evidence:** `git merge-base --is-ancestor` reports `9ba2bbc` and `843d5fa` are NOT ancestors of HEAD, while `f309322` and `f1ecb55` are. The orphans survive only in the reflog and would vanish at the next `git gc`, so the tracker was pointing at hashes that a fresh clone could not resolve at all.
- **What I did:** Corrected both rows to the real hashes. **Changed the procedure so it cannot recur: no more `--amend` for hash backfill.** From T-003 onward the task's commit is made once and final, and its hash is written into `TRACKER.md` as part of the *next* task's commit. The cost is that the newest row's Commit cell reads `_pending_` until the following task lands, which is visibly incomplete rather than confidently wrong — the right trade. This is worth recording rather than quietly fixing because `TRACKER.md` is declared authoritative on status, and an authoritative document with unresolvable references is worse than one with an obvious gap.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-010 · T-003 · MiniMax serves the Responses API, so the provider is a parameterisation, not a fourth client — and the plan's two steps pointed at different routes

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `BUILD-PLAN.md` T-003 step 2 says MiniMax "is OpenAI-compatible, so reuse the OpenAI code path with a different `base_url`". Step 4 says to "set `reasoning_split` and `max_completion_tokens` explicitly on every call". **These two instructions point at different APIs, and only one of them can be followed.** The repo's OpenAI path (`_openai_call`) uses the **Responses API** (`client.responses.create`, `response.output_text`, `response.incomplete_details`), whereas `reasoning_split` and `max_completion_tokens` are **Chat Completions** parameters — the route T-002 probed. Following step 2 literally means step 4's parameters do not exist; following step 4 means abandoning step 2 and writing a fourth client.

  Rather than pick from the armchair, I probed. **MiniMax implements the Responses API too**, and the measurements settle it:

  | Property | `/chat/completions` (T-002) | `/responses` (this task) |
  |---|---|---|
  | `<think>` in output | **leaks by default**; needs `reasoning_split: true` | **never leaks**, no flag required |
  | Output parses as JSON | only with the flag | yes, unconditionally |
  | Determinism at temp 0 | 5/5 identical | **5/5 identical** |
  | Truncation at a tiny budget | `finish_reason=length`, **content empty, no error** (OBS-006) | `status="incomplete"`, `incomplete_details.reason="max_output_tokens"`, **partial text still returned** |
  | Bad key | HTTP 401 | HTTP 401 → `openai.AuthenticationError` |
  | Bad model | — | HTTP 400 → `openai.APIStatusError` |

- **Evidence:** Live probes against `https://api.minimax.io/v1` using the installed `openai` SDK 3.1.0 with only `api_key`/`base_url` changed: `responses.create` returns `status="completed"` with populated `output_text`; `max_output_tokens=16000` (the existing `MAX_OUTPUT_TOKENS`) is accepted; `incomplete_details.reason` is the exact string `_openai_call` already tests for. End-to-end smoke through `analyze_evidence()` with `LLM_PROVIDER=minimax` returned a correctly-structured 1402-character analysis over synthetic RR1 BGP evidence.
- **What I did:** **Chose the Responses API**, and recorded step 4 as not applicable to it rather than silently ignored. Three reasons, in order: it is what step 2 asks for and needs no fourth client; **the two behaviours step 4's parameters were meant to guarantee are achieved structurally on this route instead of by a flag** — no `<think>` because reasoning is a separate output item, and a bounded response because `max_output_tokens` is already set — which is D2's organising principle ("make the unsafe state unrepresentable rather than instructing against it") applied one layer down; and it eliminates OBS-006's silent-empty-content failure mode outright, since this route returns partial text plus an explicit incomplete marker instead of an empty string with no error. **If a future task moves the gate to Chat Completions, OBS-006 and step 4 both come back into force** — that is the condition under which this decision should be revisited.
- **Needs human review:** yes — the route choice is load-bearing for the MVP-1 gate, and it departs from a literal reading of the plan.
- **Blocks:** none.

---

## OBS-011 · T-003 · Sonnet 5's implementation accepted after one correction: errors named the wrong provider

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** T-003 was delegated to Sonnet 5 against a written specification, per §0.9. The returned work was correct in substance — `_openai_call` parameterised with the default path byte-identical, `minimax` added to `get_provider()` as explicit-only, an explicit `minimax` branch in `fabric_analysis.analyze_fabric` (without which minimax would have fallen through to the OpenAI config and failed with a misleading "OPENAI_API_KEY was rejected"), and 9 new tests. **One requirement was half-met.** The spec asked that the error messages "reflect the actual provider so a MiniMax failure does not tell the operator to check the wrong variable". The environment-variable names were made provider-aware, but the provider *name* was left hardcoded in four messages, so a MiniMax outage would report `Could not reach the OpenAI API` and a bad model would report `Unknown OpenAI model: MiniMax-M3`.
- **Evidence:** `git diff` on `src/agent_nettools/llm_analysis.py` before the correction: `raise LLMAnalysisError(f"Unknown OpenAI model: {resolved_model}. Check {model_env}.")` and three siblings. No existing test pinned those strings, so the suite was green with the defect present — which is exactly why acceptance is judged by reading the diff and not by reading the test count.
- **What I did:** Fixed it myself rather than round-tripping a four-line wording change: added a `provider_label: str = "OpenAI"` parameter, set to `"MiniMax"` in `_minimax_call_kwargs()`. Added 8 tests in two parametrized halves — one asserting each MiniMax failure path names MiniMax and contains no "OpenAI" at all, and its mirror asserting the plain OpenAI path's wording is unchanged word for word. The mirror matters: it is what stops a later refactor from "fixing" the labels in the wrong direction. Verified independently of Sonnet's report: frozen files untouched (`tests/test_safety.py`, `tests/test_template_security.py`, `templates.py`, `platforms.py`, and `agent_loop.py` all clean), the test diff purely additive with no existing test modified, no secret in the diff, **567 passed / 4 skipped / lint clean** (550 baseline + 17 new).
- **Needs human review:** no
- **Blocks:** none

---

## OBS-012 · T-003 · MiniMax supports tool calling, so `agent_loop` could stop being Anthropic-only

- **Kind:** deferred
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `agent_loop.py` refuses every provider except Anthropic, on the recorded grounds that "a 9B local Ollama model is not reliable for tool calling". T-002 check 6 showed MiniMax returns a well-formed `tool_calls` block for a trivial enum-constrained tool, so that reasoning does not extend to it. `agent_loop` was deliberately left untouched by T-003, and its existing guard names the resolved provider, so `LLM_PROVIDER=minimax` yields a correct and clear refusal rather than a confusing failure.
- **Evidence:** T-002 check 6 → `tool_calls returned: ['get_bgp_neighbor_state']`. `agent_loop.py:351-355`, unmodified.
- **What I did:** Nothing — logged only, per §0.11 NOTE. Out of scope for MVP-0, which needs no tool calling anywhere: the descent is deterministic and the report prompt is single-shot. Recording it because the LLD §10.3 lists model choice for the gate as an open item and treats tool-calling capability as a constraint on it; that constraint is now measured rather than assumed.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-013 · T-004 · Loki is receiving device logs; Q-002 answered in both halves

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** syslog-ng 4.5.0 is healthy and ships to **both** a local file and Loki over the native gRPC driver. All nine devices are present. Loki publishes no host port, but both Docker bridges are routed from the host, so it is reachable at `172.20.250.103:3100` — the **management network, the same /24 `nettools` already uses for the devices** — as well as `172.19.0.6:3100`. Labels are `job` (1 value), `host` (9), `source_ip` (9), `severity` (2). **Q-002, both halves: yes, syslog-ng ships to Loki; and yes, IOS-XR mnemonics survive — but in the message body, not as a queryable label.** 100% of 1,219 sampled lines carried a `%MNEMONIC`, zero without.
- **Evidence:** `docs/build/discovery-loki.md` records the full label scheme, a working range query, the verbatim syslog-ng config, and every measurement. Sample line: `RR1.sota-xrd RP/0/RP0/CPU0:Aug 15 13:50:46.954 UTC: ssh_syslog_proxy[1191]: %SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL : ...`
- **What I did:** Wrote the discovery document. **Decided `source_ip` is the join key, not `host`**: `nettools` says `RR1` while Loki says `RR1.sota-xrd`, a suffix applied by a hardcoded per-IP rewrite rule inside a syslog-ng config this repository does not own, whereas `source_ip` joins directly to `inventory/lab.yaml`'s existing `mgmt_ip` with no shared string convention to keep in step. Recorded for Stage 2 that mnemonic routing stays a lookup rather than a model judgement — the identifier is reliably present — but needs an extraction step, and that T-015's parser is the right place for it rather than changing shared platform config.
- **Needs human review:** no
- **Blocks:** none — informs T-015, T-028, and MVP-1's `get_logs`.

---

## OBS-014 · T-004 · The historical axis is much weaker than the plan assumes — three independent defects

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Loki works, but what is *in* it will not support T-028 as specified. Three separate problems, each sufficient on its own:

  1. **A severity floor hides the events the descent cares about.** Only `err` (3) and `warning` (4) are present — nothing at `notice` (5) or `informational` (6), across nine routers over seven days. IOS-XR logs BGP and IS-IS adjacency changes at severity 5. **The state transitions T-028 exists to correlate against are not in Loki at all.**
  2. **Duplication makes any count meaningless.** In the last 2,000 lines of the file there are **15 distinct messages**; one PE2 event with device timestamp `13:51:17.506` is stored **1,346 times**, with ingest timestamps spread over several seconds. The device timestamp is identical across every copy, so this is one event re-delivered, not a recurring one. "This interface flapped fourteen times" — the D14 use case — would be pure fiction without deduplication.
  3. **97% of the corpus is the tool's own footprint.** 1,187 of 1,219 lines are `%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL`, an SSH read error whose peer is `172.20.250.2` — the polling host. These are netmiko sessions closing and the routers logging it. The pipeline is largely observing the observer.

  Flow is also intermittent: no logs for over three hours at time of measurement, and zero lines on five of the last seven days.
- **Evidence:** `docs/build/discovery-loki.md` §6. Mnemonic histogram: 1,187 / 31 / 1 across exactly three distinct mnemonics in seven days. Loki retention is `168h`; the file covers 2026-05-13 → 2026-08-15 unrotated at 407 MiB.
- **What I did:** Logged rather than acted — T-004 is `[NON-BLOCKING]` and none of this is mine to fix. **Recorded a binding consequence for T-028: its premise does not hold against Loki today, so it should take the plan's own stated fallback and use `logging` template output from T-015, with this finding as the recorded reason** rather than discovering it late. Also recorded that any future counting consumer must dedupe on `(device, device-timestamp, message)`, and that Loki's own timestamp is **ingest time** — syslog-ng sets `timestamp("current")`, so the real event time lives only in the message body and T-015 must extract it.
- **Needs human review:** yes — whether to raise the devices' `logging trap` level is an operator decision with real consequences for log volume, and it is the single change that would make the historical axis useful.
- **Blocks:** none. Materially weakens T-028.

---

## OBS-015 · T-004 · Two out-of-scope platform defects, logged not fixed

- **Kind:** defect
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Two problems in the platform stack, both outside this build's scope. (a) **`/var/log/syslog-ng/sota-routers.log` has no rotation configured** — the `d_file` destination declares `throttle(200)` but no `file()` rotation, and it has reached **407 MiB** since 2026-05-13, almost entirely from the duplicated self-generated noise in OBS-014. (b) The syslog-ng config's header comment claims it "forwards to Loki via HTTP push API" while the destination it actually declares is the **native gRPC driver on `loki:9096`** — harmless today, but the kind of stale comment that misleads whoever next debugs the pipeline.
- **Evidence:** `docker exec sota-lab-platform-syslog-ng-1 ls -la /var/log/syslog-ng/` → 427,089,327 bytes. Config `destination d_file { file("/var/log/syslog-ng/sota-routers.log" ... ) }` with no rotation directive; `destination d_loki { loki(url("loki:9096") ...) }` against the header comment.
- **What I did:** **Nothing — logged only**, per §0.11 NOTE and §0.3's "you noticed a defect outside the current task's scope: do not fix it, log it." Neither file belongs to this repository; both are the operator's platform stack. Raising them here so they are visible at review rather than discovered when a disk fills.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-016 · T-005 · Alertmanager can be the Stage 2 trigger; the gap is that no alert names a device

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** **Q-003 answered: yes.** A webhook receiver already exists and is the *default* route — `receiver: telegram-bot`, `webhook_configs`, `send_resolved: true`, URL redacted by the API and deliberately not extracted. Grouping (`group_by: [alertname, component]`, `group_wait 30s`, `group_interval 5m`, `repeat_interval 12h`, 1h for `severity="critical"`) and two real inhibition rules are configured. So the four Stage 2 primitives the plan contemplated building — dedupe, group, silence, deliver — already exist, and n8n is not needed for the trigger path.

  **But no alert rule identifies a device.** All six rules are `inactive`, and the only label keys any of them set are `component` and `severity`. `investigate(device, subject, flow)` needs a device and a subject; a `LabRoutersDown` webhook carries neither.
- **Evidence:** `docs/build/discovery-alerting.md` §2 (full config verbatim, rule table). `GET /api/v2/alerts` → 0 alerts, so the label shape was taken from the rule definitions rather than sampled.
- **What I did:** Wrote the discovery document. Recorded that the Stage 2 blocker is **rule authoring, not infrastructure** — the telemetry underneath is already per-device and flowing (OBS-018), so a rule that carries a `source` label is cheap to add when Stage 2 arrives. Also recorded the payload shape (standard Alertmanager webhook JSON) so a future receiver can be written against it without re-discovery.
- **Needs human review:** no
- **Blocks:** none — informs Stage 2 and T-035 (Telegram is evidently already the team's alert channel, which bears on Q-007's residency question).

---

## OBS-017 · T-005 · **The lab is no longer broken.** The fixtures' ground truth no longer matches the live fabric

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Cross-checking live gNMI telemetry against the committed fixture ground truth shows the fabric was **rebuilt and fully repaired roughly 48 hours ago**. Every documented symptom this project is built around is gone:

  | Documented ground truth | Live now |
  |---|---|
  | RR1 → `10.255.0.12` **Idle** | `bgp-st-estab` |
  | RR1 → `10.255.0.14` **Idle** | `bgp-st-estab` |
  | PE2 **0** IS-IS adjacencies | **2** |
  | PE4 **0** IS-IS adjacencies | **2** |

  All 16 BGP sessions are Established. Every device's IS-IS adjacency count differs from `inventory/lab.yaml`'s `expected:` baseline (P1 5 vs 2, P2 5 vs 4, P3 5 vs 1, P4 5 vs 3, PE1 2 vs 1, RR1 2 vs 1). Every adjacency reports ~47.8h uptime and the `clab-sota-xrd-*` containers report "Up 2 days", so this is a rebuild, not a flap.
- **Evidence:** `docs/build/discovery-alerting.md` §5. Live: `count by (source) (Cisco_IOS_XR_clns_isis_oper:..._neighbor_uptime)` and `connection_state` on the BGP neighbor series. Baseline: `inventory/lab.yaml` `expected:` blocks. Fixture: `tests/fixtures/cisco_xr/RR1/t0/`.
- **What I did:** Logged only; changed no code and touched no device. **Establishing precisely what this does and does not break, because the scary reading is wrong:**
  - **Unaffected — everything fixture-based.** `make test` is still 567 passed / 4 skipped. `test_health.py`, `test_fabric_prompt_contains_pe2_pe4_isolation_and_rr1_idle_peers` and the committed `t0`/`t1` captures all still describe the broken fabric, because a fixture is a recording. **T-025 and the M3 milestone are therefore safe** — the acceptance test runs offline against `t0` and its Idle peer is still Idle there.
  - **Materially broken — T-011.** Its instruction is "capture `t0` … **against the current broken state**", and that state no longer exists to capture. Worse than merely losing the broken case: capturing template output now would place *healthy* `bgp_neighbor`/`route` captures **under the same `t0` label** as the existing *broken* intent captures. A descent reading both would see `show bgp summary` say Idle and `show bgp neighbor 10.255.0.12` say Established — an internally contradictory fixture set, which is a worse foundation than no fixture at all.
  - **Affected — T-033.** A live run of `RR1 → 10.255.0.12` will now yield `all_layers_healthy`, not a broken rung. That is still a valid test of the descent; it just no longer demonstrates root-cause finding.
  - **Note on re-breaking:** deliberately shutting an interface to restore the broken state would be **writing to a network device — a HALT under §0.11**, and is not something I will do or arrange. It is available to the operator, and it is one of the options T-007 should weigh.

  Deferring resolution to **T-007 (fixture gap analysis)**, which is exactly the task for it and is two steps away. Flagging now so it is not discovered at T-011 with capture already underway.
- **Needs human review:** **yes** — resolving T-011 needs an operator decision (re-break the lab, capture a new consistent healthy label, or construct the broken case synthetically in-test).
- **Blocks:** T-011 as written. Informs T-007 and T-033.

---

## OBS-018 · T-005 · Telemetry is rich and joins cleanly, but a BGP series carries 192 labels

- **Kind:** risk
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Prometheus holds 570 metric names, 316 of them streamed YANG oper data, all with current samples at 6,891 samples per scrape. The device join key is **exact**: the `source` label carries the bare names `P1`…`RR1`, matching `inventory/lab.yaml` and the CLI with no mapping — strictly better than Loki's `RR1.sota-xrd`. Of the three metrics T-005 asked about: **BGP session state is available but as a *label* (`connection_state`), not a metric name**; **interface error counters are available** (all currently 0); **interface oper-state is not available at all** — the exposed model is `infra_statsd_oper`, which is counters only. The risk: a single BGP neighbor series carries **192 labels**, every YANG leaf promoted to one, including free-text fields like `peer_reset_reason` and `reset_reason` whose values *change on state transitions*, minting a new series each time.
- **Evidence:** `docs/build/discovery-alerting.md` §3. Label dump of one `..._connection_established_time` series.
- **What I did:** Nothing — logged only, per §0.11 NOTE; the gnmic/telegraf config is not this repository's. Recorded the `source` join key and the "state is a label, not a metric" nuance so MVP-1 does not re-derive them. Also recorded **why the descent must still not read rungs off Prometheus** despite how convenient it looks: scraped metrics lag and go stale, the design says the device wins on current state (D8), and a stale "Established" would produce a confident wrong verdict — the precise failure `unevaluated` exists to prevent. Corroboration and flap history are the legitimate uses, and those are Stage 2.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-019 · T-005 → T-011 · Q-012 resolved by the operator: four fixture labels, and a coordinated capture window

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (recording an operator decision)
- **What happened:** The operator resolved Q-012 (OBS-017, the lab having been rebuilt healthy) by combining options 1 and 2 rather than choosing between them. The fixture set becomes four labels, not two:

  | Label | State | Status |
  |---|---|---|
  | `t0`, `t1` | The original partly-broken fabric, ~90s apart | **Committed. Untouched — do not recapture, do not extend.** |
  | `healthy` | The current clean fabric, all 16 BGP sessions Established, PE2/PE4 at 2 IS-IS adjacencies | Not yet captured — T-011 |
  | `broken` | A deliberate PE2 core-interface shutdown | Not yet captured — T-011 |

  This is a better answer than either option alone. Keeping `t0`/`t1` frozen preserves the ground truth that `test_health.py`, `test_fabric_prompt_contains_pe2_pe4_isolation_and_rr1_idle_peers` and the T-025 acceptance test are all pinned to. Adding two *complete* new labels — every intent **and** every template, captured in one pass per label — avoids the contradiction OBS-017 warned about, where healthy template output would have sat beside broken intent output under one label. And a deliberate, documented break is a far better test subject than the original accidental brokenness, because its cause is known exactly rather than inferred.
- **Evidence:** Operator instruction, this session. Current layout: `tests/fixtures/cisco_xr/<device>/<label>/`, 9 devices × 2 labels × 7 files.
- **What I did:** Recorded the decision and the **capture protocol**, since this needs the lab to change state under operator control while I capture:
  1. T-007 produces the capture manifest — until it exists I do not know which templates and parameters to capture, so capturing early would capture the wrong things.
  2. T-010 fixes the parser contract; T-011 extends `nettools capture` to templates.
  3. At T-011 I capture **`healthy`** against the current clean fabric and then signal the operator to run the break.
  4. Operator shuts the PE2 core interface. I capture **`broken`** — every device, every intent, every template — then signal that capture is complete.
  5. Operator restores.

  **The lab must stay in its current healthy state until T-011.** If anything rebuilds or changes it before then, the `healthy` label will not match what the rest of this analysis measured, and I need to be told. Also writing `tests/fixtures/README.md` now, ahead of T-011, at the operator's request — its stated purpose is reproducibility after the *next* rebuild, which is a reason to write it before the fixtures exist rather than after.
- **Needs human review:** no — this records their decision.
- **Blocks:** none. T-011 now has a defined shape; the capture window is the only step needing operator presence.

---

## OBS-020 · T-022 / T-023 · The `bgp_session` descent crosses devices below the transport rung

- **Kind:** risk
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (operator-raised)
- **What happened:** The operator raised a design problem the LLD and the plan both gloss over. `run_descent(flow, device, subject)` takes **one** device, and `Rung` as specified in T-022 carries `name`, `collect`, `check`, `finding` — no device scope. But the `bgp_session` descent's own rungs are not all about the same device:

  | Rung | Whose state is it actually about? |
  |---|---|
  | `bgp_session` | RR1 — the local session FSM |
  | `transport` | RR1 — the local TCP connection |
  | `route_to_peer` | RR1's RIB — still local, but the *answer* is about the path |
  | `igp_adjacency` | **Not RR1.** RR1 having adjacencies says nothing about why `10.255.0.12` is unreachable |
  | `interface` | **Which interface, on which device?** |

  The concrete case makes it sharp. For `RR1 → 10.255.0.12`, the original fault was **PE2** being IS-IS isolated. A descent that checks *RR1's* IS-IS adjacencies finds them healthy and either continues past the real fault or stops with a wrong verdict. The lowest broken layer is the root cause (D6) — but only if the descent is looking at the device where the layer is actually broken.
- **Evidence:** `BUILD-PLAN.md` T-022 `Rung` dataclass; T-023's five-rung ladder; LLD §5.3–5.4. Ground truth in `t0`: PE2/PE4 isolated, RR1's own adjacencies fine, RR1 reporting both sessions Idle from the far side.
- **What I did:** Logged for decision **at T-022**, which is where the `Rung` dataclass is defined and therefore the last cheap moment to add a device-scope field. Deliberately not deciding now — T-022 is a contract task I own, and the choice wants the fixtures in hand. The options as they stand:
  - **(a) `Rung.device_scope`** — an enum like `subject_device` / `local_device` / `path`, resolved by the walker. Keeps one descent, makes the shift explicit and testable.
  - **(b) Resolve the subject to a device first**, then run the lower rungs against *that* device. Simple, and matches the failure mode — but needs a subject→device mapping (`10.255.0.12` → PE2) that only the inventory can supply, which pulls inventory into a layer the LLD wants pure.
  - **(c) Stop the descent at `route_to_peer`** and report "unreachable, cause is off-device" — honest, terminal, and much less useful.
  - **(d) Two-phase**: descend locally on RR1, then re-enter the same flow with the peer as subject on PE2.

  Option (a) plus a subject→device resolution step looks strongest, but this is a T-022 decision and the alternatives deserve the fixtures. Raising it as **Q-013** so it cannot be quietly skipped.
- **Needs human review:** yes — it changes the `Rung` contract and possibly `run_descent`'s signature, both of which T-024 and T-025 build on.
- **Blocks:** T-022 (contract), T-023 (descent definition), T-024 (walker signature).

---

## OBS-021 · T-003 · OBS-010 accepted: the Responses API is confirmed

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5 (recording an operator decision)
- **What happened:** The operator accepted OBS-010's route choice and confirmed the reasoning, singling out the failure mode as decisive: **Chat Completions returning empty `content` with `finish_reason: "length"` and no error is disqualifying on its own.** That is the OBS-006 measurement — at a small budget, reasoning consumes the whole allowance and "the model never got to answer" becomes indistinguishable from "the model answered with nothing".
- **Evidence:** OBS-006 (the measurement), OBS-010 (the decision and the route comparison). Operator instruction, this session.
- **What I did:** Marked **Q-010 accepted**. The MVP-1 gate is now cleared to build on the Responses API rather than waiting on that confirmation. OBS-010's revisit condition still stands and is worth restating: if anything later moves a call to Chat Completions, OBS-006 and `BUILD-PLAN.md` T-003 step 4 both come back into force, and that path must then treat empty content with `finish_reason: "length"` as a structured error.
- **Needs human review:** no
- **Blocks:** none — unblocks the MVP-1 gate design.

---

## OBS-022 · T-028 · OBS-014 accepted: `show logging` fallback for MVP-0, and two platform items blocking B-206

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (recording an operator decision)
- **What happened:** The operator accepted OBS-014 for MVP-0: **T-028 takes the `show logging` template-output fallback** rather than querying Loki. They also classified both root causes correctly — these are **platform fixes, not Loki limitations**. Loki is working; it is faithfully storing what it is sent. Two backlog items were raised, both **blocking B-206**:

  | Item | Problem | Why it is a platform fix |
  |---|---|---|
  | **1 — severity floor** | Device `logging trap` levels are dropping the informational events the investigation layer needs. Only `err`(3) and `warning`(4) reach the collector; `%BGP-5-ADJCHANGE` and IS-IS transitions are severity 5 and never arrive. | The filter is on the routers, upstream of syslog-ng. syslog-ng applies no severity filter of its own — its config filters on source address only. |
  | **2 — collector SSH churn** | The collector's own SSH sessions are 97% of the corpus: 1,187 of 1,219 lines are `%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL`, peer `172.20.250.2`, from netmiko sessions closing abruptly. Amplified by re-delivery — one event stored 1,346 times. | Fixing it means changing how the collector closes sessions (clean disconnect), not changing Loki. |

  Both must be fixed before the historical axis carries signal. Until then, "when did it change, and how often" — the D8 historical axis and the whole point of T-028 — cannot be answered from Loki, whatever query is written against it.
- **Evidence:** OBS-014 and `docs/build/discovery-loki.md` §6 carry the measurements. Operator instruction, this session.
- **What I did:** Recorded both as backlog items blocking **B-206**. **B-206 is not tracked in this repository** — no backlog file or reference exists anywhere in the tree, so these are logged here with the blocking relationship stated rather than filed against the real item. If they should live in an external tracker, I need its location; otherwise this entry is the record. Confirmed T-028's direction: use the `logging` template output from T-015, with OBS-014 as the recorded reason rather than a silent substitution — and note that the T-015 parser must extract the **in-body** device timestamp, because Loki's own timestamp is ingest time (`timestamp("current")`) and `show logging` output carries the device clock directly.
- **Needs human review:** no
- **Blocks:** B-206 (external). T-028 now has a settled source.

---

## OBS-023 · T-006 · `<vrf>:<rd>` is eliminated by evidence — RD is reused across PEs

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Three VRFs across four PEs: **CUSTA** (RD `65000:100`, on PE1 and PE3), **CUSTB** (RD `65000:200`, on PE2 and PE4), and **SHARED-SVCS** (RD `65000:300`, PE1 only, IPv4 only). Import RT equals export RT for the two customer VRFs.

  **The RD is not unique across PEs.** PE1 and PE3 both use `65000:100` for CUSTA; PE2 and PE4 both use `65000:200` for CUSTB. So the plan's `<vrf>:<rd>` candidate names two different VRF instances on two different routers and cannot distinguish them — **eliminated by measurement, not by preference.** This is legal configuration (a type-0 RD reused fabric-wide is common), but a subject identifier that silently conflates two devices is worse than a verbose one.
- **Evidence:** `docs/build/discovery-l3vpn.md` §1 and §4. `show vrf all detail` on all four PEs, read-only, over a manual netmiko session per T-006's sanction. No command was added to the allowlist; no code changed; the probe stayed in the scratchpad, uncommitted.
- **What I did:** **Recommended `<pe>:<vrf>`** — `PE1:CUSTA`, `PE3:CUSTA`, `PE1:SHARED-SVCS`. Four reasons: it is unique where `<vrf>:<rd>` is not; it **already matches this repository's own convention**, since `glossary.md` keys operational memory as `PE2:GigabitEthernet0/0/0/1` and `PE2:bgp:10.255.0.31` — device first, object second — so a future D14 event store needs no second convention; it carries the device every collect step needs, so no rung re-resolves scope; and it expresses the asymmetric case honestly, since `PE1:SHARED-SVCS` exists and `PE3:SHARED-SVCS` does not. A fabric-wide question becomes a **fan-out** resolved in code (`CUSTA` → `{PE1:CUSTA, PE3:CUSTA}`), not a different naming scheme. **Recommended, not implemented** — T-006 says do not build the flow, and T-022 is where the registry shape is fixed.
- **Needs human review:** yes — it is the naming decision Q-004 asked for, and T-022 will build on it.
- **Blocks:** none for MVP-0 (`l3vpn_service` stays a `NotImplementedError` stub). Informs T-022.

---

## OBS-024 · T-006 · The crossed CE attachment is confirmed, and nothing on the PEs records it

- **Kind:** surprise
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** The documented crossing is real, verified from both ends by /31 pairing: **CE1→PE1, CE2→PE3, CE3→PE2, CE4→PE4**. So CUSTA (PE1+PE3) serves CE1 and CE2, and CUSTB (PE2+PE4) serves CE3 and CE4.

  The part worth recording is *how* it had to be verified. **No interface on any PE carries a description**, and nothing else on the device names its customer. The attachment is derivable only by arithmetic on the /31 — pairing CE2's `172.16.10.3` with PE3's `172.16.10.2`.

  This sharpens D10's prediction rather than merely confirming it. A model asked "which PE serves CE2?" faces three mutually agreeing wrong signals: the numeric match (CE2→PE2), the `Loopback100` last-octet convention (`.1`=PE1, `.3`=PE3), and the VRF numbering. All three point the same way and all three are wrong, twice out of four. There is no textual clue on PE3 that CE2 is its customer.
- **Evidence:** `docs/build/discovery-l3vpn.md` §3. PE side from `show running-config interface`; CE side from `ip -4 -o addr` inside the `clab-sota-xrd-CE*` containers.
- **What I did:** Logged only. Recorded for T-022 that a CE-oriented question ("why can't CE2 reach CE1?") names neither a PE nor a VRF, so **scope resolution — the /31 arithmetic — must exist in code beside the flow registry**, and must never be a model inference. This is D10's "the model names a scope, code resolves the attachment" with a concrete measurement behind it.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-025 · T-006 · `SHARED-SVCS` is a failure mode the protocol-stack descent cannot see

- **Kind:** deferred
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** PE1 carries a deliberate route-leaking demo, documented in its own config comment: `SHARED-SVCS` (RD `65000:300`) imports *and* exports both `65000:100` and `65000:300`, leaking bidirectionally with CUSTA. Its only interface is `Loopback150` = `172.30.30.1/32`, which should be reachable from CUSTA on **PE3** — a PE that does not carry SHARED-SVCS at all.
- **Evidence:** `docs/build/discovery-l3vpn.md` §1. Config comment: `Leak path: CUSTA (RT 65000:100) <-> SHARED-SVCS (RT 65000:300); Verify on PE3: show route vrf CUSTA 172.30.30.1`.
- **What I did:** Logged for whoever designs the `l3vpn_service` descent, well after MVP-0. **This is a reachability failure whose cause lives in RT import/export policy, not in the protocol stack** — the `bgp_session` ladder (transport → route → IGP → interface) would find every rung healthy and still not explain a broken leak. That is a genuinely different descent shape, not the BGP ladder with a VRF attached, and it is worth knowing before someone assumes the ladder generalises. Also noted: SHARED-SVCS is IPv4-only while CUSTA and CUSTB carry both families, so no flow may assume a VRF has both.
- **Needs human review:** no
- **Blocks:** none — post-MVP-0.

---

## OBS-026 · T-007 · Two of five `bgp_session` rungs cannot run at all today

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** 126 fixture files exist — 9 devices × 2 labels × 7 files, verified perfectly uniform. **Not one is template output.** `PLATFORM_TEMPLATES` holds six templates and none has a fixture. Mapped against T-023's ladder for `RR1 → 10.255.0.12`, rung 2 (`transport`, `show bgp neighbor 10.255.0.12`) and rung 3 (`route_to_peer`, `show route 10.255.0.12/32`) are **pure template reads with no fixture and no parser**, and rung 5's `interface` template is missing too. Only rung 1 is fully covered by what exists.
- **Evidence:** `docs/build/capture-manifest.md` §1–§2. Every command in the manifest was verified renderable by calling the real `render_command('cisco_xr', ...)`, and every filename by the real `command_slug` — so the manifest is executable, not aspirational.
- **What I did:** Wrote the manifest. The one judgement worth recording: **it deliberately over-captures.** Rungs 4 and 5 have no unambiguous device (Q-013/OBS-020), so rather than block on that decision, the manifest captures **every template on all nine devices**. Capture is per-device anyway, so this costs one extra command per device per template and makes the fixture set correct under *every* candidate answer to Q-013. The asymmetry is what decides it: under-capturing means discovering at T-024 that the walker needs output nobody recorded, and `broken` cannot be recaptured without another operator break window. Over-capturing costs a few kilobytes.
- **Needs human review:** no
- **Blocks:** none — T-011 now has an executable manifest.

---

## OBS-027 · T-007 · `nettools capture` cannot capture templates, but the read path already can

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** T-007 step 5 asks whether capture can be extended or needs new code. **New code — but much less than expected.** The read/replay half already works untouched: `command_slug` is generic over any command string and produces correct names for rendered template commands (including a `/32` in a prefix and slashes in an interface name); `fixture_path` keys on the *command*, not an intent; and **`run_template()` already accepts `sender=`**, so template fixtures will replay through the existing seam the moment the files exist. What is missing is only the write half — `capture_device()` iterates `collect_evidence()`, which runs static intents only, and there is no code path that runs a template during capture.
- **Evidence:** `docs/build/capture-manifest.md` §6. `network_tools.py:657` (`run_template`'s `sender=` parameter). `fixtures.py` `capture_device` / `fixture_path` / `command_slug`.
- **What I did:** Scoped T-011 to four narrow additions — a manifest structure, a capture loop over `run_template` writing through the *existing* `fixture_path`/`scrub_output` pipeline, template selection on the CLI, and honest partial-capture reporting. **Flagged one real hazard for that task:** `capture_device` deliberately uses `collect_evidence` because it is *one SSH session for all seven commands* — IOS-XR rate-limits repeated logins. A naive per-template loop would open 13–16 sessions per device across 9 devices, twice. That is precisely the login pattern the existing design avoids, and it is very likely the source of the `%SECURITY-SSHD_SYSLOG_PRX` churn measured in OBS-014. T-011 should reuse one session per device, and must be measured before it is pointed at the one-shot `broken` window.
- **Needs human review:** no
- **Blocks:** none — T-011 is specified.

---

## OBS-028 · T-007 · The `interface` flow's rungs are never specified by the plan

- **Kind:** assumption-wrong
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** T-022 requires `interface` to be implemented alongside `bgp_session`, and T-007 step 3 asks me to enumerate the commands its descent needs. But **the plan never defines the `interface` flow's rungs.** T-023 spells out `bgp_session`'s five in full; there is no equivalent anywhere for `interface`, in `BUILD-PLAN.md` or the LLD.
- **Evidence:** `BUILD-PLAN.md` T-022 ("Implement `bgp_session` and `interface` only") against T-023, which covers only `bgp_session`. LLD §5.3 lists the flow registry but details only the BGP descent.
- **What I did:** Recorded what the flow *can* read — `show interfaces brief` from the `interfaces` intent for admin/line state, and `show interfaces <name>` from the `interface` template for counters, MTU, description and bandwidth (which is what `interface_state`'s Q-005 error-counter threshold needs) — and captured fixtures for both, so T-022 is not constrained by missing data whichever shape it picks. **Explicitly marking this as inference rather than specification**, so nobody later reads the manifest as if the flow were already defined. T-022 must settle the rungs; the fixtures will already be there.
- **Needs human review:** no
- **Blocks:** none. T-022 must define what T-023 defined for `bgp_session`.

---

## OBS-029 · T-008 · TTP for every new parser; Genie rejected on weight, and on a requirement neither library meets

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** **Decision: TTP for all six new parsers. Genie is not adopted, not even for `bgp_neighbor`** — the exception the plan explicitly left open.

  **Equivalence, measured rather than assumed.** A throwaway TTP template for `show bgp summary` was run against **every committed fixture — all 9 devices × both labels, 18 files — and produced records byte-identical to the hand-written `parse_xr_bgp` in 18 of 18 cases.** That includes the eight files where the correct answer is *no records*: P1–P4 and PE4 answer `% BGP instance 'default' not active`, and TTP returns empty exactly as the hand parser does, rather than inventing a row.

  **Footprint, measured from PyPI without installing Genie:**

  | | `ttp` 0.10.1 | `genie` 26.7 | `pyats` 26.7 |
  |---|---|---|---|
  | Largest distribution | **0.1 MB** | **31.4 MB** | 5.6 MB |
  | Installed size | **1.1 MB** | — | — |
  | Runtime dependencies pulled | **zero** | 7 × `genie.libs.*` + PrettyTable, tqdm, dill, jsonpickle, netaddr | 14 × `pyats.*` |
  | `requires_python` | `<4.0,>=3.9` | `>=3.8` | `>=3.8` |

  TTP declares 20 dependencies in `requires_dist`, but **every one sits behind its `full` or `docs` extras** — the base install pulled exactly one new package and `pip show` reports `Requires:` empty. Genie, by contrast, depends on seven `genie.libs.*` subpackages which each pull more, and on pyATS itself; adopting it means adopting the whole pyATS ecosystem.
- **Evidence:** Trial script in the scratchpad (uncommitted). PyPI JSON metadata for all three packages. `pip install ttp` → `Successfully installed ttp-0.10.1`, site-packages grew 1,124 KiB, package count 63 → 64.
- **What I did:** Three reasons, in the order that decided it.

  1. **Neither library satisfies §0.10, so Genie's main selling point evaporates.** §0.10 requires that every non-blank line be either matched or matched by a *declared* ignore rule, with the remainder surfaced in `unaccounted_lines`. **TTP's result contains only matches — it cannot report what it skipped**, and Genie's parsers likewise return a structured dict with no accounting. I verified §0.10 is achievable by hand-building the accounting over the same input (`unaccounted_lines == []` for RR1/t0), but **that code is ours either way.** The argument for Genie was "the parsers are already written and maintained"; a pre-written parser that cannot meet the plan's own completeness requirement is not actually less work.
  2. **Weight, against this repository's established practice.** `evidence_store.py` uses stdlib `sqlite3` rather than an ORM; `metrics.py` hand-writes Prometheus exposition rather than adding `prometheus_client`; `output.py` renders tables with no dependency. A 31.4 MB wheel plus the pyATS ecosystem would be the largest dependency in the project by roughly two orders of magnitude, for six parsers that are a few lines each.
  3. **Genie's parsers are version-keyed to specific `show` output.** Where they disagree with XRd 7.11.2, the work becomes debugging someone else's regex against a device we cannot change — strictly worse than owning a small template.

  Added `ttp>=0.9,<1.0` to `pyproject.toml` as a **core dependency, not an extra**, deviating from T-008's phrasing ("added under the right extra") deliberately: `run_template` will attach parsed data on every call from T-018 onward, so the package cannot do its job without it. An optional extra would make the descent silently unavailable on a default install. Upper bound pinned at `<1.0` because TTP is pre-1.0 and its template syntax is the API. Confirmed `pip install -e .` resolves it and the suite stays green at **567 passed / 4 skipped**, lint clean. The `<4.0,>=3.9` range covers both CI's 3.11 and the local 3.13, so OBS-004's split-interpreter risk does not apply here.
- **Needs human review:** yes — it adds the first new runtime dependency of this build, and the core-vs-extra placement departs from the task's wording.
- **Blocks:** none — unblocks T-010 and T-012–T-017.

---

## OBS-030 · T-008 · §0.10's line accounting is our code, and it shapes T-010's contract

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The most consequential thing T-008 turned up is not about library choice. **No parsing library provides §0.10's line accounting.** TTP's `result()` returns matched groups only; there is no channel through which it reports lines it ignored. So `unaccounted_lines` and `unparsed_rows` cannot be delegated — they are a discipline `template_parsers.py` has to implement itself.
- **Evidence:** TTP result keys for the RR1/t0 parse are exactly `['meta', 'records']` — matches only. A hand-built accounting pass over the same input, subtracting matched neighbours plus nine *declared* ignore patterns (blank, IOS-XR timestamp banner, six preamble forms, two table headers), yielded `unaccounted_lines == []`, confirming the requirement is satisfiable this way.
- **What I did:** Recorded the concrete consequence for **T-010**, which defines the contract every parser then implements: each parser needs (a) its TTP template, (b) **a named constant holding its declared ignore patterns**, each commented with what it is — §0.10 requires the accounting be reviewable in one place, and a regex that quietly swallows unrecognised lines defeats the entire mechanism — and (c) a shared accounting helper that walks the raw input, subtracts matched and declared-ignored lines, and reports the remainder. Building that helper **once in T-010** rather than six times in T-012–T-017 is the difference between a contract and a convention. Also noted that `unaccounted_lines` and `unparsed_rows` must stay separate keys: the first means "the template does not know what this line is", the second means "the template knows and it did not fit", and collapsing them would hide a vendor output change behind a malformed-row count.
- **Needs human review:** no
- **Blocks:** none — directly shapes T-010.

---

## OBS-031 · T-009 · The scaffold was already built; the work was fixing what had gone stale

- **Kind:** surprise
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** T-009 asks for the `PART 5` structure to be created, the design documents moved into `docs/design/`, `FINDINGS.md` created from the template, a Design documents section added to `CLAUDE.md`, and `docs/README.md` written. **All five were already done** — by `install-docs.sh` before this run started, and recorded in OBS-001. Auditing against `PART 5` found every required path present: `docs/README.md`, `docs/design/`, `docs/build/`, `prompts/tests/cases/`, `scripts/`, and the three pre-existing `docs/` files.

  What was genuinely wrong was **staleness in the map itself**. `docs/README.md` listed `docs/design/architecture.md`, which does not exist and never has — `install-docs.sh` places it only "if present" and it was never written. A documentation map whose own entries do not resolve is worse than no map: it is the file a newcomer trusts first.
- **Evidence:** Audit of all eight `PART 5` paths — all present. Link check across `CLAUDE.md` and `docs/README.md`: **23 distinct `.md` references, all resolving** after the fix; `architecture.md` was the one broken entry before it.
- **What I did:** Rewrote `docs/README.md` rather than patching it: corrected the tree to what actually exists, stated plainly that `architecture.md` is referenced by `install-docs.sh` but unwritten and that nothing depends on it (`CLAUDE.md` carries the architecture today), added the four discovery documents with one-line descriptions of what each answers, added `prompts/` and `scripts/` — both part of `PART 5` and both previously unmentioned — and added **`tests/fixtures/README.md`**, which did not exist when the map was written and is now the file someone must read before capturing anything. Also added the `TRACKER.md`-wins rule to the map, since that precedence is easy to miss and is exactly the kind of thing a map should surface.

  **Deviation from §0.9's suggested allocation, logged deliberately:** T-009 is listed as sonnet-5's. I did it myself. The task turned out to be an audit plus one file rewrite, and writing a specification precise enough to delegate would have been longer than the work. §0.9's table is explicitly a *suggested* allocation and rule 1 gives Opus 5 the plan; this is not a case of taking implementation work back from Sonnet, it is a case of the task not being what the plan expected.
- **Needs human review:** no
- **Blocks:** none — **M1 reached.**

---

## OBS-032 · T-010 · Contract shipped; the accounting helper is the real content, and it adds a fourth failure mode

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `src/agent_nettools/template_parsers.py` and `tests/test_template_parsers.py` exist. **27 new tests, 594 passed / 4 skipped, lint clean.** The registry is deliberately empty until T-012.

  T-010 step 3 anticipated possibly needing to refactor `parsers.py` to export its constants. **Not needed** — `ParseError`, `PARSE_OK`, `PARSE_UNAVAILABLE` and `PARSE_FAILED` are already importable, so they are imported rather than re-declared. A test asserts them by **identity**, not equality: a copied string constant would compare equal today and drift the first time one was edited.

  Two shape decisions worth recording:

  1. **A template parser takes a single `str`, not a `dict[str, str]`.** `parsers.parse_intent` takes a dict because one intent can run several commands (`facts` runs two). A template always renders exactly one command. Matching `parsers`' signature here would have meant every parser carrying a shape it never uses — this is why the module is separate rather than more entries in `PARSERS`.
  2. **`parse_template_output` adds a fourth failure mode that `parse_intent` does not have: a result missing its §0.10 accounting is `PARSE_FAILED`.** A parser that bypassed `finalize()` has unmeasurable coverage, and evidence whose completeness is unknown must not be presented as parsed. That is `health.py`'s `unevaluated` reasoning one layer down — "I could not tell" must never render as "fine". Pinned by a test that fails if the check is removed, per D19's bar.
- **Evidence:** `pytest tests/test_template_parsers.py -q` → 27 passed. Full suite 594 passed / 4 skipped (567 baseline + 27), `ruff check .` clean.
- **What I did:** Built §0.10's accounting **once, here**, as OBS-030 required: a frozen `IgnoreRule(pattern, reason)`, a shared `XR_COMMON_IGNORES` covering blank lines and the IOS-XR timestamp banner (present in every `show` response, so no parser should re-declare it), `account_lines()` and `finalize()`. `finalize()` is the only function that builds a well-formed result, which is what makes the accounting **a contract rather than a convention** — a parser cannot produce a valid result without declaring what it ignored. Three details chosen deliberately: comparison is on the *stripped* line, because captured fixtures carry trailing whitespace that would otherwise create phantom unaccounted lines; `include_common=False` lets a parser with genuinely different framing opt out and declare everything itself, still explicitly; and `IgnoreRule.reason` is required and tested non-empty, because §0.10's purpose is that a reviewer can see the whole accounting in one place, which a bare regex does not provide.
- **Needs human review:** no
- **Blocks:** none — unblocks T-011 onward. T-012 will fail `test_registry_is_empty_until_the_parsers_land`, which is the intended signal to update that test rather than delete it.

---

## OBS-033 · T-010 → T-019 · The package `__init__` eagerly imports the device layer, which will trap T-019's acceptance test

- **Kind:** risk
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Checking that `template_parsers` stayed free of device-layer dependencies, `sys.modules` showed `agent_nettools.inventory` and `agent_nettools.network_tools` loaded after importing it. **The module is clean** — it imports only `re`, `dataclasses`, `typing`, `collections.abc` and `.parsers`, and `netmiko` never loads. The loading comes from `agent_nettools/__init__.py`, which eagerly imports `agent_loop`, which pulls in `network_tools` → `inventory`. Importing *any* submodule imports the package, so it happens regardless.
- **Evidence:** `import agent_nettools.template_parsers` → `agent_nettools.network_tools` in `sys.modules`; `netmiko` not in `sys.modules`. `src/agent_nettools/__init__.py` lines 11-12 (`from .agent_loop import ...`). `grep '^from\|^import' src/agent_nettools/template_parsers.py` shows five stdlib imports and one from `.parsers`.
- **What I did:** Nothing to the code — the eager `__init__` is pre-existing and out of scope. **Recording it because it is a trap laid directly across T-019's acceptance criterion**, which reads "no import of `inventory`, `network_tools`, or anything that touches a device". The obvious way to test that — asserting those names are absent from `sys.modules` — **will fail for `checks.py` no matter how pure `checks.py` is.** The test must instead inspect the module's own imports (its AST, or `grep`-equivalent over its source), which is what actually expresses the intent. Flagging now so T-019 does not lose time to it, or worse, weaken the criterion to make a badly-written test pass.
- **Needs human review:** no
- **Blocks:** none — informs T-019.

---

## OBS-034 · T-011 · `BUILD-PLAN.md` was overwritten by an `install-docs.sh` re-run, and restored

- **Kind:** environment
- **Escalation:** NOTE
- **Model:** opus-5 (recording an operator report)
- **What happened:** `docs/build/BUILD-PLAN.md` was accidentally overwritten by a re-run of `install-docs.sh`, which reinstalls the upstream pack over the repository's copy. It was restored from git with **all ten `DONE` statuses intact**, so no task state was lost.
- **Evidence:** Operator report, this session. `install-docs.sh` `place BUILD-PLAN.md docs/build/BUILD-PLAN.md` — the script backs up before overwriting but does treat the installed copy as replaceable, which is true of the design documents and **not** true of the build documents.
- **What I did:** Recorded the ownership rule, which was implicit and is now explicit: **`BUILD-PLAN.md`, `TRACKER.md`, `FINDINGS.md` and `BACKLOG.md` are owned by this repository and are never re-installed.** They accumulate state — task statuses, findings, backlog items — that exists nowhere upstream, so an install overwrites work rather than refreshing a reference. The `docs/design/` documents are the opposite: they are reference copies and reinstalling them is harmless (verified byte-identical at OBS-001). Worth noting the near-miss: had this happened without the statuses being recoverable from git, the run would have silently lost its record of which tasks were complete, and `TRACKER.md` — which is authoritative on status — would then have disagreed with the plan, which §0.11 makes a finding in its own right.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-035 · T-011 · Operator decisions recorded: Q-013 deferral confirmed, Q-014 and OBS-023 accepted

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5 (recording operator decisions)
- **What happened:** Three of the open items were settled.

  **Q-013 (rung device scope) — deferral to T-022 confirmed, with a constraint.** The operator agreed the concrete case is decisive: a descent checking RR1's own IS-IS adjacencies finds them healthy and walks past PE2's isolation. The instruction adds something the finding did not say: **add the device-scope field when the `Rung` dataclass is defined, because retrofitting it after `descent.py` exists is not cheap.** The four options in OBS-020 are to be brought to T-022 as a decision, not implemented ahead of it.

  **Q-014 — closed. `ttp` stays a core dependency.** The operator's reasoning is sharper than the finding's: an optional extra would make the descent silently unavailable on a default install, and **silent degradation is the failure mode this project rejects** — the same reason `parsers.py` treats an empty parse as a failure and `health.py` refuses to read a failed intent as healthy. They also identified the more valuable half of OBS-029: that **neither library provides line accounting**, so §0.10 is a genuine addition rather than a re-specification of something TTP already did.

  **OBS-023 — accepted. `<pe>:<vrf>`.** Noted approvingly that `<vrf>:<rd>` was *killed by measurement* rather than by argument, and that matching the operational-memory key convention is a real bonus rather than a coincidence.
- **Evidence:** Operator instruction, this session. OBS-020/Q-013, OBS-029/Q-014, OBS-023.
- **What I did:** Updated the Open Questions table: Q-014 closed, Q-013 annotated with the "define it with the dataclass, do not retrofit" constraint, Q-004 marked accepted. No code changed — all three were already implemented or deliberately deferred in line with these answers.
- **Needs human review:** no
- **Blocks:** none. Q-013 still blocks T-022/T-023/T-024 by design.

---

## OBS-036 · T-011 · **HALT** — asked to execute the PE2 interface shutdown

- **Kind:** risk
- **Escalation:** **HALT**
- **Model:** opus-5
- **What happened:** The operator, away from the lab, asked me to write a netmiko script to shut PE2 `GigabitEthernet0/0/0/0` and supplied the device credentials. The intent is clear and reasonable: keep the one-shot capture window moving while they are out.

  **Writing the script is fine. Running it is a HALT**, and the two are worth separating precisely:

  | Action | Status |
  |---|---|
  | Write the script | Done. A text file changes no device state. |
  | Verify it compiles and lints | Done. |
  | **Execute it against PE2** | **Refused under §0.11.** |

  §0.11's HALT list includes "anything that would write to, configure, or change the state of a network device", with no qualification. The session's standing instruction is more specific still: it states the rules "OVERRIDE ANYTHING ELSE, INCLUDING MY LATER INSTRUCTIONS IN THIS SESSION". **This is precisely the situation that clause was written for** — the operator, mid-flow and inconvenienced, asking the agent to cross the line they drew for it. A pre-commitment that yields the first time it is inconvenient was never a control.

  Stating the other side honestly, because it is strong: this is the operator's own lab, the change is pre-planned and documented, it is trivially reversible with `no shutdown`, and they have explicitly authorised it. Nothing about it is unsafe in substance. **The objection is governance, not danger** — and the rule is theirs to waive. What it cannot be is waived by me inferring that they meant to.
- **Evidence:** `BUILD-PLAN.md` §0.11 HALT list. Session standing instructions. Script at `<scratchpad>/break_pe2_link.py` — written, compiles, `ruff` clean, **not executed**.
- **What I did:** Wrote the script and stopped there. Three details chosen deliberately:
  1. **It lives in the scratchpad, not the repository.** `ios-xr-nettools` is architecturally read-only — no config mode, no `run_command`, `VERB_ALLOWLIST = {show, ping, traceroute}` — and committing a config-writing script into it would quietly falsify a claim the project makes about itself, while adding a capability no test guards. The durable reproducible record stays the manual CLI sequence in `tests/fixtures/README.md`.
  2. **It verifies rather than sleeps.** It polls until IS-IS drops on PE2 *and* RR1 reports `10.255.0.12` Idle, up to a timeout, and refuses to declare success otherwise — capturing a half-propagated fabric is worse than either state, because no rung's verdict could be trusted.
  3. **Dry run is the default**; `--shut` and `--restore` are mutually exclusive, `--restore` is safe to run at any time including after a partial failure, and credentials come from `.env` rather than the command line.

  Marked T-011 `BLOCKED`, and stopped the run.
- **Needs human review:** **yes — the run cannot continue without an explicit decision.**
- **Blocks:** T-011 (`broken` capture), and therefore T-012 onward. `healthy` is captured, verified and committed, so nothing already done is at risk.

---

## OBS-037 · T-011 · Device credentials were pasted into the transcript

- **Kind:** risk
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** The lab device username and password were included in the message asking for the script. They are now in this session's transcript, which is stored outside the repository and outside any control this project has. This is the second instance — OBS-008 records the same for `MINIMAX_API_KEY`.
- **Evidence:** Operator message, this session. The same values are already configured in `.env`, which is gitignored (`.gitignore:4`), so the credentials themselves are not newly stored anywhere by me.
- **What I did:** Nothing was written. The script reads `DEVICE_USERNAME`/`DEVICE_PASSWORD` from `.env` via dotenv rather than taking them as arguments or embedding them, so they never reach a command line, a process listing, shell history, or a file I create. Repeating OBS-008's recommendation rather than treating it as already made: **these are lab credentials on a management network, so the exposure is low, but a transcript is a copy nobody here can revoke.** Worth rotating alongside the MiniMax key when the build is finished. Flagging, not acting — rotating a credential is the operator's call.
- **Needs human review:** no — folded into Q-009.
- **Blocks:** none

---

## OBS-038 · T-011 · Waiver granted, exercised, and discharged — one scoped device write

- **Kind:** decision-made
- **Escalation:** **HALT-waived**
- **Model:** opus-5
- **What happened:** The operator waived §0.11 for **one specific action**, authorising a single scoped device write for the purpose of capturing the `broken` fixture set. Their scope, verbatim in effect: run `--shut` against PE2 `GigabitEthernet0/0/0/0`, verify propagation, capture `broken`, verify against the 217-file shape of `healthy`, then `--restore` and confirm the fabric is back. **"The waiver expires the moment you report the restore. §0.11 is unchanged for anything after that — do not treat this as precedent for any other device write."**

  Timeline, UTC:

  | Time | Event |
  |---|---|
  | 23:01:19 | `--shut` issued against PE2 `Gi0/0/0/0` |
  | 23:01:21 | Commit accepted; interface `admin-down` |
  | 23:05:19 | Propagation predicate failed after 240s — script exited 1, refused to declare success |
  | ~23:06 | Read-only diagnostic (see OBS-039) |
  | 23:07:19 | `--restore` issued |
  | 23:07:37 | Adjacencies back after ~8s; exit 0 |
  | 23:08+ | Restoration verified independently, twice |

  **The waiver is now discharged.** `broken` was not captured; the reason is OBS-039, and it is a property of the fabric rather than a failure of the window.
- **Evidence:** Restoration verified three ways, deliberately not trusting the script that had just proven it contained a defect: PE2 reports `Total adjacency count: 2` and `Gi0/0/0/0 up/up` on a direct read; gNMI telemetry reports PE2 = 2 adjacencies and **all 16 BGP sessions `bgp-st-estab`**; and P1/P2 confirm 5 adjacencies each on direct reads, matching the pre-shut baseline exactly.
- **What I did:** Executed only what was scoped, and stopped when it did not work rather than improvising. **Specifically, I did not shut `Gi0/0/0/1`** — which is what isolating PE2 would actually have required (OBS-039). That is a second device write, outside the waiver's scope, and "the obvious next step" is exactly the reasoning a scoped waiver exists to refuse. Followed the operator's failure rule on the letter: the verification failed, so I restored immediately rather than capturing a state I could not vouch for.

  One transient anomaly, chased down rather than left: immediately after restoration, telemetry showed only 8 devices reporting and P1 at 4 adjacencies, then briefly **6** — while the device itself said 5. That was scrape lag plus stale series, not damage; ground truth from P1 and P2 directly is 5 and 5. Worth recording because it is unplanned live evidence for OBS-018's argument that **the descent must not read rungs off Prometheus**: for roughly a minute after a real topology change, the telemetry was confidently wrong in both directions.
- **Needs human review:** no — the waiver is discharged and the fabric is verified back.
- **Blocks:** none. `broken` remains uncaptured; see OBS-039 for what it would take.

---

## OBS-039 · T-011 · Shutting one uplink does not isolate PE2 — the fabric routed around it

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The `broken` scenario assumed that shutting PE2's `Gi0/0/0/0` would isolate it and drive RR1's session to `10.255.0.12` Idle. **It does not. PE2 has two core uplinks**, and losing one changes nothing above the link layer:

  | | Before | After the shut |
  |---|---|---|
  | PE2 `Gi0/0/0/0` (→ P1) | up/up | **admin-down** |
  | PE2 `Gi0/0/0/1` (→ P3) | up/up | up/up |
  | PE2 IS-IS adjacencies | 2 (P1, P3) | **1 (P3)** |
  | PE2 → RR1 BGP | Established 2d04h | **Established 2d04h, undisturbed** |
  | RR1 → `10.255.0.12` | Established | **Established** |

  The IGP reconverged over the remaining path and the BGP session never noticed. This is a resilient design working exactly as designed — the scenario, not the fabric, was wrong.

  **Two independent defects surfaced, and the second nearly hid the first.**

  1. **The scenario defect.** Isolating PE2 requires shutting **both** `Gi0/0/0/0` and `Gi0/0/0/1`. `t0`'s brokenness was total isolation; one link down is a different and much milder condition.
  2. **A defect in my own script.** `pe2_adjacencies()` reported **0** immediately after the commit, when the true value was 1. netmiko's `cisco_xr` `.commit()` leaves the session in config mode — the device echoed `PE2(config-if)#` — so every subsequent `show` on that connection ran in config mode and returned nothing. The propagation wait was measuring an artifact of my own SSH session, not the fabric. Fixed with an explicit `exit_config_mode()`; the restore then verified correctly in ~8s.

  The two together produced a misleading picture: "IS-IS dropped to 0 but BGP is still up" looked like a half-propagated fabric, when the truth was "IS-IS dropped to 1 and BGP is correctly unaffected". **Had I trusted the script's numbers and captured, `broken` would have been mislabelled** — the exact hazard OBS-017 warned about, arriving from a direction nobody anticipated.
- **Evidence:** `show isis adjacency` on PE2 after the shut: `P3 Gi0/0/0/1 ... Up`, `Total adjacency count: 1`. `show interfaces brief`: `Gi0/0/0/0 admin-down admin-down`. RR1's `show bgp summary`: all four peers with `2d04h` uptime and numeric prefix counts.
- **What I did:** Restored, and did **not** shut the second interface — outside the waiver. Recorded what a future window needs: **both uplinks, shut in one commit** so the fabric never sees a transient single-link state, followed by the same verified propagation wait.

  Also recording why this costs less than it appears. **T-025's acceptance test does not need the `broken` label.** It runs `run_descent(bgp_session, RR1, 10.255.0.12)` against `t0`, where `show bgp summary` already reports the peer Idle — so rung 1 (`bgp_session_state`) is broken and the descent *stops there by definition*, never collecting rungs 2–5. The template fixtures matter for the opposite case: walking all the way down and reaching `all_layers_healthy`, which the `healthy` label covers completely. So the milestone that matters, M3, is not blocked by this.
- **Needs human review:** yes — whether to schedule a second window for a two-interface break, or accept `healthy` + `t0` as sufficient for MVP-0.
- **Blocks:** the `broken` label only. Not T-012 (parsers work from `healthy`), not T-025.

---

## OBS-040 · T-012 · `bgp_neighbor` parser accepted; §0.10 verified by mutation; one volatile-field defect fixed

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** T-012 delegated to Sonnet 5 against a written spec, and the work is good. All **41** committed `bgp_neighbor` fixtures round-trip with `unaccounted_lines == []` and `unparsed_rows == 0` on the first pass. The three legitimate response shapes are distinguished correctly and all three return **`PARSE_OK`**, which is the crux of the task — "the command succeeded and the answer is no" must never look like "the parser could not read this":

  | Shape | Result |
  |---|---|
  | Full neighbor block | `found=True`, 5 address-family records |
  | `% BGP instance 'default' not active` | `found=False`, `reason="bgp_not_active"` |
  | `% Neighbor not found` | `found=False`, `reason="neighbor_not_found"` |

  **§0.10 was verified by mutation rather than assumed.** Injecting a plausible future vendor line — `Graceful Restart Extended: negotiated, stale time 300 seconds` — into a real fixture surfaced it in `unaccounted_lines` instead of dropping it silently. **The mechanism fires.** That is the whole justification for OBS-030's conclusion that the accounting had to be ours; had it not fired, §0.10 would have been decoration. A whitespace change also surfaces, which is correct behaviour rather than brittleness — the template genuinely no longer recognises that line.

  The 48 declared `IgnoreRule`s were audited individually: every one is anchored and specific, every one carries a non-empty reason, and **none matches arbitrary text** (tested against three synthetic unknown lines). No catch-all crept in, which was the failure mode §0.10 explicitly warns about.
- **Evidence:** `make test` 662 passed / 4 skipped, `ruff` clean. Frozen files and `t0`/`t1` untouched, verified by `git diff`. Mutation test output recorded above.
- **What I did:** Accepted after fixing **one real defect Sonnet correctly flagged as uncertain rather than hiding**. `last_reset_reason` captured the whole text after `Last reset `, giving `'1d23h, due to Address family activated'` — a duration embedded in a field named *reason*, and not declared volatile. Two captures of an unchanged device would therefore diff as changed (`1d23h` → `2d00h`): **exactly the 100%-false-positive failure `CLAUDE.md` records from before Phase 2.**

  Split it into `last_reset_reason` (`"Address family activated"`) and `last_reset_ago` (`"1d23h"`), and declared **only the duration volatile**. The alternative — marking the whole field volatile — would have removed the noise but also thrown away the signal: a reason changing from `Address family activated` to `Peer closing down the session` is a real event worth diffing, and folding them together discards it. Added three tests, including one pinning that `last_reset_reason` is *not* in the volatile set, and one for the `Last reset <duration>` form with no `due to` clause so it is still consumed rather than surfacing as unaccounted.
- **Needs human review:** no
- **Blocks:** none — unblocks T-013.

---

## OBS-041 · T-004 → B-206a · Correction: the severity floor is **not** the devices' trap level

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** **This corrects OBS-014 and re-scopes backlog item B-206a.** OBS-014 observed that only `err`(3) and `warning`(4) reach Loki, and reasoned that "the most likely cause is the routers' own `logging trap` level being set to `warning` or higher", explicitly flagging that it was *not confirmed*. The `healthy` capture now contains `show logging` output from every device, and it contradicts that hypothesis:

  ```
  Trap logging: level informational, 50 messages logged
  Logging to 172.20.250.101, 50 message lines logged
  Buffer logging: level debugging, 555 messages logged
  ```

  **The trap level is already `informational`.** The device buffer holds severity 6 and 7 messages (`%SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL`, `%SYSDB-SYSDB-7-INFO`) that never appear in Loki. Note also the disparity in the device's own counters: **555 messages buffered, 50 shipped.**
- **Evidence:** `tests/fixtures/cisco_xr/*/healthy/show-logging-last-200.txt`. Mnemonic histogram across those fixtures: `%SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL` ×1169, `%SYSDB-SYSDB-7-INFO` ×447, `%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL` ×181, `%PKT_INFRA-PQMON-6-QUEUE_DROP` ×3 — against Loki, which holds only severities 3 and 4.
- **What I did:** Corrected **B-206a** in `BACKLOG.md`: it previously said the devices' trap levels are dropping the events, which is now known to be false and would have sent someone to change a setting that is already correct. The real question is narrower and further down the pipe — **something between the device and Loki drops severity ≥5**, and the candidates are syslog-ng's own handling or the `logging` configuration's destination filtering, neither of which this repository owns. **B-206a's substance is unchanged and it still blocks B-206**: the informational events the historical axis needs are absent. Only the diagnosis moved.

  Worth noting how this was caught: it fell out of capturing `show logging` for T-015's parser, a task whose stated purpose is entirely different. The fixture set is already paying for itself as evidence, before a single parser consumes it.
- **Needs human review:** no — the backlog item is corrected in place and its blocking relationship is unchanged.
- **Blocks:** none. Corrects OBS-014's diagnosis; B-206a still blocks B-206.

---

## OBS-042 · T-013 · `route` parser accepted; the fixtures held a third shape nobody had specified

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** All **44** `route` fixtures round-trip with `unaccounted_lines == []`. But the spec I wrote described **two** output shapes and the fixtures contain **three**. Sonnet found the third and reported it rather than quietly bending the schema around it:

  | Shape | Count | Notes |
  |---|---|---|
  | `Known via "isis CORE"`, next-hop paths | 40 | the documented case |
  | `% Network not in table` | 3 | the not-found case, captured deliberately |
  | **`Known via "local", … (connected)`** | **1** | **unspecified** |

  The third is PE4 looking up **its own loopback**, which resolves to a directly-connected local route: no `Local Label`, no `labeled SR`, and a descriptor block reading `directly connected, via Loopback0` — **no next-hop address and no `from` address at all**. My spec asserted a record schema (`next_hop`, `from`, …) that this line cannot populate.

  I had actually walked past this. When I surveyed the fixtures for the spec I grouped them by *line 4* and got a single shape, `Routing entry for N.N.N.N/N` — which is identical across all three cases. **The survey was too shallow and I wrote the spec on it.** One fixture in 44 disagreed, and only a parser forced to account for every line found it.
- **Evidence:** `tests/fixtures/cisco_xr/PE4/healthy/show-route-10-255-0-14-32.txt`. Verified after the fix: `found=True protocol='local' distance=0 metric=0 paths=1`, record `{"next_hop": "directly connected", "from": null, "interface": "Loopback0", "directly_connected": true}`, `unaccounted_lines == []`.
- **What I did:** Accepted Sonnet's `next_hop="directly connected"` sentinel — its reasoning is right that the string is exactly as stable an identity across two captures as a real address, so `TEMPLATE_RECORD_KEYS`'s `next_hop` role is not compromised. **But a sentinel alone was not good enough**: it makes the field polymorphic, and a consumer calling `ipaddress.ip_address(record["next_hop"])` would crash on it. Added `directly_connected: bool` to **every** record — `True` here, `False` on ordinary paths — so downstream code branches on a boolean instead of string-matching a sentinel, and never has to distinguish "absent" from "false". Two tests pin both directions.

  Only 5 `IgnoreRule`s were needed against `bgp_neighbor`'s 48, which is proportionate: `show route` is a dozen lines, not 150. Re-ran the §0.10 mutation myself — an injected vendor line surfaces as unaccounted.

  **The lesson worth keeping is about my spec, not the parser.** Grouping 44 files by one line and concluding "all the same shape" was a cheap check that produced a confident wrong answer. §0.10 is what caught it — a parser that may not ignore anything undeclared cannot walk past an unfamiliar line. That is the mechanism doing exactly the job OBS-030 argued for, on its second outing.
- **Needs human review:** no
- **Blocks:** none — unblocks T-014.

---

## OBS-043 · T-016/T-017 · A read timeout produces a truncated capture reported as success

- **Kind:** defect
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Capturing the negative branch for `ping` and `traceroute` (both absent — every existing fixture is a 100% success and a completed trace), I probed `192.0.2.1`, which is unroutable here.

  **`ping` worked and revealed a shape difference worth having:** at 0% success the device emits `.....` and `Success rate is 0 percent (0/5)` — and **omits the `round-trip min/avg/max` line entirely.** A parser assuming that line always follows would fail on exactly the case that matters. Captured and kept.

  **`traceroute` produced a truncated artifact.** The template's 60s `read_timeout` expired mid-probe against an unreachable destination, and the result was an **8-byte file containing a single line, ` 10  * `** — no `Tracing the route to` header, no earlier hops. **`run_templates` reported `errors: []` and `status: success`.**
- **Evidence:** `tests/fixtures/cisco_xr/PE1/healthy/ping-192-0-2-1.txt` (164 bytes, complete, kept). The traceroute artifact: 8 bytes, 1 line, deleted rather than committed.
- **What I did:** **Deleted the traceroute artifact.** A bad fixture is worse than no fixture: committed, it would have been enshrined by T-017's round-trip test and the parser would have been shaped partly by a truncation. The fixtures README already says review by eye before committing, and this is what that is for.

  **Logged the underlying defect rather than fixing it — it is outside T-016/T-017's scope but it is real.** A read timeout at the transport boundary yields *partial output with no error*, so a caller cannot distinguish "the device said this" from "we stopped listening". That is the same failure shape as OBS-006 (MiniMax returning empty content with `finish_reason: length` and no error), and this project rejects it in both places on the same grounds. The right fix is for `_netmiko_send_commands` to mark a timed-out command as an error rather than returning what arrived; that touches the transport path and belongs in its own task, not smuggled into a parser task.

  T-017 is not blocked. Every one of the 9 good traceroute fixtures already contains a `*` hop, so partial-loss parsing has real coverage; only the never-completes case lacks a fixture, and the plan already requires a synthetic truncated-output test that covers the same parsing path.
- **Needs human review:** yes — the silent-truncation defect deserves a backlog item; I have not created one because backlog IDs are the operator's to assign.
- **Blocks:** none.

---

## OBS-044 · T-014 → T-020 · A line-down interface omits its error counters entirely — absent is not zero

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** T-014 accepted with no correction — all 45 fixtures round-trip clean, 14 declared ignore rules, the volatile judgement implemented exactly as specified, §0.10 mutation verified independently. **776 passed.**

  The valuable part is a shape neither the LLD nor my spec anticipated, which Sonnet surfaced rather than working around. **While line protocol is down, IOS-XR does not emit the error counters at all:**

  | | Healthy `Gi0/0/0/0` | Line-down `Gi0/0/0/2.300` |
  |---|---|---|
  | Counters emitted | **22** | **6** |
  | `input_errors`, `crc`, `frame`, `overrun`, `abort` | present | **absent** |
  | `carrier_transitions` | present | **absent** |
  | `runts`, `giants`, `throttles`, `parity` | present | **absent** |

  They are not reported as zero. The lines do not exist.
- **Evidence:** `tests/fixtures/cisco_xr/PE1/healthy/show-interfaces-gi0-0-0-2-300.txt` — the counter block ends after `Output 0 broadcast packets, 0 multicast packets`. Parsed: `admin_state="up"`, `line_state="down"`, records `['packets_input','bytes_input','total_input_drops','packets_output','bytes_output','total_output_drops']`.
- **What I did:** Accepted the parser — its per-line independent matching handles the short block correctly without special-casing, and a fixture missing counter lines simply yields fewer records, which is the honest representation.

  **Recording a binding requirement for T-020, because this is a trap with a wrong answer that looks right.** `interface_state`'s specified broken condition is "admin up + line down, **or error counters above threshold**". On this fixture the first half fires, so the check reaches the right verdict by luck. But a check that evaluates the counter half by reading `input_errors` from the records and finding nothing **must return `unevaluated`, never "0 errors, therefore healthy"** — the counters are unknown, not zero. Defaulting absent-to-zero would report an interface whose error state cannot be determined as clean, which is precisely the failure `health.py`'s `unevaluated` discipline exists to prevent and which `glossary.md` states as "a failed collection must never look like a verdict".

  What makes this worth flagging loudly: the fixtures contain **exactly one** line-down interface, so a check written against the healthy 44 would never encounter a missing counter, pass its tests, and carry the defect into production. Q-005 (the threshold value) is already open; this adds a second question to settle with it — what the check does when the counter is absent. The answer is `unevaluated`, and T-020 needs a test pinning it against this specific fixture.

  One honest gap Sonnet flagged: `admin_state` normalisation of IOS-XR's literal `"administratively down"` to `"admin-down"` is implemented but **untested against real output**, because all 45 fixtures are `admin_state == "up"`. The one capture that would have exercised it was the PE2 window, where `Gi0/0/0/0` did read `admin-down` — but that was the interface we shut, and no `show interfaces <name>` capture was taken before the restore.
- **Needs human review:** yes — folded into Q-005, which T-020 must answer.
- **Blocks:** none. Binds T-020.

---

## OBS-045 · T-015 · The `logging` parser discards nothing — and the Stage 2 routing key is verified against 1,800 real entries

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** Accepted with no correction. **804 passed.** All 9 fixtures round-trip with `unaccounted_lines == []` and `unparsed_rows == 0`, 200 records each.

  **`LOGGING_IGNORES` is empty — the only parser so far that declares no ignores at all.** That is not an omission, it is the strongest possible §0.10 outcome: every one of the 208 non-blank lines per file is *consumed* into either `meta` or a record. Nothing about this command's output is discarded. It also makes the accounting maximally sensitive — with no ignore rules to absorb anything, any future vendor line surfaces immediately. Verified by mutation: an injected header field appears in `unaccounted_lines`.

  **The Stage 2 routing key is sound.** `mnemonic.rsplit("-", 2)` rather than a left-anchored regex, because the facility half legitimately contains hyphens (`SECURITY-SSHD_SYSLOG_PRX`, `PKT_INFRA-PQMON`) while the code half never does. I checked this the only way worth checking it — recomposing `facility-severity-code` back into the original mnemonic **for all 1,800 records across all 9 fixtures: 0 mismatches.** Severities are exactly `{"3", "6", "7"}`, matching the survey.

  This is what makes D5's growth claim real: a Stage 2 trigger table can key on the full mnemonic or on `facility`+`code`, and both are now available as clean fields rather than as substring arithmetic at lookup time.
- **Evidence:** Sample record: `{"timestamp": "Aug 14 08:21:51.298 UTC", "node": "RP/0/RP0/CPU0", "process": "ssh_syslog_proxy", "pid": "1191", "mnemonic": "SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL", "facility": "SECURITY-SSHD_SYSLOG_PRX", "severity": "6", "code": "INFO_GENERAL", "text": "sshd[55923]: Accepted authentication for clab ..."}`. `record_key = None`, volatile = `{lines, messages_dropped, window_start, window_end}`.
- **What I did:** Accepted. Three things worth recording beyond the pass:

  1. **`record_key` is deliberately `None`.** A log is an append-only stream with no stable per-record identity — two captures share history but the set grows, so matching records by identity would make `diff_evidence` produce nonsense. The contract's documented meaning of `None` is "positional, cannot be matched by identity", and that is exactly right here. It is commented in place, because `timestamp` looks like an obvious key and is not one.
  2. **`unparsed_rows` is now demonstrably distinct from `unaccounted_lines`**, which no earlier parser had shown. A line matching the entry envelope but with a mnemonic that will not split increments `unparsed_rows` *and* is marked consumed, so it never also appears as unaccounted. "The template knows what this should be and it did not fit" and "the template does not know what this is" are finally two different, separately observable states rather than a distinction stated only in the docstring.
  3. **The OBS-041 evidence is now pinned by a test.** `trap_level == "informational"` and `logging_to == "172.20.250.101"` hold on **every** fixture, not just one. That fact is what re-scoped backlog item B-206a, and a future parser change can no longer quietly lose it.
- **Needs human review:** no
- **Blocks:** none — unblocks T-016.

---

## OBS-046 · T-016 · `ping` accepted; and a spec wording bug Sonnet was right to push back on

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** Accepted with no correction. **833 passed.** All 10 fixtures clean. The case the fixture was captured for behaves correctly: at 0% success the device omits the `round-trip min/avg/max` clause entirely, and the parser returns `rtt_min/avg/max = None` rather than `"0"` — the distinction between "no measurement exists" and "the measurement was zero", which is the same discipline as `unevaluated` one field down. `loss_pct` is derived (`100 - success_pct`) rather than parsed, because the device never prints it.

  The volatile split landed as specified and is worth restating because it is the third time this judgement has come up: `rtt_min/avg/max` and `result_string` are volatile — RTTs vary run to run and so does the exact reply pattern — while **`success_pct`, `loss_pct`, `sent` and `received` are deliberately not.** A ping going from 100% to 0% is the entire signal the template exists to produce; marking those volatile would discard it while removing no noise.

  **Sonnet pushed back on my spec, correctly.** Test item 9 said "truncated output (first 2 lines of a real fixture)". Taken literally that is the blank line and the IOS-XR timestamp banner — both already absorbed by `XR_COMMON_IGNORES` — so the parser would never reach its header line and *would* raise, contradicting the same item's "does not raise". It read the intent as the first two lines of *ping-specific* content and said so explicitly rather than quietly picking one. That is exactly the behaviour §0.9 rule 2 asks for: an ambiguous spec is my defect, not the implementer's to paper over.
- **Evidence:** Success: `sent=5 received=5 success_pct=100 loss_pct=0 rtt=2/2/3 result='!!!!!'`. Failure: `sent=5 received=0 success_pct=0 loss_pct=100 rtt=None/None/None`. Volatile set verified to exclude all four signal fields.
- **What I did:** Accepted both the implementation and the interpretation. Recording the spec bug against myself: three of the six parser specs I wrote have now had a gap the fixtures exposed (T-013's third route shape, T-014's counter absence, T-016's truncation wording). The pattern is consistent — **I write specs from a survey, and a survey is a sample.** §0.10 and an implementer willing to say "this does not fit" have caught all three.
- **Needs human review:** no
- **Blocks:** none — unblocks T-017, the last parser.

---

## OBS-047 · T-017 · Last parser accepted; the one uncovered shape fails loudly rather than silently

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** Accepted with no correction — **861 passed**, all 9 fixtures clean, and the first of the six specs to survive contact with the fixtures without a gap. The trap the spec was written to prevent held: **`completed` is `True` on all 9 fixtures even though not one of them ends at the traced target**, because the destination replies from `10.0.1.16`/`10.0.1.18` — its own interface addresses — rather than from its loopback. A `last_hop == target` implementation would have been wrong on 100% of the data while looking obviously right.

  Sonnet flagged a real narrowing rather than hiding it: **a fully-timed-out hop (` 3  * * * `, no address at all) is not supported**, because no fixture contains one and the plan forbids writing parsers against invented output. It declined to build for an unobserved shape, which is the correct reading.
- **Evidence:** `PE1` trace parses to `hops=2 completed=True`, records `[{hop:1, address:10.0.1.1, mpls_label:24010, rtt_msec:["1","1","1"]}, {hop:2, address:10.0.1.16, mpls_label:null, rtt_msec:["2",null,"2"], probes_lost:"1"}]`.
- **What I did:** Accepted, after checking the property that makes the gap tolerable rather than taking it on trust. **Appending ` 3  * * * ` to a real fixture yields `unparsed_rows=1`, not a silent drop and not an `unaccounted_lines` entry.** So the shape is *visible* when it arrives: the round-trip test asserts `unparsed_rows == 0`, which means the first real capture containing a fully-timed-out hop fails loudly instead of quietly losing a hop from the path.

  That is the right resolution of a genuine tension. Supporting the shape would mean guessing the device's exact formatting — forbidden, and the class of guess that produced this project's `intent`/`domain` confusion in the first place. Leaving it unsupported *and invisible* would be worse still. Unsupported and loud is the honest third option, and it is precisely what `unparsed_rows` was separated from `unaccounted_lines` to express: "the template knows what this should be and it did not fit."

  **All six parsers are now complete**, and a cross-parser sweep confirms every one of the 148 template fixtures round-trips with `unaccounted_lines == []` and `unparsed_rows == 0`.
- **Needs human review:** no
- **Blocks:** none — unblocks T-018, the last task in Part 2.

---

## OBS-048 · T-018 · The blocking gap is closed — **M2 reached**

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `run_template` now attaches `data.parsed` and `data.parse_status` to every result, via `_attach_parsed_template` — the template-side twin of `_attach_parsed`, deliberately identical in semantics. **871 passed.**

  This is what LLD §4.2 calls *the blocking gap*: "`run_template` attaches no parsed data at all… Every rung of the dependency descent below the top one reads exactly those commands. **Without template parsers there is no descent** — only a model reading raw text, which §3 forbids." That is invariant 4 of §0.6, and until this commit the repository could not satisfy it for any template.

  Verified end to end against fixture replay — all six templates now return typed records rather than text:

  | Template | `parse_status` | records |
  |---|---|---|
  | `bgp_neighbor` | ok | 5 address families |
  | `route` | ok | 2 paths |
  | `interface` | ok | 22 counters |
  | `logging` | ok | 200 entries |
  | `ping` | ok | 0 (summary only, by design) |
  | `traceroute` | ok | 2 hops |
- **Evidence:** **158/158 template fixtures** round-trip with `unaccounted_lines == []` and `unparsed_rows == 0`, across all six parsers. The six frozen safety tests pass (186 assertions), and **all four frozen files are byte-identical to the baseline commit** across the entire run.
- **What I did:** Mirrored `_attach_parsed`'s three semantics rather than inventing new ones, because a second set of rules for the same concept is how the `intent`/`domain` confusion started:
  1. **Parsing is independent of transport.** An `error` result still gets a parse attempt over whatever output arrived. Pinned by a test: garbage output yields `status: "success"` with `parse_status: "failed"` — the device answered, we could not read the answer, and those are different failures.
  2. **`unsupported` gets `PARSE_UNAVAILABLE` with no attempt.** No parser was tried, which is not the same as one failing.
  3. **Additive only.** A test asserts every pre-existing envelope key is still present, so no existing caller — CLI, MCP, `agent_loop` — sees a changed shape. The MCP `get_lab_*` tools inherit parsed output for free, since they all route through `run_template`.

  One deliberate omission: **`run_templates` (the batched capture path) does not attach parsed data.** Its envelope holds many commands, so a single `parse_status` would be meaningless, and capture writes raw text by design — the fixtures are the input to parsing, not its output. Noted so the asymmetry reads as a decision.

  Done by opus-5 rather than the suggested sonnet-5: the change is ~30 lines against a pattern that already existed, and it is the milestone judgement, so writing a spec would have cost more than the work. Same reasoning as OBS-031, logged for the same reason.

  **M2 — "Parsing unblocked" — is reached**, and Part 2 is complete.
- **Needs human review:** no
- **Blocks:** none. Part 3 (checks and descent) is unblocked, subject to Q-013 at T-022.

---

## OBS-049 · T-011 · Second waiver: `broken` captured with both uplinks — and it paid for itself immediately

- **Kind:** decision-made
- **Escalation:** **HALT-waived**
- **Model:** opus-5
- **What happened:** The operator granted a second scoped §0.11 waiver — PE2 `Gi0/0/0/0` **and** `Gi0/0/0/1` shut in **one commit**, so the fabric never passes through the half-isolated state that made the first attempt useless. Same terms: restore immediately on any failure at any stage, never leave the fabric shut, waiver expires on reporting the restore.

  | Time (UTC) | Event |
  |---|---|
  | 07:41:43 | shut issued, both interfaces, one commit |
  | 07:41:54 | commit accepted |
  | 07:44:08 | **propagation confirmed after ~134s** — consistent with the 180s BGP hold time |
  | 07:44:36–07:49:15 | capture, 216 files, zero errors |
  | 07:50:16 | restore issued — **failed** |
  | 07:50:48 | restore retried — also raised, but the config had applied |
  | 07:51+ | restoration verified three ways |

  Both uplinks was the right diagnosis. Where one changed nothing above the link layer, two isolated PE2 completely: adjacencies 2→0, both interfaces `admin-down`, `RR1 → 10.255.0.12` Idle, and PE2's route to RR1's loopback gone entirely.

  **The restore reported failure twice while actually succeeding.** Both attempts raised `netmiko.exceptions.ReadTimeout: Pattern not detected: '[#\$]'` — the second from inside `exit_config_mode`, *after* `send_config_set` had applied the change. So the `no shutdown` committed and the script's post-commit prompt handling is what broke. I did not trust either the failure or the eventual success: I read the device directly, then confirmed against RR1 and against gNMI telemetry.
- **Evidence:** Restoration verified three independent ways — PE2 direct read: `Gi0/0/0/0` and `Gi0/0/0/1` both `up/up`, `Total adjacency count: 2`. RR1: `10.255.0.12` numeric with uptime `00:00:44`, re-established. Telemetry: PE2 = 2 adjacencies, **all 16 BGP sessions `bgp-st-estab`**.
- **What I did:** Followed the failure rule, and the ordering mattered. When verification of the *capture* turned up four parser defects, I **restored first and fixed the parsers afterwards from the committed fixtures** — the fabric was degraded and I no longer needed it. Keeping it shut to write regexes would have been indefensible.

  Recording the script defect honestly: **a script that reports failure on success is worse than one that fails cleanly**, because the operator's instruction was "if restore fails, retry, then halt loudly" — and following that literally would have had me halt while the fabric was already healthy. What saved it was refusing to believe the script and reading the device instead. `--restore` is idempotent, so the retry was safe; had it not been, the second attempt could have caused harm on a fabric that was already fine.

  **The waiver is discharged.** §0.11 is in force again, and this is not precedent for anything.
- **Needs human review:** no
- **Blocks:** none. **T-011 is now DONE rather than PARTIAL.**

---

## OBS-050 · T-011 · The `broken` label found four parser defects the healthy set could not reach

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator's stated reason for wanting this label was that a fixture where rung 1 is Idle *and* rungs 2–5 carry real output is what proves `descent.py` descends rather than stopping at the first thing it looks at. It paid off before `descent.py` exists. Within minutes of the capture, four defects surfaced that **158 healthy fixtures are structurally incapable of producing**:

  | Defect | How it surfaced |
  |---|---|
  | `BGP state = Idle (No route to multi-hop neighbor)` — parenthetical unparsed | `unaccounted_lines` (§0.10) |
  | `line protocol is administratively down` | **hard `PARSE_FAILED`** on 2 fixtures |
  | Socket "not armed" down-variant | `unaccounted_lines` |
  | Notification error-code / payload / timing lines | `unaccounted_lines` |

  Both failure modes behaved exactly as designed: §0.10 made three of them *visible*, and the fourth was loud on its own.
- **Evidence:** Both labels now round-trip 100% clean — **healthy 158/158, broken 153/153**. 1030 passed.
- **What I did:** Captured the BGP parenthetical as a new `state_reason` meta key rather than ignoring it — it is the single most diagnostic field in the command, since it says *why* the session is down, and `bgp_transport`'s check at T-020 will want it. Always present, `None` when established, tested both directions. Normalised `line_state`'s `administratively down` to `admin-down` so consumers see one vocabulary rather than two spellings.

  Declared the other five down-session lines as ignore rules rather than extracting them. `state_reason` already carries the diagnostic that matters, and **widening the schema mid-stream to chase adjacent detail is how a parser contract stops being reviewable** — the same discipline that kept `bgp_neighbor` to 48 explained rules instead of 130 extracted fields.

  **This also closes OBS-044's honest gap.** The `admin_state` normalisation was recorded as implemented-but-untested because all 45 healthy fixtures are admin-up. PE2's shut interfaces now test it against real output — and revealed that the *line protocol* half had never worked at all.
- **Needs human review:** no
- **Blocks:** none.

---

## OBS-051 · T-019 · `checks.py` shipped, with "absence is unevaluated" as a stated rule

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `src/agent_nettools/checks.py` and 17 contract tests. **1047 passed.** The five checks land at T-020; what exists now is the shape they must satisfy.

  The operator's instruction was to generalise Q-005's second half into a rule in the module docstring: **a check may only answer `healthy` about a field it actually read; absence is `unevaluated`.** It is stated there with all three instances named, because the point is that this is not a one-off:

  | | Where | What it looked like |
  |---|---|---|
  | OBS-006 | model API | empty content, `finish_reason: "length"`, no error |
  | OBS-043 | transport | partial output, `status: "success"`, `errors: []` — now **B-411** |
  | OBS-044 | parsed evidence | a line-down interface omits error counters; absent reads as zero |

  Same shape every time: **silent degradation behind a green flag.** A wrong verdict that announces itself is a bug; a wrong verdict that looks right is a liability, because nothing downstream can detect it.
- **Evidence:** 17 tests. `checks.py` imports exactly `dataclasses`, `typing` and `.parsers` — nothing that can reach a device, a file, the clock or the environment.
- **What I did:** Three design choices worth recording, each enforcing the rule structurally rather than by convention:

  1. **`require_parsed()` is the gate every check starts with.** It returns `(section, None)` on `PARSE_OK` and `(None, unevaluated)` otherwise — so the honest answer is the *default path* and reaching records without checking takes deliberate effort. Its three failure reasons stay distinct (section missing / platform unsupported / parser failed), because collapsing them discards the only clue about what to do next.
  2. **A `healthy` or `broken` verdict citing no evidence is refused at construction.** D20 requires every claim to cite an evidence key; a verdict that cites nothing cannot be grounded, and catching that in `__post_init__` beats discovering it in the grounding check three layers later. `unevaluated` is the sole exception — there may genuinely have been nothing to read.
  3. **`parsed_records()` carries a warning in its own docstring** that it returns `[]` for both "parsed fine, no records" and "never parsed", with a test pinning that the two are indistinguishable by records alone. That is precisely the OBS-044 hazard, kept visible next to the function that causes it rather than only in a docstring above.

  **The T-019 acceptance criterion needed OBS-033's warning to test at all.** "No import of `inventory`, `network_tools`, or anything that touches a device" cannot be checked via `sys.modules` — `agent_nettools/__init__.py` eagerly imports `agent_loop`, which pulls in both, so importing *any* submodule loads them however pure `checks.py` is. The test parses the module's own AST instead. Without that finding I would have written a test that failed for reasons unrelated to the code, and the likely reaction would have been to weaken the criterion.
- **Needs human review:** no
- **Blocks:** none — unblocks T-020, which must use `require_parsed` and test the absent-counter case against the one line-down interface.

---

## OBS-052 · T-020 · Q-005 resolved: the error-counter signal is a **rate**, not a total

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (recording an operator decision)
- **What happened:** The operator settled Q-005's first half, and rejected the framing the plan offered. `BUILD-PLAN.md` T-020 says `interface_state` is broken when "error counters above threshold" — an absolute. **That is the wrong shape.** A counter total is meaningless without uptime: 500 CRC errors accumulated over three months is noise; 500 in ten minutes is a dying optic. The same number, opposite conclusions.

  The ruling, which is a rate:

  | Outcome | When |
  |---|---|
  | `broken` | error delta non-zero and **rising between observations** |
  | `unevaluated` | only one observation, **or** counters absent |
  | `healthy` | counters read in both observations and **flat** |

  With a single-observation absolute permitted only to flag a warning-equivalent — **never to make the check broken, never to stop a descent.**
- **Evidence:** Operator instruction. `t0`/`t1` exist as a deliberate pair ~90s apart, and `diff_evidence` already computes deltas, so a rate is computable wherever two observations exist.
- **What I did:** Recorded it, and worked out the consequence the ruling implies but does not spell out — **the two halves of `interface_state` do not compose symmetrically**, and getting that wrong would break every descent.

  A descent runs on **one** collection. So the counter half is almost always `unevaluated` in practice. If an `unevaluated` counter half made the whole check `unevaluated`, the interface rung would be permanently inconclusive and **every descent would end in `undetermined`** — the check would be strictly worse than not existing. The operator's "never stop a descent" is precisely this.

  So the composition is ordered, not conjunctive: admin-up-plus-line-down is `broken` and outranks everything; a rising counter delta is `broken`; otherwise, if line state was read and is up, the answer is **`healthy` with the reason naming the counter half as unevaluated**.

  **Case 3 is worth defending explicitly, because it looks like a violation of the rule I just wrote into `checks.py` and is not.** The rule forbids claiming health about a field you did *not* read. Case 3 claims health about **line state, which it did read**, and declines to claim anything about counters — in the reason string, where a report can carry it. Reporting the field you read is the rule working; refusing to report it would be a different failure, one where an honest "I could not check everything" degrades into "I can tell you nothing".

  Specified into T-020 with that reasoning in the function docstring, because someone will otherwise tidy case 3 into `unevaluated` for consistency and silently disable the descent.
- **Needs human review:** yes — flagged as instructed. The composition rule is mine, derived from the ruling rather than stated by it.
- **Blocks:** none. Binds T-020, and T-024 should be aware the interface rung concludes on line state alone in single-observation mode.

---

## OBS-053 · T-020 · The five checks; and a reasoned divergence from `health.py` that T-021 must expect

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (judgement) · sonnet-5 (implementation)
- **What happened:** All five checks implemented against the `healthy` and `broken` labels. **1069 passed**, the 17 T-019 contract tests including the AST purity test still pass unchanged, and no fixture contradicted the spec.

  The Q-005 composition landed exactly as intended. On a single observation of a healthy interface:

  > `healthy` — *"line protocol up; error-counter rate not evaluated (single observation)"*

  The check answers about the field it read and says plainly what it did not evaluate, which is the whole point of the rule.

  **Sonnet generalised rule 1, correctly, and flagged it.** My spec said "`admin_state` up and `line_state` down → broken". PE2's shut uplinks are `admin-down`/`admin-down` — neither field is `up`, so the literal rule would not have fired on the one interface the `broken` label exists to capture. The implemented rule is "not both fields exactly `up` → broken", which subsumes mine. My wording came from the LLD's table and would have missed the case the fixture was captured for.
- **Evidence:** `interface_state(PE2 broken, "Gi0/0/0/0")` → `broken`, reason `"interface Gi0/0/0/0 is not up (admin_state='admin-down', line_state='admin-down')"`, citing `PE2:interface:Gi0/0/0/0`. Same interface on `healthy` → `healthy` with the counter half named.
- **What I did:** Accepted. The reason text does the load-bearing work here: **"admin-down" and "line protocol down" are different findings** — one says a human did this deliberately, the other says something failed — and a report that conflated them would send an operator to the wrong place. Naming both fields in the reason keeps that distinction available downstream.

  **Recording the divergence from `health.py` before T-021 meets it**, because T-021 failing is a §0.11 HALT and I would rather it arrive as a known allowance than as a surprise:

  `health.py`'s `_interface_admin_up_line_down` deliberately does **not** fire on admin-down — its docstring says "an admin-down interface is intentional and must never fire this rule". `checks.interface_state` calls the same interface `broken`. **Both are right for the question they answer**, which is exactly the per-device/per-object split the LLD draws: "is this device unhealthy?" is not the same question as "why is this specific path broken?" An intentionally shut interface is not evidence of ill health, and *is* the complete answer to why a path is down.

  Measured on the fixtures, they do not currently collide: on the `broken` label `health.py` reports PE2 **critical** (`isis_isolated`, `bgp_session_down`) while the check reports the interface `broken` — same direction, different attribution. **The collision is reachable though**, and T-021 needs to handle it: an interface shut on an otherwise-healthy device would give `health.py` severity `ok` and `checks` `broken`. That must be encoded as an explicit, justified allowance in the agreement test — **not** as a loosened assertion, and not by weakening either module.

  One structural consequence Sonnet flagged honestly: a line-down interface short-circuits to `broken` before the counter half runs, so the absent-counter path cannot be reached through the public API on that fixture. It is tested directly against the private helpers using `Gi0/0/0/2.300` — the one real line-down interface — never against the healthy 44, as OBS-044 requires.
- **Needs human review:** yes — the `health.py` divergence is a design position, and T-021 encodes it.
- **Blocks:** none. Binds T-021.

---

## OBS-054 · T-021 · The agreement test passes — no HALT — and it caught itself being vacuous twice

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** **`checks.py` and `health.py` do not contradict each other anywhere in the corpus.** 92 comparisons across all four labels × nine devices, plus a per-peer BGP comparison. No genuine disagreement, so **no HALT**.

  Agreement is meaningful rather than accidental — the verdicts track the real fabric:

  | | `t0`/`t1` | `healthy` | `broken` |
  |---|---|---|---|
  | `isis_adjacency` broken on | PE2, PE4 | none | PE2 |
  | `bgp_session_state` broken on | PE2, RR1 | none | PE2, RR1 |

  In every case `health.py`'s corresponding rule fires on exactly the same devices.
- **Evidence:** `tests/test_checks_agree_with_health.py`, 92 passed / 18 skipped (the skips are devices with no BGP process, which is correct). Full suite **1161 passed**.
- **What I did:** Wrote the test myself rather than delegating — §0.9 assigns T-021 to Sonnet, but this is the one task the plan singles out as a HALT trigger, and I had already run the whole comparison by hand while judging T-020. Logged as a deviation for the same reason as OBS-031 and OBS-048.

  **The test was wrong twice before it was right, and both failures are worth recording, because a green agreement test is exactly the kind of thing that gets trusted without inspection.**

  1. **It passed vacuously on the first run.** 36 comparisons, all green — because `load_fixture_evidence` replays through `collect_evidence`, which resolves a device *before* the injected sender runs, so without credentials every section errored, every check returned `unevaluated`, and every assertion was trivially satisfied. What caught it was the anti-vacuity test I had written into the same file: it asserts the corpus actually produces both `healthy` and `broken` verdicts. **Without that guard I would have committed a test that compared nothing and reported success** — which is the same silent-degradation shape as OBS-006, OBS-043 and OBS-044, this time in a test rather than in the code.
  2. **It then failed for a wrong reason.** Eight interface comparisons "disagreed" — but `health.py` names the object in its finding as `subject`, and I had read `interface`. The lookup set was `{None}`, nothing ever matched, and every line-down interface looked like a conflict. Read at face value, that is eight HALTs. It was one typo.

  The second is the more instructive: **a red agreement test is not evidence of disagreement any more than a green one is evidence of agreement.** That is the same lesson as §0.11's new paragraph about tools reporting failure, arriving one layer up. I verified the finding shape directly before concluding anything, and the fabric turned out to agree with itself all along.

  The documented `admin-down` divergence (OBS-053) is encoded as a narrow allowance and is **genuinely exercised** — PE2's two shut uplinks take that path, so it is not dead code. Anything else disagreeing still fails.
- **Needs human review:** no
- **Blocks:** none — Part 3's checks are complete. **T-022 next, and it needs Q-013 answered.**

---

## OBS-055 · T-022 · Q-013 measured against the fixtures — and a second, larger problem with the descent's stopping rule

- **Kind:** risk
- **Escalation:** **HALT** (T-022 blocked pending the operator's decision, as agreed)
- **Model:** opus-5
- **What happened:** Ran the full `bgp_session` ladder for `RR1 → 10.255.0.12` against the `broken` label, both ways. The `broken` capture settles Q-013 empirically rather than by argument:

  | Rung | Checked on RR1 (local) | Checked on PE2 (subject device) |
  |---|---|---|
  | 1 `bgp_session` | **broken** | n/a |
  | 2 `transport` | **broken** | n/a |
  | 3 `route_to_peer` | **broken** | n/a |
  | 4 `igp_adjacency` | **healthy** | **broken** |
  | 5 `interface` | *which interface?* | **broken** — `admin_state='admin-down', line_state='admin-down'` |

  **Read locally, the descent finds nothing below rung 3 and cannot reach the cause.** Read against the subject device, rung 5 lands exactly on the interface that was shut. Q-013 is not a matter of taste; one reading answers the question and the other does not.

  **A second and larger problem surfaced while measuring this**, and it is not what Q-013 asks about. Rungs 1, 2 **and** 3 are all broken. `BUILD-PLAN.md` T-024 specifies the walker as *"healthy → continue to the next rung; broken → **stop.** That rung's `finding` is the result."* Applied here the descent stops at **rung 1** and reports `peer_not_established` — **the symptom it was invoked to explain.** It never reaches the shut interface, whichever way Q-013 is decided.

  That contradicts the design the ladder exists to serve. D6: *"the lowest broken layer is the root cause"* — but "stop at the first broken rung, descending from the top" stops at the **highest** broken layer. The LLD's own terminal findings (`peer_unreachable_no_route`, `igp_isolated`, `interface_line_down`) are only reachable if the walk continues **past** broken rungs. Under the specified rule, four of the five findings the flow declares are unreachable.

  `t0` hid this completely: there, rung 1 is broken and the deeper rungs have no template fixtures, so stopping at rung 1 looks like correct behaviour. **The `broken` label is what made it visible** — which is precisely the argument the operator made for capturing it.
- **Evidence:** Measured output above, from `checks.*` against `tests/fixtures/cisco_xr/{RR1,PE2}/broken/`. Rung 5's reason on PE2: `"interface Gi0/0/0/0 is not up (admin_state='admin-down', line_state='admin-down')"` — the exact fault that was introduced.
- **What I did:** Stopped at T-022 as agreed rather than choosing. Both questions change the `Rung` contract, and T-022 is the last cheap moment to settle them — retrofitting after `descent.py` exists is what the operator already ruled against for Q-013, and it applies at least as strongly to the walk semantics.
- **Needs human review:** **yes — T-022 is blocked on both.**
- **Blocks:** T-022, T-023, T-024, and therefore T-025/M3.

---

## OBS-056 · T-022 / T-023 · `flows.py` — Q-013 and Q-017 resolved into the contract

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (contract task)
- **What happened:** Both operator decisions are now in the `Rung` contract, plus the `bgp_session` and `interface` ladders. **1181 passed**, 20 new tests.

  **Q-013 → `DeviceScope`.** Each rung declares `LOCAL`, `SUBJECT` or `PATH`. The `bgp_session` ladder switches at the right place, and the measurement that decided it is pinned as a test rather than left in a finding: rungs 1–3 are `LOCAL` (the local FSM, socket and RIB), rungs 4–5 are `SUBJECT`, because RR1's own IS-IS was healthy while PE2's was not.

  **The operator's aggregation addition is enforced, not documented.** A `PATH` rung without an `Aggregation` raises at construction, and so does a single-device rung that declares one. Both directions are tested. Left implicit, the walker would silently pick a rule — and the right rule differs per rung, which is exactly why it cannot be inferred: ECMP routes are `ANY_HEALTHY`, path hops are `ALL_HEALTHY`.

  **Q-017 → `cause_not_localised`** joins `all_layers_healthy` and `undetermined` as universal findings every flow must declare. A test asserts all five `bgp_session` rung findings are reachable — the registry-level form of the bug: under the old walk rule, four of them were not.
- **Evidence:** `tests/test_flows.py`, 20 passed. Ladder verified: `bgp_session(LOCAL) → transport(LOCAL) → route_to_peer(LOCAL) → igp_adjacency(SUBJECT) → interface(SUBJECT)`.
- **What I did:** Three judgement calls worth recording.

  1. **No rung uses `PATH` yet.** `route_to_peer` is the obvious candidate — an ECMP set with `ANY_HEALTHY` — but this fabric's route to a loopback resolves to one entry plus a Local-LFA backup, not a true ECMP set. Declaring `PATH` would be modelling a topology we do not have, and the aggregation machinery would go untested-by-use while looking exercised. `LOCAL` is the honest scope until a fabric needs otherwise; the enum and its enforcement exist for when one does.
  2. **The `interface` flow has one rung.** The plan never specified its ladder (OBS-028), and an interface has nothing below it this tool can observe — optics, cabling and the far end are precisely where the descent bottoms out and hands to a human (D3's ceiling). `checks.interface_state` already folds line state and the counter rate into one ordered verdict, so splitting them across two rungs would double-report the same object.
  3. **`flow_for` raises rather than returning `None`** for a declared-but-unimplemented type, and raises `KeyError` rather than `NotImplementedError` for an unknown one. `None` would let a caller read "no flow" as "nothing wrong" — the same silent-degradation shape this build keeps meeting — and a typo and a deliberate stub are different mistakes that deserve different errors.

  The safety test T-022 asks for is in, mirroring `test_check_tool_intents_exist_in_the_platform_table`, and extended: every template collect step must also name a parameter the template actually has. A bad ladder now fails as a static property of the registry rather than mid-descent against a real device, where it would have looked like a device fault.
- **Needs human review:** no
- **Blocks:** none — T-024 next, implementing the corrected walk.

---

## OBS-057 · T-024 / T-025 · **M3 reached** — the descent produces an RCA, not a restatement

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (semantics task)
- **What happened:** The walker implements the corrected semantics and the acceptance test passes against **both** labels. **1196 passed.** Measured, offline, no lab, no API key, no model call:

  ```
  broken   RR1 -> 10.255.0.12      FINDING: interface_line_down
    bgp_session     RR1   broken
    transport       RR1   broken
    route_to_peer   RR1   broken
    igp_adjacency   PE2   broken
    interface       PE2   broken
    cause         : interface on PE2
    causal chain  : bgp_session -> transport -> route_to_peer -> igp_adjacency
  ```

  That is the sentence the ladder exists to produce: *the interface is down on PE2, which isolated IS-IS, which removed the route, which blocked transport, which is why BGP is Idle.* Under the plan's original rule the same input returned `peer_not_established` and stopped at rung 1. **`healthy` gives `all_layers_healthy` for the same subject**, so the two labels give opposite answers about one peer — which is what makes the result a measurement rather than a coincidence.

  **M3 — "the diagnostic ladder is deterministic end to end" — is demonstrated rather than argued.**
- **Evidence:** `tests/test_descent.py`, 15 tests. Acceptance on both labels, plus the walk semantics against stub ladders: broken does not stop the walk, healthy does not stop it, `unevaluated` does and **nothing below it is collected** (asserted on the collector, not the result).
- **What I did:** Found and closed a real gap in the T-022 contract I had committed an hour earlier. **The walker's first run returned `undetermined` on both labels**, because the ladder crosses four subject vocabularies and the contract had no way to say so: `bgp_session` takes a peer address, `route_to_peer` a prefix, `igp_adjacency` a whole device, and `interface` an interface name — which is **not derivable from a peer address at all**. The collector and the check were each transforming the subject independently and disagreeing silently.

  Fixed by adding **`SubjectRule`** to `Rung`, following the operator's own principle for `Aggregation`: explicit on the rung, never implied by the check. `AS_IS`, `HOST_PREFIX`, `DEVICE_WIDE`, `EACH_PHYSICAL_INTERFACE`. The last one fans out over objects rather than devices, so `Rung.evaluates_a_set` now covers both and an aggregation is required for either.

  **`EACH_PHYSICAL_INTERFACE` excludes subinterfaces, and that is measured rather than tidy.** PE1 and PE3 each carry a `Gi0/0/0/2.300` that is legitimately line-down on a completely healthy fabric; including it would make 2 of 9 devices report broken in the `healthy` label. A subinterface being down is a service condition; a physical link being down is a path condition, and the path is what a descent is about.

  Two smaller decisions worth recording. A `SUBJECT`-scoped rung with **no resolver is `unevaluated`, never a silent fall-back to the local device** — falling back is precisely the wrong-device reading Q-013 exists to prevent, and it would look healthy. And `unevaluated` **dominates any aggregation**: if one member of a set could not be read, the set's verdict is not known either. Absence is not health, at every level.
- **Needs human review:** yes — `SubjectRule` extends the `Rung` contract the operator approved, and `EACH_PHYSICAL_INTERFACE`'s exclusion rule is a judgement grounded in this fabric's shape.
- **Blocks:** none. Part 3 complete.

---

## OBS-058 · T-026 · Prompt library scaffold — the rules are enforced, and Part 4's requirements are written into it

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `prompts/README.md`, the directory structure, and 8 tests. **1202 passed.** No prompts yet — those are T-027 and T-028.

  **Each of the README's four rules has a test.** A rule nobody checks is a preference, and a prompt is an input to a system whose output someone acts on, so it gets the same treatment as the allowlist: versioned filenames, no duplicate versions, a named refusal path in every prompt, and a golden case for each.

  **The operator's three Part-4 requirements are recorded as requirements, not intentions:**

  1. *The report renders the causal chain, not just the finding.* Written into the README with the concrete contrast — `"BGP is down. Cause: interface_line_down on PE2"` versus the five-clause chain — and pinned by a test asserting the README still carries it. A report that states the finding without the four rungs above it is an assertion where the chain is an argument.
  2. *Grounding covers the chain.* The mapping is recorded because it is cleaner than I expected: **every rung in the chain is an `observation`** carrying the evidence key its `CheckResult` already read, and **the lowest broken rung is the `interpretation`**, citing the observations above it. The observation/interpretation split was specified before the causal chain existed and takes it without modification.
  3. *T-032 asserts both labels.* Recorded against that task.
- **Evidence:** `tests/test_prompts.py`, 8 tests. `prompts/` with README and `tests/cases/`.
- **What I did:** Two things worth recording.

  **The missing Evaluation slot is enforced, not just explained.** A test bans `"verify that every claim is supported by the provided data"` and its neighbours from any prompt file. That exact clause is in `llm_analysis.py`'s existing `TROUBLESHOOTING_PROMPT` — it reads as reassurance and provides none. Evaluation here is `grounding.py` and the schema validator, both of which run every time without anyone's attention, which is the property a prompt clause cannot have. Banning it stops the reassuring version drifting back in.

  **The two parametrized rule tests currently *skip*, because there are no prompts to run them against** — pytest reports "empty parameter set" and moves on. That is honest but silent, and a skipping rule is an unenforced rule. This build has already shipped one vacuously-passing guardrail before catching it (OBS-054), so there is now a test asserting the scaffold state explicitly, which **fails the moment the first prompt lands** — the signal to confirm those two tests actually run rather than still skip. Same handover mechanism as `test_registry_is_empty_until_the_parsers_land` at T-012.
- **Needs human review:** no
- **Blocks:** none — T-027 next.

---

## OBS-059 · T-026 · `llm_analysis.py` carries a live instance of the clause the prompt library bans

- **Kind:** defect
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `tests/test_prompts.py` bans `"verify that every claim is supported by the provided data"` from any file in `prompts/`. **That exact sentence is live in `llm_analysis.py`'s `TROUBLESHOOTING_PROMPT` today**, as its `Evaluation:` section — it has been there since before this build started, and it is what the ban was written against.

  It is harmless in the sense that nothing depends on it working: it asks the model to check its own output, the model complies or does not, and either way the answer is unverified. That is precisely the objection. It reads as a guarantee and provides none, and a reader comparing it against `prompts/README.md` would find the repository contradicting itself about whether self-evaluation is part of the assurance story.
- **Evidence:** `src/agent_nettools/llm_analysis.py`, `TROUBLESHOOTING_PROMPT`, the `Evaluation:` block. The ban: `tests/test_prompts.py::test_no_prompt_asks_the_model_to_mark_its_own_work`.
- **What I did:** **Logged, not fixed** — that prompt predates the library, is not covered by its rules, and is consumed by `analyze_evidence`/`analyze_fabric`, neither of which is in MVP-0's path. Changing it now would be an out-of-scope edit to a shipped prompt whose output is pinned by existing tests, for no benefit to the current task.

  Filed as **B-413**, to be removed when that prompt is next touched — which under the library's own rules is a version bump, not an in-place edit. Recording it rather than leaving it because a banned pattern living in the codebase is exactly how a ban erodes: the next person to read `README.md` and then `llm_analysis.py` learns the rule is aspirational.
- **Needs human review:** no
- **Blocks:** none.

---

## OBS-060 · T-027 · The `report` prompt, its three golden cases — and §0.12's handover firing on the day it was written

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `prompts/report.v1.txt`, a versioned loader/renderer, and 13 golden tests. **1217 passed.**

  **All three of the operator's requirements are pinned, and all three cases come from committed fixtures:**

  | Case | Source | Finding |
  |---|---|---|
  | localised cause | the `broken` label | `interface_line_down` on PE2, 4-rung chain |
  | `cause_not_localised` | **composed** — `broken`'s BGP section over `healthy`'s lower layers | `cause_not_localised`, empty chain |
  | refusal path | the `t0` label | `undetermined`, stopped at `transport` |

  The composed case is the only synthetic one, and it is synthetic in a specific and defensible way: **both halves are real captured output, only the combination is invented.** No consistently-behaving fabric can produce "session Idle, every layer beneath it healthy" — that is what makes the finding worth having and also what makes it uncapturable.

  The refusal case needed no construction at all. `t0` predates template capture, so its transport rung genuinely cannot be read, and the descent stops there with `undetermined`. Real data, real gap.
- **Evidence:** `tests/test_report_prompt.py`, 13 passed. `prompts/tests/cases/report.cases.json` is **read by the tests**, not decoration — a case file nothing reads is exactly the ornamental artifact these rules exist to prevent.
- **What I did:** Three things worth recording.

  **§0.12's handover mechanism fired the same day it was written, and caught something real.** Committing `report.v1.txt` broke `test_the_library_is_still_a_scaffold_and_the_rule_tests_are_skipping`, exactly as designed. What it forced me to check was whether the two parametrised rule tests had actually switched from skipping to running — and pytest now reports them as `[report.v1.txt]`, so they had. Without that companion test, the rules would have quietly kept skipping and I would have had no reason to look.

  **Invariant 4 is structural here, not filtered.** `build_report_prompt` cannot leak device output because it never receives any: it takes a `DescentResult`, which holds verdicts, reasons and evidence keys, and serialises exactly those. A test asserts no recognisable IOS-XR output (`RP/0/RP0/CPU0`, `Routing entry for`, `BGP neighbor is`, …) appears in the rendered prompt. A redaction pass over text that *might* contain raw output is something somebody eventually gets wrong; a function that never holds the text cannot.

  **One test failed for the wrong reason and I fixed the test.** An assertion on `"Do not supply a likely cause"` failed because the prompt wraps that phrase across a line break. That is a test depending on formatting — precisely the mistake these golden tests exist to avoid — so matching now collapses whitespace first. The operator's rule applied to itself: *if a test fails because of phrasing, the test is wrong.*
- **Needs human review:** no
- **Blocks:** none — T-028 next.

---

## OBS-061 · pattern · Structural containment beats filtering — a function that never holds the text cannot leak it

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5 (operator-identified as a pattern)
- **What happened:** Recorded as a **pattern rather than a T-027 detail**, at the operator's direction, because it has now appeared three times in this design independently and it should shape the tasks that have not been written yet.

  The shape: **when something must not escape, build the boundary so the dangerous value never reaches the function, rather than removing it on the way out.**

  | Where | Structural form | The filtering alternative that was rejected |
  |---|---|---|
  | `templates.py` (Phase 5) | Canonicalize by reconstruction — the command is rendered from a *parsed object's* canonical form, so caller text never reaches it | Validate the text with a regex, then interpolate the text |
  | `prompt_library.build_report_prompt` (T-027) | Takes a `DescentResult` of verdicts and evidence keys; **never receives device output at all** | Redact raw output from an assembled prompt |
  | `notifier` (T-035, specified) | Receives the report object only, never the evidence bundle | Filter secrets out of a bundle before sending |

  The argument is the same each time and it is not about diligence: **a redaction pass over text that might contain the dangerous value is something somebody eventually gets wrong** — a new output format, an unanticipated field, a regex that was broad enough last year. A function that never holds the value cannot leak it regardless of who edits it next, and the guarantee survives people who have never read the rule.

  `CLAUDE.md` already states this for `templates.py`: *"A regex broad enough to accept every legitimate value is also broad enough to admit a lookalike nobody anticipated."* The generalisation is that the same reasoning governs egress, not just ingress.
- **Evidence:** `test_the_rendered_prompt_carries_no_raw_device_output` asserts five IOS-XR output markers are absent from a rendered prompt — but the test is a check on the property, not the mechanism. The mechanism is the signature.
- **What I did:** Recorded it against **T-030** and **T-035**, the two tasks where it is still a live choice. T-030's `investigate()` assembles what the model sees, and the temptation there is to pass the evidence bundle "in case the model needs it"; T-035's notifier already has the right shape specified and needs it kept. Also noted the test-design consequence: a test that asserts the dangerous value is absent is worth having, but it verifies the property while the *signature* is what guarantees it — if the two ever disagree, the signature is the thing to fix.
- **Needs human review:** no
- **Blocks:** none. Informs T-030 and T-035.

---

## OBS-062 · T-028 · Window shaping in code; and `show logging` turns out to be *better* than Loki here, not a fallback

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `correlate.v1.txt`, `log_window.py`, and 16 golden tests. **1235 passed.**

  **The operator's question — filter in code or leave it to the model — decided as code**, and the measurement is the argument. PE2's `broken` window is 200 entries / 37,962 characters, of which **137 are the collector's own SSH sessions** and 43 more are `exec`-session registration churn from those same sessions. **20 genuine network events remain.** Three reasons, in increasing order of weight: context budget (~34,000 characters of noise re-read on every call); testability ("the model usually ignores SSH churn" is not a property anything can assert); and **determinism** — the same window must yield the same filtered set every time, or two runs of one investigation can correlate against different evidence. Handing that to a probabilistic step at the last moment would give away the reproducibility the whole deterministic-descent argument rests on.

  Because filtering is code, **what was dropped is knowable**: `shape_window` reports the counts, so a report can say "20 of 200 entries were network events" rather than presenting 20 and implying that was all there was.

  **The 20 events are the entire incident, timestamped:**

  ```
  07:41:54.688  Gi0/0/0/0 -> Administratively Down
  07:41:54.688  Gi0/0/0/1 -> Administratively Down
  07:41:54.688  ISIS adjacency to P1 Down
  07:41:54.690  ISIS adjacency to P3 Down
  07:41:54.712  Configuration committed by user 'clab'
  07:44:28.097  BGP neighbor 10.255.0.31 Down - hold time expired
  ```

  That is D6's stated value of the historical axis, present and checkable: the descent says the interface is down, the logs say *when*, and whether it coincided with a commit. It did — in the same second, 154 seconds before BGP followed.
- **Evidence:** `tests/test_correlate_prompt.py`, 16 passed. Both golden cases captured whole, neither composed.
- **What I did:** Three findings worth separating from the task.

  **1. `show logging` is not a fallback for this fabric — it is strictly better than Loki.** Every one of those causal events is severity 5 or 6 (`ADJCHANGE`, `CHANGED`, `LINEPROTO`, `DB_COMMIT`), and per OBS-041/B-206a **none of them reaches Loki at all.** The operator approved `show logging` as a substitute for a blocked path; measured, it is the only path that carries the evidence. B-206 remains worth doing for cross-device and device-unreachable cases, but it should not be described as an upgrade to this.

  **2. A measured correction to OBS-014.** That finding saw one event stored 1,346 times and concluded any count over the corpus is fiction without deduplication. True of **Loki**; **not** true of the device's own buffer, where deduplicating removes **exactly zero** records. The duplication is introduced by the syslog pipeline, not present at source. `dedupe` is kept and pinned by a test asserting it is a no-op here — load-bearing for B-206, dead-looking until then, and now documented so nobody removes it.

  **3. Subject filtering exists and is off by default, because using it would discard the cause.** The events explaining an isolated peer never name the peer: on the `broken` label the causal lines say `GigabitEthernet0/0/0/0` and `P1`, not `10.255.0.12`. Filtering a `bgp_session` window to its subject throws away exactly what correlation needs. Tested in that direction.

  Also: **my own T-026 rule was too narrow and failed on its second prompt.** `test_every_prompt_names_its_refusal_path` asserted the literal word `undetermined`, which is `report`'s refusal but not `correlate`'s — a correlation with nothing to correlate returns `found: false`. The rule was right, the check was written from a single example. Each prompt's case file now declares its own `refusal_marker` and the test reads it. Same shape as three of the six parser specs: **a rule generalised from one instance fits one instance.**
- **Needs human review:** no
- **Blocks:** none — T-029 next.
- **Operator response (2026-08-16):** all three accepted, and **all three correct things the design documents asserted, not things the implementation got wrong.** Recorded explicitly because the direction matters: `evidence-reduction.md` §3 reduction 4 (aggregation as a major reduction), §3 reduction 5 (project to subject), and §9 (Loki as the real source, `show logging` as the fallback) were each written before the measurement and each contradicted by it. **The document was wrong; the implementation was right.** All three amended at OBS-063. A finding that corrects a specification is worth more than one that corrects code, because the specification was going to be built from again.

---

## OBS-063 · reconciliation · `evidence-reduction.md` read against `log_window.py` — and the one divergence that went the other way

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator added `docs/design/evidence-reduction.md` (219 lines) after T-028 shipped and asked for reconciliation rather than re-implementation: where document and implementation disagree, the implementation wins and the document is amended. **Six divergences. Four went that way. One went the other way, and it was worth more than the other five together.**

  **The one that went the other way — §3 reduction 2, "filter by source, not by content".** `drop_collector_noise` dropped every record in two *facilities*, `SECURITY-SSHD_SYSLOG_PRX` and `SYSDB-SYSDB`. That is a content rule wearing a provenance label: it deletes SSH events because they are *usually* the collector here, having never established that any particular one was.

  Measured cost on PE2's `broken` window: **eight** entries deleted that nothing attributes to this tool. All eight are **severity 3 — the highest-severity records on the device** — and one is

  ```
  Aug 16 07:41:32.006 UTC  sshd[202504]: process_output:
    ssh_packet_write_poll: Connection reset by peer
  ```

  an interactive session dying **22 seconds before the interfaces went down**. On this fabric that was my own capture script. In production the same line is an operator's session dropping mid-change, which is exactly what a timeline is for. **A noise filter that can silently delete the tool's own damage is the wrong filter.**

  Corrected: every `NoiseRule` must declare *how* it attributes a record, and there are two honest forms — `SOURCE_ADDRESS` (every address the line names is a known management host; measured, all 2,723 SSH records fabric-wide name only `.1/.2/.5/.6`, and the devices are `.11`–`.31`) and `GENERATING_PROCESS` (the line names the process and the session object; measured, 843 of 843 SYSDB records name both `client 'exec'` and `/vty/`). **A record whose provenance cannot be established is kept**, and `unattributed_kept` reports how many, so the conservative choice stays visible instead of becoming folklore.

  Note what `COLLECTOR_SOURCES` is *not*: a subnet test on `172.20.250.0/24`. The devices' own management interfaces are in that /24, so a subnet rule would attribute a device-sourced session to the collector — silently. A test asserts no device address is in the set.
- **Evidence:** `log_window.py` rewritten; `tests/test_correlate_prompt.py` 27 tests; `prompts/correlate.v2.txt`; `docs/design/evidence-reduction.md` §10 divergence log. **1245 passed, lint clean.**
- **What I did:** Four things beyond the fix.

  **1. The fix to one failure mode produced the test for another.** Correcting the filter made PE2's `healthy` window non-empty: nine severity-3 `Connection closed by remote host` lines survive. That broke the refusal golden case, which asserted `entries_retained: 0` — and the replacement is strictly better. An empty window tests constraint 6, which is nearly free. Nine urgent-looking lines that are *not* related test constraint 5, which is where a model actually fails. `empty_window` was added as a separate constructed case so constraint 6 stays covered.

  **Over-reach and empty-result are the same failure seen from two ends**: a filter aggressive enough to guarantee an empty window has already deleted the evidence that would have filled it.

  **2. `correlate.v2.txt`, because an honest filter creates a new hazard.** Leaving unattributable records in the window means the model is now shown high-severity lines that are not evidence. v2's grounding states both halves — why they are there, and that *retention is not relevance* — plus a new constraint 7: "a high-severity entry is not thereby a relevant one. Severity ranks how loudly a device reports something, not whether it bears on this finding." v1 stays in the tree; `prompt_library.CURRENT_VERSION` is now a single reviewable table rather than a default buried in six signatures.

  This was a version bump and not an edit even though **v1 had never produced a report**, so nothing was unreproducible. Carving the first exception to a rule on the day after writing it is how the rule stops meaning anything.

  **3. §7's five failure modes: three covered, one covered by the fix, one filed.** Template collision (`dedupe` keys on the mnemonic, so two event types cannot merge however similar their text), over-aggregation (one `ROUTING-BGP-5-ADJCHANGE` among 3,000 routine records, asserted to survive — no reduction in the module has a minimum-count threshold), noise over-reach (the defect above, now with a corpus containing collector churn *and* a genuine foreign-address auth failure *and* an unattributable error), empty result (twice over). **Clock skew is filed as B-415**, untestable today for an honest reason: every window this layer reads is single-device, so there are no two clocks to disagree. It arrives with B-206 and belongs in the same change.

  **4. Reductions 3 and 4 are not implemented, and that is a scope statement rather than a gap.** The document is right that the mnemonic already *is* the template key — the parser emits it as a field, so grouping is a `Counter` and no clustering algorithm is required. But at 28 records the aggregate carries no information the records do not, and collapsing them costs the verbatim ordering the timeline is built from. Filed as **B-414**, with the one property that must hold on day one pinned by a test *now* rather than described in the backlog item.
- **Needs human review:** no — but the direction of divergence 5 is worth the operator's attention
- **Blocks:** none — T-029 next.

---

## OBS-064 · pattern · A test written from the same premise as the implementation confirms the premise, not the implementation

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The facility-based noise filter had sixteen tests. Every one passed. They passed because they were written from the same assumption the code was: that `SECURITY-SSHD_SYSLOG_PRX` means "the collector". Nothing in the suite could have caught it, because the suite and the defect share an author and a premise.

  What caught it was a specification written independently, from the problem rather than from the code. `evidence-reduction.md` §3 reduction 2 states the rule in one sentence — *filter by source, not by content* — and the violation is visible the moment the code is read against it.

  > **A test written from the same premise as the implementation confirms the premise, not the implementation. An independent specification is the only thing that catches a premise.**

  This is the third distinct form of the same underlying problem this build has hit. §0.12 covers the guardrail that measures nothing. OBS-062 covers the rule generalised from one instance. This one covers the test that agrees with the code by construction. All three are *green things that verify nothing*, and none of them is detectable from inside the artefact that has the problem.
- **Evidence:** OBS-063 divergence 5. Sixteen passing tests over a filter deleting eight severity-3 records unattributed.
- **What I did:** Recorded as a standing observation rather than a task finding. The practical consequence is narrow and worth stating: **when a design document arrives for code that already exists, read the document against the code rather than the code against the document.** The second reading finds nothing — every line of code justifies itself.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-065 · pattern · A rule generalised from one instance fits one instance

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Operator observation at T-028, generalised here and placed alongside §0.12 as standing practice.

  Three instances in this build, all the same shape:

  | Instance | The rule | What it was generalised from | How it failed |
  |---|---|---|---|
  | T-013 | "A route entry has a next hop" | 44 fixtures grouped by line 4 — which is identical across all three route shapes | A directly-connected local route has no next hop at all |
  | T-026 | "Every prompt names its refusal path as `undetermined`" | `report`, the only prompt that existed | `correlate`'s refusal is `found: false` |
  | T-028 | "SSH facility records are collector noise" | A corpus where they happened to be | Eight severity-3 records deleted unattributed (OBS-063) |

  In each case **the rule was right and the check was written from a single example.** That is the distinction worth keeping: the failure is not bad generalisation, it is a correct generalisation encoded at the wrong width. "Every prompt names its refusal path" is true; "every prompt says `undetermined`" is that same true rule, narrowed to its one witness.

  > **A survey is a sample. A rule generalised from one instance fits one instance. Encode the rule at the width of the rule, and let the instance supply the value.**

  The mechanical form is small: the check reads the value from data the instance declares, rather than hardcoding the value the first instance had. `refusal_marker` in each case file; `IgnoreRule` per parser; `NoiseRule.attribution` per facility. In every one of the three, the fix was to move a constant out of the assertion and into a declaration.
- **Evidence:** OBS-021 (T-013), OBS-062 (T-026), OBS-063 (T-028).
- **What I did:** Recorded as standing practice alongside §0.12. Not written into BUILD-PLAN Part 0 as a numbered rule — §0.12 earned that because it prescribes an action (write the companion test). This one is a reading habit, and Part 0 is stronger for holding only rules that can be checked.
- **Needs human review:** no
- **Blocks:** none

---

<!--
Copy this block for each new entry.

## OBS-nnn · T-xxx · <short title>

- **Kind:**
- **Escalation:** HALT | DECIDE-AND-LOG | NOTE
- **Model:** opus-5 | sonnet-5 | fable-5
- **What happened:**
- **Evidence:**
- **What I did:**
- **Needs human review:**
- **Blocks:**

---
-->

# Open Questions

Anything logged with `Needs human review: yes` is mirrored here so the review has one list to work from.

| ID | Raised in | Question | Blocking? | Status |
|----|-----------|----------|-----------|--------|
| Q-001 | T-002 | Does `reasoning_split: true` fully suppress `<think>` in `content`? If not, is a stripping step acceptable, or should the gate use the Anthropic-compatible route instead? | Yes — gate depends on it | **Resolved (OBS-005)** — yes, fully. No stripping step, no route change. Must be set explicitly on every call. |
| Q-002 | T-004 | Is syslog-ng shipping to Loki, and do IOS-XR mnemonics survive into a queryable label? | No — affects Stage 2 only | **Resolved (OBS-013)** — ships to file *and* Loki; mnemonics survive on 100% of lines but in the body, not as a label. Extraction belongs in T-015's parser. |
| Q-014 | T-008 | `ttp` added as a **core** dependency rather than an optional extra, deviating from T-008's wording. Rationale: `run_template` attaches parsed data on every call from T-018, so an extra would make the descent silently unavailable on a default install. | No — decided and green | **Closed (OBS-035)** — accepted; an extra would be silent degradation on a default install |
| **Q-015** | **T-011** | **HALT.** Executing the PE2 `Gi0/0/0/0` shutdown is a device state change — §0.11's absolute HALT, under a standing instruction that explicitly overrides later session instructions. The script is written and ready. **Does the operator waive §0.11 for this single pre-planned, reversible action, or run it themselves?** | **Yes — blocks T-011 and everything after it** | **Resolved (OBS-038)** — waiver granted, exercised, discharged. Fabric verified restored. |
| **Q-016** | **T-011** | Shutting one uplink does not isolate PE2 — it has two, and the IGP routed around it (OBS-039). Isolating it needs **both** `Gi0/0/0/0` and `Gi0/0/0/1` shut in one commit. Schedule a second window, or accept `healthy` + `t0` as sufficient for MVP-0? **T-025/M3 are not blocked either way.** | No — nothing downstream is blocked | **Resolved (OBS-049)** — second window run, both uplinks in one commit, PE2 fully isolated, 216 files captured |
| **Q-017** | **T-024** | **The specified walk semantics make four of five findings unreachable.** T-024 says "broken → stop", but rungs 1–3 are all broken for `RR1 → 10.255.0.12`, so the descent stops at rung 1 and reports the symptom (`peer_not_established`), never the shut interface. D6 says the *lowest* broken layer is the root cause. | **Yes — blocks T-022/T-023/T-024/T-025** | **Resolved (OBS-056)** — operator confirmed the plan was defective. New semantics in T-024/T-025 and D6; `cause_not_localised` added. |
| Q-013 | T-022 | Does a `Rung` carry its own device scope? The `bgp_session` descent's lower rungs (route, IGP adjacency, interface) concern the *path*, not the subject device — checking RR1's own IS-IS adjacencies would miss that PE2 is the isolated one. | **Yes — blocks T-022/T-023/T-024** | **Resolved (OBS-056)** — `DeviceScope` enum on `Rung`, option (a)+(b); subject resolution beside the registry. Aggregation required for `PATH`, enforced at construction. |
| Q-011 | T-004 | Should the devices' `logging trap` level be lowered so severity-5 events (`%BGP-5-ADJCHANGE`, IS-IS transitions) reach Loki? Today only `err`/`warning` arrive, so the events T-028 correlates against are absent entirely. Operator decision — it changes log volume on a pipeline already carrying 97% self-generated noise. | No for MVP-0 · **yes for a useful historical axis** | Open (OBS-014) |
| Q-003 | T-005 | Does Alertmanager have a webhook receiver, and can it replace n8n as the Stage 2 trigger? | No — Stage 2 | **Resolved (OBS-016)** — yes to both. Gap is that no alert rule carries a device label; that is rule authoring, not infrastructure. |
| Q-004 | T-006 | What is the subject naming scheme for an L3VPN service object? | No — flow not in MVP-0 | **Accepted (OBS-035)** — `<pe>:<vrf>` recommended; `<vrf>:<rd>` eliminated because RD is reused across PEs. Confirm at T-022. |
| Q-005 | T-020 | What error-counter threshold should `interface_state` treat as broken? **And what does it do when the counters are absent?** A line-down interface omits them entirely (OBS-044) — the answer must be `unevaluated`, never "0 errors, healthy". | No — but the absent-counter half is a correctness trap | **Both halves answered.** First: a rate, not a total (OBS-052). Second (OBS-051) — `unevaluated`, now a stated rule in `checks.py`. Threshold value still open for T-020. |
| Q-006 | T-025 | Does the descent's stopping rung match what a network engineer would conclude by hand from the same fixtures? | **Yes — this validates the architecture** | Open |
| Q-007 | T-035 | Telegram or Mattermost? Hosted means device names, IPs and RCA text leave the estate; self-hosted keeps them in. Decide before implementing — only one provider gets built. | Yes for T-035 | Open |
| Q-008 | T-035 | Which host runs `nettools` in the target deployment, and does it have outbound egress to the chosen channel? | Yes for T-035 | Open |
| Q-010 | T-003 | The MiniMax provider uses the OpenAI **Responses** API, not Chat Completions, so `BUILD-PLAN.md` T-003 step 4 (`reasoning_split`, `max_completion_tokens`) does not apply. Both behaviours it targeted are achieved structurally on that route. Confirm the route choice before the MVP-1 gate is built on it. | No for MVP-0 · **yes for the MVP-1 gate** | Open — decided and evidenced (OBS-010) |
| Q-012 | T-005 | **The lab was rebuilt ~2 days ago and is now healthy** — all 16 BGP sessions Established, PE2/PE4 back to 2 IS-IS adjacencies. T-011 says to capture "against the current broken state", which no longer exists. Re-break the lab, capture a new consistent healthy label, or build the broken case synthetically in-test? | **Yes for T-011** (T-025/M3 unaffected — fixtures still hold the broken state) | **Closed (OBS-049)** — `broken` captured 2026-08-16 with both uplinks. Originally (OBS-019) — options 1+2: keep `t0`/`t1` frozen, add complete `healthy` and `broken` labels; operator runs the break, capture coordinated at T-011 |
| Q-009 | T-002 | Should `MINIMAX_API_KEY` **and the lab device credentials** be rotated after this build? Both were pasted into the transcript (OBS-008, OBS-037). It was pasted into the session transcript, which no control in this repository can revoke. | No — nothing is blocked on it | Open — recommended (OBS-008) |

---

Task status lives in `TRACKER.md`, not here. This file records *what was learned*; the tracker records *what was done*.
