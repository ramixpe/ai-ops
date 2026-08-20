# Elite Engineering Review — IOS-XR Nettools

**Review date:** 2026-08-20  
**Reviewed revision:** `d39a78ccf370` on `main`  
**Review type:** Independent whole-application architecture, correctness, safety, security,
reliability, release, operability, and engineering-practice review  
**Decision:** **Do not promote beyond a single-user lab or tightly controlled shadow pilot yet.**

## 1. Executive verdict

This is a substantially better-engineered system than its size and nominal maturity suggest.
Its core product idea is sound: collect explicitly allowlisted, read-only network evidence;
normalize it into typed structures; make deterministic logic authoritative; and constrain any
model to grounded interpretation or presentation. The repository demonstrates unusually good
engineering instincts in refusal semantics, provenance, evidence freshness, concurrency
admission, offline reproducibility, mutation guards, and honest documentation of limitations.

The application is nevertheless not ready for unattended or broad production use. The primary
reason is not the diagnostic logic. It is that several boundary layers do not yet preserve the
guarantees claimed by the core:

1. The file evidence backend has a demonstrated path-traversal deletion vulnerability.
2. SSH host identity is not verified, so the evidence source is unauthenticated and credentials
   are exposed to a management-path attacker.
3. Built distributions omit runtime prompt assets and the flagship offline fixture corpus.
4. Flap detection silently ignores the configured SQLite evidence backend.
5. Exception paths can bypass the otherwise strong model/MCP egress sanitization boundary.
6. Invalid safety-relevant configuration often warns and continues or silently falls back.

An elite team would immediately freeze feature expansion, close those six boundary failures,
make the built artifact—not the source checkout—the tested unit, and then run a measured shadow
pilot with explicit abstention and accuracy targets. They would not add more model autonomy or
protocol breadth until the evidence path is authenticated, backend-independent, and proven as a
release artifact.

### Overall assessment

| Area | Assessment | Short verdict |
|---|---:|---|
| Product and domain model | 8/10 | Correctly treats network diagnosis as evidence and uncertainty management. |
| Deterministic safety design | 9/10 | Exact allowlists, read-only posture, and typed authoritative reporting are excellent. |
| Correctness | 7/10 | Very strong tests, but storage/backend integration defects are material. |
| Security | 5/10 | Strong command boundary; weak filesystem containment and SSH authenticity. |
| Reliability | 7/10 | Good timeouts, retries, epochs, and admission; inconsistent model deadlines and persistence. |
| Packaging and release | 4/10 | Editable-install CI masks broken installed-artifact behavior. |
| Observability and operations | 6/10 | Thoughtful metrics/audit concepts; concurrency and workflow completion remain incomplete. |
| Maintainability | 6/10 | Excellent intent and comments, but several very large modules and duplicated policy paths. |
| Production readiness | 4/10 | Suitable for lab use; shadow pilot only after release blockers are fixed. |

The scores are directional, not mathematical. The release decision is driven by the highest-risk
failure modes, not by averaging them away.

## 2. Scope, method, and limitations

The review covered the CLI, MCP surfaces, transport layer, command/template allowlists, inventory,
evidence stores, deterministic checks, investigation/descent logic, model boundary, agent loop,
external telemetry adapters, audit/metrics/ticket persistence, fixture workflows, packaging,
container, CI, tests, documentation, and backlog governance.

Evidence used:

- Source inspection across approximately 40,708 lines in `src/` and `mcp_server/`.
- Test inspection across approximately 41,977 lines in `tests/`.
- Full offline test execution: **3,185 passed, 22 skipped** in 32.83 seconds.
- Ruff execution: **passed**.
- Focused safety/security execution: **342 passed**.
- A real wheel build and import/load probe outside the checkout.
- Isolated `/tmp` adversarial probes for filesystem traversal, SQLite flap behavior, and exception
  egress. No repository data was used as a destructive target.
- Review of current product/security claims and the existing engineering-review/backlog material.

Limitations:

- The 22 live-lab tests were not run. This review does not independently certify behavior against
  real IOS-XR devices, failure under live SSH churn, or diagnostic accuracy on new incidents.
- No production deployment, multi-user service, Internet-facing MCP server, or enterprise identity
  integration was presented. The review evaluates the current single-user lab threat model and
  the consequences of expanding it.
- Dependency vulnerability results were not generated; the finding is that no such release gate
  exists, not that a particular dependency is known vulnerable.

## 3. What the application gets right

These are important strengths to retain during remediation.

### 3.1 The model is subordinate to evidence

The deterministic descent and health/reporting paths—not model prose—own the operational verdict.
The model-egress projector minimizes and bounds the evidence sent to providers, while grounding
checks can withhold unsupported paraphrases. This is the right architecture for operational AI:
models help interpret; they do not manufacture state.

### 3.2 Read-only command execution is structurally constrained

The transport executes named intents/templates through exact per-platform command specifications.
Arguments are reconstructed and validated before credentials or a session are opened. There is no
general-purpose CLI executor exposed to the model. Focused allowlist, injection, parameter,
model-egress, MCP-boundary, and frozen-file tests passed.

### 3.3 Uncertainty is first-class

The code distinguishes healthy, unhealthy, unsupported, refused, failed, stale, and unevaluated
states instead of collapsing missing evidence into health. Evidence epochs and freshness checks
make cross-device conclusions more defensible. This is precisely the mindset required for network
operations, where a false negative is often worse than an explicit refusal.

### 3.4 Concurrency protection is appropriate to the execution model

Admission control uses cross-process file locks rather than pretending an in-process semaphore can
protect multiple CLI/event-worker processes. The chosen default limits are documented with lab
measurements and the code refuses excess work instead of allowing a connection storm.

### 3.5 The verification culture is unusually strong

The repository has high test density, property tests, mutation guards, offline end-to-end tests,
frozen-surface checks, and comments that explain the invariant rather than merely restating code.
Corrupt evidence reads produce visible degradation instead of silently erasing history. File writes
use temporary files, `fsync`, and atomic replacement. This culture is the asset that will make the
remaining work tractable.

### 3.6 The project is candid about its boundary

The security and architecture documents explicitly describe a single-user lab tool, no RBAC,
limited protocol coverage, and no automated remediation. That candor should remain. The problem is
not the stated scope; it is a handful of implementations that fail even within that scope.

## 4. Prioritized findings

Severity definitions:

- **Critical:** release blocker; can destroy/escape evidence or invalidate the system's trust root.
- **High:** can silently produce wrong operational conclusions, leak untrusted data across a model
  boundary, or break a documented installed workflow.
- **Medium:** materially weakens reliability, operability, maintainability, or supply-chain posture.
- **Low:** hardening or developer-experience issue with limited immediate operational impact.

### EER-001 — Critical — Evidence prune can delete JSON outside the evidence root

