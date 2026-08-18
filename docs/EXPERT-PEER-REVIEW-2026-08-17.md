# Expert Peer Review and Next-Level Roadmap

**Application:** IOS-XR Read-Only Network Tools (`agent-nettools` 0.2.0)  
**Review date:** 2026-08-17  
**Reviewed revision:** `e018aab` (`feat/investigation-layer`)  
**Scope:** application architecture, correctness, safety, security, performance,
operability, testing, packaging, product capabilities, and repository cleanup.  
**Review mode:** source inspection and offline verification. No device configuration was
changed and no live-lab fault was injected.

---

## 1. Executive verdict

This is a strong research-grade, single-operator network-diagnostics application with an
unusually good safety core. Exact command allowlisting, parameter reconstruction,
three-state checks, temporal-coherence handling, deterministic descent, citation checks,
fixture replay, and adversarial regression tests are substantially better than what most
early-stage AIOps tools implement.

The application is ready for **attended lab use and production shadow evaluation**. It is
not yet ready to page, close incidents, choose escalation ownership, or recommend/execute
remediation without an engineer reviewing the authoritative payload.

The most important conclusion is that the application currently contains **two trust
classes that are not consistently separated**:

1. `nettools investigate` is a deterministic, typed, temporally checked diagnostic path.
   Its authoritative report is rendered from code-owned fields.
2. `nettools analyze`, fabric analysis, and `nettools agent` are model-authored paths. They
   do not inherit all of the deterministic path's evidence and grounding guarantees.

That distinction is acknowledged in parts of the documentation, but a repository-wide
invariant still says that no unparsed device text reaches a model. The implementation
contradicts it: the legacy analysis path serializes the full evidence envelope, fabric
analysis deliberately falls back to raw command output after a parse failure, and the
tool-calling agent returns unsanitized tool envelopes to the model. The agent's final
free-text answer is also not grounded by `ground_report()`.

### Overall scorecard

| Area | Rating | Summary |
|---|---:|---|
| Device mutation safety | **A** | Exact read-only allowlist; no generic command executor; validation precedes credential access. |
| Deterministic diagnosis | **A-** | Excellent bounded design, but only two flows are implemented and the live evidence base is still small. |
| Model-output trust | **C** | Strong on `investigate`; materially weaker on `analyze`, fabric analysis, and the free-form agent. |
| Security/privacy | **B- for the lab; D for multi-user deployment** | Good secret discipline and MCP sanitization; no authentication/RBAC, incomplete raw-evidence boundary, cloud-data controls absent. |
| Performance/scalability | **C+** | Batching and some parallelism exist; device-side login cost, serial paths, soft budgets, and no fleet admission control remain. |
| Reliability/operability | **B-** | Metrics, retries, audit logs, snapshots, and notifications exist; persistence and concurrency guarantees are best-effort. |
| Testing | **A-** | 1,813 clean-source tests plus adversarial/mutation work; CI matrix, type checks, coverage targets, performance tests, and production holdouts are missing. |
| Maintainability | **C+** | Clear intent and excellent comments, but several 1,000–2,000-line modules and extensive historical documentation raise change cost. |
| Product readiness | **B for a lab / C- for production** | A credible diagnostic kernel, not yet a complete incident-management product. |

### Immediate release decision

- **Allow:** attended lab use, fixture demonstrations, read-only shadow runs, engineering
  evaluation, and deterministic `investigate` output with its caveats preserved.
- **Do not yet allow:** autonomous paging/closure, multi-user exposure, automated
  remediation, or treating `agent`/`analyze` prose as authoritative.
- **Release blockers for stronger trust:** close findings P0-01 through P0-03, refresh the
  stale live baseline, measure the coherence margin, and complete a randomized holdout
  evaluation.

---

## 2. What was verified

### Commands and results

| Check | Result |
|---|---|
| Ruff (`.venv/bin/ruff check .`) | **Pass** |
| Existing local pytest cache (`.venv/bin/pytest -q`) | **1 failure, 1,812 passed, 24 skipped** |
| Fresh bytecode cache (`PYTHONPYCACHEPREFIX=/tmp/... .venv/bin/pytest -q`) | **1,813 passed, 24 skipped** |
| Python dependency consistency (`python -m pip check`) | **Pass** |
| Git patch whitespace (`git diff --check`) | **Pass** |
| Git worktree before this report | Untracked `docs/archive/BACKLOG-STATUS.md`; preserved unchanged |

