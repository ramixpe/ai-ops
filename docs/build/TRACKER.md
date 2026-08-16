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
| Last updated | 2026-08-16 |
| Current task | T-024 |
| Halted? | no — Q-013 and Q-017 resolved by the operator |
| Operator decisions pending capture | **Lab must stay healthy until T-011** — `healthy` then `broken` captured in one coordinated window (OBS-019) |

**Baseline environment (T-001).** Python 3.13.11 · Linux 6.8.0-136 x86_64 · ruff 0.16.3 · pytest 9.1.1 · 63 pip packages · `agent-nettools` 0.2.0 · repo at `/home/rami/ai-agent-ops/ios-xr-nettools`. CI pins Python 3.11 — see OBS-004.

**Regression line.** Fewer than 550 passed, or any skip outside `tests/test_live_lab.py`, is a regression introduced by this build.

---

## Part 1 — Discovery

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-001 | Baseline verification | DONE | opus-5 | `f309322` | OBS-003, OBS-004 | Green: 550 passed / 4 skipped / lint clean |
| T-002 | MiniMax API contract test | DONE | opus-5 | `f1ecb55` | OBS-005…008 | **Q-001 resolved**: `reasoning_split` works, no stripping step. 5/5 deterministic; tool calling available |
| T-003 | Wire MiniMax as a provider | DONE | sonnet-5 (opus-5 spec + review) | `c754900` | OBS-010…012 | Responses API, not chat/completions — see Q-010. 567 passed |
| T-004 | Loki discovery | DONE | opus-5 | `c4cc236` | OBS-013…015 | **Q-002 resolved**; new Q-011. Historical axis much weaker than assumed — see OBS-014 |
| T-005 | Alertmanager / Prometheus discovery | DONE | opus-5 | `199d027` | OBS-016…018 | **Q-003 resolved**; new **Q-012 — lab is no longer broken** (OBS-017), resolve at T-007 |
| T-006 | L3VPN discovery | DONE | opus-5 | `bb5786a` | OBS-023…025 | **Q-004 answered**: `<pe>:<vrf>`; `<vrf>:<rd>` eliminated — RD reused across PEs |
| T-007 | Fixture gap analysis | DONE | opus-5 | `92bf1d6` | OBS-026…028 | Manifest written; ~370-400 files over 2 labels. Over-captures to be Q-013-proof |
| T-008 | Parser library decision | DONE | opus-5 | `8875b4c` | OBS-029, OBS-030 | **TTP**, 18/18 equivalence. Genie rejected. New Q-014 (core dep, not extra) |
| T-009 | Docs scaffold | DONE | opus-5 (see OBS-031) | `2daa214` | OBS-031 | Already built by install-docs.sh; fixed a stale map. All 23 doc links resolve |

## Part 2 — Parsing

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-010 | `template_parsers.py` skeleton and contract | DONE | opus-5 | `6a56734` | OBS-032, OBS-033 | Contract + §0.10 accounting helper. 27 new tests, 594 passed |
| T-011 | Extend capture to templates | **DONE** | opus-5 | `24fc1f4`, `2fd50bf`, `f8c5a9a` | OBS-034…039, 049, 050 | `run_templates` batching done; **`healthy` captured+verified (217 files)**. `broken` NOT captured — one uplink does not isolate PE2 (Q-016) |
| T-012 | `bgp_neighbor` parser | DONE | sonnet-5 (opus-5 spec + review) | `5360adc` | OBS-040, OBS-041 | 41 fixtures round-trip, unaccounted=[]. §0.10 verified by mutation. 662 passed |
| T-013 | `route` parser | DONE | sonnet-5 (opus-5 spec + review) | `d690246` | OBS-042 | 44 fixtures; a third output shape found that the spec missed. 719 passed |
| T-014 | `interface` parser | DONE | sonnet-5 (opus-5 spec + review) | `04a0bf1` | OBS-043, OBS-044 | 45 fixtures, 3 shapes. Line-down omits error counters — binds T-020. 776 passed |
| T-015 | `logging` parser | DONE | sonnet-5 (opus-5 spec + review) | `b45d4d2` | OBS-045 | 1,800 entries; mnemonic split verified by recomposition. Zero ignores. 804 passed |
| T-016 | `ping` parser | DONE | sonnet-5 (opus-5 spec + review) | `cd07101` | OBS-046 | 10 fixtures; RTTs None (not 0) at 0%. 833 passed |
| T-017 | `traceroute` parser | DONE | sonnet-5 (opus-5 spec + review) | `d4f9bf5` | OBS-047 | 9 fixtures; `completed` trap avoided. 861 passed |
| T-018 | Attach parsed data to `run_template` | DONE | opus-5 (see OBS-048) | `62d218b` | OBS-048 | **Blocking gap closed.** 158/158 fixtures clean. 871 passed |

