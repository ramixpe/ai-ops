# Deep code review — every model path probed, every expert claim re-verified

**2026-08-17, at `80f3a03`.** Companion to `EXPERT-PEER-REVIEW-2026-08-17.md` (the
third-eye review), which this pass **verifies rather than inherits**: every load-bearing
claim in it was re-derived against source, and several were sharpened or corrected. The
second half is what this pass found that it did not.

**Method.** Every module in `src/agent_nettools/` and `mcp_server/` read; the model-egress
paths (`llm_analysis`, `agent_loop`, `evidence_budget`, `prompt_library`, `boundary`)
read line by line; fourteen live probes run against the committed fixtures where a claim
could be settled by execution rather than reading. Probes are quoted with their output.
This pass did not touch the lab and injected nothing.

---

## 1. The expert review, scored

| Their finding | Verdict | My evidence |
|---|---|---|
| **P0-01** stale bytecode | **CONFIRMED, partially addressed** | Independently hit the same defect the same day (OBS-148 §5.3). `scripts/mutate_guards.py` now purges caches around every mutation; **the `.venv` rebuild and the two `.docs-backup-*` decisions remain open and are the operator's** |
| **P0-02** the invariant is false on three paths | **CONFIRMED, all three legs, at source** | `llm_analysis.py:155-169` and `:282-285` serialize the full evidence dict (raw `data.commands` included); `evidence_budget._section_text:134-136` falls back to raw output *by test-pinned design*; `agent_loop.py:430` `json.dumps(result)` of unsanitized envelopes into `tool_result` |
| **P0-03** agent prose ungrounded | **CONFIRMED** | `agent_loop.py:467-471` treats any unrecognised stop reason as `end_turn`; `cli._cmd_agent:326` prints the model's free text with no trust label; no `ground_report`, no containment |
| **P1-01** the "hard" budget is soft | **CONFIRMED** | `agent_loop.py:383` — the deadline is checked only between model turns; one turn's tools run unbounded |
| **P1-03** one annotation for probes and reads | **CONFIRMED** | `server.py:74` — a single `ToolAnnotations(read_only_hint=True)` for all 21 tools, ping/traceroute included |
| **P1-07** persistence not crash/concurrency-safe | **CONFIRMED and sharpened** | See §2.4 — the SQLite golden failure direction is *loss*, not duplication, and two read paths crash on the truncated files the write path can produce |
| **P1-08** serial fabric paths | **CONFIRMED** | `server.py:473` and `agent_loop._assess_health:266` — serial dict comprehensions; `check_fabric` is pooled, its siblings are not |
| **P1-09** packaging | **CONFIRMED** | CI pins 3.11 twice; `Dockerfile` is `python:3.11-slim` by floating tag; no lock file; the image is built by nothing in CI (already in `REPO-INVENTORY.md`) |
| **P2-01** module sizes | **CONFIRMED exactly** | 1,981 / 1,742 / 1,535 / 1,148 / 973 — their numbers reproduce to the line |
| **P2-02** silent config fallbacks | **CONFIRMED** | `_float_env`/`_int_env` in `network_tools.py:46-65`, `evidence_budget._env_int:68`, `notifier._float_env` — a typo'd timeout silently becomes the default |
| **P2-06** sanitization is key-based | **CONFIRMED — and understated; see §2.1, which is this pass's main finding** | |
| P1-02, P1-04, P1-05, P1-06, P1-10, P1-11, P2-03, P2-04, P2-05 | **Consistent with everything read**, not independently re-derived | P1-04/P1-05 are B-465/B-466, filed here before their review; P2-05 is confirmed *concretely* in §2.2–2.3 |

**Where their review is wrong or overstated: nothing material found.** Two calibration
notes. Their scorecard's "Testing A-" ignores that the two most safety-critical suites
cannot be mutation-tested while frozen (`BACKLOG-STATUS.md` §12) — the A- is earned by
volume, not by that gap being closed. And their P0-01 acceptance criterion is already
half-met by `mutate_guards.py`; the remaining half is an environment rebuild, not code.

---

## 2. What this pass found that the expert review did not

### 2.1 Parsing bounds *structure*, not *content* — 17,916 characters of device-authored prose survive sanitisation ⚠ the main finding

The invariant says no **unparsed** device text reaches a model. `boundary.sanitize()`
enforces it by withholding `commands` and `unaccounted_lines` and leaving `data.parsed`
intact, on the stated ground that *"structured records are what a model should reason
over."*