The initial test failure is important. `descent.py` contains
`Aggregation.ANY_HEALTHY`, but the local interpreter loaded stale bytecode containing
`Aggregation.ALL_HEALTHY`. This altered the executed diagnosis while `git status` was
clean. The untracked backlog-status document independently records that a mutation-test
harness restored source without invalidating compiled bytecode. A fresh cache passes all
tests, so this is not a current source regression; it is a contaminated local runtime that
can still execute the wrong code.

### Scale of the reviewed tree

- Approximately **38,674 lines** across Python/shell source and tests.
- Largest production modules:
  - `network_tools.py`: 1,981 lines
  - `template_parsers.py`: 1,742 lines
  - `checks.py`: 1,535 lines
  - `cli.py`: 1,148 lines
  - `grounding.py`: 973 lines
- Workspace footprint: approximately **200 MiB**.
  - `.venv`: 160 MiB
  - `.git`: 30 MiB
  - tests/fixtures and tests: 6.1 MiB
  - docs: 1.5 MiB
- Local generated state includes 5,144 `.pyc` files (mostly in `.venv`), 470
  `__pycache__` directories, build output, egg metadata, two documentation backups,
  caches, a preflight log, and transient evidence.

### Review limitations

This pass did not contact the live lab, inject a new failure, run provider APIs, inspect a
deployed container, or perform an external dependency-vulnerability scan. The 24 skipped
tests are live-lab tests by design. Findings about live diagnostic accuracy therefore use
the repository's committed evidence and recorded trials; they are not a new production
accuracy measurement.

---

## 3. Architecture assessment

### The strongest design choices

1. **The safety boundary is code, not prompt wording.** Commands are exact-match
   allowlisted per platform, checked before credentials are loaded, and parameterized
   commands are rebuilt from canonical typed values.
2. **Absence is not health.** `healthy`, `broken`, and `unevaluated` prevent failed parsing
   or missing evidence from becoming a reassuring zero.
3. **Diagnosis and prose are separated on the main path.** The deterministic descent
   reaches a finding without a model, and the authoritative report is rendered from typed
   results.
4. **Temporal coherence is treated as part of correctness.** The epoch implementation and
   re-read checks address the otherwise serious risk of constructing a causal chain from
   states that never coexisted.
5. **The test philosophy is mature.** Fixture replay, true negatives, regression pinning,
   guard mutation, and explicit vacuity checks are excellent practices.
6. **MCP raw-output sanitization is structural.** Applying `sanitize()` at registration is
   much safer than requiring every tool author or caller to remember a flag.
7. **The application communicates uncertainty.** Coverage caveats, temporal caveats,
   `subject_not_found`, `cause_not_localised`, and `no_fault_on_path` are materially better
   than forcing every outcome into “healthy” or “root cause found.”

### The important trust-boundary mismatch

| Path | Device text given to model | Deterministic finding | Grounding gate | Recommended trust |
|---|---|---:|---:|---|
| `investigate` authoritative report | Typed descent fields only | Yes | Report rendered by code | **Authoritative within flow scope** |
| `investigate --paraphrase` | Typed prompt | Yes | Yes, paraphrase is non-authoritative | Advisory wording only |
| MCP tools | Parsed fields; raw keys sanitized | Depends on tool | No general prose gate | Structured evidence only |
| `analyze` | Full evidence JSON, including raw command output | No | No | Exploratory only |
| Fabric analysis | Parsed data when available; raw output on parse failure | Health rules only | No | Exploratory only |
| `agent` | Unsanitized tool results, including command output | No | No | Exploratory only |

The architecture should make these classes impossible to confuse in code, schemas, CLI
output, documentation, and metrics.

---

## 4. Prioritized findings

Priority meanings:

- **P0:** address before relying on the affected path.
- **P1:** address before production or multi-user deployment.
- **P2:** important hardening or maintainability work.
- **P3:** polish or longer-term improvement.

### P0-01 — The local runtime is executing stale diagnostic bytecode

**Evidence.** The source at `descent._rung_subjects()` returns
`Aggregation.ANY_HEALTHY` when reverse-path members exist. The normal local test run
executed bytecode returning `ALL_HEALTHY`; disassembly confirmed the mismatch. A fresh
`PYTHONPYCACHEPREFIX` made the isolated test and all 1,813 source-level tests pass.