## Part 3 — Checks and descent

| Task | Title | Status | Model | Commit | Findings | Notes |
|------|-------|--------|-------|--------|----------|-------|
| T-019 | `checks.py` and `CheckResult` | DONE | opus-5 | `1437059` | OBS-051 | Contract + "absence is unevaluated" rule. 17 tests, 1047 passed |
| T-020 | The five checks | DONE | sonnet-5 (opus-5 spec + review) | `fcc2210` | OBS-052, OBS-053 | **Q-005 closed** (rate, not total). 1069 passed. Divergence from `health.py` recorded for T-021 |
| T-021 | Agreement test with `health.py` | DONE | opus-5 (HALT-sensitive) | _next commit_ | OBS-054 | **No disagreement. No HALT.** 92 comparisons, 1161 passed |
| T-022 | `flows.py` registry and dataclasses | DONE | opus-5 | _next commit_ | OBS-055, OBS-056 | `DeviceScope` + `Aggregation` on `Rung`; 7 declared, 2 implemented |
| T-023 | The `bgp_session` descent | DONE | opus-5 | _next commit_ | OBS-056 | 5 rungs, scopes settled by measurement. 1181 passed |
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
| **M1 — Discovery complete** | T-009 done; all discovery docs written; Open Questions populated | ☑ **2026-08-15** |
| **M2 — Parsing unblocked** | T-018 done; template results carry parsed data; all §0.10 completeness tests green | ☑ **2026-08-16** |
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
| 3 | T-022 | Two contract questions before the `Rung` dataclass is fixed: device scope (Q-013) and the walk stopping rule (Q-017), the latter found by measuring the ladder against the `broken` label. | Q-013, Q-017 | **Yes** — operator resolved both 2026-08-16; T-024's rule confirmed as a plan defect and corrected (OBS-056) |
| 2 | T-011 | Second scoped waiver: shut PE2 `Gi0/0/0/0` **and** `Gi0/0/0/1` in one commit to capture the `broken` label. | Q-016 | **Yes** — granted, exercised 07:41–07:51Z 2026-08-16, discharged. Fabric verified restored three ways (OBS-049) |
| 1 | T-011 | Asked to execute a device configuration change (shut PE2 `Gi0/0/0/0`). §0.11 makes any device state change an absolute HALT, under a standing instruction that overrides later session instructions. Script written and verified; not run until authorised. | Q-015 | **Yes** — operator granted a scoped one-action waiver; exercised 23:01-23:07Z and discharged. Fabric verified restored (OBS-038) |

---

## Session log

One row per working session, so elapsed effort is visible without reading git.

| Date | Tasks attempted | Tasks completed | Halted? | Notes |
|------|-----------------|-----------------|---------|-------|
| 2026-08-15/16 | T-001…T-018 | **T-001…T-018 all DONE** | HALT raised + waived | Required reading done; OBS-001/OBS-002 logged for the pre-plan install gap; baseline green; MiniMax contract verified and Q-001 resolved; MiniMax provider wired (567 tests); Loki + alerting discovery done; found the lab has been repaired (Q-012); L3VPN mapped; capture manifest written; TTP chosen; **M1 + M2 reached**; all six parsers done; blocking gap closed |