**Measured through the real path:** `get_lab_logging("PE2", 200)` → `sanitize()` → the
parsed records carry a `text` field per log line — **17,916 characters of verbatim
device-authored prose** in one sanitised tool return. And on the *trusted* `investigate`
path, `build_correlate_prompt` embeds every shaped window record's `text` verbatim — 28 of
28 in the probe — into the model prompt.

Syslog text is the softest input this system consumes: **an unauthenticated attacker can
write into it from the network** (a failed SSH login embeds the attacker-chosen username;
crafted traffic produces crafted log lines). So the honest statement is:

> **Prompt injection through syslog reaches every model call on every path, the
> deterministic path's correlate/paraphrase included.** The invariant, as written and as
> enforced, does not bound it — parsing a log line does not make its content safe, it
> makes its *shape* known.

What actually contains the damage today — and this is real, not decoration — is that the
**outputs** are gated: `ground_correlation` requires every timeline entry to cite a real
timestamp and matching mnemonic, B-453 refuses invented identifiers, and the paraphrase is
non-authoritative and withheld on failure. An injected log line cannot fabricate a *cited*
finding. It can steer non-authoritative prose and burn tokens.

**The fix is not to strip `text`** — correlation is impossible without the event text.
It is to type it: free-text device fields (`text`, `last_reset`, interface descriptions)
should carry a provenance tag at parse time, model prompts should render them inside
clearly delimited quote blocks with an instruction boundary, and the egress projector
P0-02 calls for should count and cap them per prompt. Filed as **B-467**, and it subsumes
their P2-06.

### 2.2 `nettools ping` exits 0 on 100% packet loss

Probe: `ping_device("PE1", "192.0.2.1")` on the committed fixtures — transport `success`,
parse `ok`, `loss_pct: '100'`, received `0` — and `_cmd_ping` (`cli.py:243`) maps any
transport success to **exit 0**.

Four decades of Unix convention say `ping && next-step` means the target answered. Here
it means *the SSH session to the device worked*. An operator scripting this gets the
opposite of what the exit code promises, in the exact place muscle memory is strongest.
Same class for `traceroute`. The parsed loss percentage is sitting in the payload,
unread by the exit-code computation — **shape 7, evidence collected, parsed, carried,
never read**, in the CLI's own exit logic. Filed as **B-468**.

### 2.3 `status: "unsupported"` exits 1 from the per-device CLI, against its own contract

The project's documented position (three places) is that `unsupported` *"is not a
failure: `check_fabric` stays green."* Probe: a check returning `unsupported` through
`_cmd_check` (`cli.py:206`) exits **1** — `EXIT_OK if status == "success" else
EXIT_WARNING` has only two buckets. So the same fact about the fabric is green from
`nettools fabric` and red from `nettools sr <junos-device>`. Concrete instance of their
P2-05, with a one-line fix. Filed with **B-468**.

### 2.4 The persistence failure directions, sharpened (extends their P1-07)

- **SQLite golden replacement loses the golden, it does not duplicate it.**
  `evidence_store.py:284-288`: DELETE runs in one connection context, `_insert` opens a
  **second**. A crash between them leaves the device with *no* golden — and since
  `load_golden_snapshot` orders by `id DESC`, even a duplicate would be silently masked,
  so no unique index would ever be missed by observation. The failure is invisible until
  a `diff --against golden` quietly compares against nothing.
- **The write path's failure mode crashes two read paths.** `save_snapshot` writes
  directly to the final path (`:165`); a crash leaves truncated JSON; then
  `detect_flaps` (`network_tools.py:1964`) and `FileEvidenceStore.list_history` (`:190`)
  `json.loads()` every file with no error handling — one corrupt snapshot turns flap
  detection into a stack trace, violating the project's own structured-error convention.
- **Metrics are last-writer-wins across processes by design of the load-once flag.**
  `metrics.py:120-122` seeds from disk once per process, then `_persist` rewrites the
  whole file on every mutation. Two concurrent CLI invocations each seed, count, and
  overwrite each other. The in-process lock guards nothing between processes.

### 2.5 A silent semantics-degrading catch on the trust-loss path

`investigation.py:559`:

```python
except (ValueError, Exception):  # noqa: BLE001 -- an inventory gap, not a crash
    origin = None
```

The tuple is redundant (`Exception` subsumes `ValueError`) — cosmetic. What matters is
what the catch *does*: any failure in `origin_prefix_for` — including a future
programming error — silently sets `origin = None`, which flips rung 5 from the
path-scoped `ANY_HEALTHY` member set to the all-interfaces `ALL_HEALTHY` fallback.
**That fallback is the exact residual exposure `ROUND-6.md` §2.3 seals as the remaining
trust-loss path.** A typo in `origin_prefix_for` would reintroduce reviewer B's scenario
fabric-wide, silently, with every test that injects its own collector still green.
Narrow the catch to the inventory errors it means, and record the degradation in the
payload (`origin_unresolved: <reason>`) so it is visible in output. Filed as **B-469**.