**Impact.** A clean source tree is not sufficient evidence that the locally executed
diagnostic logic matches it. The affected aggregation can change whether a redundant path
is reported healthy or broken.

**Recommendation.** Before the next application run:

1. Preserve/commit any intended untracked documents.
2. Remove repository-local Python caches and recreate `.venv` from declared dependencies.
3. Make mutation tooling run in a disposable checkout or with an isolated pycache.
4. End every mutation run with a fresh-source verification that cannot read the mutated
   cache.
5. Add a preflight check comparing source and loaded function origin/cache freshness for
   the critical descent modules, or set `PYTHONDONTWRITEBYTECODE=1` for development
   verification commands.

**Acceptance criterion.** Normal and isolated-cache test runs both report 1,813 passing;
disassembly/import inspection agrees with source; the mutation harness cannot leave an
executable artifact behind.

### P0-02 — The “no unparsed device text reaches a model” invariant is false

**Evidence.** `llm_analysis.build_analysis_prompt()` and
`_anthropic_user_content()` serialize the full evidence dict. Evidence envelopes carry
raw output under `data.commands`. `evidence_budget._section_text()` explicitly falls back
to raw command output after parse failure. `agent_loop._execute_tool()` returns normal
unsanitized network-tool envelopes, which are JSON-serialized into `tool_result` content.
Tests protect the invariant on report/correlation and MCP paths, but not on these paths;
one evidence-budget test explicitly requires raw fallback.

**Impact.** The mismatch creates four risks:

- prompt injection from log, banner, hostname, description, or other device-controlled
  text;
- unintended transmission of configuration-derived or operational data to cloud models;
- token/cost inflation and weaker reasoning over CLI-formatted text;
- a false assurance in contributor/security documentation.

**Recommendation.** Move one model-egress projector into `src/agent_nettools` and require
every model path to use it. It should emit typed parsed fields, provenance, parse/coverage
status, and withheld counts—not raw fallback. If parsing fails, the model should receive
`unavailable/unparsed` plus safe metadata and the authoritative path should refuse any
claim that requires the missing data. Add an explicit, separately named operator override
only if raw cloud egress is a product requirement; make it default-off, provider-aware,
audited, and visibly non-authoritative.

**Acceptance criterion.** A canary raw string inserted into every tool/envelope field fails
to appear in prompts or tool results for `investigate`, MCP, `analyze`, fabric analysis,
and `agent`. This must be a parameterized test over all model-egress paths.

### P0-03 — The tool-calling agent emits ungrounded prose under a “grounded answer” prompt

**Evidence.** `run_agent_loop()` executes tools selected by the model and prints the final
text directly. It does not call deterministic descent, `ground_report()`, citation
containment, coverage checks, or a structured-output validator. Unknown model stop reasons
are treated as `end_turn`. The system prompt asks for a grounded answer, but that is an
instruction, not an enforcement mechanism.

**Impact.** The most flexible user-facing path can omit checks, misattribute a real field,
invent a relationship between real identifiers, or offer an incomplete answer with a
successful exit code. Its safety from device mutation does not imply diagnostic
correctness.

**Recommendation.** Formally split the product into:

- **Classifier:** deterministic `investigate`, authoritative and machine-consumable.
- **Explorer:** model-selected read-only tools, explicitly non-authoritative.

The explorer should return a typed claim graph: each factual claim cites a tool-call ID and
JSON pointer; citations and identifier containment must validate before prose is shown.
Prefer invoking deterministic flows as tools when one applies. Render a large
`trust_class: exploratory` field in JSON and human output. Treat unknown stop reasons,
budget exhaustion, and tool failures as incomplete, never `end_turn` success.

**Acceptance criterion.** Removing citations, inventing an identifier, swapping device
attribution, skipping a requested check, or returning an unknown stop reason causes a
structured non-authoritative/incomplete result and a non-zero answer-health exit.

### P1-01 — The agent's wall-clock budget is not a hard budget

The deadline is checked only between model turns. One model request, SSH call, fabric
check, or sequential group of tool calls can run beyond it. The module calls the budget
“hard,” which the implementation cannot guarantee. Multiple tool-use blocks are also
executed sequentially even though the documentation describes parallel requests.

**Fix.** Use an absolute deadline propagated through provider and transport timeouts;
cancel/not-start work when remaining time is insufficient. Execute independent tool blocks
through a bounded pool, with per-device admission limits. Report elapsed and overrun.

