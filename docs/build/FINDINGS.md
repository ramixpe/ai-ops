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
| Q-011 | T-004 | Should the devices' `logging trap` level be lowered so severity-5 events (`%BGP-5-ADJCHANGE`, IS-IS transitions) reach Loki? Today only `err`/`warning` arrive, so the events T-028 correlates against are absent entirely. Operator decision — it changes log volume on a pipeline already carrying 97% self-generated noise. | No for MVP-0 · **yes for a useful historical axis** | Open (OBS-014) |
| Q-003 | T-005 | Does Alertmanager have a webhook receiver, and can it replace n8n as the Stage 2 trigger? | No — Stage 2 | Open |
| Q-004 | T-006 | What is the subject naming scheme for an L3VPN service object? | No — flow not in MVP-0 | Open |
| Q-005 | T-020 | What error-counter threshold should `interface_state` treat as broken? | No — default chosen, needs review | Open |
| Q-006 | T-025 | Does the descent's stopping rung match what a network engineer would conclude by hand from the same fixtures? | **Yes — this validates the architecture** | Open |
| Q-007 | T-035 | Telegram or Mattermost? Hosted means device names, IPs and RCA text leave the estate; self-hosted keeps them in. Decide before implementing — only one provider gets built. | Yes for T-035 | Open |
| Q-008 | T-035 | Which host runs `nettools` in the target deployment, and does it have outbound egress to the chosen channel? | Yes for T-035 | Open |
| Q-010 | T-003 | The MiniMax provider uses the OpenAI **Responses** API, not Chat Completions, so `BUILD-PLAN.md` T-003 step 4 (`reasoning_split`, `max_completion_tokens`) does not apply. Both behaviours it targeted are achieved structurally on that route. Confirm the route choice before the MVP-1 gate is built on it. | No for MVP-0 · **yes for the MVP-1 gate** | Open — decided and evidenced (OBS-010) |
| Q-009 | T-002 | Should `MINIMAX_API_KEY` be rotated after this build? It was pasted into the session transcript, which no control in this repository can revoke. | No — nothing is blocked on it | Open — recommended (OBS-008) |

---

Task status lives in `TRACKER.md`, not here. This file records *what was learned*; the tracker records *what was done*.
