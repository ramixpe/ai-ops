# Deep review — 2026-08-27

Review of the current working tree at `v1.2.0` + 45 commits, with the
uncommitted Version-2 revamp in place. Written after the fact from what was
actually executed against the tree, not from the design documents.

## Scope and method

| | |
|---|---|
| Tree | `main`, `v1.2.0` + 45 commits, 222 files uncommitted (+3,887 / −34,637) |
| Code reviewed | `src/agent_nettools/` + `mcp_server/` — 55,264 lines, 98 modules |
| Tests | 54,633 lines |
| New in this revamp | **34 untracked source modules** — the event pipeline, the Telegram delivery layer, the investigation activity stack, procedure approvals |

**Ground truth established first, before reading anything:**

- `pytest -q` → **3,913 passed, 22 skipped, 0 failed** (145 s, Python 3.12.3)
- `ruff check .` → **All checks passed**
- Pattern sweep for `eval` / `exec` / `pickle` / `yaml.load` / `shell=True` /
  mutable default arguments / un-timed HTTP calls → **zero hits**

That matters for how to read the rest of this document. Nothing below is a
regression, and nothing below is caught by the existing suite. **Every finding
is a gap in what is tested, not a break in what is.** Each one was reproduced
against the real modules; the reproduction is quoted with it.

The four inherited invariants (credential-free platform resolution, allowlist
before credentials, no interpolated commands, no unparsed device text to a
model) were spot-checked and hold. The safety boundary is not where the
problems are.

---

## Verdict

The durable core of the revamp — `event_store.py`'s state machine, the
`BEGIN IMMEDIATE` discipline, the outbox schema, the incident identity index —
is well built. The state transitions are correct, the idempotency keys are
right, and the SQL is parameterised throughout.

**What is missing is not the happy path. It is every recovery path.** The store
models leases, attempts, retry deadlines and delivery state, and then ships
with no code that ever acts on any of them. A worker that dies leaves an event
stranded forever; a notification that fails once is never retried and never
logged. The mechanisms are present and inert.

That is the theme of findings 1–4, and it is worth more than the rest combined.

---

## High — the event lifecycle has no recovery paths

### 1. A crashed worker strands its event permanently

`acquire_lease` (`src/agent_nettools/event_store.py:1066`) documents that "an
expired `running` lease is recoverable after a worker crash". It is —
*if you already know the event id*. **No query anywhere enumerates events whose
lease has expired.** The full public surface of `EventStore` has no
`expired_leases()`, and `eligible_retries` (`:1050`) filters on
`state = 'retryable_failed'`, which a crashed `running` event never reaches.

```
after acquire: state='running' lease_owner='workerA'   (lease_seconds=1)
eligible_retries 1h later : ()
dead_letters              : ()
health dead_letter_count  : 0
event state 1h later      : 'running'   (lease long expired)
```

`health_snapshot` counts by state, so the event shows as `running: 1`
indefinitely with no age signal — it does not read as stuck.

**Fix.** Add `expired_leases(now)` selecting
`state = 'running' AND lease_expires_at <= ?`, and have `EventWorker` sweep it
alongside `eligible_retries`. Add an oldest-`running`-lease age to
`health_snapshot` so the condition is visible before someone goes looking.

### 2. The notification outbox is never drained — and failures are silent

The outbox has states, leases, an `attempts` counter and a `last_error` column.
Nothing ever reads them after the first attempt.

`claim_notification` is called from exactly two places
(`telegram_delivery.py:45`, `:92`), both reached only from
`event_notification.py:105` / `:125` — **at enqueue time**. There is no
`eligible_notifications()` on the store and no sweeper anywhere.
`finish_notification(sent=False)` (`event_store.py:1218`) returns the row to
`pending`, and `pending` is a terminal state in practice.

Both call sites then swallow the failure whole:

```python
except Exception:  # noqa: BLE001 -- delivery must not consume the incident transaction
    return
```

`event_notification.py:111` and `:126` — no logging, no metric, no re-raise.