### P1-02 — No identity, authorization, or tenant boundary exists

This is honestly documented as a single-operator lab. `NETTOOLS_ACTOR` is caller-controlled
provenance, not identity. Any process able to launch or reach MCP gets the entire inventory
and the configured device credentials' read capability. Telegram chat IDs are delivery
destinations, not inbound authorization.

**Fix before service deployment.** Add authenticated transport, user/service identity,
per-site/device scopes, RBAC for passive reads versus active probes, credential-broker
integration, immutable audit IDs, and rate limits per principal. Do not retrofit this by
trusting environment variables supplied by the caller.

### P1-03 — Active probes are registered with the same `readOnlyHint` as passive reads

Ping and traceroute generate traffic and can be expensive or policy-sensitive, yet the
registration wrapper applies the same read-only annotation to every MCP tool. They are
gated by `NETTOOLS_ALLOW_ACTIVE_PROBES`, which is good, but clients may use annotations to
auto-approve calls.

**Fix.** Define separate annotations/capability classes for passive reads and active
probes. Add per-target rate limits, maximum count/TTL, concurrency limits, destination
policy, and an audit field identifying active traffic generation.

### P1-04 — The live topology baseline is known to be stale

Backlog B-465 records that expected IS-IS counts were learned from a previously broken
fabric. Direction-aware drift now avoids treating an improvement as a fault, but the
baseline itself still does not represent today's healthy lab.

**Impact.** Health output can normalize old faults or produce informational drift that
operators must reinterpret. “Golden” and “expected” are weaker words than the UI implies.

**Fix.** Establish a reviewed baseline lifecycle: candidate capture, invariant checks,
human approval, version/provenance, expiry, and rollback. Relearn the healthy lab only
after an independent health check; never let the model approve or mutate it.

### P1-05 — The coherence threshold has little operating margin

Backlog B-466 records a healthy investigation at 23.1 seconds against a 30-second bound.
One retry or device-side login penalty can qualify/refuse an otherwise stable result.

**Fix.** Collect a latency/skew distribution across healthy, degraded, and login-throttled
runs. Track p50/p95/p99 and percentage of the bound. Reduce sessions/fan-out where the
flow allows; then calibrate a per-flow bound from protocol semantics and observed cost.
Do not simply widen the threshold to hide collection latency.

### P1-06 — Only two deterministic flows are implemented

`bgp_session` and `interface` are implemented; IS-IS adjacency, LDP session, L3VPN
service, device health, and topology remain roadmap items. The current BGP subject is also
an IPv4 peer address, which is insufficient for VRFs, multiple BGP instances, IPv6,
AFI/SAFI-specific state, and common eBGP link-address cases.

**Fix.** Introduce a stable object identity containing device, network instance, protocol
instance, peer/local address where relevant, and address family. Build the IS-IS flow next
to prove the pattern on a second protocol before parallel flow expansion.

### P1-07 — Evidence and metrics persistence are not crash- or concurrency-safe

The file evidence backend writes snapshots and golden baselines directly to final paths.
A crash can leave truncated JSON. The metrics file also uses direct read/modify/write and
only an in-process lock, so multiple CLI/MCP processes can overwrite each other's counts.
Audit-log rotation is called concurrently by fabric worker threads without a rotation
lock. SQLite golden replacement deletes and inserts in separate transactions/connections,
and the schema does not enforce one golden row per device.

The file backend also joins `evidence["device"]` directly into a path. Normal callers use
validated inventory names, but the storage API itself does not enforce that invariant.

**Fix.** Use temporary-file + fsync + atomic replace for JSON/metrics, inter-process locks
where files remain, and structured corruption handling. In SQLite, replace golden state in
one transaction and enforce a partial unique index. Validate device IDs at the storage
boundary. Prefer SQLite as the production default after migration/backup tooling exists.

### P1-08 — Some whole-fabric paths are serial and there is no global admission control

`check_fabric()` has bounded parallel collection, but `assess_lab_fabric_health()` and
the agent's fabric health helper use a serial dict comprehension over devices. More
generally, separate MCP calls can create independent pools/sessions with no process-wide
or per-device concurrency budget.

**Impact.** Slow answers, VTY/login-throttle amplification, inconsistent evidence windows,
and self-induced `unevaluated` results during an incident burst.

