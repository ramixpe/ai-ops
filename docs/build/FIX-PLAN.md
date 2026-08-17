# Fix plan — both reviews' findings, implemented

**2026-08-17, at `3c26a77`.** Executes the reconciled priority order from
`DEEP-REVIEW-2026-08-17.md` §3. Already fixed before this plan: B-468, B-469, the
`run_templates` dead code, the audit-log retry accounting.

**Delegation per `BUILD-PLAN.md` §0.9**: Sonnet 5 receives precise specifications and
returns code plus tests; Opus 5 reviews every diff, runs the full suite in the main tree,
and accepts or rejects. No agent may touch the four frozen files, and every agent is told
so explicitly.

## Waves

Wave 1 runs in parallel, each agent in an isolated worktree, file sets disjoint by
design. Wave 2 depends on Wave 1-A's public contract and starts after A merges.

| Agent | Item | Backlog | Files |
|---|---|---|---|
| **1-A** | Model-egress projector + free-text typing | **B-470** (their P0-02) + **B-467** | NEW `model_egress.py`; `llm_analysis.py`, `evidence_budget.py`, `fabric_analysis.py`, `prompt_library.py` |
| **1-C** | Atomic persistence + guarded reads | **B-474** (their P1-07 + DEEP-REVIEW §2.4) | `evidence_store.py`, `metrics.py`, `network_tools.py` (flaps/summary only) |
| **1-D** | Active-probe MCP annotations + parallel fabric health | **B-473** (P1-03) + half of **B-475** (P1-08) | `mcp_server/server.py`, its tests |
| **1-E** | Settings validator + `nettools config` | **B-476** (their P2-02, scoped) | NEW `settings.py`; `cli.py` (new subcommand only) |
| **1-F** | `SECURITY.md`, stale counts, doc links | their P3 items | docs only |
| **2-B** | Agent loop: trust labels, real deadline, projector wiring, parallel tools | **B-471** (P0-03 cheap half) + **B-472** (P1-01) + rest of B-475 | `agent_loop.py`, `cli.py` (`_cmd_agent`) |

**Deliberately not in any wave:** the explorer claim-graph (their P0-03's expensive
half — a project, not a fix), RBAC (their P1-02 — deferred honestly per B-301), the
module split (their P2-01 — boundaries first, exactly as they argue), the `.venv`
rebuild (mine, last, because every merge gate runs tests through it).

## Merge gates, per agent

1. Diff reviewed by Opus in full — not skimmed.
2. Full suite green **in the main tree** after merge, lint clean.
3. Frozen files byte-identical to `6629a2c`.
4. New guards mutation-tested where cheap (`scripts/mutate_guards.py` gains entries).
5. Backlog row updated with the evidence, FINDINGS entry where a finding emerged.

## Cleanup (Opus, main tree, concurrent with Wave 1)

Per the expert review §7 Phase 0, and *only* Phase 0 — no file moves, no doc
restructuring before publication:

1. Delete repo-local `__pycache__`, `.pytest_cache`, `build/`, `agent_nettools.egg-info/`.
2. Diff the two `.docs-backup-*` directories against tracked docs; **report, do not
   delete** — that call is the operator's.
3. Inventory `preflight-*.log` and `evidence/` transients; report.
4. **Last, after all merges:** recreate `.venv` from declared dependencies and verify
   normal/fresh-cache parity (their P0-01 acceptance criterion).