**Combined effect: one transient Telegram failure means the operator's live
card never arrives, is never retried, and nothing anywhere reports it.** The
docstring on `deliver_live_activity` says "durable outbox preserves retry on
failure". The retry is recorded. It is never performed.

**Fix.** Add an outbox sweeper claiming `pending` rows past a backoff deadline,
and log at WARNING before returning at both call sites. Recording an error into
a column nothing reads is not error handling.

### 3. `run_eligible` wipes the retry deadline before it takes the lease

`event_receiver.py:75-83`:

```python
self.store.transition(record.event_id, "admitted", reason="retry backoff elapsed")
self.store.acquire_lease(record.event_id, owner=self.owner, ...)
except EventLeaseError:
    continue
```

The two steps are not atomic, and `transition` (`event_store.py:960`) sets
`retry_at = NULL` unconditionally. If `acquire_lease` raises, the `continue`
leaves the event in `admitted` with a null deadline — invisible to
`eligible_retries` from then on.

```
after schedule_retry      : state='retryable_failed' retry_at='2026-08-27T11:03:46+00:00'
eligible_retries          : ['ev2']
after transition->admitted: state='admitted' retry_at=None   <-- wiped
eligible_retries now      : []
```

**Fix.** Claim the lease and the state change in one store method under a
single `BEGIN IMMEDIATE`, or roll the state back on lease failure.

### 4. Leases are enforced on acquire but not on write

`acquire_lease` correctly refuses to steal a live lease. `transition` takes no
owner argument and performs no ownership check, so any caller can write
terminal state over a lease another worker holds:

```
workerA holds lease until 2026-08-27T11:02:01+00:00  owner='workerA'
workerB correctly refused: event e1 lease is held by 'workerA' until ...
workerB called transition() with NO ownership check -> state='completed'
```

With finding 1 unfixed this is latent. Fix finding 1 — introducing genuine
lease recovery — and it becomes live: a slow worker whose lease was reclaimed
can still write the outcome of work the new owner is redoing.

**Fix.** Add a fencing token (a monotonic `lease_epoch` on `events`, bumped by
`acquire_lease`) and require it on `transition`, rejecting stale writers.

---

## Medium — security and audit integrity

### 5. `simulate_approved_procedure` verifies nothing

`procedure_workflow.py:170` checks only that the receipt's digest matches the
proposal. It does not check the HMAC, the expiry, or the nonce, and it never
calls `verify_approval`:

```
forged receipt (expired 7 days + bogus HMAC + unknown nonce) ACCEPTED -> status='not_executed'
procedure_simulations rows: 1 -> an audit row asserting an approved dry-run
```

**Scope this correctly: it is not an execution flaw.** `SimulationResult` is
hardcoded to `not_executed`, the DB `CHECK` constraints make anything else
unrepresentable, and the foreign key means a digest must already have a stored
approval. The damage is to provenance: a row can be written asserting an
approved dry-run for a receipt that was never valid, after expiry and after the
nonce was consumed. In a repo whose stated rule is that a ticket records what
*code* observed, that is the wrong kind of row to be able to forge.

**Fix.** Call `verify_approval` from `simulate_approved_procedure`, or take the
already-verified receipt as a distinct type that cannot be constructed without
verification.

### 6. Nonce replay escapes as the wrong exception type

`verify_approval` raises `ApprovalError` for a replay on the in-memory path and
`EventStoreError` on the durable path:

```
store path  -> EventStoreError: procedure approval nonce is replayed   <-- not the documented type
memory path -> ApprovalError  : approval nonce is replayed
```

`EventStoreError` is a `RuntimeError`; `ApprovalError` is a `ValueError`. They
share no ancestor below `Exception`. A caller written against the documented
contract — `except ApprovalError` — does not catch a replayed approval when the
durable store is in use, which is the configuration that has replay protection
worth catching.

**Fix.** Wrap the `consume_procedure_approval` call and re-raise as
`ApprovalError`.

### 7. The MCP bearer-token layer fails open on non-HTTP scopes