**Evidence.** `_validate_device_name()` exists, but it is applied only on save. The file backend
joins arbitrary reader/pruner input directly under the root in
[`evidence_store.py`](../src/agent_nettools/evidence_store.py#L245); load/history paths use that join
without validation, and `prune()` unlinks the resulting `*.json` files at
[`evidence_store.py`](../src/agent_nettools/evidence_store.py#L303). The comment claiming inventory
already validates storage-safe names is contradicted by
[`inventory_model.py`](../src/agent_nettools/inventory_model.py#L132), where `Device.name` is an
unconstrained string.

An isolated reproduction created `evidence/` and adjacent `outside/victim.json`, then called:

```text
FileEvidenceStore(evidence_root).prune(device_name="../outside", keep_count=0)
```

The result reported one removal and `outside/victim.json` no longer existed. No repository file was
used. Read methods can likewise traverse and load a suitably named JSON file outside the root.

The fixture path has the same trust shape: platform, device name, and label are joined directly in
[`fixtures.py`](../src/agent_nettools/fixtures.py#L88).

**Impact.** A malicious or mistaken CLI value, inventory device name, fixture label, or direct API
caller can read or delete JSON beyond the configured evidence directory with the privileges of the
process. In a directory containing unrelated JSON, `evidence prune` is destructive.

**Required fix.** Create one storage-key policy and enforce it at every read, write, list, prune,
capture, and replay boundary. Reject `.`, `..`, separators, absolute paths, NULs, and unsupported
characters. After joining, resolve both root and candidate and assert containment. Validate
inventory names, fixture labels, platform keys, and CLI arguments before I/O. Treat containment as
defense in depth, not as a replacement for semantic validation.

**Acceptance gate.** Table-driven and property tests prove all evidence and fixture operations
reject traversal—including encoded/alternate separators where relevant—and a canary outside the
root remains byte-identical after every read/prune/capture API.

### EER-002 — Critical — SSH evidence has no authenticated source identity

**Evidence.** Netmiko connection parameters in
[`network_tools.py`](../src/agent_nettools/network_tools.py#L468) set device type, host, username,
port, and timeouts, but no host-key policy. With the installed Netmiko 4.7.0 defaults, a runtime
signature probe resolved `ssh_strict=False`, `system_host_keys=False`, and `alt_host_keys=False`.

**Impact.** A management-path attacker can impersonate a router, capture password credentials, and
return fabricated command output. Every later deterministic check can be internally correct while
operating on forged evidence. The real root of trust is therefore weaker than the model-grounding
and provenance layers above it.

**Required fix.** Enable strict host-key verification and load a managed, explicitly located
known-hosts file. Build a deliberate enrollment and rotation workflow: out-of-band fingerprint
verification, atomic updates, audit record, and fail-closed behavior on mismatch. Prefer per-device
key pinning where operationally feasible. Do not add an automatic “trust first use” fallback to an
unattended flow.

**Acceptance gate.** Tests assert strict parameters reach Netmiko. An integration harness proves
known key succeeds, unknown key refuses, changed key refuses loudly, and no command or credential
exchange proceeds after mismatch.

### EER-003 — High — The published wheel omits runtime prompts

**Evidence.** `prompt_library.py` locates prompts three parents above the installed module at
[`prompt_library.py`](../src/agent_nettools/prompt_library.py#L91). Package data includes only
`data/*.yaml` in [`pyproject.toml`](../pyproject.toml#L82). A wheel built from the reviewed revision
contained no `prompts/` directory. Loading `report.v2` from that wheel outside the checkout raised
`PromptNotFoundError` referencing an absent `/tmp/.../prompts/report.v2.txt`.

**Impact.** Installed LLM analysis/paraphrase paths can fail even though editable-install CI passes.
The versioned-prompt reproducibility guarantee exists in source but is not delivered to users.

**Required fix.** Move prompts inside the Python package, declare them as package data, and load via
`importlib.resources`. Build wheel and sdist once, install each into clean environments, then execute
all prompt builders and at least one fully offline investigation from outside the source tree.

**Acceptance gate.** The CI-tested wheel is the same artifact published or placed in the container;
all declared prompt versions are enumerated and load successfully from it.

### EER-004 — High — The flagship offline workflow is source-tree dependent

**Evidence.** Fixture lookup defaults to the relative path `tests/fixtures` and constructs paths in
[`fixtures.py`](../src/agent_nettools/fixtures.py#L84). The packaging configuration excludes
`tests*` at [`pyproject.toml`](../pyproject.toml#L85), and the built wheel contains no fixture corpus.
CI's offline-demo job installs with `pip install -e .` at
[`ci.yml`](../.github/workflows/ci.yml#L54), leaving the checkout available and masking the artifact
contract. The README presents the fixture investigation as a primary no-credentials workflow.

**Impact.** A normal package installation cannot reproduce the documented first-run experience.
This erodes operator trust precisely at the safest evaluation path.

**Required fix.** Decide the product contract explicitly. The recommended choice is to package a
small, scrubbed demonstration corpus under application resources and reserve the full test corpus
for tests. If source-checkout-only is intentional, say so prominently and provide a separate
installed smoke workflow. In either case, never resolve runtime assets relative to caller CWD.

**Acceptance gate.** `pip install artifact.whl` in an empty directory, with no network, credentials,
or repository, completes the documented demo and reaches all promised exit codes.

### EER-005 — High — SQLite mode silently disables flap detection

**Evidence.** Both evidence stores implement `list_history()`, but `detect_flaps()` bypasses the
abstraction and reads file paths directly in
[`network_tools.py`](../src/agent_nettools/network_tools.py#L2302). `get_store()` advertises files or
SQLite at [`evidence_store.py`](../src/agent_nettools/evidence_store.py#L537).

An isolated probe saved five alternating BGP states into `SQLiteEvidenceStore`. The backend returned
five history records; `detect_flaps()` returned `snapshots_examined: 0` and an empty flap list.

**Impact.** Enabling a supported backend changes a positive flap finding into a false clean result
without an error or “unsupported” state. This violates the application's most important rule:
missing evidence must not be reported as health.

**Required fix.** Make flap detection consume `EvidenceStore.list_history()` and report backend plus
coverage metadata. Remove parallel file-specific history readers. Until parity exists, refuse flap
detection in SQLite mode rather than returning an empty result.

**Acceptance gate.** A backend contract suite feeds identical histories to file and SQLite stores
and asserts byte-equivalent normalized flap results, corrupt-record accounting, and retention
behavior.

### EER-006 — High — Agent-loop exceptions bypass model-egress sanitization

**Evidence.** Normal tool results are projected through `model_egress` in
[`agent_loop.py`](../src/agent_nettools/agent_loop.py#L398). The exception branch instead places
`f"{type(exc).__name__}: {exc}"` directly into the next model-visible `tool_result` at
[`agent_loop.py`](../src/agent_nettools/agent_loop.py#L430). A canary exception containing
`RAW-DEVICE-CANARY` appeared verbatim in that content.

**Impact.** A current or future tool exception containing raw device output, a secret, internal path,
or untrusted instruction-shaped text can cross the boundary the normal path correctly protects.
Current adapters often convert failures into envelopes, which lowers likelihood but does not make
the invariant structural.

**Required fix.** Send the model only a fixed error taxonomy, retryability, and opaque correlation
ID. Put redacted diagnostics in the internal audit trail. Apply the same maximum-size and untrusted
text rules to every exception path.

**Acceptance gate.** For every registered agent tool, generated exceptions containing canary raw
output, credential shapes, and prompt-injection text never occur in model-visible messages.

### EER-007 — High — MCP sanitization wraps returns, not raised exceptions

**Evidence.** The central registration wrapper is an excellent design, but it returns
`sanitize(function(...))` without an exception boundary in
[`server.py`](../mcp_server/server.py#L383). The MCP SDK, not this sanitizer, therefore owns how an
unexpected exception becomes a protocol error.

**Impact.** An exception from an inventory adapter, transport seam, or future tool can disclose raw
or internal text to the MCP client/model. This is the MCP analogue of EER-006.

**Required fix.** Catch exceptions in the one registration wrapper and return a classified,
sanitized error envelope with a correlation ID. Log only redacted detail. Preserve cancellation and
process-termination exceptions rather than swallowing them indiscriminately.

**Acceptance gate.** A registration-level test injects an exception with a canary and proves it is
absent from both classic and staged MCP protocol results.

### EER-008 — High — Safety-relevant configuration does not consistently fail closed

**Evidence.** `settings.py` explicitly says validation is report-only and does not rewire consumers
([`settings.py`](../src/agent_nettools/settings.py#L26)). CLI startup warns but continues at
[`cli.py`](../src/agent_nettools/cli.py#L2588). Unknown evidence-backend values silently select files
at [`evidence_store.py`](../src/agent_nettools/evidence_store.py#L537). An invalid MCP surface warns
then retains the wider classic surface at [`server.py`](../mcp_server/server.py#L1644). Several
boolean consumers treat any unknown spelling as enabled; the declaration comments recognize this
hazard.

**Impact.** A typo can split history across backends, widen an intended staged MCP deployment, or
enable outbound/active behavior depending on the setting. A warning is easily lost in automation.

**Required fix.** Parse one immutable, typed Settings object at process startup and inject it into
consumers. Reject invalid enums, booleans, ranges, and mutually inconsistent combinations before
opening credentials, files, sockets, or stdio protocol. Safety gates must recognize only explicit
true values; unknown means disabled or startup failure. Retain warning-only behavior only for
cosmetic settings.

**Acceptance gate.** Every malformed declared setting produces a nonzero startup/config-check result
and zero side effects. A test proves settings declarations and runtime consumers cannot drift.

### EER-009 — High — Inventory accepts ambiguous and path-unsafe identities

**Evidence.** `Device.name`, site, router ID, local AS, port, expected counts, credential environment
names, and schema version have little or no domain validation in
[`inventory_model.py`](../src/agent_nettools/inventory_model.py#L46) and
[`inventory_model.py`](../src/agent_nettools/inventory_model.py#L132). Cross-validation checks only
duplicate device names and referenced platform/credential groups; it does not require unique
management IP/router ID, supported schema version, safe storage identifiers, valid port/AS ranges,
or nonnegative expected counts.

**Impact.** Invalid identities can reach storage paths (EER-001), and duplicate routing identities
can misattribute peer devices or causal chains. Future schema versions may be interpreted with old
semantics instead of refused.

**Required fix.** Model domain types explicitly: supported schema version literal, storage-safe
device key, nonempty normalized site/tag names, IPv4 router ID, TCP port 1–65535, valid ASN range,
nonnegative counts, environment-variable-name grammar, and uniqueness constraints for identities
that must be unique in this estate. Aggregate validation errors so an operator can fix one file in
one pass.

**Acceptance gate.** A generated JSON Schema and invalid-inventory corpus cover bounds, duplicates,
unsupported versions, traversal, Unicode/confusable policy, and actionable error paths.

### EER-010 — Medium — Model calls lack one enforceable deadline contract

**Evidence.** Device calls have explicit connection/read timeouts and retries. Provider clients are
constructed largely with SDK defaults; Ollama uses a separate fixed timeout; the agent loop has a
whole-run budget but does not consistently pass remaining time to each turn. Retry behavior also
inherits provider defaults.

**Impact.** A CLI or MCP request can remain blocked longer than an operator expects, and behavior
varies by provider. Whole-run cancellation cannot be reasoned about if each nested call owns an
independent default timeout/retry policy.

**Required fix.** Establish one monotonic operation deadline. At each transport call, calculate and
pass remaining time, cap provider retries within it, and return an explicit timed-out state. Expose
duration, retries, cancellation, and provider outcome without logging prompt/evidence content.

**Acceptance gate.** Fake slow providers prove every path exits within deadline plus a small tested
scheduler tolerance, including multi-turn agent runs and retry sequences.

### EER-011 — Medium — File-persisted metrics lose concurrent updates

**Evidence.** `MetricsCollector` uses a process-local `threading.Lock`, reads the file once, mutates
in-memory state, and atomically replaces the whole file at
[`metrics.py`](../src/agent_nettools/metrics.py#L102). Atomic replace prevents torn JSON but does not
serialize read-modify-write cycles across CLI processes.

**Impact.** Concurrent collection/event processes can overwrite one another's counters. Metrics used
to justify reliability or grounding quality may undercount, undermining pilot decisions.

**Required fix.** Use SQLite/WAL for counters, or a cross-process lock covering reload + merge +
commit. For service deployments, prefer emitting events to a real metrics backend. Define whether
audit, ticket, and ledger appenders are single-writer or cross-process safe and test accordingly.

**Acceptance gate.** A multiprocess test records a known count under contention and recovers exactly
that count across repeated runs and abrupt worker termination.

### EER-012 — Medium — The release pipeline tests a checkout, not the deliverable

**Evidence.** CI uses editable installs in [`ci.yml`](../.github/workflows/ci.yml#L22), has no
wheel/sdist install test, and does not build/smoke the Docker image. Supported Python starts at 3.10,
but CI covers only 3.11 and 3.12. This gap directly allowed EER-003 and EER-004.

**Impact.** Green CI does not mean a user-installable or containerized application works. Metadata,
package data, entry points, permissions, and clean-environment behavior are under-tested.

**Required fix.** Build wheel and sdist once in CI; inspect them; install them into clean 3.10–3.13
environments; run CLI, prompt, fixture/demo, and MCP smoke tests from outside the checkout; then build
the container from that exact wheel and smoke it as the non-root user. Publish only the tested digest.

**Acceptance gate.** A release job fails if the artifact differs from what was tested, imports depend
on CWD, required resources are absent, or the container cannot serve MCP over stdio.

### EER-013 — Medium — Dependency and container builds are not reproducible enough

**Evidence.** Most dependencies use lower bounds only in
[`pyproject.toml`](../pyproject.toml#L23). The container uses mutable `python:3.11-slim` and resolves
dependencies during `pip install .` in [`Dockerfile`](../Dockerfile#L4). CI actions use mutable major
tags. There is no visible lock/constraints file, artifact hash policy, SBOM, provenance/signing gate,
or automated dependency/OS vulnerability scan.

**Impact.** Two builds from the same commit can contain different code. A dependency or base-image
change can break behavior or introduce a vulnerability without an application diff.

**Required fix.** Maintain reviewed, hashed constraints per supported Python line; update them with an
automated PR bot; pin base images by digest and GitHub Actions by commit SHA; generate SBOM and build
provenance; sign release artifacts; and scan Python plus OS dependencies with an explicit severity
policy. Preserve useful compatible ranges in library metadata while locking deployed applications.

**Acceptance gate.** Repeated builds from the same source and inputs produce traceable, equivalent
dependency sets, and release refuses unresolved vulnerabilities above the documented threshold.

### EER-014 — Medium — Verification lacks complementary static and coverage gates

**Evidence.** The existing suite is large and valuable, but CI gates are Ruff with a deliberately
small rule set plus pytest. There is no type checker, branch-coverage floor, complexity guard,
security/static scan, or installed-artifact contract test.

**Impact.** Tests confirm thousands of anticipated examples while integration seams can remain
unexercised. EER-005 is a classic abstraction bypass that types and backend contract tests could
help expose; EER-003 is invisible to source-tree tests.

**Required fix.** Add branch coverage as a diagnostic and ratcheting floor, not a vanity 100% target.
Introduce pyright or mypy incrementally at typed protocol boundaries. Add Bandit/Semgrep or equivalent
for dangerous sinks, plus the artifact and backend contract suites described above. Track mutation
score on the highest-risk pure logic rather than expanding mutation testing indiscriminately.

**Acceptance gate.** New gates are baselined, ratcheted, fast enough for PR use, and cannot be bypassed
by editable-install path leakage.

### EER-015 — Medium — Module size and policy duplication raise change risk

**Evidence.** Several core modules are very large: `cli.py` (~2,631 lines), `network_tools.py`
(~2,359), `template_parsers.py` (~2,045), `checks.py` (~1,898), and `mcp_server/server.py` (~1,700).
Configuration parsing, history access, and error classification are duplicated in ways already
producing behavioral drift.

**Impact.** A change to a cross-cutting invariant requires broad cognition and is easy to apply to
one path but not another. The issue is cohesion and duplicate policy, not line count by itself.

**Required fix.** Refactor only along stable ownership seams: typed settings, storage protocol and
key policy, transport/authentication, error taxonomy, model boundary, CLI adapters, MCP manifests,
and parser families. Keep public contracts frozen while moving behavior behind them. Avoid a broad
rewrite.

**Acceptance gate.** Each extracted boundary has one implementation of its policy, contract tests,
and no cyclic dependency. Code movement must not reduce the current mutation/security coverage.

### EER-016 — Medium — Backlog and documentation are not a reliable control plane

**Evidence.** `docs/build/BACKLOG.md` contains overlapping authoritative/planning tables and internal
status contradictions—for example an item marked blocked while its text says it is genuinely open,
and a dependency described as unbuilt after its own row is done. The project also has multiple large
review/status documents that can disagree as the implementation advances.

**Impact.** An engineer cannot reliably compute what is open, blocked, satisfied, or release-critical.
This matters because the repository uses written invariants as part of its safety process.

**Required fix.** Store backlog state once in machine-readable data or an issue tracker: unique ID,
owner, state, dependency IDs, evidence link, acceptance test, and release gate. Validate the graph in
CI and generate human views. Archive superseded review snapshots; keep README as a product guide,
ADRs as design decisions, and one generated evidence index.

**Acceptance gate.** CI rejects duplicate IDs, impossible state transitions, missing dependencies,
cycles, “blocked by done,” and completed items without verification evidence.

### EER-017 — Medium — Operational workflow remains incomplete for production use

**Evidence.** The product deliberately has no RBAC or multi-user service boundary; protocol and
vendor coverage are narrow; live-lab validation is opt-in; ticket/handover wiring remains incomplete;
and event-trigger automation is still conservative. These limitations are candidly documented.

**Impact.** The tool can assist a knowledgeable operator in a known lab, but it does not yet provide
the identity, authorization, audit durability, coverage, handover, or prospective accuracy evidence
needed for unattended operations.

**Required fix.** Define maturity stages: lab, shadow pilot, assisted operation, and automation.
Remain in shadow mode while collecting prospective, independently labeled incidents. Measure wrong
device/cause rate, false clean rate, refusal rate, evidence coverage, latency, and operator override.
Calibrate selective risk: the system should know when to abstain. Add protocol/vendor breadth only
from observed incident demand. Keep remediation out of scope until a separate authorization and
change-safety architecture exists.

**Acceptance gate.** A signed pilot report meets predeclared thresholds on held-out incidents and
shows no critical evidence-integrity failures. Production promotion requires an explicit threat model,
identity/RBAC design, durable audit destination, and rollback/incident runbooks.

### EER-018 — Medium — The vulnerability disclosure channel should be private-first

**Evidence.** The security policy directs reporters toward a public GitHub issue on the premise that
there is little sensitive material to withhold.

**Impact.** Vulnerabilities in this application can expose credentials, device evidence, local paths,
or destructive primitives. Public disclosure before remediation can increase operator risk.

**Required fix.** Enable GitHub private vulnerability reporting or publish a monitored security email
and coordinated-disclosure expectations. Publicly disclose after a fix and operator mitigation are
available.

**Acceptance gate.** `SECURITY.md` provides one tested private channel, supported-version policy,
response expectations, and disclosure process.

### EER-019 — Low — Local durable-data permissions should be explicit

**Evidence.** Atomic temporary evidence writes are created with restrictive permissions, but not all
durable files and directories have an explicit uniform permission contract; some inherit process
umask. The current threat model is single-user, so this is hardening rather than the leading risk.

**Impact.** On a shared host or permissive umask, evidence metadata, model/audit errors, tickets, or
database contents may be readable by unintended local users.

**Required fix.** Create private state directories as `0700` and sensitive files/databases as `0600`,
verify existing-file permissions on startup, and document the requirement. For service deployment,
use a dedicated OS identity and secret store.

**Acceptance gate.** Tests under a permissive umask prove newly created sensitive state remains
private, including SQLite, logs, ledgers, tickets, and lock/state directories where appropriate.

### EER-020 — Low — Development commands are environment-sensitive

**Evidence.** A direct `make lint` outside the activated virtual environment could not locate Ruff,
and a direct pytest run had one make-driven failure because the `nettools` entry point was not on
`PATH`. With the documented virtual environment activated, the entire suite and lint passed.

**Impact.** This is not a product defect—the setup documentation says to activate the environment—but
it makes local and automated verification easier to run incorrectly.

**Required fix.** Give contributors one hermetic runner (`uv run`, `tox`, `nox`, or Make targets that
explicitly use `.venv/bin`) and make the environment preflight fail with one actionable message.

**Acceptance gate.** A clean checkout can execute the documented single verification command without
manually mutating `PATH`, and that command exercises the same artifact gates as CI.

## 5. What a top 0.1% engineering team would do

The distinguishing behavior is sequencing. They would reduce existential uncertainty before adding
capability, and they would turn every claim into an executable contract.

### First 72 hours: contain and establish truth

1. Mark EER-001 and EER-002 as release blockers. Disable or guard file-backend prune until the
   containment patch is released. Notify any existing users of the unsafe argument shape.
2. Add failing regression tests for all six highest-risk findings before changing implementation.
3. Patch storage containment everywhere, including fixtures, and audit every `Path / user_value`
   construction in the repository.
4. Decide and document the SSH trust bootstrap. Enable strict checking before any production-like
   pilot; do not paper over enrollment friction.
5. Catch and classify exceptions at both model boundaries. Canary-test them.
6. Make SQLite flap mode refuse explicitly until backend parity ships.
7. Build an artifact and run the application outside the repository. Treat every resulting failure
   as a release defect, not a documentation surprise.

### First two weeks: make boundaries singular

1. Introduce shared `StorageKey`, typed Settings, error taxonomy, and EvidenceStore contract suites.
2. Move prompts and the minimal demo corpus into package resources.
3. Change CI to build once and test wheel, sdist, and container. Add Python 3.10 and 3.13 or narrow
   the declared support range honestly.
4. Lock deployed dependencies; pin base image/actions; add SBOM, provenance, and vulnerability gates.
5. Add hard model deadlines and fake-provider timeout tests.
6. Replace file counter persistence with a multiprocess-safe store.
7. Convert the backlog into validated single-source data and designate owners for every blocker.

### Days 15–45: prove operational usefulness

1. Run read-only shadow investigations alongside humans. Do not let outputs trigger changes or pages.
2. Pre-register evaluation thresholds and label incidents independently of tool output.
3. Measure false clean, wrong cause, wrong device, abstention, unsupported coverage, stale evidence,
   collection failure, and time-to-useful-evidence—not merely overall accuracy.
4. Partition results by workflow, platform, topology, failure class, and evidence completeness.
5. Review every high-confidence wrong answer as a safety incident; improve abstention before recall.
6. Complete ticket/handover wiring so a human can see claim, evidence, uncertainty, next probe, owner,
   and history without reconstructing it from JSON.

### Days 46–90: earn a promotion decision

1. Conduct failure injection: SSH key mismatch, partial device reachability, corrupt stores, concurrent
   writers, stale epochs, provider timeout, malformed inventory, and MCP client cancellation.
2. Establish SLOs for collection success, bounded latency, evidence freshness, and audit durability.
3. Add a dedicated service identity, RBAC, secret management, private state, and centralized audit if
   the tool will become multi-user or remotely reachable.
4. Perform an external security review focused on SSH trust, local paths, model boundaries, MCP
   exposure, and dependency provenance.
5. Promote only if the pilot thresholds and all critical/high release gates pass. Otherwise stay in
   shadow mode; maturity labels are a safety control, not marketing copy.

## 6. Proposed release gates

Before the next public/package release:

- [ ] EER-001 storage and fixture containment tests pass.
- [ ] Strict SSH host-key verification and enrollment documentation ship.
- [ ] Agent and MCP exception canaries cannot cross model-visible boundaries.
- [ ] File and SQLite backend contract suites pass, including flap detection.
- [ ] Invalid safety-relevant settings fail before side effects.
- [ ] Wheel and sdist load every runtime resource outside the checkout.
- [ ] The installed wheel completes the documented offline demo with no repository present.
- [ ] The container is built from the tested artifact and smoked as non-root.
- [ ] Dependency set, image digest, SBOM, scan result, and provenance are attached to the release.

Before any production-like shadow pilot:

- [ ] All release gates above pass.
- [ ] Live-lab tests pass against the intended IOS-XR versions and topology.
- [ ] SSH mismatch, partial reachability, timeout, stale epoch, and corrupt-store drills pass.
- [ ] Metrics are multiprocess-correct and audit records reach a durable destination.
- [ ] Accuracy and abstention thresholds are declared before evaluation.
- [ ] Operators have a rollback/disable switch and a documented manual fallback.

Before any multi-user, remote, paging, or automated-change use:

- [ ] A new threat model and external security assessment are complete.
- [ ] Authentication, authorization, tenant/data isolation, rate limits, and secret rotation exist.
- [ ] Evidence/audit retention and incident-response policies are approved.
- [ ] Prospective held-out results meet the predeclared safety thresholds.
- [ ] Automated remediation is designed as a separate authorized change system; read-only guarantees
      must not be casually extended into configuration execution.

## 7. Verification record

| Check | Result |
|---|---|
| Git status before review | Clean |
| Reviewed commit | `d39a78ccf370` |
| Full test suite with documented venv on `PATH` | `3185 passed, 22 skipped in 32.83s` |
| Ruff | Passed |
| Focused safety/security suite | `342 passed` |
| Wheel build | Succeeded |
| Wheel prompt load outside checkout | Failed: prompt asset absent (EER-003) |
| Wheel fixture inventory | `tests/fixtures` absent (EER-004) |
| File-store traversal canary | Outside JSON deleted (EER-001) |
| SQLite flap parity canary | 5 stored snapshots became 0 examined (EER-005) |
| Agent exception egress canary | Exception text reached model-visible content (EER-006) |
| Netmiko host-key defaults probe | Strict/system/alternate host keys all disabled (EER-002) |
| Live-lab execution | Not run; 22 tests skipped |

## 8. Taking the application to the next level

### 8.1 Product north star

The strongest future for this application is **not a general network chatbot**. It is a
**proof-carrying incident intelligence system for network operators**:

> Given a symptom, identify the lowest supported cause—or explicitly refuse—then produce a
> replayable incident case showing exactly which authenticated observations, topology relations,
> time bounds, configuration facts, and operator decisions justify that conclusion.

That positioning builds on what is already distinctive here: deterministic descent, typed evidence,
bounded collection, explicit uncertainty, configuration reconciliation, citation checks, event
routing, and a human-scored diagnosis ledger. A generic chat interface would make the product look
familiar while discarding its real advantage. The moat is not prose; it is trustworthy, replayable
diagnosis.

The primary product outcome should be:

> Reduce **time to trustworthy next action**, without increasing false-clean or wrong-cause risk.

This is better than optimizing questions answered, number of tools, token usage, or even aggregate
accuracy. An operator gains value when the system gives a justified next action quickly or refuses
early enough to prevent a bad one.

### 8.2 The capability model to grow toward

The application should evolve from excellent read-only tools into one coherent incident lifecycle:

| Stage | Product question | Durable output |
|---|---|---|
| Detect | What changed or became unhealthy? | Typed signal with source, freshness, and deduplication key |
| Scope | Which service, path, devices, and users are affected? | Time-versioned topology/service subgraph |
| Diagnose | What is the lowest supported cause? | Deterministic finding, uncertainty, and alternatives |
| Decide | What evidence would most reduce uncertainty next? | Bounded, allowlisted next-best probe plan |
| Handover | Can another engineer verify and continue the work? | Replayable incident capsule and timeline |
| Learn | Was it correct, and what systematic gap remains? | Human verdict, failure taxonomy, evaluation sample |

The model can assist at each stage with navigation and explanation, but durable objects should remain
typed. No essential state transition should exist only in model prose.

### 8.3 Highest-value product bets

#### Bet 1 — A proof-carrying incident case

Unify the current Markdown ticket, diagnosis ledger, snapshots, timelines, operator notes, and
session pointer into one versioned IncidentCase domain model. This is the most important feature
because every later capability needs a stable unit of work.

An incident case should contain:

- Stable case/run IDs, status, owner, severity, service impact, and timestamps.
- The original symptom and every normalized event that joined the case.
- Inventory, topology, parser, prompt, application, and policy versions used.
- Evidence observations with device, intent, capture interval, freshness, provenance,
  authentication status, parse coverage, and content hash.
- Hypotheses considered and why each was supported, refuted, or unevaluated.
- The authoritative finding, selective-risk class, coverage limits, and next safe probe.
- Configuration/change facts and maintenance-window context.
- Human comments, handovers, override/verdict history, and external-ticket links.
- A cryptographic manifest so exported cases can be verified and replayed without trusting prose.

The case is not agent memory. It is an auditable record derived from evidence and human actions.
Models may read a bounded projection; they must not silently rewrite it.

**MVP.** Define a Pydantic/JSON Schema model, migrate ticket/ledger output behind it, export one
self-contained scrubbed incident capsule, and replay it offline to reproduce the finding.

**Success gate.** A second engineer can open a case without the original session, reproduce the
finding, see every uncertainty, and identify the exact next action in under five minutes.

#### Bet 2 — Time-aware change correlation

Most serious incidents start with “what changed?” The application already reads limited
configuration and log/metric windows. Make changes a typed evidence source rather than letting a
model infer causality from nearby timestamps.

Ingest normalized change events from sources actually used by the estate: Git-backed configuration
archives, approved change/ticket systems, NetBox updates, device commit/config logs, image upgrades,
interface maintenance, and controller actions. Correlate them with topology and the symptom window.

The output must distinguish:

- changed before symptom — temporal relation only;
- on affected dependency path — topology/service relation;
- mechanism matches — the changed field can cause the observation;
- verified by reversal or independent evidence — strongest causal status;
- coincidental or unknown — never silently promoted to cause.

**MVP.** Add one read-only configuration-history source, normalize interface/ISIS changes, and join
them to a case only when device, object, and time window match.

**Success gate.** On labeled change-induced faults, the feature improves time-to-cause while never
presenting temporal proximity alone as verified causality.

#### Bet 3 — Service- and path-aware diagnosis

Current flows diagnose individual control-plane objects well. Operators ultimately care about
services: “Can customer A reach service B through VRF X, and where does the service path stop being
valid?” The repository correctly refused an L3VPN flow because it lacks VRF-scoped evidence. That
refusal defines the next valuable vertical slice.

Build one complete service slice rather than shallow support for many protocols. For this estate,
the likely first slice is L3VPN reachability:

1. VRF existence and interface binding.
2. CE-facing interface and addressing state.
3. Per-VRF route presence and selected next hop.
4. VPNv4/v6 import/export and route-target policy.
5. PE-to-PE transport/LSP resolution through existing IGP/LDP/SR evidence.
6. Data-plane validation through explicitly gated probes.
7. Both-end and route-reflector corroboration where required.

Represent identity with full scope—device, VRF, address family, route distinguisher/targets, prefix,
peer, and path—rather than reusing default-table BGP fields. After L3VPN, prioritize BFD and failure
domains most common in the diagnosis ledger. EVPN, deeper SR, optical, and broad platform support
should follow measured incident demand, not feature symmetry.

**MVP.** One VRF, IPv4 unicast, one working service and three captured failure mechanisms, with
explicit unsupported/unevaluated behavior for every absent layer.

**Success gate.** Blind replay localizes the correct failing service layer and never returns
all-layers-healthy when a required layer was unobserved.

#### Bet 4 — A deterministic next-best-evidence planner

When the system cannot localize a cause, it should explain which safe observation would most reduce
uncertainty. This is more useful and safer than a free-form agent inventing its own plan.

Each flow should expose a finite set of typed candidate probes derived only from observed objects and
the allowlisted capability registry. Rank candidates by:

- hypotheses separated and expected information gain;
- connection/session cost and evidence-coherence impact;
- active-probe traffic risk;
- freshness already available from passive sources;
- probability that the platform/parser will produce a usable answer.

The planner proposes or executes only already-approved read operations. A model may select among
candidate indices or explain the tradeoff; it may not invent a command, target, device, or fact.

**MVP.** For cause-not-localised in current ISIS/LDP/BGP flows, return at most three candidates with
the hypothesis each separates, estimated cost, and a refusal reason if none is safe.

**Success gate.** On held-out cases, the top proposal shrinks the unresolved hypothesis set more often
than a fixed probe order without exceeding session or active-traffic policy.

#### Bet 5 — A time-versioned topology and evidence graph

The existing graph/topology features should become a diagnostic substrate, not merely an export.
Model devices, interfaces, adjacencies, peers, VRFs, prefixes, LSPs, services, changes, observations,
and incidents as typed nodes/edges with valid-from, valid-to, source, freshness, and confidence.

This enables safe answers to:

- Which services depended on this interface when the incident began?
- Which symptoms share the same lowest failed object?
- Did topology change before, during, or after the alarm?
- Is this observation current for the incident epoch or merely the latest stored value?
- Is an edge absent because collection failed or because the relationship was observed down?

Start with an in-process domain graph or SQLite representation and a stable query protocol. Neo4j
can remain an optional projection; do not make it the source of truth before temporal semantics are
stable.

**MVP.** Produce the affected subgraph for one incident and serialize it in the incident capsule.

**Success gate.** Blast radius and path scoping are reproducible from the capsule and distinguish
missing observation from observed absence.

#### Bet 6 — An operator-first incident workspace

The CLI and MCP surfaces are valuable, but a case lifecycle needs a human view. Build a small web or
terminal UI only after the IncidentCase API stabilizes. It must be a projection of typed state, not
a second source of truth.

The default view should show:

- Symptom, affected service/path, finding, and whether it is trustworthy.
- A numbered causal chain with direct evidence links.
- A synchronized timeline of events, observations, and changes.
- Evidence coverage/freshness and every reason the system abstained.
- Competing hypotheses and the next-best safe probe.
- Raw output behind an explicit reveal, marked untrusted and separately permissioned.
- Human confirm/incorrect/unknown controls that append to verdict history.
- Export/handover to the ticket destination.

Avoid a blank chat box as the primary interface. Chat is useful for navigating a case; it is poor at
representing coverage, uncertainty, topology, and provenance.

**MVP.** Read-only case viewer plus human verdict/handover controls for one local operator.

**Success gate.** Operators find evidence and recognize an unevaluated branch faster than with
JSON/Markdown alone, without losing source detail.

#### Bet 7 — Production-grade integrations and event lifecycle

The repository already has NetBox/graph, Loki/Prometheus, Alertmanager/syslog routing, n8n/systemd
examples, tickets, and notifications. The next step is not more adapters. It is making a few of them
reliable, idempotent, and case-aware.

Prioritize:

1. Alert deduplication, grouping, update, resolve, and reopen semantics around IncidentCase.
2. NetBox or the chosen source of truth as versioned topology/intended-state input.
3. One ticket platform with idempotent create/update, deep links, attachment handling, and a
   private/redacted evidence policy.
4. OpenTelemetry traces/metrics for collection, parsing, reasoning, model, and case transitions.
5. Maintenance/change-calendar ingestion that suppresses automation while preserving findings.

Prometheus already satisfies the measured telemetry need. Do not add direct gNMI merely to claim
protocol support; revisit it only when freshness, fidelity, or scale measurements prove a gap.

**MVP.** One Alertmanager event creates or joins one case and updates one external ticket exactly once
through retries and restarts.

**Success gate.** An event-storm test proves deterministic grouping, bounded collection, idempotency,
and correct resolution/reopen history.

#### Bet 8 — A capability-pack SDK

Platform/protocol growth will otherwise multiply conditionals across already-large modules. Define a
declarative, testable capability pack containing:

- Platform/version match rules and feature discovery.
- Approved intent/template specifications and parameter types.
- Parser schema, raw fixtures, and coverage declaration.
- Normalization into stable cross-platform domain types.
- Deterministic checks/flow rungs and dependency declarations.
- Model-egress policy for every field.
- Healthy, degraded, unsupported, timeout, corrupt, and adversarial test cases.
- Compatibility metadata and a safety-review signature/hash.

Loading a pack must never grant generic command execution. The central runtime validates manifests,
enforces commands, and exposes only capabilities whose contracts pass.

**MVP.** Extract one IOS-XR flow behind the pack contract without changing behavior, then add one
IOS-XE capability through that public contract. Junos remains out of scope unless the existing
product decision is reversed and a real device/corpus is supplied.

**Success gate.** A new supported flow requires no edits to transport, model boundary, CLI dispatch,
or MCP sanitization, and the conformance suite rejects an unsafe pack.

#### Bet 9 — An evaluation and fault-injection platform

The diagnosis ledger is the beginning of the most strategically valuable dataset in the project.
Turn it into an evaluation system that prevents feature velocity from outrunning truth.

Maintain three datasets:

- Development cases: visible and freely iterated on.
- Regression cases: promoted after a discovered failure; no longer unbiased evidence.
- Sealed audit cases: held independently and consumed once for a release decision.

Each case should label mechanism, affected object/service, expected lowest cause, acceptable
abstentions, required evidence, misleading signals, and recovery state. Controlled fault injection
and real incident captures estimate field value; synthetic mutation helps robustness but cannot
estimate production accuracy alone.

Report selective-risk curves rather than one accuracy number: accuracy among answered cases as the
system abstains more, plus false-clean and high-confidence-wrong rates. Partition results by flow,
platform, evidence completeness, and fault class.

**MVP.** One replay command produces a signed evaluation report for current flows and fails the build
on any critical false-clean regression.

**Success gate.** Every new capability ships with mechanism-distinct cases, a held-out result, and a
declared threshold before it can influence production-like decisions.

#### Bet 10 — Fleet and multi-user operation, only when demanded

If the application grows beyond one operator, it needs a real control plane rather than a long-running
CLI process: authenticated API, RBAC, job queue, idempotency keys, distributed admission, per-tenant
state, centralized secrets, durable database, audit export, retention, and horizontal workers. This
is a product boundary, not an incremental deployment flag.

Use a small control-plane API that creates cases and schedules read-only collections. Workers should
be stateless except for bounded caches, and state transitions must be transactional and observable.
Preserve per-device rate limits across the fleet.

**MVP.** Only after demand exists: viewer/operator roles, one tenant, OIDC, durable case/job store,
and distributed per-device admission.

**Success gate.** Authorization, isolation, idempotency, secret rotation, audit durability, and
overload behavior pass an external security and reliability review.

### 8.4 Recommended feature portfolio

| Priority | Capability | Why now | Depends on |
|---:|---|---|---|
| P0 | Close EER-001 through EER-009 | Trust and release integrity precede growth | Current review blockers |
| P1 | Versioned IncidentCase and capsule | Stable product object that every capability compounds | Safe storage and packaging |
| P1 | Evaluation/fault-injection runner | Separates diagnostic progress from feature progress | Incident schema and fixtures |
| P1 | Time-aware change correlation | Highest-value new evidence axis for real incidents | Case timeline and source versions |
| P1 | Next-best-evidence planner | Converts abstention into a bounded next action | Candidate gate, cost model, deadlines |
| P2 | Complete L3VPN service flow | Moves from device symptoms to customer impact | VRF evidence and scoped identity |
| P2 | Time-versioned affected subgraph | Makes blast radius and causal context explicit | Incident/evidence graph |
| P2 | Operator incident workspace | Makes trust, uncertainty, and handover usable | Stable IncidentCase API |
| P2 | Case-aware alert and ticket loop | Closes the operational event lifecycle | Idempotent case state machine |
| P3 | Capability-pack SDK | Enables safe platform/protocol growth | Stable schemas and conformance suite |
| P3 | Fleet/multi-user control plane | Supports organizational adoption | Demand and an RBAC threat model |
| P4 | Approved remediation workflows | Potential value, highest new risk | Separate change-safety architecture |

### 8.5 Architecture evolution

The next architecture should preserve a narrow trusted core and place optional capabilities around it:

    events / operator symptom / API
                  |
                  v
         incident case state machine  <---- human verdicts and handovers
                  |
                  v
         typed collection plan + global policy
                  |
                  v
    authenticated read-only transports ---- passive telemetry/change sources
                  |                                      |
                  +---------------+----------------------+
                                  v
                  versioned observation/evidence graph
                                  |
                  +---------------+----------------+
                  |                                |
                  v                                v
         deterministic flows/checks       next-best-evidence planner
                  |                                |
                  +---------------+----------------+
                                  v
               proof-carrying finding + uncertainty
                                  |
                  +---------------+----------------+
                  |                                |
                  v                                v
         operator workspace/ticket        bounded model projection

The trusted computing base should include settings/policy, storage containment, authenticated
transport, command validation, parsers/domain schemas, evidence graph, deterministic checks, case
state machine, and model projection. UI, providers, external graph databases, and ticket/chat systems
should remain adapters around that core.

### 8.6 Suggested delivery sequence

#### Level 0 — Trustworthy artifact (weeks 0–3)

- Close the critical/high review findings.
- Package runtime resources and test wheel/container artifacts.
- Add strict SSH trust, typed settings, backend contracts, and exception sanitization.
- Define versioned observation and incident schemas.

**Promotion:** repeatable release artifact with no known critical/high evidence-integrity defect.

#### Level 1 — Trustworthy case (weeks 3–8)

- Ship IncidentCase, capsule export/replay, and case-aware ledger/ticket migration.
- Add a hashed/signed manifest and typed case timeline.
- Build the evaluation runner and promote existing failures into regression cases.
- Add a read-only local case viewer.

**Promotion:** another engineer can reproduce and audit a diagnosis offline.

#### Level 2 — Trustworthy next action (months 2–4)

- Ship one change-history adapter and mechanism-aware correlation.
- Ship the candidate next-best-evidence planner for current flows.
- Complete one L3VPN service slice with captured faults and selective-risk evaluation.
- Add time-versioned affected-subgraph and blast-radius reporting.

**Promotion:** the system measurably reduces time to the correct next action on held-out cases.

#### Level 3 — Trustworthy workflow (months 4–6)

- Ship case-aware Alertmanager and one ticket integration with idempotency.
- Add OpenTelemetry and multiprocess-safe operational state.
- Run a controlled shadow pilot; iterate on abstention and operator UX.
- Extract the capability-pack SDK only after domain contracts survive the pilot.

**Promotion:** pilot SLOs and safety thresholds pass with durable handover and no automated changes.

#### Level 4 — Trustworthy organizational platform (after measured demand)

- Add authenticated multi-user API, RBAC, secrets, durable job/case store, and distributed admission.
- Add platform/protocol packs based on incident volume.
- Consider approved remediation plans as a separate surface with human authorization, pre/post
  checks, rollback, and blast-radius policy.

**Promotion:** independent security/reliability review plus prospective operating evidence.

### 8.7 Metrics that should govern roadmap decisions

#### Safety and truth

- Critical false-clean rate.
- High-confidence wrong-cause and wrong-device rate.
- Unsupported/unevaluated correctly surfaced versus silently treated as absent.
- Evidence-authentication, freshness, parsing, and topology-coverage rates.
- Model-grounding rejection and untrusted-egress escape rate.

#### Operator value

- Median and tail time to trustworthy next action.
- Time to establish affected service/blast radius.
- Device sessions and commands per resolved incident.
- Operator confirmation, correction, and abandonment rates.
- Handover time and percentage of cases reproducible by a second operator.

#### System reliability and cost

- Collection success and timeout rate by platform/device.
- Evidence epoch skew distribution and coherence refusal rate.
- Event deduplication/idempotency correctness.
- Case/audit durability, queue backlog age, and artifact smoke success.
- Provider cost per confirmed useful outcome.
- Passive-cache hit rate, avoided sessions, and human minutes saved.

Do not use number of model calls, number of tools, or aggregate answer rate as success metrics. They
reward activity rather than trustworthy operational value.

### 8.8 Features not to build yet

Elite product development includes explicit refusals:

- **A generic autonomous troubleshooting agent.** The gated agent is useful research, but free-form
  tool choice should not become the product while deterministic cases cover the target domain.
- **Autonomous configuration/remediation.** Read-only diagnosis and change execution have different
  authorization, testing, rollback, and liability models. Design remediation separately.
- **Broad multi-vendor support by translation.** Add a platform only with devices, raw corpora,
  parser coverage, normalization tests, and mechanism-distinct failures.
- **A vector database marketed as long-term memory.** Truth belongs in typed cases, evidence,
  topology, and human verdicts. Retrieval may index them later, never replace them.
- **Direct gNMI for novelty.** Existing measurements show Prometheus meets the present need. Reopen
  only against a measured freshness, fidelity, or scale gap.
- **Unqualified full-config ingestion.** Continue narrow section reads and explicit sensitive-data
  handling. More text is not automatically more evidence.
- **A causal device-health ladder.** Health is an aggregation of independent signals; causal descent
  belongs to a specific symptom or service.
- **Automatic event-triggered investigations before precision is known.** Keep routing dry-run or
  shadow-only until triggers have measured precision and storm behavior.
- **Predictive-failure ML.** Labeled volume and feature semantics are not yet sufficient. First build
  the case/evaluation system that could make the work scientifically valid.
- **A chat-first UI.** Chat can navigate a case, but coverage, time, topology, alternatives, and
  uncertainty require structured views.

### 8.9 The first ten concrete product epics

If one team starts tomorrow, this is the recommended ordered backlog:

1. **Boundary repair release:** EER-001–009, published mitigations, and regression tests.
2. **Artifact contract:** packaged resources, wheel/sdist/container matrix, SBOM and provenance.
3. **Incident schema:** IncidentCase, observation references, timeline, state machine, JSON Schema.
4. **Capsule and replay:** scrubbed export, manifest verification, offline reproduction.
5. **Evaluation runner:** dev/regression/sealed partitions, selective-risk report, release thresholds.
6. **Change evidence:** one config-history adapter and mechanism-aware correlation.
7. **Next-best probe:** finite candidates, information/cost ranking, current-flow integration.
8. **L3VPN vertical slice:** VRF evidence, scoped identity, service descent, captured faults.
9. **Incident workspace:** evidence/timeline/subgraph UI and human verdict/handover workflow.
10. **Operational loop:** idempotent alert grouping, ticket synchronization, tracing, shadow pilot.

Do not run these as ten parallel feature streams. Epics 1–5 define the contracts and truth system;
6–10 consume them. Parallelizing consumers before those contracts stabilize will recreate the policy
drift found in this review.

## 9. Final recommendation

Keep the architecture. Do not rewrite the diagnostic core and do not chase broader autonomy. The
highest-leverage work is to make the boundaries live up to the core's philosophy:

1. authentic evidence source;
2. contained and backend-independent evidence storage;
3. sanitized failure paths;
4. fail-closed configuration;
5. tested, reproducible artifacts;
6. measured shadow-pilot performance with explicit abstention.

Once those are true, this project has a credible path from an exceptional lab tool to a dependable
operator-assistance system. Until then, its safest and most honest posture is single-user lab use,
with production-like operation limited to read-only shadow evaluation.
