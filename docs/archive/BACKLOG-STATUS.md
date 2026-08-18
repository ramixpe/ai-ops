# Backlog status — verified, not reported

**Generated 2026-08-17 from `docs/build/BACKLOG.md` at `e018aab`.** Every row below was
parsed out of that file mechanically. Nothing is from memory, and nothing is from any list
supplied to me.

**98 items.** Every dependency is shown with the state its target *actually* has in the
reconciliation, resolved at generation time rather than copied from the row — which is how
§6's five stale dependency claims were found.

---

## Summary — read this first

| | |
|---|---|
| DONE claims checked | **36** |
| DONE claims that survived verification | **36** |
| DONE claims that changed state | **0** |
| Guardrails mutation-tested | **12** |
| Guardrails that proved vacuous | **0** |
| Defects found **in the verification harness itself** | **3** |
| Genuinely OPEN for **Part 1** (before publication) | **0** |
| Genuinely OPEN for **Part 2** (MVP-1) | **17** |
| OPEN but gated on something else | **3** |

> **Nothing changed state, and that is a result rather than a skipped pass — but it is a
> weaker result than it looks, and §7 says exactly how weak.**
>
> The reference check on all 36 DONE items passed. It proves every piece of evidence points
> at something that exists. **It does not prove the claims are true**, and a pass on that
> check alone would have been close to worthless. That is why §5 mutation-tests the guards.
>
> **What did change is not in the backlog at all.** Five dependency rows record a state
> their target no longer has (§6), and the mutation harness turned out to have three
> defects of its own — two wrong test files and a cache-invalidation gap that can produce
> a false *pass* (§5.2, §5.3). **Every defect this pass found was in the verifier, not in
> the thing verified**, which is worth stating rather than burying.

---

## 1. Frozen files — hash-compared to the T-001 baseline

`git rev-parse 6629a2c:<path>` against `git rev-parse HEAD:<path>`. **Not asserted.**

| File | Baseline blob | HEAD blob | Result |
|---|---|---|---|
| `tests/test_safety.py` | `840b24b53714` | `840b24b53714` | **IDENTICAL** |
| `tests/test_template_security.py` | `fa1af64bcb5d` | `fa1af64bcb5d` | **IDENTICAL** |
| `src/agent_nettools/platforms.py` | `ae2469781a92` | `ae2469781a92` | **IDENTICAL** |
| `src/agent_nettools/templates.py` | `a2dcc684f116` | `a2dcc684f116` | **IDENTICAL** |

**Checked again after every mutation in §5**, because that pass edits source files and a
restore failure there is the worst outcome this document could cause. All four identical
afterwards; `git status --porcelain` empty.

---

## 2. Rounds — `git ls-files`, not `ls`

| Round | Tracked files | Verdict |
|---|---|---|
| 1–4 | **0** | **NOT COMMITTED** — findings and metrics only, and they cannot be. §6.1d names this gap |
| 5 | 15 | **COMMITTED** |
| 6 | 0 | n/a — sealed, unrun |
| 7 | 6 | **COMMITTED** |
| 8 | 7 | **COMMITTED** — retroactively, at `2e12786` |
| 8b | 0 | n/a — sealed, unrun |

**Rounds 1–4 are the honest exception.** Their entries in `tests/test_rounds_regression.py`
assert what the finding logic does with a given rung vector. That is a true assertion and it
is **not** the claim that the vector is still producible from evidence.

---

## 3. DONE — 36 items, every one verified

The `Verified` column shows what actually resolved: `OBS-nnn✓` means the heading exists in `FINDINGS.md`; a SHA means `git cat-file -e` succeeded; a symbol means it was found in `src/`, `mcp_server/` or `tests/`.

