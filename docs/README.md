# Documentation map

```
ios-xr-nettools/
├── CLAUDE.md                          Architecture, layers 0-5, the safety boundary
├── docs/
│   ├── design/     WHY — reference. Read before building. Changes rarely.
│   │   ├── glossary.md                Pinned terminology. Read this FIRST — `intent` collides
│   │   ├── design-thinking.md         Decisions D1-D20: options, choice, rationale, growth path
│   │   ├── lld-investigation-layer.md Delta spec: what to add to this repo and where
│   │   └── interfaces.md              Human interaction ladder + residency/identity. Before T-035
│   ├── build/      HOW — active during the build
│   │   ├── BUILD-PLAN.md              35 sequential tasks. Part 0 is binding. Status in place
│   │   ├── TRACKER.md                 Progress. Authoritative on status
│   │   ├── FINDINGS.md                Append-only log + the Open Questions table
│   │   ├── discovery-loki.md          T-004 — log pipeline, label scheme, mnemonics
│   │   ├── discovery-alerting.md      T-005 — Alertmanager routing, Prometheus metric surface
│   │   ├── discovery-l3vpn.md         T-006 — VRF/RT map, CE attachment, subject naming
│   │   └── capture-manifest.md        T-007 — what T-011 must capture, per device and label
│   ├── devices.md                     Generated from inventory — do not hand-edit
│   ├── REVIEW.md                      Code review
│   └── architecture.drawio            Diagram source
├── prompts/                           Versioned prompt artifacts (GRACE). Created at T-026
│   └── tests/cases/                   Golden input -> expected output shape
├── scripts/
│   └── probe_minimax.py               T-002 — the six MiniMax contract checks
└── tests/fixtures/
    └── README.md                      What each fixture label means, and how to reproduce it
```

`docs/design/architecture.md` is referenced by `install-docs.sh` but **has not been written**. Nothing depends on it; `CLAUDE.md` carries the architecture today.

## Reading order

**Starting the build:** `design/glossary.md` → `design/design-thinking.md` →
`design/lld-investigation-layer.md` → `build/BUILD-PLAN.md` Part 0 → T-001.

**Resuming:** `build/TRACKER.md` for where things stand, then `build/FINDINGS.md`
for anything logged since you last looked, then the next `TODO` task.

**Reviewing:** `build/FINDINGS.md` Open Questions table, then the entries it references.

**Touching fixtures:** `tests/fixtures/README.md` before capturing anything. `t0` and
`t1` are frozen, and the reason is not obvious from looking at them.

## The three build documents

| File | Nature | Who writes it |
|---|---|---|
| `BUILD-PLAN.md` | The plan. Task specs. | Human; executor edits status fields only |
| `TRACKER.md` | What is done. Authoritative on status. | Opus 5, after every task |
| `FINDINGS.md` | What was learned. Append-only. | Opus 5, whenever a trigger fires |

If `BUILD-PLAN.md` and `TRACKER.md` disagree about whether a task is done,
**`TRACKER.md` wins and the discrepancy is itself a finding.**
