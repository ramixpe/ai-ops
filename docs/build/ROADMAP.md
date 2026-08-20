# Roadmap — the ten epics, and the order they must go in

Source: `docs/ELITE-ENGINEERING-REVIEW-2026-08-20.md` §8, an independent
engineering review of the `v1.0.0` tag. This file records what that review
recommended building, in the order it argued for. **Nothing here is started.**
`BACKLOG.md` remains authoritative for work in flight; this is the layer above
it — what the product becomes, and what has to be true first.

## The constraint that governs everything below

> *"Do not run these as ten parallel feature streams. Epics 1–5 define the
> contracts and truth system; 6–10 consume them. Parallelizing consumers
> before those contracts stabilize will recreate the policy drift found in
> this review."*

This is not generic advice. The review found the same defect shape five
separate times — a boundary that did not preserve the guarantee its core
claimed (raw exceptions past a sanitiser, a silently-downgraded storage
backend, config that failed open, a wheel missing its own runtime assets).
Every one of those is a *contract* defect. Building consumers on unstable
contracts is precisely how they got there.

So: epics run **in sequence within a level**, and a level does not start until
the previous one's promotion gate is met.

## Status

**Level 0 (epic 1, boundary repair) is complete as of v1.1.0** — EER-001
through EER-020 closed or explicitly deferred; see `CHANGELOG.md`. Epic 2 is
partly done (runtime assets are packaged; the wheel/container test matrix is
in flight). Everything from epic 3 on is unstarted.

## Level 0 — Trustworthy artifact · *promotion: a repeatable release artifact with no known critical/high evidence-integrity defect*

| # | Epic | State |
|---|---|---|
| 1 | **Boundary repair** — EER-001..009 + published mitigations + regression tests | **DONE** (v1.0.1 + v1.1.0) |
| 2 | **Artifact contract** — packaged resources, wheel/sdist/container matrix, SBOM and provenance | **IN PROGRESS** |

## Level 1 — Trustworthy case · *promotion: another engineer can reproduce and audit a diagnosis offline*

| # | Epic | Depends on |
|---|---|---|
| 3 | **Incident schema** — `IncidentCase`, observation references, typed timeline, state machine, JSON Schema | epic 2 |
| 4 | **Capsule and replay** — scrubbed export, hashed/signed manifest, offline reproduction | epic 3 |
| 5 | **Evaluation runner** — dev/regression/sealed partitions, selective-risk report, release thresholds | epics 3–4 |

Note how well this fits what already exists: the ticket is already an
append-only flight recorder carrying the full model exchange, and the ledger
already separates *what was diagnosed* from *whether a human confirmed it*.
`IncidentCase` is the typed object those two are reaching toward.

## Level 2 — Trustworthy next action · *promotion: measurably reduces time to the correct next action on held-out cases*

| # | Epic | Depends on |
|---|---|---|
| 6 | **Change evidence** — one config-history adapter, mechanism-aware correlation | epic 3 |
| 7 | **Next-best probe** — finite candidate set, information/cost ranking, integration with current flows | epics 3, 5 |
| 8 | **L3VPN vertical slice** — VRF evidence, scoped identity, service descent, captured faults | epics 3, 5 |

Epic 7 is the one that changes the product's character: it turns an
abstention ("cause not localised") into a bounded, ranked next action. It is
also the one most able to do harm if built on an unvalidated descent, which
is why it sits behind the evaluation runner.

## Level 3 — Trustworthy workflow · *promotion: pilot SLOs and safety thresholds pass, durable handover, no automated changes*

| # | Epic | Depends on |
|---|---|---|
| 9 | **Incident workspace** — evidence/timeline/subgraph view, human verdict and handover | epic 3 |
| 10 | **Operational loop** — idempotent alert grouping, ticket synchronisation, tracing, shadow pilot | epics 3, 9 |

## Level 4 — Organizational platform · *only on measured demand*

Authenticated multi-user API, RBAC, durable job/case store, distributed
admission, capability-pack SDK, platform/protocol packs. Explicitly gated on
demand plus an independent security review — not on readiness.

## Standing non-goals

The review's "do not build yet" list, adopted as decisions so they are refused
by reference rather than re-argued each time:

- **A generic autonomous troubleshooting agent.** The gated `nettools agent`
  is useful research; free-form autonomy is not the product.
- **Autonomous configuration or remediation.** Read-only diagnosis and change
  execution have different safety architectures. If it is ever built it is a
  separate surface with human authorisation, pre/post checks, rollback and a
  blast-radius policy — not an extension of this one.
- **Broad multi-vendor support by translation.** Add a platform only with real
  devices and a captured corpus behind it.
- **A vector database as "long-term memory."** Truth belongs in typed cases,
  evidence and the ledger.
- **Direct gNMI for novelty.** Prometheus meets the present need; reopen only
  on a measured gap.
- **Unqualified full-config ingestion.** Narrow section reads only.
- **A causal device-health ladder.** Health is an aggregation over independent
  signals, not a dependency descent — this project already investigated and
  refused exactly that (B-108).
- **Automatic event-triggered investigations before precision is known.**
  Routing stays dry-run until the trigger table earns more than that.
- **Predictive-failure ML.** Labelled volume and feature semantics are not
  there. Build the evaluation corpus first.
- **A chat-first UI.** Chat can navigate a case; it cannot be the case.

## Metrics that should govern these decisions

Not velocity. The review proposes three families, all of which this codebase
is already unusually well positioned to measure: **safety and truth**
(abstention correctness, grounding failures, false-clean rate), **operator
value** (time to correct next action, verdict-confirmed rate from the ledger),
and **system reliability and cost** (evidence freshness, session count per
investigation, tokens per case).

The false-clean rate deserves emphasis: EER-005 was exactly that defect
(SQLite silently turning a real flap into a clean result), and nothing was
measuring it.