**Fix.** Build one collection scheduler shared by CLI, MCP, agent, health, and
investigation. It should deduplicate in-flight reads, cache briefly by evidence epoch,
enforce global/per-device limits, prioritize targeted investigations over broad surveys,
and expose queue/collection latency separately.

### P1-09 — Packaging is not reproducible enough for an operational tool

Dependencies use broad lower bounds, there is no lock/constraints file, the container base
uses a floating tag rather than a digest, and CI tests only Python 3.11 even though the
package declares `>=3.10` and development currently runs 3.13. A clean install can resolve
different transitive versions on different days.

**Fix.** Define supported Python upper/lower bounds, test the complete matrix, generate a
reviewed lock/constraints file, build/test wheels and the container in CI, pin the base
image digest, add dependency/SBOM scanning, and exercise the minimal installation separately
from `[dev,llm]`.

### P1-10 — Live accuracy evidence is promising but too small for automation

The repository is admirably candid: one vendor, one bounded lab, a few blind trials,
single-fault emphasis, and several rounds whose verdicts exist without replayable payloads.
This establishes feasibility, not a stable false-positive or wrong-device rate.

**Fix.** Create development, sealed holdout, and production-shadow datasets. Randomize
fault class and target; include healthy controls, unrelated faults, convergence, parser
degradation, simultaneous faults, stale logs, and load. Report confusion matrices by flow,
finding, OS release, and evidence-coverage class.

### P1-11 — Notification delivery has no durable reliability contract

Telegram delivery is intentionally best-effort and cannot affect diagnosis, which is the
right failure isolation. It also means a process exit, transient outage, or partial
multi-chat failure loses the notification, with no queue, retry receipt, idempotency key,
or dead-letter state.

**Fix.** Keep diagnosis independent, but emit a durable report event with investigation
ID and content hash. Deliver asynchronously with bounded retries and expose delivery SLOs.
Retain the direct notifier only as a simple lab adapter.

### P2-01 — Core modules are too large and mix multiple reasons to change

`network_tools.py`, `checks.py`, `template_parsers.py`, and `cli.py` are each over 1,100
lines. Extensive comments improve local readability but do not reduce merge conflicts,
import surface, or regression radius.

**Fix.** Split by stable responsibility, not arbitrary line count:

- transport/session/retry/audit;
- intent collection and template collection;
- fabric scheduler;
- snapshots/diff/flaps;
- protocol-specific checks and parsers;
- CLI command groups;
- model-egress and trust-policy modules.

Keep registries as the composition layer so the safety surface remains enumerable.

### P2-02 — Invalid configuration often silently falls back to defaults

Several integer/float environment readers return defaults for malformed or out-of-range
values. This is convenient locally but makes a typo in timeouts, retries, worker counts,
budgets, retention, or log rotation invisible.

**Fix.** Parse all configuration into one validated settings model. Fail startup for
invalid safety/operational values; allow explicit warnings only for cosmetic options.
Expose an effective, secret-redacted configuration through `nettools config check/show`.

### P2-03 — Quality gates do not include typing, coverage policy, complexity, or security

Ruff and pytest are strong but incomplete. There is no static type check, coverage
threshold/diff coverage, dependency audit, secret scan, or performance regression suite.
Large dict-shaped payloads make type drift particularly easy.

**Fix.** Add pyright/mypy incrementally at module boundaries, TypedDict/Pydantic models for
public envelopes, branch coverage for safety-critical packages, `pip-audit` or equivalent,
secret scanning, and benchmark budgets for collection, parsing, grounding, and prompt size.

### P2-04 — Documentation has multiple competing status authorities

The repository contains historical reviews, a tracker, findings log, backlog, completion
plan, verification report, session handover, peer-review brief, backlog status, round
reports, and an old general review. Their history is valuable, but current truth is hard to
identify and counts have drifted (for example, `CONTRIBUTING.md` says 1,776 tests while the
clean source suite has 1,813).

**Fix.** Preserve historical documents immutably under a clearly labeled archive. Keep one
current roadmap/status source and generate indexes/counts from it. Add `last_verified_at`
and revision to operational claims. Do not delete the append-only evidence history.

### P2-05 — Error and trust semantics vary by front end

The CLI documents that exit code 2 means an answer problem for `investigate` but a severe
network outcome for `health`. Agent errors are printed directly and an unknown stop reason
becomes success. MCP returns structured envelopes but tool-specific shapes differ.

