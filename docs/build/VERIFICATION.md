# Verification report

**2026-08-17, at `098da8f`.** Every row below was checked against the repository at the
moment of writing. **Nothing here is recorded from memory** — where a row says a commit
exists, `git cat-file -e` was run; where it says a test passes, the suite was run; where it
says a file is unchanged, the blob hashes were compared.

**Suite at time of writing:** `1813 passed, 24 skipped`, `ruff` clean. Branch
`feat/investigation-layer`, **0 commits ahead of the remote**.

> **A verification that confirms everything has probably not verified anything.** This one
> found **six** discrepancies, listed in §7. Three are documentation drift, one is a
> mis-citation, one is a taxonomy conflation, and one is a claim in the request that
> commissioned this report. None invalidates a result; all six were invisible before
> someone compared two things that were supposed to agree.

---

## 1. Frozen files — hash-compared, not asserted

`BUILD-PLAN.md` §0.5. Compared as git blob hashes against `6629a2c`, the commit
immediately before T-001.

| File | Baseline blob | HEAD blob | Result |
|---|---|---|---|
| `tests/test_safety.py` | `840b24b53714` | `840b24b53714` | **IDENTICAL** |
| `tests/test_template_security.py` | `fa1af64bcb5d` | `fa1af64bcb5d` | **IDENTICAL** |
| `src/agent_nettools/platforms.py` | `ae2469781a92` | `ae2469781a92` | **IDENTICAL** |
| `src/agent_nettools/templates.py` | `a2dcc684f116` | `a2dcc684f116` | **IDENTICAL** |

**Verified how:** `git rev-parse 6629a2c:<path>` against `git rev-parse HEAD:<path>`.

**What this does and does not establish.** It establishes the four files are byte-identical
to the pre-build baseline. It does **not** establish that the safety property is intact —
a relaxation could live in a file that is not frozen and that these two suites do not
reach. The frozen-file check is a tripwire on the *most likely* place for one, not a proof.

---

## 2. Guardrail tests — by name, and each one run

| Test | Location | Guards | Result |
|---|---|---|---|
| `test_refuses_unapproved_commands_before_loading_credentials` | `tests/test_network_tools.py` | The allowlist ordering invariant — runs with an empty environment | **PASS** |
| `test_render_command_attempts_every_parameter_before_ever_assembling_a_command` | `tests/test_templates.py` | No partially-assembled command exists, even transiently | **PASS** |
| `test_quiet_fabric_pair_reports_no_change` | `tests/test_fixtures.py` | The 100%-false-positive diff defect stays fixed, across all 9 devices | **PASS** |
| `test_devices_doc_matches_rendered_inventory` | `tests/test_docs.py` | `docs/devices.md` cannot drift from the inventory | **PASS** |
| `test_mcp_readme_lists_exactly_the_exposed_tools` | `tests/test_docs.py` | The MCP README cannot drift from the server's real surface | **PASS** |
| `test_audit_log_failure_never_breaks_a_check` | `tests/test_network_tools.py` | A bad `NETTOOLS_LOG` path cannot fail a successful check | **PASS** |
| `tests/test_checks_agree_with_health.py` | whole file | `checks.py` and `health.py` never contradict, 9 devices × t0/t1 | **PASS** |
| `test_the_notifier_cannot_receive_an_evidence_bundle` | `tests/test_notifier.py` | The T-035 egress bound, by signature introspection | **PASS** |
| `test_the_exact_timestamp_the_model_fabricated_live_is_now_refused` | `tests/test_grounding.py` | The T-033 fabrication, pinned verbatim | **PASS** |
| `test_a_paraphrase_naming_a_device_this_fabric_does_not_have_is_withheld` | `tests/test_investigation.py` | B-453 reached through the runner, not only unit-tested | **PASS** |

**Discrepancy found — see §7.4.** `test_checks_agree_with_health` is cited in four
documents as a *test name*. It is a **file name**; there is no function by that name. The
artefact exists and passes, so this is a precision defect in the citation, not a missing
guardrail — but a reader grepping for the function finds nothing.

