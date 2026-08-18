# Documentation map

Regenerated 2026-08-17 from the tree. The previous version listed 15 of 32 files
and pointed at two that did not exist.

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
│   │   └── next-level.md               Proposal: the three tiers + the local model, read together
│   ├── build/      HOW — the build's own record. Append-only in spirit.
│   │   ├── BUILD-PLAN.md               The task plan. Part 0 is binding
│   │   ├── TRACKER.md                  Progress. Authoritative on task status
│   │   ├── FINDINGS.md                 Append-only log + the Open Questions table
│   │   ├── BACKLOG.md                  Every open item with its reconciled state
│   │   ├── MVP0-REVIEW.md              The M4 review: what it does, and what it does not
│   │   ├── VERIFICATION.md             Every claim with its evidence. Found six overstated
│   │   ├── BACKLOG-STATUS.md           All 98 items by state, DONE claims verified,
│   │   │                               12 guardrails mutation-tested
│   │   ├── PEER-REVIEW-BRIEF.md        For a reviewer: what to read, and what to attack
│   │   ├── REPO-INVENTORY.md           Every file, purpose, last touched, referenced by
│   │   ├── OPERATOR-RUNBOOK.md         Step by step for the outstanding lab work
│   │   ├── MCP-RETEST-PROTOCOL.md      The six MCP questions and what to capture
│   │   ├── ROUND-6.md                  Injection round 6 — the trust-loss scenario (sealed)
│   │   ├── SESSION-HANDOVER.md         Read first if resuming a build session
│   │   ├── PLAN-V2.md                  The current plan
│   │   ├── BACKLOG-COMPLETION-PLAN.md  Superseded by PLAN-V2; kept for its reasoning
│   │   ├── MCP-EXPERIMENT.md           The MCP experiment: audit, refuted prediction, B-459
│   │   ├── ROUND-5.md                  Injection round 5 — invoke during propagation
│   │   ├── ROUND-7.md                  Injection round 7 — does a down port persist
│   │   ├── ROUND-8.md                  Injection round 8 — AS mismatch; §5 scored, §6 re-sealed
│   │   ├── AUTONOMOUS-LOG.md           Unattended-run log
│   │   ├── capture-manifest.md         T-007 — what to capture, per device and label
│   │   ├── discovery-loki.md           T-004 — log pipeline, label scheme, mnemonics
│   │   ├── discovery-alerting.md       T-005 — Alertmanager routing, Prometheus surface
│   │   ├── discovery-l3vpn.md          T-006 — VRF/RT map, CE attachment, subject naming
│   │   ├── FIX-PLAN.md                 Both reviews' findings, reconciled into one wave plan
│   │   └── OPS-WAVE-PLAN.md            n8n/knowledge/MCP judged, then built (B-477–480)
│   ├── devices.md                      Generated from inventory — do not hand-edit
│   ├── REVIEW.md                       Code review
│   ├── ARCHITECTURE-REVIEW.md          External review A
│   ├── OPERATIONS-REVIEW.md            External review B
│   ├── EVALUATION-METHODOLOGY-REVIEW.md  External review C
│   ├── EXPERT-PEER-REVIEW-2026-08-17.md  External review: architecture, safety, security, roadmap (P0-P3)
│   ├── DEEP-REVIEW-2026-08-17.md       Verifies the review above at source; B-467, the sanitisation gap
│   └── architecture.drawio             Diagram source
├── examples/                           Orchestrator wiring (n8n, systemd) — plumbing only,
│                                       never logic; the boundary rule is stated inside
├── prompts/README.md                   Versioned prompt artifacts (GRACE) and their rules
├── evidence-archive/                   Committed round payloads. See its own README
├── scripts/                            Probes and round samplers
└── tests/fixtures/README.md            What each label means, and how to reproduce it
```

## Reading order, by what you are doing

**Building or resuming:** `build/SESSION-HANDOVER.md` → `build/TRACKER.md` →
`build/FINDINGS.md` for anything logged since you last looked → `build/PLAN-V2.md`.

**Changing the investigation layer:** `design/glossary.md` →
`design/architecture.md` → `CLAUDE.md`'s safety boundary → the rung rules on
`flows.Rung`.

**Reviewing the repository:** start at `build/PEER-REVIEW-BRIEF.md` — it names what to
read, in what order, what is already known to be wrong, and where an attack is most
likely to land. Then `build/VERIFICATION.md` for every claim with its evidence.

**Touching fixtures:** `tests/fixtures/README.md` before capturing anything. `t0`
and `t1` are frozen and the reason is not obvious from looking at them.

**Running or scoring a round:** `design/chaos-harness.md` §6 — the protocol is
binding, and §6.1d has cost two rounds their evidence.

## The three build documents

| File | Nature | Who writes it |
|---|---|---|
| `BUILD-PLAN.md` | The plan. Task specs. | Human; executor edits status fields only |
| `TRACKER.md` | What is done. Authoritative on status. | Opus 5, after every task |
| `FINDINGS.md` | What was learned. Append-only. | Opus 5, whenever a trigger fires |

If `BUILD-PLAN.md` and `TRACKER.md` disagree about whether a task is done,
**`TRACKER.md` wins and the discrepancy is itself a finding.**

**`FINDINGS.md` is not tidied.** It is long, it is out of order, and it contains
wrong turns that were later corrected in place with the original left visible.
That is deliberate: the order things were learned in is most of its value, and a
narrative rewrite would lose the one thing a build log can offer that a design
document cannot.
