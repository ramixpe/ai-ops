# Ops wave — n8n, knowledge, and the MCP surface: judged, then planned

**2026-08-17.** The operator proposed three directions: n8n predefined flows
(troubleshooting / fact-gathering-for-LLM / audit), a knowledge MCP (RAG-shaped), and a
deep rethink of the exposed MCP tools. This document is the judgement first — including
what is over-engineered — then the implementation wave.

---

## 1. Judgement

### n8n — yes as plumbing, no as brain

**What holds up.** Scheduling, multi-step delivery, retries, and (later, Stage 3) human
approval nodes. The operator already runs a Docker platform stack; n8n fits it, and
"orchestration is configuration rather than code" is the same argument T-005 already
accepted for Alertmanager.

**What does not hold up, and the repo's own record says so:**

- **Troubleshooting logic in n8n flows.** Tier III flows are versioned code with golden
  tests, mutation guards, and grounding. An n8n flow is JSON in a GUI database — no
  frozen-file discipline, no §0.12 companions, nothing that can falsify it. Moving
  diagnosis there recreates the exact failure class 150+ findings were spent eliminating.
  **n8n calls `nettools investigate`; it never reimplements it.**
- **"Gather facts and present to the LLM" via n8n.** That routes evidence around the
  model-egress projector built this week (B-470/B-467). If an n8n LLM node stitches
  device output into a prompt, invariant 4 dies outside our boundary. **n8n never touches
  a device and never builds a prompt. It calls the CLI and routes the CLI's JSON.**
- **n8n as the alert trigger.** T-005 measured this: Alertmanager's native webhook
  receiver already does grouping, dedup, inhibition and repeat-suppression, and the stack
  already runs a Telegram relay container. *"n8n is not needed for it"* is the discovery
  document's own sentence. The genuine Stage-2 gap T-005 names is **alert rules carrying
  a device label** — rule authoring on the operator's stack, not our code.

**The boundary rule, stated once:** an orchestrator (n8n, cron, systemd, Alertmanager)
may *trigger* nettools and *route* its structured output. It may not touch devices,
build prompts, or hold diagnostic logic. That is what keeps every guarantee this
repository makes true regardless of what sits around it.

### Knowledge MCP — yes as declared items, no as RAG

The docs corpus is **1.6 MB across 43 markdown files**. A vector database, an embedding
pipeline, and a retrieval stack for a corpus grep answers in 40 ms is over-engineering by
two orders of magnitude — and it would introduce the one thing this architecture has
none of: an unreviewable, non-deterministic layer between a question and its source.

What the idea is *actually* pointing at, and all three are cheap and real:

1. **The repo is already the knowledge base.** Expose it: a `search_lab_knowledge` tool
   (plain text search over `docs/` + the glossary + inventory `notes:`) and MCP
   resources for the key documents. Version-controlled, reviewed, cited by path — RAG's
   benefits with none of its machinery.
2. **A curated mnemonic table.** T-004 catalogued IOS-XR mnemonics; a reviewed YAML
   mapping mnemonic → meaning → typical causes → which flow investigates it is
   *knowledge as a declared Tier-I item*, exactly the house pattern. An
   `explain_lab_mnemonic` tool reads it. No scraping, no embeddings, no licence
   questions.
3. **B-210, finally.** The inventory's `notes:` blocks have twice held correct findings
   nobody executed (OBS-139; the `bgp_no_prefixes` note). Surface matching notes in the
   `investigate` payload as `operator_notes`, so known conditions stop being
   rediscovered on every run. This was always the honest core of "give the model
   knowledge".

**General protocol knowledge (RFC/vendor RAG): rejected.** The navigating model's job is
tool selection, not protocol tutoring; the answering path uses no model at all; and the
prompts deliberately constrain the model to evidence. A protocol encyclopedia would be
feeding the one component whose job is to stay small.

### The MCP surface rethink — yes, as a parallel surface, so the measurement survives