**Fix.** Define a versioned top-level result envelope with separate axes:
`execution_status`, `network_status`, `answer_status`, `trust_class`, `coverage`, and
`retryable`. CLI exit codes then derive from a named policy instead of overloading one
integer across commands.

### P2-06 — Raw-text sanitization is key-based and should be schema-driven

MCP sanitization is well designed, but it relies on known key names such as `commands` and
`unaccounted_lines`. A future parser can introduce a differently named raw field.

**Fix.** Represent raw text with a distinct type/provenance tag at ingestion and make model
projection accept only safe typed nodes. Retain the recursive key-based scanner as a
defense-in-depth test, not the primary classifier.

### P3 items

- Replace stale test counts in contributor docs with generated output or omit the number.
- Add package authors/maintainers and release metadata before publishing.
- Add a `SECURITY.md` describing the lab threat model, disclosure path, and unsupported
  deployment modes.
- Add user-facing shell completion and examples for object identities and exit semantics.
- Add structured notification formatting with message IDs and links to stored reports.
- Add a small HTML/static report viewer for evidence, causal chain, caveats, and timeline.

---

## 5. Performance assessment

### Current strengths

- Multiple commands can share one Netmiko session.
- Fabric checks have a bounded thread pool and a streaming iterator.
- Retries, backoff, connect/read/banner timeouts, and retry metrics exist.
- Evidence epochs reduce repeated reads and retain observation spans.
- Fabric evidence has per-intent and total prompt budgets.
- Deterministic reports avoid model latency on the authoritative path.

### Highest-value performance work

1. **Unify collection scheduling.** This has more value than an async rewrite by itself.
   Deduplication, per-device concurrency, priority, and epoch reuse prevent redundant SSH
   work regardless of sync/async transport.
2. **Parallelize independent agent tool calls and fabric-health collection.** Respect one
   global budget and device limits.
3. **Measure before changing the coherence threshold.** Optimize sessions first, then fit
   bounds to the observed distribution and protocol timer semantics.
4. **Add short-lived parsed-evidence caching.** Key by device, command manifest, and epoch;
   never reuse across a configured freshness ceiling.
5. **Stream/reduce early.** For fleet health, parse and produce verdicts per device instead
   of retaining raw whole-fabric evidence.
6. **Track end-to-end latency components.** Queue, connection, command, parse, descent,
   grounding, model, and notification times should be separate histograms.
7. **Set explicit service targets.** Suggested initial lab targets:
   - targeted passive check p95 < 10 s without device throttle;
   - deterministic BGP investigation p95 comfortably below 50% of its coherence bound;
   - no more than one concurrent login per device;
   - zero investigations refused solely because the tool exceeded its own healthy-run
     latency budget in a 100-run soak.

An async transport should come only after the scheduler contract is stable. Async code
without admission control can overload devices faster.

---

## 6. Features and capabilities that would take the product forward

### Tier 1 — Make the trusted kernel broader

1. **IS-IS adjacency flow.** This is the best next proof that the deterministic-flow model
   generalizes beyond BGP.
2. **L3VPN service flow.** Use stable `<PE, VRF>` identity, then walk attachment,
   route-target/control-plane, transport label/tunnel, and forwarding installation.
3. **LDP/SR transport flow.** Separate session/control state from installed label/SID and
   forwarding state.
4. **Device-health and topology flows.** Turn existing checks into explicit dependency
   paths with trustworthy terminal findings.
5. **Intent-versus-observed diagnosis.** Collect effective configuration, resolve
   inheritance, compare intended object state with operational state, and keep config
   reads separate from any write capability.

### Tier 2 — Become an incident system rather than a command wrapper

1. **Event-driven investigations.** Alertmanager/webhook input → authenticated event →
   deduplication/correlation → deterministic flow selection → report event → durable
   notification.
2. **Case timeline and session memory.** Store investigation IDs, evidence epochs,
   findings, operator notes, acknowledgements, and repeat occurrences. Model-authored text
   must never become baseline truth.
3. **Competing hypotheses.** Report all explanations still consistent with the evidence
   and the next safe observation that would discriminate them. Do not force every case to
   one root cause.
4. **Multi-fault handling.** Use violated intent, independent evidence per rung, event
   episodes, and human-controlled intervention/re-descent where observation alone cannot
   separate masked faults.
