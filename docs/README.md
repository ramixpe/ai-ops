# Documentation

Start with the repository [README](../README.md) for the runnable offline demo.

## Current Operations

- [Backlog](build/BACKLOG.md): authoritative work status.
- [Findings](build/FINDINGS.md): append-only engineering record.
- [Process](build/PROCESS.md): contribution and safety rules.
- [On-call runbook](build/ON-CALL-RUNBOOK.md): operational response.
- [Migration](build/MIGRATION.md): move the lab/tooling to a new host.
- [Deep review — 2026-08-27](build/DEEP-REVIEW-2026-08-27.md): current audit,
  peer-review reconciliation, remediation status, and residual risks.
- [MCP user guide](MCP-USER-GUIDE.html): rendered operator guide; the PDF is
  available beside it for offline distribution.

## Event Operations

- [Version 2 architecture and execution plan](build/VERSION-2-EXECUTION-PLAN.md)
- [SOTA plan](build/SOTA-PLAN-2026-08-23.md)
- [Implementation review](build/SOTA-IMPLEMENTATION-REVIEW-CHECKLIST.md)
- [Trigger acceptance](build/B-706-TRIGGER-ACCEPTANCE-CAMPAIGN.md)
- [Syslog delivery handoff](build/B-717-SYSLOG-DUPLICATION-HANDOFF-2026-08-23.md)

## Design Reference

- [Architecture](design/architecture.md)
- [Design decisions](design/design-thinking.md)
- [Glossary](design/glossary.md)
- [Interfaces and trust boundaries](design/interfaces.md)
- [Evidence epoch](design/evidence-epoch.md)
- [Evidence reduction](design/evidence-reduction.md)
- [Reasoning gate](design/reasoning-gate.md)
- [Stage 2 architecture](design/stage-2-architecture.md)
- [Stretching the AI space](design/ai-space.md) — the graduated-freedom tier ladder and the unknown-error evidence hunt (2026-08-24 brainstorm)
- [Chaos harness](design/chaos-harness.md)

## Lab Evidence

- [Capture manifest](build/capture-manifest.md)
- [Loki discovery](build/discovery-loki.md)
- [Alerting discovery](build/discovery-alerting.md)
- [L3VPN discovery](build/discovery-l3vpn.md)
- [Evaluation corpus](build/EVALUATION-CORPUS.md)

## Diagrams

[Thirteen hand-authored diagrams](diagrams/index.html) — one unified layer,
built with the `diagram-design` skill and derived from the tree as of
2026-08-24. There is no generator and no CI byte-pin (that two-layer split
was retired 2026-08-23): these can drift from the code as the code moves,
and are only as current as their last update. Start at
[`diagrams/index.html`](diagrams/index.html).
