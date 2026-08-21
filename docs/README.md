# Documentation map

Regenerated 2026-08-20 from the tree, ahead of the v1.0.0 tag. The previous
version (2026-08-19) predated this pass: `BUILD-PLAN.md`'s binding Part 0
moved out to its own live file, `docs/build/PROCESS.md`, and four more
completed process documents — `SESSION-HANDOVER.md`, `OPERATOR-RUNBOOK.md`,
`M0-DOC-RETIREMENT.md`, `TRACKER.md`, plus what was left of `BUILD-PLAN.md`
itself (Parts 1-8) — moved from `docs/build/` to `docs/archive/`. See
`docs/archive/README.md` for what each was and what superseded it, and
`docs/archive/M0-DOC-RETIREMENT.md` for the earlier, larger 2026-08-18 pass
this one follows the same reasoning as.

## If you have just cloned this

You do not need any of it to try the tool — `README.md` opens with a command that
runs against committed captures with no lab, no credentials and no API key.

When you want to know whether to believe the output, read in this order:

1. **[design/glossary.md](design/glossary.md)** — short, and `intent` means *a
   question name* here, which collides with how the word is usually used.
2. **[build/MVP0-REVIEW.md](build/MVP0-REVIEW.md)** §5, *"What it does not do"* —
   the limits, stated by the people who built it.
3. **[design/design-thinking.md](design/design-thinking.md)** — the decisions and
   what would make each one wrong. Every one carries a *Revisit if*.
4. **[design/peer-review-response.md](design/peer-review-response.md)** — three
   independent external reviews, and which claims they forced us to withdraw.

**[design/chaos-harness.md](design/chaos-harness.md)** §1 is the honest statement
of what "measured" means here and what it does not yet cover.

## The tree