---

## 3. The §0.13 faces — seven, each with the finding that produced it

**There are seven faces.** `BUILD-PLAN.md` §0.13 states it three times and the table has
seven rows. **The request that commissioned this report asked for eight** — see §7.1; the
eight is a different taxonomy.

| # | Face | Produced by | Verified how |
|---|---|---|---|
| 1 | **Data** — a survey is a sample | T-013: 44 route fixtures grouped by line 4, identical across all three route shapes | §0.13 table row present; T-013 in `TRACKER.md` |
| 2 | **Rules** — one instance fits one instance | T-026: *"every prompt names its refusal path as `undetermined`"* — true of `report`, not `correlate` | §0.13 table row; recurred at **OBS-146** this session |
| 3 | **Tests** — a shared premise confirms itself | T-028: **sixteen green tests** over a filter deleting eight severity-3 records unattributed | §0.13 table row + the two-forms subsection at `BUILD-PLAN.md:434` |
| 4 | **Identity** — width only where the corpus varies | T-029a: `refusal_marker` keyed by family, failed again when a second *version* appeared | §0.13 table row |
| 5 | **Setup** — the environment verifies itself | T-031: `--from-fixtures` "needed no credentials" because `main()` loads this repo's `.env` | §0.13 table row; closed by **B-423**, the `offline-demo` CI job |
| 6 | **Duplication** — consistent duplicates read as correctness | B-460: `St/PfxRcd` discriminator re-derived by `_is_numeric` in three consumers, agreeing, for eight phases | §0.13 table row + subsection at `:404` |
| 7 | **Procedure** — every step succeeds, the outcome is void | OBS-131: §6.1d said *archive the payload*; `.gitignore`'s `*.jsonl` dropped every sample | §0.13 table row + subsection at `:360`, and a **second instance** added this session (OBS-135) |

**Face 7 has recurred twice since it was written**, which is the strongest evidence any of
them are real: OBS-135 (the noun was underspecified, not only the verb) and the round-6
snapshot gap (`ROUND-6.md` §1, where the *verification machinery* could not see the port it
was verifying).

**Face 2 recurred this session, against me.** OBS-146: I scoped B-465's fix by generalising
over PE2 and PE4, and PE1 — the counterexample — was one line down in the same finding.

---

## 4. The eight silent-failure shapes — a different taxonomy

`BUILD-PLAN.md:456`. **§0.13 states explicitly that these are orthogonal axes**
(`BUILD-PLAN.md:488`): *"the faces classify what the evidence could not show you; the
shapes classify what the failure looks like."*

| # | Shape | Findings |
|---|---|---|
| 1 | Green flag over a degraded read | OBS-006, OBS-043, OBS-044 |
| 2 | Absence read as a healthy value | OBS-044, OBS-051 — answered by `unevaluated` |
| 3 | A guardrail passing over an empty set | §0.12 |
| 4 | A rule generalised from one instance | OBS-021, OBS-062, OBS-063, OBS-069 |
| 5 | A test agreeing with the code by construction | OBS-064 |
| 6 | Wrong evidence read as right evidence | OBS-071, OBS-072, OBS-089 |
| 7 | Evidence collected, parsed, carried, never read | OBS-092 |
| 8 | A fix silently deletes coverage that was always correct | OBS-097 |

**Discrepancy — see §7.2.** `MVP0-REVIEW.md:25` and `:33` still say **seven** shapes.
Shape 8 was added after that review was written and the review was not updated. **The H2
claims audit missed this**, which makes it the eleventh cross-file contradiction and the
first one found by a *count* rather than by reading.

---

## 5. Rounds — payload path and whether it is COMMITTED

`chaos-harness.md` §6.1d: **a round is archived when its payload is committed.** Verified
with `git ls-files`, not by looking on disk.