5. **Topology graph and blast radius.** Build a versioned graph from inventory plus
   observed adjacencies; show which sessions/services depend on the failed object and how
   much redundancy remains.
6. **Service and forwarding validation.** Add RIB/FIB/CEF/MPLS/SR/EVPN evidence and bounded
   active probes so “control plane healthy” is not mistaken for “service works.”

### Tier 3 — Operator experience

1. **Web report viewer/dashboard.** Show finding, causal chain, evidence citations,
   coherence window, coverage gaps, off-path observations, historical recurrence, and raw
   evidence only to authorized humans.
2. **Guided investigation.** Let operators choose an object from observed inventory rather
   than type free-form addresses, reducing wrong-object investigations.
3. **Change validation workflow.** Human pins pre-change intent, performs the change
   outside this application, and the tool verifies pre/post invariants and rollback
   criteria.
4. **Explainability export.** Versioned JSON and HTML/PDF incident bundles with hashes,
   timestamps, source versions, parser versions, and tool configuration.
5. **SLO and confidence dashboard.** Accuracy by finding, refusal rate, parse coverage,
   coherence margin, model paraphrase-withheld rate, collection latency, and notification
   delivery.

### Tier 4 — Platform and ecosystem

1. **Junos support only with real captured evidence.** Add semantic equivalence tests, not
   only schema conformance.
2. **Northbound API.** A small authenticated service API for investigations and stored
   reports, while retaining CLI and MCP as clients of the same application layer.
3. **Pluggable event/log sources.** Loki/Prometheus/Alertmanager adapters behind normalized
   contracts with explicit coverage and clock-skew metadata.
4. **Enterprise secret providers.** Vault/cloud secret manager integration with short-lived
   credentials and per-scope authorization.
5. **Human-approved remediation plans.** Keep this physically and logically separate from
   the read-only diagnostic runtime. Start with generated, reviewed plans; do not add a
   generic config executor.

---

## 7. Deep cleanup recommendation

The cleanup goal should be **one obvious runtime, one authoritative status source, one
model-egress boundary, and smaller modules with the same safety properties**. Cleanup must
not erase the evidence history that makes the project credible.

### Phase 0 — Recover a trustworthy local runtime (immediate)

1. Commit or deliberately discard the untracked `docs/archive/BACKLOG-STATUS.md` after human
   review; do not let cleanup remove it accidentally.
2. Recreate `.venv` instead of trying to surgically repair its cache.
3. Remove repository-local `__pycache__`, `.pyc`, `.pytest_cache`, `.ruff_cache`, `build/`,
   and root `agent_nettools.egg-info/` through a reviewed cleanup command.
4. Compare the two `.docs-backup-*` directories with current tracked docs. Archive outside
   the repo or delete only after differences are understood.
5. Decide whether `preflight-20260817-191323.log` and `evidence/PE1/...json` are records to
   preserve. If not, move them to an operator-owned archive or remove them; do not mix
   transient runtime state with source.
6. Preserve `evidence-archive/` and `tests/fixtures/`; they are product evidence, not cache.

Expected reclaim is roughly **160+ MiB** from the venv and generated files, while the
tracked evidence corpus remains intact.

### Phase 1 — Consolidate boundaries before reorganizing files

1. Implement one typed model-egress projector and route all six model surfaces through it.
2. Define the versioned top-level result/trust envelope.
3. Introduce one validated settings model.
4. Introduce one collection scheduler/admission-control interface.

Doing these first prevents a file split from creating more copies of the current policy.

### Phase 2 — Split high-churn modules

Suggested target structure:

```text
src/agent_nettools/
  application/
    investigate.py
    analyze.py
    health_service.py
  domain/
    evidence.py
    findings.py
    identity.py
    flows/
      bgp.py
      interface.py
      isis.py
    checks/
      bgp.py
      route.py
      isis.py
      interface.py
  infrastructure/
    transport/netmiko.py
    collection/scheduler.py
    persistence/files.py
    persistence/sqlite.py
    llm/providers.py
    notifications/telegram.py
  interfaces/
    cli/
    mcp/
```

The exact directory names are less important than dependency direction: domain code must
not import CLI, MCP, Netmiko, provider SDKs, or persistence implementations.

### Phase 3 — Simplify tests without reducing assurance

1. Group tests into unit, contract/safety, fixture integration, provider contract, and
   live-lab suites.
