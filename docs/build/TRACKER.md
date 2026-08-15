# Build Tracker

Progress record for the investigation-layer build. **Authoritative on status.** If `BUILD-PLAN.md` and this file disagree about whether a task is done, this file wins and the discrepancy is itself a finding.

Maintained continuously, not at the end. Updated after every task by Opus 5.

- Plan: `BUILD-PLAN.md`
- Findings: `FINDINGS.md`

**Status values:** `TODO` · `IN-PROGRESS` · `DONE` · `BLOCKED` · `SKIPPED` · `PARTIAL`

**A task is `DONE` only when all of these hold:**
1. Its acceptance criteria are met and judged by Opus 5.
2. `make test` and `make lint` are green.
3. At least one entry exists in `FINDINGS.md` referencing the task.
4. The commit is made with the task ID in the message.

---

## Run header

| Field | Value |
|---|---|
| Branch | `feat/investigation-layer` |
| Baseline commit | `6629a2c` — `docs: add design and build documentation pack` |
| Baseline test count | **550 passed, 4 skipped, 0 failed** (554 total; the 4 are `live_lab`, skipped by default) |
| Run started | 2026-08-15 |
| Last updated | 2026-08-15 |
| Current task | T-005 |
| Halted? | no |

**Baseline environment (T-001).** Python 3.13.11 · Linux 6.8.0-136 x86_64 · ruff 0.16.3 · pytest 9.1.1 · 63 pip packages · `agent-nettools` 0.2.0 · repo at `/home/rami/ai-agent-ops/ios-xr-nettools`. CI pins Python 3.11 — see OBS-004.

**Regression line.** Fewer than 550 passed, or any skip outside `tests/test_live_lab.py`, is a regression introduced by this build.

---

## Part 1 — Discovery

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-001 | Baseline verification | DONE | opus-5 | `f309322` | OBS-003, OBS-004 | Green: 550 passed / 4 skipped / lint clean |
| T-002 | MiniMax API contract test | DONE | opus-5 | `f1ecb55` | OBS-005…008 | **Q-001 resolved**: `reasoning_split` works, no stripping step. 5/5 deterministic; tool calling available |
| T-003 | Wire MiniMax as a provider | DONE | sonnet-5 (opus-5 spec + review) | `c754900` | OBS-010…012 | Responses API, not chat/completions — see Q-010. 567 passed |
| T-004 | Loki discovery | DONE | opus-5 | _next commit_ | OBS-013…015 | **Q-002 resolved**; new Q-011. Historical axis much weaker than assumed — see OBS-014 |
| T-005 | Alertmanager / Prometheus discovery | TODO | opus-5 | | | Q-003, non-blocking |
| T-006 | L3VPN discovery | TODO | opus-5 | | | Q-004 |
| T-007 | Fixture gap analysis | TODO | opus-5 | | | |
| T-008 | Parser library decision | TODO | opus-5 | | | TTP vs Genie |
| T-009 | Docs scaffold | TODO | sonnet-5 | | | |

## Part 2 — Parsing

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-010 | `template_parsers.py` skeleton and contract | TODO | opus-5 | | | contract task |
| T-011 | Extend capture to templates | TODO | sonnet-5 | | | HALT if lab unreachable |
| T-012 | `bgp_neighbor` parser | TODO | sonnet-5 | | | §0.10 completeness |
| T-013 | `route` parser | TODO | sonnet-5 | | | §0.10 completeness |
| T-014 | `interface` parser | TODO | sonnet-5 | | | §0.10 completeness |
| T-015 | `logging` parser | TODO | sonnet-5 | | | mnemonic field — Stage 2 |
| T-016 | `ping` parser | TODO | sonnet-5 | | | §0.10 completeness |
| T-017 | `traceroute` parser | TODO | sonnet-5 | | | §0.10 completeness |
| T-018 | Attach parsed data to `run_template` | TODO | sonnet-5 | | | closes the blocking gap |

## Part 3 — Checks and descent

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-019 | `checks.py` and `CheckResult` | TODO | opus-5 | | | contract task |
| T-020 | The five checks | TODO | sonnet-5 | | | Q-005 threshold |
| T-021 | Agreement test with `health.py` | TODO | sonnet-5 | | | HALT on disagreement |
| T-022 | `flows.py` registry and dataclasses | TODO | opus-5 | | | contract task |
| T-023 | The `bgp_session` descent | TODO | sonnet-5 | | | |
| T-024 | `descent.py` walker | TODO | opus-5 | | | semantics task |
| T-025 | **Acceptance test — RR1 → 10.255.0.12** | TODO | opus-5 | | | Q-006 · milestone |

## Part 4 — Prompts, grounding, MVP-0

T-035 is optional and does not gate the definition of done.

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-026 | Prompt library scaffold (GRACE) | TODO | opus-5 | | | |
| T-027 | `report` prompt | TODO | opus-5 / sonnet-5 | | | |
| T-028 | `correlate` prompt | TODO | opus-5 / sonnet-5 | | | depends on T-004 |
| T-029 | `grounding.py` | TODO | sonnet-5 | | | |
| T-030 | `investigation.py` runner | TODO | sonnet-5 | | | no gate in MVP-0 |
| T-031 | CLI wiring | TODO | sonnet-5 | | | |
| T-032 | End-to-end offline test | TODO | sonnet-5 | | | no network |
| T-033 | Live lab run | TODO | opus-5 | | | Q-006 confirmation |
| T-034 | Documentation update | TODO | sonnet-5 | | | |
| T-035 | Report relay — outbound only | TODO | sonnet-5 | | | **optional fast-follow**; residency decision first |

---

## Milestones

| Milestone | Gate | Reached |
|---|---|---|
| **M1 — Discovery complete** | T-009 done; all discovery docs written; Open Questions populated | ☐ |
| **M2 — Parsing unblocked** | T-018 done; template results carry parsed data; all §0.10 completeness tests green | ☐ |
| **M3 — Architecture validated** | T-025 green offline, no model call, no lab, no API key | ☐ |
| **M4 — MVP-0 shipped** | Part 7 definition of done satisfied in full (T-001…T-034) | ☐ |
| **M5 — Team can see output** | T-035 done; a real investigation report reaches the team channel | ☐ |

**M3 is the one that matters.** It is the point at which the central claim — that the diagnostic ladder is deterministic end to end — is demonstrated rather than argued.

---

## Consultation log

Every Fable 5 consultation, per `BUILD-PLAN.md` §0.9. The finding carries the detail; this is the index.

| # | Task | Question (one line) | Accepted? | Finding |
|---|------|---------------------|-----------|---------|
| | | | | |

---

## Halt log

Every HALT under §0.11. A populated row here means the run stopped and a human is needed.

| # | Task | Reason | Question ID | Resolved |
|---|------|--------|-------------|----------|
| | | | | |

---

## Session log

One row per working session, so elapsed effort is visible without reading git.

| Date | Tasks attempted | Tasks completed | Halted? | Notes |
|------|-----------------|-----------------|---------|-------|
| 2026-08-15 | T-001…T-004 | T-001…T-004 | no | Required reading done; OBS-001/OBS-002 logged for the pre-plan install gap; baseline green; MiniMax contract verified and Q-001 resolved; MiniMax provider wired (567 tests); Loki discovery done |