| Round | Doc | Payload path | Tracked files | Archived? |
|---|---|---|---|---|
| 1–4 | none — recorded in `BACKLOG.md`, `SESSION-HANDOVER.md`, `test_rounds_regression.py` | — | **0** | **NO — findings and metrics only.** §6.1d names this gap explicitly |
| 5 | `ROUND-5.md` | `evidence-archive/round5/20260817-094426/` | **15** | **YES** |
| 6 | `ROUND-6.md` (sealed, unrun) | — | 0 | n/a — not yet run |
| 7 | `ROUND-7.md` | `evidence-archive/round7/{151001,151420}/` | **6** | **YES** |
| 8 | `ROUND-8.md` | `evidence-archive/round8/{160359,161045}/` + `round8.py` | **7** | **YES** — committed `2e12786`, retroactively |
| 8b | `ROUND-8.md` §6 (sealed, unrun) | will default into `evidence-archive/round8b/` | 0 | n/a — not yet run |

**Two things this row-set makes visible that prose did not.**

**Rounds 1–4 are not archived and cannot be.** Their entries in
`tests/test_rounds_regression.py` (198 lines, 13 tests, all passing) assert what the
*finding logic* does with a given rung vector. That is a true and useful assertion and it
is **not** the claim that the vector is still producible from evidence. Anyone citing
rounds 1–4 as replayable is overstating them.

**Round 8's archive is retroactive.** It was committed at `2e12786` *after* the round ran,
from a directory in `~/ai-agent-ops/faultlab/` which **is not a git repository**. It
survived only because nothing deleted it; round 7 lost 158 samples to that exact state.
`round8b.py` now writes into the tracked repo by default and copies itself in beside the
samples, so the next round does not depend on anyone remembering.

---

## 6. DONE since the peer review — 31 items, evidence resolved mechanically

The peer review landed at `8af8cc9`. Every item from **B-420** onward is its cohort or
later. **All 31 were checked**: each row's evidence field was parsed for `OBS-nnn`
references and commit SHAs, every OBS was looked up in `FINDINGS.md`, and every SHA was
resolved with `git cat-file -e`.

**Result: 29 of 31 resolve to a real finding or a real commit. 2 do not** — see §7.3.

| Evidence form | Count | Verified how |
|---|---|---|
| `OBS-nnn` reference | 21 | Heading `## OBS-nnn ` present in `FINDINGS.md` |
| Commit SHA | 6 | `git cat-file -e <sha>^{commit}` |
| Inline prose naming a symbol or file | 2 | Symbol resolved in the source tree |
| **Not checkable** | **2** | **B-422, B-423 — see §7.3** |

**Spot-verified beyond the reference check**, because a resolving reference is not the same
as a true claim:

| Item | Claim | Verified how | Result |
|---|---|---|---|
| **B-420** | Both halves complete | `Coverage.gaps()` exists; `ground_correlation` returns `unbacked_absence_claim` for an uncovered absence | **CONFIRMED** — and OBS-124 records it was *measured*, not assumed. This is the row the whole report is modelled on |
| **B-422** | `--format json` is pipeable; `_note` goes to stderr | **Re-verified accidentally this session**: a `test_notifier.py` assertion against stdout failed because the note was on stderr | **CONFIRMED**, by a test written for something else |
| **B-423** | CI runs the demo in a clean environment | `.github/workflows/ci.yml` `offline-demo` job: installs without the `llm` extra, asserts `.env` absent and 7 credential vars unset, checks all three exit codes | **CONFIRMED** |
| **B-435** | Both false anomaly classes go to zero | `find_lldp_disagreements` and `find_neighbors_not_in_inventory` both return empty over the nine `t0` fixtures | **CONFIRMED**, and the positives are now synthetic so the classes can still fire |
| **B-453** | Zero false positives on round 3's report | `tests/test_rounds_regression.py` 13/13 pass with containment wired into `ground_report` | **CONFIRMED** |
| **B-458** | Raw device text does not reach a model over MCP | Live: the MCP re-test payload shows `commands_withheld … 748 chars` | **CONFIRMED in production**, not only by test |
| **B-459** | An invented subject is refused | Live: the MCP re-test refused `10.255.0.99` end to end | **CONFIRMED in production** |
| **B-462** | A down port does not persist | 99 samples, 0 persisting; the restore's lagging sample is the positive control | **CONFIRMED** — the control is what makes the zero interpretable |

