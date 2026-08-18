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
- **Promoted 2026-08-16:** the operator made this **BUILD-PLAN §0.13**, alongside §0.12, and named it the fifth and widest of the build's silent-failure shapes. §0.13 carries the full table of all five and the second practical consequence: a passing suite is not acceptance for a component that encodes a judgement about the world.
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

## OBS-136 · Gate Zero · A missing state does not fail loudly; it rounds to the nearest one

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Gate Zero's reconciliation gave every backlog item a state from `OPEN` · `DONE` · `BLOCKED` · `SUPERSEDED` · `CLOSED-AS-MEASURED` · `unverified`. Cross-checking it against `PLAN-V2.md` found 30 open-or-unverified items named nowhere in the plan; most are covered by a Stage 2/Stage 3 block rather than by ID, which is fine. Eight are not, and all eight are wrong in the same way.

  **`B-443`–`B-450` are the peer review's §5 deferrals.** Each carries an explicit trigger — *"until a second address family or a VRF-scoped session exists"*, *"until any multi-device concurrent deployment"*, *"until a characterised failure envelope exists"*. My pass marked them `unverified` because they have no `FINDINGS.md` entry.

  `unverified` means **nobody has looked**. These were looked at by three independent reviewers, in detail, and deliberately not scheduled with a stated condition. They are among the **best-documented items in the file**, and they received the label meaning the opposite.
- **Evidence:** `BACKLOG.md` reconciliation table before this change; the eight items' own rows in the main table, each of which contains the word *"Deferred until …"*.
- **What I did:** Added `DEFERRED` — *examined, not scheduled, with the unblocking condition as its evidence* — and recategorised. Also corrected four individual rows: **B-441** is `DONE` (`MVP0-REVIEW.md:203` reports all three strata and names the sampling frame, which is exactly what the item asked for); **B-448** is `BLOCKED` on hardware with B-401, not deferred by choice; **B-412** is `BLOCKED` because it lives in `~/ai-agent-ops/faultlab/`, outside this repository, and cannot be closed from here (OBS-135); **B-453** and **B-435** are `OPEN` and were scheduled nowhere.

  **The vocabulary had `BLOCKED` (*cannot*) and `unverified` (*not examined*) and no state for *examined, deferred, with a condition*.** That third thing is not a shade of either.

  > **A missing state does not fail loudly. It silently rounds to the nearest available one**, and the rounding is invisible afterwards because the result is a well-formed table.

  **This is Gate Zero's own lesson, one level down, and that is the part worth keeping.** Gate Zero exists because a plan was written from item *titles* rather than item *content* (OBS-123, OBS-124). The remedy was to read every item and assign a state. I then read all 95 items and assigned states through a vocabulary that could not express what eight of them said — so the second pass reproduced the first pass's failure through a different mechanism, having been designed specifically to prevent it.

  The general form, which is why this is a finding and not a fix: **a classification is bounded by its categories, and a category that does not exist cannot be reported as missing.** Same family as §0.13 — the artefact was internally consistent, every row well-formed, and nothing in the table could have revealed the gap. Only comparing the table against the items' own prose did.

  **Counts after correction, 95 items:** 34 `DONE` · 33 `unverified` · 14 `BLOCKED` · 7 `DEFERRED` · 6 `OPEN` · 1 `CLOSED-AS-MEASURED`. Twelve rows changed state.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-137 · H2 · A correction pass fixes the claims it was handed, not the ones it was not

- **Kind:** audit
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The publication audit swept six documents for claims. `peer-review-response.md` §4 had already catalogued **five** claims exceeding their evidence and they were corrected. This pass found **ten places where two documents state the same fact differently**, and none of the ten was on §4's list.

  **Two are substantive.**

  **(a) Whether grounding caught the fabricated timestamp.** `MVP0-REVIEW.md:145` read *"The grounding gate refused a fabricated timestamp, which is evidence that the gate works."* It did not refuse it. The report path's citations were checked; the correlation path had **no citation check at all** (OBS-085), which is why `grounding.check_timeline_citations` was then written. `README.md:107` states this correctly and precisely. `chaos-harness.md` said *"passed grounding"* — directionally right, but it describes a gate letting something through when the fact is that no gate ran, and that looseness is what makes the inversion easy.

  The sentence turned a hole into evidence of soundness, in the document that serves as the M4 gate, in the answer to *"does the model layer degrade gracefully"*.

  **(b) Two documents still teach the walk rule Q-017 identified as a defect.** `glossary.md:48` — *"Stops at the first broken rung, which is the root cause"* — and `lld-investigation-layer.md:222` — *"`broken` → stop."* That is the pre-Q-017 specification, under which four of five findings are unreachable. The code has never implemented it. **The glossary is the file `CLAUDE.md` tells a reader to consult first.**
- **Evidence:** `FINDINGS.md:1893` — *"the correlation path has no citation check at all, and it fabricated a timestamp"*. Q-017/OBS-056 for the walk rule. Full list of ten in the commit message and in this entry's table below.

  | # | Conflict | Resolution |
  |---|---|---|
  | 1 | grounding caught / did not catch | did **not** — MVP0-REVIEW corrected, chaos-harness tightened |
  | 2 | walk rule stop / continue | **continue** — glossary and LLD corrected, LLD's left visible as superseded |
  | 3 | 97 vs 86 findings | 138 today, 97 at M4; both numbers dated |
  | 4 | LLDP self-contradictory | **it is not** (OBS-103) — README and evidence-reduction corrected, and the count-only rule kept on its surviving reason |
  | 5 | MCP tools 21 / 20 / ~22 | **21** — design docs dated, and the LLD's list flagged for naming two tools B-438 removed |
  | 6 | `correlate` v1 in the tree, v3 in the history | tree listing completed |
  | 7 | 1377 / 554 / 1365 / 16 tests | **1776 / 24** — present-tense claims updated, historical ones left alone |
  | 8 | 3/4 "do not report" vs reported | §3.5 superseded the prohibition; the superseded sentence now says so |
  | 9 | the five §4 claims | verified in their named homes |
  | 10 | speed attribution | corrected text present; original left readable above it, which is correct |
- **What I did:** Fixed all ten. Where a claim was superseded rather than wrong, the superseded text is struck and kept rather than deleted — the reason it changed is worth more than the tidiness.

  **The finding is about the shape, not the ten.** Every one is a **superseded claim that did not announce itself**, and in three cases the superseding text is in the *same file*, sixty lines away. §4's pass corrected five claims because someone handed it a list of five. It did not look for others, and the two most serious were not on the list.

  > **A correction pass bounded by a list inherits the list's coverage. Being handed the errors is the same epistemic position as being handed the tests** — §0.13's tests face, wearing a review for a coat.

  **The cheap mechanical remedy, adopted:** when a claim is corrected, **grep the repository for its other statements before closing the correction.** It costs one command. It would have caught 6 of these 10, including (a).

  **And the reason prose is worse than code here.** A stale constant fails a test; a stale sentence renders identically to a true one. The documents were held to a lower standard than the code, exactly as `peer-review-response.md:276` says — and this pass is evidence that saying so did not by itself fix it.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-138 · B-453 · Identifier containment, and the naming convention the check had to read rather than assume

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Shipped `check_identifier_containment`, wired into `ground_report` so the emit path cannot miss it. It refuses a report naming a device, interface, address or prefix that appears nowhere in the descent.

  **The permitted set is the descent's own vocabulary** — its devices, its subject, every rung's device/subject/reason, and every evidence key — not the inventory and not the evidence store. `prompt_library` hands the model a `DescentResult` and nothing else, so every identifier the model can legitimately use is in that object. One that is not was invented between reading the descent and writing the prose. That framing also means the check needs no new input and holds no device text.
- **Evidence:** 10 new tests; **1786 green**; `ruff` clean; the offline demo still emits its report. Reviewer A's `PE7` and `10.255.0.99` are both refused; round 3's real report still passes, which is the zero-false-positive measurement the item was filed on.
- **What I did:** Two design decisions are worth recording, because both were places the obvious implementation would have been wrong.

  **1. The device-name pattern is derived from the fabric, not written down.** `PE7` must be refused and `Established` must not, and no fixed regex tells those apart across fabrics — one tuned to `PE|P|RR` is a rule generalised from one instance, which is §0.13's rules face. So `_device_name_families` reads the convention off the names that exist: `RR1` and `PE2` yield `^(?:RR|PE)\d{1,3}$`, which `PE7` matches and no English word does.

  **And a fabric whose devices are named arbitrarily gets no device checking at all.** `core-router-alpha` produces no family, so `edge-router-beta` passes. That is the correct failure mode and it is pinned by a test: **silence is better than a pattern guessed from one example**, and an invented *address* is still refused there, because that kind is unambiguous everywhere.

  **2. The bug found in review was a possessive.** `PE7's` is how a report actually writes it, and the first implementation stripped only `.,;:` — so `PE7's` failed to match the device family and **the counterexample the check exists for went undetected**, while `RR1's` went uncounted. One `clean_token` helper, shared by extraction and candidate scanning, fixed both directions at once.

  Worth noting *how* it surfaced: the companion test — the one asserting a legitimate report passes — failed on `identifiers_checked >= 4`. **The refusal tests were all green.** A check that silently examines nothing passes every negative test it has, and only a count assertion distinguishes "found nothing wrong" from "looked at nothing" (§0.12). The count was in `GroundingResult` because that pattern was already established here; it earned its keep on its first use.

  **What this does not do, stated so it is not over-read.** It catches an **invented entity, not a wrong relation between real ones** (`peer-review-response.md` §3.3). A report swapping two real device names passes and always will. It raises the floor while B-439 is designed; it is not B-439.
- **Needs human review:** no
- **Blocks:** nothing. B-439 is unaffected — this is the intermediate check, not a substitute.

---

## OBS-139 · B-435 · The anomaly report had been wrong for the life of the project, and its tests agreed with it

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `topology.py` compared LLDP device IDs against inventory labels. At `t0`/`t1` three devices ran configured hostnames that differ from their labels — P1 was `LEAF05_DHCP_SERVER`, P3 `Lab-leaf01`, PE4 `SDWAN-Edge01` — so the comparison read a naming difference as a wiring fact.

  The report therefore claimed **one LLDP disagreement and three foreign neighbours**, on a fabric that had neither. OBS-103 established this in prose on 2026-08-16; the code was never changed.

  **After the fix both classes are empty:**

  ```
  LLDP disagreements (0):            none
  LLDP neighbors not in inventory (0): none
  Devices with zero adjacencies (2):  PE2, PE4
  ```

  Four things were being reported as anomalies. Three were the tool misreading its own fabric. The one that survives — PE2 and PE4 at zero IS-IS — is the one that always mattered.
- **Evidence:** `topology.hostname_map` over the nine `t0` fixtures resolves `leaf05_dhcp_server → P1`, `lab-leaf01 → P3`, `sdwan-edge01 → PE4`. 1790 green, lint clean.
- **What I did:** `configured_hostname` reads `facts.parsed.meta.hostname` — **already collected, so the fix costs no command**, which is why the item was cheap all along. `hostname_map` maps both spellings to the inventory name; `resolve_device` is called before either comparison.

  **Three things worth recording beyond the fix.**

  **1. The four failing tests were §0.13's tests face, verbatim.** `test_lldp_disagreement_between_p1_and_p2_is_detected` asserted the defect, and its docstring defended it: *"This is a verified fact about the fixture data, not a bug to paper over."* It was written from the same premise as the code, so it agreed with the code and made the defect look confirmed. Every one of them passed for a year.

  **2. The fix would have silently deleted coverage that is still correct.** With both classes empty on the real fixtures, the only positive cases for *genuine* disagreements and *genuine* unknown neighbours were gone — the finders could have returned `[]` unconditionally and passed. That is the shape `SESSION-HANDOVER.md` names as shape 8. The positives are now **synthetic**, including one that matters: a device correctly resolved by hostname whose link *still* disagrees, which proves resolution does not paper over a real inconsistency.

  **3. The spec was already in the repository, written by the operator.** `inventory/lab.yaml` carries an `lldp` note dated 2026-08-16: *"Compare LLDP device IDs against each device's configured hostname, not against its inventory label."* The fix is that sentence. It sat in a `notes:` block in a data file for a day, correct and unexecuted — **a structured note beside the data is a good place to record a finding and a bad place to track work**, and the backlog item existed the whole time without being scheduled.

  **The count-only rule survives with a better reason.** It was justified by "link topology cannot be stated truthfully here", which is no longer true. The reason now: a device-reported name and an inventory label can legitimately differ — they did here for months — so a link claim has to pick a spelling and defend it, while a per-device count is true under either. **Counts survive a rename; links do not.**
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-140 · Gate Zero · Examining 33 items found three stale dependencies and no new work

- **Kind:** audit
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Read all 33 `unverified` backlog items and gave each a real state. `unverified` is now **0**, reached by reading rather than relabelling. Final counts across 95 items: 36 `DONE` · 25 `DEFERRED` · 18 `OPEN` · 15 `BLOCKED` · 1 `CLOSED-AS-MEASURED`.
- **Evidence:** `BACKLOG.md` reconciliation table; `TRACKER.md` for T-004/T-015/T-035; Q-004 in the Open Questions table.
- **What I did:** **The examination found three stale dependencies, and that is its entire yield.**

  - **B-110** (`l3vpn_service` flow) was recorded as waiting on the T-006 subject-naming finding. **Q-004 accepted `<pe>:<vrf>` (OBS-035)** on the grounds that RD is reused across PEs. The blocker was resolved and the item was never updated.
  - **B-202** (mnemonic → flow lookup) waited on T-004 and T-015. **Both `DONE`.** Unblocked, merely unscheduled.
  - **B-209** (report relay hardening) was `unverified` and is actually **`BLOCKED`** — T-035 is `TODO`, gated on Q-007's residency decision, so there is no relay to harden.

  Two of the three had been startable for some time while reading as unexamined. **A dependency is a claim, and claims go stale**; nothing in the backlog re-checked whether a stated blocker still blocked, so an item could be freed by work elsewhere and stay parked indefinitely. Cheap fix, and the same shape as OBS-137's: *when something is resolved, grep for what was waiting on it.*

  **What the pass did not find is worth recording too.** No item turned out to be obsolete, duplicated, or already built. The backlog's *content* was accurate; only its *bookkeeping* had drifted. That is a reassuring result and it bounds what this kind of audit is for — it corrects the index, not the substance.

  **And a caveat against over-reading the zero.** "Examined" here means: the item is real, its description still matches the repository, its stated dependencies are accurate, and it belongs to a named phase. It does **not** mean anyone has re-argued that the item is the right thing to build. **An item at `OPEN` is one nobody has argued against, not one anybody has argued for** — and a state column cannot carry that difference, which is exactly the limitation OBS-136 was about, one turn later and now stated in the document rather than discovered again.
  **And a fourth, found by the consistency check rather than by reading items.** The counts said 95; the backlog holds **96**. **B-464 had been filed into the reconciliation table in the *main* table's format** — item, title, description — so it carried no state, no dependency and no last-touched, and every count taken since silently excluded it.

  It is the same family as OBS-136 seen from the other side. There the *vocabulary* could not express an item; here the *row shape* could not. Both produce a table that is well-formed, readable, and wrong, and in both cases nothing could have flagged it: a row in the wrong format is not a parse error, it is just a row. **The check that found it was comparing two independent derivations of the same number** — items mentioned anywhere in the file against items carrying a state — which is the only kind of check that catches a well-formed absence.
- **Needs human review:** no
- **Blocks:** nothing. B-110 and B-202 are now visibly startable when their phases begin.

---

## OBS-141 · Rounds 8b and 6 · A sealed method that arithmetic showed could not deliver its own falsifier

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Wrote `round8b.py` and sealed round 6's prediction (`ROUND-6.md`), so both lab windows are ready to spend rather than prepare.

  **And amended `ROUND-8.md` §6.3, which I sealed earlier the same day.** It asked for *"sub-200 ms sampling of the socket field"*. Round 8 measured 1.563 s for three `show` commands, so **one command costs ~520 ms of round trip** and no loop tuning beats the wire. The sealed method was unreachable.
- **Evidence:** `mean_sample_seconds: 1.563` over three commands, `evidence-archive/round8/20260817-161045/verdict.json`.
- **What I did:** **Sub-200 ms was a proxy, not the requirement.** §2a.2 needs a good chance of landing in the `OpenSent` window at least once, which is a function of *cycles*, not resolution:

  > expected catches ≈ N_cycles × min(1, W_window ÷ T_sample)

  Round 8: 10 cycles, W ≈ 150 ms, T = 1.563 s → **0.96 expected**. It observed exactly one. Round 8b: one command per sample (T ≈ 0.52 s) and a 900 s dense window (~39 cycles) → **≈ 11 expected**, so **P(zero) ≈ 2×10⁻⁵**.

  **That is the whole point: it converts a zero from "the instrument did not see it" into "it is not there."** A zero at 1.5 s resolution was uninterpretable; a zero at ~11 expected catches refutes §2a.2 and closes B-463.

  **The prediction is untouched — only the method changed.** A method amended *before the run*, *stated in advance*, to make the claim **more** falsifiable is exactly what §6.1b permits. Round 5 lost a correct answer to a setup error found afterwards; the cost of finding one beforehand is a paragraph.

  **Four instrument changes, two of them verified against round 8's own strings.** The anchored positional socket regex parses all three shapes correctly including `armed for read, not armed for write` — the discriminating case **no fixture covers**. And `classify_reset` orders specific before generic, so `"due to BGP Notification sent: hold time expired"` classifies as `hold_expired` rather than firing the AS discriminator, which is OBS-134's second defect.

  **Two structural fixes worth more than the round.** The verdict now reports the **baseline** socket count as its first field and **aborts before pushing anything** if the socket does not read armed on an Established session — round 8 had that control and did not read it. And the output path defaults into the tracked repo, with the script copying itself in beside the samples: **the instrument is archived with its own output mechanically**, rather than by anyone remembering OBS-135.

  **Round 6's prediction is that the defect is already fixed**, which is a weaker thing to confirm than a discovery and is worth running anyway. B-456 narrowed rung 5's member set to the path for *aggregation* reasons; that it also closes reviewer B's trust-loss scenario is **an inference from its member-set rule, not an observation.**

  **The residual is sealed separately, and it is the honest half.** `_rung_subjects` falls back to all physical interfaces with `ALL_HEALTHY` when the member set is empty — correct, and it means the scenario stays reachable for any fault that removes the route as well as the session. Round 6 tests the protected half. **If it holds, the unprotected half is the more valuable round** and should be filed before B-440 closes.
- **Needs human review:** no
- **Blocks:** nothing — both rounds are operator-gated on a lab window.

---

## OBS-142 · MCP re-test · A selection experiment needs the fabric in the same state, not only the same questions

- **Kind:** surprise
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (scoring); `gemma-4-e4b` under test
- **What happened:** The operator ran `MCP-RETEST-PROTOCOL.md` against LM Studio. Scored in `MCP-EXPERIMENT.md` §10. **§9's registered question came back void, and the reason is a confound the seal did not think to guard.**

  §9's refutation clause names `check_lab_bgp_neighbors` by name. On **Q1, the registered question, the model called `check_lab_bgp_neighbors`.** By the letter, refuted.

  But two of the three "why" questions went **straight to `investigate_lab_session`**, and the negative control chose `assess_lab_device_health` correctly. That is not a model spreading across tools, and the Q1 trace says what actually happened: it named `investigate_lab_session` as the right tool, found it lacked the `subject` argument, and called `check_lab_bgp_neighbors` **to discover it**. In the original run the model had the same gap and *asked the user*. It now goes and finds it.

  **Then the trajectory ended, because the session was Established.** The original observation was made with PE2's session **down**. This run refuted the question's premise at step one, so step two of the model's stated plan never ran.
- **Evidence:** `MCP-EXPERIMENT.md` §10; the exported transcript, `gemma-4-e4b`, LM Studio 0.4.21.
- **What I did:** Scored §9 as **content survives on Q2/Q4, control passes on Q3, Q1 void**, and recorded the general form:

  > **A tool-selection experiment needs the fabric in the same state, not merely the same questions.** The question is the stimulus; the **first tool result** is what shapes everything after it. A question whose premise is false ends the trajectory at step one, and every later step is unobserved rather than changed.

  §9 wrote *"uninterpretable if the question set differs"*. It is equally uninterpretable if the **ground truth** differs, and that clause was not written. The seal controlled the input and left the environment free.

  **The fix costs no extra window: ask Q1 while round 6 or 8b's fault is applied.** Both put a real BGP fault on PE2, which is the state the original observation was made in. The selection experiment and the fault round want the same fabric, and asking a model a question does not perturb a descent sampled by a separate process. **Two experiments, one window** — and B-113's consolidation, held back to keep the 21-tool structure intact for this measurement, unblocks once that third arm lands.

  **Four guarantees held live for the first time**, all previously exercised only by tests: B-458 withheld 748 characters of device text and said so; B-459 refused the invented peer end to end through MCP; B-456 evaluated 2 of PE2's 3 physical ports because the third is off-path; B-428 returned `all_layers_healthy` with no cause named.

  **B-456's is partial pre-confirmation of round 6** — `ROUND-6.md` §2.2 predicts a shut `Gi0/0/0/2` will not be evaluated, and this shows it is already outside the member set while healthy. The mechanism is now observed rather than inferred; the two-fault case still needs the round.

  **And one thing the protocol claimed to test and did not.** Q4's refusal came from subject resolution, not from B-453 — the payload reads `"paraphrase": {"status": "not_attempted"}` and `"0 identifiers contained"`. No paraphrase was generated, so the grounding gate had nothing to grade. **B-453 is still unexercised on a live path**, and I wrote a protocol asserting otherwise. Same shape as §0.12: a test that cannot fail because the thing it tests never runs.
- **Needs human review:** no
- **Blocks:** nothing. Q1 needs re-asking during a fault window.

---

## OBS-143 · B-465 · A baseline learned from a broken fabric has a second failure mode, and it appears after the repair

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The MCP re-test's health check on PE1 reported `isis_adjacency_count_drift`: *"IS-IS adjacency count 2 differs from the recorded baseline 1."*

  The recorded `expected:` blocks were derived by `learn-topology` from the **broken** fabric: PE2 records `isis_adjacencies: 0`, PE4 records `0`, PE1 records `1`. This run observed **2 on PE2** and **2 on PE1**. The fabric has been repaired and the baselines never re-derived.
- **Evidence:** `inventory/lab.yaml` expected blocks against the transcript's Q2 rung 4 (*"2 IS-IS adjacency(ies), all Up"* on PE2) and Q3's drift finding on PE1.
- **What I did:** Filed **B-465**, and recorded the half that is new.

  **The known failure of a baseline learned from a broken fabric is that it suppresses the fault it learned from.** `health.py`'s `suspicious_baseline` meta rule exists for exactly that, and `CLAUDE.md` documents it: *"'matches the baseline' is never read as 'is healthy' for a device whose baseline was learned from a broken fabric."*

  > **The second failure mode: once the fabric is repaired, the same baseline emits false drift alarms — and `isis_adjacency_count_drift` reporting a repair is indistinguishable from reporting a regression.**

  Nobody wrote that down, and it is the one an operator meets. A suppressed fault is silent; a false drift alarm pages someone.

  **And `suspicious_baseline` cannot catch it.** It fires on `isis_adjacencies == 0`, so PE2 and PE4 are flagged and **PE1's `1` is not** — equally wrong, structurally invisible. The rule tests for a *value* where the defect is a *relationship*: the baseline disagrees with what a role invariant would require. That is the fix, and it is why B-465 is two pieces of work rather than a `learn-topology` re-run.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-144 · B-466 · The coherence bound runs at 77% on a fabric with nothing wrong with it

- **Kind:** risk
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The MCP re-test's `investigate RR1 -> 10.255.0.12` payload carries `"skew_seconds": 23.117, "bound_seconds": 30.0, "within_bound": true` on a **routine, uneventful, all-five-rungs-healthy** descent.
- **Evidence:** the transcript's Q2 payload, `coherence` block.
- **What I did:** Filed **B-466**. `evidence-epoch.md` §3 asked for this number and could not produce it:

  > *"A bound that only speaks when violated says nothing about how close we routinely run. If real epochs land at 25 s against a 30 s bound, that is a finding."*

  **23.1 of 30 is 77%.** Not a violation, and not comfortable. The bound was **derived** — the fastest relevant timer, IS-IS hold at 30 s — and never fitted to how long an epoch actually takes.

  **The consequence is a refusal mode nobody costed.** One slow device, one connection retry, or the ~8 s consecutive-login penalty (chaos-harness §6.1c) landing twice inside a single epoch pushes a healthy fabric over the bound to `temporally_incoherent` and **exit 2** — *no trustworthy answer* — **caused by the tool's own latency, on a fabric with nothing wrong with it.**

  That is a worse failure than it sounds, because it is the exact shape reviewer B's §3.3 warns about, inverted: not a confident wrong answer, but a refusal an operator cannot distinguish from a real one. And `nettools health` and `nettools diff` use a different exit-code scheme, so a script calling both sees `2` mean two things.

  **What B-466 must not do is widen the bound to make the number look better.** The bound is derived from a protocol timer and that derivation is sound; a bound relaxed to fit the tool's latency stops bounding anything. The work is to measure the skew distribution across several runs first, then either **defend** the 30 s or **reduce the epoch's duration** — and the epoch has already been reduced once, from 5 sessions to 2 (`evidence-epoch.md` §1), so there may be less headroom left than there was.
- **Needs human review:** no
- **Blocks:** nothing yet. It becomes blocking the first time a healthy fabric returns exit 2.

---

## OBS-145 · T-035 · Egress bounded by a function signature, and one deviation from the spec

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Shipped `notifier.py` and `nettools investigate --notify`. Telegram, per Q-007. 18 tests, 1808 green.
- **Evidence:** `tests/test_notifier.py`; `nettools investigate RR1 10.255.0.12 --from-fixtures --label broken --notify` exits 1 with a failing channel, unchanged from without the flag.
- **What I did:** **T-035's three recorded decisions, in the order the task asked for them.**

  **1. Residency** — Telegram, taken by the operator with the trade-off stated (Q-007). The mitigation is structural: one provider interface, so Mattermost is a swap. `mattermost` is deliberately **absent rather than stubbed** — the task says implement one real provider, and a stub is an untested path wearing a name.

  **2. Identity** — not solved, and not pretended. `TELEGRAM_CHAT_ID` is a **delivery allowlist, not authorization**: it controls where output goes and grants nothing, because there is no inbound path to grant it on. Said in the module docstring, the class docstring, and `.env.example`, because this is the one that will be misread if an inbound path is ever added.

  **3. Egress** — the operator still owes Q-008 (which host runs `nettools`, and does it have outbound). Recorded as outstanding rather than assumed.

  **The guardrail is the shape of the call, and I wrote it first as the task required.** `notify()` takes `report, device, subject, finding` and **there is no parameter an evidence bundle could arrive in.** The test asserts the signature by introspection, so adding an `evidence=` parameter fails the suite — the addition is the defect, not the test.

  A second test covers the stowaway case: a bundle stuffed *inside* the report dict still does not render, because `render_report_text` reads named fields rather than iterating. Two different failures, two different tests; the first would not have caught the second.

  **One deviation from the spec, and it is an addition.** T-035 specifies `TELEGRAM_CHAT_ID`, singular. This accepts a **comma-separated allowlist** under the same name, because the operator asked to be "allowed" rather than to be the destination, and because *empty means send to nobody* only means something if the field is a list. The name is unchanged, so the spec's env surface is intact.

  **Three failures found by writing the tests, all mine.**

  - `main()` takes no argv; the CLI tests drive it through `sys.argv`. I wrote three tests calling `cli.main([...])`.
  - `_note` writes to **stderr**, so stdout stays parseable JSON. I asserted against stdout. **The code was right** — a delivery failure must not corrupt the machine-readable result either, and the test now checks both streams.
  - The token appears in Telegram's URL path, so `urllib` puts it in exception strings for free. `_redact` exists because of that, and the test asserts the token is absent from the whole returned record rather than only from the message.

  **What is not built, deliberately:** no inbound anything, no threading, no per-user identity, no formatting beyond a readable summary. All MVP-1 or later. If this module ever grows a way for a message to *cause* something, that is a HALT.