```
ios-xr-nettools/
├── README.md                           Start here. Runnable demo in the first ten lines
├── CLAUDE.md                           Agent instructions and the rules that must not break
├── SECURITY.md                         Threat model, what is/isn't enforced, disclosure path
├── CONTRIBUTING.md                     How to set up, test, and submit a change
├── docs/
│   ├── design/     WHY — reference. Changes rarely.
│   │   ├── glossary.md                 Pinned terminology. Read FIRST — `intent` collides
│   │   ├── architecture.md             The layer stack and every phase's design notes
│   │   ├── design-thinking.md          Decisions D1-D20: options, choice, rationale, Revisit if
│   │   ├── lld-investigation-layer.md  Pre-build delta spec. Partly superseded; says where
│   │   ├── interfaces.md               Human interaction ladder + residency/identity
│   │   ├── evidence-reduction.md       Making large sources model-readable without a model
│   │   ├── evidence-epoch.md           One observation window; why skew is not coherence
│   │   ├── chaos-harness.md            Fault injection and how accuracy is measured
│   │   ├── peer-review-response.md     Three external reviews; accepted, corrected, deferred
│   │   ├── stage-2-architecture.md     Stage 2 brainstorm: MCP hub, cache+syslog invalidation,
│   │   │                               wide/narrow flow-vs-tool dimension. Decision record
│   │   ├── cache-spike.md              M4 spike: answers the cache brief with file:line
│   │   │                               evidence; concludes a cache isn't justified yet
│   │   └── next-level.md               Proposal: the three tiers + the local model, read together
│   ├── build/      HOW — the build's own record. Append-only in spirit.
│   │   ├── PROCESS.md                  The rules of engagement — BUILD-PLAN.md's former Part 0.
│   │   │                               Binding: model roles, escalation ladder, frozen files
│   │   ├── FINDINGS.md                 Append-only log + the Open Questions table
│   │   ├── BACKLOG.md                  Every open item with its reconciled state — current status
│   │   ├── MVP0-REVIEW.md              The M4 review: what it does, and what it does not
│   │   ├── HOLISTIC-REVIEW.md          The 2026-08-18 five-perspective review: B-481/B-482
│   │   │                               fixed, 18 UX fixes, the two-lens-convergence finding
│   │   ├── MCP-EXPERIMENT.md           The MCP experiment: audit, refuted prediction, B-459
│   │   ├── MCP-RETEST-PROTOCOL.md      Six MCP questions and what to capture. Q1 still owed
│   │   │                               as of round 6 — kept live, deliberately not archived
│   │   ├── ROUND-5.md                  Injection round 5 — invoke during propagation
│   │   ├── ROUND-6.md                  Injection round 6 — the trust-loss scenario (sealed)
│   │   ├── ROUND-7.md                  Injection round 7 — does a down port persist
│   │   ├── ROUND-8.md                  Injection round 8 — AS mismatch; §5 scored, §6 re-sealed
│   │   ├── ON-CALL-RUNBOOK.md          For whoever is paged, not at a terminal with the lab
│   │   ├── capture-manifest.md         T-007 — what to capture, per device and label
│   │   ├── discovery-loki.md           T-004 — log pipeline, label scheme, mnemonics
│   │   ├── discovery-alerting.md       T-005 — Alertmanager routing, Prometheus surface
│   │   └── discovery-l3vpn.md          T-006 — VRF/RT map, CE attachment, subject naming
│   ├── archive/    Completed process documents. Kept, not deleted — see archive/README.md
│   │   ├── README.md                   What each file was, and what superseded it
│   │   ├── BUILD-PLAN.md               The 34-task plan (Parts 1-8), archived 2026-08-20 —
│   │   │                               its binding Part 0 lives on at build/PROCESS.md
│   │   ├── TRACKER.md                  Progress on that plan. Authoritative on its task
│   │   │                               status; current project status is build/BACKLOG.md
│   │   ├── SESSION-HANDOVER.md         A build-state snapshot, 2026-08-18/19. Superseded by
│   │   │                               build/BACKLOG.md for current state
│   │   ├── OPERATOR-RUNBOOK.md         A lab-window checklist, 2026-08-19. Not all done —
│   │   │                               check build/BACKLOG.md before treating it as current
│   │   ├── M0-DOC-RETIREMENT.md        The doc kill-list the 2026-08-18 M0 pass executed from
│   │   │                               (2 deleted, 9 moved). A historical plan record
│   │   ├── REVIEW.md                   The first external review, 2026-07-28, at 32 tests
│   │   ├── PLAN-V2.md                  The plan Gate Zero reconciled the backlog against
│   │   ├── BACKLOG-COMPLETION-PLAN.md  A completion plan for the backlog. Executed
│   │   ├── FIX-PLAN.md                 The six-agent fix wave. Executed
│   │   ├── OPS-WAVE-PLAN.md            The OPS wave plan. Executed — B-477…B-480 shipped
│   │   ├── BACKLOG-STATUS.md           Point-in-time verification of 98 items (now 129 rows)
│   │   ├── VERIFICATION.md             Point-in-time claim audit, superseded by the same
│   │   ├── PEER-REVIEW-BRIEF.md        Brief handed to a reviewer; superseded by the reviews
│   │   │                               it produced
│   │   └── REPO-INVENTORY.md           Pre-publication tree inventory; the tree moved under it
│   ├── ARCHITECTURE-REVIEW.md          External review A
│   ├── OPERATIONS-REVIEW.md            External review B — "Would I use this at 3am?"
│   ├── EVALUATION-METHODOLOGY-REVIEW.md  External review C
│   ├── EXPERT-PEER-REVIEW-2026-08-17.md  External review: architecture, safety, security, roadmap (P0-P3)
│   ├── DEEP-REVIEW-2026-08-17.md       Verifies the review above at source; B-467, the sanitisation gap
│   ├── diagrams/                       Nine SVGs of the system as built — layer stack, one call
│   │   │                               end to end, trust boundary, capabilities, current state,
│   │   │                               the descent, Stage 2, the event-driven loop, and the model
│   │   │                               boundary. Generated from the tree by d1.py…d9.py;
│   │   │                               byte-pinned, regenerate, never hand-edit
│   │   ├── README.md                   What each diagram answers, and how to regenerate it
│   │   └── design/                     **A second, hand-authored layer** (2026-08-21) — ten
│   │                                   presentation diagrams of the whole stack, tool plus lab
│   │                                   platform, drawn with the diagram-design skill. Better to
│   │                                   read, and CAPABLE OF GOING STALE: the SVGs above are
│   │                                   generated and byte-pinned, so they cannot drift from the
│   │                                   code without failing CI. When the two disagree, the
│   │                                   generated ones are right. Start at design/index.html
│   └── architecture.drawio             Pre-M0 diagram source, superseded by docs/diagrams/ —
│                                       not regenerated since 6629a2c; treat as historical
├── examples/                           Orchestrator wiring (n8n, systemd) — plumbing only,
│                                       never logic; the boundary rule is stated inside
├── prompts/README.md                   Versioned prompt artifacts (GRACE) and their rules
├── evidence-archive/                   Committed round payloads. See its own README
├── scripts/                            Probes and round samplers
├── mcp_server/README.md                The MCP tool surface and the sanitisation boundary
└── tests/fixtures/README.md            What each label means, and how to reproduce it
```

