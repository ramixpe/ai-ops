# M0 — the document kill-list, for operator sign-off

**Nothing is deleted until this list is signed off.** The operator asked to
"nuke all old and not anymore related documents." This is the reviewed list,
with a recommendation per file and the reasoning for each.

**The finding that shapes the whole list:** every single retirement candidate
is **still referenced by something else** (checked by grepping the tree for each
filename). Nothing is orphaned. So a naive delete does not remove clutter — it
creates dangling references, which is the defect the 2026-08-18 docs-drift lens
found twenty instances of. That argues for **archive over delete** in most
cases.

**The other constraint, from this build's own rules:** `FINDINGS.md` is
append-only *because the order things were learned in is the evidence*, wrong
turns included. A completed plan is the same kind of artefact one level up: it
records what was **intended**, against which what **happened** can be read.
Deleting it destroys the only copy of the intention.

---

## Tier 1 — DELETE (recommended: 2 files)

Truly superseded, no evidential value, and their content lives in full
elsewhere.

| File | Lines | Why it can go |
|---|---|---|
| `docs/devices.md` | 20 | **Generated**, and stale. `devices_doc.py` produces it from `inventory/lab.yaml`, which is now the single source of truth and is itself being superseded by NetBox in M3. A generated file with a live generator is not a document. |
| `docs/build/AUTONOMOUS-LOG.md` | 75 | A running log of one 2026-08-16 unattended session. It says itself that `FINDINGS.md` records what was *learned* and this records what *happened* — and everything in it that mattered was written into FINDINGS or a commit message. The commit history is the better version of this record. |

---

## Tier 2 — ARCHIVE, do not delete (recommended: 9 files → `docs/archive/`)

Completed process documents. Each did its job and none is a live reference; but
each is the record of an intention, and several are cited by FINDINGS entries
that would dangle. Moving them to `docs/archive/` with a one-line header
("completed <date>, superseded by <x>") removes them from the working set
without destroying evidence.

| File | Lines | Status |
|---|---|---|
| `docs/archive/REVIEW.md` | 312 | The 2026-07-28 review, at **32 tests**. Historically the first external eye; superseded by five later reviews. Cited 18×. |
| `docs/archive/PLAN-V2.md` | 215 | The plan Gate Zero reconciled against. Complete. |
| `docs/archive/BACKLOG-COMPLETION-PLAN.md` | 402 | Completed. |
| `docs/archive/FIX-PLAN.md` | 49 | Completed — the six-agent wave it planned is landed. |
| `docs/archive/OPS-WAVE-PLAN.md` | 135 | Completed — the OPS wave shipped (B-477…B-480). |
| `docs/archive/BACKLOG-STATUS.md` | 418 | A point-in-time verification of 98 items; the backlog has moved to 129 rows. Superseded by `BACKLOG.md` itself. |
| `docs/archive/VERIFICATION.md` | 257 | Same shape — a point-in-time claim audit, now superseded. |
| `docs/archive/PEER-REVIEW-BRIEF.md` | 148 | Its review happened; the findings are in `HOLISTIC-REVIEW.md` and FINDINGS. |
| `docs/archive/REPO-INVENTORY.md` | 210 | A pre-publication inventory; the tree has changed under it. |

**Also review at sign-off:** `docs/build/MCP-RETEST-PROTOCOL.md` (131) — Task 0
is complete and scored in `MCP-EXPERIMENT.md` §11/§12, but Q1 is **still owed**
during round 6, so this one stays live until that runs.

---

## Tier 3 — KEEP, and why (evidence class)

**Never delete these.** They are the record this project's credibility rests on,
and several are cited by name in commits, findings and the reviews.

- `docs/build/FINDINGS.md` (5,004 lines) — append-only, 168 observations.
- `docs/build/ROUND-5/6/7/8.md` — sealed predictions and their scored results.
  ROUND-6 is not even run yet.
- `docs/build/MCP-EXPERIMENT.md` — the model trials, §11/§12 scored today.
- `evidence-archive/` + its README — committed round payloads (§6.1d).
- The six review documents: `ARCHITECTURE-REVIEW`, `DEEP-REVIEW-2026-08-17`,
  `EVALUATION-METHODOLOGY-REVIEW`, `EXPERT-PEER-REVIEW-2026-08-17`,
  `OPERATIONS-REVIEW`, `MVP0-REVIEW`, `HOLISTIC-REVIEW`,
  `design/peer-review-response`. **`OPERATIONS-REVIEW.md` in particular is now a
  live requirements document** — it contains the ticket spec M1 is building.
- The three discovery notes (`discovery-loki`, `discovery-alerting`,
  `discovery-l3vpn`) and `capture-manifest.md` — these turn out to be **Stage-2
  inputs, not history**: the Loki and alerting notes are the groundwork for M5.

## Tier 4 — KEEP, live reference

`README`, `CLAUDE`, `SECURITY`, `CONTRIBUTING`, all of `docs/design/`,
`BACKLOG`, `BUILD-PLAN`, `TRACKER`, `SESSION-HANDOVER`, `OPERATOR-RUNBOOK`,
`docs/README`, `docs/diagrams/`, and the component READMEs.

---

## Net effect

| | files | lines |
|---|---|---|
| Delete | 2 | ~95 |
| Archive (moved, not lost) | 9 | ~2,146 |
| Working set after | — | **~2,240 lines lighter** |

**Sign-off needed on:** the 2 deletions, the 9 archives, and whether
`MCP-RETEST-PROTOCOL.md` waits for round 6 (recommended) or archives now.

**One recommendation against the instruction, stated plainly:** I do not
recommend deleting the completed plans outright. They are cheap to keep, they
are cited, and this project's whole method is that the record of what was
*intended* is what makes the record of what *happened* meaningful. Archiving
gets the working set clean without that loss. If you want them genuinely gone,
say so and I will delete rather than move — it is your call, and git history
retains them either way.
