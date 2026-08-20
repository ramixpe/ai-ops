# Changelog

All notable changes to this project are documented in this file. Format
follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/); this
project does not yet promise semantic-versioning stability outside the CLI
and MCP surfaces (see README's "product surface for 1.0" note).

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
