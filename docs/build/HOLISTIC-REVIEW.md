# Holistic Review — 2026-08-18

Five perspectives on the repository at the close of Part 1: four read-only
reviewer lenses run in isolated worktrees, plus the orchestrator's own pass.
Each lens verified its claims by execution, not recollection. Every load-bearing
finding was then re-verified by the orchestrator's own probe before acceptance —
none was inherited on a reviewer's word (the method OBS-147 requires: *a
verification finding nothing must be asked what it found wrong with itself*).

This document is the synthesis. The finding-by-finding disposition is below;
the meta-finding is OBS-156 in `FINDINGS.md`.

---

## The verdict, up front

**Two serious defects, both in the OPS wave** (the one wave built solo after its
four agents were lost to a spend limit, OBS-155), **both found independently by
two different lenses, both fixed the same day with mutation-verified regression
tests.** Everything else was either a cheap correctness/UX fix (applied) or a
capability-level suggestion (filed B-481..B-489). Nothing in the core
architecture — the descent, the grounding gate, the allowlist boundary, the
offline path — was found wanting; the reviewers probed all four and each held.

The signal worth naming: the two worst issues were found *twice, separately*.
A single reviewer finding one is a report. Two reviewers finding both, without
seeing each other, is a measurement — and it measures that solo-built code, held
to the same specs and the same merge gate, still carried a defect class that
neither line-by-line review nor the 1953-test suite caught. The missing gate was
not more careful reading; it was a second, differently-motivated pair of eyes.
That is the argument for the review being multi-lens rather than deeper-single-
lens, stated as evidence.

---

## The five lenses

| Lens | Method | Headline |
|---|---|---|
| **Adversarial new-code** | Mutation-tested every guard in an isolated copy; probed the OPS-wave modules with hostile input | The P0 shell-injection path; five vacuous/convention guards; a genuinely vacuous escape test |
| **Invariants by probe** | Canary sweep over *every* egress path on both MCP surfaces; frozen-file hashes; allowlist-before-credentials on every entry point | The MCP boundary never got B-467's free-text treatment — device text reaches the client unmarked |
| **Docs drift** | Derived every claim twice (docs vs code) | 20 drifts; the misleading class (dual totals, 35-commit-stale handover, undocumented commands) |
| **Operator experience** | Ran every subcommand on fixtures from a clean setup | Import-time `--help` crash on a bad inventory; stale page-one model narrative; audit-table misrender |
| **Orchestrator** | Verified each lens's load-bearing claims by own probe; owns the merge gate | The two-lens convergence (OBS-156); the fixes and their mutation guards |

---

## The two that mattered — SECURITY

### 1. The MCP boundary never inherited B-467's free-text quoting *(B-481, fixed)*

`mcp_server/boundary.py`'s `sanitize()` withheld raw `commands` buffers but
passed device-authored free text under `parsed.records` — a syslog line's
`text`, a BGP neighbor's `last_reset_reason`, an interface `description` — to
the MCP client **completely unmarked.** The MCP client is a model consumer; this
is the exact "an unauthenticated attacker writes it into device output" threat
model B-467/B-470 exist to close.

Both the adversarial lens (canary through the registered tools) and the
invariant lens (canary sweep over every egress path) reproduced it on **both**
the `classic` and `staged` surfaces. The orchestrator confirmed it with its own
canary before fixing.

Why it existed: B-467 shipped the projector for the four *model-egress* paths
(`llm_analysis`, `evidence_budget`, `prompt_library`, `agent_loop`). The MCP
boundary was a **fifth path to a model** and its scope never listed it — the
same "an invariant that holds for every internal caller is a convention that has
not met a new consumer" lesson CLAUDE.md already records for invariant 4,
recurring one consumer later.

**Fix:** `boundary.py` imports the projector's `FREE_TEXT_FIELDS` and
`quote_device_text` (mcp_server may depend on agent_nettools, never the reverse)
and wraps those fields in the same `<<<DEVICE-TEXT untrusted>>>` delimiters. The
content is preserved as data; it is marked so a client reads it as data, not
instruction. Regression-tested; mutation-verified (guard `B-467-MCP`).

### 2. The P0 shell-injection path *(B-482, fixed)*

`route_alertmanager` never validated its `subject` label (the syslog path does),
so an Alertmanager label of `"10.0.0.1; touch /tmp/x #"` flowed straight into
`suggested_command`. And the shipped n8n example *joined* `suggested_command`
into a shell string and echo-piped the webhook body — the exact anti-pattern
`RoutingDecision.suggested_command()`'s own docstring warns against and the
sibling systemd example got right. The same PR got the pattern right in one
artefact and wrong in another.

Found by both the adversarial lens (marker-file reproduction through `sh -c`)
and the invariant lens (code read + threat model). Fixed in depth:

- `_validated_subject` — IPv4-or-interface reconstruction, refuses anything with
  shell metacharacters. `render_command` re-validates at run time too, but the
  suggestion must not carry an unvalidated value in the first place.
- Two P1 crashes closed: a non-dict alert entry and a non-string device label
  both took the CLI down with a raw traceback instead of the documented 0/1/2
  scheme — now stated refusals.