- **Needs human review:** no
- **Blocks:** nothing. It delivers once `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are in `.env`; until then `--notify` is a no-op that says so.

---

## OBS-146 · B-465 · My own backlog scoping was wrong, and writing the fix is what showed it

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** I filed B-465 (OBS-143) with two pieces of work, and wrote the second as: *"give `suspicious_baseline` a way to flag any baseline that disagrees with a role invariant, not only a zero."*

  **That would not have caught the case that motivated the item.** PE1's recorded baseline is `isis_adjacencies: 1`. The role invariant is `_isis_isolated`, which requires **at least one**. So `1` does not disagree with any role invariant, and a widened `suspicious_baseline` would have passed it exactly as the narrow one does.
- **Evidence:** `checks._isis_isolated` fires only on zero records; `inventory/lab.yaml` records PE1 at `1`; the MCP re-test observed `2`.
- **What I did:** **The defect is direction, not the invariant.** `_isis_adjacency_count_drift` fired at `warning` on *any* difference, so once the fabric was repaired it reported the repair — and a warning about a restored adjacency is indistinguishable from one about a lost adjacency, which is the only case the rule was built for.

  An increase is now `info`, says the baseline is likely stale, and names `learn-topology`. A decrease is unchanged at `warning`. **It is not silenced**: a genuinely new adjacency is worth a line, and suppressing it would trade one blind spot for another.

  This needed one small mechanism: a finding fragment may now carry `severity` and override its rule's. Documented as the exception rather than the pattern — *a rule reaching for it routinely is two rules* — and used here because losing and gaining an adjacency are the same rule and not the same news. Splitting into `..._up`/`..._down` would break anyone filtering on the existing name for a distinction better carried in the finding.

  **What the mis-scoping came from, because that is the transferable part.** I generalised from the two devices I had looked at. PE2 and PE4 record `0`, and `0` *is* a role-invariant violation, so "the baseline violates a role invariant" fitted both instances perfectly and read as the general form. **PE1 was in the same finding, one line down, and did not fit** — I had the counterexample in hand when I wrote the rule and did not check the rule against it.

  That is §0.13's **rules face** exactly: a generalisation drawn from instances that happened to share a property the general case does not have. The build's standing detector for it is *"each caught by the next instance arriving"* — and here the next instance had already arrived. **Writing the fix is what checked the rule against the data; filing it did not.**

  Worth keeping as a working rule: **a backlog item that names its own fix has had the fix designed at the moment of least information.** Filing should name the *defect*; the fix belongs to whoever has the code open.
- **Needs human review:** no
- **Blocks:** nothing. B-465's remaining half — re-run `learn-topology` against the current fabric — needs the lab.

---

## OBS-147 · Method · A verification that finds nothing must be asked what it found wrong with itself

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `BACKLOG-STATUS.md` checked 36 DONE claims and 12 guardrails. **Nothing changed state and no guard proved vacuous.** A clean sweep is indistinguishable, in its output, from a pass that was skipped — both produce a document full of ticks.

  What made the pass readable was not the ticks. It was that **every defect it found was in the verifier rather than in the thing verified**: two wrong test files and a cache-invalidation gap in the mutation harness, and five stale dependency rows found by resolving what the reconciliation asserted.
- **Evidence:** `BACKLOG-STATUS.md` §5.2, §5.3, §6, §7.
- **What I did:** Promoted the framing out of that document, because it generalises past this build.

  > **A verification pass that reports no findings has two possible explanations, and its
  > own output cannot distinguish them: the thing is sound, or the check did not look.**
  >
  > **The discriminator is what the pass found wrong with itself.** A real pass exercises
  > its own instruments and finds them imperfect, because instruments are. A pass that
  > reports nothing wrong anywhere — including in its own method — has produced a result
  > that cannot be read.
  >
  > So: **state what was checked, state what the check cannot see, and state what the pass
  > found wrong with its own apparatus.** If that third answer is "nothing", say so
  > explicitly, and treat it as a reason for suspicion rather than confidence.

  **This is §0.12 turned on the auditor.** §0.12 says a guardrail that can pass by measuring nothing needs its empty case made visible. The same is true of an audit, and the audit's empty case is *"I found nothing, including in myself."*

  **Why the self-check is the right discriminator rather than, say, coverage.** Coverage can be inflated deliberately. Finding your own instrument wrong cannot — nobody breaks their own tool on purpose to look thorough, and the specific defects are checkable afterwards. It is a costly signal, which is what makes it worth reading.

  **The honest reading of two clean passes**, recorded in `BACKLOG-STATUS.md` §7 and worth repeating: not *"everything is correct"* but **"the cheap checks are exhausted"**. The next assurance costs a lab window or an outside reader. An audit that keeps returning clean is telling you to change instrument, not to relax.

  Added as a pointer from `BUILD-PLAN.md` §0.12, which is where the vacuity rules live.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-148 · Harness · A verifier whose errors produce plausible alarms is worse than one whose errors produce noise

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The first mutation harness reported **B-458 and B-456 as `GUARD VACUOUS`**. Both were false. It had chosen each guard's test file by **name similarity** — `test_mcp_server.py` for a guard in `mcp_server/server.py`, `test_descent.py` for one in `descent.py`. The guards live in `test_mcp_boundary.py` and `test_flows.py`. Run against the right files, both fail immediately.
- **Evidence:** `BACKLOG-STATUS.md` §5.2. `test_registration_is_what_applies_the_boundary` and `test_each_member_set_carries_its_own_aggregation` both fail under their mutations.
- **What I did:** Rewrote the harness as `scripts/mutate_guards.py`. **A test target is never guessed**: `resolve_guard_tests` greps `tests/` for the *guard's own symbol*. When it finds nothing it returns `UNRESOLVED` and exits non-zero, **which must never render the same as `VACUOUS`** — those are different answers and conflating them is the defect.

  **Why this failure mode is worse than an ordinary false negative, which is the finding.**

  > **A verifier's errors should look like noise. When they look like findings, they are
  > adopted.**
  >
  > *"This guard is vacuous"* is precisely the output this tool exists to produce. Its
  > false negatives are therefore **indistinguishable from its true positives** — same
  > word, same column, same weight. And they point at the **more alarming** conclusion,
  > which is the direction a tired reader accepts without re-deriving.

  **B-458 and B-456 would have been reopened on a false report**, and the work would have been to re-test guards that already held. The cost is not just wasted effort: an item reopened on a false alarm arrives with evidence attached, and the evidence is wrong.

  **The second harness defect, which matters more than the first.** It left stale `__pycache__`. After a pass, `make test` reported a failure that existed in no source file — loud, and self-correcting. **The other direction is not.** Source restored, bytecode still mutated, a later run executing code that exists nowhere and reporting green: **a mutation recorded as caught when it was not.** That is a vacuous guard certified as sound by the tool built to find vacuous guards.

  Every mutation is now bracketed by a cache purge, and `assert_no_stale_bytecode` refuses to continue if a `.pyc` survives. Restores are asserted byte-for-byte, and the run ends by re-checking the four frozen files against `6629a2c` and requiring a clean `git status`.

  **Three defects in one harness, all found by using it and none in the thing it verifies.** That is OBS-147's discriminator, and this is the finding it points at.
- **Needs human review:** no
- **Blocks:** nothing. 12/12 guards hold under the corrected harness.

---

## OBS-149 · Gate Zero · Two mechanisms found stale dependencies and neither would have found the other's

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** **Gate Zero (OBS-140) found three stale dependencies by reading items**: B-110's naming blocker had been resolved by Q-004, B-202's T-004/T-015 were both `DONE`, and B-209 was `BLOCKED` rather than unexamined.

  **`BACKLOG-STATUS.md` found five more by resolving them** — comparing each row's claimed dependency state against the state that dependency actually holds:

  | Item | Row claims | Actually |
  |---|---|---|
  | B-416, B-418, B-419 | B-414 (BLOCKED) | **CLOSED-AS-MEASURED** |
  | B-426 | B-201 (unverified) | **DEFERRED** |
  | B-459 | B-453 (OPEN) | **DONE** |

  **Neither pass would have found the other's.** Reading an item catches a blocker that was discharged elsewhere and never propagated back. Resolving a reference catches a *recorded state* that has drifted from the record it names. The first is about the world; the second is about the table.
- **Evidence:** OBS-140 for the three; `BACKLOG-STATUS.md` §6 for the five. B-459's row says it waits on B-453, which shipped the same day.
- **What I did:** Recorded the consequence, which is procedural rather than a fix.

  > **A dependency is a claim about another item, written once and re-read never.** It goes
  > stale silently, in a cell that stays perfectly well-formed. **So dependency resolution
  > has to run on every verification pass, not once at reconciliation** — a one-time
  > reconciliation fixes the table as it stood that morning and starts decaying that
  > afternoon.

  `BACKLOG-STATUS.md` resolves every dependency at generation time rather than printing the string in the row, so **this class cannot recur in that document** — only in `BACKLOG.md` itself, which is where the five still are until someone edits them.

  **The general shape, and it is the third instance in two days.** OBS-136: a vocabulary that could not express what eight items said. OBS-140: dependencies that had been discharged elsewhere. This: dependencies whose recorded state had drifted. **All three are the index disagreeing with the content, and none of them is visible from inside the index** — every row was well-formed every time. The only thing that finds them is deriving the same fact twice by different routes and comparing.
- **Needs human review:** no
- **Blocks:** nothing. The five rows are cosmetic today; B-459's is the one that would mislead.

---

## OBS-150 · Process · `git add -A` swept a 745-line file I had not read into a commit about something else

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `docs/EXPERT-PEER-REVIEW-2026-08-17.md` appeared in the working tree at 19:37, written by the operator or a review they commissioned. I noticed it, identified it, and then ran `git add -A` for an unrelated commit. **It went in — 745 lines, in a commit whose message describes OBS-147/148/149 and does not mention it.** Pushed before I checked.
- **Evidence:** `git show --stat 294bf9e` lists it first, at `745 ++++`.
- **What I did:** Recorded it rather than rewriting history — the file almost certainly belongs in the repository, and a force-push to fix a commit message is worse than the defect. **The operator decides whether it stays; I should not have been the one to decide it by accident.**

  **Two things wrong, and the second is the one worth keeping.**

  The narrow one: I committed a document I had not read. This session's own rule for the Artifact path is that you never distribute what you have not seen, and a push is distribution.

  The general one: **`git add -A` is the mirror image of a blanket `.gitignore`, and it fails the same way.** OBS-131's detector note says *"anything whose contribution is a non-action cannot report a mistaken non-action"* — a `.gitignore` cannot warn when it swallows the wrong file. **A blanket `git add` cannot warn when it includes one.** Both are wildcards whose effect is invisible at the point of use, and both produce a commit that looks exactly like a correct one.

  > **`archive.sh` exists because of precisely this class**, and it verifies its end state with `git ls-files` before reporting success. I wrote that yesterday and then staged by hand with `git add -A` today, in the same repository, four commits later. **Knowing the rule does not protect against it** — which is OBS-114's finding, arriving against me for the second time.

  **The cheap remedy, adopted:** `git add` the paths a commit is about, and read `git status --porcelain` *before* staging rather than after. A commit whose `--stat` contains a file its message does not mention is a defect regardless of whether the file belongs there.
- **Needs human review:** **yes** — whether `docs/EXPERT-PEER-REVIEW-2026-08-17.md` should be tracked, and whether it wants its own commit.
- **Blocks:** nothing.

---

## OBS-151 · Deep review · The expert review verified finding-by-finding, and the sharpest defect was one it missed

- **Kind:** audit
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator commissioned a third-eye review (`docs/EXPERT-PEER-REVIEW-2026-08-17.md`) and asked for a deep line-by-line pass of my own. Written up in `docs/DEEP-REVIEW-2026-08-17.md`: every load-bearing claim in theirs re-derived against source (twelve confirmed, several sharpened, none materially wrong), plus what this pass found that theirs did not.
- **Evidence:** fourteen live probes against the committed fixtures, each quoted with output in the review document.
- **What I did:** Filed **B-467/B-468/B-469**; fixed B-468 and B-469 plus two §2.6 defects the same day; B-467 is the substantial one and stays open.

  **The finding that matters (B-467): parsing bounds *structure*, not *content*.** The invariant — no *unparsed* device text reaches a model — is honoured everywhere it applies and does not bound prompt injection: `sanitize()` leaves `parsed` intact, and `get_lab_logging`'s parsed records carry **17,916 measured characters** of verbatim device-authored prose in one sanitised return. The trusted path is included: `build_correlate_prompt` embeds all 28 shaped records' text verbatim. Syslog is attacker-writable from the network. What contains it today is **output-side** — grounded timelines, B-453, non-authoritative labelling — so an injected line cannot fabricate a *cited* finding, but the input side has no content typing at all. The expert review's P2-06 said a future parser *could* introduce a raw field; the measured reality is today's parser already ships the prose.

  **B-468, both halves measured then fixed.** `nettools ping` exited 0 on 100% packet loss — `loss_pct` was parsed, carried, and never read by the exit computation, which is **shape 7 in the CLI's own logic**. And `unsupported` exited 1 from the per-device commands against a contract the code states three times. Partial loss exits 0 deliberately and the docstring says why.

  **B-469, fixed.** `except (ValueError, Exception)` around `origin_prefix_for` would have converted any future programming error into a silent switch of rung 5's semantics to the ALL_HEALTHY fallback — the exact residual `ROUND-6.md` §2.3 seals as the remaining trust-loss exposure — fabric-wide, with every collector-injecting test green. Narrowed to `ValueError`; the degradation now travels in the payload as `origin_unresolved`. **The comment on that catch also claimed the rung goes `unevaluated`, and the code it sat on falls back to all-interfaces** — a doc-versus-code lie inside one function.

  **Also fixed:** dead code in `run_templates` (an unreachable second `sender` branch), and the audit log recording the configured retry maximum instead of retries consumed — an auth failure was logged as having retried once when the auth check's whole point is that it never retries. `_with_retries` gained an `on_failure` hook so the count is what actually hit the wire.

  **Scoring the third-eye review:** P0-01/02/03, P1-01/03/07/08/09, P2-01/02/06 all confirmed at source, several sharpened (the SQLite golden failure direction is *loss*, not duplication; two read paths crash on the truncated files the write path can produce). Nothing materially wrong found in it. Its Tier 1–4 roadmap maps almost item-for-item onto the existing backlog — **both reviews and the backlog now agree on the same list, so the next level is sequencing, not ideas.**
- **Needs human review:** no
- **Blocks:** nothing. B-467 is the first row of the reconciled priority order and should ride with the model-egress projector.

---

## OBS-152 · Cleanup · The two doc backups were byte-identical to blobs git already holds

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The expert review's cleanup phase (§7 Phase 0, item 4) said to compare the two `.docs-backup-*` directories with tracked docs and *"archive outside the repo or delete only after differences are understood."* Understood, by hashing rather than reading:

  - `.docs-backup-20260815-162657/CLAUDE.md` → blob `44a4b9f1548c` — **already in history** at `6629a2c` (2026-08-15) and `85191f8` (2026-07-29).
  - `.docs-backup-20260815-174903/docs/build/BUILD-PLAN.md` → blob matching `5360adc` / `6a56734` (both 2026-08-15).

  Both directories were hand-made safety copies of files git was already versioning, taken the day of the peer-review edits. **Deleting them loses zero bytes of information**, and the blob IDs above are the receipt.
- **Evidence:** `git log --all --find-object=<blob>` output quoted above; re-runnable.
- **What I did:** Deleted both. Also removed the main tree's `build/`, `agent_nettools.egg-info/`, `.pytest_cache/`, `.ruff_cache/` and `__pycache__` directories (all gitignored, all regenerable). **Left alone:** `preflight-*.log` (the operator's run record), `evidence/PE1/` (a runtime snapshot cache, gitignored by design), `.venv` (rebuilt last, after all Wave-1 merges, because every merge gate runs the suite through it), and the agents' worktrees under `.claude/`.

  Worth one line: the safe way to "understand differences" between a backup and a repo is `git hash-object` + `--find-object`, not reading diffs — content-addressing answers "does git already have this exact file" in one command, with no judgement involved.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-153 · Merge gate · The mutation harness found a vacuous guard in freshly merged, freshly reviewed work

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (gate); sonnet-5 (the wave under test)
- **What happened:** Running the merge-gate mutations over Wave 1's guards, **B-474's came back VACUOUS**: reverting `save_snapshot` from `_atomic_write_text` back to a bare `write_text` passed **all 35** of the wave's new persistence tests.

  The tests were good tests — they proved `_atomic_write_text` behaves correctly under a failed replace, in both the exists and never-existed cases. **None of them proved the save paths *call* it.** The helper was pinned; the wiring was not. §0.13's tests face, in work that had just passed a full line-by-line review at merge.
- **Evidence:** `scripts/mutate_guards.py` run log — `B-474 ... VACUOUS ... 35 passed`. After the wiring test: `1 failed, 35 passed` under the same mutation.
- **What I did:** Added `test_save_paths_actually_route_through_the_atomic_writer` — a recorder monkeypatched over the helper, asserting both save paths and `metrics._persist` route through it. The same run also surfaced two harness-entry defects of my own (a wrong anchor for B-473's title — a dict entry, not a kwarg — and a resolve symbol no test mentioned for B-474b), each reported as `ANCHOR-MISSING`/`UNRESOLVED` rather than `VACUOUS`, which is OBS-148's three-verdict distinction doing exactly what it was built for.

  **Why this one is worth a finding when eighteen guards now hold.** The wave's tests were written by the same agent that wrote the fix, from the same premise — *"the atomic writer must behave"* — and both the code and the tests satisfied it perfectly while the actual guarantee (saves are atomic) was one silent revert away from false. **Review read the tests and found them good; only removing the guard showed they guarded the wrong layer.** The merge-gate order matters: mutation runs *after* review precisely because it catches what reading cannot.

  18/18 guards hold under the corrected harness.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-154 · FIX-PLAN · Six agents, six merges, and what the orchestration layer itself learned

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (gates and merges); sonnet-5 × 6 (implementation)
- **What happened:** Both reviews' actionable findings implemented in two waves per `FIX-PLAN.md`. All six agents merged; suite went **1820 → 1916**, 96 net new tests; `ruff` clean; frozen files byte-identical throughout; **.venv rebuilt last** with normal and fresh-bytecode runs in parity (1916/1916), which closes the expert review's P0-01 acceptance criterion in full.

  Closed: B-467, B-470, B-471, B-472, B-473, B-474, B-475 (scoped), B-476 — plus SECURITY.md and the count corrections.
- **Evidence:** merge commits on `feat/investigation-layer`; each wave's report; `scripts/mutate_guards.py` — **18/18 guards hold**.
- **What I did:** Three observations about the *process*, which is what this entry is for.

  **1. Two agents corrected my specifications, and both were right.** Wave 1-A: my spec named `last_reset` (the field is `last_reset_reason`) and `interfaces` (the template context is `interface`) — under the literal spec the free-text protection would have silently covered nothing for those entries. Wave 1-D found `ToolAnnotations`' signature reports pydantic *aliases* (camelCase), so a snake_case capability probe returns false on a capable SDK. **The instruction that mattered in every brief was "say what met resistance — do not silently deviate."** An agent that builds faithfully on a wrong spec produces exactly the §0.13 failure: internally consistent, wrong, and green.

  **2. The merge gate caught what line-by-line review did not, twice.** OBS-153: wave 1-C's atomic-write tests proved the helper and not the wiring — VACUOUS under mutation, invisible to reading. And wave 2-B surfaced a genuine design tension (validation refusals classified into uselessness) by *reporting* it as resistance rather than resolving it silently; the fix — eleven refusal kinds added to both ERROR_KINDS copies — restores the reason the agent loop's own prompt depends on, while the offending value still never crosses. **Review reads intent; mutation tests wiring; agents report friction. Each catches a class the others cannot.**

  **3. Disjoint file sets did more for merge quality than isolation did.** Five worktrees produced zero merge conflicts because the waves were partitioned by file, not by topic — the one shared file (`cli.py`) was partitioned by *function* with an explicit "do not touch any existing command body" clause. The sequencing constraint that mattered was semantic, not textual: 2-B waited for 1-A's public contract, and its brief named the contract rather than the diff.
- **Needs human review:** no
- **Blocks:** nothing. What remains from both reviews is deliberately deferred and filed: the explorer claim-graph (P0-03's expensive half), RBAC (B-301), the module split (their P2-01), the cross-surface scheduler, and the packaging matrix (their P1-09) — the last is worth a small wave of its own before publication.

---

## OBS-155 · OPS wave · Four agents lost to a spend limit; the wave built solo to the same specs and gates

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (all four items, after the sonnet-5 agents terminated)
- **What happened:** All four OPS-wave agents hit the account's monthly spend limit mid-orientation — none had committed. The wave was rebuilt directly by the orchestrator, sequentially, to the same written specifications and through the same merge gates. Suite: **1916 → 1953**; closed **B-477, B-478, B-479, B-480, plus B-202 and B-210** from the deferred pile.
- **Evidence:** commits 076d83a (N-4), 5f829f0 (N-1), 0322888 (N-2), and the N-3 commit; each message carries its own measurements.
- **What I did:** Three things the build itself surfaced, worth keeping:

  **1. The audit's MTU rule failed its own synthetic test on the first run** — it joined LLDP's `GigabitEthernet0/0/0/0` against the interface table's `Gi0/0/0/0` by exact name and silently never fired. That is the *precise* two-spelling join `descent._path_members` documents, recurring in new code the same day its documentation existed. Fixed through `interface_kind.canonical`; the lesson is that a documented trap still catches whoever has not read that paragraph yet, and only a synthetic positive written before trusting the rule (§0.12) caught it.

  **2. Two rules are named as absent rather than faked.** BGP timer asymmetry and IS-IS metric asymmetry belong in the audit and cannot be built from the current parses — the summary carries no timers, the IS-IS records no metric. Writing a second parser inside an audit rule would have planted the duplication face; the module docstring names both gaps and what each needs.

  **3. The fixture labels validated the audit better than any synthetic could.** Measured before pinning: `healthy` audits clean, `broken` catches exactly PE2's configured-but-dead BGP, `t0` catches exactly the three OBS-103 renames. The fabric's own recorded history is the audit's acceptance suite.

  Also: the description-form guard (wave 1-D's registry test) refused my first knowledge-tool docstring for lacking a "prefer" clause — a guard built by one wave correcting the next author, which is what registry-driven tests are for.
- **Needs human review:** no
- **Blocks:** nothing. The spend limit is the operator's lever; future waves can return to delegation when it resets.

---

<!--
Copy this block for each new entry.

## OBS-066 · T-029 · `grounding.py` — the gate, and the half of it the spec could not see

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** T-029 as specified is three bullets of citation integrity: every observation's key is real, every `based_on` resolves, the recommendation carries `requires_human: true`. All three implemented, all three tested exactly as the plan asks. **They are not sufficient, and the report that defeats them is the likely one rather than an adversarial one.**

  ```json
  {"observations":    [{"claim": "PE2's Gi0/0/0/0 is administratively down",
                        "evidence_key": "PE2:interface:GigabitEthernet0/0/0/0"}],
   "interpretations": [{"claim": "The interface is the cause.", "based_on": ["obs-1"]}],
   "recommendation":  {"next_check": "confirm with the operator", "requires_human": true}}
  ```

  Every claim cites something real. Every citation resolves. **`check_grounding` passes it**, and it is precisely the output `prompts/README.md` rules out — the cause named and the four rungs that explain it dropped. Citation integrity cannot see it, because nothing in it is false. What is wrong with the report is what it *omits*, and omission is invisible to a check that only inspects what is present.

  So the operator's requirement — *grounding covers the causal chain* — is a second check with a different input, and the difference in input is the whole reason the first cannot absorb it: completeness is measured against the descent, and `check_grounding` never sees the descent.

  | Function | Question | Input |
  |---|---|---|
  | `check_grounding` | Is this report internally sound? | the report, a key set |
  | `check_chain_coverage` | Is this report the argument the descent made? | the report, the descent |
  | **`ground_report`** | **both** — the function the emit path calls | the report, the descent |

  The spec's signature is kept exactly, as a component. `ground_report` derives the key set from the descent rather than taking one, which closes a second gap: the authoritative set is what the checks **read**, never what exists in the evidence store. `PE2:interface:GigabitEthernet0/0/0/2` is a real key for a real interface this descent never looked at, and citing it is an uncited claim wearing a citation.
- **Evidence:** `src/agent_nettools/grounding.py`; `tests/test_grounding.py`, 34 tests. **1279 passed, lint clean.** Built against the real five-rung `broken` descent, not a constructed one — `test_the_broken_descent_has_a_chain_worth_checking` asserts 5 rungs, a 4-rung chain and keys on every rung before anything else runs.
- **What I did:** Four decisions worth recording.

  **1. The union of `based_on` covers the chain, not a single interpretation.** The operator's phrasing was "the lowest broken rung is the interpretation citing the observations above it". Implemented as the union across all interpretations, deliberately: splitting a five-link chain into two sentences is better prose and no weaker an argument. Dropping a link is what the rule forbids, and the union catches that exactly. Pinned in both directions — the split version passes, the version citing only the cause fails with four `chain_link_not_argued` failures.

  **2. Rule 1's exemption is narrower than "unevaluated rungs are exempt".** The exemption is keyed on *having no evidence keys*, not on the verdict. A rung that read something before giving up is still required to be cited; only a rung with nothing to cite is exempt. The wider version was the obvious phrasing and would have let a partially-read rung disappear from the report — the same shape as OBS-065, a rule encoded at the wrong width.

  **3. `GroundingFailure` has no field a claim can occupy.** "A failed grounding check means the report is not emitted" is worth nothing if the rejection reason quotes it. Structural containment rather than redaction, for OBS-061's reason: a rule enforced by remembering is a rule that eventually is not enforced. Evidence keys *are* included — an invented key is the thing that failed and naming it is what makes the failure actionable — but they are flattened and capped at 120 characters, since a key in a rejected report is model-authored text like any other.

  **4. §0.12 applied to this module's own result.** An all-`unevaluated` descent and an empty report satisfy every rule here by having nothing to check. That is a legitimate pass, and `ok` alone is indistinguishable from a real one — so `GroundingResult` carries the counts and `vacuous` names the case. Two tests, in both directions: the empty pass reports vacuous, the real pass reports not-vacuous. Without the second, `vacuous` could be hardcoded `True`.

  Also pinned: `observation_labels` against the prompt's own instruction that observations are numbered `obs-1, obs-2, …`. That label scheme is a contract between two files with no third place to write it down, and if they ever disagree the gate rejects every report a correctly-behaving model produces — a total outage presenting as a model quality problem.
- **Needs human review:** no
- **Blocks:** none. **T-030 acceptance depends on the runner calling `ground_report`, not `check_grounding`** — the latter passes every internally-consistent report.

---

## OBS-067 · merge · `evidence-reduction` revisions 1 and 2 — and what the log platform would actually have returned

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Revision 2 arrived restructured, with five capabilities revision 1 did not have (relationship-aware projection, episodes, coverage metadata, progressive disclosure, rarity baselines) and with §3.2/§3.4/§3.5/§4/§6 written to incorporate the six measured divergences. Merged into a single document; the v2 file deleted. All six divergences verified against the fixtures again rather than trusted from the summary, and **three of revision 2's claims were wrong**.

  **1. "All fabric SSH records name one of four management hosts."** 2,558 of 2,723 do. **165 name no address at all** and are kept as unattributable. That remainder is not a rounding error — it is the entire point of the rule, because those 165 are exactly the records a facility drop deletes and an attribution rule keeps. Stating "all" would have made the unattributable case look like an edge condition rather than the mechanism.

  **2. "Every causal event was severity 5 or 6, and none reach the platform."** True as stated, and it reads as "the platform would have returned nothing." **It would not.** Two records in the same window are severity 3 and would be delivered:

  ```
  Aug 15 23:07:29.168  sev3  PKT_INFRA-LINK-3-UPDOWN
  Aug 15 23:07:29.189  sev3  PKT_INFRA-LINK-3-UPDOWN
  ```

  Those are line-state changes from **the previous day's restore** — a different incident. So an investigator querying the platform for this device receives two real, correctly-timestamped link events belonging to the wrong event, and nothing at all from the isolation under investigation.

  **An empty result is honest and visibly incomplete. A partial result is neither.** A severity filter that removes the consequences of an event while retaining superficially similar events from elsewhere in the buffer does not degrade a timeline — it fabricates one. This is the strongest argument for coverage metadata in the document, and I would not have found it without re-deriving the claim.

  **3. §6's episode example used invented mnemonics** (`OPTICS_RX_LOW`, `BFD_SESSION_DOWN`). The fabric produced a real six-event episode, so the invented one was replaced — and replacing it earned something the invented version could not have: a correspondence table between the descent's five rungs and the episode's events in which **two rungs have no log event at all**. That gap is what makes an episode corroboration of a descent rather than a substitute for one, and a fabricated example with one event per layer would have implied the opposite.
- **Evidence:** `docs/design/evidence-reduction.md`, merged, 16 sections. Measurements re-derived: 3,600 records across 18 fixtures, dedupe removes zero; 2,723 SSH records, 165 unattributable; 8 severity-3 records recovered by the §3.2 correction; 0 of 28 retained under subject projection.
- **What I did:** Added one measured constraint revision 2 did not have. **The gap between the interface event and the BGP event is 154 seconds** — the hold timer expiring, not processing delay. A naive temporal-proximity threshold of a few seconds splits the episode in two and severs exactly the link it exists to preserve. So B-416's proximity bound must be derived from **protocol timers, not human intuitions about "at the same time"**: BGP's default hold timer is 180s, IS-IS's is 30s, and any window shorter than the slowest timer in the dependency chain systematically breaks the chains that matter. Recorded against B-416 as a design constraint rather than left to be rediscovered.

  Also widened one measurement before restating it: dedupe-removes-zero was measured on PE2 alone at T-028. Re-run across all 18 committed `show logging` fixtures — 3,600 records, zero removed. The claim in the document is now the wider one.
- **Needs human review:** no
- **Blocks:** none. T-029a next, then T-030.

---

## OBS-068 · T-029a · Absence claims must be backed by coverage — and neither golden case can make one

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Grounding enforced citation for claims of **presence** and nothing for claims of **absence**. So `"no correlating events in window"` passed with nothing behind it, on a source measured to drop severity 5 and 6 — which is to say on a source that cannot support the claim at all.

  Same asymmetry `check_chain_coverage` closes, arriving on a different axis: **a check that inspects only what is present cannot see what was omitted.** A peer check rather than a rule inside the existing one, because the input it needs — the coverage record — is not in the report.

  `coverage.py`'s `Coverage.gaps()` lists every reason a source cannot support a negative. **Built by code**, never asserted by a caller and never by a model: `coverage_from_logging` reads every field from the `show logging` header the device itself emitted. The parser was extended (additively) to capture the message counts the header already prints, so

  ```
  Buffer logging: level debugging, 593 messages logged
  ```

  against 200 records returned is a *statement* that 393 were not retrieved, not an inference from "we asked for 200 and got 200".
- **Evidence:** `src/agent_nettools/coverage.py`, `grounding.check_absence_coverage`, `prompts/correlate.v3.txt`, 16 new tests. **1297 passed, lint clean.**
- **What I did:** Four things.

  **1. The measured consequence is uncomfortable and correct: neither golden correlate case can assert a clean negative.** PE2's `healthy` buffer holds **555** messages; `show logging last 200` retrieved 200. Whatever is in the other 355 was not read, so "there were no correlating events" is stronger than the evidence allows. The supportable claim is "none in the available coverage" — an `unevaluated`, not a `no`.

  I considered relaxing the rule to `returned >= requested` being acceptable, and rejected it. **The remedy is to widen the window until the source reports itself exhausted, which is exactly the incentive the rule should create.** A rule that permits the claim the evidence does not support, because enforcing it is inconvenient, is not a rule.

  **2. Two failure kinds, deliberately distinct.** `unbacked_absence_claim` is a construction bug and the report is not emitted. `absence_claim_exceeds_coverage` is a real answer at the wrong strength, and **downgrading the finding is a legitimate response**. Grounding states what the evidence supports; what to do about a shortfall is the runner's call, and collapsing the two would have forced T-030 to discard a usable answer.

  **3. A trap I nearly walked into: read the *buffer* level, not the trap level.** `show logging` returns the buffer, at `debugging` (0–7). The trap level governs what is *shipped to the collector* and is `informational` (0–6). Reading the trap level would understate the local source by exactly the severity class B-206a is about — while looking entirely correct. Pinned by a test that also asserts the two levels differ on this fabric, so it stops passing vacuously if they ever coincide.

  **4. `correlate.v3`**, carrying a COVERAGE slot, constraint 7 (*never state a negative more strongly than the coverage supports*), and the refusal marker `"no correlating events in the available coverage"`.
- **Needs human review:** no
- **Blocks:** none. T-030 next, and it should treat `absence_claim_exceeds_coverage` as a downgrade rather than a rejection.

---

## OBS-069 · T-029a · The refusal-marker rule was too narrow for the *second* time, one level in

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `correlate.v3` changed the refusal wording, and `test_every_prompt_names_its_refusal_path` immediately failed — for `correlate.v1` and `correlate.v2`, which had not changed.

  The test reads `refusal_marker` from the prompt's case file. One marker per **prompt family**. But the refusal wording is a property of a **version**: v1 and v2 say "in window", v3 says "in the available coverage", and each is correct for its own era. Checking every version against the current wording fails the historical record *for being historical*.
- **Evidence:** `prompts/tests/cases/*.cases.json` now carry `refusal_markers`, keyed by filename; `tests/test_prompts.py` looks the prompt up by its own name.
- **What I did:** Fixed it, and recorded it because of what it says about OBS-065 rather than about prompts.

  **This is the third encoding of the same rule and the second time it was too narrow.** The first hardcoded `undetermined` — the value the one existing prompt used. The fix moved the value into data, which was right, and encoded it at the width of *a prompt* — because there was one version of each. The second failure is the same mistake at the next level in.

  So the generalisation in OBS-065 needs sharpening. "Encode the rule at the width of the rule" is correct and insufficient, because **the width is not observable from a corpus with one member at every level.** With one prompt you cannot see that the marker varies by prompt; with one version of each you cannot see that it varies by version. Each widening was invisible until the corpus grew.

  > **A rule generalised from one instance fits one instance — and the corpus tells you the width only up to the dimension in which it already varies. Ask which dimension is about to grow, not which one has.**

  The practical form is cheap: when moving a constant into data, key it by the **finest identity the thing has** — a filename, not a family name — rather than by the coarsest one that currently works. Keying `refusal_markers` by filename costs one dictionary level and would have survived both widenings.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-070 · T-030 · The runner — and the wiring mistake that would have looked like a clean result

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `investigation.py`, the MVP-0 runner: resolve scope → descend → correlate → report → ground → emit. 22 tests against both labels with a scripted analyst — no model, no network. The thing under test is the *wiring*, and a real model would make every assertion probabilistic while testing none of them better.

  **The decision that mattered most was where to read the log window from.** The obvious wiring reads it from `device` — the device the investigation was launched against. That is wrong, and wrong in the worst way: `RR1 → 10.255.0.12` finds its cause on **PE2**, and PE2's buffer is where the interface and IS-IS events are. Reading RR1's logs would correlate a PE2 interface event against a device that never saw it and return **"no correlating events" with perfect confidence** — a clean, plausible, wrong answer, and one that would have passed every test I would naturally have written.

  It is the same shape as Q-013's wrong-device reading, one layer up: the descent already learned that a rung must be evaluated on the device the *subject* lives on, and correlation inherits the same requirement from the *cause*. Pinned by a test that captures which device the window callable is asked for.
- **Evidence:** `src/agent_nettools/investigation.py`; `tests/test_investigation.py`, 22 tests. **1319 passed, lint clean.**
- **What I did:** Five decisions beyond the spec, and one test I would not have written a week ago.

  **1. `report` is `None` whenever grounding failed** — not populated with a flag beside it. "A failed grounding check means the report is not emitted" is worth nothing if a caller writing `result.report or "..."` prints the rejected prose anyway. Third application of OBS-061: make it structurally impossible rather than remembered. `withheld_because()` returns grounding failures, which carry loci and never claims.

  **2. A coverage shortfall downgrades, it does not discard.** `absence_claim_exceeds_coverage` means a real answer at the wrong strength — "no correlating events" over a buffer that returned 200 of 593. Discarding loses a usable result; emitting it as a negative overstates it. It is kept and labelled `coverage_limited`. Every *other* grounding failure withholds, and the scoping is tested in both directions so the downgrade cannot quietly become "accept everything".

  **3. A markdown fence is stripped; nothing else is.** The line is that stripping a fence cannot change what the JSON says, whereas anything reaching into the content is a regex second-guessing the model. Trailing prose after the JSON is withheld, not salvaged. The repair is *recorded* rather than silently applied, because a model that keeps ignoring "no markdown fences" is a prompt problem someone should see.

  **4. No analyst is a mode, not a degraded run.** A descent is a complete result — it is the half with no model in it — and both model outputs report `not_attempted` rather than being absent.

  **5. `inventory_resolver` raises on an unknown subject** rather than falling back to the local device, matching `_resolve_devices`'s refusal for the same reason: a wrong-device read looks exactly like a healthy one.

  **The test I would not have written a week ago** is `test_the_model_cannot_influence_the_diagnosis`: run the same investigation with and without an analyst and assert the descents are identical — finding, rung path, evidence keys, every outcome's reason. "There is no gate in MVP-0" is the claim the entire architecture rests on, and until now it was a property of how the code happened to be arranged. **Nothing else in the suite would catch a model call leaking into evidence collection or rung selection**, because such a leak would still produce a plausible descent. §0.13: the behavioural tests all agree with the premise, so the premise needs its own test.

  For the same reason `test_the_runner_grounds_through_ground_report` asserts the call *by name*. That is blunt and I would normally avoid it — but swapping `ground_report` for `check_grounding` would leave every behavioural test in this file passing while turning the chain requirement off, which is exactly the failure §0.13 names.
- **Needs human review:** no
- **Blocks:** none — T-031 (CLI wiring) next.

---

## OBS-071 · pattern · Wrong evidence read as right evidence — a sixth silent-failure shape, and not a variant of the other five

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Correcting revision 2's claim that "none of the causal events reach the platform" surfaced a failure shape this build had not named.

  The claim is true and it *reads* as "the platform would have returned nothing". It would not. Two records in the same window are severity 3 and would be delivered:

  ```
  Aug 15 23:07:29.168  sev3  PKT_INFRA-LINK-3-UPDOWN
  Aug 15 23:07:29.189  sev3  PKT_INFRA-LINK-3-UPDOWN
  ```

  Those are line-state changes from **the previous day's restore** — a different incident. An investigator querying the platform for this device and a generous window receives two real, correctly-timestamped link events belonging to the wrong event, and nothing at all from the isolation under investigation.

  **Everything before this was absence read as presence.** Shapes 1–5 are all the same underlying thing: something was not measured, and the gap is invisible, so a green result or a plausible conclusion fills the space. Shape 6 is the opposite polarity — the evidence is *present*, real, correctly parsed, correctly timestamped, and internally consistent. It is simply about a different event.

  That is why no consistency check can catch it. There is nothing inconsistent to find. Timestamps are ordered, severities are genuine, the mnemonic is right for a link event, and a correlation built from them would read as a competent timeline. An empty result announces its own incompleteness; a partial result does not.
- **Evidence:** OBS-067, and the measured Loki reachability of PE2's `broken` window: two severity-3 records deliverable, all six causal events (severity 5 and 6) not.
- **What I did:** Recorded as a distinct shape rather than folded into shape 1, and added to BUILD-PLAN §0.13's table with that distinction stated — a taxonomy whose sixth entry is a restatement of its first is worse than five entries.

  The practical consequence is that **only coverage metadata makes shape 6 visible**, which is a stronger argument for T-029a than the one I built it on. `unevaluated` and grounding both work by noticing that something is *missing*. Neither can notice that what arrived is about something else. The coverage record can, because it states what the source *could not have carried* — and severity 5 and 6 being absent from a source is a fact about the source, knowable without knowing what the answer should have been.

  The generalisation worth carrying:

  > **A source that filters what it delivers does not return less of the truth. It returns a different, complete-looking truth.**

- **Generalised 2026-08-16 (round 2, OBS-089).** That statement is about *one instance* of the shape and it reads as a claim about data pipelines. It is not. **Shape 6 is a property of inference from partial evidence; tools have nothing to do with it.** Four instances now, only one involving a filter: the Loki severity drop (here), a hand diagnosis reading a transit next-hop as a destination owner (OBS-089), a developer's own successful runs answering *"does this work in my environment"* when the question was *"does this work with nothing"* (OBS-072), and reading a device's trap level to describe what its buffer holds. **Two of the four are human inferences with no tool involved, and one occurred in a hand diagnosis written specifically to be an independent check on a tool.** The general form is in `BUILD-PLAN.md` §0.13: the evidence is real, correctly read, and **answers a different question than the one being asked of it.**
- **Needs human review:** no
- **Blocks:** none

---

## OBS-072 · T-031 · `--from-fixtures` silently required a device password, and the demo hid it

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `nettools investigate --from-fixtures` worked perfectly from my shell and failed with no credentials in the environment. The shell runs passed because `main()` calls `load_dotenv()`, and **this repository's `.env` holds real lab credentials** — so every manual check was silently supplied with the thing the command is supposed not to need.

  It failed the moment a test stubbed `load_dotenv` out. That test exists only because the operator's requirement was specific: *"runs with no lab and no API key"*, first command in the README. A looser requirement would have shipped this.

  Three call sites resolved credentials before checking for an injected `sender`: `_run_approved_commands`, `_run_rendered_command`, and `run_templates`. A `sender` short-circuits the transport entirely, and **every sender in this codebase reads only `device["name"]` and `device["platform"]`** — both credential-free inventory data. So a password was being demanded to read a committed text file.
- **Evidence:** `tests/test_cli_investigate.py::_no_environment` strips `DEVICE_USERNAME`/`DEVICE_PASSWORD`/`DEVICE_SSH_KEYFILE` and all three API keys. **1338 passed, lint clean.** `tests/test_safety.py` and `tests/test_template_security.py` byte-identical and passing (186 tests).
- **What I did:** Moved credential resolution behind the sender check at all three sites, and handed the sender a `{"name", "platform"}` record built from credential-free data.

  **This strengthens the §0.6 ordering invariant rather than bending it.** The rule is "the allowlist is checked before credentials load". Loading credentials strictly later, on strictly fewer paths, cannot violate it — and `test_refuses_unapproved_commands_before_loading_credentials`, which runs with an empty environment, is unchanged and still passes. I checked that first, because a change in this area that *needed* a frozen test edited would have been a HALT.

  The finding worth keeping is not the defect. It is **how it stayed invisible**: the verification environment was contaminated by the thing being verified. Every manual run of `--from-fixtures` "proved" it needed no credentials while being handed credentials. That is §0.13's family again — *the evidence could not have shown me the dependency, because the evidence was produced with the dependency satisfied.*

  > **A demo verified in the developer's own environment verifies the developer's environment.**

  Which is the argument for the test asserting it rather than a person checking it: the test can strip the environment, and a person running a command cannot easily un-know their own `.env`.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-073 · T-031 · The exit-code scheme, decided in the open

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Three orthogonal outcomes — did the descent find a fault, did grounding pass, was coverage complete — and three exit codes. The operator proposed a scheme and asked me to take it or argue it. **I take it, in full, and the reasoning is worth recording because one case is genuinely contestable.**

  | Code | Meaning |
  |---|---|
  | 0 | the descent completed and found no fault |
  | 1 | the descent completed and found a fault — a **network** problem |
  | 2 | no trustworthy answer was produced — an **answer** problem |

  The contestable case is a **grounding failure over a real fault**. The descent found `interface_line_down`; the network genuinely is broken; exit 1 is defensible and my first instinct was that exit 2 risks a real outage being read as a tool problem.

  That instinct is wrong, and the argument that settles it is not the one about "the caller got nothing":

  > **If a grounding failure exited 1, a systematic grounding regression would hide in the noise of routine faults forever.** Faults are normal. Exit 1 is normal. A model layer that had quietly stopped producing verifiable output would look exactly like a fabric with intermittent problems — indefinitely, and to everyone.

  That is the silent-degradation shape this build has spent its entire length eliminating, and it decides the case. The counter-concern also dissolves on inspection: exit 2 is *more* alarming than exit 1, not less, and the descent's finding stays in the payload either way, so nothing about the network is concealed.
- **Evidence:** `cli._cmd_investigate`; `tests/test_cli_investigate.py`, 19 tests. All three codes produced from three real fixture labels, and asserted to be *different* — the §0.12 companion, since each expectation is individually satisfiable by a constant.
- **What I did:** Three things beyond adopting it.

  **1. Documented the divergence from `nettools health`, which is a live trap.** `health --all` maps 2 to the worst *network* outcome; `diff` and now `investigate` map it to "the answer is not trustworthy". Within one CLI, exit 2 means opposite things. That predates this task, but a cron job calling both and assuming one scheme will read a critical fabric as a broken tool or the reverse. It is now in the subcommand's `--help` and in the README, and a test asserts the help text says so.

  **2. `coverage_limited` follows the descent, and the caveat is a field.** The operator's rule is right — the finding is deterministic and reached with no model, so only the *timeline* is qualified. But "carries the caveat in the report" is a promise unless every renderer keeps it, so the caveat is a payload field and the test is parametrised over all three formats. A caveat surviving only in `--format json` is one the person reading a summary never sees.

  **3. An `undetermined` walk names no cause.** `DescentResult.cause` is "the lowest broken rung", which for a stopped walk is only "the lowest broken rung reached before we stopped". Rendering that as a cause would present a stopping point as a conclusion. Suppressed in the payload and both renderers.
- **Needs human review:** no
- **Blocks:** none — T-032 next.

---

## OBS-074 · T-032 · A mock that reads its prompt, and a test of mine that checked the wrong object

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The full pipeline offline on both labels: same device, same subject, same flow, same code, opposite captured state — `all_layers_healthy` / exit 0 against `interface_line_down on PE2` / exit 1, both with grounded reports. **1348 passed.**

  Two decisions worth recording, and one mistake of my own.

  **1. The mock model reads its own prompt.** My first instinct was a canned report string. That is wrong for an end-to-end test in a specific way: a hardcoded report is correct *whatever the pipeline rendered*, so it cannot detect the pipeline handing the model the wrong descent. In a chain this long — collect, parse, check, descend, render, ground, emit — feeding the model the wrong payload is the most likely wiring bug and the least visible, because the output stays plausible. `ReadsItsPrompt` parses the descent out of the prompt and answers from it, so `test_the_model_was_handed_the_right_descent_on_each_label` fails if a `healthy` run is handed the `broken` descent.

  **2. "No network" is enforced, not assumed.** `netmiko` is replaced with a module that raises on contact. An offline test that merely *happens* not to reach a lab stops being offline the day someone adds a code path that does, and it fails only when the lab is down — which is the worst possible time to learn about it.
- **Evidence:** `tests/test_end_to_end_offline.py`, 10 tests. All four frozen files byte-identical throughout.
- **What I did:** **Found and fixed a defect in my own test, and it is worth recording because of what kind it was.**

  `test_no_unparsed_device_text_reaches_the_model` asserted invariant 4 against `analyst.saw_descents` — the *parsed JSON payloads* the mock had extracted. That object cannot contain raw command output by construction. The test was green, and it was checking nothing.

  That is §0.13's tests face in miniature, in a test written *by* the person who had just written §0.13: I asserted against the object I had conveniently stored rather than the object the invariant is about. The fix stores the verbatim prompts separately and asserts against those — and adds the line that makes it non-vacuous:

  ```python
  assert "Cisco IOS XR Software" in raw, "the corpus must contain what we forbid"
  ```

  Without that, a corpus that simply lacked the forbidden strings would pass identically.

  The version that ships also pins the *distinction* rather than just the prohibition: the correlate prompt **does** carry device log lines — parsed records re-serialised — while **not** carrying the raw `show logging` header. Invariant 4 is "no unparsed device text", not "no device text", and a test that could not tell those apart would eventually be satisfied by removing the evidence.
- **Needs human review:** no
- **Blocks:** none — T-033 (live lab run) next, and it is the first task since T-011 that touches real devices.

---

## OBS-075 · T-033 · B-412's shape for the third time — inside the function written to prevent it

- **Kind:** defect
- **Escalation:** HALT (device state; see the reading below)
- **Model:** opus-5 · **reported by the operator, 2026-08-16**
- **What happened:** *Operator's account, recorded as given.* The T-033 fault-injection harness reproduced B-412's exact shape for the third time.

  `restore()` was built around the rule B-412 exists to enforce — **verify by reading, never trust the write** — and on an exception it retried three times **without ever reading the device**. The restore had in fact succeeded on the first attempt, **252 ms after the fault**, confirmed independently by the device's own syslog and by `show isis adjacency`. The script raised a manual-intervention alarm on a healthy fabric.

  Two lessons, as the operator stated them:

  1. **The verification must run on EVERY path, including the error path.** A verification reachable only on the success path verifies nothing when it matters.
  2. **The exception was a `TypeError`** — a code defect, not a network condition — swallowed by a broad handler and rendered as a network failure. **Catching broadly around a device call converts your own bugs into false reports about the network.**

  The independent check that caught it was **a human watching the device console. Nothing inside the tool would have.**
- **Evidence:** Operator's report, with two independent confirmations named (device syslog, `show isis adjacency`). Cross-checked from this side — see below.
- **What I did:** Recorded it, amended §0.11, reopened B-412 — and then applied the finding's own rule to the finding itself.

  **A read of the fabric, because a report of success is not evidence of success either.** `show isis adjacency` across the five edge/RR devices, 2026-08-16:

  ```
  PE1: 2 adj    PE2: 2 adj    PE3: 0 adj    PE4: 2 adj    RR1: 2 adj
  ```

  **PE3 currently has no IS-IS adjacencies.** Every other device is at its expected two, and PE3 had two in the baseline I read before proposing the fault. That is consistent with two quite different situations — the T-033 fault deliberately (re-)applied and still in place, or a fault left unrestored — and **I did not read further to distinguish them**, because if it is the first, the additional reads are the diagnosis I am under protocol to keep closed until the operator's hand diagnosis is recorded.

  Reported to the operator rather than acted on. No write attempted: restoring would be a device write, and §0.11's HALT is not waived. It is also precisely the move §0.11 forbids — re-applying a change on the strength of a report, against a device whose state I have read exactly once.

  **Three additions of my own, beyond the two lessons.**

  **1. This is not §0.12's failure mode, and conflating them would produce the wrong fix.** §0.12 is a guardrail that *runs* and passes over an empty set. Here the guardrail was correct and would have passed — it was **never reached**. The remedy for §0.12 is a companion test that fails when the empty set ends; the remedy here is structural: the verification belongs in a `finally`, or after the try/except, never inside the success branch. A test asserting "restore verifies the device" passes against this defect, because on the happy path it does.

  **2. The repository has the same idiom, correctly, and one line of drift would break it.** `CLAUDE.md` documents broad `except Exception` with `# noqa: BLE001` as the established idiom at the SSH boundary, and that is right — a device call genuinely can fail in unbounded ways, and a structured error beats a traceback. **The idiom is safe only while it wraps the call and nothing else.** Widen it by one line to include the caller's own logic and every `TypeError`, `KeyError` and `AttributeError` in that logic becomes a reported network fault. That is the most expensive kind of wrong this project can produce, because it is indistinguishable from the thing the tool exists to detect.

  **3. The witness has to be outside the thing being verified.** "Nothing inside the tool would have caught it" is the whole finding compressed. A tool cannot be its own witness for whether it succeeded at a write: the same defective code path produces both the action and the report of the action. This is §0.13's **setup** face on the device axis — the bound is invisible from inside — and it is the argument for the harness reading state through a *different* path than the one it wrote through, which on this fabric means the device's own syslog rather than the return value of the commit.
- **Needs human review:** **yes** — PE3's IS-IS state, and whether it is intended
- **Blocks:** **T-033.** No device-touching run until PE3's state is confirmed intended, and B-412 is reopened as blocking any further harness use.

---

## OBS-076 · T-033 · Q-006 — the independent hand diagnosis, recorded before the agent run

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** — (human operator)
- **What happened:** Q-006 asks whether the descent's stopping rung matches what a network engineer would conclude by hand from the same fabric. The operator's protocol for answering it: the hand diagnosis is written **first**, recorded verbatim, and committed **before** the agent runs — so the ordering is a fact in git history rather than a claim in a document.

  Recorded here without comment or evaluation, exactly as given. Subject: `RR1 10.255.0.13`. Fault live. Ground truth sealed.

```
---- BEGIN INDEPENDENT DIAGNOSIS (recorded 14:08 UTC, before agent run) ----
Rung: igp_adjacency, on PE3.  Finding: igp_isolated.

Evidence:
  - RR1 BGP to 10.255.0.13 is Idle (rung 1 broken)
  - RR1 `show route 10.255.0.13` returns only the static default 0.0.0.0/0 via
    MgmtEth. No specific route to PE3's loopback (rung 3 broken)
  - RR1's own IS-IS is healthy: P1 and P4 both Up for 2d20h. The fault is not on RR1
  - PE3 has zero IS-IS adjacencies
  - PE3 physical interfaces Gi0/0/0/0, Gi0/0/0/1, Gi0/0/0/2 are all up/up
    (rung 5 healthy)

Lowest broken rung is 4. IS-IS is down on PE3 while the physical layer is intact,
so IS-IS was disabled at the protocol level on the core interfaces rather than the
links failing.

Gi0/0/0/2.300 is line-down but is the pre-existing subinterface condition, excluded
by EACH_PHYSICAL_INTERFACE.

Corroboration: session shows Up/Down 00:00:05 at 14:07:23, so it dropped ~170s after
the change landed at 14:04:28 — the BGP hold timer expiring, consistent with the
transport path disappearing rather than a direct session teardown.
---- END INDEPENDENT DIAGNOSIS ----
```

- **Evidence:** This entry, committed before the agent run. The agent's result is OBS-077.
- **What I did:** Recorded it and committed, before running anything.

  One note on what the ordering does and does not buy, because it is worth being precise rather than ceremonial. **The descent itself cannot be contaminated by my having read the above**: `run_descent` is deterministic code with no model in it, so the rung it reaches is a function of the device output alone. The ordering matters for the two places judgement enters — the *report prose*, which a model writes, and *my acceptance judgement*, which is exactly the thing the operator's protocol is protecting. Reading a conclusion before assessing an answer is how agreement gets given too easily, and that risk is real regardless of the determinism below it.
- **Needs human review:** no
- **Blocks:** none — OBS-077 is the agent run.

---

## OBS-077 · T-033 · **Q-006 answered: the agent and the engineer reached the same rung** — and the run found two defects

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (descent, deterministic) · MiniMax (report, correlation)
- **What happened:** Live run against the real fabric, `RR1 → 10.255.0.13`, fault applied by the operator, ground truth sealed. The hand diagnosis was recorded and committed at **14:11:50 UTC** (OBS-076, commit `093d665`); the agent ran at **14:12:02 UTC**.

  **They agree exactly.**

  | | Hand diagnosis | Agent |
  |---|---|---|
  | Rung | `igp_adjacency` | `igp_adjacency` |
  | Device | PE3 | PE3 |
  | Finding | `igp_isolated` | `igp_isolated` |
  | Rung 5 | healthy, subinterface excluded | `HEALTHY — 3 of 3 members healthy` |
  | Broken above | rungs 1 and 3 named | `bgp_session`, `transport`, `route_to_peer` |

  The agent's causal chain carries `transport` explicitly, which the hand diagnosis implied but did not enumerate; otherwise the two are the same argument.

  **Q-006 is answered yes**, and on the case worth answering it with. The symptom from RR1 — BGP Idle, no route to the loopback — is *indistinguishable* from the captured `broken` label, where the cause was `interface` on PE2. Anything pattern-matching the symptom lands on the wrong rung. Both the engineer and the descent walked past a **healthy** interface rung to report the broken IGP rung above it, which is the corrected Q-017 semantics doing the work it was rewritten for, on real state, for the first time.

  **Run metrics**

  | | |
  |---|---|
  | Wall clock | **114.2 s** (healthy baseline 103 s, **+11%**) |
  | Exit code | **1** — a fault, and the answer is trustworthy |
  | Report | emitted, **grounded: 7 observations, 14 citations, 5/5 rungs cited** |
  | Correlation | emitted, `found: true`, timeline of 9 events |
  | Coverage | `device_buffer`, 200 of **684** records, `complete: false` — and the model said so unprompted in its summary |
  | Repairs | none — no markdown fence, valid JSON both calls |
  | Prompt size | 6,673 chars report + 13,218 correlate ≈ **5k tokens** per investigation |

  Every report citation resolved. The model's recommendation is correctly scoped and flagged: *"Verify PE3's IS-IS configuration … before touching the interfaces"* — it declined to blame the layer that was healthy.
- **Evidence:** `docs/build/FINDINGS.md` OBS-076 committed at `093d665` before the run. Run output and payload captured.
- **What I did:** **The live run found two defects. Both were invisible to 1,348 passing tests.**

  **Defect 1 — `_log_window` never worked, and no test could have found it.** The first run exited **2** with the correlation withheld. Two bugs in one function:

  * `count=200` passed as an `int`, where `render_command` requires text — canonicalize-by-reconstruction rejects it at the boundary rather than coercing. Every log read on every device failed with `count: expected a string, got int`.
  * the output read from `data["outputs"]`, where `run_template` writes `data["commands"]`.

  Neither raised. A structured error became an empty window with no coverage, which `check_absence_coverage` then correctly refused to let anything claim absence over. **The only symptom was a missing timeline.**

  `_log_window` was written in T-030 and **every test in the suite passes `window=`**, so the real function was never executed. §0.13's tests face, exactly: *the tests confirmed the stub.* Fixed, and now covered by two tests that call it for real — one asserting a successful read produces coverage, one asserting a failed read produces a window with **no** coverage rather than an empty one, since flattening those two is what would turn a broken log read into a confident "no correlating events".

  **Worth noting what went right here:** exit 2 was correct. The scheme refused to call the run trustworthy while part of the pipeline had silently failed, and that is the behaviour I argued for at T-031 — a defect surfacing as a distinct exit code instead of hiding in the noise of routine faults. The tool caught its own bug.

  **Defect 2 — the correlation path has no citation check at all, and it fabricated a timestamp.** The emitted timeline contains:

  ```
  Aug 14 04:28.238 UTC   ROUTING-ISIS-5-ADJCHANGE   Adjacency to P2 ... Down
  ```

  The real record is `Aug 16 14:04:28.238 UTC`. The model dropped characters and produced a **malformed date, two days earlier**, in the one field `correlate.v3` constraint 2 says to quote exactly. Verified against the window: **1 of 9 timeline timestamps does not exist in the evidence.**

  **Grounding passed it.** `ground_correlation` runs `check_absence_coverage` only, which returns a *vacuous pass* whenever `found` is not `False`. So a correlation asserting presence is emitted with **zero verification** — `correlation grounding: vacuous pass — 0 observations, 0 citations` is printed in the payload, and I did not read it as the warning it was until the timestamp caught my eye.

  This is the precise mirror of the gap T-029a closed, and I missed it *because* I closed that one:

  | | presence | absence |
  |---|---|---|
  | **report** | checked — every `evidence_key` must be one the descent read | n/a |
  | **correlation** | **unchecked** | checked (T-029a) |

  T-029a asked "what does grounding not check for correlations?", found absence, fixed it, and the symmetry with the report's presence check made the other half *feel* covered. It was not. The `vacuous` flag was even reporting it truthfully in every payload.

  **Not fixed** — §0.3: a defect outside the current task's scope is logged, not fixed. Filed as **B-424** and recommended as **T-029b before T-034**, mirroring how B-420 became T-029a. The fix is small and already specified by the existing code: every timeline entry's `at` must be a timestamp present in the shaped window and its `mnemonic` must match the record at that timestamp — `check_grounding`'s evidence-key rule, applied to the other output.

  **Token accounting is not instrumented.** `complete_prompt` returns text only; neither the Anthropic nor the OpenAI/MiniMax path surfaces `usage`. T-033 asks for token usage and I can only report the proxy above (~5k tokens of prompt per investigation, response excluded). Filed as **B-425** rather than reported as a number I did not measure.
- **Needs human review:** **yes** — defect 2 is a grounding hole that emitted a fabricated timestamp to a user
- **Blocks:** none for T-034, but B-424 should land first.

---

## OBS-078 · D6 · Two simultaneous faults — the descent cannot tell one from two, and the output is identical

- **Kind:** risk
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 · raised by the operator
- **What happened:** D6's stopping rule — *the lowest broken rung is the root cause* — assumes the broken rungs form a **chain**: each is broken because of the one below it, so fixing the lowest fixes all of them. Under two independent simultaneous faults that assumption fails, and **nothing the descent can see changes.**

  The operator's example: an interface admin-down **and** the BGP neighbour administratively shut. The lowest broken rung is the interface. The descent reports it. It is genuinely broken — and **fixing it will not bring the session up.**

  The sharp version, which is worse than the example suggests:

  > A single interface fault, and an interface fault plus a BGP shut, produce **identical rung verdicts**. Every rung broken, lowest is the interface. One is a complete answer and the other is half of one, and the rung table is byte-identical.

  Any fault *above* the lowest one is masked, and it is masked precisely *because* the layer beneath it is also broken — which is the normal, correct case the ladder was designed for. The masking is not a bug in the walk; it is the walk working as specified against a situation the specification did not consider.

  This is the sixth silent-failure shape (§0.13) in a new place: not absence read as presence, and not wrong evidence read as right, but **a correct answer indistinguishable from a complete one.**
- **Evidence:** Unanswerable from the fixture corpus — there is no two-fault capture in `tests/fixtures/`, on any label. That absence is itself the point: every label was produced by one deliberate change.
- **What I did:** Recorded it in D6 directly, as an open section rather than a footnote, and filed the experiment that answers it.

  **Two candidate signals, neither validated, both worth stating so the experiment has hypotheses rather than just data:**

  **1. Timeline.** Two independent faults rarely land in the same instant. One fault produces one configuration commit; two produce two, separated in time. The T-033 window shows this shape already — adjacencies down at 14:04:28.238 coincident with a commit, BGP following 165 s later on the hold timer. A *second* commit elsewhere in the window, unexplained by the localised cause, is the signature. **This is the strongest argument yet for episodes (B-416)**, and it upgrades that item from "better rendering" to "the mechanism for detecting a masked second fault".

  **2. Forward consistency.** After localising, ask whether the rungs *above* look the way this cause **alone** predicts. An interface down predicts a session that timed out; an administratively shut session reports a distinguishable state. A mismatch between predicted and observed upper rungs is positive evidence of a masked fault. Note this is the inverse of everything the descent does — it currently reasons only downward, and never checks that the cause it found accounts for the symptoms it started from.

  Neither can be settled by reasoning and neither by the current corpus. **Only injection answers it** — B-426, whose two-fault combinations exist for this question specifically.

  **On sequencing, and I agree with the operator's instinct here.** T-033 produced agreement on one rung. That is one data point, and building a harness on it would be fitting infrastructure to a single observation. Four manual rounds first, and — the part worth adding — **landing on four different rungs**, because four rounds of the same fault shape is one data point sampled four times, which is §0.13's data face. The suggested coverage is in B-426, and round 4 is deliberately a **true negative** (`all_layers_healthy` under a perturbation the IGP absorbs), since a corpus made only of faults never tests the outcome a false-positive-prone system gets wrong.
- **Needs human review:** **yes** — this is an unresolved question in a load-bearing decision
- **Blocks:** nothing in MVP-0. D6's rule stands for single faults, which is every case the corpus contains.

---

## OBS-079 · design · Forward consistency is a general architectural gap — and the corpus already contains an instance

> **Operator ranking: this finding outranks the task it was found in.** A cause reported on an Established session carrying traffic is precisely the outcome `chaos-harness.md` §4.3 says a fault-only corpus can never surface — found before the harness existed, and now a *predicted* adverse result for round 4 (OBS-082) rather than a hoped-for silence.

- **Kind:** defect
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Raised while amending `chaos-harness.md` §7, and it turned out to be larger than the two-fault question it came from. **The operator asked that it be recorded as a general architectural gap rather than only a two-fault detector, and that is right — it is a closed loop the design lacks.**

  **The descent reasons strictly downward and never checks that the cause it found accounts for the symptom it started from.** Every rung asks "is this layer broken"; nothing ever asks "does the thing I localised explain the thing I was called about".

  **A measured instance, found offline, needing no injection.** Construct the round-4 perturbation — one uplink down on a device with two, IGP reconverges over the survivor:

  ```
  rung 1  bgp_session    HEALTHY     <- the session is Established and working
  rung 2  transport      HEALTHY
  rung 3  route_to_peer  HEALTHY
  rung 4  igp_adjacency  HEALTHY
  rung 5  interface      BROKEN      <- ALL_HEALTHY over EACH_PHYSICAL_INTERFACE
  ```

  `DescentResult.cause` → **`interface`**. `causal_chain` → **empty**. The descent localises a fault on a session that is up and carrying traffic.

  **The empty causal chain is the tell, and the descent already has it.** A cause with no broken rungs above it means "I found something broken and nothing above it is affected" — which, for an investigation that began from a symptom, is a contradiction: the symptom should *be* one of those upper rungs. Rung 1 healthy means there was no symptom. The information needed to catch this is already in the `DescentResult`; nothing reads it.
- **Evidence:** Constructed against the real `bgp_session` ladder via `DescentResult`, offline. Reproducible without a lab.
- **What I did:** Recorded, filed, **not fixed** — §0.3, and T-029b is the task in front of me. Filed as **B-428**.

  Three notes on scope, because the obvious narrow fix is wrong.

  **1. This is not "add a healthy-rung-1 special case".** The general rule is *forward consistency*: after localising, check that the observed upper rungs match what the cause **alone** predicts. Rung 1 healthy is the degenerate instance where the prediction is "there is nothing to explain". The two-fault case is the instance where the prediction is "the session timed out" and the observation is "the session is administratively shut" — same check, different mismatch.

  **2. It bears on `EACH_PHYSICAL_INTERFACE` + `ALL_HEALTHY`, which was a deliberate decision (OBS-057) with a stated revisit condition.** This is that condition arriving. The rule is right for *isolation* — all uplinks down means isolated — and produces a false positive under *redundancy*, where one down uplink affects nothing. Forward consistency is the better fix than weakening the aggregation, because weakening it would lose the isolation case that the rule exists for.

  **3. It is the second-strongest argument for the harness, and the first that does not need it.** Round 4 was chosen as a true negative — "does the tool stay quiet under a perturbation the IGP absorbs". The prediction is now specific and adverse: **it will not stay quiet.** A round that predicts a particular failure is worth more than one that hopes for silence, and this one can be checked offline first, so the live round confirms rather than discovers.
- **Needs human review:** **yes** — a false positive on a healthy session is user-visible
- **Blocks:** nothing immediately. Should be settled before the four rounds, since round 4 is designed to hit it.

---

## OBS-080 · process · Three improvements to operator specifications, recorded as such

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** The operator asked that three amendments accepted this session be recorded **as improvements on what was specified**, rather than folded silently into the documents. Recorded here so the direction of each correction is visible, in the same spirit as OBS-062's note that three T-028 findings corrected the document rather than the implementation.

  | # | As specified | As amended | Why it matters |
  |---|---|---|---|
  | 1 | The injector prohibition filed in `chaos-harness.md` §3.1 alongside §0.11 | Split into two rule *kinds*, with the blinding rule explicitly **not** a §0.11 rule | §0.11 rules get waived on reversibility and supervision — twice in this build, both times correctly. The same reasoning applied to the blinding rule would waive it, and every argument would be sound and beside the point |
  | 2 | Two-fault output is "true, incomplete, and actionably misleading" | The rung tables are **byte-identical**, and the masking is structural | "Incomplete" suggests a careful reader might notice. Nothing distinguishes the two outputs. The consequence is decisive: **no care within the descent can fix it**, because the signal is not in the rung verdicts |
  | 3 | Non-contiguity is "the most promising and cheapest" signal, to be tested first | Sound but **blind to the canonical case**; forward consistency goes first | Interface-down plus BGP-shut leaves every rung broken and perfectly contiguous. Non-contiguity catches only the cases where neither fault propagates far enough to mask the other |

  And one in the other direction, recorded for symmetry: the operator's **round 3** — replacing the build's only composed fixture (`cause_not_localised`, the single synthetic artefact in the corpus) with a captured one — closes a gap I had noted at T-027 and left open. I had treated "no consistently-behaving fabric can produce this" as a fact about fabrics. It is a fact about *single* faults; an administratively shut session with a healthy underlay produces it directly.
- **Evidence:** `chaos-harness.md` §3.1 and §7; `docs/design/design-thinking.md` D6; `prompts/tests/cases/report.cases.json` `fixture_provenance`.
- **What I did:** Recorded. The pattern across all four is the same and worth stating once: **the corrections that mattered were about the *kind* of a thing, not its content.** A rule of method filed as a rule of safety; an incompleteness filed as a detectability problem; a signal filed as primary when it is partial; an impossibility filed as a property of fabrics when it is a property of single faults. In each case the original statement was true and the classification was what misled.
- **Needs human review:** no
- **Blocks:** none

---

## OBS-081 · T-029b · Presence checking for correlations — and the flag that was reporting the gap all along

> **Operator ranking: the second finding here outranks the task.** Not the fix — the observation that a guard reported the gap honestly in every payload and nobody read it. Promoted to **T-029c**.

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Closed B-424. `ground_correlation` now runs `check_timeline_citations` alongside `check_absence_coverage`, and the runner passes it the shaped window. **1365 passed.**

  The rule is the report's, applied to the other output: every timeline entry's `at` must be a device timestamp present in the window, and its `mnemonic` must match a record at that timestamp. The second rule is not redundant — a verbatim-correct timestamp attached to an event that did not happen at it is the same fabrication wearing a valid citation, and a timestamp check alone passes it.
- **Evidence:** `grounding.check_timeline_citations`; 15 tests, including the live T-033 fabrication pinned verbatim as a regression. `1365 passed`, lint clean.
- **What I did:** Two things worth recording beyond the fix.

  **1. The vacuous flag was reporting this in every payload, and I read past it.** §0.12 made `GroundingResult.vacuous` exist precisely so a pass over nothing would be distinguishable from a real pass — and the T-033 output printed

  ```
  correlation grounding: vacuous pass -- nothing to verify: 0 observations, 0 citations
  ```

  next to a nine-entry timeline. The instrument worked. **I did not read it**, and I only looked because a malformed date caught my eye in the prose.

  That is a distinct failure from the ones catalogued in §0.13, and worth naming as such: not *the evidence could not have shown you*, but **the evidence did show you and nobody looked.** A guard that reports honestly into an output no one reads is, in effect, not running. The practical consequence is narrow and cheap: a `vacuous` pass on a payload that plainly contains claims is a contradiction the *code* should surface, not something a reader should have to notice. Filed as **B-429**.

  **2. Scoping held.** The fabricated timeline withholds only the correlation. The descent is untouched — it is deterministic and has no model in it — and the report still grounds and emits. That is the two-gate design doing what it was built for, and the runner test asserts all three facts together so a future change that collapses them fails.
- **Needs human review:** no
- **Blocks:** none — T-034 next.

---

## OBS-082 · round 4 · **PREDICTION, recorded before the round runs** — the true negative will fail

- **Kind:** risk
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Round 4 of the manual injection sequence is a **true negative**: a real change the fabric correctly absorbs. Recorded here **before** the round runs, on the same protocol as the T-033 hand diagnosis (OBS-076) and for the same reason — *a predicted failure that then occurs is worth more than a discovered one*, because only the first distinguishes understanding the defect from noticing it.

  **Setup.** One core-facing uplink administratively shut on a device that has two. The IGP reconverges over the survivor. The BGP session to that device's loopback stays **Established and carries traffic** throughout. Nothing about the service is affected; the correct answer is that nothing is wrong.

  **Predicted agent output:**

  | | Predicted |
  |---|---|
  | rung 1 `bgp_session` | HEALTHY |
  | rung 2 `transport` | HEALTHY |
  | rung 3 `route_to_peer` | HEALTHY |
  | rung 4 `igp_adjacency` | HEALTHY |
  | rung 5 `interface` | **BROKEN** — `ALL_HEALTHY` over `EACH_PHYSICAL_INTERFACE` |
  | `finding` | `interface_line_down` |
  | `cause` | **`interface`** |
  | `causal_chain` | **empty** |
  | exit code | **1** — a fault reported on a working session |

  > **A cause with no chain is a cause explaining nothing.**

  **Confidence: high.** This is not a guess about model behaviour — the descent is deterministic and model-free, and the outcome was already produced offline against the real `bgp_session` ladder (OBS-079). The live round tests that the *fabric* behaves as expected (the IGP does reconverge, the session does stay up), not that the descent does. If the session drops, the round is invalid as a true negative and must be re-set with the redundancy verified first.

  **What would falsify the prediction**, stated so it is not unfalsifiable: rung 5 returning HEALTHY (the aggregation is not what I believe), or the finding coming back `all_layers_healthy` (something downstream already suppresses a chainless cause), or exit 0. Any of those means B-428 is wrong about the mechanism and the finding needs rewriting rather than confirming.
- **Evidence:** OBS-079's offline construction against the real ladder. `chaos-harness.md` §4.3.
- **What I did:** Recorded and committed before the round. Two notes on why this round is worth running at all given the outcome is already known offline.

  **1. Offline construction proves the descent's arithmetic; the round proves the premise.** I constructed rungs 1–4 healthy and rung 5 broken *by hand*. Whether a real one-uplink shutdown actually produces that state — whether the IGP reconverges fast enough, whether the session survives the reconvergence, whether `interface_state` reads a shut interface the way I assumed — is a fact about the fabric that only the fabric can supply. The offline result would be worthless if the premise were wrong.

  **2. It is the round most likely to be quietly skipped.** Three rounds that find faults feel productive; a round designed to find nothing feels like a formality, and it is the one where this architecture is structurally weakest — the walk is built to find the lowest broken thing, and *"nothing that matters is broken"* is the one answer it has no mechanism for reaching. Writing the prediction down in advance is partly to make skipping it visibly a choice.
- **Needs human review:** no — but the round is now specified and should not be run before B-428 is settled or deliberately deferred
- **Blocks:** none

---

## OBS-083 · T-029c · A warning nobody reads is not running — and the audit that kept the rule narrow

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Closed B-429, promoted to a task by the operator before T-034. `ground_report` and `ground_correlation` now refuse a payload whose verdict is `vacuous` while the payload itself plainly contains claims. **1377 passed.**

  > **Any warning that requires a human to notice it will eventually not be noticed. Where a condition is checkable, check it.**

  §0.12 built `vacuous` precisely so a pass over nothing would be distinguishable from a real pass, and it did its job perfectly — printing `vacuous pass, 0 citations` beside a nine-entry timeline in every T-033 payload. The mechanism was not the problem. **Reading was the problem**, and reading is not a mechanism.
- **Evidence:** `grounding.claims_present`, `_refuse_unmeasured`; 6 tests. `1377 passed`, lint clean.
- **What I did:** Three things.

  **1. Audited every reader-facing flag in the package, and deliberately changed only one.** `unattributed_kept`, `repairs`, `retries` and `unaccounted_lines` all stay informational. The distinction that decides it: a **contradiction** (the verdict says it measured nothing, the payload says it asserted things) is raised; a **fact** (the noise filter kept 8 records it could not attribute) is reported. Nothing is inconsistent about a fact.

  This narrowness is load-bearing rather than conservatism. **Turning every reader-facing number into an error is the same failure wearing the opposite sign** — it produces a stream of warnings people learn to skip, which is exactly how the T-033 warning went unread in the first place. A rule against unread warnings that generates unread warnings has defeated itself.

  **2. It caught a case I had not looked for.** `{"correlation": {"found": true}, "timeline": []}` — a positive correlation asserting a result with no cited event — was previously accepted by every gate. That is the purest form of the defect and it was reachable before this change.

  **3. Three existing tests failed, and all three were asserting the old, weaker behaviour.** `test_presence_is_not_weakened_by_a_coverage_gap` passed a bare `found: true` with no timeline and no window; rewritten to use a real cited timeline, because *"presence survives a coverage gap"* is a claim about **cited** presence, not about the word `true`. Two malformed-correlation cases asserted `result.ok` for payloads that were already malformed; they now assert the narrow property they were about (the absence rule does not fire) **and** that the gate refuses them anyway.

  Worth noting the shape: **the new check's first act was to expose three tests that had been encoding the defect as expected behaviour.** They passed before because the code and the tests shared the premise — §0.13's tests face.

  **And this is the first *prospective* catch of that face in the build.** Every prior instance was found after something went wrong: T-013's route shape by a failing parse, T-026's refusal marker by a second prompt arriving, T-028's noise filter by an independent specification, T-033's `_log_window` by a live run. Each was a post-mortem.

  Here the sequence ran the other way. A change was made on principle — *check the condition rather than report it* — and the change **exposed three tests that had encoded the defect as correct**, before any incident. Nothing had gone wrong; nothing was going to go wrong until a model emitted a bare positive claim.

  That is the mechanism working as intended, and it is worth knowing it **can** work that way, because §0.13 as written reads like a diagnosis rather than a tool. The operational form: **a change that tightens a rule will fail exactly the tests that encoded the old rule as correct — and those failures are findings, not breakage.** The instinct to "fix the tests" back to green is the instinct to restore the defect. All three of these looked like ordinary breakage and all three were the check reporting on the suite.

  One precision the tests pin: on the exact T-033 shape the failure raised is `uncited_timeline`, **not** `verified_nothing`. The timeline check *ran*, found no window, and refused. `verified_nothing` is the backstop for when nothing examined the payload at all — a gate that refused something did not measure nothing, and conflating the two would make the specific diagnosis disappear behind the generic one.
- **Needs human review:** no
- **Blocks:** none — T-034 next.

---

## OBS-084 · T-034 · Documenting the failure is the persuasive part

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** README, `CLAUDE.md` and `.env.example` updated. The two operator requirements — `--from-fixtures` first, and the T-033 result documented **including the fabricated timestamp** — are now recorded in the T-034 spec as binding on future edits, not just satisfied once.
- **Evidence:** `README.md` +146 lines, `CLAUDE.md` +40, `.env.example` +10. `1377 passed`, lint clean, doc-sync tests green.
- **What I did:** Three notes.

  **1. The honest account is the stronger one, and it is worth understanding why rather than treating it as a virtue.** A README claiming the model never errs invites exactly one question — *what happens when it does?* — and has no answer. The T-033 section answers it with the real case: the model corrupted a device timestamp into a malformed date two days early, **and the diagnosis was unaffected, because the diagnosis was never the model's to produce.** That single paragraph carries the architecture better than the architecture section does. The failure is the proof: a system where a model error is confined to the timeline is visibly a different shape from one where a model error is the answer.

  **2. `.env.example`'s MiniMax block already existed; the real gap was elsewhere.** The spec said "MiniMax variables" and they had been added at T-003. What was missing was **which surfaces need a provider at all** — a reader had no way to learn that `investigate` needs one only for the report and timeline, and that `--no-model` and `--from-fixtures` need none. Added as a table in the config file itself, where the question is asked.

  **3. `--from-fixtures` being first is now a documented constraint rather than a layout accident.** It sat first because T-031 put it there; nothing recorded *why*, so the next person tidying the README into a conventional order would have moved it below the feature list without noticing they had removed the demonstration. The reason is now in the T-034 spec.
- **Needs human review:** no
- **Blocks:** none — M4 next.

---

## OBS-085 · M4 · MVP-0 complete, and the review's uncomfortable number

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** M4 marked. T-001–T-034 plus T-029a/b/c, 97 commits, **1377 pass / 22 skip**, four frozen files byte-identical against `6629a2c` — the commit before T-001, verified against that baseline rather than against the repo root, which is a different diff and does show changes. `docs/build/MVP0-REVIEW.md` written before any MVP-1 work.
- **Evidence:** `MVP0-REVIEW.md`; TRACKER M4 row.
- **What I did:** Wrote the review, and two of its answers are worse than I expected going in.

  **1. The silent-failure rate did not fall.** The operator asked whether it fell as the rules landed or whether we just got better at spotting them. **The second, and slowly.** Roughly fourteen instances across six shapes, and instances kept arriving through T-033 and T-029c — after §0.12, §0.13 and §0.14 were all written down. What changed is the *detection mechanism*: incident → companion test → independent specification → **prospective**, and the last happened exactly once (T-029c).

  **One prospective catch out of fourteen is the honest score**, and the review says so. Nothing in the log book supports a claim that the rules prevent these failures. They make them findable and give them names, which is worth having and is a smaller claim than the rules' confident tone implies.

  I also recorded why the rate cannot be computed cleanly rather than computing one: the taxonomy was built retrospectively, the tasks are not uniform in size, and the later tasks were deliberately the judgement-dense ones. A rate across that measures the denominator.

  **2. Forty-three percent of the log book is `decision-made`** — 37 of 86. Decisions the plan did not specify. That is not plan failure, but it says the judgement-to-typing ratio was far higher than a 34-task list implies, and a future plan of this shape should budget for it rather than discover it.

  **3. Q-006 was originally the wrong question**, and it is recorded as such. *"Does the stopping rung match what an engineer concludes from the same fixtures?"* would have compared two readings of a corpus the code was written against. It would have agreed, and the agreement would have meant nothing. The version that was answered — a live fault, never seen, whose symptom is identical to a captured fault with a different cause, hand diagnosis committed first — is a different question, and the reframing was the operator's.

  **The section I expect to matter most in six months** is §3, the six design decisions measurement corrected. The uncomfortable part is not that the documents were wrong; it is that they were **good** and wrong in six places, every one producing a plausible answer. A build that had trusted them would have shipped all six.
- **Needs human review:** the review itself is the artefact for review
- **Blocks:** nothing. **Next is the four injection rounds, not MVP-1.**

---

## OBS-086 · planning · B-401 parked, and three tracks — with the shared contracts named

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** Post-M4 planning. **B-401 (Juniper) parked** — there is no Junos device or container to capture from, and writing templates against vendor documentation would be building against a specification, which this build corrected six times out of six by measuring instead (MVP0-REVIEW §3). Revisit condition recorded: a device to capture from, and nothing else.

  Three tracks recorded, one agent each, merge order A → B → C.
- **Evidence:** `BACKLOG.md` B-401 and *What happens next*; `BUILD-PLAN.md` §0.9a.
- **What I did:** Three additions the instruction implied but did not state, each because the rule is otherwise unenforceable.

  **1. Enumerated the shared contracts by name.** "Any change to a shared contract is a HALT" is unusable without a list — two agents will disagree about what counts, and they will disagree *silently*, which is the whole failure mode. Eight are named, with an owner each. The one that matters is **`log_window.ShapedWindow`**: track B's B-414 and B-416 will want to add aggregated records and episodes to it, and `prompt_library.build_correlate_prompt` and `grounding.check_timeline_citations` both consume it. That is the collision, and it is at the seam between the two tracks that are not supposed to couple.

  **`investigation.py` is named as the most likely merge conflict** — it wires every module both tracks touch, and it is on track A's side of the line while depending on track B's output.

  **2. §0.9a states why the HALT is stricter than §0.11's ladder would make it.** Most contract edits are, on their face, DECIDE-AND-LOG: a defensible technical choice with no safety consequence. That classification is correct on one branch and wrong on three. **A defensible choice made independently on two branches produces two defensible, incompatible contracts** — and the incompatibility surfaces at merge, when both are finished and both authors are confident. §0.14: the same statement, different kind of claim, depending on whether one agent or three are acting on it.

  **3. Amended the five-flow note, which contradicted the new sequencing.** B-107–B-111 carried "Parallelisable — these five are where concurrent Sonnet 5 agents genuinely pay off". That is now wrong in two ways: they do not start until B-428 lands, and `isis_adjacency` runs serially first. Left unamended it would have read as standing authorisation to fan out five agents.

  The reason is worth stating rather than just the schedule: **the descent has produced one correct blind diagnosis, on one flow.** Spawning five concurrent implementations off a pattern demonstrated once replicates an unvalidated assumption five times — §0.13's data face applied to project planning rather than to a corpus. One instance fits one instance, whether the instance is a fixture or a flow.

  **On B-401's payoff test.** The operator's framing is the important half and I have recorded it prominently: *do not let the absence of a counterexample read as confirmation.* No code has ever run against a second vendor, so nothing has had the **opportunity** to leak upward. A boundary that has never been pushed on is not a boundary that has held, and `juniper_junos` exists in the tree precisely to keep the abstraction honest while being itself unverified — which makes it easy to mistake for evidence.
- **Needs human review:** no
- **Blocks:** none. Track A starts with the four rounds.

---

## OBS-087 · round 2 · The independent hand diagnosis, recorded before the agent run

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** — (human operator)
- **What happened:** Round 2 of the manual injection sequence (track A). Subject `RR1 10.255.0.11`. Fault live, ground truth sealed. Recorded verbatim and committed **before** the agent runs, on the protocol established at OBS-076: the ordering is a fact in git history rather than a claim in a document.

  Recorded without comment or evaluation.

```
---- BEGIN INDEPENDENT DIAGNOSIS (round 2, recorded 15:42 UTC, before agent run) ----
Rung: transport.  Finding: transport_blocked.  Subject: the RR1<->10.255.0.11 session.

Evidence:
  - rung 1 broken: 10.255.0.11 is Active, up/down 00:03:28
  - rung 3 HEALTHY: show route 10.255.0.11 returns a /32 via isis CORE, distance 115,
    metric 20, labeled SR, primary via Gi0/0/0/0 plus an LFA backup, installed 2d22h.
    The route never went away.
  - rung 4 HEALTHY: RR1 IS-IS to P1 and P4 both Up for 2d22h, unchanged
  - rung 5 HEALTHY: all physical interfaces up/up on the device inspected.
    Gi0/0/0/2.300 is the pre-existing subinterface; srte_c_20_ep is a tunnel.

Reasoning: the route is present and stable, the IGP never flapped, and the session
still will not establish. "Active" means the local speaker is retrying the TCP
connection — a transport failure, not a reachability one. Nothing beneath rung 2 is
broken, so the descent should stop at rung 2 rather than descend to a healthy rung.

Not determinable from this evidence: whether the far end is rejecting the connection
(MD5 mismatch or an administratively shut neighbour) or something is blocking port 179.
Same rung either way.

Caveat: 10.255.0.11 appears to be P1's loopback, not PE1's — RR1 has no direct link to
PE1, and the route resolves via 10.0.1.17 toward P1. If so, the faulted device and the
subject device are different machines, which is not the round that was planned. If the
agent finds every rung healthy and reports all_layers_healthy, that mismatch is the
finding, not a diagnostic error.
---- END INDEPENDENT DIAGNOSIS ----
```

- **Evidence:** This entry, committed before the agent run. The agent's result is OBS-088.
- **What I did:** Recorded and committed, before running anything.

  **This round is scored on a rung the corpus has never localised to.** Round 1 landed on `igp_adjacency`; the captured `broken` label lands on `interface`. `transport` would be the third distinct rung, which is the point of sequencing four rounds across four rungs rather than repeating one.

  **And it carries a second question the sequence had not yet reached.** The operator's caveat flags that the subject may not resolve to the device that was changed — the first trial where those differ. That is recorded and scored **separately** from the diagnostic result (OBS-089), because conflating them would let a device-resolution defect read as a diagnostic failure, or the reverse. Which of the two it is cannot be decided before the run, and deciding it after is exactly the judgement the blind protocol protects.
- **Needs human review:** no
- **Blocks:** none — OBS-088 is the agent run.

---

## OBS-088 · round 2 · **Second match, on a third rung** — `transport_blocked`

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (descent, deterministic) · MiniMax (report, correlation)
- **What happened:** Hand diagnosis committed `ed77882` at **15:42:34 UTC**; agent ran at **15:42:45**. Ground truth sealed.

  **They agree exactly.**

  | | Hand | Agent |
  |---|---|---|
  | Rung | `transport` | `transport` |
  | Device | RR1 | RR1 |
  | Finding | `transport_blocked` | `transport_blocked` |
  | rung 1 | broken — Active | `BROKEN` — state Active |
  | rung 3 | **healthy**, route present and stable | `HEALTHY` — route present, 2 paths |
  | rung 4 | **healthy** | `HEALTHY` — 2 adjacencies, all Up |
  | rung 5 | **healthy** | `HEALTHY` — 3 of 3 members |

  **This is the third distinct rung across three trials** — `interface` (captured `broken`), `igp_adjacency` (round 1), `transport` (round 2) — and the first where the descent had to **stop at rung 2 with three healthy rungs beneath it**. Rounds 1 and 3 of the ladder are worth distinguishing: round 1 required walking *past* a healthy rung to a broken one below; this one required *not* descending into healthy rungs and reporting a cause above them. Opposite pressures on the same walk rule, both correct.

  | | |
  |---|---|
  | Wall clock | **121.7 s** (round 1: 114.2 s; healthy baseline 103 s) |
  | Exit code | **1** |
  | Report | emitted, **grounded: 5 observations, 10 citations, 5/5 rungs cited** |
  | Correlation | **`coverage_limited`** — 200 of **847** records retrieved |
  | Citation integrity | every report citation resolved; the single timeline entry cited a real record |
  | Repairs | none |
  | Character proxy | comparable to round 1 (~5k tokens of prompt); both runs retrieve 200 log records, so the correlate prompt dominates and is stable across rounds |

  The model's recommendation is correctly scoped to the rung: capture the BGP OPEN attempts, check TCP 179 filtering and the local neighbour configuration. It did not speculate about the far end.
- **Evidence:** OBS-087 committed before the run. Payload captured.
- **What I did:** Two observations.

  **1. The coverage machinery earned its place on a case it was not designed for.** The model answered `found: false` — no correlating events — over a window holding **200 of 847** records. T-029a refused it as a clean negative and the runner downgraded it to `coverage_limited`, exit code unchanged at 1 because the descent's finding is deterministic and only the timeline is qualified. That is the T-031 decision behaving exactly as specified, on a round where the fault genuinely leaves no trace in a device buffer — a transport-layer block produces one `ROUTING-BGP-5-ADJCHANGE` and nothing else, which is precisely the case where "no correlating events" is both true and unsupportable.

  **2. One prompt-adherence miss, not a grounding failure.** `correlate.v3` says *"When `found` is false, `timeline` is an empty list."* The model returned `found: false` **with a one-entry timeline**. Grounding accepted it, correctly — the entry cites a real record at a real timestamp, so nothing is fabricated. It is a contract violation with no evidential consequence, which is the right place for one to land. Noted rather than filed; if it recurs it is a prompt clarity problem, not a validation gap.
- **Needs human review:** no
- **Blocks:** none — round 3 next.

---

## OBS-089 · round 2 · The device-resolution question, scored separately — and why this round could not have answered it

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator's hand diagnosis carried a caveat, correctly hedged: *"10.255.0.11 appears to be P1's loopback, not PE1's — RR1 has no direct link to PE1, and the route resolves via 10.0.1.17 toward P1."* If true, the subject would resolve to a different machine than the one that was changed — the first trial where those differ.

  **Resolved by reading the devices: the resolver was right.**

  ```
  PE1  Loopback0   Internet address is 10.255.0.11/32   <- the subject
  P1   Loopback0   Internet address is 10.255.0.1/32
  ```

  `10.0.1.17` is the **transit next-hop toward** PE1, not the owner of the destination. RR1's route to `10.255.0.11/32` carries `from 10.255.0.11` with two paths — a protected primary and a Local-LFA backup — which is a loopback advertised into IS-IS by its owner, reached through a neighbour.

  The caveat's own falsification condition also fired: it predicted that a mismatch would show as the agent reporting `all_layers_healthy`. The agent reported two broken rungs.
- **Evidence:** `show interfaces Loopback0` on PE1 and P1, read directly. `show route 10.255.0.11/32` on RR1.
- **What I did:** Recorded it separately from OBS-088, as instructed, and then found the more important thing — **this round could not have detected a resolver error even if one existed.**

  The cause is at rung 2, which is `DeviceScope.LOCAL` and needs no resolution at all. Rungs 4 and 5 *were* resolved, to PE1, and both returned healthy. **P1's IGP and interfaces are also healthy.** So had the resolver pointed at P1 instead, the descent would have produced a byte-identical result.

  > **A resolution defect is only detectable when the wrongly-read device differs in state from the correctly-read one.**

  That is a corpus-design requirement, not an observation about this round, and it belongs in **B-427**: a trial intended to exercise device resolution must be constructed so the two candidate devices are in *different* states. Otherwise the trial scores a pass on resolution while testing nothing about it — §0.12's vacuity, arriving in the evaluation corpus rather than in a test.

  **On the caveat itself, fairly.** It was hedged with an explicit "if so" and an explicit falsification condition, which is the correct way to record an uncertainty and is why it cost nothing. But the mechanism is worth naming: the next-hop address was **real, correctly read, and about a different thing** — a transit neighbour mistaken for a destination owner. That is silent-failure shape 6 (§0.13, OBS-071), occurring in a human hand diagnosis rather than in a log query. The shape is not a property of tools.
- **Needs human review:** no — but B-427 gains a requirement
- **Blocks:** none

---

## OBS-090 · round 2 · Two operator corrections, and what each one relocated

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Two corrections from round 2, both the operator's own, and both **relocate** a finding rather than fix it — which is §0.14's shape again: the original statements were true and filed under the wrong thing.

  **1. Shape 6 is not about sources.** It was first recorded from a log-platform instance (OBS-071) and framed as *"a source that filters what it delivers returns a different, complete-looking truth"*. True, and it reads as a data-pipeline concern. **The general form is inference from partial evidence.** Four instances now, two of them human, and one — OBS-089 — occurring inside a hand diagnosis written specifically to be an independent check on a tool.

  Relocating it changed what the fix is scoped to. `evidence-reduction.md` §7 now states plainly that **coverage metadata closes the source-side instance and no other**: it can state what a source could not have carried, and it cannot state that a correctly-read value is being asked the wrong question. A reader who took "coverage metadata solves shape 6" from that section would have taken the wrong lesson, and the section previously invited it.

  **2. The resolver finding is a corpus-design defect, not a system defect.** I had recorded it as an observation about round 2. It is a **binding requirement on B-427**, now stated verbatim in the item: *a trial tests resolution only if the wrongly-resolved device differs in observable state from the correctly-resolved one.* The difference matters because the first framing invites "we should watch for that" and the second forbids constructing a trial that cannot fail.
- **Evidence:** `BUILD-PLAN.md` §0.13; `evidence-reduction.md` §7; `chaos-harness.md` §6.1a; `BACKLOG.md` B-426/B-427.
- **What I did:** Recorded both, and two things that follow from them.

  **The hedging rule is now a protocol property**, in `chaos-harness.md` §6.1a rather than in a round writeup:

  > **A hand diagnosis may include an alternative reading only if it states what would refute it. An uncertainty with a falsification condition is evidence; the same uncertainty without one is a second opinion that arrives too late to be independent.**

  Round 2's caveat was wrong and cost nothing, because it named the condition that would refute it and that condition then fired. An unhedged version would have stood as a competing diagnosis with no stated way to settle it — and settling it after seeing the agent's output is precisely the judgement the blind protocol protects. The rule already applies on the agent's side (OBS-082's prediction lists what would falsify it); it is now symmetric.

  **The opposite-pressure observation is now the stated reason for the sequence**, in B-426 rather than as a remark:

  | | What the walk had to do |
  |---|---|
  | Round 1 | **descend past** a healthy rung to a broken one below |
  | Round 2 | **not descend into** three healthy rungs, and report a cause above them |

  A walk that stopped at the first broken rung passes round 2 and fails round 1. A walk that always reported the deepest evaluated rung passes round 1 and fails round 2. **Neither defect is distinguishable from correct behaviour by four rounds on one rung** — that is one data point sampled four times. Each round is now chosen for the *pressure it puts on the rule*, which is a sharper criterion than "different rungs" and happens to produce the same schedule.

  **Round 3 gains a second purpose.** The operator will run it on a device that has not been a subject yet, *partly to give the resolver something it could get wrong*. Recorded in B-426's round table, because it is the first round designed to satisfy B-427's new requirement rather than to hit a rung.
- **Needs human review:** no
- **Blocks:** none — round 3 next.

---

## OBS-091 · round 3 · The independent hand diagnosis, recorded before the agent run

- **Kind:** decision-made
- **Escalation:** NOTE
- **Model:** — (human operator)
- **What happened:** Round 3 of the manual injection sequence (track A). Subject `RR1 10.255.0.12`. Fault live, ground truth sealed. Recorded verbatim and committed **before** the agent runs, per the OBS-076 protocol.

  Recorded without comment or evaluation.

```
---- BEGIN INDEPENDENT DIAGNOSIS (round 3, recorded 16:16 UTC, before agent run) ----
Rung: bgp_session.  Finding: cause_not_localised.  Device: RR1.

Evidence:
  - rung 1 broken: 10.255.0.12 is Active on RR1, up/down 00:00:55
  - rung 3 HEALTHY: /32 via isis CORE, distance 115, metric 20, labeled SR, protected
    primary via Gi0/0/0/0 plus LFA backup, installed 08:23:01 — unchanged today
  - rung 4 HEALTHY: RR1 IS-IS to P1/P4 Up 2d22h; PE2 IS-IS to P1/P3 Up 08:23
  - rung 5 HEALTHY: PE2 physical interfaces all up/up. BV200 is down/down but is a
    bridge-group virtual interface, not a physical member — EACH_PHYSICAL_INTERFACE
    should exclude it. Flagging in case it does not.
  - PE2's own view: 10.255.0.31 is "Idle (Admin)". The neighbour is administratively
    shut on PE2's side.

Reasoning: the fault is in PE2's configuration, which is a layer the ladder has no rung
for. Everything the ladder can inspect below rung 1 is genuinely healthy — the path
exists, the IGP is up, the interfaces are up. Hence cause_not_localised.

Refutation condition: if rung 2 reports broken, the finding is transport_blocked. That
is a legitimate and arguably more literal outcome, since PE2 is not listening on 179
while administratively shut. If it lands that way, the finding is that this fault class
is indistinguishable from round 2's at the rung level — which is itself worth knowing
and should be scored as a corpus result, not as a diagnostic error.
---- END INDEPENDENT DIAGNOSIS ----
```

- **Evidence:** This entry, committed before the agent run. The agent's result is OBS-092.
- **What I did:** Recorded and committed, before running anything.

  **This diagnosis is the strongest use of the §6.1a hedging rule so far.** It does not merely name a refutation condition — it names the *alternative finding*, argues that the alternative is legitimate rather than an error, and specifies how to score it if it occurs. Both branches are settled in advance, so neither outcome requires a judgement made after seeing the agent's output.

  Two things it commits me to checking regardless of which way the round lands, both recorded here before the run so they cannot be selected after it: **whether `BV200` was excluded from `EACH_PHYSICAL_INTERFACE` and by what rule**, and **what `bgp_transport` actually tested** — since that predicate alone decides between the two candidate findings, and neither is wrong.
- **Needs human review:** no
- **Blocks:** none — OBS-092 is the agent run.

---

## OBS-092 · round 3 · The refutation condition fired — and the tool collected the answer without looking at it

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (descent) · MiniMax (report, correlation)
- **What happened:** Hand diagnosis committed `3598726` at **16:16:06 UTC**; agent ran at **16:16:17**.

  | | Hand (primary) | Hand (refutation branch) | Agent |
  |---|---|---|---|
  | Rung | `bgp_session` | `transport` | **`transport`** |
  | Finding | `cause_not_localised` | `transport_blocked` | **`transport_blocked`** |

  **The refutation branch fired, and it was specified in advance**, including how to score it: *"the finding is that this fault class is indistinguishable from round 2's at the rung level — a corpus result, not a diagnostic error."* Scored that way. This is §6.1a's hedging rule paying for itself a second time; nothing about the outcome required a judgement made after seeing the output.

  **It does not replace the composed fixture.** The round was intended to capture `cause_not_localised` from a real fabric. It did not produce it, so `report.cases.json`'s `cause_not_localised` case remains the build's only composed artefact — and §3 below explains why it may have to stay that way.

  | | |
  |---|---|
  | Wall clock | **118.8 s** (rounds 1–2: 114.2, 121.7; healthy baseline 103) |
  | Exit code | **1** |
  | Report | emitted, **grounded: 5 observations, 10 citations, 5/5 rungs cited**, all resolving |
  | Correlation | emitted |
  | Coverage | RR1, **200 of 896**, incomplete |
  | Repairs | none |
  | Character proxy | ~5k tokens of prompt, stable across all three rounds |
- **Evidence:** OBS-091 committed before the run. Both committed checks executed; results below.
- **What I did:** Ran the two checks I committed to before the round, and both found something. The second is the most valuable result of the three rounds so far.

  ### Check 1 — BV200 *was* excluded, and for the wrong reason

  PE2's interfaces, and what the rung kept:

  ```
  KEPT   : Gi0/0/0/0, Gi0/0/0/1, Gi0/0/0/2
  DROPPED: BV200, Lo0, Lo100, Nu0, Mg0/RP0/CPU0/0
  ```

  The rule is `name.startswith("Gi") and "." not in name` — **a naming filter, not a semantic one.** BV200 is excluded because it does not begin with `Gi`, not because anything in the system knows a bridge-group virtual interface is not a physical member. On this fabric every physical interface is a `GigabitEthernet`, so the rule is correct **by coincidence of naming**.

  Three consequences, none of which this fabric can show:

  * A physical interface named `TenGigE`, `HundredGigE`, `FortyGigE` or `Bundle-Ether` is **silently excluded from the rung that matters most.**
  * If that leaves the member set empty, `_aggregate` raises **`IndexError: list index out of range`** from `results[0].subject` — unhandled, not caught by `_cmd_investigate`'s `(ValueError, KeyError)`, so it surfaces as a traceback. Loud rather than silent, which is the right direction, but it is an unhandled crash in the emit path — the thing `test_a_malformed_report_is_a_grounding_failure_not_a_crash` exists to prevent one layer up.
  * The filter is **duplicated in three places** with two different definitions: `descent.py:166`, `investigation.py:312`, and `fixtures.py:152` (which uses `startswith("Gi") or name == "Lo0"`).

  Filed as **B-431**.

  ### Check 2 — `bgp_transport` reads one field, and the answer was in the next one

  `bgp_transport` reads `meta["connection_state"]` and nothing else. Here is what RR1's own `show bgp neighbor 10.255.0.12` actually parsed to:

  ```
  connection_state    : Active
  last_reset_reason   : BGP Notification received: administrative shutdown
  ```

  **The far end told RR1 exactly why the session is down, the parser captured it, and the check ignored it.**

  The operator's hand diagnosis reached "PE2 administratively shut the neighbour" by logging into PE2 and reading `Idle (Admin)` from the other side. **That was not necessary.** The evidence was on the local device, in a record the tool had already collected and parsed, in a field named `last_reset_reason`.

  This is not a wrong answer — `transport_blocked` is true, and the recommendation the model wrote from it is sound. It is a **wasted answer**: the descent had the material to say *"the peer administratively shut this session"* and said *"the transport is not established"* instead. Filed as **B-430**, and it is the strongest single argument yet for forward consistency (B-428), since "what does the upper rung say about *why*" is exactly the question that field answers.

  ### 3. `cause_not_localised` may be unreachable for this flow

  Falls out of check 2 and was not something I set out to test. Rung 1 reads the session state from `show bgp summary`; rung 2 reads `connection_state` from `show bgp neighbor`. **Two independent commands reporting the same FSM.** For `cause_not_localised` the walk needs rung 1 **broken** and every rung beneath it **healthy** — which here requires the summary table to say not-Established while the neighbour detail says Established.

  That is not a fault state. It is an inconsistent device.

  So one of the five findings `bgp_session` declares is, in practice, unreachable — and the golden case covering it is composed **because it has to be**, not because capturing it was inconvenient. The T-027 note said *"no consistently-behaving fabric can produce this"*; the operator corrected that to *"a fact about single faults"* (OBS-080). Both were close. The accurate version is narrower and more useful: **it is a fact about rung 2 restating rung 1's state machine.** A flow whose second rung tested something genuinely independent — reachability by probe, say — would reach it. Filed as **B-432**.
- **Needs human review:** **yes** — B-430 and B-432 bear on the flow's design, not just its implementation
- **Blocks:** none. Round 4 next, and its outcome is already predicted (OBS-082).

---

## OBS-093 · pattern · Shape 7 — the system held the answer and reported something weaker

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 · raised by the operator from OBS-092
- **What happened:** A seventh silent-failure shape, and the first with this polarity.

  **Every shape so far concerns what the evidence could not tell you. This one is the opposite: the evidence told you, and nothing listened.**

  > **Shape 7 — evidence collected, parsed, carried in the envelope, and never read.**

  The instance is round 3. `bgp_transport` reads `connection_state` and reports `transport_blocked`. The same parsed record, same envelope, same command, same device, also carried `last_reset_reason: "BGP Notification received: administrative shutdown"`. The far end had said why; the parser captured it; the check read the field beside it. **The independent diagnostician logged into the far device to learn what the local device had already reported.**
- **Evidence:** OBS-092. Audit measured below.
- **What I did:** Recorded it in §0.13, and turned the detection method into a measurement rather than an aspiration.

  **Why no existing mechanism can catch it.** Everything this build has constructed — §0.12's vacuity companions, §0.13's independent specification, grounding's citation gate, T-029a's absence coverage, T-029c's contradiction check — is aimed at output that claims **too much**. Shape 7 is output that claims **too little**, and it passes every one of them for the right reasons: nothing is fabricated, nothing is uncited, nothing is overstated. `transport_blocked` is *true*.

  > **It cannot be caught by grading the output, because the output is correct.**

  **The detection method is mechanizable, so I ran it.** Enumerate the fields each parser emits from a real fixture; grep the check module for each:

  | Template | Parsed | Read by any check |
  |---|---|---|
  | `bgp_neighbor` | 23 | **5** |
  | `interface` | 14 | **5** |
  | `route` | 8 | **2** |

  **The honest caveat first: 33 unread fields are not 33 defects.** `mac_address`, `bandwidth_kbps` and `description` are not diagnostic for these checks, and treating every unread field as a finding would reproduce precisely the noise-generating over-correction T-029c refused — a rule against unread evidence that generates unread warnings has defeated itself in the same way.

  **What is in the list anyway.** `bgp_neighbor`'s unread fields include `state_reason`, `previous_state`, `remote_as`, `hold_time`, `keepalive`. **An AS mismatch and a hold-timer mismatch both produce exactly the `Active` state round 3 produced.** The tool cannot currently distinguish either from an administrative shutdown — while parsing, carrying and discarding the fields that would. `interface` discards `last_link_flapped` and `state_transitions`, which are flap evidence; `route` discards `distance`, `metric` and `protocol`.

  So round 3 did not expose one wasted field. It exposed a **class**, and the class is measurable in twenty lines. Filed as **B-433**, to run as a test so it fails when a parser gains a field nothing reads.

  **One structural observation about where this shape comes from.** Parsers were built to §0.10 — *every non-blank line must be accounted for* — which is a completeness rule on **extraction**. Checks were built to their own predicate. Nothing ever connected the two. §0.10 guarantees the parser captures everything; **nothing guarantees anyone uses it.** The two disciplines meet nowhere, and shape 7 lives exactly in that gap. That is worth stating because it predicts where the next instance will be: any place a thorough extractor feeds a narrow consumer.
- **Needs human review:** no
- **Blocks:** none, but B-433 should precede any new flow — B-107's checks will be written against the same parsers.

---

## OBS-094 · round 4 · **The predicted failure occurred, exactly** — and the model caught what the descent could not

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (descent) · MiniMax (report, correlation)
- **What happened:** The true negative. One uplink shut on PE2, IGP reconverged over the survivor, **BGP session Established and carrying traffic throughout**. The correct answer is that nothing is wrong.

  No hand diagnosis this round — the operator had left. **OBS-082 serves instead, and is a stronger instrument: it was committed before the fault existed**, not merely before the run.

  | | OBS-082 predicted | Actual |
  |---|---|---|
  | rung 1 `bgp_session` | HEALTHY | **HEALTHY** — Established |
  | rung 2 `transport` | HEALTHY | **HEALTHY** |
  | rung 3 `route_to_peer` | HEALTHY | **HEALTHY** — 2 paths |
  | rung 4 `igp_adjacency` | HEALTHY | **HEALTHY** — 1 adjacency, all Up |
  | rung 5 `interface` | **BROKEN** | **BROKEN** — 2 of 3 members |
  | `finding` | `interface_line_down` | **`interface_line_down`** |
  | `cause` | `interface` | **`interface` on PE2** |
  | `causal_chain` | **empty** | **empty** |
  | exit code | **1** | **1** |

  **Nine of nine.** None of the three stated falsifiers fired.

  > **A fault reported, with exit code 1, on a BGP session that is Established and carrying traffic.**

  | | |
  |---|---|
  | Wall clock | **113.7 s** (rounds 1–3: 114.2, 121.7, 118.8) |
  | Report | emitted, grounded: 7 observations, 14 citations, **5/5 rungs cited**, all resolving |
  | Correlation | emitted |
  | Coverage | PE2, 200 of 701, incomplete |
  | Repairs | none |
- **Evidence:** OBS-082, committed 2026-08-16 before the fault was applied. Payload captured.
- **What I did:** Scored it, and then found the thing I was not looking for.

  ### The model did forward consistency, unprompted, and got it right

  From the same payload the descent produced, MiniMax wrote:

  > *"Because higher layers are healthy, the broken interface state observed here is **not on the dependency path** between RR1 and 10.255.0.12, and the descent did not reach a rung that would link this local interface…"*

  and recommended:

  > *"**Clarify the intended scope of the investigation:** the descent did not localise a cause on the dependency path to 10.255.0.12 (RR1's BGP session is Established with valid transport, route, and IGP)."*

  **That is B-428.** The model performed the check the descent lacks — comparing the cause against what the upper rungs actually show — and reached the correct conclusion in prose, from an empty `causal_chain` beside five healthy-above-broken rungs.

  Three things follow, and the third is the one that matters.

  **1. The information was sufficient.** B-428 is not blocked on collecting anything. The empty chain plus the rung table is enough to detect the condition, and something reading only that reached the right answer on its first attempt. The fix is a predicate, not an evidence-gathering exercise.

  **2. It is shape 7 with the polarity reversed.** Round 3 was *the system held the answer and reported something weaker*. Here the system held the answer, **the model read it**, and the deterministic layer that owns the verdict did not.

  **3. The prose is correct and everything a machine consumes is wrong.** `finding: interface_line_down`. `cause: interface on PE2`. `trustworthy: true`. **Exit code 1.** A human reading the report is correctly warned; a cron job reading the exit code pages for a healthy session. The model's caveat lives in exactly the layer this architecture deliberately treats as non-authoritative — and it is right, and it does not count.

  **This sharpens MVP0-REVIEW §5's warning rather than softening it.** *"Do not wire this to anything that pages on exit 1 until B-428 is settled"* was written from an offline construction. It is now measured against a live fabric, and the failure is louder than predicted: the system does not merely fail to conclude health, it **asserts a fault** and marks the assertion trustworthy.
- **Needs human review:** **yes** — B-428 is now demonstrated on live hardware
- **Blocks:** the four rounds are complete. Scoring in OBS-095.

---

## OBS-095 · B-427 · The four rows, and whether agreement is a pattern

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The four manual rounds are complete. These are B-427's first four rows, scored on the columns the round-3 review added: rung match, **how** it matched, and whether the answer was as strong as the evidence supported.

  | # | Fault | Predicted | Actual | Rung | Via | Answer ≥ evidence | Diagnosis correct |
  |---|---|---|---|---|---|---|---|
  | 1 | IS-IS shut on PE3's uplinks | `igp_adjacency` | `igp_adjacency` | ✅ | primary | ✅ | ✅ |
  | 2 | transport block, RR1↔PE1 | `transport` | `transport` | ✅ | primary | ✅ | ✅ |
  | 3 | BGP admin-shut on PE2 | `cause_not_localised` / *`transport_blocked`* | `transport_blocked` | ✅ | **refutation branch** | ❌ **weaker** | ⚠️ correct, impoverished |
  | 4 | one uplink shut, IGP absorbs | `interface`, empty chain, exit 1 | `interface`, empty chain, exit 1 | ✅ | primary *(predicted failure)* | n/a | ❌ **wrong** |

  Four distinct rungs. Four distinct outcomes. No round repeated another's pressure on the walk rule.
- **Evidence:** OBS-076/077, OBS-087/088, OBS-091/092, OBS-082/094. Every prediction committed to git before its run.
- **What I did:** Two scores, and they must not be combined.

  ### Prediction accuracy: 4 of 4. Diagnostic accuracy: 3 of 4.

  **Do not report 75%.** The denominator hides the structure:

  | Class | Rounds | Correct |
  |---|---|---|
  | A fault exists **on the dependency path** | 1, 2, 3 | **3 / 3** |
  | No fault on the dependency path | 4 | **0 / 1**, and structurally 0 / *n* until B-428 |

  Round 4 is not a miss that better luck avoids. It is a **class failure**: every true negative under redundancy produces the same false positive, by construction. Averaging it with the other three produces a number that is wrong in both directions — it understates the fault-localisation result and wildly overstates the health result.

  ### Is agreement a pattern?

  **On fault localisation, provisionally yes — and this is the strongest form the evidence can take at n=3.** Three rounds, three different rungs, and rounds 1 and 2 exercised the walk rule in *opposite directions*: one required descending past a healthy rung, the other required not descending into three. A walk defective in either direction fails one of them. Both passed.

  **Three caveats that keep it provisional.**

  - **Selection effect.** The rounds were designed by someone who knows the ladder, to land on specific rungs. A fault chosen without reference to it might not land on a rung at all — and nothing here measures that.
  - **Every fault was single.** Q-019 is untouched; the two-fault case remains unmeasured and the rung tables are byte-identical to the single-fault case.
  - **Round 3's match was via the refutation branch**, and the answer was weaker than the evidence supported. Counting it as a plain match is the mistake the new column exists to prevent.

  **On concluding health, no — and no amount of additional rounds will change it.** That is B-428, now demonstrated live.

  ### The uncomfortable observation

  **We predicted the tool's behaviour more accurately than the tool diagnosed the network — 4 of 4 against 3 of 4.**

  That is the right direction and not a comfortable one. It means the *understanding* of the failure modes is ahead of the implementation, which is to say **the backlog is currently a more accurate model of this system than the code is.** Round 4 is the clearest case: the defect was constructed offline, written down with its falsifiers, and reproduced exactly on live hardware weeks of reasoning before anyone fixed it.

  The practical consequence for track A: **B-428 is no longer a hypothesis to validate.** It is a measured defect with a demonstrated reproduction and a known-sufficient information source. It should be implemented, not investigated.
- **Needs human review:** **yes** — this is the pattern assessment the four rounds were run to produce
- **Blocks:** nothing started. Stopping here as instructed.

---

## OBS-096 · B-428 · **HALT** — clause 2 of the specification is unreachable except where it would clobber `cause_not_localised`

- **Kind:** assumption-wrong
- **Escalation:** **HALT**
- **Model:** opus-5
- **What happened:** B-428 was specified by the operator as three clauses, with an explicit instruction: *"If anything about this specification turns out to be wrong when it meets the code, HALT and record it. Do not improvise a different rule."* **Clause 2 is wrong, and I stopped rather than implementing around it.**

  **Clause 1** — *if rung 1 is HEALTHY, emit `no_fault_on_path`, exit 0* — is correct and is exactly round 4's defect. Verified:

  ```
  rung 1 healthy, rung 5 broken -> cause=interface  chain=[]  finding=interface_line_down
  ```

  **Clause 2** — *if rung 1 is BROKEN and the causal chain is empty, that is contradictory; emit `undetermined`* — matches exactly one reachable state, and that state is not contradictory:

  ```
  rung 1 broken, rungs 2-5 healthy -> cause=bgp_session  chain=[]  finding=cause_not_localised
  ```

  **An empty causal chain when the cause *is* rung 1 is necessary, not contradictory.** `causal_chain` is defined as the broken rungs **above** the cause. Rung 1 is the top of the ladder, so when it is the cause there is nothing above it by construction. The chain is empty because it must be.

  And the state clause 2 appears to be reaching for — rung 1 broken, cause *below* rung 1, chain empty — **is unreachable**. If the cause is below rung 1 and rung 1 is broken, rung 1 is itself in the chain:

  ```
  [B,H,H,H,B] -> cause=interface  chain=['bgp_session']
  [B,B,H,H,H] -> cause=transport  chain=['bgp_session']
  ```

  So clause 2's only reachable instantiation is `cause_not_localised`, and applying it would **replace a correct, well-defined, golden-tested finding with `undetermined`** — turning "the descent confirmed the symptom and found nothing beneath it to explain it" into "the descent could not read something", which is a different and false claim.
- **Evidence:** Constructed against the real `bgp_session` ladder via `_finding_for` and `DescentResult.causal_chain`, offline, no devices. `descent.py:318` is the branch clause 2 would override.
- **What I did:** **Nothing to the code.** No clause implemented, including clause 1.

  That is deliberate and I want the reasoning on record, because implementing clause 1 alone was tempting and I judged it wrong. The two clauses are mutually exclusive conditions, so they *look* separable — but they are one change to one function and one finding set, and `no_fault_on_path`'s place in the closed finding set is decided alongside whatever clause 2 becomes. **Applying a HALT selectively, on my own judgement that the remainder is safe, is the exact erosion `chaos-harness.md` §3.1 describes**: every argument for proceeding would be locally reasonable, and the value of the rule is that it does not bend to locally reasonable arguments. The operator's instruction anticipated this case by name.

  **Two resolutions, neither chosen.**

  **(i) Drop clause 2.** Its only reachable case is already handled correctly. Clause 1 plus "every other case unchanged" is then the whole rule, and it is small.

  **(ii) Keep a clause-2-shaped guard, scoped to the genuinely impossible state** — cause below rung 1 with an empty chain — as a defensive assertion that should never fire. That is a *different rule* from the one specified, which is why I am not writing it.

  **One interaction worth flagging before the decision is made.** B-432 records that `cause_not_localised` may be unreachable in practice for `bgp_session`, because rung 2 restates rung 1's state machine. If clause 2 was intended as a step toward **retiring** `cause_not_localised`, that is a coherent position — but it resolves B-432, which the same instruction said not to do. If it was not intended that way, clause 2 and B-432 are pulling in opposite directions on the same finding and should be decided together.
- **Needs human review:** **yes — this is a HALT**
- **Blocks:** **B-428, and therefore track A.** Nothing else was started.

---

## OBS-097 · B-428 · `no_fault_on_path` — the review's sharpest warning no longer holds

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** B-428 implemented, as clause 1 alone. Clause 2 was dropped after the HALT at OBS-096 — the operator confirmed the reading and took resolution (i), explicitly declining the never-firing-assertion variant: *"a never-firing assertion encodes a belief about the state space, and my belief about this state space was wrong an hour ago."*

  > After a descent completes and before a finding is emitted: **if rung 1 is healthy, no cause below it explains anything, because there is no symptom to explain.** Emit `no_fault_on_path` with the broken rungs recorded as observations rather than as a cause. Exit 0, not 1. Every other case unchanged.

  Nine lines of predicate in `_finding_for`, reached only when something is broken — the all-healthy case returns above it — so it fires exactly on *"broken, but not on the path"*.

  | Vector | Before | After |
  |---|---|---|
  | round 1 `B B B B H` | `igp_isolated` | unchanged |
  | round 2 `B B H H H` | `transport_blocked` | unchanged |
  | round 3 `B B H H H` | `transport_blocked` | unchanged |
  | **round 4 `H H H H B`** | `interface_line_down`, **exit 1** | **`no_fault_on_path`, exit 0** |
  | captured `broken` `B B B B B` | `interface_line_down` | unchanged |
  | `cause_not_localised` `B H H H H` | `cause_not_localised` | unchanged |

  **1391 pass**, lint clean. No model call anywhere in it; `ALL_HEALTHY` aggregation untouched; four frozen files still byte-identical.
- **Evidence:** `tests/test_rounds_regression.py` — 13 tests pinning all four rounds plus three corpus shapes as executable vectors.
- **What I did:** Four things worth recording.

  **1. A failing test that was the change working.** `test_a_healthy_rung_does_not_stop_the_walk_either` used a rung-1-healthy ladder and asserted `finding == "finding_2"`. Its *stated* property — the walk visits every rung — is asserted by `rung_path` and is untouched; the finding assertion encoded the old behaviour. OBS-083's rule, applied directly: **a change that tightens a rule fails exactly the tests that encoded the old rule as correct, and those failures are findings, not breakage.**

  **2. And the companion that failure would otherwise have deleted silently.** Converting that test to expect `no_fault_on_path` leaves *"descend past a healthy rung to a broken one below and name it"* — the property round 1 depends on — with **no coverage at all**, because its only test had just been repurposed. Added `test_a_healthy_rung_does_not_stop_the_walk_when_there_IS_a_symptom` (`B H B`).

  Worth flagging as a general hazard, because it is not one of the seven shapes: **fixing a defect can silently delete coverage of the behaviour that was always correct, and the deletion looks like a routine test update.** The tell is a test whose *inputs* had to change rather than its expectations.

  **3. "Observations rather than as a cause" is two requirements, and having only the first is worse than the bug.** Suppressing the cause without reporting the broken rung produces a run that silently drops a real interface fault — the opposite mistake, and quieter. `off_path` carries them, all three renderers show them, and a test asserts every renderer does. **`no_fault_on_path` must never read as "nothing found".**

  **4. The review's most important line is corrected, and narrowed rather than deleted.** MVP0-REVIEW §5 said *"it cannot tell you nothing is wrong"*. It can now say **"no fault on the path between these two endpoints, and here is what else is broken"**. It still cannot say *"this device is healthy"* — that was never the question — so §5 now reads **"read exit 0 as *not on this path*, not as *all clear*"**. Deleting the warning outright would have replaced a true limitation with an implied capability that does not exist.

  **What this validates about the process rather than the code.** B-428 was constructed offline, predicted with falsifiers before the fault existed, reproduced exactly on live hardware, and fixed in nine lines. OBS-095 called the backlog *"a more accurate model of this system than the code is"* — this is the first item to close that gap, and closing it was cheap **precisely because the diagnosis had been done properly first.**
- **Needs human review:** no
- **Blocks:** none. Stopping here — B-430, B-431, B-432, tracks B and C, and MVP-1 all untouched.

  ### Amended 2026-08-17, after round 5 — clause 1 is also wrong

  **Recorded at the operator's instruction, and in their words: B-428 clause 1 is my specification being wrong for the second time.** Clause 2 was withdrawn before it shipped (Q-020). Clause 1 shipped, was golden-tested, was the finding I was most confident about in the whole build, and round 5 measured it producing **exit 0 on a blackholing path for fifty seconds** (OBS-109).

  The qualification, confirmed by the operator:

  > **Rung-1-healthy is right when the broken rungs are off-path, and wrong when they are on-path and the symptom has not propagated.**

  What makes this worth more than a correction: the *evidence* for clause 1 was sound. Round 4 really did produce a false `interface_line_down` on a healthy session, and the predicate really does fix that case. Nothing about the observation was wrong. The rule inferred from it was too broad, and it was too broad in a direction the observation could not show — round 4 was a **settled** fabric, and the failure mode is a **converging** one. §0.13's first face: the evidence never bounded the conclusion, and every test written afterwards sampled the same settled condition the original observation came from.

  That is silent-failure **shape 4** — a rule generalised from one instance — with the additional twist that the instance was real, the fix was correct for it, and the over-generalisation was invisible until the protocol was changed to observe a state it had always excluded. Which is §0.15: the settled-fabric protocol was tightened for rigour, and what it excluded was the only condition that could have falsified clause 1.

---

## OBS-098 · principles · Four judgements from B-428, promoted out of the task writeup

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 · promoted at the operator's direction
- **What happened:** Four judgements made while implementing B-428 are recorded as standing rules rather than as notes about one task. Each is filed where it will be read by someone who has never seen B-428.

  ### 1. Shape 8 — a fix silently deletes coverage of behaviour that was always correct

  `BUILD-PLAN.md` §0.13, its own entry. **Not the tests face**, and filing it there would lose what makes it findable: in the tests face the test was *wrong*, encoding the same premise as the code. Here the original test was **right** — it covered real behaviour and would have caught a real regression — and a correct fix repurposed it out of existence.

  The suite went 1377 green → 1378 green. Nothing was ever red.

  > **The tell: a test whose *inputs* had to change, rather than its expectations.**

  An expectation changing is the normal shape of a fix — same scenario, different answer. **Inputs changing means the scenario moved**, and the scenario that left is uncovered unless someone notices. It produces the same end state as every other shape in that list by a route none of them take: §0.12's vacuity companions do not fire because nothing is empty, and §0.13's independent specification does not fire because the specification is satisfied. **Only the diff catches it**, and only if read with that question in mind.

  ### 2. Corpus integrity, binding on B-427

  > *A corpus records what the system did at the time, not what it does now. Scores are never rewritten after a fix. Corrections are appended as new rows with the fix referenced.*

  Made binding rather than left as a decision about round 4, and the reason is when the pressure arrives: **the urge to tidy a corpus is strongest exactly when a fix has just landed and the old score reads as an embarrassment rather than as evidence.** A rule that has to be re-argued at that moment is a rule that loses.

  ### 3. Deleting a stated limitation asserts a capability

  `BUILD-PLAN.md` §0.14, as a corollary. B-428 made *"it cannot tell you nothing is wrong"* false, and the tempting edit was to delete the sentence.

  **Deleting it is not neutral.** A review that once named a limitation and no longer does reads as saying the limitation is gone — a *different* claim from the one the fix supports. B-428 lets the tool say *"no fault on the path between these two endpoints"*; it does not let it say *"this device is healthy"*, which was never the question a flow asks.

  > **An absence of stated limits is itself a claim, and it is the one kind nobody reviews, because there is no sentence to review.**

  So §5 was narrowed to *"read exit 0 as **not on this path**, not as **all clear**"* — shorter than the original warning, and still a warning.

  ### 4. A compound requirement where half is worse than none

  *"Broken rungs recorded as observations rather than as a cause"* is **two** requirements: suppress the cause, **and** report the rungs. Implementing only the first passes any test that checks `cause is None`, and produces a run that **silently drops a real interface fault** — quieter than the false positive it replaced, and therefore worse.

  > **When a requirement is phrased as *"X rather than Y"*, it contains two obligations. Satisfying only the negative one is often worse than satisfying neither, because the failure it produces is silent where the original was loud.**

  Guarded by `off_path` in the payload, rendering in all three formats, and a test asserting every renderer shows the broken rung. `no_fault_on_path` must never read as *"nothing found"*.
- **Evidence:** `BUILD-PLAN.md` §0.13 (shape 8), §0.14 (the corollary); `BACKLOG.md` B-427; `MVP0-REVIEW.md` §5.
- **What I did:** Recorded all four, and noticed what they have in common — which is worth one sentence, because it is not what I expected.

  **Three of the four are about what happens *after* a defect is fixed**, not about finding one. Shape 8 is a fix removing coverage. Corpus integrity is a fix tempting a rewrite of the record. The narrowing rule is a fix tempting the deletion of a warning. Only the fourth is about reading a specification.

  The build's rules up to §0.14 are almost entirely about **detection** — how a green thing verifies nothing, how evidence bounds conclusion, how a claim gets misfiled. These three are about the moment immediately after detection succeeds, which until now had no rules at all and is where the pressure to tidy is highest.
- **Needs human review:** no
- **Blocks:** none. Nothing further started.

---

## OBS-099 · B-411 · **HALT** — the item describes a mechanism netmiko does not have

- **Kind:** assumption-wrong
- **Escalation:** **HALT** (item skipped, session continues per the operator's standing answer)
- **Model:** opus-5
- **What happened:** B-411 says *"`_netmiko_send_commands` returns whatever arrived when a per-command `read_timeout` expires, with no error recorded"*, and proposes fixing it in the transport path. **Measured, that is not what happens**, and both layers the item calls blind are not.

  **netmiko 4.7's `read_timeout` is an absolute wall-clock budget that raises.** From `BaseConnection.send_command`:

  ```
  while time.time() - start_time < read_timeout:
      ...
  else:  # nobreak
      raise ReadTimeout(msg)
  ```

  Not a quiet-period timeout, and expiry does not return partial output.

  **Measured with a fake netmiko whose `send_command` raises:**

  ```
  outputs: {}
  errors : ["traceroute 10.0.0.1: Pattern not detected '[#\\$]'"]
  ```

  Already a structured error, already no output. The transport path the item targets is correct.

  **And the parse layer catches the residual case too.** Feeding a truncated capture — the 8-byte shape the item measured — to both active-probe parsers:

  ```
  ping        -> parse_status = 'failed'
  traceroute  -> parse_status = 'failed'
  ```

  So a prematurely-returned command reaches `require_parsed`, fails, and becomes **`unevaluated`** — which is the correct verdict and the one the whole `unevaluated` discipline exists to produce.
- **Evidence:** `netmiko 4.7.0` source read directly; both behaviours reproduced offline with a fake transport and the real parsers.
- **What I did:** **Halted the item and implemented nothing**, per the standing instruction. Writing a transport-layer truncation guard would have been a fix for a defect that is not there, over code that already handles the case correctly — and it would have looked like progress.

  **The observation behind B-411 was real; the diagnosis was not.** An 8-byte `traceroute` fixture file with a clean success genuinely existed (OBS-043). Given the above, the mechanism cannot have been a read timeout. The likely one is a **premature prompt match** — netmiko returning normally because it saw the prompt pattern in partial output, which is fast rather than slow and so invisible to any timeout-based check.

  **Where that still bites is capture, not investigation.** `nettools capture` gates on `capture["errors"]` and **not** on parse status, so a prematurely-returned command writes a truncated fixture and exits 0. Investigation is safe — the parser fails and the rung goes `unevaluated` — but a *fixture corpus* can silently acquire a truncated file, and every test built on it inherits the truncation.

  **Recommended re-scope**, for the operator rather than decided here: B-411 becomes *"`nettools capture` does not gate on parse status"*, in the capture path, with the check being `parse_status is PARSE_OK` before a fixture is written. That is a different file, a different failure, and a much smaller change than the one filed.

  Worth noting what caught this: **the item was written from an observed symptom and a plausible mechanism, and the mechanism was never checked against the transport.** §0.14 — a symptom filed as a diagnosis. The measurement took four minutes and the fix it would have prompted would have been permanent.
- **Needs human review:** **yes — HALT, and the item needs re-scoping before anyone works it**
- **Blocks:** B-411 only. Session continues.

---

## OBS-100 · B-432 · The independent signal was already in the output, filed as an ignore rule

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** B-432 offered two options: **(A)** make rung 2 test something rung 1 cannot see — a TCP probe, which is an **active probe with its own allowlist class** and therefore a change to the safety boundary — or **(B)** collapse the flow to four rungs. The operator chose A, with `show tcp brief` named as a passive middle ground worth evaluating first.

  **Neither was needed.** Before adding a command I read what `show bgp neighbor` already emits, and the TCP-layer signal was there:

  ```
  healthy : Socket not armed for io, armed for read, armed for write
  broken  : Socket not armed for io, not armed for read, not armed for write
  ```

  **It was already being parsed — as two declared `IgnoreRule`s**, labelled *"socket bookkeeping"* and *"the down-session variant of the armed-for-read line above"*. §0.10's discipline had captured it, named it, and recorded a decision not to read it.

  So option A was implemented with **no new command, no new template, no `VERB_ALLOWLIST` change, and no fixture capture** — the corpus already contained the evidence on every device and every label.
- **Evidence:** Measured across every committed `show bgp neighbor` fixture: **armed on 14 of 14 Established sessions, not armed on 2 of 2 Idle ones.** `test_the_socket_field_discriminates_across_the_whole_corpus` pins it. `1442 passed`; the four round vectors are byte-identical.
- **What I did:** Four things worth recording.

  **1. `cause_not_localised` is reachable now, and the composed fixture is no longer structurally composed.** Rung 1 reads the FSM from `show bgp summary`; rung 2 reads the socket, which is a different subsystem. Rung 1 broken with a live TCP transport beneath it is an ordinary fault — an AS mismatch, a capability mismatch, an MD5 failure *after* TCP establishes. The golden case stays composed only because no such fault has been captured, which is a gap in the corpus rather than an impossibility. Annotated in place, and it is now a candidate for a future injection round.

  **2. The honest limit on the independence claim.** Socket-armed correlates *perfectly* with Established across the corpus. That is not evidence they are the same field — it is evidence the corpus contains no fault that separates them, which is precisely the gap this change opens. Stated in the code comment rather than left for someone to infer a stronger claim than the data supports.

  **3. `state_reason` came along, and it points down the ladder.** `BGP state = Idle (No route to multi-hop neighbor)` — the device naming the layer beneath it. Unlike `last_reset_reason` this is *current*, so it is stated without a staleness caveat. Two of `bgp_neighbor`'s explanatory fields are now read; four remain.

  **4. A second population for shape 7, which the B-433 audit does not see.** The audit compares *parsed* fields against *read* fields. This signal was in neither: it was an **ignored line**. Every `IgnoreRule` in `template_parsers.py` is a documented decision not to extract something, and that set has never been reviewed for diagnostic value.

  > **§0.10's ignore rules are shape 7's other reservoir. The audit measures what was parsed and discarded; it cannot see what was never parsed.**

  Filed as **B-434**. Worth noting the mechanism worked exactly as designed even so — the rule was *declared and reviewable*, which is why re-reading it took one grep. An undeclared regex would have swallowed the line invisibly.
- **Needs human review:** no — the operator chose option A and this is option A, reached more cheaply
- **Blocks:** none

---

## OBS-101 · B-403 · Closed as **won't do** — the two modules answer different questions, and merging makes one worse

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** B-403 proposed consolidating `checks.py` and `health.py`, filed with the caveat *"merging is a later decision with real risk to a large passing suite"*. Examined and **closed without implementing**. The risk is not the reason; the reason is that consolidation makes something worse whichever direction it goes.

  | | `checks.py` | `health.py` |
  |---|---|---|
  | Question | is *this rung* broken for *this subject* | is *this device* healthy given its role |
  | Unit | one object — a peer, an interface, a prefix | one device, rolled up |
  | Verdict | `healthy` / `broken` / `unevaluated`, unordered | severity `ok < info < warning < critical` |
  | Inputs | **parsed records only** — no I/O, no inventory, no clock | parsed records **plus `inventory/lab.yaml`'s `expected:` blocks** |
  | Count | 5 predicates | 10 rules across 3 tables |

  **Three of ten health rules overlap three of five checks** (`isis_isolated`, `bgp_session_down`, `interface_admin_up_line_down`). Everything else does not, and the non-overlapping parts are the ones that resist merging:

  * **Baseline rules need the inventory.** `checks.py`'s docstring states its purity as an invariant — *"no I/O, no device access, no inventory reads, no clock, no environment"* — and gives the reason: it is what makes every rung of a descent reproducible from a fixture. Absorbing baseline drift would break that, and the property it buys is load-bearing for the whole offline corpus.
  * **`suspicious_baseline` has no rung.** It is a rule *about the baseline*, not about a device or an object. There is nowhere in a ladder for it to live.
  * **Severity has no meaning per-subject**, and the tri-state has no ordering. Neither vocabulary survives translation into the other.

  So the two directions are: `checks` absorbs `health` and loses fixture reproducibility, or `health` absorbs `checks` and loses either the device roll-up or the severity ordering. **Both trade a working property for tidiness.**
- **Evidence:** `tests/test_checks_agree_with_health.py`, 92 passed. Its own docstring already recorded the decision: *"The LLD keeps them separate on purpose."*
- **What I did:** Closed it, and checked the two things that would have changed the answer.

  **1. Is the duplication actually guarded?** Yes, and thoroughly. The agreement test compares every overlapping pairing across **all four labels** — `t0`, `t1`, `healthy`, `broken` — on the rule that neither may say `healthy` where the other says `broken`. It carries its own anti-vacuity companion (`test_the_agreement_comparison_is_not_vacuous`), which exists because the first version of that test **did** pass vacuously. The risk consolidation would remove is already removed by something cheaper.

  **2. Is there a narrower win — having the three overlapping health rules delegate to `checks`?** No. Health rules take a `RuleContext` of pre-extracted per-device records; checks take an evidence dict keyed by convention (`bgp_neighbor:<peer>`). The shapes differ enough that the adapter would be more code than it deletes, and it would put an inventory-free module in the import path of one that is not.

  **The general point, which is why this is worth a finding rather than a one-line closure.** Two modules that overlap in *three of fifteen* rules are not duplicated — **they are two views of one evidence set, and the overlap is where the views happen to coincide.** Merging on the strength of an overlap that size optimises for the appearance of tidiness over two properties that are each doing work. The right response to a partial overlap is a test that pins the agreement, which is what exists.

  > **Duplication guarded by a test that compares the duplicates is not duplication worth removing. It is a redundancy with a witness.**
- **Needs human review:** **yes** — this closes an item rather than implementing it, and the operator asked for all three killed
- **Blocks:** none

---

## OBS-102 · B-421 · A faithful implementation of a specification that was wrong

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (spec, review) · sonnet-5 (implementation)
- **What happened:** First delegated item of the session, and the first acceptance review. **The implementation was faithful and the specification was wrong.**

  My spec said: *"reorder so all static text precedes all volatile text — and the reordering must not change what the model is being asked."* Sonnet did exactly that, mechanically and correctly. The result:

  ```
  COVERAGE
  --------
  What this source was able to tell us, measured by code:
                              <- the payload that was here is now at the end
  **Read `gaps` before you conclude anything negative.** ...
  ```

  with the volatile half being **three anonymous JSON documents** separated by blank lines. `correlate` substitutes three payloads — coverage, finding, window — and their only remaining distinguisher was internal shape.

  Every character of the prompt still existed. Nothing was reworded. It satisfied the letter of *"must not change what the model is being asked"* and violated it entirely.

  > **The association between a heading and its payload is content.** Text-preservation is not meaning-preservation, and a check for one reads exactly like a check for the other.
- **Evidence:** Rendered both prompts and read them. `1464 passed`. Two tests pin the corrected property, including the cost of the correction (<100 uncached characters).
- **What I did:** Merged the agent's work first, then corrected on top as a separate commit, so both the faithful implementation and the specification defect are visible in history rather than one silently absorbing the other.

  The fix: each payload carries its section heading into the volatile half. A few dozen tokens of static text now live on the uncached side. **That is the right trade every time** — B-421 is explicitly *cost, not correctness*, and a cheaper prompt that says something slightly different is not the thing being optimised.

  **Three things about the delegation itself, since this is the first one.**

  **1. The spec's own words were the trap.** *"Must not change what the model is being asked"* is unfalsifiable as written — it invites checking that the text survived, which it did. A spec that had said *"each payload must remain identifiable as the section it belongs to"* would have been checkable and would have produced the right implementation first time.

  **2. The agent flagged it and I nearly missed it.** Its report mentioned *"the orphaned 'DESCENT RESULT' heading"* in passing, as an observation about mechanics rather than a concern. It had seen the thing; the framing carried no alarm, and a faster review would have read past it. **A subordinate reporting a fact neutrally is not the same as a subordinate raising a problem**, and the reviewer owns the difference.

  **3. Acceptance had to be judged by looking at the artefact, not the tests.** 1462 tests passed, including eleven the agent added, one of which asserts *"system + user retains every GRACE slot and the payload"* — true, and blind to the defect, because concatenating the halves restores an adjacency the model never sees. §0.13's tests face, arriving through a delegation: **the tests encoded the same premise as the specification.**
- **Needs human review:** no
- **Blocks:** none

---

## OBS-103 · B-404 · The fabric's LLDP never contradicted itself — three devices were wearing different names

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 · surfaced by sonnet-5 during B-404
- **What happened:** The Sonnet agent doing B-404's line accounting flagged something it called *"striking"* rather than a defect: three `t0` fixtures capture `show running-config hostname` answering with a name that is not the device's inventory name.

  ```
  P1  -> hostname LEAF05_DHCP_SERVER
  P3  -> hostname Lab-leaf01
  PE4 -> hostname SDWAN-Edge01
  ```

  **That resolves a fabric anomaly this build has treated as unresolvable since Phase 3.** `CLAUDE.md` states, and `topology.py`'s anomaly report is built on, the claim that *"this fabric's own LLDP data is self-contradictory: P1 reports its Gi0/0/0/0 facing P2's Gi0/0/0/0, while P2 reports that same port facing `LEAF05_DHCP_SERVER` instead."*

  Read directly:

  ```
  P1 Gi0/0/0/0 neighbour -> P2
  P2 Gi0/0/0/0 neighbour -> LEAF05_DHCP_SERVER
  P1's own configured hostname at t0 -> LEAF05_DHCP_SERVER
  ```

  **P1 and P2 agree exactly.** Both describe one link: `P1(=LEAF05_DHCP_SERVER) Gi0/0/0/0 ↔ P2 Gi0/0/0/0`. LLDP was correct at both ends throughout. The disagreement was never within LLDP — it was **between LLDP's device-reported names and the inventory's labels**, and nothing in the tool held the mapping between them.

  The same explains the second anomaly class. `Lab-leaf01`, `LEAF05_DHCP_SERVER` and `SDWAN-Edge01` were reported as *"LLDP neighbours that are not in this inventory at all"*. They are P3, P1 and PE4, under the names those boxes were configured with. All three were aligned to their inventory labels by the `healthy`/`broken` captures, so the anomaly is historical and specific to `t0`/`t1`.
- **Evidence:** `show running-config hostname` and `show lldp neighbors` across the committed fixtures, read directly.
- **What I did:** Corrected `CLAUDE.md`, the B-402 note I had seeded **one hour earlier** stating the contradiction as fact, and the test asserting it. Filed the code change as **B-435** rather than making it minutes before a push — `topology.py` should compare LLDP device IDs against each device's *configured hostname* and stop reporting three known devices as unknown.

  **Three things worth keeping.**

  **1. This is silent-failure shape 6, and it survived a year of documentation.** The evidence — `LEAF05_DHCP_SERVER` in P2's LLDP table — was real, correctly parsed, and correctly reported. It answered *"what name does the neighbour advertise"*. It was read as answering *"which inventory device is on this port"*. Real evidence, right value, **wrong question**, and every consistency check it faced it passed, because there was nothing inconsistent to find.

  **2. I propagated it into a fresh artefact while it was still wrong.** The B-402 note went in an hour before this, presented as one of *"the facts this build proved"*. It was one of the facts this build **assumed**, restated with more confidence each time it was repeated — CLAUDE.md, `topology.py`'s design, MVP0-REVIEW, then an operator note. **A fact's provenance does not improve by being cited**, and the `revisit_when` field I added to that very schema is the mechanism that would have caught it, had the note carried an honest one.

  **3. It came from line-by-line accounting, not from anyone looking for it.** §0.10 exists to make parsers complete. Its by-product is that somebody has to *read every line* of real output, and a human — or an agent — reading every line notices a hostname that does not match the folder it came from. That is the second time the accounting discipline has produced a finding outside parsing (the first being B-432's socket line). **A rule that forces exhaustive reading pays out in things nobody was looking for.**
- **Needs human review:** **yes** — this contradicts a claim in `CLAUDE.md` and in the MVP-0 review
- **Blocks:** none. B-435 filed.

---

## OBS-104 · B-403 · Implemented on operator instruction — and it cost T-019's acceptance criterion

- **Kind:** decision-made
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** I closed B-403 as won't-do at OBS-101, arguing that consolidation makes one module worse whichever way it goes. **The operator reviewed that and directed the merge anyway.** Implemented: `health.py`'s 655 lines moved into `checks.py`, which is now 1,405 lines; `health.py` is a 69-line re-export shim so the nine importers keep working. **1689 pass, lint clean, the descent works end to end.**

  **What it cost, measured rather than argued.**

  ```
  checks.py now imports, from the previously-forbidden set:
      inventory_model, network_tools
  ```

  Those two names were in a **declared forbidden list** in `test_checks_imports_nothing_that_can_touch_a_device`, which is **T-019's stated acceptance criterion**. Not a test somebody wrote — a criterion in the build plan, pinned. The merge makes it false, and it failed on the first run, which is the design saying exactly what I had predicted at OBS-101.

  **This is more than the trade I put to the operator.** My question offered *"you lose either fixture-reproducibility or the severity ordering"*. It did not say *"this falsifies a completed task's acceptance criterion"*, because I had not yet found the test that pins it. A reasonable person could answer that question differently knowing the larger version. Recorded here so the choice can be revisited with the full cost visible; the merge is one `git revert` away.
- **Evidence:** `1689 passed`. Import-graph check run directly. Offline demo verified end to end.
- **What I did:** Implemented it, replaced the criterion with the strongest thing that survives, and did not quietly delete anything.

  **The replacement, and what it is not.** The old test asserted over `checks.py`'s *import graph* — a claim about what is **possible**. It could not be violated at runtime, because the capability was not in the module. The new tests run each of the five rung predicates against real parsed evidence with `load_inventory_file`, `socket` and `open` replaced by bombs — a claim about what **actually happens**.

  > **An invariant you cannot violate became one you are told about.**

  A predicate that grows an inventory read now fails a test. Before, nobody could have written one. That is strictly less, it is the honest maximum after the merge, and it is stated in the test's own docstring rather than left for a reader to work out.

  **The one thing genuinely gained.** `_is_numeric` existed twice, and the documented copy said so in as many words: *"Deliberately re-declared rather than imported from `health.py` — the two rule sets coexist on purpose and neither should have to import the other to share one three-line predicate."* That duplication is gone. It is three lines.

  **What I would say if asked again.** The merge works and nothing is broken. The exchange rate was three duplicated lines for a structural guarantee that the descent's predicates cannot reach a device — and `descent.py` now sits, through one import, on top of the SSH, credential and metrics layer. §0.14: the item was filed as *"consolidation"*, which sounds like a tidying decision, and is a decision about a dependency boundary.
- **Needs human review:** **yes** — the full cost was not visible when the instruction was given
- **Blocks:** none

---

## OBS-105 · peer review · **HALT** — §5 defers the mechanism that makes §3.6's third dataset real

- **Kind:** assumption-wrong
- **Escalation:** **HALT** (scoped to item 8 / B-442; other items proceed)
- **Model:** opus-5
- **What happened:** The operator asked me to check whether any of §5's ten deferrals is load-bearing for items 1–8 in a way the response missed. **One is.**

  §3.6 accepts C's three-dataset split and defines the third as the **one-shot audit** set: *"inaccessible to developers, evaluated once on a frozen release, then spent."* Item 8 (B-442) is to write that into `chaos-harness.md`.

  §5 defers **"Frozen-release audit governance — until the harness matures."**

  Governance is not an accessory to that dataset; **it is the entire content of the property that distinguishes it.** A development set and a one-shot audit set contain the same kind of faults and are run by the same harness. The only difference between them is a set of rules about who may see the contents, who may run it, and what happens after it is spent. Defer the rules and the third dataset is a development set with a label.

  **§3.6 makes this concrete against its own deferral.** It records that of C's eight leakage routes, **two apply today**: the same tool-aware person defining both catalogues, and *"local Git history being treated as an immutable seal when it can be amended or rebased."* So the document identifies live leakage, then defers the work that addresses it until a future maturity, while item 8 specifies the artefact that leakage would compromise.

  That is §0.14 exactly — the shape the operator predicted when asking. Governance was classified as *maturity work* when for this dataset it is *definitional work*.
- **Evidence:** `peer-review-response.md` §3.6 and §5, read against each other.
- **What I did:** Halted item 8's scope and recorded it, per the standing instruction; items 1–7 are unaffected and proceed. **Nothing about item 8 is blocked except writing the one-shot set as though it were sealed.**

  **The narrow fix, for the operator to accept or reject rather than for me to take:** item 8 writes the development and regression sets in full, and writes the one-shot audit set as **specified but not yet established**, with its two known-live leakage routes named in the document. That is honest and costs nothing. Promoting B-452 out of §5 is the alternative, and it is more work than item 8 itself.

  **One thing I have to record against myself, because C's second leakage route is aimed at me.** *"Local Git history treated as an immutable seal when it can be amended or rebased."* The blind-trial protocol I have relied on all session (OBS-076, OBS-087, OBS-091) rests on exactly that: *"the ordering becomes a fact in git history rather than a claim in a document."* Local git history is amendable, and **I used `git commit --amend` during this session**, on a documentation commit. The ordering claim for rounds 1–4 was weaker than I stated it to be for as long as the branch existed only locally.

  It is stronger now, and for a reason that had nothing to do with the protocol: the branch was pushed to a remote, after which a rewrite is detectable by anyone who fetched. **The seal became real when it stopped being local** — which was an accident of the operator asking me to push, not a property of the method. The protocol should say *push before the run*, not *commit before the run*.
- **Needs human review:** **yes — this is a HALT on item 8's scope, and a correction to a protocol I have used four times**
- **Blocks:** B-442 as specified. Items 1–7 unaffected.

  **Resolved 2026-08-17 — HALT accepted, narrow fix taken.** Item 8 writes development and regression in full, and the one-shot set **as specified but not established**, naming its two live leakage routes. B-452 stays deferred. The protocol correction was taken separately and immediately (OBS-107).

  The operator recorded the misclassification against the review response itself: *"my deferral table classified governance as tooling when it is the definition — that is §0.14 in the review response itself."* Filed as a fifth row in BUILD-PLAN §0.14, which also gained a check the first four did not need: **a deferral — is the deferred thing the subject's support, or is it the subject?** A document written to apply §0.14 committed §0.14 while doing so, which is the more useful half of this finding: the section is a list of shapes, not a list of closed cases.

---

## OBS-106 · B-438 · The model-visible surface had three writes, not one

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Item 1 of the review's order of work. Reviewer A named `pin_lab_golden_snapshot`: a persistent write behind a decorator called `_read_only_tool`, letting a model pin an outage state as golden so drift comparison suppresses that fault indefinitely. Contradicts D12 and D14.

  **There were three.** `save_lab_snapshot` writes, and `_diff_against` — shared by `diff_lab_device_against_latest` and `diff_lab_device_against_golden` — saved the fresh collection on every call. That last one is the one nobody named, and it is the one a model would hit most: **snapshot history is what `detect_lab_flaps` reads**, so a model diffing in a loop reshapes the evidence a later flap analysis sees. Not ground truth, but the same category.
- **Evidence:** `mcp_server/server.py` imports no write function; three tests, `1692 passed`.
- **What I did:** Removed both writing tools, made the diff tools non-persisting, and **deleted the write imports from the module entirely**.

  That last part is the fix. Removing two functions leaves the next person free to add a third, and `_read_only_tool` would decorate it just as willingly. **A module that cannot import a write function cannot expose one** — the same containment argument as `prompt_library` never holding device text (OBS-061) and `GroundingFailure` having no field for a claim. A test asserts the import graph, not the decorator.

  Two things worth recording beyond the fix.

  **The decorator was the defect, not the symptom.** `_read_only_tool` was introduced to attach `read_only_hint=True` for MCP clients — an accurate *hint* about intent. Read as a *guarantee* it was false for three of twenty tools, and nothing checked. **A name that states a property is a claim, and every other claim in this build is required to carry a check.** This one was exempt because it looked like plumbing.

  **The diff tools' behaviour genuinely changed, and it is better here.** The CLI still saves on diff, deliberately — a human's diff advances the baseline. The MCP surface now compares against a **stable** baseline instead of a moving one, which for a read-only surface is the more defensible semantics rather than a compromise. Worth stating because a reviewer of this change would otherwise read a lost feature; the feature moved to where the writer is a person.
- **Needs human review:** no
- **Blocks:** none. Items 3 and 4 next, and the design goes to the operator first.

  **Operator confirmation, 2026-08-17.** `_diff_against` named as the most important of the three, for the reason given above — it is the one a model hits most and snapshot history feeds flap detection. Removing the imports rather than the functions confirmed as the right shape, and placed explicitly in the same family as `build_report_prompt` never receiving device text. The `_read_only_tool` reading recorded as canonical: **an accurate hint read as a guarantee, false for three of twenty tools, checked by nothing.**

---

## OBS-107 · chaos-harness · The blind protocol's seal was trust, not mechanism

- **Kind:** method-defect
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** §6.1 step 6 said the agent's answer is *committed* before the reveal, and §6.1a said the hand diagnosis is *committed* before the agent runs. Both were written as though a commit were a seal.

  **It is not.** `git commit --amend` rewrites it and `git rebase` reorders it, and both leave a history that reads exactly as though the original ordering held. The guarantee the protocol needs is that the record became unalterable **by the person being tested** before the answer was known. Only a push does that.

  This is reviewer C's second live leakage route (§3.6) — *local Git history treated as an immutable seal.* C aimed it at the fault catalogue. It lands with more force on the diagnosis, because the diagnosis **is** the protocol's central guarantee.
- **Evidence:** `git commit --amend` was used during the same session that ran the rounds — not on a diagnosis, but its availability is the point. Round 1's timestamps (14:11:50 diagnosis, 14:12:02 run) are consistent with the ordering claimed.
- **What I did:** Amended `chaos-harness.md` §6.1 step 6 and §6.1a to **push**, not commit, on operator instruction. Recorded that **rounds 1–4 used the weaker form and their sealing rests on trust rather than mechanism.**

  Two things worth keeping separate, because collapsing them is the temptation.

  **This does not invalidate rounds 1–4.** The ordering claimed is the ordering that happened. What changed is who has to take that on faith.

  **But it should be stated rather than assumed, and the reason is not politeness.** A protocol that provides trust while asserting mechanism has a defect that is invisible from inside it: everyone involved knows the ordering was honest, so nobody notices the seal was never tested. It is only visible to a reader who was not there — which is precisely the reader a blind protocol exists to convince. **The failure mode of a seal is not that it breaks; it is that nobody ever tries it.**

  Generalises past this build: any "recorded before" claim should name the mechanism that makes it checkable by someone who does not trust the recorder. If there is no such mechanism, the claim is a testimony, and testimony is fine as long as it is labelled.
- **Needs human review:** no
- **Blocks:** nothing. Round 5 (B-451) is the first to use the stronger form.

---

## OBS-108 · B-436 · Everyone attributed 114 seconds to collection; nobody measured the split

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Reviewer B's §3.4: *"114–122 seconds is too slow for an interactive command."* §2.1 attributed the duration to per-rung collection, §6 credited item 3 with fixing it, and my own `evidence-epoch.md` opened with *"one change, three defects."*

  After item 3 landed I measured it live on `RR1 → 10.255.0.12`, and the first reading looked spectacular: **6.0 s**, against the recorded 114–122 s. A 19× speedup.

  **It was not a like-for-like comparison and I nearly reported it as one.** The 114 s runs made **two model calls**; my 6 s run was `--no-model`. Measured properly, both without a model:

  | | Before | After |
  |---|---:|---:|
  | Deterministic descent | **8.5 s** | **5.8 s** |
  | Commands | 40 | 24 |

  A full `investigate` with one model call: **40.5 s**. Round 1's 114.2 s carried two, the second over a 13,218-character correlate prompt.

  **So the epoch saves 2.7 seconds of about 114.** B's observation is right and B's attribution is wrong, and the response document, the order of work and my design all inherited the attribution without checking it.
- **Evidence:** Live, this fabric, both paths in one process with the same credentials: `epoch 5.8s / per-rung 8.5s`, both `all_layers_healthy`. `nettools investigate --format json` end to end: 40.5 s, report withheld, correlation `not_attempted`. Fixture replay: 24 commands against 40.
- **What I did:** Corrected `peer-review-response.md` §3.4 and the header of `evidence-epoch.md`; removed the speed claim from both. Item 3 keeps the two claims it can support — temporal coherence, and 40% fewer commands against A's §4.6 device-load concern.

  **Three things worth keeping.**

  **The 19× reading is silent-failure shape 6 with me as the subject.** Wrong evidence read as right evidence: the number was real, the run was real, and it answered a different question from the one I was asking. What caught it was not scepticism about the number — it was that the number was *too good*, which is a much weaker instrument than it feels like and would not have fired at 3×.

  **A claim that three independent reviewers, a response document and an implementation design all repeat is not thereby evidenced.** It is one unmeasured claim with four citations. The convergence in §2.1 was real and load-bearing for the *correctness* half; the speed attribution rode along on it, and consensus is what made it invisible. This is §0.13's first face — the evidence never bounded the conclusion, because nobody produced any.

  **It relocates a deferred item.** B-439, deterministic authoritative report rendering, is first in §5's deferred table as a *grounding* improvement. It is now the only live answer to §3.4, because it removes the model from the interactive path. A deferral's cost changes when something else is measured, and nothing re-reads the deferral table when that happens.
- **Needs human review:** no — the correction is made; B-439's promotion is the operator's call
- **Blocks:** nothing. Round 5 proceeds.

---

## OBS-109 · B-451 · Round 5: the coherence check fired on a real transition, and exit 0 was wrong for 50 seconds

- **Kind:** validation
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The first round invoked *during* convergence rather than after it. Fault applied 09:44:42 (`router isis CORE` shut on both PE2 uplinks, one commit), 13 probes over 340 s, restored and verified by reading the device. Full table in `ROUND-5.md` §7.

  **Two results, and the second is the important one.**

  **The mechanism works.** Probe 09 at T+117 s read rung 1 **healthy** when the epoch opened and **broken** when it was re-read 38 s later — `stable: false`, `agrees: false`, refused. That is item 3 catching a real state change on a live fabric on its first attempt, and it is the first time in this build the coherence check has fired on anything but a fixture.

  **And it does not help with the thing that matters more.** Probes 01–07 — **seven consecutive samples across 50 s** — reported `no_fault_on_path`, **exit 0**, `trustworthy: true`, with the coherence check **passing**. From probe 03 the rungs read `..XX.`: RR1 had no route to `10.255.0.12` and PE2's IS-IS was down. Traffic was blackholing and the tool said there was no fault on the path.
- **Evidence:** `docs/build/ROUND-5.md` §7; payloads archived under `evidence-archive/round5/20260817-094426/`. Prediction §4 pushed at `3f588cb`, re-sealed at `9cfad7d`, both before the fault.
- **What I did:** Filed **B-454** and **B-455**. Scored every prediction, including the two that failed.

  **The defect, stated precisely.** B-428 says *rung 1 healthy → nothing below it can be a cause*. That is right when the broken rungs are **off the path** — round 4's redundant uplink with the IGP reconverged. It is wrong when they are **on the path and the symptom has not propagated yet**. BGP's hold timer is 180 s, so for most of that window the top of the ladder reads Established while everything under it is already gone. `no_fault_on_path` conflates the two and nothing in the descent distinguishes them. That is item 7's business, and it qualifies the finding I have been most confident about all build.

  **Temporal coherence and causal correctness are independent, and this round separates them experimentally rather than by argument.** Probes 01–07 are *coherent and wrong*: rung 1 genuinely was healthy at both reads, so the observations really do describe one state — a state the tool then misinterprets. Item 3 could not have caught this and was never going to. Worth stating because "we fixed temporal coherence" reads like a general soundness improvement, and it is a narrow one.

  **A prediction failure that was mine, not the tool's.** I predicted phase C would report `interface_line_down`. It could not: the harness applies `router isis CORE / interface … / shutdown`, which disables IS-IS *on* the ports and leaves them up, while my own §1 wrote the fault as "shut `Gi0/0/0/0` and `Gi0/0/0/1`". The interface rung read healthy in all 13 probes, correctly. Had the bound not masked phase C's finding, **I would have scored a correct `igp_isolated` as a prediction failure** — a falsifier written against a misdescribed setup fires on the truth. §0.13's fifth face, with the injector script as the part I did not read closely enough.

  **The unpredicted finding (B-454).** `temporally_incoherent` conflates *the fabric moved* with *collection was slow*, and in this round the second destroyed a correct answer: probes 10/11/99 saw a settled broken fabric whose right finding was `igp_isolated`, and the bound replaced it with a refusal four minutes after the fabric stopped changing. The build already has the right pattern — `COVERAGE_LIMITED` keeps a real answer and labels it — and I did not apply it to the epoch. A width breach should qualify a finding; only instability should refuse one.

  **What this round does not establish.** One trial, one fault, one fabric. Nothing here is a rate. And the skew jump from 4 s to 38 s at T+64 s is a **correlation with no mechanism** — the per-observation timings that would diagnose it exist on `Observation` and are discarded by `to_payload()`, which is shape 7 inside code written three hours ago to fix a different problem.
- **Needs human review:** no — but B-454 and the B-428 qualification are design calls for the operator
- **Blocks:** item 7 (B-437) should now be scoped against this result rather than in the abstract.

---

## OBS-110 · B-455 · One session per device, except where a rung fans out

- **Kind:** improvement
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** B-455 established that skew is `(sessions − 1) × an ~8 s device-side login penalty`, so session count is the only term the tool controls. `collect_evidence_and_templates` runs a device's intents **and** its rendered templates over one login.

  **The result is 3 sessions, not 2, and the reason is worth more than the saving.** RR1 collects in one pass. PE2 cannot: its `interface` rung fans out over `EACH_PHYSICAL_INTERFACE`, and the member list lives *inside* the `interfaces` intent — so the manifest is not known until the first pass returns. That device costs two logins and no amount of batching removes it.

  | | Sessions | Skew, penalty armed |
  |---|---:|---:|
  | Before the epoch | 10 | — |
  | Epoch, per-template logins | 7 | 61–75 s |
  | Batched templates | 4 | 34–38 s (round 5) |
  | Combined runner | **3** | **25–31 s** |
- **Evidence:** Live, penalty deliberately armed first so the measurement is of the condition round 5 actually hit. 1728 passing, four frozen files byte-identical.
- **What I did:** Added `collect_evidence_and_templates` and a private transport that **authorizes nothing** — every command reaching it has already passed its own rule. Two rules, kept separate and neither loosened: intents through `is_approved` against the exact-match frozenset, templates through `render_command` + `is_safe_rendered_command`. Both before `get_device`. Three tests, two of them with the credential environment explicitly cleared.

  **The design risk a combined runner creates, and what was done about it.** The temptation is one validation path for two kinds of command, because that is simpler and reads better. It is also how a rendered template command ends up admitted to the static allowlist, or how an intent stops being exact-matched. So the transport function is **private, authorizes nothing, and takes `platform` as a required keyword** — there is no path into it that skips a check because there is no public path into it at all. Structural containment, the same argument as `prompt_library` never holding device text.

  **One refusal refuses the whole batch.** Partially collecting after refusing part of it would make "the allowlist refused something" a condition a caller could miss while holding plausible-looking evidence — the exact failure a refusal exists to prevent, arriving by a different route.

  **This connects B-456 to something it was not filed for.** Removing PE2's probe pass needs the member set to be knowable without reading the device first, and the operator's B-456 candidate says the route output already names its outgoing interface. So B-456 is not only about whether `no_fault_on_path` is rare — **it is also the remaining session, and therefore the remaining ~8 s.** A correctness candidate and a performance fix turn out to be the same change, which is not how either was filed.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-111 · B-458 · Invariant 4 was true of the code and false of the surface

- **Kind:** defect-found
- **Escalation:** **HALT** (accepted; fixed on instruction)
- **Model:** opus-5
- **What happened:** Before connecting LM Studio, the operator asked for invariant 4 — *no unparsed device text ever reaches a model* — to be audited at the MCP boundary, checking **actual return shapes, not intent**, and to treat any finding as a HALT.

  **14 of 20 tools returned raw device text** under `data.commands`. Measured:

  | Tool | Chars |
  |---|---:|
  | `get_lab_logging` (count=200) | **37,962** |
  | `get_lab_bgp_neighbor` | 6,139 |
  | `check_lab_fabric` (9 devices, live) | 4,239 |
  | `collect_lab_evidence` | 2,629 |
  | `get_lab_interface` | 1,403 |
  | eight others | 29–985 each |

  `get_lab_logging` is the one that matters: 38 kB of unshaped device log buffer — precisely the input `log_window.py` exists to filter by attribution and `coverage.py` exists to bound. The `investigate` path shapes it to a few hundred records with a coverage record attached; the MCP path handed a model the whole thing.
- **Evidence:** Measured per tool against committed fixtures, and live for `check_lab_fabric` which needs credentials. 21 tools now smoke-tested against the live lab through a real `ClientSession` (`tests/test_mcp_live_lab.py`, 24 tests, 6m51s).
- **What I did:** Reported and halted before changing anything, per instruction. Then, on the operator's direction: `mcp_server/boundary.py`, applied by the **registration decorator** so a tool is sanitised by the act of being registered.

  **The finding worth keeping is not the leak.** Nothing was broken in the library. Every one of those envelopes also carried `data.parsed`, and every internal consumer read the parsed half and ignored the text. The code was correct and had been correct for eight phases.

  > **An invariant that holds for every internal caller is not an invariant. It is a convention that has not yet met a new consumer.**

  This is OBS-106's shape at larger scale — there, `_read_only_tool` was an accurate *hint* read as a *guarantee*, false for 3 of 20 tools. Here the guarantee was real, enforced structurally in `prompt_library` (OBS-061), and simply did not extend to a second path to a model that was built at a time when no model was on the other end of it. **A structural guarantee protects the path it is built into and no other**, and nothing enumerated the paths.

  **Two corrections against myself during the fix**, both from measuring rather than reasoning:

  *My audit understated the leak.* I probed `parsed["unaccounted_lines"]` and measured zero. The key lives at `parsed["meta"]["unaccounted_lines"]`, one level deeper — the test caught it, not me. §0.10's remainder is raw device lines by the parser contract, empty across this corpus and non-empty the first time another platform is parsed.

  *And it overstated it.* `unparsed_rows` reads like `unaccounted_lines`'s sibling, is named like one, and is documented beside it — and is an **`int`**. Stripping it would have destroyed a diagnostic for no safety gain. **A rule written from a name walks into that; a rule written from a measured type does not.**

  **One vacuous check found on the way.** `test_mcp_readme_lists_exactly_the_exposed_tools` filtered by a hand-maintained prefix allowlist, so `investigate_lab_session` was added and the doc-sync test **passed** — silently not covering the newest tool, which is the one most likely to be undocumented. Now derived from the registry, like the boundary sweep. Same lesson twice in one hour, which is the argument for the lesson.
- **Needs human review:** no — reported and directed before implementation
- **Blocks:** nothing. The known residual is bounded rather than claimed clean: a transport exception can embed device output in its message, so error strings are truncated at 400 characters (B-458's note).

---

## OBS-112 · MCP experiment · The tool description is the control surface for selection

- **Kind:** prediction-refuted
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (recording an operator result)
- **What happened:** **The operator's sealed prediction about the 21-tool surface is refuted.** Asked *"why is the BGP session on PE2 down?"*, `gemma-4-e4b` selected `investigate_lab_session`, and its reasoning trace named the description as the reason: *"preferred when the question is 'why is this broken?'"*.

  Twenty tools say some form of **"collect read-only X"**. One says **what it achieves and when to prefer it**. The model read the difference and acted on it.
- **Evidence:** Operator-run, LM Studio over SSH stdio, `gemma-4-e4b`, one question, reasoning trace quoted above. Recorded as reported; the prediction itself was held outside this repo and is not quoted here rather than paraphrased into something it may not have said.
- **What I did:** Recorded, and reframed **B-113**.

  **The finding: a tool's description is not documentation, it is the selection mechanism.** It is the only part of a tool a model reads before deciding, and the only part it can reason about. Twenty descriptions written as inventory entries and one written as an affordance produced exactly the selection the affordance described.

  **What this does to B-113.** Consolidation was filed as the fix for D10/D11's failure mode — a manifest that grows with the catalogue. The measurement says **the count was not the binding constraint; the wording was.** A smaller manifest is still worth having, but the work that changes selection is writing each surviving description in terms of *what question it answers and when to prefer it* — and that work pays off whether or not consolidation ever happens.

  **The order matters, and getting it wrong would have been self-concealing.** Consolidate first and the descriptions get rewritten as a side effect of merging; selection improves; consolidation is credited with a fix that the rewording made. Do the wording first and the two effects are separable — and it is the cheaper half.

  **What this does not establish.** One model, one question, one occasion. It shows a description *can* drive selection, not that this description reliably does, and certainly not that a larger surface is safe. It is the right kind of evidence for retiring an assumption and the wrong kind for asserting a property — which is the same distinction the four rounds kept running into.
- **Needs human review:** no
- **Blocks:** nothing. B-113 reordered rather than dropped.

---

## OBS-113 · B-459 · Every containment mechanism guards the return; nothing guards the argument

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG (filed, not fixed)
- **Model:** opus-5 (recording an operator finding)
- **What happened:** The operator's second finding from the MCP experiment, and it names a boundary this build has never covered.

  **Everything protective here operates on what a tool returns.** The allowlist governs what may be *sent*; `render_command` governs how a parameter is *rendered*; `boundary.sanitize` governs what may be *returned*; grounding governs what a model may *claim*. **Nothing constrains what a model supplies as an argument.**

  The question *"why is the BGP session on PE2 down?"* contains no peer address. The model needed one and **asked** rather than inventing. That is the right behaviour and it is not a guarantee — it is one model on one occasion.
- **Evidence:** Operator-run, same session as OBS-112.
- **What I did:** Filed as **B-459** — *not* B-458 as instructed, because B-458 was taken an hour earlier by the error-string residual in the same MCP work. Flagged rather than renumbered, since a silently reused ID is worse than a visible correction.

  **Why a fabricated argument would have defeated the entire build.** Walk it through: `investigate_lab_session("PE2", "10.255.0.99")` for a peer that does not exist. `render_command` accepts it — it is a well-formed IPv4 address, and canonicalisation by reconstruction is about *syntax*, which is exactly right and exactly not this. The descent walks. Rung 1 reads `show bgp summary`, finds no such peer, and answers honestly. The report renders from real typed fields. Every citation resolves. Grounding passes. **The output is a fully grounded, correctly cited, deterministically derived investigation of a session that does not exist**, and there is no gate anywhere that asks whether the subject is real.

  Note what makes it invisible: **nothing malfunctions.** Every component does its job correctly on the input it was given. That is silent-failure shape 6 — wrong evidence read as right evidence — moved upstream of the evidence entirely, to the *question*.

  **The defence, and why its shape is already familiar.** Validate an argument naming a network object against that device's observed state **before executing**: a peer address in no BGP summary on that device is not a peer. This is **B-453 pointed the other way**. B-453 checks that every identifier in a model's *output* appears in the evidence; this checks that every identifier in a model's *input* appears in the device. Same mechanism, same canonicalisation table, opposite direction — which is a strong sign it is the right shape rather than an invention.

  **Two limits worth stating now so the item is not over-scoped later.** It catches an invented object, not a wrong one: a real peer address that is not the one the operator meant passes cleanly, exactly as B-453 catches an invented entity and not a wrong relation. And validating requires a read *before* the read, which on this fabric costs a session and an ~8 s login — so **where** the check runs is a design question, not a detail, and the epoch is the obvious place to put it since it already collects the device's state first.
- **Needs human review:** no — filed for the operator to schedule
- **Blocks:** nothing today. It is a precondition for trusting any model-driven entry point, which now includes the MCP surface.

---

## OBS-114 · MCP experiment · A model is not a reliable source about its own prior output

- **Kind:** observation
- **Escalation:** NOTE
- **Model:** opus-5 (recording an operator result)
- **What happened:** Asked about an earlier statement, the model said *"I did not explicitly say it was 'not established'"*. **It had, verbatim, one message earlier.**
- **Evidence:** Operator-observed, same session as OBS-112/113.
- **What I did:** Recorded. Small on its own, and it bears on a class of thing this build does in several places.

  **The rule: a model's account of its own prior output is not evidence about that output.** The transcript is. Anywhere a design has a model refer to, summarise, or reason about a previous turn, the referenced content has to be re-supplied from the record rather than recalled — because recall and generation are the same operation, and a plausible reconstruction of what it probably said is indistinguishable to the model from what it did say.

  Two places this already touches. The `agent_loop` accumulates its own prior turns and reasons over them, which is fine while the tool results are in the context verbatim and not fine if anything ever summarises them. And it is a caution on any future gate that asks a model to check its own earlier claim — a self-consistency check between a generated claim and a *recalled* claim measures nothing.

  Worth noting the shape rather than only the instance: this is the same failure as **shape 6** (wrong evidence read as right evidence) with the model's own memory as the wrong evidence, and it is invisible for the same reason — the recalled version is fluent, specific, and confidently wrong.
- **Needs human review:** no
- **Blocks:** nothing.

  **Corrected 2026-08-17 by the operator, and the correction is to the framing rather than the fact.**

  This was filed as a property of *models* — recall and generation are one operation, so a model checking what it said generates a fresh claim rather than retrieving an old one. **That mechanism is narrower than the failure it explains.**

  > **Anyone reasoning about a prior exchange without the record in front of them is reconstructing it, and a confident reconstruction is indistinguishable from a memory.**

  The operator demonstrated it twice in one session — once asserting a device misattribution that the payload contradicts (see the amendment to OBS-115), and once earlier — **after directing that this rule be written.** That is not an aside: a rule about reconstruction, written down and agreed, did not prevent its author from reconstructing an hour later. The rule is not a habit anyone acquires by knowing it, which is precisely why the remedy has to be mechanical: *supply the record*, never *recall the record*.

  So the practical form in `prompts/README.md` is right and its scope statement was wrong. It is not a rule about prompting a model. It is a rule about any claim whose subject is an earlier exchange, and the prompt case is one instance of it.

---

## OBS-115 · B-439 · The live case: a model restatement dropped a rung

- **Kind:** defect-observed
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (recording an operator result)
- **What happened:** **B-439 was justified by argument. It now has an observation.**

  **Corrected 2026-08-17 — see the amendment at the end of this entry. The device-misattribution half of this finding was false and has been withdrawn.**

  `investigate_lab_session` returned a deterministic report carrying **five rungs, each with the device it was evaluated against**. The model's **first** report listed all five, including `route_to_peer`, correctly. Its **recovery message**, restating the same result later in the conversation, **listed four — omitting `route_to_peer`**.

  No probing, no adversarial prompt. Nobody was testing for it.
- **Evidence:** Operator-observed, `gemma-4-e4b` over LM Studio, same session as OBS-112/113.
- **What I did:** Recorded against B-439.

  **The surviving error is the reviewers' failure in miniature.** Dropping `route_to_peer` removes a link from the causal chain, and the remaining four still read as a coherent explanation — of a path that was never checked. It is the *degradation across a restatement* that matters, not its size: the model had the correct five-rung answer and lost one of them while saying it again.

  **The distinction that makes this a stronger result than expected.** This was **not** the `paraphrase` field — the MCP tool produces none, by design. This was the chat model restating a *correct* deterministic report in ordinary conversation. So the degradation happened **downstream of every gate this build has**, on a surface we do not control and cannot instrument.

  Two consequences follow, and the second is the uncomfortable one:

  **B-439 is validated and its scope was too narrow.** Rendering the authoritative report deterministically was right, and marking a model paraphrase non-authoritative was right — but the marking only governs *our* paraphrase field. A chat client's own prose is a paraphrase nothing labels.

  **The defence available is the report's shape, not a gate.** If the authoritative report is structured so that dropping a rung or moving a device is *visibly* a deletion — an explicit per-rung device column, a stated rung count, a chain the reader can re-count — then a restatement that loses one is checkable against the tool output sitting directly above it. That is weaker than enforcement and it is what is available at a boundary we do not own. Currently `to_payload()` emits the rungs as a list with devices, which is most of the way there; what is missing is anything that makes the *count* explicit enough to notice a missing element.
- **Needs human review:** no
- **Blocks:** nothing.

  **Resolved 2026-08-17 — scope separated, design call taken.**

  The operator accepted the scope correction and directed that it be recorded as a **distinct boundary rather than an instance of B-439**, filed as **B-461**. The distinction is worth the extra item: B-439 governs *our* `paraphrase` field — produced here, graded here, marked here. B-461 is a surface with **no field to mark, no grounding hook and no instrumentation**. Collapsing them would hide which half is fixable.

  **The minimal design call, taken:** the report shape states `rungs_examined` and numbers every observation `1/5 … 5/5`, each naming its device; the payload carries `position`/`of` on every rung. A restatement listing four is then visibly short to a human reading both.

  **It is not enforcement and must not be written as though it is.** Nothing here prevents a chat client from dropping a rung. It makes the omission *detectable* at a boundary where nothing can be enforced — which is a real but strictly weaker thing, and the distinction is exactly the one this build keeps having to make between a structural guarantee and a convention.

  ### Amendment 2026-08-17 — the misattribution claim was false, and the error was the operator's

  **Withdrawn in full: the model did not misattribute any device. It read the payload correctly.**

  The MCP investigation was `device=PE2, subject=10.255.0.31`. `10.255.0.31` is **RR1's** router ID, so the subject device was RR1, `igp_adjacency` and `interface` resolved to **RR1**, and the payload said RR1 per rung. The descent was right and so was the model.

  The claim came from carrying the device mapping from round 2 — `RR1 → 10.255.0.12`, where the same two rungs resolve to **PE2**. Same fact, reversed direction, wrong investigation.

  **This is silent-failure shape 6, committed about a prior exchange, without the payload in front of the person making the claim** — which is precisely the failure OBS-114 describes, by the person who directed OBS-114 be written. The operator records it as the second time in this project they have asserted something about an earlier exchange that the record contradicts.

  Two things make it worth more than a correction.

  **It nearly became a test.** `test_every_observation_names_the_device_it_was_evaluated_against` asserted `igp_adjacency == "PE2"` — true for round 2's direction and false for the one being discussed. It passed, because it was written against the *wrong investigation*, and a passing test would have pinned the error as expected behaviour. That is **§0.13's tests face arriving through a specification rather than an implementation**: the code was never wrong, the test agreed with a mistaken description of it, and nothing in the suite could have told them apart. The test is now parameterised over both directions (`PE2 → RR1` and `RR1 → PE2`) and asserts *resolution*, not a device name; a falsification check confirms it fails when resolution is hardcoded.

  **OBS-114 generalises further than it was written.** It was recorded as a property of *models* — recall and generation being one operation. The mechanism is narrower than the failure: **anyone reasoning about a prior exchange without the record in front of them is reconstructing it**, and a confident reconstruction is indistinguishable from a memory. The rule that follows is the same one already written into `prompts/README.md`, and it is not model-specific: *a claim about earlier content must be checked against that content, supplied from the record.*

  **A smaller correction in the same direction.** The B-439 sub-item was **partly already satisfied** before it was requested — every rung already carried its own `device` field, in both the report and the payload. Only the count was missing. What landed is therefore narrower than it was specified as, and the entry above should not be read as though numbering introduced per-rung devices.

---

## OBS-116 · B-442 · The three datasets, and the one that is specified but does not exist

- **Kind:** design
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Item 8 of the peer review's order of work, the last one open. `chaos-harness.md` §3.5 specified two sets and §11 told you to re-run set B as a regression suite after every change — incompatible, as reviewer C pointed out: *"once its results influence a fix, model choice, parser or prompt, it is a regression set."*
- **Evidence:** `chaos-harness.md` §3.5, §5, §10, §11, §12 reconciled against each other.
- **What I did:** Wrote the three datasets under the narrow fix the operator accepted at OBS-105.

  **Development** and **regression** in full. The rule that makes regression meaningful is that **a case joins it the first time it fails**, with its expected outcome recorded at that moment — so the set is a record of defects actually met rather than a wish-list of faults someone thought worth covering. `test_rounds_regression.py` already works this way for the four rounds, which is why it was worth stating as the rule rather than inventing one.

  **The one-shot audit set is written as specified and explicitly not established**, with both live leakage routes named in the document rather than left to a reader who was present. Its specification was never the missing part: **governance is not an accessory to that set, it is the entire content of the property that distinguishes it.** All three sets hold the same kind of faults and run through the same harness; the only difference is who may see the contents, who may run it, and what happens after. Defer the rules and the third dataset is a development set with a label.

  **The reconciliation was larger than the section.** §11's phase 7 and phase 8 named the same set for validation and for regression — that *was* the incompatibility, sitting two sections away from where it was diagnosed. Phase 9 now exists and is blocked on B-452. §13's example claim previously offered a holdout figure as though it were available; it now states that only the second sentence estimates unseen performance and that the honest form stops after the first until the audit set exists.

  **On the sizing numbers.** They are recorded and placed **last**, because C's caveat is the operative sentence rather than a footnote: *sampling validity comes before sample size.* Running 59 trials against the development set produces a tight interval around a quantity nobody wants to know. Until the estimand is defined, this project has trials and no estimand — and 59 of them would not change that.

  **What closing this item does not do.** It does not give the harness a third dataset. It makes the absence of one visible in the document a reader will actually reach, instead of leaving a specification that reads as a description. That is the whole content of the narrow fix, and it is worth being plain that the review's final item closes by *documenting a gap* rather than filling one.
- **Needs human review:** no
- **Blocks:** nothing. **Every item in the peer review's order of work is now closed**, with B-440 (round 6) operator-scheduled and B-452 deferred by decision.

---

## OBS-117 · B-456 · The path-scoping fix would have checked the wrong device's interfaces

- **Kind:** defect-avoided
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** B-456's premise was *"the route output already names its outgoing interface, so the subset is knowable rather than inferred."* Measured: the first half is true and the conclusion does not follow.

  The route names the **local** device's egress. The interface rung is `SUBJECT`-scoped. For `RR1 → 10.255.0.12` on the healthy label:

  ```
  route on RR1 names:    GigabitEthernet0/0/0/0, GigabitEthernet0/0/0/1   (RR1's ports)
  interface rung checks: Gi0/0/0/0, Gi0/0/0/1, Gi0/0/0/2                  (PE2's ports)
  ```

  **And the naive implementation is actively wrong on this fabric, not merely useless.** Both devices have interfaces named `Gi0/0/0/0` and `Gi0/0/0/1`. Canonicalising through `interface_kind` — which is precisely what the item proposed, and the right thing to do within a device — makes them **match**. A path-scoping filter built that way would scope PE2's rung by RR1's interface names, produce a plausible member set, and be wrong about which router it was talking about.
- **Evidence:** Measured across the committed corpus: 85 route fixtures parsed, `interface` and `path_role` present on every path record. The RR1/PE2 name collision confirmed directly on the healthy label.
- **What I did:** Corrected the premise before implementing, and took the variant that works: read the route **on the subject device, back toward the local device's loopback**. `show route 10.255.0.31/32` on PE2 names PE2's own egress toward RR1. Same information, right device, and it batches into the subject's existing session so it costs no extra login.

  **Why this is worth a finding and not a design note.** It is **B-461's shape — a report naming the wrong device — arriving through the fix for a different problem.** Nobody would have caught it by reviewing the fix against its own goal: scoping the interface rung to the path is correct, `interface_kind` canonicalisation is correct, and the two composed produce a wrong answer that a uniformly-named fabric renders invisible.

  **It is the second time this session a fix has carried the failure it was adjacent to.** The first was the B-113 rewording nearly confounding its own experiment by leaving one description in a distinct form (§0.13's setup face, new route). This one is different in mechanism and identical in shape: *the repair inherits the defect class of the thing it repairs*, and in both cases the concealment came from something that looks like good practice — preserving evidence in the first, canonicalising names in the second.

  **The general check, which is cheap:** when a fix moves data between two scopes — devices, sessions, time windows — ask what identifier is being used to join them and whether that identifier is unique across the join. A name that is unique *within* a device is not a key *across* devices, and a naming convention that makes it look like one is a hazard rather than a convenience.
- **Needs human review:** no — premise correction accepted, three design decisions taken by the operator
- **Blocks:** nothing.

---

## OBS-118 · B-456 · The corpus cannot settle the LFA timing question, and the zero was vacuous

- **Kind:** measurement-refused
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Path scoping inherits a timing dependency: **can a down interface persist in the route table as an LFA backup before reconvergence completes?** If it can, the member set includes a port that is down but still named, and the rung reports a degradation that is really a stale route entry.

  The operator made this the gating item rather than a footnote, since round 5 established this ladder gets read mid-convergence.

  Searched every device and label: **70 routes with a path, zero naming a down interface.**

  **That zero is vacuous.** The corpus contains **no device with a down physical port and a surviving route** — the one label with down ports is `broken`, where PE2 is isolated and the route is `found=False`. Zero hits is what a corpus that cannot contain the case returns, and it is indistinguishable from zero hits in a corpus that could.
- **Evidence:** 70 routes examined across all devices and labels; 0 devices satisfying both conditions.
- **What I did:** Refused to conclude, per the operator's instruction not to assume either way, and applied §0.12's anti-vacuity discipline to a **measurement** rather than to a test.

  **This is the part worth keeping.** The first result — *"zero routes name a down interface"* — is a perfectly good sentence, it is true, and reporting it as evidence that down interfaces do not persist would have been wrong. The check that caught it is the same one §0.12 prescribes for guardrails, pointed at an analysis: *before believing a negative, ask whether the corpus could have produced a positive.*

  Anti-vacuity has been applied to tests throughout this build. This is the first time it has been applied to a **measurement**, and the failure mode is worse there: a vacuous test is a green tick nobody reads, while a vacuous measurement becomes a sentence in a document that a later decision rests on.

  **Shipped with the dependency recorded as open**, not assumed away. It is the subject of the next injection round — a fault that leaves a port down while a route survives, which is exactly the round-4 topology and therefore cheap to arrange.
- **Needs human review:** no — the operator pre-authorised this branch
- **Blocks:** nothing. **B-462** files the round.

---

## OBS-119 · B-456 · Path scoping needs two member sets and two aggregations, and it does not save a session

- **Kind:** implementation
- **Escalation:** DECIDE-AND-LOG (one HALT raised and resolved mid-work)
- **Model:** opus-5
- **What happened:** `SubjectRule.EACH_PATH_INTERFACE` landed. Three things the specification did not anticipate, all found by measurement.

  **1. Decision 3 was wrong, and so was my objection to correcting it.** The operator specified *unevaluated when the reverse route is absent, never fall back to all-interfaces*. Measured on the `broken` label, that produced `undetermined` — the isolated device has no reverse route *because* it is isolated, so the rung has no member set exactly when the interfaces are the answer. The operator's correction was that an absent reverse route is a **finding, not a failure to read**.

  Their predicted consequence was `igp_isolated`. Measured, marking rung 5 broken yields **`interface_line_down`** — rung 5 is the *lowest* rung, so its verdict decides the finding whenever it is broken; there is nothing below it to localise to. And `interface_line_down` is the right answer: PE2's two uplinks really are `admin-down`.

  **I also overstated a collision.** I reported that choosing the member set after the walk would violate `Flow`'s prewalk precondition. It does not: the switch is on *observed evidence* — did the reverse route resolve — not on an earlier rung's **verdict**. Both member sets are collectable up front and the precondition holds untouched.

  **2. The aggregation has to switch with the member set, and a first implementation missed it.** With the all-physical set under `ANY_HEALTHY`, PE2's one healthy port outvoted its two shut uplinks and the rung reported **healthy on a completely isolated device**. "Does a path survive" and "is any port down, explaining this isolation" are different questions and cannot share a combining rule. `_rung_subjects` now returns an aggregation override alongside the subjects.

  **3. It costs a session rather than saving one.** B-456 and B-455's last session were planned as the same change. They are not. The fan-out manifest cannot be built until the reverse route is read, so the subject device still needs two passes — the same structure it had before. A first implementation gave the route its own session and pushed skew from **25–31 s to 38–42 s**; batching it with the intents brought it back to **29–33 s**, roughly neutral.

  **B-455's floor is three sessions, not two**, and the reason is structural: on a fan-out device, what to collect second depends on what was read first.
- **Evidence:** Live, login penalty deliberately armed. Fixture replay across ten device/subject/label combinations, all correct. 1749 passing.
- **What I did:** Implemented all three corrections. Recorded the two claims of mine that measurement corrected, alongside the operator's.

  **A design flaw a test caught, worth its own paragraph.** The first version set `CollectStep(parameter="origin_prefix")`, overloading one field to mean both *which template argument* and *how to fill it*. It reads naturally and it is false — the `route` template takes `prefix`. `test_every_template_collect_step_names_a_real_parameter` failed on it. `CollectStep` now has a separate `fill` field with a declared vocabulary, which is the honest shape: the argument name is a fact about the template, the strategy is a fact about the collector.

  **Round 4 is not settled and the regression vector stays.** Under path scoping round 4's `[H,H,H,H,B]` should become `[H,H,H,H,H]` — the reverse route names only the surviving uplink. **That is reasoning, not measurement**: round 4 ran live and its payload was not archived, so it cannot be replayed. The vector is kept with the prediction written beside it, because deleting a true assertion about the finding logic on the strength of an untested prediction about the rung is the wrong trade, and because if the prediction holds what should follow is a recorded change rather than a quiet disappearance. Round 6 measures it.
- **Needs human review:** no — one HALT raised mid-work and resolved by the operator
- **Blocks:** B-462 (does a down port persist as an LFA backup) remains open and is the known soft spot.

---

## OBS-120 · B-460 · A field whose type depends on its value cannot be diffed

- **Kind:** defect-fixed
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** `show bgp summary`'s `St/PfxRcd` column holds **either** a prefix count **or** a session-state name, and the parser stored whichever appeared in one field. So `state_pfx_rcd == "0"` and `state_pfx_rcd == "Idle"` were the same field carrying different *kinds* of value.

  Filed initially as a naming improvement. The operator sharpened it, and the sharpening is what made it worth doing now: **the defect is live, in flap detection.** `diff_evidence` compares parsed records field by field, so a session going Established → Idle is recorded as one field changing value. `detect_flaps` reads those same diffs, so **a session bouncing Established/Idle was counted as a string oscillating, not as a session flapping** — and the flap detector exists precisely because a bouncing peer looks clean in every pairwise diff.

  It was detecting the right transitions for the wrong reason and under the wrong name, which means its output could not be filtered to "sessions that flapped" without re-deriving the discriminator the CLI discards.
- **Evidence:** Three `_is_numeric(state)` re-derivations in `checks.py`, one per consumer. Flap test now reports `session_state` with values `[Idle, Established, Idle, Established, Idle]`. 1751 passing.
- **What I did:** Split at the parser: `session_state` always present, `prefixes_received` **absent — not zero** — when the session is not Established.

  **The absence rule is the part that matters.** Emitting `prefixes_received: 0` for an Idle session would be a measurement nobody took, and it would have a consequence: `bgp_no_prefixes` fires on `prefixes_received == 0`, so every down session would report *"Established with 0 prefixes received"* alongside its real fault. Absence makes that structurally impossible rather than requiring the rule to remember. Same rule Phase 3 applies to `router_id`/`local_as` for a device with no BGP process.

  **The general form, which is why this is a finding and not a chore.** A field whose *type* depends on its value cannot be checked, diffed or compared without every consumer reconstructing the discriminator that was thrown away at parse time. Three consumers did exactly that, identically, and agreed — which is why nothing looked wrong. **Agreement among re-derivations is not the same as not needing to re-derive**, and the place to split is the parser, because it is the last point at which the discriminator is still observable.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-121 · B-437 · The criterion audited: one gap, and it is the one B-432 predicted

- **Kind:** audit
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator asked for B-437 to be **measured before building to its filed spec**, on the grounds that B-456 may have shrunk it, and to close it at its real size rather than pad it.

  Measured across every rung vector this project has — three from the committed fixtures, four from the injection rounds, one composed:

  | Boundary | Separated by |
  |---|---|
  | `bgp_session` \| `transport` | **nothing captured** — only the composed `BHHHH` |
  | `transport` \| `route_to_peer` | rounds 2 and 3 |
  | `route_to_peer` \| `igp_adjacency` | the `RR1 → PE1 broken` fixture |
  | `igp_adjacency` \| `interface` | round 1 |

  **Three of four boundaries hold on captured evidence. The fourth is exactly the gap B-432 flagged** when it gave rung 2 the TCP socket: *"no captured fault yet separates it."*
- **Evidence:** `tests/test_rungs.py`, auditing every boundary in the flow. 1758 passing.
- **What I did:** Closed B-437 at its measured size and filed **B-463** for the one gap.

  **What was left of it, precisely.** Part 2 (distinct subsystem) is satisfied for all five rungs, and the mechanically checkable half of it is now pinned: no two rungs draw from an identical set of collect steps, which would make them unable to disagree by construction. Parts 3/4 reduce to one missing round. Part 5 (the expected upper-layer signature) is **deliberately not built** — it is forward consistency, which is Q-019's open question, and two items answering one question badly is worse than one answering it later.

  So the item's durable content is a rule on `Rung` plus an audit that **fails** when a rung is added without separating evidence. That was the load-bearing half of "binding on every future rung" all along; the rest was an audit of five rungs that turns out to pass.

  **The finding worth more than the audit.** Round 3 was supposed to close the 1\|2 gap and did not: it produced `BBHHH`, because the administrative shutdown broke the socket as well as the FSM. So **rung 2 was given a distinct subsystem fifteen commits before anyone checked whether the fabric could exhibit the distinction.**

  > **A capability added and never exercised is indistinguishable from one that does not work.**

  That is not a variant of the vacuity rule — a vacuous *test* passes over nothing, while here the code is real, correct and unexercised. It is closer to §0.13's duplication face in its detector: nothing disagrees, nothing is wrong, and the only way to see it is to *count* — here, to ask which distinctions the evidence base has ever actually shown. B-463 is a one-line reversible config change (AS, MD5 or hold-timer mismatch on one neighbour) and would also replace this build's last composed fixture with a captured one.
- **Needs human review:** no — closed at measured size, as the operator invited
- **Blocks:** nothing. B-463 and B-462 are both operator-scheduled rounds.

---

## OBS-122 · B-459 · The input side of the containment boundary

- **Kind:** defect-fixed
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Track A, A1. The largest open safety gap: every containment mechanism in this build guards what a tool **returns**, and nothing guarded what a caller **supplies**.

  Measured before the fix, on the committed corpus: `investigate("RR1", "10.255.0.99")` for a peer that does not exist walked all five rungs, produced a rung table, and reported honestly at every step — because at every step it *was* honest. The device has no such neighbour, so rung 1 correctly said the session is not Established, and everything beneath followed.
- **Evidence:** `tests/test_subject_existence.py`, 11 tests. Refusal produces 0 rungs. 1766 passing.
- **What I did:** `Flow.subject_present`, declared per flow — `bgp_peer_exists` for `bgp_session`, `interface_exists` for `interface` — run against the epoch's evidence **before the walk**, producing the new finding `subject_not_found`.

  **Four decisions worth recording.**

  **It is not `undetermined`.** That means *a rung could not be read*, and reading is exactly what succeeded: the device answered, and what it said is that it has no such object. A caller told `undetermined` retries; a caller told `subject_not_found` corrects the question. Same distinction B-454 drew between a moved fabric and a slow collection, in a different place.

  **The refusal names what the device does have.** *"RR1 has no BGP neighbour at 10.255.0.99. It has 4: …"* A refusal that only says "not found" invites another guess.

  **It is still a rendered report.** The early return produces a full authoritative report rather than `None`, because B-439's contract has no exception for refusals — and a `None` would send a caller to a model's prose for the one result whose whole value is that it is *not* a claim about the network.

  **The injected-collector path is exempt, deliberately.** A caller supplying its own evidence has already decided what exists; asking it whether the subject is real is asking the test harness to validate the test. §0.13's setup face, and the reason the exemption is `None` rather than a silent pass.

  **What it does not catch, stated so it is not over-scoped later.** An invented object, not a wrong one: a real peer address that is not the one the operator meant passes cleanly — exactly as B-453 catches an invented entity and not a wrong relation. The two items are the same mechanism at opposite ends of the pipeline and they have the same blind spot.

  **A §0.12 audit fired during the work**, which is the cheapest evidence this build has that the audits are load-bearing: adding a universal finding failed `test_every_universal_finding_has_a_registered_next_check` because no recommendation was registered for it. The finding would otherwise have shipped with generic advice nobody wrote.
- **Needs human review:** no
- **Blocks:** nothing. A2 next.

---

## OBS-123 · Track A · Four of five items were already done, and measuring said so

- **Kind:** audit
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Track A listed five items. **A1 was the only one with work in it.** The other four were measured before building, per the operator's standing instruction, and the measurements are the result:

  | Item | Filed as | Measured |
  |---|---|---|
  | **A2** B-411 | *"a read timeout returns partial output with `errors: []` and `status: success`"* | **Not reproducible.** netmiko 4.7's `read_timeout` raises; the raise is caught per command; the envelope reports `status: error` with the failing command named, and keeps the outputs that answered |
  | **A3** B-425 | usage not instrumented | **Done.** `TokenUsage` + `Completion.usage` shipped at B-425 |
  | **A4** B-403 | consolidation to judge | **Already consolidated.** `health.py` is a re-export shim; all 40 rule definitions are in `checks.py` |
  | **A5** B-404 | six parsers predate §0.10 | **Done.** All six intents report `unaccounted_lines`, empty across the corpus |
- **Evidence:** A2 measured with a fake netmiko raising `ReadTimeout` mid-batch: `status='error'`, one error naming `show isis neighbors`, 2 of 3 outputs retained; per-intent slicing isolates it (`isis` error/0 commands, `bgp` success with its output). Two regression tests added.
- **What I did:** Closed A2 as measured-not-a-defect with the behaviour **pinned**, since "the filed defect does not exist today" is not the same as "it cannot arrive tomorrow" — a retry loop that swallowed the exception, or a transport that returned partial text instead of raising, would both fail the new tests.

  Kept the half of A2's acceptance that survived: *audit every consumer of `status`*. A partial batch isolates cleanly — the failed intent carries `status: error` and **zero** commands, so no consumer can read its absence as data, and its siblings keep a `success` that is true of them.

  **A4 needs no consolidation and the shim stays.** Nine call sites import from `health`, including `mcp_server/server.py`, `cli.py`, `agent_loop.py` and four test modules. Rewriting them buys tidiness and risks a large green suite for it — which is exactly what the item warned against. A shim whose entire content is a documented re-export is not duplication; it is one name for one thing, in the place its callers already look.

  **One thing I got wrong and caught by checking.** My first A5 probe reported `version` as unaccounted, and I nearly filed it as a gap in an item claiming all six were done. There is no `version` intent — it is `facts`. **A defect assembled from a name I did not verify**, which is the same shape as everything else this session and the reason the second probe enumerated `platforms.all_intents()` instead of a list I typed.
- **Needs human review:** no — four closures at measured size, as the plan asks
- **Blocks:** nothing. Track A is complete.
## OBS-124 · Track B · Six of seven items are blocked, and the seventh is done

- **Kind:** blockage
- **Escalation:** **HALT the track** (§0.5 — the other tracks continue)
- **Model:** opus-5
- **What happened:** Track B's seven items, measured against their own recorded dependencies before any implementation:

  | Item | Depends on | State |
  |---|---|---|
  | **B1** B-414 log normalisation | **B-206** | blocked — B-206 is Stage 2, excluded by this plan's own Part 1 |
  | **B2** B-418 temporal shape | B-414 | blocked transitively |
  | **B3** B-416 event episodes | B-414 | blocked transitively |
  | **B4** B-420 coverage metadata | — | **done** (pulled forward as T-029a) |
  | **B5** B-415 clock skew | **B-206** | blocked — *"scope it into the same change as B-206"* |
  | **B6** B-417 relationship projection | **B-107** | blocked — the `isis_adjacency` flow is not implemented |
  | **B7** B-419 four tiers | B-101, B-414 | blocked transitively |

  **Nothing on this track is runnable.** Not one item halted on contact with the code; all six were already recorded as blocked, in the dependency column of the backlog the plan was written from.
- **Evidence:** Dependency column read for all seven. B-420 verified complete in **both** halves: `Coverage` with `gaps()` exists, and `ground_correlation` returns `unbacked_absence_claim` for an absence claim with no coverage — measured, not assumed.
- **What I did:** Halted the track and recorded it. Two things came out of the measurement that are worth more than the halt.

  **B-414's deferral reason was checked and holds exactly.** The item says reductions 3 and 4 are deliberately not applied because *"at 28 records the aggregate carries no information the records do not."* My first probe read **200** records and I nearly treated the premise as stale. That was the pre-shaping count. After `shape_window` the corpus gives **28 kept records on PE2**, 7 on RR1, 9 on PE3 — the backlog's number, unchanged. Collapsing 28 records into 9 groups costs the verbatim ordering the timeline is built from and gains nothing; the argument is as good today as when it was written.

  **The dependency that blocks the track is the one the plan excluded on the previous page.** Part 1 lists Stage 2 as not running. B-206 is Stage 2. Track B is six items whose common prerequisite the same document had already ruled out — visible from the backlog's dependency column without reading a line of code, and it is the same failure as Track A's: **the plan was written from item titles rather than from item metadata.** Track A's version cost four items of restated work; this one cost a whole track.

  So OBS-123's rule is not enough on its own. A plan item must cite its last finding **and** its dependencies must be checked against the plan's own exclusion list — because an item can be perfectly well described, never previously measured, and still unbuildable.
- **Needs human review:** no — halted with the reason, per §0.5
- **Blocks:** Track B in full. Unblocked by B-206 (Stage 2) for five items, by B-107 for one.

---

## OBS-125 · Gate Zero · The backlog had drifted, and the number is 11 of 12

- **Kind:** audit
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** PLAN-V2's Gate Zero: reconcile every backlog item against the repository before scheduling anything. 95 items, four fields each.

  | State | Count |
  |---|---:|
  | `unverified` | **43** |
  | `DONE` | 30 |
  | `BLOCKED` | 14 |
  | `OPEN` | 8 |

  **The drift measurement the operator asked for.** `BACKLOG-COMPLETION-PLAN.md` scheduled 12 items across two tracks. Reconciled: **five were already `DONE`, six were `BLOCKED`, and one was `OPEN`.** That one was B-459, and it shipped. So 11 of 12 were not schedulable at the moment the plan was written.
- **Evidence:** `BACKLOG.md`'s reconciliation table, generated from the struck-through markers, a scan of every `FINDINGS.md` entry for item mentions, and the existing dependency column. 30 items needed a judgement read; the other 65 were mechanical.
- **What I did:** Wrote the table into `BACKLOG.md` and marked the previous plan superseded rather than deleting it — its two track results are the evidence for why this one starts here.

  **The finding is not the 11.** It is the **43 `unverified`**, which is nearly half the backlog. Those are items nobody has checked since filing — not "checked and still open", which is a different state and was indistinguishable from it until now. The previous plan's failure was reading `unverified` as `OPEN`, and it could not have done otherwise: `BACKLOG.md` had no way to express the difference.

  So the mechanical fix is not really the citation rule. It is that **the backlog now has a vocabulary that can say "nobody has looked"**, and a plan written from it inherits that distinction for free. A rule that depends on the planner remembering to check findings is the kind this build keeps replacing; a state column is read whether or not anyone remembers.

  **On scope, since the operator asked.** Reconciling all 95 was the right call and took under an hour, because three of the four fields derive mechanically — state from the struck-through marker, last-touched from a findings scan, dependencies from the column that already existed. Only 30 items needed reading.

  **And narrowing it would have produced a biased number.** Reconciling only what Part 1 and Part 2 schedule measures drift *in the items someone chose to schedule*, which is exactly the sampling error §0.13's data face describes. The 43 `unverified` are mostly items nobody scheduled, and they are the population the finding is about.
- **Needs human review:** no
- **Blocks:** nothing. Gate Zero is complete; P1.1 may start.

---

## OBS-126 · P1.1 · The error residual was real, and truncation was not a fix

- **Kind:** defect-fixed
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** P1.1's six items reduced to two — the reconciliation showed B-402, B-413, B-421, B-423 and B-461 already `DONE`. Both remaining ones produced something.

  **B-457.** A rejected paraphrase stopped changing the exit code at B-439, correctly, and that removed the only signal a *systematic* grounding regression had. The per-run status was in the payload and on stderr; **a field nobody aggregates is not detection**, and the failure this guards against is a change in the *rate*. `metrics.py` now counts paraphrase outcomes, exposed as `nettools_paraphrase_outcomes_total`, labelled in the exposition text as tool-health with *"Never page on this."*

  **B-458 was filed as a bounded residual and turns out to be a live one.** The mitigation was truncating error strings at 400 characters, on the reasoning that a transport exception *can* embed device output. Measured in netmiko 4.7's `base_connection`: one `ReadException` message interpolates **`output={repr(output)}`** directly. So it does, and a 400-character cap passed up to 400 characters of it to a model.
- **Evidence:** netmiko source inspected at the raise sites. 1776 passing.
- **What I did:** Replaced truncation with **classification**. Each error is rebuilt from two values the boundary already trusts — the command we rendered, and a phrase from a declared `ERROR_KINDS` table. An unmatched detail is withheld entirely.

  **The distinction is the finding.** Truncation is a *filter*: the dangerous value passes through the function and some of it survives. Classification is *containment*: the output is assembled from safe parts and the dangerous value is never in it. Same argument as `prompt_library` never holding device text (OBS-061) and `mcp_server` not importing a write (OBS-106) — this build keeps arriving at it, and this is the third time a filter has been replaced by a construction.

  **"Bounded rather than claimed clean" was the right thing to write and the wrong thing to leave.** The item was honest about being a bound, which is why it was findable; but a bound recorded as acceptable is a filter nobody will revisit, and the measurement that made it urgent took four minutes. Worth noting for the other residuals: *a documented limit is a decision that expires, not a state.*

  A third test came out of writing the second: `errors` is a list of strings and nothing guarantees `"<command>: <detail>"`. An entry without a colon must not fall through unmodified, which is how a filter written for one shape leaks on another.
- **Needs human review:** no
- **Blocks:** nothing. P1.1 complete.

---

## OBS-127 · P2.4 · "The local buffer is better" does not imply "so build against it"

- **Kind:** assumption-wrong
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5 (recording an operator correction of their own reading)
- **What happened:** PLAN-V2's P2.4 offered three options for the evidence-reduction chain and said option 2 — *descope B-206, build normalisation against the local `show logging` buffer* — is where the evidence points.

  The first half follows and the second does not. **Severity 5 and 6 events never reach the log platform**, so for local correlation the device buffer genuinely is the better source; that is measured and it is already what the code reads. It says nothing about whether **normalisation** is needed.

  **B-414's own deferral argument survives the descoping intact:** *"at 28 records the aggregate carries no information the records do not, and collapsing them costs the verbatim ordering the timeline is built from."* Re-measured this session — 28 kept records on PE2, 7 on RR1, 9 on PE3, after shaping. Building reductions 3 and 4 against that window is building *N* before validating one.
- **Evidence:** `shape_window` output across three devices on the `broken` label; Q-011 on the trap level.
- **What I did:** Recorded the operator's own framing of the error: *"I extended 'the local buffer is better' into 'so build against it', which does not follow."*

  **Resolution taken:** descope B-206 as a dependency, and separately mark **B-414 `CLOSED-AS-MEASURED`** with the 28-record measurement attached, reopening if a window exists that makes aggregation informative.

  **One correction to the option itself:** descoping B-206 unblocks **4 of 6**, not all six. B-415 needs two clocks to disagree and there is only one; B-417 needs B-107, an unimplemented flow. An option described as unblocking a chain should say how much of the chain.

  The shape is worth naming because it is not the data face and not the rules face: **a measurement that is sound, and an inference from it that reaches one step further than the measurement covers.** The evidence was about *which source*; the conclusion was about *what to build*.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-128 · Gate Zero · A state column is mechanical; a citation rule is attentional

- **Kind:** method
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator's §0.2a rule was *a plan item cites the last finding that touched it*. Gate Zero's reconciliation produced something that supersedes it, and the operator has recorded it as such.

  **The 43 `unverified` matter more than the 11-of-12.** Eleven of twelve scheduled items being unschedulable is the visible failure. Nearly half the backlog never having been checked since filing is the condition that produced it.
- **Evidence:** 95 items reconciled: 43 `unverified`, 30 `DONE`, 14 `BLOCKED`, 8 `OPEN`.
- **What I did:** Recorded the supersession.

  > **The previous plan could not have avoided its error, because the vocabulary to express the difference did not exist.** `BACKLOG.md` had no way to say *"nobody has looked at this"* as distinct from *"this is open"*. Reading `unverified` as `OPEN` was not carelessness; it was the only reading the document supported.

  That is why a state column beats a citation rule. **A citation rule is the attentional form** — it asks the planner to go and check, every time, for every item, and it fails the first time someone is in a hurry. **A state column is the mechanical form** — it is read whether or not anyone remembers, and a plan written from it inherits the distinction for free.

  The general shape, which this build has now met in several places: when a rule asks someone to *do* something repeatedly, look for the representation change that makes the doing unnecessary. §0.16 (a turn ends with a hash) is the same move applied to reporting; `Flow.subject_present` is the same move applied to validation.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-129 · B-462 · Round 7 scored: a down port does not persist, and the restore proved the instrument

- **Kind:** validation
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Round 7 answered the gating question B-456 shipped with. **99 samples with `Gi0/0/0/0` admin-down on PE2, and not one names it in the route to `10.255.0.31`.** A down port does not persist as `Backup (Local-LFA)`, so `EACH_PATH_INTERFACE` cannot derive a member set containing a dead port. **B-456's premise is sound; B-462 is closed.**
- **Evidence:** `evidence-archive/round7/`, two runs at ~1.04 s resolution. Full per-sample payloads archived per §6.1d.
- **What I did:** Scored it, and recorded two things the run produced that the prediction did not anticipate.

  **The secondary claim survives and is unmeasured, and the distinction is not pedantry.** The 10 s falsifier did not fire, so the claim stands — but what was established is **"under 1.04 s"**, not a measured window. The harness said so in its own verdict: *"the window is bounded above by the sampling resolution, not measured as zero."* Reporting `estimated_window_seconds: 0.0` would be **absence read as presence**, which is the `unevaluated` discipline arriving in a measurement instead of a check. A method cannot measure a window shorter than its own resolution.

  **The restore transition is a positive control, and it is the most useful thing in the round.** Scored separately at the operator's instruction: 0 samples name the port while it is not up — same answer, sign reversed. But **one** sample has the port `up/up` with the route not yet naming it.

  That single sample is what makes the shutdown-side zero mean something. A bare zero is consistent with *"nothing happened"* **and** with *"the instrument is too coarse to see it"*. Catching a ≤1.04 s lag on the restore side rules out the second at that scale. **The negative result is only interpretable because the same run produced a positive one of comparable size** — and it was free, because the harness sampled through the restore rather than stopping at it.

  Worth generalising: **a run that observes only the absence of a thing should be asked what it would have taken to observe its presence.** Where the same run answers that by accident, keep it.
- **Needs human review:** the sample count — see below
- **Blocks:** nothing. B-462 closed.

  **Corrected 2026-08-17 — the count, and what it cost.** I recorded a discrepancy between a reported three runs and the two on disk. The operator resolved it: two directories were deleted during a tidy-up, one of which held a **158-sample** run whose numbers had been reported.

  **It is not carried as a replication.** A count quoted in a transcript is not evidence once its payload is gone — which is §6.1d arriving from the other direction. That rule exists because round 4 archived a *finding* and could not be re-examined when the semantics changed; the same conclusion follows when inputs were archived and then lost. **A finding without its inputs cannot be re-examined, and how it came to lack them does not matter.**

  **So B-462 rests on one run**, `151420`: 99 down-port samples, zero naming the port, 1.04 s resolution, positive control in the same run. That is a sound result and it is stated as one run rather than padded to three.

  **And `151001` is not a failed round — it is the control for OBS-130.** It ran `--dry-run` and produced zero writes and zero down-port samples, because `round7.py` line 329 sets the callee's flag explicitly (`fault_lab._dry_run = _dry`), which is exactly what `round5.py` omits. The same code path that pushed configuration during round 5's "dry" run wrote nothing here.

---

## OBS-130 · Harness · A safety flag in the caller and a guard in the callee look identical and are unrelated

- **Kind:** defect-found
- **Escalation:** **HALT-class** — a device write occurred during a run labelled dry
- **Model:** opus-5
- **What happened:** `round5.py` and `round7.py` each define `_dry = False` and set it from `--dry-run`. `fault_lab.push()` guards on **`fault_lab._dry_run`**, which they never set. At the call site the two are indistinguishable:

  ```python
  _dry = args.dry_run                    # the caller's flag
  status = push(conn, APPLY, "apply")    # reads the callee's, still False
  ```

  **So a `--dry-run` pushes configuration.** Round 5's dry run recorded `push_status: 'send:ReadTimeout+committed+exited'` at 09:30:48 — a real `send_config_set` and commit against PE2, in a run whose own log line reads *"[dry-run] fault not applied"*.
- **Evidence:** `faultlab/round5/20260817-092916/timeline.jsonl`; `fault_lab.py` lines 199, 264; `round5.py` line 92.
- **What I did:** Audited every cross-module flag in the harness and recorded the general form in §0.13's duplication face.

  **What round 5's dry run did to the fabric cannot now be determined, and that is the worse half.** The evidence is genuinely mixed: the push reported `send:ReadTimeout`, meaning `send_config_set` raised before completing — and `probe-01`, a direct read of PE2's IS-IS **69 seconds after the commit**, reports `igp_adjacency PE2 healthy`, which two shut uplinks would not. So the config most likely never took effect.

  But *most likely* is where it ends. **Nothing read the running configuration**, because the same defective flag sent `restore()` down its `if _dry:` branch — *"RESTORE verified (dry-run: nothing written)"* — skipping the verification that would have said. §6.1's rule is *verify by reading the device, never by the write's report*, and the dry-run guard is exactly what disabled it.

  **The two failures compound.** A push that should not have happened is bad; a push that should not have happened *and* a verification that was skipped for the same reason means the fabric's state during that window is unrecoverable. Recorded as unknown rather than assumed benign.

  **Why this is the duplication face and not carelessness.** Both modules hold what should be one value, and they agreed for as long as nobody looked — identical in shape to the three `_is_numeric` re-derivations. The difference is the direction of failure: a diverged discriminator gives a wrong answer; a diverged safety flag **writes to a production device**. The remedy is the same: one flag, owned by the module that acts on it, read by everything else through a function.

  **The correction to my own record.** I attributed the dry run's 56–61 s skew to a device slowdown (OBS-107, ROUND-5 §5) and it may partly have been the fault. Probe 00 predates the commit and was already slow, so the login-penalty account survives for that sample; probes 01 and 99 span a window whose fabric state is now unknown. **The skew analysis is not invalidated, it is narrowed** — one clean sample instead of three.
- **Needs human review:** **yes — a device write occurred outside an authorised window, and the harness is the operator's**
- **Blocks:** nothing in this repository. The harness needs the flag fixed before the next round.

---

## OBS-131 · Process · An archive that is not committed is a working file that still exists

- **Kind:** method-defect
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Two round-7 run directories were deleted during a tidy-up, taking a **158-sample** result with them. Its numbers had been reported; the samples had not been committed.

  **Then I reproduced the same failure while writing this up.** I copied both surviving runs into `evidence-archive/round7/` and committed — and the repository's `.gitignore` carries a blanket `*.jsonl`, so `git add -A` took `verdict.json` and **silently dropped every `samples.jsonl` beside it.** The archive step looked done and produced nothing durable. It was caught only because the operator's correction sent me back to check what was tracked.
- **Evidence:** `git check-ignore -v` on the archived payload returns `.gitignore:10:*.jsonl`. Before the fix, `git ls-files evidence-archive/` listed two `verdict.json` files and no samples.
- **What I did:** Force-added the round-5 and round-7 payloads, added a `!evidence-archive/**/*.jsonl` negation with the reason beside it, and extended `chaos-harness.md` §6.1d.

  > **A round is archived when its payload is committed. Until then it is a working file that happens to still exist.**

  **Two things make this worth a finding rather than a `.gitignore` fix.**

  **The rule was already written and still did not bind.** §6.1d has required archiving the full payload since round 5, and it was followed — the files were written, the directory existed, the copy was made. Every step of the stated procedure was performed and the outcome was an empty archive, because the rule said *archive* and the failure was in what archiving meant. **A procedure can be followed exactly and produce nothing when a word in it is underspecified**, and the word will look unambiguous to whoever wrote it.

  **The blanket ignore was correct for what it was written for and wrong here.** `*.jsonl` sensibly excludes evidence-store scratch and metrics files. It also silently reclassified the one class of `.jsonl` that is the project's primary evidence. Nothing warned, because a `.gitignore` match is not an event — it is the absence of one, which is the shape this build keeps meeting.

  Related to §6.1d's own general form and distinct from it: that rule is about storing *inputs* rather than *conclusions*. This is about storing them **somewhere that survives**, and both failures produce the same end state — a result nobody can re-examine.
- **Needs human review:** no
- **Blocks:** nothing. Round 5's and round 7's payloads are now tracked.

---

## OBS-132 · Follow-up · Two more blanket patterns over evidence, one of them in code written today

- **Kind:** audit
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** The operator asked for two follow-ups from OBS-131: audit `.gitignore` for anything else that could silently exclude evidence, and apply the end-state rule to the other procedural instructions.

  **The audit found two more, and the second is worse than the first.**

  **`*.log`** covers `round5.log` and `round7.log` — a round's narration, sitting beside its samples. Tracked now only because I force-added them; the next round's log would have been ignored.

  **`evidence/`** ignores an entire directory — and **both samplers wrote there by default.** `scripts/round7_sampler.py`, which I wrote today, defaulted to `evidence/round7/samples.jsonl`. Had round 7 been run with my sampler rather than the operator's, its payload would have landed somewhere git refuses to see, and the finding would have read exactly like a successful archive.
- **Evidence:** `git check-ignore -v evidence/round7/samples.jsonl` → `.gitignore:20:evidence/`. Both sampler defaults confirmed by grep.
- **What I did:** Added `!evidence-archive/**/*.log`, annotated the `evidence/` pattern with why it is correct and what it is *not*, and repointed both samplers at `evidence-archive/`.

  **The naming is the trap and it deserves recording.** `evidence/` and `evidence-archive/` are one hyphen apart and mean opposite things: the first is a snapshot **cache**, correctly ignored; the second is a round's **payload**, which must be tracked. Nothing about the names says which is which, and the ignore pattern for one silently swallows anything written to a path that looks like the other. The annotation now says so in `.gitignore`, which is where someone adding a pattern will be looking.

  **The end-state pass changed less than expected, and the reason is interesting.** Only two loose procedural verbs turned up across both documents. §6.1d itself was the main offender, and it is now *"Every round's full payload is **committed**"* with the check named: `git ls-files evidence-archive/round-N/` lists the samples. §6.1's step 6 gained one too — *the prediction's commit is on the remote*, since "pushed" was already right but the check was not stated.

  **What the sparseness suggests:** this build's rules are mostly stated as properties already (*"no unparsed device text reaches a model"*, *"a turn ends with a commit hash"*), and those are end states by construction. The procedural face bites where a rule describes a **workflow** rather than an invariant — and §6.1d was the only real workflow in either document. Worth knowing that the exposure is concentrated rather than diffuse.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-133 · B-463 · A falsifier is only meaningful if the instrument can produce the value that would fail it

- **Kind:** insight
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Round 8's §2a.2 said *"refuted if no sample in the whole window shows `socket_armed_read: true` with the session not Established."* No sample showed it. `SEPARATION_OBSERVED: false`, `separation_sample_count: 0`, over 178 post-fault samples.

  **The falsifier fired on an instrument that could not have produced the value in any state of the world.** `round8.py`'s `_SOCKET = re.compile(r"socket.*?(armed|not armed)", re.I)` is a minimal-width search over `Socket not armed for io, armed for read, armed for write`, so it reads the **io** field — which is `not armed` on a healthy session — and returns `False` unconditionally.

  Across both runs there are **195 samples in which the session is `Established`**: four pre-fault, eleven post-restore, and all 181 of the dry run. **`socket_armed` is `False` in all 195.**
- **Evidence:** `evidence-archive/round8/`, both runs. `20260817-160359/verdict.json` reports `samples_socket_armed: 0` on a run whose `fsm_states_seen` is `['Established']` alone — the fault never applied, the session never left Established, and the socket still never armed.
- **What I did:** Scored §2a.2 as **void, not refuted**, and recorded the general rule in `ROUND-8.md` §5.1.

  **The distinction is not pedantry: a refutation and a void trial license opposite next actions.** A refutation of §2a.2 closes B-463 as "not separable". A void trial leaves it exactly where it was and costs a lab window. `SEPARATION_OBSERVED: false` is the same three characters either way, which is what makes this worth a rule rather than a note.

  **And the check was already in the payload, unread.** `samples_socket_armed` counts over the post-fault window only. Had it counted over the baseline it would have printed `0 of 4 armed on an Established session` and stopped the round at its own first verdict line. Round 7's positive control was designed in (OBS-129); round 8 had one by accident and did not look at it. **A control you do not read is not a control** — it is a column.

  Round 8b's re-seal (§6.2, precondition 3) now requires the baseline and post-fault counts to be reported separately, which makes the check unavoidable rather than available.
- **Needs human review:** no
- **Blocks:** nothing — B-463 stays open, which is where it already was.

---

## OBS-134 · B-463 · Two fields, one command, one parser defect — the raw one survived and the derived one did not

- **Kind:** insight
- **Escalation:** NOTE
- **Model:** opus-5
- **What happened:** `round8.py` has two defects, and they are the **same idiom**: a first-match search over a string carrying more than one candidate token.

  1. `_SOCKET` takes the `io` field instead of `read` (OBS-133).
  2. `_NOTIF` matches `notification` before `hold time expired` in *"due to BGP Notification sent: hold time expired"*, so `reset_names` is `"notification"` — and `as_named` treats `"notification"` as evidence of an AS mismatch. **The dry run reports `discriminator_reset_names_as: true` while containing zero samples that name an AS**; all 181 of its resets are hold-timer expiries.

  So two of §2a.7's three discriminators were implemented wrongly. **The mechanisms were separated anyway**, and what separated them is the raw `last_reset` string stored verbatim in every sample: `BGP Notification sent: peer in wrong AS`, 156 samples. That rules in the AS rejection, and rules out TTL/multihop (the peer's OPEN was received and evaluated, so TCP established) and BFD (§2a.6).
- **Evidence:** `evidence-archive/round8/round8.py` lines 117–120 and 294–297, committed beside the payloads. Counts from both `samples.jsonl`.
- **What I did:** Recorded it in `ROUND-8.md` §5.3.

  **The round demonstrates the amendment it produced, inside its own payload.** Two fields, from the same command, in the same file, under the same defective parser. One was stored as text and one as a boolean. **The text survived and the boolean did not**, and the recovery of §2a.5, §2a.6 and two thirds of §2a.7 rests entirely on which of the two a field happened to be.

  This was not foresight. `last_reset` stayed raw because a string is awkward to reduce; `socket_armed` was reduced because a boolean is tidy. **The tidier choice is the one that lost the round** — which is a better argument for §6.1d's amendment than the amendment's own reasoning, because nobody was trying to prove it.
- **Needs human review:** no
- **Blocks:** nothing.

---

## OBS-135 · Process · The procedure face a third time, and the first remedy was working

- **Kind:** defect-found
- **Escalation:** DECIDE-AND-LOG
- **Model:** opus-5
- **What happened:** Round 8's payload was **not archived**. Both run directories were in `~/ai-agent-ops/faultlab/`, which is not a git repository — not untracked within one, no repository at all. Round 7 lost 158 samples to precisely this state (OBS-131).

  Nothing detected it, and OBS-131's remedy would not have: `git ls-files evidence-archive/round8/` returns empty, which is the right answer to the wrong question, because nobody ran it.
- **Evidence:** `git -C ~/ai-agent-ops/faultlab status` → `fatal: not a git repository`. `find` over the repo for `*round8*` → nothing.
- **What I did:** Committed both runs plus `round8.py` to `evidence-archive/round8/` (7 files, `git ls-files` confirms), and excluded `evidence-archive/` from ruff — a formatter must not rewrite an artefact that has to stay byte-identical to what ran, and `--fix` on one silently alters the record.

  **What makes this worth a separate finding is that the first remedy held.** OBS-131 replaced *archive* with *committed* and named `git ls-files` as the check. That fix is correct and it is still correct. §6.1d's **other** underspecified word is the noun: *"archive the full payload"*. Round 8 archived the `investigate`-shaped payload, which is a structure of already-parsed fields, and so held 355 copies of a broken parse's output and no copy of the line it came from.

  > **Specifying the end state fixes *whether* the artefact exists. It says nothing about *what the artefact is*, and a procedure has two ways to be void.**

  Recorded in `BUILD-PLAN.md` §0.13 under the procedure face as a second instance, and in `chaos-harness.md` §6.1d as *a parsed field is a conclusion; the input is the text it was parsed from*.

  **The generalisation.** *Payload*, *evidence*, *result*, *record*, *sample* all name a boundary the author fixes implicitly and the reader re-fixes on their own terms. The boundary that matters is not where any module draws it — it is **wherever the next dispute lands**, which by definition is unknown when the rule is written. So the detector cannot be "name the boundary correctly"; it has to be *store one layer lower than seems necessary*. A raw line beside a boolean costs bytes.
- **Needs human review:** no
- **Blocks:** nothing.

---

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
| Q-006 | T-025 | Does the descent's stopping rung match what a network engineer would conclude by hand from the same fixtures? | **Yes — this validates the architecture** | **Resolved (OBS-076, OBS-077)** — hand diagnosis recorded and committed at 14:11:50 UTC, agent ran at 14:12:02. Identical: `igp_adjacency` on PE3, `igp_isolated`, interface rung healthy. Answered on a case whose *symptom* is indistinguishable from the captured `broken` label, where the cause was a different rung. |
| Q-007 | T-035 | Telegram or Mattermost? Hosted means device names, IPs and RCA text leave the estate; self-hosted keeps them in. Decide before implementing — only one provider gets built. | Yes for T-035 | **Resolved 2026-08-17 — Telegram.** Operator decision, residency trade-off accepted explicitly for a lab. Mitigation is structural: the notifier sits behind one provider interface, so Mattermost is a swap not a rewrite. Env surface in `.env.example`; chat-ID allowlist is fail-closed and is **delivery, not authorization**. Unblocks T-035 and B-209 |
| Q-008 | T-035 | Which host runs `nettools` in the target deployment, and does it have outbound egress to the chosen channel? | Yes for T-035 | Open |
| Q-010 | T-003 | The MiniMax provider uses the OpenAI **Responses** API, not Chat Completions, so `BUILD-PLAN.md` T-003 step 4 (`reasoning_split`, `max_completion_tokens`) does not apply. Both behaviours it targeted are achieved structurally on that route. Confirm the route choice before the MVP-1 gate is built on it. | No for MVP-0 · **yes for the MVP-1 gate** | Open — decided and evidenced (OBS-010) |
| Q-012 | T-005 | **The lab was rebuilt ~2 days ago and is now healthy** — all 16 BGP sessions Established, PE2/PE4 back to 2 IS-IS adjacencies. T-011 says to capture "against the current broken state", which no longer exists. Re-break the lab, capture a new consistent healthy label, or build the broken case synthetically in-test? | **Yes for T-011** (T-025/M3 unaffected — fixtures still hold the broken state) | **Closed (OBS-049)** — `broken` captured 2026-08-16 with both uplinks. Originally (OBS-019) — options 1+2: keep `t0`/`t1` frozen, add complete `healthy` and `broken` labels; operator runs the break, capture coordinated at T-011 |
| **Q-018** | **T-033** | **PE3 has 0 IS-IS adjacencies as of 2026-08-16** (every other device is at its expected 2, and PE3 had 2 in the pre-proposal baseline). Intended — the T-033 fault applied and in place — or an unrestored fault from the OBS-075 harness incident? I did not read further to distinguish them, because if it is the former those reads are the diagnosis the Q-006 protocol keeps closed. | **Yes — blocks T-033** | **Resolved** — intended; the T-033 fault, confirmed live by the operator. |
| **Q-019** | **D6** | **Is "lowest broken rung is the root cause" still right under two simultaneous faults?** Interface down *and* BGP neighbour admin-shut gives the interface as the lowest broken rung — correctly — but fixing it will not bring the session up, and the rung table is identical to the single-fault case. Two candidate signals (a second unexplained commit in the timeline; forward consistency of the upper rungs against what the cause alone predicts), neither validated. | No for MVP-0 — every corpus label is a single fault | Open (OBS-078) — **only injection answers it (B-426)** |
| **Q-020** | **B-428** | **HALT.** Clause 2 of the B-428 specification (*rung 1 broken + empty causal chain → `undetermined`*) matches exactly one reachable state, and that state is `cause_not_localised` — where an empty chain is **necessary**, because the chain is the broken rungs *above* the cause and rung 1 has none. Applying it would replace a correct golden-tested finding with a false one. The state it appears to reach for (cause below rung 1, chain empty) is unreachable. **Drop clause 2, or re-scope it to the impossible state as a never-firing assertion?** Note the interaction: if clause 2 was a step toward retiring `cause_not_localised`, that resolves B-432, which the same instruction deferred. | **Yes — blocks B-428 and track A** | **Resolved** — operator dropped clause 2 and confirmed the reading; resolution (i), explicitly not (ii). B-428 shipped as clause 1 alone (OBS-097). B-432 stays deferred and untouched. |
| Q-009 | T-002 | Should `MINIMAX_API_KEY` **and the lab device credentials** be rotated after this build? Both were pasted into the transcript (OBS-008, OBS-037). It was pasted into the session transcript, which no control in this repository can revoke. | No — nothing is blocked on it | Open — recommended (OBS-008) |

---

Task status lives in `TRACKER.md`, not here. This file records *what was learned*; the tracker records *what was done*.

## OBS-156 · Holistic review · Two independent lenses converged on the same two defects, both in solo-built code

The 2026-08-18 holistic review ran four read-only reviewer lenses (adversarial
new-code, invariants-by-probe, docs-drift, operator-experience) plus the
orchestrator's own pass. Two of the four found, **independently and without
seeing each other's work, the same two most-serious defects** — and both live
in the OPS wave, the one wave built solo after its four agents were lost to a
spend limit (OBS-155).

**1. The MCP boundary never got B-467's free-text treatment.** `sanitize()`
withheld raw `commands` buffers but passed device-authored free text under
`parsed.records` — a syslog line's `text`, a BGP `last_reset_reason`, an
interface `description` — to the MCP client **completely unmarked**. The MCP
client is a model consumer; this is the exact "an unauthenticated attacker
writes it into device output" threat model B-467/B-470 exist to close, on a
path B-467's scope never listed. Both the adversarial lens (by canary through
the registered tools) and the invariant lens (by canary sweep over every
egress path) reproduced it on both surfaces. The projector shipped for the
`llm_analysis`/`evidence_budget`/`prompt_library`/`agent_loop` paths; the MCP
boundary was a *fifth* path to a model and inherited nothing — the same
"an invariant that holds for every internal caller is a convention that has
not met a new consumer" lesson CLAUDE.md already records for invariant 4,
recurring one consumer later.

**2. The P0 shell-injection path.** `route_alertmanager` never validated its
`subject` (the syslog path does), and the shipped n8n example *joined*
`suggested_command` into a shell string and echo-piped the webhook body — the
exact anti-pattern `RoutingDecision.suggested_command()`'s own docstring warns
against and the sibling systemd example got right. The same PR got the pattern
right in one artefact and wrong in another.

**The meta-lesson.** Solo-built code, held to the same specs and the same
merge gate (OBS-155), still carried a defect class that neither line-by-line
review nor the test suite caught but that adversarial probing and invariant
canaries did — and the *convergence of two independent lenses on the same two
issues* is what raised them from "plausible" to "fix now". A single reviewer
finding one of these is a report; two reviewers finding both, separately, is a
measurement. The gate that was missing was not more careful reading — it was a
second, differently-motivated pair of eyes probing for what the builder
assumed. That is the argument for the review being multi-lens rather than
deeper-single-lens, stated as evidence rather than as principle.

Both fixed the same day, each with a regression test, and both tests
mutation-verified (20/20 guards hold) so they cannot themselves become the
vacuous companions OBS-153 warned about. Every other lens finding was verified
by the orchestrator's own probe before acceptance — the walkthrough's
import-time `--help` crash, the audit-table misrender, the NaN-through-range-
check, the dead ERROR_KINDS entry, the credential-requiring "credential-free"
`list_devices` — none inherited on the reviewer's word.

## OBS-157 · MCP re-test · A model's completeness claims carry no information — measured three times in one session

Task 0 (2026-08-18, `gemma-4-e4b`) closed the Q5/Q6 debt §10.5 recorded, and
produced a sharper version of both findings it was sent to test.

**§6.2 recurs with the opposite shape to the one predicted.** The restatement's
sin was not omission — two of five rungs survived only inside "fully healthy",
which in two sentences is fair. It was **addition**: the authoritative report
says *"no fault on this dependency path; if a problem is being reported it is
about something this flow does not cover"* and marks `requires_human: true`.
The paraphrase turned that into *"likely an application or configuration
problem"* and dropped the caveat; a turn earlier the same model offered *"the
service running on 10.255.0.12 is down"* about a **router loopback**. The
descent's entire value is declining to name a cause it cannot evidence, and the
restatement handed that discipline back. B-439 is built against a model
dropping what the descent found; B-490 is the half where it supplies what the
descent refused.

**§6.1 confirmed, and generalised past its original claim.** Asked whether its
summary covered every rung, the model answered that it *"accurately captured
the conclusion of all five rungs"* — then, in the same message, accounted for
rungs 1, 4 and 5 only. Not a model misremembering an earlier turn: a
completeness claim contradicted by the enumeration printed directly beneath it.
And unprompted, earlier in the same session, it listed **22 of 23** tools as
the complete set. Three enumerations, two incomplete, three claims of
completeness. **The finding is not that models omit things. It is that asking
one whether it omitted something returns no information at all** — so every
place the design reads back a self-report must become a code-side comparison
against the typed payload (B-491).

**And a hypothesis of mine that measurement killed.** Three of nine parallel
MCP calls failed to connect, and I predicted the unbounded fan-out was the
cause — nine concurrent logins, no pool. Measured: sequential 16/16 clean,
library-unbounded-9 zero failures, library-pooled-4 one failure. Concurrency
is associated with failure; **the worker count does not predict it**, so the
stated hypothesis is refuted and only the weaker claim survives. Left in the
record with the wrong version visible, because OBS-147's rule cuts both ways:
a check that only ever confirms has not checked anything, and that includes
checks on my own reasoning.

**One real defect, fixed the same hour.** Those three failures each read *"an
unclassified error; its detail is withheld because a transport exception can
embed device output"*. netmiko had said `TCP connection to device failed.` and
no `ERROR_KINDS` entry matched — **the commonest production failure produced the
least useful message the system can emit**. The withholding rationale does not
even apply: a connection that never opened has no device output to embed. Five
connect-failure kinds added to both copies, pinned by a test.

## OBS-158 · Paired model arms · The bigger model answered better, and every bit of the difference was in the part that is switched off by default

`gemma-4-31b-it` ran §11's three questions on the same healthy fabric, in a
genuinely fresh session. It beat the 4B on every dimension a human would
notice, and the shape of *where* it beat it is the finding.

**It rejected the false premise.** Asked *"why can't RR1 reach 10.255.0.12?"*
when RR1 demonstrably can, the 4B answered inside the premise and speculated
about an application fault on a **router loopback**. The 31B said the premise
was wrong and showed why. No model had done that before.

**It fired an active probe, unprompted — a first here.** After a clean descent
it ran `get_lab_ping` on its own initiative, reasoning that the flow covers the
control plane and "reach" might mean the data plane. Correct engineering. Also:
`NETTOOLS_ALLOW_ACTIVE_PROBES` defaults to `True` and B-473's annotations are
signalling, not enforcement, so nothing stood in the way. **Initiative scales
with capability**, and the model most likely to start generating packets when
told "no fault on this path" is the capable one, during an incident. B-473 was
built on that argument with no evidence; this is the evidence, arriving from
exactly the predicted direction (B-493).

**It refuted my own finding from four hours earlier.** OBS-157 concluded that a
model's completeness claims *"carry no information"*. Asked whether its summary
covered every rung, the 31B answered *"No — I omitted the transport and
physical interface rungs"*, which is exactly right. So the claim was too broad:
self-report is capability-dependent, not structurally broken.

**The correction is worth more than the original claim.** The 4B's false
completeness claim and the 31B's true one are *indistinguishable at read time*
— both fluent, both specific, both confident. The reason to replace a
self-report with a code-side diff is therefore not that models cannot do it;
it is that **a reader cannot tell a correct self-report from a confident wrong
one**, so the answer's form carries no evidence either way. Same fix, better
reason, and the better reason is only visible because two sizes were run.

**And the result the thesis actually rests on.** Both models selected the same
tool, got the same authoritative report, and reached the same verdict about the
network. **Every difference between the arms lived in narration** — the layer
this build already declares non-authoritative, grades separately, and ships
switched off. Capability bought better prose, better initiative and a caught
false premise; it bought nothing whatsoever in the diagnosis, because the
diagnosis was never the model's to make.

The caveat that belongs beside it: a CLI user reads the authoritative report,
but **an MCP user reads the model's narration of it** — the paraphrase path
with no flag to leave off. The model-size floor that does not apply to
`nettools investigate` does apply to the MCP surface, and an invented service
on a loopback is what that floor looks like from underneath.

## OBS-159 · Preflight · The one new finding is a live instance of the flow that is not built yet

Preflight (2026-08-18T10:26Z) **passed**: tree clean and in sync, four frozen
blobs identical to `6629a2c`, ruff clean, 1964 passed, offline demo exits 1 on
the fixture fault, fault option 8 and `SPARE_IF` both present.

**Round 6's off-path precondition holds**, checked by hand because the script
deliberately refuses to check it: `PE2 → 10.255.0.31` egresses
`Gi0/0/0/0` (Protected, metric 20) and `Gi0/0/0/1` (Backup Local-LFA, metric
30). **Neither is `Gi0/0/0/2`**, so the spare port is genuinely off-path and
the round is not void as sealed.

**Two warnings. One is known** (faultlab is still not a git repository,
OBS-135; `round8b.py` already defaults into `evidence-archive/`).

**The other is new and worth the paragraph.** `isis_adjacency_count_drift` on
**PE3**, off this fabric's derived floor. Traced:

* PE3 has **two** LLDP neighbours — P2 on `Gi0/0/0/0`, P4 on `Gi0/0/0/1`.
* PE3 has **one** IS-IS adjacency — P4 only.
* P2's four adjacencies are P1, P4, P3, PE1. **PE3 is not among them.**
* Both ends of the PE3↔P2 link are `up/up`, MTU 1514 on each, ARPA.

So: physical up, LLDP forming, MTU matched, and **neither end sees the other in
IS-IS**. The direction matters and B-465's fix is why this surfaced at all — it
is *below* baseline, a warning, where PE2/PE4's floor entries are *above* and
merely informational.

**And here is the useful part: the tool cannot say why, and that is correct
rather than a defect.** Every remaining candidate — the interface not being in
the IS-IS instance, a level or authentication mismatch, a missing address — is
**configuration**, and this build reads operational state only. `show
running-config` beyond `hostname` is not in the allowlist and will not be. This
is the config axis, B-104, arriving as a concrete need rather than a planned
feature: the fabric produced a question MVP-0 is structurally unable to answer.

It is also a **naturally occurring broken case for `isis_adjacency`** — B-107,
the flow that runs first and alone once B-428 lands, and the one whose backlog
entry insists its broken state must be *designed alongside the flow rather than
captured afterwards*. A real one has now appeared on its own. Worth capturing
as a labelled fixture before the lab is rebuilt, because this exact shape —
link up, LLDP up, adjacency absent — is the case a healthy-only corpus can
never contain.

**No impact on the sealed rounds.** Round 8b and round 6 both act on PE2; PE3
is on neither's path, and the RR1↔PE2 path used by §11/§12 shows
`igp_adjacency@PE2` healthy with 2 adjacencies. **Do not repair it before the
windows** — changing the fabric now would invalidate a preflight that has
already passed, and the rounds do not need it.

## OBS-160 · Round 8b · The precondition was verified on the wrong artefact, and the abort that caught it cost forty seconds

Round 8b aborted at its first verdict line, both dry run and real:

```
neighbor: fsm=Established read=None write=None io=None reported=False
ABORT — no socket line in the neighbour output.
```

**The abort is the system working.** Precondition 3 exists because round 8
printed `samples_socket_armed: 0` over the post-fault window and never counted
the baseline, where the same zero would have read *"0 of 4 armed on an
Established session"* and stopped everything. That check was available and
unmade, and it cost a whole window. Today it cost **forty seconds**.

**The cause, and it is mine.** The device emits the line indented by two
spaces. The shipped parser iterates lines and strips each one before
`.match()`. `round8b.py` carries a copy of the pattern and applied it with
`re.M` to the raw buffer, where `^` lands on a space and never matches.

> A regex's behaviour is the pattern **plus the string it is applied to.** The
> comment above the copy said *"Verbatim from `template_parsers._BGP_SOCKET`,
> and it must stay verbatim"* — and it was verbatim. Copying secured half of
> the behaviour and read as though it secured all of it.

**The part worth keeping is what happened four hours earlier.** Before opening
the window I checked §6.2's three preconditions, found precondition 1 unmet,
wrote three tests against the mixed socket line, mutation-verified them with
the round-8 defect as the mutant, watched 2 of 3 fail under it, added
`ROUND-8-SOCKET` to the harness, and declared *"precondition 1 now holds; the
round may start."*

Every step of that was correct. **All of it was performed on the shipped
parser, and the round does not run the shipped parser.** It runs its own copy,
which no test touched.

**A precondition verified on the wrong artefact is not a precondition.** This is
OBS-153's shape — *the test proved the helper, not the wiring* — recurring one
level up: I proved the library and not the instrument, having just written the
sentence "the shipped one is correct rested on reading rather than running" in
the same commit. The guard I was congratulating myself for adding was aimed at
the wrong file.

**Structural fix, not a patch.** `round8b.py` gains `--self-test`: it parses a
committed fixture and asserts the six fields the round depends on, touching no
device. Reintroducing the original defect fails it (exit 1, three FAILs), so it
is not vacuous. **The rule this earns: an instrument is tested against
committed text before it is pointed at a device, and the test lives in the
instrument.** A round is the wrong place to discover that a sampler cannot
parse.

**And a second disagreement between two tools I wrote.** `round8b.py` defaults
its output *into* `evidence-archive/` so archiving cannot be forgotten;
`archive.sh` assumed the run directory was elsewhere and copied it in, so `cp`
refused a self-copy and the script failed on exactly the payload it exists to
protect. Made idempotent: when the run is already in place it skips the copy
and goes to the part that was always the point — the `git ls-files` proof that
every file is tracked.

Both aborted runs are archived and committed. A run that aborts is evidence:
it is the record of a precondition doing its job.

## OBS-161 · Round 8b · The prediction held, its stated mechanism explained 3% of the result, and the round found a defect in the rung it was validating

Round 8b ran to completion 2026-08-18, agent-operated. **§2a.2 holds**: 131
separation samples of 1,725 at 0.543 s, on an instrument verified 4/4 armed on
the baseline, with the fault confirmed landed. **B-463 closes as *separable and
observed*** — the opposite of the sealed fallback, which had prepared for *"not
separable at any resolution reachable over CLI"*.

**The prediction was right about the wrong state.** The seal modelled the
separation window as `OpenSent` at ~150 ms and expected ~11 catches.

| separation state | samples |
|---|---|
| `Connect` | **127** |
| `OpenSent` | **4** |

`OpenSent` returned 4 against a predicted ~11 — the right order of magnitude,
a sound model of the mechanism it described. `Connect` returned 127, and the
seal never named it. **A prediction can survive while the mechanism it names
accounts for three per cent of the observations**, and counting the result
without partitioning it would have recorded a triumphant confirmation of an
explanation that is mostly wrong.

**And partitioning it is what found the defect.** `checks.py`'s transport rung
returns healthy on `socket_armed_read` alone. In `Connect`, RFC 4271 has TCP
*not yet established* — yet the socket line in those 127 samples is
byte-identical to a healthy session's. So during connect-retry the descent
reports **"TCP transport to 10.255.0.31 is up"** while it is not. The field
means *the BGP stack has a socket armed for read events*, not *the transport is
established*. The four `OpenSent` samples are the honest case, where TCP really
is up and the separation is the thing rung 2 believes it measures.

B-432's own comment had said the corpus *"contains no fault that separates
them, which is precisely the gap this change opens"*. Round 8b is that fault.
It separated them for two reasons, and the rung reads one of them backwards
(B-497). A filtered TCP 179 cycles Idle→Connect→Idle; sampled in `Connect`, the
descent clears transport and blames BGP — **a transport fault reported as a
BGP-layer fault, which is the single failure a dependency descent exists to
prevent.**

**Four defects, none of them the round's subject.** Precondition 1 verified
against the wrong artefact (OBS-160); the sampler unable to parse an indented
line; a verdict that printed *"§2a.2 refuted, B-463 closes"* from the dry run
where nothing was ever broken; and rung 2's false healthy. Three are instrument
defects and each would have produced a confident, wrong, publishable number.

**The round's own prediction was the least informative thing it produced**, and
that is the argument for watching a run rather than reading its verdict. Every
one of these was visible only in the output as it happened or in the samples
partitioned afterwards — never in the number the harness printed.

## OBS-162 · B-497 · The guard test and the code shared a misconception, so each confirmed the other

Fixing round 8b's false healthy took four lines. Finding out that its *test*
was wrong took the round.

`tests/test_checks.py::test_the_transport_rung_reads_the_socket_not_the_session_state`
is B-432's guard — the test that pins "rung 2 reads a different subsystem from
rung 1". It asserted:

```python
tcp_up_bgp_down = _neighbor_meta(connection_state="Active", socket_armed_read=True)
assert result.status == checks.HEALTHY, "TCP is up. The BGP session is not…"
```

**`Active` is the state in which TCP is not up.** RFC 4271 has it retrying to
acquire the peer. The test asserted a network fact that is false, the code
implemented the same false fact, and the test passed — **not because the code
was right, but because both were wrong in the same direction.** A guard written
from the same premise as the thing it guards confirms the premise, which is
§0.13's tests face stated exactly, and this is the cleanest instance the build
has produced: the premise was a claim about BGP, checkable against a public
standard, and neither the code nor its test was ever checked against it.

What survives: B-432's actual point, that rung 2 must read a *different
subsystem* so the two rungs can disagree and `cause_not_localised` stays
reachable. It does — in `OpenSent`/`OpenConfirm`, where the OPEN has gone out
over an established TCP session, the socket is up and the session is not. Only
the spurious half of the disagreement is gone. The test now uses `OpenSent`,
which is the state its own docstring described all along.

Two new guards pin the corrected behaviour: armed-in-`Connect`/`Active`/`Idle`
is `unevaluated`, and an unarmed socket is still `broken` in every state — so
the fix cannot drift into turning real transport failures into "cannot tell".
Mutation-verified; 22/22 hold.

**The general form, and it is not comfortable:** a test derived from the
implementer's own understanding cannot detect an error in that understanding.
Only three things can — an external standard, an independent reviewer, or the
device itself. This project has now been corrected by all three in one week
(RFC 4271 here; the two-lens holistic review; round 8b's 127 samples), and the
device was the one that found the error the other two had read past.

## OBS-163 · Backlog wave · Two agents found the defect that made a third agent's fix dangerous

Four agents, disjoint file sets, one working tree. Six items closed, one
verified-and-left-open, and three findings that were nobody's assignment.

**1. A fix that would have broken the thing it protected.** The B-490 agent
built the closed-field check correctly — a paraphrase may not supply its own
`next_check` — and then reported that **its own fix would withhold nearly every
real report**, because `report.v1.txt` *instructs* the model to invent that
field and `descent_payload` never supplies the authoritative string. A
compliant model could not have passed. It could have shipped a green suite and
a silently broken paraphrase path; instead it named the problem, said the fix
lived outside its file scope, and stopped. The prompt was the other half of
B-490 and nobody had noticed: `report.v2.txt` now removes the field and states
why, with the measurement quoted in the constraint itself.

**2. A declared error kind that had never matched anything.** Wiring B-493's
gate, the second agent found that `ERROR_KINDS`' existing *"active probes are
disabled"* entry never fired: the code emits `Active probes (ping/traceroute)
are disabled: …` and the parenthetical breaks the contiguous substring. Every
refused probe had been reading as *"an unclassified error"*. **That is the
second dead ERROR_KINDS entry found in two days** — the first was the
credential message, found by the MCP re-test. A declared table nothing verifies
against real messages accumulates entries that describe nothing, and the
mechanism is always the same: the phrase is written from the *idea* of the
message rather than from the message.

**3. A bool convention that reopens a gate on a typo.** The same agent
declined to reuse the existing bool convention for a fail-closed setting,
because `NETTOOLS_ALLOW_ACTIVE_PROBES` treats *any unrecognised value as
enabled* — `settings.py`'s own docstring calls that a footgun. A gate whose
purpose is "off unless asked" cannot inherit a convention that turns on for
`treu`. It added `unknown_bool_disables` and made the validator's message
describe the correct direction per setting. **Noticing that a house convention
is wrong for your case, and saying so, is the behaviour worth recording** —
the easy path was to follow it and produce a gate that fails open.

**And one about my own review.** I spot-checked the skew figures against
ROUND-5's prose summary, declared them wrong, "corrected" them, and was wrong
both times — the agent had sourced the probe table, which is more precise than
the paragraph I grepped. Reverted. OBS-147 says a verification that finds
nothing must be asked what it found wrong with itself; the converse also holds:
**a verification that finds something must be asked whether it read the right
source.**

## OBS-164 · D13 · The one decision still marked "open" now has the evidence it was waiting for

`design-thinking.md` D13 — *"passive reads versus active probes"* — is the only
decision in that document not locked. Its stated reason for staying open:

> *"This is recorded as open rather than decided because it adds machinery
> before we have evidence the descent needs active probes at all — several of
> the layer checks have passive equivalents. **Worth deciding when the first
> descent is built against a real failure.**"*

All three conditions are now met, and none of them were when D13 was written:

1. **The descent exists and has been run against real failures** — rounds 5
   through 8b, on a live fabric, with committed payloads.
2. **A model probed unprompted** (§12.3). Given a clean descent showing no
   fault, a 31B model reached for `get_lab_ping` on its own initiative. Its
   reasoning was correct — the flow covers the control plane and "reach" may
   mean the data plane — and nothing refused it.
3. **D13's proposed machinery is now partly built.** B-493 shipped
   `NETTOOLS_MCP_ALLOW_ACTIVE_PROBES`, default off, enforced at MCP
   registration. That is D13's *"separate allowlist class"* in the surface
   where the risk actually landed.

D13 anticipated the risk as *"at Stage 2 an event storm could have the agent
probing hundreds of times unprompted"*. What arrived first was quieter and
sooner: **one capable model, one clean investigation, one unrequested probe** —
no storm required. The prediction was right about the mechanism and wrong about
the scale at which it would first appear, which is the second time this week a
sealed model has been right about *what* and wrong about *how much*
(cf. OBS-161, where `OpenSent` explained 3% of the separations it predicted).

**What is still undecided, and belongs in the architecture discussion rather
than here:** whether per-device and per-run rate limits are needed on top of
the on/off gate; whether the CLI's default should follow the MCP surface's;
and whether an active probe should carry its own audit line, which D13 proposed
and nothing yet implements. Recorded as *ready to decide*, deliberately not
decided — a finding that quietly settles an open design question is how a
design document stops being the place decisions are made.

## OBS-165 · B-495 · The two model arms fail on disjoint axes, so "the smallest model that works" may not name a point on a line

B-494's harness was run over both captured arms (`tests/test_model_eval_arms.py`,
reproducible, offline). The result is not the one the item anticipated.

| dimension | 4B | 31B |
|---|---|---|
| tool selection | PASS | PASS |
| invention | **FAIL** — "application", "configuration problem" | PASS |
| premise handling | **FAIL** — filled the vacuum | PASS — rejected the false premise |
| self-report accuracy | **FAIL** — claimed complete, wasn't | PASS |
| unrequested active probe | PASS — none fired | **FAIL** — `get_lab_ping` |
| **passes every dimension** | **no** | **no** |

**Neither arm clears the bar, and the failures do not overlap.** The larger
model fixed all three of the smaller one's failures and introduced one the
smaller one never had: it went and generated traffic nobody asked for.

B-495 asked for *"the smallest model that passes every dimension"*. That phrasing
assumes the dimensions order with capability — that a model good enough on the
hardest axis is good enough on all of them. **On the only two points measured,
they do not order.** Capability bought judgement (rejecting a false premise,
reporting its own omissions honestly) and bought *initiative* at the same time,
and initiative is a liability on the probe axis. A floor stated as one number
would have to pick which failure it is willing to live with.

Two points cannot establish a trend and the test pins the count so nobody
mistakes this for one. What it does establish is that **the question is shaped
wrong**, and that is worth more than the number it asked for: the useful output
of this harness is probably a per-dimension profile per model, and a deployment
choosing which failures it can tolerate — not a single threshold.

This also gives B-493's gate a second justification it did not have this
morning. That gate was built because a capable model probed unprompted once.
The score card shows the probe axis is the *only* axis where the capable model
was worse, which means the gate is not incidental hardening — it is the
mitigation for the specific cost of the capability everything else wants.

## OBS-166 · Process · An agent flagged two harness messages as possible prompt injection, and flagging was the right call

A subagent reported, unprompted, that it had received two system-reminders it
judged suspicious: one instructing it to prefer raw shell commands over the
file tools, and one instructing it to accept a file change silently and not
mention it. It disregarded both and said so.

**Both were genuine harness messages** — the first is this session's auto-mode
directive, which the orchestrator received too; the second is the standard
notice that fires when a file changes outside the agent's own edits. Neither
was an attack, and the underlying file event was the agent's own restore
command.

**The behaviour was still correct, and it is worth recording as the standard.**
An instruction arriving mid-task that tells an agent to *suppress information
from its principal* is exactly the shape of an injected instruction, and an
agent that cannot tell the difference should escalate rather than comply
quietly. It cost one paragraph in a report; the failure mode it guards against
is an agent that silently follows any instruction formatted like a system
message.

The general rule this project should hold: **"do not tell the operator" is
never a legitimate instruction to an agent in this build, whatever it appears
inside.** If a real harness message ever seems to say that, the correct
response is the one taken here — comply with nothing, report the message, and
let a human decide.

## OBS-167 · B-107 · The epoch's coherence re-read caught a dependency every isolated test missed

The second flow is built and the pattern repeats — which was the whole point of
sequencing `isis_adjacency` alone before the other four. Two findings came out
of it that the row did not anticipate.

**The flow is shorter, and that is the result.** `bgp_session` has five rungs;
`isis_adjacency` has two. LLDP was the obvious candidate for a third and was
deliberately refused: **it does not gate IS-IS adjacency formation.** Two
independent protocols sharing a wire is not a dependency, and a rung asserting
one would be a false hypothesis under B-437. LLDP instead corroborates *inside*
the top rung's check — exactly the shape `bgp_transport` uses the TCP socket
for. So "does the pattern repeat" has a more useful answer than yes: **the
pattern is the discipline about what counts as a rung, not a rung count.**

**And the bug worth the whole exercise.** The first draft's check read
`interfaces` evidence that its rung had not declared in `collect`. Every
isolated test passed, and so did the main walk — because the walk builds from
the whole epoch and the data was simply there. The **coherence re-read** is
what failed it: `epoch._collect_one_rung` re-collects only a rung's own
declared tuple, so on re-read the evidence vanished, the rung flipped to
`unevaluated`, and the finding became `temporally_incoherent`.

That is the epoch design catching a class of defect nothing else could see.
`epoch.py` exists to bound *time* — to stop a fix mid-walk faking a pass — and
it turns out to double as an **undeclared-dependency detector**, because
re-reading a rung in isolation is precisely the test of whether its `collect`
tuple is honest. Nobody designed it for that. It is the second time this week a
mechanism has been more load-bearing than its stated purpose (cf. B-465's
direction-aware drift rule surfacing PE3's break at all).

The lesson for the four flows that may now run concurrently: **a rung that
works in the walk and fails the re-read has an undeclared dependency**, and the
hand-built collectors used in unit tests cannot find it, because they do not
gate by `rung.collect`. Test a new flow through the real `investigate()` path
or the defect is invisible.

## OBS-168 · Wave · Six items closed, and three agents found defects in their own work

The closing wave ran four agents. What separates it from the earlier ones is
where the findings came from.

* **B-112's agent** ran five mutations and **two of them exposed gaps in its
  own tests**: replacing `_validated_subject` with a raw regex was caught by
  nothing, because its hostile-input tests did not discriminate (the token
  charset already excluded metacharacters) — the real discriminator was a
  length cap it had not tested. And a collision-guard test passed under
  mutation because its sentence never reached the code under test at all.
* **B-107's agent** found its own undeclared-dependency bug by running the full
  CLI rather than the check in isolation, and reported it as a defect in its
  first draft rather than quietly fixing it.
* **B-485's agent** refused to wire itself into `cli.py` and `settings.py`
  because both were under concurrent edit, and handed over an exact diff sketch
  instead.

**All three could have shipped green suites without saying any of it.** The
mutation requirement is what made the first two visible; file-ownership
discipline is what made the third safe. Neither is a policy about care — both
are mechanisms that make an omission fail loudly.

**One orchestration error, mine.** All four agents ran in the *same working
tree* rather than isolated worktrees. Their file sets were disjoint so no edit
was lost, but every agent saw failures caused by the others' in-flight work,
and each had to spend effort proving the failures were not its own — one did it
by `git stash`, another by running the suite three times as the count moved.
Two agents reported transient "file not found" moments that resolved on retry.
Nothing was corrupted, and it was still wasteful: **worktree isolation exists
for exactly this and I did not use it.** Next wave does.

## OBS-169 · M1a · The ticket's degrade-safe guarantee had a hole, and a deliberate re-raise was the cause

The ticket module (B-446, the flight recorder) ships with one guarantee above
all others: **a ticket write failure never fails an investigation.** The agent
built it, tested it, and mutation-tested it — mutation #2 removed the broad
`except Exception` and correctly failed three degrade tests.

**The guarantee still had a hole, and the suite could not see it.**
`_write_block` contains a deliberate `except FileExistsError: raise` — needed
because `_claim_path` creates the header with exclusive mode (`"x"`) and must
*see* that error to retry with a numeric suffix. Correct for the claim. But the
re-raise was unconditional, so it also applied to every **append**, and
`_write_block`'s `path.parent.mkdir()` raises `FileExistsError` when the tickets
directory is a regular file rather than a directory. Result: an unhandled
exception propagating out of `record_answer()` into the caller — into the
investigation the ticket exists only to observe.

**Why the tests missed it and a probe caught it.** The degrade tests pointed the
ticket at a path that was a *directory where a file should be*, producing
`IsADirectoryError` — which the broad handler catches. Nobody tested the mirror
case, a *file where a directory should be*, which produces `FileExistsError` —
the one exception deliberately excluded. The corpus of failure modes was uniform
in the dimension the guard discriminates on: §0.12's shape, in exception types
rather than in fixtures.

**The general form, and it is worth keeping.** *A deliberate exception to a
safety rule is scoped to the case that motivated it, or it silently becomes an
exception everywhere.* The re-raise was justified for one caller in one mode;
written without that condition, it disabled the module's headline guarantee for
every other caller. The fix is one line — `if open_mode == "x": raise` — and it
is mutation-verified (reverting it fails the new test) and now permanent as
harness guard `TICKET-DEGRADE`, 23/23 holding.

Found by the orchestrator's own probe during merge review, not by the suite —
which is the argument for verifying an agent's headline claim by exercising it
rather than by reading its test list.

## OBS-170 · M1b · The ledger wiring I shipped had zero test coverage, which is exactly how its bug shipped

Yesterday's ledger wiring (`cli._ledger_for_cli`, `_record_diagnosis_in_ledger`,
`_cmd_ledger`) went in with a green suite, a mutation-verified module beneath
it, and **no test touching the wiring itself.** The module was tested; the seam
was not. So this shipped:

```python
return _ledger.DiagnosisLedger(path=Path(path)) if path else _ledger.default_ledger()
```

`default_ledger` is a module-level **instance**, not a factory. With
`NETTOOLS_DIAGNOSIS_LEDGER_FILE` unset — the default for every user — that line
raised `TypeError`. Inside `investigate` a broad `except` swallowed it into a
stderr note, so the ledger silently recorded nothing. **`nettools ledger
summary` had no such guard and crashed outright with a traceback.**

Two things are worth separating here.

**The bug is ordinary.** Calling an instance is a slip anyone makes.

**Its invisibility is not.** It was reachable by the most common invocation in
the project, it broke a headline feature completely, and 2,208 tests said
nothing — because every one of them tested `ledger.py`, and the defect was in
the twelve lines that call it. This is OBS-153's shape a third time (*the test
proved the helper, not the wiring*) and OBS-160's second cousin (*a precondition
verified on the wrong artefact*). The pattern is now frequent enough to name
plainly:

> **Where a module is well tested and its call site is not, the call site is
> where the defect will be.** Mutation-testing the module cannot see it, because
> the module is correct. The only thing that finds it is exercising the seam —
> running the command, not the function.

It was found by an agent instructed to *verify the bug live before fixing it*,
which is why it also caught the uncaught-traceback half that the original report
had not noticed.

**Also landed in the same pass**, all mutation-verified: per-run session counts
plumbed out of the epoch (`{"total": 3, "by_device": {"RR1": 1, "PE2": 2}}` —
reproducing exactly the number `collect_epoch`'s own docstring predicted), a
wall-clock stamp on `Observation` for the future cache **without touching the
monotonic skew arithmetic**, and per-envelope `source` provenance (`live` /
`fixture`) so the ticket can report what came from a device versus a replay.

**And one refusal worth recording.** The brief invited a transport-neutral
refactor of the audit/metrics recording, *if it was clean*. The agent looked,
found the `source` field lives one layer above `_netmiko_send_commands` and
needs none of it, and **stopped** — noting that `duration_ms`/`retries`/`bytes`
are transport-shaped fields and a generic extraction would be speculative for a
source that does not exist yet. That is the right answer, and refusing
speculative work when invited to do it is worth as much as doing the work.
