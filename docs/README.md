# Documentation

Start with the repository [README](../README.md) for the runnable offline demo.

## Current Operations

- [Backlog](build/BACKLOG.md): authoritative work status.
- [Process](build/PROCESS.md): contribution and safety rules.
- [On-call runbook](build/ON-CALL-RUNBOOK.md): operational response.
- [Migration](build/MIGRATION.md): move the lab/tooling to a new host.
- [MCP user guide](MCP-USER-GUIDE.html): rendered operator guide; the PDF is
  available beside it for offline distribution.

## Design Reference

- [Architecture](design/architecture.md)
- [Glossary](design/glossary.md)
- [Interfaces and trust boundaries](design/interfaces.md)
- [Evidence epoch](design/evidence-epoch.md)
- [Evidence reduction](design/evidence-reduction.md)
- [Reasoning gate](design/reasoning-gate.md)
- [Chaos harness](design/chaos-harness.md)

## Lab Evidence

- [Evaluation corpus](build/EVALUATION-CORPUS.md)

## Diagrams

[Thirteen hand-authored diagrams](diagrams/index.html) — one unified layer,
built with the `diagram-design` skill and derived from the tree as of
2026-08-24. There is no generator and no CI byte-pin (that two-layer split
was retired 2026-08-23): these can drift from the code as the code moves,
and are only as current as their last update. Start at
[`diagrams/index.html`](diagrams/index.html).