- The n8n workflow passes the body on **STDIN** (never interpolated into the
  command string) and keeps `suggested_command` an **argv array**, never shell-
  joined.
- The systemd bridge: `ThreadingHTTPServer` (one held connection can't starve
  it), bounded/rejected `Content-Length` (a raw `-1` blocked forever), capped
  fan-out, a shared-secret header, an Origin/CSRF check, and systemd sandboxing
  directives. Still an example, now honest about the shape a real bridge needs.

Mutation-verified (guard `P0-ALERTMANAGER-SUBJECT`).

---

## Correctness & UX — verified, then fixed

| # | Finding | Lens | Disposition |
|---|---|---|---|
| C1 | A bad `NETTOOLS_INVENTORY` crashed **`nettools --help` itself** with a raw traceback — `DEVICES = all_devices()` ran at import, before argparse | operator | `DEVICES` is now lazy; the excellent `InventoryError` renders cleanly. Deliberately not try/except-at-import (that would hand callers a plausible empty dict) |
| C2 | `--help` synopsis omitted `investigate`/`audit`/`route-event` — three of the newest commands invisible in the cheat-sheet | operator | Added all three |
| C3 | `audit --format table` misrendered as a single-device health verdict (shared `severity`+`findings` keys), printing `DEVICE: ?` | operator | Own renderer dispatched on the `tool` marker first |
| C4 | `list_devices()` — and `explore_lab()`/`check_lab("fabric")`/the `lab://inventory` resource documented "no credentials" — actually *required* `DEVICE_USERNAME`/`PASSWORD` | invariant | Reads the parsed inventory file directly; genuinely credential-free now, with a regression test |
| C5 | NaN passed every range check (`nan<min` and `nan>max` both False) in `_float_env` and the settings validator — a `=nan` typo became a timeout that never fires | adversarial | Rejected as non-finite in both |
| C6 | The `ERROR_KINDS` "credential is not configured" entry was **dead code** — the `": "` partition split on the first colon and lost the kind | invariant | Classify on the whole text; mirrored in both copies |
| C7 | `flow_for`'s unknown-type error led with the declared-but-unimplemented vocabulary a retry would try next | operator | Leads with the implemented flows |
| C8 | `nettools inspect` couldn't test the staged surface — `NETTOOLS_MCP_SURFACE` wasn't forwarded to the spawned child | operator | Forwards `NETTOOLS_*` + credential vars to the child |
| C9 | Staged tool params were bare `str`; the JSON Schema didn't enforce the enums the prose promised | operator | `Literal[...]` on every enum param; in-function branches stay for non-validating clients |
| C10 | Safe local selection errors (`unknown intent 'bogus'`) were withheld by the boundary, teaching neither human nor navigator | operator | Static valid-value kinds added to `ERROR_KINDS` |
| C11 | `_isolated_but_configured` silently excluded the zero-records case its own docstring named as the clearest debris | adversarial | Zero records is now in scope |
| C12 | `run_audit` crashed the whole fabric on one non-dict device evidence | adversarial | Guarded at `configured_hostname`, the shared reader → that device lands in `unevaluated` |
| C13 | `explore_lab` collected evidence **twice** on the tool billed as the cheapest opening move | adversarial | One collection; `collect_evidence` already carries `facts` |
| C14 | `explain_mnemonic(12345)` raised `AttributeError` | adversarial | Guards non-str |
| C15 | The ISIS-MTU mnemonic stated an unqualified "adjacency forms, then fails" — IOS-XR pads IIH to MTU by default, so a mismatch normally *prevents* the adjacency | adversarial | Caveat added |
| C16 | `investigate --format table` clipped the load-bearing REASON column at 80 chars with no hint | operator | Prints a "run with --format json" hint when any reason was clipped |
| C17 | Quick Start ran the live-only `make facts` right after the offline demo with no warning | operator | Offline commands marked offline; `make facts` marked LIVE with its requirements |
| C18 | Page-one "What the model is for" read as if a model renders the chain by default (B-439 made it opt-in behind `--paraphrase`) | operator | Rewritten to state plainly that no model runs by default |

## Vacuous / convention-only guards

The review's own discipline turned on the tests. Findings:

- **A genuinely vacuous companion** *(fixed)*: `test_the_query_is_capped_and_escaped`
  only asserted capping — removing `re.escape` passed it identically. Rewritten
  to prove `.ntent` does not match `intent`.
- **The frozen-file byte-identity check lived only in `scripts/preflight.sh`**, a
  manual pre-lab script nothing in CI runs — so a relaxed validator in
  `platforms.py`/`templates.py` would merge through ruff+pytest with nothing
  catching it *(fixed: `tests/test_frozen_files.py`, parametrized over the four
  files, fails CI on any change)*.
- **`_run_rendered_command`'s gate is verb+char-only** and would admit a
  `show running-config`-shaped string; safe today only because it has exactly one
  caller. A structural fact, not enforced by the function *(filed B-489)*.
