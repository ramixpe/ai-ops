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
| Q-001 | T-002 | Does `reasoning_split: true` fully suppress `<think>` in `content`? If not, is a stripping step acceptable, or should the gate use the Anthropic-compatible route instead? | Yes — gate depends on it | Open |
| Q-002 | T-004 | Is syslog-ng shipping to Loki, and do IOS-XR mnemonics survive into a queryable label? | No — affects Stage 2 only | Open |
| Q-003 | T-005 | Does Alertmanager have a webhook receiver, and can it replace n8n as the Stage 2 trigger? | No — Stage 2 | Open |
| Q-004 | T-006 | What is the subject naming scheme for an L3VPN service object? | No — flow not in MVP-0 | Open |
| Q-005 | T-020 | What error-counter threshold should `interface_state` treat as broken? | No — default chosen, needs review | Open |
| Q-006 | T-025 | Does the descent's stopping rung match what a network engineer would conclude by hand from the same fixtures? | **Yes — this validates the architecture** | Open |
| Q-007 | T-035 | Telegram or Mattermost? Hosted means device names, IPs and RCA text leave the estate; self-hosted keeps them in. Decide before implementing — only one provider gets built. | Yes for T-035 | Open |
| Q-008 | T-035 | Which host runs `nettools` in the target deployment, and does it have outbound egress to the chosen channel? | Yes for T-035 | Open |

---

Task status lives in `TRACKER.md`, not here. This file records *what was learned*; the tracker records *what was done*.