| ID | Title | Evidence | Depends on | Last finding | Verified |
|---|---|---|---|---|---|
| **B-402** | Operator knowledge notes | OBS-103 | — | OBS-103 | ✅ OBS-103✓ |
| **B-403** | `checks.py` / `health.py` consolidation | OBS-123 | B-107 (**OPEN**) | OBS-123 | ✅ OBS-123✓ |
| **B-404** | Line accounting retrofit for `parsers.py` | OBS-123 | MVP-0 | OBS-123 | ✅ OBS-123✓ |
| **B-411** | HALTED — the stated mechanism does not exist (OBS-099). | OBS-123 | — | OBS-123 | ✅ OBS-123✓ |
| **B-413** | `TROUBLESHOOTING_PROMPT` carries the banned self-evaluation clause | OBS-059 | — | OBS-059 | ✅ OBS-059✓ |
| **B-420** | Coverage metadata, and grounding enforcement of absence claims | T-029a; verified OBS-124 | — | OBS-124 | ✅ OBS-124✓ |
| **B-421** | Prompt caching is not applied on the rendered-prompt path | OBS-102 | T-031 | OBS-102 | ✅ OBS-102✓ |
| **B-422** | `--format json` is not directly pipeable | `_note` writes to stderr, stdout carries the payload. **Independently re-verified 2026-08-17**: a `test_notifi | — | VERIFICATION §6 | ✅ _note✓, test_notifier.py✓ |
| **B-423** | CI job: run `--from-fixtures` in a clean environment | `.github/workflows/ci.yml` `offline-demo`: installs without the `llm` extra, asserts `.env` absent and 7 crede | — | VERIFICATION §6 | ✅ llm✓ |
| **B-424** | The correlation path has no citation check | OBS-081 | — | OBS-081 | ✅ OBS-081✓ |
| **B-425** | Token usage is not instrumented on the rendered-prompt path | OBS-123 | — | OBS-123 | ✅ OBS-123✓ |
| **B-428** | Forward consistency — the descent has no mechanism for concluding health | OBS-124 | — | OBS-124 | ✅ OBS-124✓ |
| **B-429** | A vacuous pass over a payload full of claims should be an error | OBS-083 | — | OBS-083 | ✅ OBS-083✓ |
| **B-430** | `bgp_transport` ignores `last_reset_reason` — the answer is already in the rec | OBS-097 | — | OBS-097 | ✅ OBS-097✓ |
| **B-431** | `EACH_PHYSICAL_INTERFACE` is a naming filter, duplicated three times, and empt | OBS-097 | — | OBS-097 | ✅ OBS-097✓ |
| **B-432** | `cause_not_localised` is unreachable for `bgp_session` as specified | OBS-124 | — | OBS-124 | ✅ OBS-124✓ |
| **B-433** | Audit what every check reads against what its inputs contain | OBS-100 | — | OBS-100 | ✅ OBS-100✓ |
| **B-434** | Review §0.10's ignore rules for diagnostic value | OBS-100 | — | OBS-100 | ✅ OBS-100✓ |
| **B-435** | `topology.py` compares LLDP device IDs against inventory labels, not configure | `topology.hostname_map` + `resolve_device`. Both false anomaly classes go to **zero** on t0; the classes survi | — | OBS-103 · OBS-139 | ✅ topology.hostname_map✓, resolve_device✓ |
| **B-436** | Evidence-epoch contract — collect once per device, reuse across rungs | 548e380 | — | OBS-108 | ✅ 548e380✓ |
| **B-437** | Rung discrimination criterion, binding on every future rung | OBS-121 | — | OBS-121 | ✅ OBS-121✓ |
| **B-438** | `pin_lab_golden_snapshot` performs a persistent write behind `_read_only_tool` | OBS-106 | — | OBS-106 | ✅ OBS-106✓ |
| **B-439** | Render the authoritative report deterministically from typed fields | 79c6777 | — | OBS-122 | ✅ 79c6777✓ |
| **B-441** | Report all three strata, and name the sampling frame | `MVP0-REVIEW.md:203` reports 3/3 on-path, 0/1 no-fault, 3/4 overall, and names the sampling frame | — | review §3.5 | ✅ MVP0-REVIEW.md:203✓ |
| **B-442** | `chaos-harness.md`: three datasets, estimand plan, stratum sizing | OBS-116 | B-452 (**BLOCKED**) | OBS-116 | ✅ OBS-116✓ |
| **B-451** | Round 5 — invoke during propagation, not after | f6ea6ec | B-436 (**DONE**) | OBS-109 | ✅ f6ea6ec✓ |
| **B-453** | Identifier containment — the intermediate grounding check available now | `check_identifier_containment`, wired into `ground_report`. 12 tests, **including two through `investigate()`* | — | OBS-122 · OBS-138 | ✅ check_identifier_containment✓, ground_report✓ |
| **B-454** | `temporally_incoherent` conflates a moved fabric with a slow collection | 6598df8 | B-436 (**DONE**) | OBS-122 | ✅ 6598df8✓ |
| **B-455** | Skew is (sessions − 1) × an ~8 s device-side login penalty | OBS-119 | B-436 (**DONE**) | OBS-119 | ✅ OBS-119✓ |
| **B-456** | Candidate: `no_fault_on_path` may be rare, and round 4 may never have been a c | 503b2ac | B-437 (**DONE**), B-431 (**DONE**) | OBS-121 | ✅ 503b2ac✓ |
| **B-457** | A systematic paraphrase-grounding regression is now invisible | OBS-126 | B-439 (**DONE**) | — | ✅ OBS-126✓ |
| **B-458** | Residual: a transport exception can embed device output in an error string | OBS-126 | — | OBS-113 | ✅ OBS-126✓ |
| **B-459** | Argument fabrication — validate a tool's arguments against observed reality be | ee733a2 | B-453 (**DONE**) | OBS-122 | ✅ ee733a2✓ |
| **B-460** | `state_pfx_rcd` holds two different fields under one CLI heading | OBS-120 | — | OBS-120 | ✅ OBS-120✓ |
| **B-461** | The unowned surface: a chat client restating our output is a boundary we do no | numbered rungs, 61af0d0 | B-439 (**DONE**) | OBS-117 | ✅ 61af0d0✓ |
| **B-462** | Round 7 — does a down interface persist in the route table as an LFA backup? | OBS-129 — round 7, 99 down-port samples, 0 persisting | B-456 (**DONE**) | OBS-121 | ✅ OBS-129✓ |

---

## 4. OPEN — 20 items

See §8 for the Part 1 / Part 2 split.

| ID | Title | Evidence | Depends on | Last finding | Verified |
|---|---|---|---|---|---|
| **B-101** | The reasoning gate | PLAN-V2 P2.1 | MVP-0 complete | OBS-124 | — |
| **B-102** | Candidate enumeration | reasoning gate, half 2. D7; PLAN-V2 P2.1 | B-101 (**OPEN**) | OBS-140 | — |
| **B-103** | Narrowing pass | narrowing pass. D6; PLAN-V2 P2.1 | B-101 (**OPEN**), B-102 (**OPEN**) | OBS-140 | — |
| **B-104** | Config axis | config axis. D16; **dependency satisfied — MVP-0 shipped at M4** | — (was: MVP-0 complete) | OBS-140 | — |
| **B-105** | Inheritance resolution | inheritance resolution. D16 | B-104 (**OPEN**) | OBS-140 | — |
| **B-106** | Intent-vs-observed diff | intent-vs-observed diff. D16 | B-104 (**OPEN**) | OBS-140 | — |
| **B-107** | Flow: `isis_adjacency` | PLAN-V2 P2.2 | MVP-0 complete | OBS-124 | — |
| **B-108** | Flow: `device_health` | flow: `device_health`. PLAN-V2 P2.2 | B-107 (**OPEN**) | OBS-140 | — |
| **B-109** | Flow: `ldp_session` | flow: `ldp_session`. PLAN-V2 P2.2 | B-107 (**OPEN**) | OBS-140 | — |
| **B-110** | Flow: `l3vpn_service` | flow: `l3vpn_service`. **Blocker resolved — Q-004 accepted `<pe>:<vrf>` (OBS-035); the naming scheme it waited | — (was: T-006 finding) | OBS-140 | — |
| **B-111** | Flow: `topology` | PLAN-V2 P2.3 | T-006 | OBS-086 | — |
| **B-112** | Free-text flow selection | free-text flow selection. D5 | B-101 (**OPEN**) | OBS-140 | — |
| **B-113** | MCP tool consolidation | rewording DONE 6637368; consolidation open, P1.4 | MVP-0 complete | OBS-117 | — |
| **B-114** | Gate model evaluation | gate model evaluation. D7 | B-101 (**OPEN**) | OBS-140 | — |
| **B-115** | Comprehensive health-check pipeline | health-check pipeline. Part 7 open item 4; **MVP-0 shipped** | — (was: MVP-0 complete) | OBS-140 | — |
| **B-405** | Prompt library expansion | prompt library expansion. D18; MVP-1 | MVP-1 | OBS-140 | — |
| **B-407** | Session memory | session memory, multi-turn. D14; MVP-1, and small | MVP-1 | OBS-140 | — |
| **B-409** | Scale test | scale test. Part 6 — the device-count-independence claim is unmeasured | MVP-1 | OBS-140 | — |
| **B-465** | Every IS-IS baseline is stale, and drift now fires on the repair | **half done.** The drift rule is direction-aware (an increase is `info`, names the stale baseline, and no long | — | OBS-142 · OBS-146 | — |
| **B-466** | The coherence bound runs at 77% on a healthy fabric | measured 23.1 s of a 30 s coherence bound on a healthy fabric (77%). The epoch may refuse on the tool's own la | B-436 (**DONE**) | OBS-142 | — |

---

## 5. Guardrails — mutation-tested, not merely run

**A test that passes proves the code does something. Only removing the guard proves the
test would notice if it stopped.** The harness is committed at `scripts/mutate_guards.py`
and is rerunnable. Twelve guards were removed one at a time, the guarded
test run, and the source restored from an in-memory copy with the restore asserted.

**No frozen file was touched.** The harness refuses by name — which means §5.1 covers
everything *except* the two most safety-critical suites, and that limitation is real, not
rhetorical. `test_safety.py` and `test_template_security.py` cannot be mutation-tested
while they are frozen. What §5.1 *can* do is mutate the code they guard, and rows 1 and 2
do exactly that.

### 5.1 Results

| # | Guard | Mutation applied | Test that caught it | Result |
|---|---|---|---|---|
| 1 | **The allowlist itself** | `unsafe_commands = []` in `network_tools._run_approved_commands` | `tests/test_network_tools.py` — 5 failures | **HOLDS** |
| 2 | **Allowlist checked before credentials load** | inserted `get_device(...)` above the check | `test_refuses_unapproved_commands_before_loading_credentials` | **HOLDS** |
| 3 | **B-453** identifier containment reached by the gate | dropped `.merge(check_identifier_containment(...))` from `ground_report` | `test_a_paraphrase_naming_a_device_this_fabric_does_not_have_is_withheld` | **HOLDS** |
| 4 | **T-035** the notifier egress bound | added an `evidence:` parameter to `notify()` | `test_the_notifier_cannot_receive_an_evidence_bundle` | **HOLDS** |
| 5 | **B-424** timeline citation checking | renamed the `invented_timestamp` failure kind | `test_the_exact_timestamp_the_model_fabricated_live_is_now_refused` | **HOLDS** |
| 6 | **B-428** `no_fault_on_path` | replaced it with `ALL_LAYERS_HEALTHY` | `tests/test_rounds_regression.py` — 6 failures | **HOLDS** |
| 7 | **B-436** coherence refuses on a moved fabric | `refuses` returns `False` unconditionally | `tests/test_epoch.py` — 3 failures | **HOLDS** |
| 8 | **B-458** MCP boundary sanitisation | replaced `sanitize(` with a pass-through lambda | `test_every_registered_tool_return_is_free_of_raw_device_text`, `test_registration_is_what_applies_the_boundary` | **HOLDS** |
| 9 | **B-456** aggregation switches with the member set | `ANY_HEALTHY` → `ALL_HEALTHY` for the path member set | `test_each_member_set_carries_its_own_aggregation` | **HOLDS** |
| 10 | **B-465** drift direction awareness | disabled the `actual > expected` branch | `test_losing_an_adjacency_warns_and_gaining_one_does_not` | **HOLDS** |
| 11 | **B-435** LLDP hostname resolution | reverted to comparing against inventory labels | `test_the_three_hostnames_are_this_fabric_s_own_devices` | **HOLDS** |
| 12 | **B-461** numbered rungs | removed the `{position}/{total}` prefix | `tests/test_render.py` | **HOLDS** |

**Twelve mutations, twelve caught.** Tree clean afterwards, all four frozen blobs identical.

### 5.2 The harness was wrong twice, and that is the most useful thing in this section

**B-458 and B-456 first came back `GUARD VACUOUS`.** Both were false.

I had picked each guard's test file *by name similarity* — `test_mcp_server.py` for the MCP
boundary, `test_descent.py` for a change in `descent.py`. The actual guards live in
`test_mcp_boundary.py` and `test_flows.py`. Running the mutations against the full suite
showed both failing immediately.

> **A mutation test that names the wrong test file reports a vacuous guard, and a vacuous
> guard is exactly what it was built to detect.** The false negative is indistinguishable
> from the true positive in the output, and it points at the more alarming conclusion — so
> it is the one a tired reader would accept.

Same shape as `VERIFICATION.md` §7.4, twice in two days: **I inferred from a plausible
proxy instead of checking.** The fix is mechanical — locate the guard by grepping for the
symbol, not by guessing the filename from the module's name.

### 5.3 And it left stale bytecode, which is the more dangerous of the two defects

After the mutation pass, `make test` reported **one failure** —
`test_each_member_set_carries_its_own_aggregation`, the B-456 guard. `git status` was
clean and `descent.py` line 288 read `Aggregation.ANY_HEALTHY`, correctly restored.

The failure was **stale `__pycache__`**. Clearing it returned 1813 passing.

> **The harness restores the source and does not invalidate the compiled copy.** Here it
> produced a false *failure*, which is loud and self-correcting. **The same residue can
> produce a false pass**: source restored, bytecode still mutated, a later run executing
> code that is not in any file and reporting green.
>
> That is the exact combination `ROUND-6.md` §0.1 names — **harmless-looking and
> undetectable** — arriving in the tool built to detect vacuous guards.

**Not a defect in any guard**, and none of §5.1's twelve results is affected: each mutation
wrote source, and pytest recompiled before running. The residue only affects what happens
*after*. The harness needs a cache clear between mutations, and any mutation pass should
end with one before the suite is trusted.

**Three defects in one verification harness, all found by using it.** Two wrong test files
and one cache-invalidation gap — none of them in the thing being verified.

---

## 6. Five dependency rows record a state their target no longer has

Found by resolving every dependency's state at generation time instead of trusting the
string in the row.

| Item | Its row claims | The target actually is |
|---|---|---|
| B-416 | B-414 (BLOCKED) | **CLOSED-AS-MEASURED** |
| B-418 | B-414 (BLOCKED) | **CLOSED-AS-MEASURED** |
| B-419 | B-414 (BLOCKED) | **CLOSED-AS-MEASURED** |
| B-426 | B-201 (unverified) | **DEFERRED** |
| B-459 | B-453 (OPEN) | **DONE** |

**B-459 is the one that matters operationally**: its row says it waits on B-453, and B-453
shipped today. Nothing else in the file would have told you.

**Same class as OBS-140's three stale dependencies, caught by a different mechanism.** A
dependency is a claim about another item, written once and never re-read. OBS-140 found
three by reading items; this found five by resolving them. **Neither pass would have found
the other's** — the tables above resolve dependencies live, so this class cannot recur in
this document, only in `BACKLOG.md` itself.

---

## 7. What the null result on §3 does and does not mean

**All 36 DONE claims survived. Zero changed state.** Stated plainly so the pass is readable
as a result, since a clean sweep is what a skipped pass also looks like.

**What was actually checked**, per item:

- every `OBS-nnn` in the evidence column resolved to a `## OBS-nnn ` heading in `FINDINGS.md`
  — **21 items**;
- every commit SHA resolved with `git cat-file -e <sha>^{commit}` — **6 items**;
- every backticked symbol was found in `src/`, `mcp_server/` or `tests/` — **7 items**;
- every `file.md:NNN` citation pointed at a line that exists — **2 items**;
- **no item reached the end with nothing checkable.** Two did on the previous pass
  (B-422 and B-423, which cited `struck through`); both were given real evidence in
  `e018aab`, which is why this pass is clean where the last was not.

**What that does not establish.** A resolving reference proves the evidence points at
something that exists. It says nothing about whether the claim is true. **If this document
stopped at §3 it would have verified almost nothing** — which is why §5 exists, and §5 is
where the actual assurance in this report lives.

**And §5 found nothing wrong either**, across twelve mutations. The honest reading of two
clean passes in a row is not *"everything is correct"* — it is that **the cheap checks are
exhausted**, and the next real assurance costs a lab window (rounds 6 and 8b) or an outside
reader (`PEER-REVIEW-BRIEF.md`). Both are scheduled and neither is something this document
can substitute for.

**B-420 is the worked example and it went the other way**, which is why it is worth naming
here. Its evidence resolved *and* the underlying claim needed measuring: `Coverage.gaps()`
existing was not the same as `ground_correlation` actually returning
`unbacked_absence_claim`. OBS-124 records the second half being measured rather than
inferred. **Every row in §3 is only as strong as B-420's first half.**

---

## 8. What remains genuinely OPEN

### Part 1 — before publication: **0 items**

Nothing open blocks publication. The last two Part 1 items closed today: **B-453**
(identifier containment) and **B-435** (LLDP hostname resolution).

### Part 2 — MVP-1: 17 items

They begin after publication. All 17 have a design reference and a named phase; none has
been started.

| ID | Title | Where it is specified |
|---|---|---|
| **B-101** | The reasoning gate | PLAN-V2 P2.1 |
| **B-102** | Candidate enumeration | reasoning gate, half 2. D7; PLAN-V2 P2.1 |
| **B-103** | Narrowing pass | narrowing pass. D6; PLAN-V2 P2.1 |
| **B-104** | Config axis | config axis. D16; **dependency satisfied — MVP-0 shipped a |
| **B-105** | Inheritance resolution | inheritance resolution. D16 |
| **B-106** | Intent-vs-observed diff | intent-vs-observed diff. D16 |
| **B-107** | Flow: `isis_adjacency` | PLAN-V2 P2.2 |
| **B-108** | Flow: `device_health` | flow: `device_health`. PLAN-V2 P2.2 |
| **B-109** | Flow: `ldp_session` | flow: `ldp_session`. PLAN-V2 P2.2 |
| **B-110** | Flow: `l3vpn_service` | flow: `l3vpn_service`. **Blocker resolved — Q-004 accepted |
| **B-111** | Flow: `topology` | PLAN-V2 P2.3 |
| **B-112** | Free-text flow selection | free-text flow selection. D5 |
| **B-114** | Gate model evaluation | gate model evaluation. D7 |
| **B-115** | Comprehensive health-check pipeline | health-check pipeline. Part 7 open item 4; **MVP-0 shipped |
| **B-405** | Prompt library expansion | prompt library expansion. D18; MVP-1 |
| **B-407** | Session memory | session memory, multi-turn. D14; MVP-1, and small |
| **B-409** | Scale test | scale test. Part 6 — the device-count-independence claim i |

**Three clusters, and the dependency chain matters:** the reasoning gate
(B-101 → B-102 → B-103, with B-112 and B-114 hanging off B-101), the config axis
(B-104 → B-105, B-106), and three more flows (B-107 → B-108, B-109; B-110 standing alone
now that Q-004 resolved its naming blocker). **B-101 and B-104 gate almost everything
else.**

### Open but gated on something outside Part 1 or Part 2: 3 items

| ID | Title | Gated on |
|---|---|---|
| **B-113** | MCP tool consolidation | blocked on the MCP re-test — unfalsifiable if consolidated first |
| **B-465** | Every IS-IS baseline is stale, and drift now fires on the re | half done; the rest needs the lab (`learn-topology` re-run) |
| **B-466** | The coherence bound runs at 77% on a healthy fabric | needs a skew distribution across several runs — lab |

---

## 9. BLOCKED — 16 items

Cannot proceed. Most need the lab or hardware.

| ID | Title | Evidence | Depends on | Last finding | Verified |
|---|---|---|---|---|---|
| **B-206** | Loki-backed `get_logs` | platform work B-206a/b | B-206 (**BLOCKED**), B-206 (**BLOCKED**) | OBS-124 | — |
| **B-209** | Report relay hardening | **Q-007 resolved 2026-08-17 — Telegram.** Now blocked only on T-035 itself: no relay exists to harden until on | T-035 (TODO) | OBS-140 · OBS-141 | — |
| **B-401** | Juniper platform | no Junos device | **a Junos device** | OBS-086 | — |
| **B-412** | `break_pe2_link.py` reports failure on success | **outside this repository** — `~/ai-agent-ops/faultlab/`, which is not a git repo (OBS-135). Cannot be closed  | — | OBS-075 · OBS-135 | — |
| **B-415** | Clock-skew detection across devices | OBS-124 | B-206 (**BLOCKED**) | OBS-124 | — |
| **B-416** | Event episodes — deterministic sequence construction | OBS-124 | B-414 (**CLOSED-AS-MEASURED**) | OBS-124 | — |
| **B-417** | Relationship-aware projection over a bounded topology neighbourhood | OBS-124 | B-107 (**OPEN**) | OBS-124 | — |
| **B-418** | Temporal shape — `max_rate_1m`, burst detection | OBS-124 | B-414 (**CLOSED-AS-MEASURED**) | OBS-124 | — |
| **B-419** | `expand_evidence` and the four evidence tiers | OBS-124 | B-101 (**OPEN**), B-414 (**CLOSED-AS-MEASURED**) | OBS-124 | — |
| **B-426** | Fault injection harness — and the Stage 2 acceptance vehicle | earned by rounds, not assumed | B-201 (**DEFERRED**) | OBS-124 | — |
| **B-427** | Evaluation corpus and confusion matrix | needs B-426 | B-426 (**BLOCKED**) | OBS-098 **CORPUS UPDATE 20 | — |
| **B-440** | Round 6 — a real BGP fault alongside an unrelated down physical interface | needs the lab. **Prediction sealed 2026-08-17 — `ROUND-6.md`.** Predicts B-456 already prevents B's scenario f | B-431 (**DONE**) | OBS-116 · OBS-141 | — |
| **B-448** | Vendor semantic equivalence | hardware — parked with B-401. *The payoff test is unvalidated, not passed* | B-401 (**BLOCKED**) | review §5 · A | — |
| **B-452** | Frozen-release audit governance | deferred by decision, OBS-105 | — | OBS-116 | — |
| **B-463** | Round 8 — a fault that leaves TCP up and BGP down | **`round8b.py` written and verified 2026-08-17; needs the lab.** Both round-8 defects fixed and tested against | B-437 (**DONE**) | OBS-121 · OBS-133 · OBS-13 | — |
| **B-464** | Round 9 — capture `transport_blocked` from an MD5 mismatch | needs the lab; **lower priority than any round that closes a boundary**. `fault_lab.py` option 6 unchanged; th | — | ROUND-8 §1.1 | — |

---


## 10. DEFERRED — 25 items

Examined, not scheduled. The evidence column carries the unblocking condition.

| ID | Title | Evidence | Depends on | Last finding | Verified |
|---|---|---|---|---|---|
| **B-201** | Trigger intake | until Stage 2 — event-driven intake. B-426 is how it gets tested end to end | — | OBS-140 | — |
| **B-202** | Mnemonic → flow lookup | until Stage 2. **Dependencies satisfied — T-004 and T-015 both DONE**; unblocked, not scheduled | — (was: T-004, T-015) | OBS-140 | — |
| **B-203** | Operational memory: schema and writer | until Stage 2 — operational memory. D14 | — | OBS-140 | — |
| **B-204** | Operational memory: query surface | until B-203 | B-203 (**DEFERRED**) | OBS-140 | — |
| **B-205** | SQLite as the memory backend | until B-203 | B-203 (**DEFERRED**) | OBS-140 | — |
| **B-207** | Variance experiment | until Stage 2, and **before it ships, not after** — the item says so | B-201 (**DEFERRED**) | OBS-140 | — |
| **B-208** | Stage 2 property suite | until Stage 2 — this is the gate on the transition | B-201 (**DEFERRED**) | OBS-140 | — |
| **B-210** | Operator knowledge in the descent | until Stage 2 memory. B-402 is DONE, so unblocked and unscheduled | B-402 (**DONE**) | OBS-140 | — |
| **B-301** | Identity provider integration | until Stage 3. **Nothing else in Stage 3 starts without this** | — | OBS-140 | — |
| **B-302** | Procedure definitions | until B-301 | B-301 (**DEFERRED**) | OBS-140 | — |
| **B-303** | `propose_procedure` | until B-302 | B-302 (**DEFERRED**) | OBS-140 | — |
| **B-304** | Approval gate | until B-301 and B-303 | B-301 (**DEFERRED**), B-303 (**DEFERRED**) | OBS-140 | — |
| **B-305** | Execution engine | until B-304 | B-304 (**DEFERRED**) | OBS-140 | — |
| **B-306** | Post-action verification | until B-305 | B-305 (**DEFERRED**) | OBS-140 | — |
| **B-307** | Write-path audit | until B-305 | B-305 (**DEFERRED**) | OBS-140 | — |
| **B-406** | gNMI telemetry as an evidence source | until Stage 2 — gNMI telemetry. D8 | Stage 2 | OBS-140 | — |
| **B-408** | Active probe budgeting | until Stage 2 — probe budgeting matters under event storms. D13 | Stage 2 | OBS-140 | — |
| **B-410** | Runbook and on-call handover | until Stage 2 — a runbook for an unattended agent. D3 | Stage 2 | OBS-140 | — |
| **B-443** | BGP object identity: `(device, instance, bgp-instance, peer, afi-safi)` | until a second address family or a VRF-scoped session exists | — | review §5 · A+B | — |
| **B-444** | Passive-read admission control and fan-out limits | until any multi-device concurrent deployment | — | review §5 · A+B | — |
| **B-445** | Both-end session evidence | until escalation-grade output; B-430 (DONE) already surfaces the far end's stated reason | — | review §5 · B | — |
| **B-446** | Ticket-grade run bundle and handover view | until workflow adoption | — | review §5 · B | — |
| **B-447** | EVPN/SR object cardinality | until either the EVPN or the SR flow is designed | — | review §5 · A | — |
| **B-449** | Service-level forwarding validation | until any L3VPN or EVPN claim is made | — | review §5 · A | — |
| **B-450** | Operator utility study | until a characterised failure envelope exists | — | review §5 · C | — |

---


## 11. CLOSED-AS-MEASURED — 1 item

Closed at its measured size rather than its filed one.

| ID | Title | Evidence | Depends on | Last finding | Verified |
|---|---|---|---|---|---|
| **B-414** | Log normalisation as a general capability | OBS-127 — 28 kept records; aggregation carries nothing | B-206 (**BLOCKED**) | OBS-124 | — |

---

---

## 12. What this document does not verify

- **It does not re-run rounds 5, 7 or 8.** Their payloads are confirmed committed; their
  findings are not re-derived.
- **It cannot mutation-test the two frozen suites.** `test_safety.py` and
  `test_template_security.py` are byte-frozen, so §5 mutates the *code they guard* instead.
  That is weaker and it is the correct trade.
- **It does not verify the 25 `DEFERRED` or 16 `BLOCKED` items** beyond confirming their
  states parse and their dependencies resolve. Gate Zero (OBS-140) established each is real
  and belongs to a named phase; nobody has argued *for* any of them.
- **A guard can hold and still be the wrong guard.** §5 proves each test notices when its
  code is removed. It does not prove the test asserts the right thing — that is §0.13's
  tests face, and no mechanical pass can see it.