- The two headline security fixes were themselves mutation-tested before landing,
  so their regression tests cannot become the vacuous companions OBS-153 warned
  about. **20/20 guards hold** after the two additions.

## Docs drift *(lens 3, fixed in commit `c1db538`)*

Twenty findings; the misleading class fixed. The sharpest: `BACKLOG.md` carried
**two contradictory totals** six lines apart (a header "98" and a leftover
pre-Gate-Zero "95"); `SESSION-HANDOVER` — the designated read-first document —
was **35 commits stale**; and `audit`/`route-event`/`config` shipped documented
nowhere an operator looks. Fixed: totals derived not hand-maintained (and the
*last* stale "95 items" block, found this pass, replaced with a derive-it
instruction), handover refreshed, the three commands documented in
README/CLAUDE/Makefile, SECURITY.md's "B-467 open" framing corrected, and a new
`.env.example` ↔ `settings.py` sync test.

---

## What held — probed, defended, kept

The reviewers were asked to attack, and these are what they could not break —
worth recording because the guard holding is rarer in this project's log than
the guard failing:

- **The offline path is a fact, reproduced, not a claim.** `make setup` →
  `pytest -q` (1953 passed, 24 skipped, ~15s, zero network) → the fixture demo,
  byte-for-byte matching the causal chain the README describes. No lab, no key.
- **No device write path.** One chokepoint (`_netmiko_send_commands`); every new
  OPS-wave caller funnels through the allowlist or the template gate, re-checked
  immediately before credentials load. `audit.py` makes **zero** device calls —
  it reads previously-parsed evidence. Four frozen files byte-identical to the
  `6629a2c` baseline.
- **Allowlist-before-credentials holds on every entry point probed** — CLI, MCP
  classic, MCP staged — each refusing at credential resolution, strictly after
  validation, never opening a socket.
- **Egress bounds hold in the library.** The notifier has no evidence parameter;
  it refused a 10,312-char report before touching the network; the token
  redactor replaced a canary in a `URLError`.
- **The descent's regexes are ReDoS-free**; the knowledge search survived
  empty/unicode/10k/regex-metacharacter queries; the staged surface's
  registration/sanitisation boundary withholds raw device text exactly as
  classic (shared code path).
- **stdout/stderr discipline is real and tested** (B-422): `nettools audit
  --format json 2>/dev/null | jq` parses cleanly; `--quiet` means silence
  everywhere checked.
- **The self-correcting culture is genuine, not decorative.** The README's dated
  OBS-103 retraction matches what `learn-topology` outputs today; the backlog
  argues with its own past claims (B-457) instead of editing history.

---

## Product judgement *(operator-experience lens on `next-level.md`/`OPS-WAVE-PLAN.md`)*

**Endorsed:** the core thesis ("the dumber the model can afford to be, the
better the architecture", proven by a 4B navigator on description quality alone);
`verify_fix` as the right #1 pick with its `PARTIAL` masked-second-fault detector;
the optics/light-levels rung; `blast_radius` as an under-served NOC workflow.

**Pushed back:** `watch` ranked too high — a synchronous wall-clock CLI loop is a
debugging convenience, not an incident-response capability; the two-model station
rests on one offline measurement with no regression discipline for the navigator
*(filed B-487)*.

**Named as the next things to build, none of which either document proposes:**
maintenance-window/silence support — the single biggest practical production gap
*(B-483)*; ownership/escalation routing *(B-484)*; a diagnosis accuracy ledger
*(B-485)*; cross-alert correlation across concurrent runs *(B-486)*.

**Recommended to demote:** `nettools agent`'s free-form tool-calling loop, at
odds with the "the model navigates a menu, never synthesises a diagnosis" trust
story — quarantine behind explicit opt-in *(filed B-488, operator's call)*.

---

## Verification

- **Suite:** 1963 passed, 24 skipped (was 1953 at review start; +10 from the new
  regression tests). `ruff check .` clean.
- **Frozen files:** all four byte-identical to `6629a2c`, now enforced by
  `tests/test_frozen_files.py` in CI, not only by the preflight script.
- **Mutation guards:** 20/20 hold, including the two new security guards
  (`B-467-MCP`, `P0-ALERTMANAGER-SUBJECT`) — every headline fix has a test proven
  to fail when its guard is removed.
- **Commits:** `c1db538` (docs drift) → `3cc1f04` (two headline fixes + UX) →
  `d2fa896` (dead ERROR_KINDS entry) → `25e220b` (credential-free list_devices) →
  `4541cae` (page-one narrative, signposts, mutation guards, OBS-156). All pushed
  to `feat/investigation-layer`.
- **Backlog:** B-481..B-489 filed (two DONE security fixes, seven OPEN/DEFERRED
  capability items), each carrying its lens and, for DEFERRED, its unblocking
  condition.

## What this review did not do

It did not empty the backlog, and it did not touch the operator-gated lab work
(round 8b, round 6, the MCP re-test). Those remain exactly where the runbook
left them. The capability suggestions (B-483..B-488) are **filed, not built** —
scaling the tool toward production is the operator's sequencing call, and this
review's job was to make the current surface correct and honest, which it now is.
