# Changelog

All notable changes to this project are documented in this file. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this
project does not yet promise semantic-versioning stability outside the CLI
and MCP surfaces (see README's "product surface for 1.0" note).

## [2.1.0] — 2026-08-28

### Added

- `expand_lab_evidence`, a staged-MCP-only, bounded log-evidence drill-down.
  It accepts only an opaque identifier issued by a recent `investigate_lab`
  result and one previously disclosed evidence key; contexts expire after five
  minutes, are memory-only, and never recollect or persist raw device output.
- Reproducible parser and model-visible evidence coverage auditors.

### Changed

- Parser coverage on the unchanged 13,335-line committed fixture corpus rose
  from 63.01% to 71.48% structured lines (+8.47 percentage points): all 1,129
  deferred lines now parse into typed evidence, with zero unaccounted lines.
- Budget-truncated model evidence now carries a structured omission disclosure.

### Security

- The staged evidence-expansion capability is structurally excluded from
  event-model offers, preventing fabricated or cross-run expansion contexts.

## [2.0.0] — 2026-08-28

### Added

- Durable SQLite event, incident, notification-outbox, procedure-approval,
  campaign-reporting, and autonomous recovery workflows.
- Persistent Docker recovery sidecar with fenced event/outbox leases,
  heartbeat renewal, recovery observability, and conservative compaction.
- Bearer-authenticated MCP streamable HTTP deployment with persistent state and
  direct trusted-LAN/VPN access on plain HTTP port 8000.

### Changed

- MCP HTTP deployment now publishes directly on `0.0.0.0:8000`; SSH tunneling
  and transport encryption are no longer part of the deployment.
- Telegram event delivery uses durable editable cards, retry/backoff handling,
  dead letters, campaign phase updates, and operator audit provenance.
- Public documentation and repository contents were consolidated for release;
  generated measurements and internal review history remain available in Git
  history rather than the current tree.

### Security

- Direct MCP HTTP remains protected by a required bearer token but has no TLS.
  It is intended only for trusted LAN/VPN networks and must not be exposed
  directly to the public internet.

## [1.2.0] — 2026-08-21

An overnight wave run as five parallel lanes with one orchestrator. Every
headline claim below was re-verified by an independent probe before merge;
three of those probes changed the conclusion, and those are called out.

### Added

- **Event episodes** (B-416, `log_episodes.py`) — a time-bounded, ordered
  sequence of log events, joined under a bound **derived per adjacent pair
  from the protocol timers** (BGP 180s, IS-IS 30s), with the bound recorded
  on each join. Verified against the real `PE2/broken` capture: the
  interface → IS-IS → config-commit → BGP chain survives as one episode
  across the measured **153.349s** gap — the link a few-second threshold
  would have severed. An episode is the log-side mirror of the dependency
  descent, and evidence *for* one, never a substitute: two of the descent's
  five rungs have no log event at all.
- **Temporal shape** (B-418) — `max_rate_1m` and burst counts beside the
  totals, so 60 events in ninety seconds stops reading like 60 across an
  hour.
- **Relay hardening** (B-209, `relay_policy.py`) — silences, ownership
  grouping and de-duplication on the outbound path. Every failure mode
  falls back to **sending**: for a notifier the dangerous direction is a
  page that never arrives, the opposite of a capability gate. A failed send
  never records the signature, so a provider outage cannot mark a page
  delivered and swallow it on recovery.
- **Ticket handover view** (B-446) — what changed since the previous run on
  the same subject, and which rungs recovered.
- **The evaluation corpus** (B-427, `docs/build/EVALUATION-CORPUS.md`) — a
  confusion matrix over the archived rounds, obeying the rule that a past
  score is never rewritten.

### Changed

- **EER-015 closed as four extractions, zero splits** — the review's own
  instruction. 17 duplicated persistence definitions collapsed to 5
  (`_persist.py`); the snapshot-root resolver de-duplicated; the status
  vocabulary given its own module so `checks.py` no longer imports the
  transport layer; the truthy/falsy literals consolidated without touching
  either gate function body. All 66 pre-existing mutation guards re-run and
  diffed guard-by-guard against a pre-wave baseline: zero differences.
- **`suspicious_baseline` is now a role invariant, not a value test**
  (B-465) — it flagged a recorded baseline of `0` and missed an equally
  wrong `1`.

### Fixed

- **OBS-691 — device text reaching a reader unwrapped.** A test asserted the
  descent's `reason` needed no containment "because it is code-typed, not
  free text", and passed for months because its fixture reason was a
  hand-written string with no device text in it. Measured instead: a single
  broken-fixture run puts the far end's own words into `reason` at three
  rungs. `reason` and `current_reason` are now contained, and both tests
  were rewritten with fixtures that carry real quoted device text so neither
  can pass again by not exercising its own subject.
- `chars_withheld` was a hardcoded `0` claiming a measurement that was never
  taken; it is `None`.
- `fault_lab.py`'s `restore()` wrapped a pure dict comparison inside the
  device-read `try`, so a code defect there would have been reported as
  "could not read device".

### Found, not fixed — two live faults and a false clean

Recorded here because they are the wave's most important output and they
are **not** repository changes:

- **OBS-690 / OBS-692 — two leftover injected faults on the lab**, from
  earlier chaos rounds whose restores were never verified. One aborted
  round 6 at its own preflight (the fabric already read the finding the
  sealed prediction was about, which would have made the round vacuous).
  Both need a one-line operator revert; neither was touched unsupervised.
- **OBS-692 — a seven-hour false clean.** PE1 held an MD5 BGP password
  toward RR1: 4,882 authentication failures between 16:34 and 23:22, while
  the session itself never dropped once. Every rung therefore read healthy
  and `investigate` returned `all_layers_healthy`, `trustworthy: true` —
  correct at every layer it examines, wrong about the network. The descent
  structurally cannot catch this: enumerating every rung's evidence input
  across all four flows, none reads a log source, and the fault exists only
  as a *rate of rejected connection attempts*, never as state. This is the
  false-clean class the current SOTA plan singles out as unmeasured, arriving on real
  hardware, and it is the strongest justification yet for the log axis
  shipped in this same release.

## [1.1.0] — 2026-08-20

Boundary repair. Closes the remaining findings from the independent
engineering review of `v1.0.0`,
whose verdict was that the core is sound but *"several boundary layers do not
preserve the guarantees claimed by the core."* Every fix below was verified by
probe against the real system, not inferred from the change.

### Security

- **SSH now verifies host identity (EER-002).** Previously every connection
  silently trusted whatever key a device presented, so a management-path
  attacker could impersonate a router and return fabricated output that every
  deterministic check below would treat as truth. Verification runs against a
  dedicated trust store — never the operator's own `~/.ssh/known_hosts`, which
  would inherit unrelated trust — populated by `scripts/enroll_host_keys.py`.
  A host-key mismatch is also no longer classified as a *transient* failure,
  which would have retried it several times before reporting a generic
  connection error. Verified against the live lab: correct key connects, one
  flipped bit in a recorded key is refused, restoring it connects again.
- **Raised exceptions are sanitised, not just returned values (EER-006,
  EER-007).** The MCP boundary wrapped returns but not raises, so an exception
  carrying raw device output reached the client verbatim; the agent loop had
  the same hole into a model prompt. Both now route through the existing
  classifier. A tool raising with device text now yields *"an unclassified
  error; its detail is withheld because a transport exception can embed device
  output."*
- **Four configuration gates failed open (EER-008).** `NETTOOLS_ALLOW_ACTIVE_
  PROBES=flase` enabled device-side traffic; an unrecognised `NETTOOLS_MCP_
  SURFACE` selected the *wider* surface; an unrecognised evidence backend
  silently downgraded, splitting history across two stores. All now fail
  closed, with defaults unchanged — default-on and typo-fails-closed are
  independent properties.
- **Durable data is owner-only (EER-019).** Evidence, tickets, the ledger,
  session memory, metrics and the audit log no longer inherit the umask.
  Verified under `umask 000`.
- **Private-first vulnerability disclosure (EER-018).**

### Fixed

- **Flap detection silently disabled on SQLite (EER-005).** Under
  `NETTOOLS_EVIDENCE_BACKEND=sqlite`, `detect_flaps` read the filesystem
  directly, found nothing, and returned `{"flapping": [], "snapshots_examined":
  0}` with exit 0 — byte-identical to a genuinely stable device. A real flap
  became a clean bill of health, in the one check whose whole job is spotting
  instability. It now reads through the store contract. `snapshots_skipped`
  reports 0 for sqlite as *"not measurable yet"* rather than a fabricated count.
- **The wheel omitted its own runtime assets (EER-003, EER-004).** Prompts and
  a 180-file demo fixture subset are packaged, so a `pip install` can run the
  documented offline demo. CI now builds the wheel and container and runs that
  demo from outside the checkout — the check whose absence let both ship.
- **Metrics lost concurrent updates (EER-011).** A process-local lock plus a
  read-once cache meant two of the forty processes an event fan-out spawns
  would each write the same incremented value. Now serialised by a
  cross-process flock.
- **Inventory accepted ambiguous and path-unsafe identities (EER-009).**
  Device names, router IDs, AS numbers, ports and credential env-var names are
  validated; duplicate `router_id`/`mgmt_ip` are rejected. The three duplicated
  storage-key charsets are now one shared policy.
- Model calls carry an enforceable deadline (EER-010); Ollama's hardcoded 120s
  is configurable.

### Added

- Coverage measurement (93%, floor 90) and a report-only `pip-audit` job
  (EER-014). Its first run found two real advisories — see `FINDINGS.md`
  OBS-645, including why one is not currently fixable.
- Dependency upper bounds, a digest-pinned base image, and a constraints file
  (EER-013).
- Current planning and non-goals were consolidated; the superseded plan remains in Git history.
  and `docs/build/BACKLOG.md`.
- `BACKLOG.md` is now machine-checkable: one authoritative status per item, a
  documented vocabulary, and a test that enforces both (EER-016).
- Makefile targets work without a pre-activated venv (EER-020).

### Deferred, deliberately

**EER-015** (module size) and **EER-017** (production ops workflow) — both
large refactors, and doing them immediately behind a security release
maximises the chance of reintroducing what was just removed.

## [1.0.1] — 2026-08-20

Security patch. One finding from an independent engineering review of the
v1.0.0 tag, reproduced against a canary before the fix and pinned by
regression tests afterwards.

### Fixed

- **Path traversal in evidence storage (EER-001, critical).**
  `_validate_device_name` was applied on the save paths only, so the file
  backend joined unchecked caller input under the evidence root on every
  read, list and prune. `prune(device_name="../outside")` reported one
  removal and **deleted a JSON file outside the root** — confirmed by
  reproduction, not inferred. Now validated and containment-checked at
  `FileEvidenceStore._device_dir`, the single point every filesystem access
  in that backend passes through. The SQLite backend refuses the same names
  for parity (it was never traversable — its queries are parameterised).
- **The same trust shape in fixture paths.** `fixture_path` joined
  caller-supplied platform, device name and label straight into a path; a
  label of `"../.."` would read or write outside the fixture corpus. Both
  gates applied there too.

Not reachable from the MCP server in either case: `prune` is CLI-only, and
no MCP tool takes a fixture label. The exposure was to a mistaken or
malicious CLI argument, inventory entry, or direct API call.

## [1.0.0] — 2026-08-20

First tagged release. Read-only Cisco IOS-XR inspection for a single-user
lab: a deterministic dependency descent that localises the lowest broken
protocol layer, an optional grounded LLM narration layer that never touches
the diagnosis itself, and an MCP server exposing the same read-only surface
to a model client.

### Added

- **Deterministic investigation** — `nettools investigate DEVICE SUBJECT
  [--flow bgp_session|interface|isis_adjacency|ldp_session]` walks a
  dependency chain rung by rung and reports the lowest broken layer plus its
  causal chain, from one evidence epoch (one observation window per device).
  `--from-fixtures` replays committed captures with no lab, credentials, or
  API key required. `--reconcile-config` (opt-in) adds a second, live check
  comparing configured intent against observed state for the interface a
  descent bottomed out on, when the rungs alone could not localise a cause.
- **Fabric-wide checks** — `nettools health [--all] [--silence-file PATH]`
  (per-device and fabric verdicts, maintenance-window silencing that
  annotates a suppressed finding without ever dropping it),
  `nettools audit` (fabric-vs-itself consistency), `nettools analyze
  [--fabric]`, `nettools fabric`.
- **Read-only observation** — `nettools watch [DEVICE|--all]` polls Loki,
  collapses repeated log lines to one root cause per group, and reports a
  routing decision without ever opening a ticket or running an
  investigation.
- **Event routing** — `nettools route-event` turns a piped Alertmanager
  alert or syslog line into a flow selection.
- **The flight recorder** — every `investigate` run appends to the
  diagnosis-accuracy ledger (`nettools ledger summary` /
  `nettools ledger verdict ID OUTCOME --by NAME`) and writes a per-run
  ticket: an append-only Markdown record of every device interaction, tool
  event, cited evidence source, the full model exchange (system prompt,
  user payload, and response, addressable by content hash), and — once a
  human records a verdict — that verdict too.
- **Incident correlation** — diagnoses sharing a cause are grouped into one
  incident, excluding (and counting, never silently dropping) any row
  missing the identity fields a real match requires.
- **37 read-only CLI subcommands**, **37 classic + 8 staged MCP tools**, and
  a validated parameterized-template surface (`route`, `bgp-neighbor`,
  `interface`, `sr-policy`, `logging`, `ping`, `traceroute`) — the same
  narrow allowlist of `show`/`ping`/`traceroute` commands on every path,
  checked against the device's own platform before any credential loads.
- **60 mutation-tested guardrails** (`scripts/mutate_guards.py`) proving
  each safety and grounding invariant actually fails when removed, not just
  passes when present.
- Optional LLM reasoning layer (Anthropic, OpenAI, or a local
  Ollama-compatible provider) that narrates a deterministic report — never
  authors it. No unparsed device text ever reaches a model, structurally
  enforced rather than filtered.

### Fixed (release-1.0 cleanup)

- **Security:** `mcp_server/server.py` loaded `.env` at import time,
  contaminating every process that imported the module (including tests)
  with real secrets from a developer's `.env`. Moved into `main()`.
- **Packaging:** `inventory/lab.yaml` was not shipped in the wheel, breaking
  a non-editable install. Now packaged with a byte-identical drift guard.
- Ten duplicated `_float_env`/`_int_env` implementations (six modules) that
  had quietly drifted on two independent axes (inf/nan handling, sign
  checking) unified into one shared, audited implementation.
- Two independently-drifted interface-abbreviation tables merged into one.
- Six MCP error envelopes missing required keys, fixed to match the
  5-key contract every other envelope already honoured.
- Five never-raised exception classes and other dead code removed.
- CLI startup time cut roughly 4x (lazy submodule imports); a probe-identifier
  test file's runtime cut roughly 7x (cached repo walk).
- Byte-pinned architecture diagrams no longer embed the commit date or
  branch name — a release tag or a day rollover could otherwise turn CI red
  for a tree that had not actually changed.

### Wired this release

Several capabilities were built in earlier phases but never connected to a
live caller: fabric silencing (`--silence-file`), the diagnosis ledger's
`run_id`/cause-subject fields (enabling ticket↔ledger correlation and a
human verdict updating its ticket automatically), the full ticket-recording
surface (intent, tool events, evidence provenance, context footprint), and
config-vs-device reconciliation. `reasoning_gate.py` remains an intentional,
documented exception — built, measured, and refused by evidence (953
fixture investigations swept, 8 cases needing it, all one root cause;
`config_diff` already explains it), not merely unfinished.