---

## 7. Discrepancies found

### 7.1 The request asked for eight faces; there are seven

Seven faces (§0.13), eight shapes (`BUILD-PLAN.md:456`). They are separately numbered,
explicitly declared orthogonal, and one of them — face 5 *setup* and shape 6 *wrong
evidence* — shares a finding (OBS-072) under both, which §0.13 says is correct rather than
duplication.

**Two taxonomies with adjacent counts, both indexed by small integers, both about silent
failure.** Conflating them is easy and I would have made the same slip. Recorded here
rather than silently answered, because the report was asked to cover "the eight faces" and
covering seven without saying so would look like an omission.

### 7.2 `MVP0-REVIEW.md` still says seven shapes

`:25` — *"**7 shapes**, ~15 instances"*; `:33` — *"The seven silent-failure shapes"*.
`BUILD-PLAN.md:456` says eight, and shape 8 has its own subsection.

**The H2 audit did not catch this**, and it should have: it is the same class as the ten it
did catch. It was missed because H2 compared *statements*, and this is a **count** — a
number that stays grammatical while the thing it counts grows. Eleventh contradiction.

### 7.3 Two DONE rows cite a formatting artefact as evidence

B-422 and B-423 both carry `struck through` in the evidence column. That describes how the
*other* table renders the item; it is not evidence and cannot be checked by anything.

**Both items are genuinely done** — verified independently in §6 above. The defect is in
the reconciliation, not the work. Worth fixing, and worth noticing that a machine check
found it in a column a human had read past several times.

### 7.4 A guardrail is cited by a name that does not exist

`test_checks_agree_with_health` is referenced in `CLAUDE.md`, `glossary.md`,
`lld-investigation-layer.md` and `BUILD-PLAN.md` as though it were a test function. It is a
**file**: `tests/test_checks_agree_with_health.py`. The file exists and passes.

Minor, and worth one line each to fix, because the failure mode is a reader grepping for
the function, finding nothing, and concluding the guardrail is missing.

### 7.5 Rounds 1–4 are cited more confidently than their archive supports

Nothing states they are replayable, and `chaos-harness.md` §6.1d is explicit that they are
not. But they are cited by number throughout as though they were the same kind of object as
rounds 5, 7 and 8, and they are not: **those three have payloads, and rounds 1–4 have
verdicts.** The distinction is documented in one place and invisible everywhere else.

### 7.6 The frozen-file check proves less than it is usually cited as proving

Every session including this one reports "four frozen files byte-identical" as though it
were a safety result. It is a **tripwire on the most likely location**, not a proof that
the safety property holds. A relaxation in an unfrozen file that neither frozen suite
reaches would pass this check silently.

Not a defect — the check is doing its job. A defect in how it is habitually reported, this
report's own earlier sections included until this row was written.

---

## 8. What this report does not verify

Stated so the coverage is not over-read, which is the failure this whole document is
against.

- **It does not re-run the rounds.** Round 5, 7 and 8's findings are verified as *archived
  and internally consistent*, not as reproducible. Only round 8b and round 6 can produce
  that, and both need the lab.
- **It does not verify the 25 `DEFERRED` or 20 `OPEN` items** beyond what Gate Zero's
  examination established (OBS-140): that each is real, its dependencies are accurate, and
  it belongs to a named phase. **An item at `OPEN` is one nobody has argued against.**
- **It does not audit the code for defects.** It verifies that claims about the code
  resolve to artefacts that exist and tests that pass. A test can pass and be wrong —
  that is §0.13's tests face, and this report cannot see it by construction.
- **It does not verify the three external reviews' remaining findings.**
  `peer-review-response.md` §5's ten deferrals are `DEFERRED` with conditions, not
  discharged.