B-113's consolidation (21 tools → ~5 stage-shaped) is already justified by arithmetic:
the reworded descriptions grew the manifest +86%, paid on every tool-list call. But
collapsing the surface **destroys the §9/§10 A/B** whose third arm (Q1 during a fault)
is still owed. The clean move, which Appendix A of `MCP-EXPERIMENT.md` itself names as
the *right* experiment:

> **Build the staged surface as a second, complete surface behind
> `NETTOOLS_MCP_SURFACE=classic|staged` (default `classic`).** Both surfaces exist;
> the between-surface A/B becomes possible; the default flips only after Q1 lands.

Five stage-shaped tools per the LLD (§4.1), each wrapping the same already-safe
functions, sanitised by the same registration boundary, probes still distinctly
annotated.

### Over-engineered / no value — the honest list

| Idea | Verdict |
|---|---|
| Vector-DB RAG over 1.6 MB of markdown | **No.** grep. |
| Diagnosis logic in n8n | **No.** Untestable logic is the failure class this repo exists to prevent |
| n8n LLM nodes fed device output | **No.** Bypasses the egress projector; invariant 4 dies outside the boundary |
| n8n as alert trigger | **No.** T-005: Alertmanager already does it natively |
| A northbound HTTP API server now | **Not yet.** The CLI's stable JSON is the API; a service wrapper is Tier-4 work after there is a second consumer |
| Audit logic defined in n8n | **No.** Audits are deterministic rules in `nettools`; n8n may *schedule* them |
| RFC/vendor-doc RAG | **No.** Wrong component to feed |

---

## 2. The wave — four agents, disjoint by design

Baseline: **1916 passed, 24 skipped** at `a8cd664`+. Same merge gates as FIX-PLAN:
full-diff review, suite in main, frozen files, mutation entries where cheap, backlog/
FINDINGS by the orchestrator.

| Agent | Item | Backlog | Files |
|---|---|---|---|
| **N-1** | `nettools audit` — deterministic fabric/device/protocol audit | **B-477** | NEW `audit.py`; `cli.py` (new subcommand only); tests |
| **N-2** | Knowledge surface: search tool, mnemonic table, `operator_notes` (closes **B-210**) | **B-478** | NEW `knowledge.py` + `data/mnemonics.yaml`; `investigation.py` (notes only); `mcp_server/server.py` (registrations); tests |
| **N-3** | Staged MCP surface behind a flag (advances **B-113** without killing the A/B) | **B-479** | NEW `mcp_server/staged_surface.py`; `server.py` (import+flag hook only); tests |
| **N-4** | Event routing (closes **B-202**'s core): `nettools route-event` + orchestrator examples | **B-480** | NEW `event_routing.py`; `cli.py` (new subcommand only); NEW `examples/n8n/` + `examples/README.md`; tests |

**What N-1 audits (operational state only — the config axis is B-104, Part 2):**
duplicate router-IDs fabric-wide; BGP timer asymmetry between the two ends of a session;
MTU mismatch across adjacent interfaces; IS-IS metric asymmetry; hostname-vs-inventory
drift (B-435's resolver reused); protocol-version/AFI consistency. Table-driven like
`health.py`'s rules; three categories (`device`, `protocol`, `fabric`); exit codes on
the health scheme.

**What N-4 deliberately is:** a *pure function* from an Alertmanager webhook JSON or a
raw syslog line to a typed `RoutingDecision` (flow, device, subject — or a named reason
it is not routable). The receiving process is the operator's infrastructure choice (n8n
Execute Command, a 10-line systemd service, the existing relay container); the repo
ships the decision logic and examples, not a listener. T-005's device-label gap is
documented as the operator's rule-authoring task.

**Known merge frictions, accepted:** N-1/N-4 both add a `cli.py` subcommand (distant
anchors; orchestrator resolves), N-2/N-3 both touch `server.py` (registrations
mid-file vs. a flag hook at the end; orchestrator resolves).

## 3. Explicitly deferred from this wave

`verify_fix`, `watch`, the optics rung, `fabric investigate` (all `next-level.md`,
awaiting the operator's pick); the packaging wave (P1-09); the module split (P2-01);
everything lab-gated.