### 2.6 Smaller confirmed defects

| Where | What |
|---|---|
| `network_tools.py:894-904` | **Dead code**: second `if sender is not None:` in `run_templates` is unreachable — the first branch (`:869`) already returned |
| `network_tools.py:408` | The audit log records `"retries": effective_retries - 1` on a **failed** command — the configured maximum, not the retries consumed. An audit field that is *approximately* right is worse than one that is absent |
| `network_tools.py:1482` | `check_fabric` reports `f"{name}: {check} check failed"` — the device's actual error strings are in the per-device envelope but the summary drops them; an operator reading errors at the top level gets no reason |
| `cli.py:326` | The agent's answer is printed with no trust label — the payload knows it is exploratory; stdout does not say so |

### 2.7 What was probed and held — reported so the clean rows are readable (OBS-147)

- `shape_window` on records missing every expected key: **no crash**, records pass
  through.
- `epoch.for_device` on a never-collected device: returns an empty dict → every check
  answers `unevaluated`. Degrades correctly along the absence rule.
- `flows.flow_for("isis_adjacency")`: a clear `NotImplementedError` naming what is and
  is not implemented — the registry-with-declared-stubs design working.
- `_decode` on malformed model output: returns `None` + repair notes, never the prose —
  "a malformed response is prose that failed to be a report" holds at the code level.
- The frozen four: byte-identical to `6629a2c` before and after this review's probes.

---

## 3. Priority order, reconciled

Where my ordering differs from theirs, the reason is stated.

| # | Item | Theirs | Mine | Why |
|---|---|---|---|---|
| 1 | Model-egress projector (their P0-02) **+ free-text typing (B-467)** | P0-02/P2-06 separate | **One work item** | Building the projector without content-typing rebuilds the same hole one layer up: a projector that passes `parsed` intact passes the 18 kB of log prose |
| 2 | Agent trust split (their P0-03) | P0 | P0, **but the cheap half first** | `trust_class: exploratory` in the payload + CLI label + unknown-stop-reason-as-incomplete is an afternoon; the claim-graph explorer is a project. Ship the label now |
| 3 | Environment rebuild (their P0-01) | P0 | P0, operator, 10 minutes | `mutate_guards.py` closed the tooling half already |
| 4 | Exit-code fixes (**B-468**) | inside P2-05 | **Promoted** | `ping` exiting 0 on total loss is operator-facing and a one-day fix; the full versioned result envelope can follow it |
| 5 | Atomic persistence (their P1-07 + §2.4) | P1 | P1, unchanged | tmp+fsync+rename, one SQLite transaction + partial unique index, guarded reads |
| 6 | `origin_prefix_for` catch (**B-469**) | not found | **P1** | Silent semantics degradation on the sealed trust-loss path |
| 7 | Coherence margin (B-466), baseline refresh (B-465) | P1-04/05 | Unchanged — both already filed here before their review | Lab-gated |
| 8 | Identity/AuthZ (their P1-02) | P1 | **Explicitly deferred, honestly** | Correct for any multi-user future; this is a single-operator lab and B-301 already holds the position. Do not build RBAC theatre before an identity provider exists |
| 9 | IS-IS flow (their P1-06/Tier 1) | P1 | **The first Part 2 item, unchanged** | It was already B-107 / PLAN-V2 P2.2; their review independently converging on it is good evidence for the existing plan, not a new instruction |

Their Tier 2–4 product roadmap is sound and maps almost item-for-item onto the existing
backlog (B-201/202 event-driven, B-203-205 memory, B-416 episodes, Q-019 multi-fault,
B-427 corpus). **The next level is not new ideas — both reviews and the backlog now agree
on the same list.** The constraint is sequencing, and the sequence above is the first
seven rows.

## 4. What this review did not do

No live-lab contact, no fault injection, no provider API calls, no dependency CVE scan.
`checks.py` and `template_parsers.py` were read for structure and probed at their edges
but not line-audited against IOS-XR documentation — their ground truth is the fixture
corpus, and re-deriving it is what rounds are for. And per OBS-147: this pass found
three defects in its own probes before they ran (two wrong import paths and one
fixture-label assumption), which is the usual evidence the instrument was actually used.
