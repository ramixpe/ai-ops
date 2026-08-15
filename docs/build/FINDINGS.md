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
| Q-014 | T-008 | `ttp` added as a **core** dependency rather than an optional extra, deviating from T-008's wording. Rationale: `run_template` attaches parsed data on every call from T-018, so an extra would make the descent silently unavailable on a default install. | No — decided and green | Open — confirm (OBS-029) |
| Q-013 | T-022 | Does a `Rung` carry its own device scope? The `bgp_session` descent's lower rungs (route, IGP adjacency, interface) concern the *path*, not the subject device — checking RR1's own IS-IS adjacencies would miss that PE2 is the isolated one. | **Yes — blocks T-022/T-023/T-024** | Open (OBS-020) — decide at T-022 |
| Q-011 | T-004 | Should the devices' `logging trap` level be lowered so severity-5 events (`%BGP-5-ADJCHANGE`, IS-IS transitions) reach Loki? Today only `err`/`warning` arrive, so the events T-028 correlates against are absent entirely. Operator decision — it changes log volume on a pipeline already carrying 97% self-generated noise. | No for MVP-0 · **yes for a useful historical axis** | Open (OBS-014) |
| Q-003 | T-005 | Does Alertmanager have a webhook receiver, and can it replace n8n as the Stage 2 trigger? | No — Stage 2 | **Resolved (OBS-016)** — yes to both. Gap is that no alert rule carries a device label; that is rule authoring, not infrastructure. |
| Q-004 | T-006 | What is the subject naming scheme for an L3VPN service object? | No — flow not in MVP-0 | **Answered (OBS-023)** — `<pe>:<vrf>` recommended; `<vrf>:<rd>` eliminated because RD is reused across PEs. Confirm at T-022. |
| Q-005 | T-020 | What error-counter threshold should `interface_state` treat as broken? | No — default chosen, needs review | Open |
| Q-006 | T-025 | Does the descent's stopping rung match what a network engineer would conclude by hand from the same fixtures? | **Yes — this validates the architecture** | Open |
| Q-007 | T-035 | Telegram or Mattermost? Hosted means device names, IPs and RCA text leave the estate; self-hosted keeps them in. Decide before implementing — only one provider gets built. | Yes for T-035 | Open |
| Q-008 | T-035 | Which host runs `nettools` in the target deployment, and does it have outbound egress to the chosen channel? | Yes for T-035 | Open |
| Q-010 | T-003 | The MiniMax provider uses the OpenAI **Responses** API, not Chat Completions, so `BUILD-PLAN.md` T-003 step 4 (`reasoning_split`, `max_completion_tokens`) does not apply. Both behaviours it targeted are achieved structurally on that route. Confirm the route choice before the MVP-1 gate is built on it. | No for MVP-0 · **yes for the MVP-1 gate** | Open — decided and evidenced (OBS-010) |
| Q-012 | T-005 | **The lab was rebuilt ~2 days ago and is now healthy** — all 16 BGP sessions Established, PE2/PE4 back to 2 IS-IS adjacencies. T-011 says to capture "against the current broken state", which no longer exists. Re-break the lab, capture a new consistent healthy label, or build the broken case synthetically in-test? | **Yes for T-011** (T-025/M3 unaffected — fixtures still hold the broken state) | **Resolved (OBS-019)** — options 1+2: keep `t0`/`t1` frozen, add complete `healthy` and `broken` labels; operator runs the break, capture coordinated at T-011 |
| Q-009 | T-002 | Should `MINIMAX_API_KEY` be rotated after this build? It was pasted into the session transcript, which no control in this repository can revoke. | No — nothing is blocked on it | Open — recommended (OBS-008) |

---

Task status lives in `TRACKER.md`, not here. This file records *what was learned*; the tracker records *what was done*.