`mcp_server/http_api.py:26`:

```python
if scope["type"] != "http" or self._authorized(scope):
    await self._app(scope, receive, send)
```

Anything that is not an HTTP scope is passed through unauthenticated:

```
scope type=http        NO auth header -> downstream reached=[]
scope type=websocket   NO auth header -> downstream reached=['websocket']
scope type=lifespan    NO auth header -> downstream reached=['lifespan']
```

`lifespan` must pass through. `websocket` must not. This is not exploitable
today only because the wrapped FastMCP app declares no websocket routes — the
protection is the downstream app's routing table, not this auth layer. Both
tests in `tests/test_mcp_http.py` use `type: "http"` only.

**Fix.** Allow `lifespan` explicitly, authenticate `http`, reject everything
else. Add a test asserting a websocket scope is refused.

### 8. WAL and SHM sidecar files are world-readable

`_initialize` chmods the database to `0600` (`event_store.py:435`). SQLite's
own sidecars are created under the process umask and are never chmodded:

```
events.sqlite3       -rw-------  0o600
events.sqlite3-shm   -rw-r--r--  0o644
events.sqlite3-wal   -rw-r--r--  0o644
```

The `-wal` file holds recently written pages — the same event payloads,
diagnosis reasoning and model exchanges that `_persist.py`'s own EER-019 note
says must be owner-only.

**This is defense-in-depth, not a live exposure.** `_secure_mkdir` forces the
parent directory to `0700` on *every* call, so the sidecars are unreachable on
the default path. Worth fixing because the code already takes belt-and-braces
elsewhere, and because it is one `NETTOOLS_EVENT_DB_PATH` away from mattering.

**Related, and worth a deliberate decision:** `_secure_mkdir` unconditionally
chmods the DB's parent directory to `0700`. Pointing `NETTOOLS_EVENT_DB_PATH`
at a shared directory silently tightens permissions on a directory this module
does not own. Confirmed: `0755` in → `0700` out, on an existing directory.

### 9. No 429 handling and no backoff in the Telegram path

`notifier.py:495` and `:544` fold every non-2xx into a generic `NotifierError`.
Telegram's `retry_after` is never read. With finding 2 fixed and a sweeper in
place, a rate-limited chat becomes a hot retry loop; the outbox also has no
`max_attempts` and no dead-letter state, unlike events:

```
attempt 1: state='pending' attempts=1 last_error='provider 500'
attempt 2: state='pending' attempts=2 last_error='provider 500'
attempt 7: state='pending' attempts=7 last_error='provider 500'
```

**Fix these two together.** Add `retry_at` and a dead-letter state to the
outbox, honour `Retry-After`, and surface outbox dead-letters in
`health_snapshot` next to the event dead-letter count.

---

## Medium — correctness

### 10. `health_snapshot` crashes exactly when the queue is unhealthy

`event_store.py:917` subtracts a parsed (aware) timestamp from the caller's
`now`, which is never validated for tzinfo:

```
health_snapshot(now=naive), empty retry queue      -> None   (fine)
health_snapshot(now=naive), one retryable_failed   -> TypeError: can't subtract
                                                      offset-naive and offset-aware datetimes
```

The health endpoint works in every test and every healthy call, and raises the
first time an event is waiting to retry.

This is one instance of a broader inconsistency. `create_or_join_incident`,
`record_procedure_approval`, `record_procedure_simulation` and
`consume_procedure_approval` all explicitly reject naive datetimes.
`health_snapshot`, `eligible_retries`, `schedule_retry`, `acquire_lease`,
`claim_notification`, `record_narrowing_shadow` and `transition_incident` do
not.

**Fix.** Normalise once in a `_require_aware(value)` helper and call it from
every method taking `now`.

### 11. `create_or_get` silently discards a changed payload

`event_store.py:927` returns the existing row unchanged when the id already
exists. `campaign_reporting.py:72` synthesises one id per *round* and reuses it
for every phase of that round, so the durable payload freezes at the first
phase:

```
queue phase=started          created=True   stored payload phase='started'
queue phase=effect_observed  created=False  stored payload phase='started'
queue phase=recovered        created=False  stored payload phase='started'
```

The Telegram card text is correct — a new `render_hash` per phase produces a new
outbox row. The durable record is wrong, and it is the durable record a later
reader trusts.

**Fix.** Either give each phase its own event id, or have the campaign path use
an explicit update rather than `create_or_get`. At minimum, have
`create_or_get` report a payload mismatch instead of dropping it.

### 12. A foreign-key miss is reported as a dry-run contract violation

`event_store.py:755` maps every `IntegrityError` to
`"procedure simulation violates dry-run contract"`:

```
unknown digest -> procedure simulation violates dry-run contract
```

The real cause is an unknown `proposal_digest` — a missing approval, not an
attempted execution. This is the same defect class as faultlab's
`d4b688c` ("`restore()` no longer reports 'nothing to revert' as 'reverted and
verified'") and `90e38a4` ("stop letting a `diff_keys()` code defect render as a
device read failure"): a code-level fault wearing a domain-level error message.

**Fix.** Check the FK explicitly, or inspect the `IntegrityError` and
distinguish constraint from foreign key.

### 13. `event_store.py`'s docstring contradicts the code

```
"This module is intentionally not wired into ``event_agent`` yet."
```

`event_agent.py` imports it at line 112 and calls it at `:701`, `:705`, `:711`,
`:725`, `:748` and more. The docstring tells a reader the module is inert while
it sits on the live event path — the most load-bearing kind of stale comment,
and the one most likely to be believed in this repo, where docstrings are
otherwise reliable.

**Fix.** Rewrite the header to describe what it is now wired into.

---

## Low — hardening

| # | Finding | Where |
|---|---|---|
| 14 | 21 `assert` statements carry runtime invariants (`assert row is not None` after a `COMMIT`). No `-O` or `PYTHONOPTIMIZE` anywhere today, so this is latent — but under `-O` the durable store degrades to `AttributeError`. | `event_store.py` ×12, `event_agent.py:789`, others |
| 15 | `GUIDED_TOOL_NAMES` is assigned twice; the first literal tuple at `:33` is dead and can silently diverge from the registry-derived one at `:85`. Ruff's F811 does not flag module-level reassignment. | `mcp_profiles.py:33` |
| 16 | An unset `NETTOOLS_MCP_SURFACE` yields the **wide** 37-tool classic surface; a typo yields the narrow 6-tool staged one. The unconfigured default is the permissive one. | `mcp_profiles.py:161`, `settings.py:369` |
| 17 | `configured_approval_secret()` accepts any non-empty string — `'x'` is a valid HMAC key. | `procedure_workflow.py:44` |
| 18 | `access_log=False` on the authenticated HTTP transport removes the record of who connected, in a project that otherwise keeps a ledger, tickets and a flight recorder. | `http_api.py` |
| 19 | Ruff selects `E,F,I,B` only. No `S` (bandit), no `ASYNC`, no `C901`. Findings 5, 7 and 14 are all in scope for rules not enabled. | `pyproject.toml` |
| 20 | `with sqlite3.connect(...)` commits but never closes; every store method opens a fresh connection and re-runs three PRAGMAs. Correct under CPython refcounting, order-dependent under an exception that retains the frame. | `event_store.py:252` |
| 21 | `record_admission_shadow` reads via `self.get()` on one connection and inserts on another, outside any transaction — the recorded `durable_state` can be stale. | `event_store.py:861` |
| 22 | Seven new modules have no matching test file: `graph_snapshot`, `intent_evidence`, `investigation_activity`, `iosxr_syslog`, `mcp_epoch_validation`, `procedure_registry`, `trigger_table`. `trigger_table` owns the `fires` gate that `event_agent` depends on. | `tests/` |

---

## Enhancements

**Make the store's own state observable.** `health_snapshot` reports counts by
state. It cannot answer the three questions that matter during an incident:
what is stuck, for how long, and where is delivery failing. Add oldest-age per
state, an expired-lease count, and outbox pending/dead-letter counts. Findings
1, 2 and 9 all become visible from one endpoint.

**Give the store a single recovery entry point.** `EventWorker` currently
sweeps one queue. A `sweep(now)` that reclaims expired leases, drains due
retries and drains the outbox in one pass would close 1, 2 and 3 together, and
gives a natural home for the fencing check from 4.

**Normalise time handling once.** A `_require_aware()` helper called from every
`now`-taking method removes finding 10 as a class rather than an instance.

**Turn the invariant docstrings into tests.** The prose in `event_store.py`,
`procedure_workflow.py` and `http_api.py` states contracts precisely — "a live
lease is never stolen", "an expired lease is recoverable", "durable outbox
preserves retry on failure". Findings 1, 2, 4 and 13 are all cases where the
prose is true of the design and false of the code. These read as executable
assertions; three of the four would have been caught by a test named after the
sentence.

**Enable `S` in ruff** before the next surface lands. Cheap, and it covers the
category that findings 5, 7 and 14 sit in.

---

## What this review did not cover

Stated so the gaps are not mistaken for clean bills of health:

- `cli.py` (2,889 lines), `network_tools.py`, `checks.py`, `template_parsers.py`
  and `parsers.py` were not read line by line — only swept for the pattern
  classes listed under method.
- `mcp_server/server.py` (96 kB) was checked for how `boundary.sanitize` is
  applied, not tool by tool.
- The four inherited invariants were spot-checked, not re-audited. The frozen
  files (`test_safety.py`, `test_template_security.py`) were not touched.
- No live-lab run: `NETTOOLS_LIVE_LAB` was never set, so nothing here is
  evidence about device behaviour.
- Findings are ordered by severity within each section, but severity is my
  judgement against this system's single-operator lab context, not a scored
  model.

---

## Codex follow-up review and cleanup — 2026-08-27

This section is the second review requested after the peer review above. I
re-derived the findings from the working tree, implemented the safe in-scope
remediations, added regression coverage, and performed a current-documentation
cleanup. It is deliberately appended rather than rewriting the peer review, so
the original observations and the later disposition remain independently
reviewable.

### Assessment of the peer review

The peer review is technically strong. Findings 1–13 were reproducible and
correctly scoped. The most valuable observation is its central one: the store
had durable *representations* of recovery without durable *execution paths* for
recovery. The review also did well not to inflate finding 5 into a router-write
vulnerability or finding 8 into a default-path data exposure.

Two qualifications matter:

1. The proposed one-line fix for finding 5 (call `verify_approval` from
   `simulate_approved_procedure`) would have consumed the nonce twice under the
   old API. The safe fix required a verified-receipt type returned by the one
   verification/consumption step.
2. Findings 16 and the parent-directory half of 8 are documented policy choices,
   not accidental implementation drift. The classic MCP default is retained for
   compatibility and `_secure_mkdir` deliberately self-heals owner-only storage
   permissions. Both remain risks worth revisiting, but changing either during a
   cleanup would silently change an operator contract.

### Disposition of findings 1–20

| # | Disposition after this pass | Evidence / remaining condition |
|---:|---|---|
| 1 | **Remediated** | `recoverable_events()` enumerates expired `running` leases and due retries; `EventWorker` claims both. Health reports the oldest expired lease and its age. |
| 2 | **Partially remediated** | `eligible_notifications()` plus `drain_notifications()` now recover pending and expired-send work; live notification calls drain old work and failures log at warning/exception level. A quiet process still needs an external recurring invocation; see residual R1. |
| 3 | **Remediated** | `EventWorker` no longer transitions to `admitted` before claiming. `acquire_lease()` atomically accepts a due `retryable_failed` row, moves it to `running`, clears `retry_at`, and writes the lease. |
| 4 | **Remediated at the store boundary** | Schema v5 adds monotonic `lease_epoch`; leased event transitions/retries require owner+epoch and reject expired or stale writers. `run_event(..., event_lease=...)` carries the fence through terminal writes. |
| 5 | **Remediated** | `verify_approval()` returns a guarded `VerifiedApproval`; simulation rejects raw/forged receipts and rechecks expiry at simulation time. |
| 6 | **Remediated** | Durable-store approval failures are translated to `ApprovalError`; replay behavior now matches the public contract. |
| 7 | **Remediated** | Lifespan passes through, HTTP requires the bearer token, WebSocket receives policy close 1008, and unknown ASGI scope types fail closed. |
| 8 | **Partially remediated by design** | Database, WAL, and SHM files are forced to `0600`. The existing parent-directory `0700` self-healing behavior is retained because it is an explicit persistence policy; deployments pointing at shared directories must not do so casually. |
| 9 | **Remediated** | Outbox failures receive exponential backoff, a five-attempt default ceiling, dead-letter state, health visibility, and Telegram 429 `retry_after` propagation. |
| 10 | **Remediated** | One `_require_aware()` gate now covers store methods that accept time; naive health/retry/outbox calls have regression tests. |
| 11 | **Remediated for the reported campaign path; hardened elsewhere** | Campaign phases use explicit `create_or_update()` so durable phase payload advances. `create_or_get(strict=True)` detects content collisions; the compatibility default remains non-strict because duplicate syslog envelopes legitimately carry a new receipt timestamp. |
| 12 | **Remediated** | Unknown approval digests now report “approval does not exist”; dry-run contract violations retain their distinct error. |
| 13 | **Remediated** | The store header now describes its live event/notification integration. |
| 14 | **Remediated in the reviewed store path** | Required SQL rows use `_require_row()` instead of optimization-sensitive assertions; the event-context assertion was replaced by a typed inconsistency error. Unrelated proven assertions elsewhere were not mechanically removed. |
| 15 | **Remediated** | The dead literal `GUIDED_TOOL_NAMES` assignment was removed; the registry-derived value is the only definition. |
| 16 | **Retained intentionally** | Classic remains the compatibility default; child model clients still force staged. A default flip requires the outstanding surface measurement and an explicit migration decision. |
| 17 | **Remediated** | Approval secrets shorter than 32 UTF-8 bytes are refused; settings and `.env.example` document the floor. |
| 18 | **Remediated** | Authenticated MCP HTTP serving enables Uvicorn access logs. |
| 19 | **Deferred** | Ruff remains `E,F,I,B`. Enabling `S`/`ASYNC`/complexity rules is a repository-wide policy change that needs a dedicated baseline and suppression review, not a drive-by cleanup. |
| 20 | **Remediated** | SQLite connections now commit/roll back and close deterministically through `_ClosingConnection`; sidecar permissions are refreshed as connections open. |

### Additional findings from the follow-up

#### R1 — Medium: recovery exists in code but has no autonomous service owner

The peer review's outbox finding is no longer “nothing can drain it,” but the
deployment contract is still incomplete. `drain_notifications()` is invoked
opportunistically when the live-card path runs, and it is callable by a timer or
service, but no systemd unit, receiver loop, or scheduler in this repository
guarantees a sweep during an otherwise quiet period. The same operational
question applies to `EventWorker`: the recovery method exists, but a deployment
must actually run it.

**Improvement:** define one documented worker entry point that runs event and
outbox recovery on a bounded cadence, expose last-sweep/next-sweep timestamps in
health, and ship the service/timer wiring with an idle-queue recovery test.

#### R2 — Medium: no retention or compaction policy for durable lifecycle data

`event_attempts`, `notification_outbox`, incident transitions, callback results,
card state, and simulations grow monotonically. Card revisions intentionally use
`kind = card:<render_hash>`, so every changed render creates a new idempotency
key. WAL mode prevents ordinary write blocking but does not bound database size.
There is no archive horizon, completed-event purge, outbox compaction, or size
metric.

**Improvement:** specify retention by artifact class, preserve ticket/audit
referential requirements, add a dry-run compaction report, and expose database
bytes plus oldest retained record before implementing deletion.

#### R3 — Medium: lease duration and maximum model-run duration have no margin or heartbeat

`EventWorker` defaults to a 90-second lease while `AgentBounds.time_budget_s`
also defaults to 90 seconds. Startup, ticket close, notification work, and
scheduler delay sit outside or at the edge of that budget. A healthy but slow
run can therefore lose its fence just before its terminal write and be replayed.
Recovery is now correct, but unnecessary duplicate work remains possible.

**Improvement:** require `lease_seconds` to exceed the complete worker budget by
a documented margin, or add lease renewal with the same owner+epoch fence. Add a
test where a run approaches the budget and a second worker attempts recovery.

#### R4 — Low: outbox fencing is owner-based, not epoch-based

`finish_notification()` now requires the claiming owner, closing the original
“any caller can finish another worker's send” hole found during this follow-up.
Unlike events, outbox rows do not yet have a monotonic lease epoch. Current
owners include the process id and delivery is synchronous, so the stale-same-owner
window is narrow, but the two lease implementations now have different strength.

**Improvement:** add an outbox lease epoch when the next schema migration is
needed and require it on delivery completion, matching event fencing exactly.

#### R5 — Medium documentation regression: the deep deletion left live maps pointing at removed files

The documentation cleanup removed the archive, point-in-time reviews, old
diagram layer, and experiment reports, but current reader-facing documents
still linked to those deleted paths. Confirmed examples included `CLAUDE.md`,
`README.md`, `CONTRIBUTING.md`, `PROCESS.md`, the on-call runbook, discovery
notes, and `scripts/README.md`. This made the new smaller docs tree look coherent
from `docs/README.md` while routine reading paths still ended in 404s.

**Remediation in this pass:** current maps now point only at live documents or
state explicitly that retired material remains in Git history. `tests/test_docs.py`
now validates local links in the six current entry-point documents. Historical
references inside append-only findings, test provenance, and code comments were
not rewritten as though the historical source never existed.

#### R6 — Low: generic idempotency still needs domain-specific equality

Strict event identity cannot simply mean byte-identical payload. A duplicate
syslog produces the same event id but a later `received_at`, while a campaign
phase deliberately reuses one per-round event id with changing phase. This is
why `create_or_get()` retains a compatibility default and exposes strict mode,
while campaign reporting uses the explicit mutable method.

**Improvement:** define canonical identity fields per event kind and compare
those, rather than asking every caller to choose between byte equality and no
comparison. Reject differences outside the declared volatile/mutable fields.

### Cleanup performed

- Removed dead MCP profile duplication and optimization-sensitive store asserts.
- Closed SQLite descriptors deterministically and secured WAL/SHM sidecars.
- Consolidated store time validation and added explicit SQL-result failures.
- Added atomic event recovery, runner-exception isolation, fencing, outbox
  backoff/dead-lettering, 429 handling, delivery logging, and health signals.
- Repaired campaign phase persistence and approval audit provenance.
- Removed the trailing blank-file churn in `admission.py`.
- Reconciled the current documentation map with the large deletion set and
  added a link-integrity regression test.

### Verification added by this pass

Regression coverage now includes expired worker recovery, stale event fencing,
leased `run_event` completion, runner failure isolation, naive-time refusal,
outbox retry timing/dead-letter health/owner fencing, retry drains, Telegram 429
hints, forged and expired procedure approvals, WebSocket refusal, campaign
payload advancement, SQLite sidecar modes, and current-document link integrity.

### Final verification

- `.venv/bin/pytest -q` → **3,930 passed, 22 skipped, 0 failed** in 201.07 s
  on the exact final tree.
- `.venv/bin/ruff check .` → **All checks passed**.
- `git diff --check` → no whitespace errors.
- Focused recovery/security/documentation regression set → **159 passed**
  before the final full run; later worker/fencing additions are included in the
  3,930-test total above.
- No live device commands were run and `NETTOOLS_LIVE_LAB` was not enabled.
