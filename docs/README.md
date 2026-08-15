# Documentation map

```
docs/
├── design/     WHY — reference. Read before building. Changes rarely.
│   ├── design-thinking.md          Decisions D1-D20: options, choice, rationale, growth path
│   ├── lld-investigation-layer.md  Delta spec: what to add to this repo and where
│   ├── glossary.md                 Pinned terminology. Read this first — `intent` collides
│   ├── interfaces.md               Human interaction ladder + residency/identity decisions
│   └── architecture.md             High-level architecture
├── build/      HOW — active during the build
│   ├── BUILD-PLAN.md               34 sequential tasks. Status updated in place
│   ├── TRACKER.md                  Progress. Authoritative on status
│   ├── FINDINGS.md                 Append-only log of everything learned
│   ├── discovery-loki.md           T-004 output
│   ├── discovery-alerting.md       T-005 output
│   ├── discovery-l3vpn.md          T-006 output
│   └── capture-manifest.md         T-007 output
├── devices.md                      Generated from inventory — do not hand-edit
├── REVIEW.md                       Code review
└── architecture.drawio             Diagram source
```

## Reading order

**Starting the build:** `design/glossary.md` → `design/design-thinking.md` →
`design/lld-investigation-layer.md` → `build/BUILD-PLAN.md` Part 0 → T-001.

**Resuming:** `build/TRACKER.md` for where things stand, then `build/FINDINGS.md`
for anything logged since you last looked, then the next `TODO` task.

**Reviewing:** `build/FINDINGS.md` Open Questions table, then the entries it references.

## The three build documents

| File | Nature | Who writes it |
|---|---|---|
| `BUILD-PLAN.md` | The plan. Task specs. | Human; executor edits status fields only |
| `TRACKER.md` | What is done. Authoritative on status. | Opus 5, after every task |
| `FINDINGS.md` | What was learned. Append-only. | Opus 5, whenever a trigger fires |