2. Keep frozen safety tests, but document their ownership and review process in one place.
3. Run mutation tests in disposable worktrees/containers with isolated bytecode caches.
4. Add generated test counts rather than prose counts.
5. Add a small set of end-to-end “golden path” tests that exercise installed console
   scripts and the built wheel/container.
6. Retain large fixture realism, but add manifests/hashes so missing or accidental fixture
   changes are immediately visible.

### Phase 4 — Rationalize documentation

Keep these current and authoritative:

- `README.md` — user value, quick start, limits.
- `docs/architecture.md` — current architecture and trust classes.
- `docs/operations.md` — deployment/runbook/SLOs.
- `docs/security.md` — threat model and data-egress policy.
- `docs/roadmap.md` — one current backlog/status source.
- `CHANGELOG.md` — release history.

Move historical build plans, round reports, prior reviews, findings, and handovers under
`docs/history/` with an index explaining that they are evidence, not current instructions.
Generate status counts and cross-links. Keep `FINDINGS.md` append-only.

### Phase 5 — Release hygiene

1. Build wheel and sdist in CI; install each into a clean environment and run smoke tests.
2. Add a supported Python matrix and constraints/lock file.
3. Pin the container base digest, add a health/build metadata label, and scan it.
4. Publish SBOM, dependency-audit result, checksums, and signed release artifacts.
5. Remove local `.env` reliance from deployment examples in favor of secret mounts or a
   credential provider.

### What not to clean up

- Do not remove three-state verdicts to make APIs simpler.
- Do not merge exploratory agent output with authoritative investigation output.
- Do not replace exact allowlists with prefix/regex “show command” approval.
- Do not compress the append-only findings history into a polished narrative that loses
  corrections.
- Do not delete archived experiment payloads merely because they are not runtime code.
- Do not add remediation/config commands to the same process to reduce project count.

---

## 8. Recommended delivery plan

### First 7 days — restore trust and close contradictions

1. Rebuild the contaminated local environment and verify normal/fresh-cache parity.
2. Close P0-02 with a shared model-egress projector and regression matrix.
3. Label or temporarily disable `nettools agent` as authoritative; close P0-03 design.
4. Refresh the healthy topology baseline through a human-reviewed process.
5. Correct the documentation invariant and stale generated counts.

### Days 8–30 — production hardening foundation

1. Add typed result/trust envelopes and validated settings.
2. Make deadlines real and parallelize independent agent/fabric work safely.
3. Add atomic persistence and SQLite golden uniqueness/transactionality.
4. Add Python-version CI matrix, type checking, dependency audit, wheel/container tests.
5. Run healthy-lab skew/latency soak and decide the coherence policy from data.

### Days 31–60 — broaden trusted capability

1. Implement and blind-test the IS-IS adjacency flow.
2. Introduce stable, context-rich network object identities.
3. Build config/effective-intent comparison foundations.
4. Implement the shared collection scheduler with per-device admission control.
5. Start randomized holdout and shadow evaluation dashboards.

### Days 61–90 — become operationally useful

1. Add authenticated event ingestion, deduplication, durable report events, and reliable
   notification delivery.
2. Add the L3VPN service flow and forwarding-plane evidence.
3. Add case history/timeline and the operator report viewer.
4. Publish measured accuracy, refusal, latency, and coverage results by flow.
5. Reassess whether the application may page or recommend escalation; do not decide this
   from feature completion alone.

---

## 9. Definition of “next level”

The application reaches the next level when all of the following are true:

- every model path receives only the same explicitly approved typed evidence;
- authoritative and exploratory outputs are impossible to confuse;
- a hard deadline and admission controller bound the load applied to every device;
- baselines are human-approved, versioned, expiring, and recoverable;
- evidence writes are atomic and operational metrics survive concurrent processes;
- at least three materially different flows pass sealed holdout trials;
- object identity works across VRFs, instances, AFI/SAFI, IPv6, and eBGP contexts;
- service claims include forwarding-plane evidence, not control-plane state alone;
- production-shadow data establishes false-positive, wrong-device, refusal, and latency
  rates with confidence intervals;
- a user can see exactly what was checked, when, from which source version, with what
  coverage and caveats, and what remains unknown.

The current repository already contains the hardest part to retrofit later: a disciplined
read-only boundary and a deterministic diagnostic kernel. The next step is not to add more
model autonomy. It is to make every interface inherit the kernel's trust properties, then
broaden the kernel with measured protocol and service coverage.