## Reading order, by what you are doing

**Building or resuming:** `build/BACKLOG.md` for current state → `build/FINDINGS.md`
for anything logged since you last looked → `build/PROCESS.md` for the rules you
are operating under. (Earlier sessions read `archive/SESSION-HANDOVER.md` →
`archive/TRACKER.md` first; both are archived now, superseded by `BACKLOG.md` as
the live status record.)

**Changing the investigation layer:** `design/glossary.md` →
`design/architecture.md` → `CLAUDE.md`'s safety boundary → the rung rules on
`flows.Rung`.

**Reviewing the repository:** start at `build/HOLISTIC-REVIEW.md` — the most
recent whole-repo pass, five perspectives, with what it fixed. For the review
process that preceded it, `archive/PEER-REVIEW-BRIEF.md` names what to read, in
what order, and where an attack was most likely to land; `archive/VERIFICATION.md`
holds every claim from that round with its evidence. Both are archived — point in
time, not current state.

**Touching fixtures:** `tests/fixtures/README.md` before capturing anything. `t0`
and `t1` are frozen and the reason is not obvious from looking at them.

**Running or scoring a round:** `design/chaos-harness.md` §6 — the protocol is
binding, and §6.1d has cost two rounds their evidence.

## The build documents

The original three — `BUILD-PLAN.md` (task specs) and `TRACKER.md` (what was
done, authoritative on that plan's status) — governed the MVP-0 build and are
now archived alongside it (`docs/archive/`); their disagreement rule is
historical, kept below because it explains how to read them together if you do.
Two live ones replace them for anything current:

| File | Nature | Who writes it |
|---|---|---|
| `PROCESS.md` | The rules of engagement — `BUILD-PLAN.md`'s former Part 0. Binding. | Human; rarely amended |
| `BACKLOG.md` | Every open item, with its reconciled state. Authoritative on current status. | Opus 5, continuously |
| `FINDINGS.md` | What was learned. Append-only. | Opus 5, whenever a trigger fires |

**Historical, for the archived pair:** if `BUILD-PLAN.md` and `TRACKER.md`
disagreed about whether a task was done, `TRACKER.md` won and the discrepancy
was itself a finding.

**`FINDINGS.md` is not tidied.** It is long, it is out of order, and it contains
wrong turns that were later corrected in place with the original left visible.
That is deliberate: the order things were learned in is most of its value, and a
narrative rewrite would lose the one thing a build log can offer that a design
document cannot.

## Document retirement

`docs/archive/M0-DOC-RETIREMENT.md` is the reviewed kill-list M0 (2026-08-18) acted
on, with operator sign-off: two files deleted (`docs/devices.md`,
`docs/build/AUTONOMOUS-LOG.md`), nine archived to `docs/archive/`. It is kept as
the historical record of that decision, not as a live queue — no further action
is pending from it. `docs/build/MCP-RETEST-PROTOCOL.md` was deliberately left out
of that pass because Q1 is still owed as of round 6, and still is.

**A second, smaller pass ran 2026-08-20**, ahead of the v1.0.0 tag: `BUILD-PLAN.md`'s
binding Part 0 was extracted to `docs/build/PROCESS.md` (still live, same section
numbers), and the plan's executed remainder plus `SESSION-HANDOVER.md`,
`OPERATOR-RUNBOOK.md`, `TRACKER.md` and `M0-DOC-RETIREMENT.md` itself moved to
`docs/archive/` — see `docs/archive/README.md` for the reasoning per file. No
files were deleted in this pass.
