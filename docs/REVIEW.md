# Code Review & Roadmap — IOS-XR Read-Only Network Tools

Date: 2026-07-28
Scope: full tree — `src/`, `lab/`, `mcp_server/`, `scripts/`, `tests/`, build and docs files.
State at review time: **32 tests passing, Ruff clean.**

> **Resolution note (follow-up pass):** All findings in Sections 2–4 (O1–O14,
> S1–S6) were addressed, plus several Section 5 items (structured fabric
> generalization + parallel collection, evidence baselines/diff, SSH-key auth,
> JSONL audit logging, extra MCP workflow tools, and CI). The project is now
> pip-installable (`nettools` / `nettools-mcp` console scripts), the top-level
> `lab/` and `scripts/` folders are gone (merged into `src/agent_nettools/`),
> and all `PYTHONPATH` shims were removed. Deferred (need external infra or are
> large standalone efforts): TextFSM structured parsing, JSON-schema structured
> verdicts, autonomous agent tool-loop, notifications, scheduling, published
> image registry, containerlab import, and an async collection rewrite. State
> after the pass: **37 tests passing, Ruff clean.**

---

## 1. What the project does well

Worth stating first, because the core design is genuinely solid and should not
be "cleaned up" away:

- **Exact-match command allowlist.** `APPROVED_COMMANDS` is checked with set
  membership, not substrings, so there is no injection path (`show version; reload`
  is simply not in the set). The check runs *before* credentials are loaded or
  any connection is opened.
- **Credentials never leak.** They come from the environment only, are never in
  returned payloads, and a test (`test_list_devices_does_not_expose_credentials`)
  pins that.
- **No generic executor.** There is deliberately no `run_command(device, command)`
  tool, and `test_safety.py` asserts on the MCP module's public surface so one
  cannot be added unnoticed.
- **Structured results everywhere.** Every tool returns the same
  `{tool, device, status, timestamp, data, errors}` envelope — easy for both
  humans and LLMs to consume.
- **Injection seam for tests.** `sender=` lets the whole stack be tested with no
  live SSH.

---

## 2. Findings — fixed in this review cycle

| # | Finding | Fix |
|---|---------|-----|
| F1 | MCP server never loaded `.env`; every tool failed when a client spawned `server.py` directly (`make mcp`, Docker, Claude Desktop config). Only `make inspect` worked, because the client leaked its own env in. | `load_dotenv()` added to `mcp_server/server.py`. |
| F2 | `make lint` failed: `scripts/test_mcp_client.py` was mangled (blank line between every statement, unsorted imports, no trailing newline). | File reflowed; lint green. |
| F3 | `verify_migration.sh` was removed but `make verify`, README sections, and `.dockerignore` still referenced it. | All references removed; `make clean` also fixed (its `**` globs never matched under POSIX sh). |
| F4 | **One SSH login per command.** `collect_evidence` opened 7 sessions for 7 commands on the same device — slow, and IOS-XR rate-limits logins. | `_netmiko_send_commands` runs all commands over one `ConnectHandler` session; `collect_evidence` batches all 7 commands into a single login and slices results per section. Error attribution preserved (per-command vs connection-level). |
| F5 | `max_tokens=1200` silently truncated analyses; `stop_reason` was never checked (a refusal printed an empty string). | Shared `MAX_OUTPUT_TOKENS = 16000` across all three providers; truncation appends a visible notice; refusal raises. |
| F6 | No error handling around LLM calls — an auth failure or 429 crashed with a raw traceback. | `LLMAnalysisError` + most-specific-first exception chains for Anthropic, OpenAI, and Ollama; scripts print `Analysis error: …` and exit 1. |

Deferred by decision: relying on the Anthropic SDK's own credential resolution
(`ANTHROPIC_AUTH_TOKEN`, `ant auth login` profiles) instead of requiring
`ANTHROPIC_API_KEY` specifically.

---

## 3. Findings — open

Severity: **H** = wrong/misleading today, **M** = will bite later, **L** = polish.

### Code

| # | Sev | Finding | Recommendation |
|---|-----|---------|----------------|
| O1 | M | **Duplicated command lists.** `EVIDENCE_COMMANDS` and the six per-check tools (`get_device_facts` … `check_sr_policies`) hard-code the same strings independently. Adding a command to one and forgetting the other silently drops it from evidence collection. | Make the six tools read from the map: `_run_approved_commands(name, EVIDENCE_COMMANDS["interfaces"])`. One source of truth. |
| O2 | L | **Dead code:** `analyze_with_claude` shim in `llm_analysis.py` — nothing imports it anymore. | Delete. |
| O3 | L | **Serial fabric check.** `check_fabric_bgp` logs into 9 devices one after another; wall-clock ≈ 9× one device. | `ThreadPoolExecutor(max_workers=len(devices))`. Only worth it if the target is used often. |
| O4 | L | **Inconsistent test seam.** The six per-device tools don't accept `sender=`, so `test_lldp_isis_sr_tools_use_approved_commands` drives the private runner and only asserts `callable(tool)`. | Falls out of O1 — once the tools share the map, thread `sender=` through and test them directly. |
| O5 | L | **Style nits in `llm_analysis.py`:** one blank line after `class LLMAnalysisError` (PEP 8 wants two; Ruff's blank-line rules are preview-only), and `PENE_TROUBLESHOOTING_PROMPT` reads like a typo unless it's an acronym for the prompt's Purpose/Examples/kNowledge/Evaluation sections — rename or add a comment. |
| O6 | L | **Stale package exports.** `src/agent_nettools/__init__.py` exports only 4 of the 9 public tools — missing `check_lldp_neighbors`, `check_isis_neighbors`, `check_sr_policies`, `check_fabric_bgp`, `collect_evidence`, and everything from `llm_analysis`. | Export the full public surface (or export nothing and let callers import from submodules — but be consistent). |

### Documentation drift

| # | Sev | Finding | Recommendation |
|---|-----|---------|----------------|
| O7 | H | **README safety boundary lists 5 commands; code approves 7.** `show isis neighbors` and `show segment-routing traffic-eng policy` are missing. For a project whose whole point is the allowlist, the docs must match it exactly. | Update the list — or generate it: a tiny doc test asserting the README block equals `APPROVED_COMMANDS` keeps it honest forever. |
| O8 | H | **`mcp_server/README.md` lists 4 tools; the server exposes 8.** Missing `check_lab_lldp_neighbors`, `check_lab_isis_neighbors`, `check_lab_sr_policies`, `check_lab_fabric_bgp`. | Update. |
| O9 | M | README "Common Commands" omits `lldp`, `isis`, `sr`, `fabric-bgp`, `demo`. | Update (or just point at `make help`, which is already complete — one list fewer to maintain). |
| O10 | L | `lab/devices.md` duplicates the README inventory table (minus roles). | Keep the richer `lab/devices.md`, link to it from README instead of duplicating the table. |

### Project hygiene

| # | Sev | Finding | Recommendation |
|---|-----|---------|----------------|
| O11 | H | **Not a git repository.** `.gitignore` exists, but no history — every fix so far is unversioned and unrecoverable. | `git init`, commit, then everything below becomes reviewable diffs. |
| O12 | M | **No `.env.example`.** `.gitignore` line `!*.example` anticipates one; a fresh clone has no template. | Copy `.env`, strip values, commit as `.env.example`. |
| O13 | M | **Docker image carries dev/unused deps.** The single `requirements.txt` puts `pytest`, `ruff`, and `python-dotenv`-adjacent dev tooling into the runtime image; `openai` is installed even for Anthropic/Ollama-only users. | Split `requirements.txt` (runtime) / `requirements-dev.txt` (pytest, ruff). LLM SDKs are only needed if the container should run analysis — the MCP server itself needs none of them. |
| O14 | L | LICENSE file was removed alongside the migration script. If the project is ever published, it needs one again. | Restore a LICENSE (the old one was MIT-length). |

---

## 4. Structure review

### Current layout

```
.
├── lab/                      # top-level package: 1 dict + 1 function + docs
│   ├── __init__.py
│   ├── inventory.py          # DEVICES map, all_devices()
│   └── devices.md
├── src/agent_nettools/       # src-layout package
│   ├── inventory.py          # env creds + lab.inventory → device dicts
│   ├── network_tools.py      # allowlist, SSH, evidence
│   └── llm_analysis.py       # provider selection + analysis
├── mcp_server/
│   ├── server.py             # 8 read-only MCP tools
│   └── README.md
├── scripts/
│   ├── 02_inventory_loader.py
│   ├── 05_claude_network_analysis.py
│   ├── check_device.py
│   ├── final_agent_demo.py
│   └── test_mcp_client.py
├── tests/                    # 5 files, 32 tests
├── Dockerfile / Makefile / README.md / pyproject.toml / requirements.txt
```

### Findings

**S1 (root cause of most awkwardness): the project is not installable.**
`pyproject.toml` has only tool config — no `[project]` section, no build
backend. That is why `PYTHONPATH=src:.` is repeated in **five places**: every
Makefile target, the Dockerfile `ENV`, the pytest config, the MCP client JSON
in `mcp_server/README.md`, and `scripts/test_mcp_client.py`. Any consumer who
forgets the incantation gets `ModuleNotFoundError`.

*Fix:* add `[project]` metadata + setuptools, `pip install -e .` in `make setup`,
and delete every `PYTHONPATH` reference. This one change simplifies the
Makefile, Dockerfile, MCP config, and test client simultaneously.

**S2: two packages at two different layouts for no benefit.** `lab/` is a
top-level package holding one dict and one function; `agent_nettools` is
src-layout. The split forces the dual `PYTHONPATH` root (`src:.`) and gives the
inventory data a different import story than everything else.

*Fix:* move `lab/inventory.py` → `src/agent_nettools/lab.py` (and
`lab/devices.md` → `docs/`). `tests/test_lab_inventory.py` merges into
`tests/test_inventory.py`. One package, one layout, `PYTHONPATH` needs only
`src` — and disappears entirely after S1.

**S3: scripts are workshop leftovers.** The numbering (`02_`, `05_` — with 01,
03, 04 missing) betrays the origin; names are inconsistent; and there is real
overlap:

| Script | Overlaps with | Notes |
|---|---|---|
| `02_inventory_loader.py` | `check_device.py` could do this | prints inventory JSON |
| `05_claude_network_analysis.py` | `final_agent_demo.py` | both = `collect_evidence` + `analyze_evidence`; the demo adds a device-list step and narration; the name says "claude" but it is provider-agnostic |
| `test_mcp_client.py` | — | name looks like a pytest file; it is only *not* collected because `testpaths = ["tests"]` |

*Fix:* consolidate into **one CLI** (`check_device.py` already has the
subcommand shape): `nettools inventory | facts | interfaces | bgp | lldp | isis |
sr | fabric-bgp | analyze | demo | inspect`. With S1, expose it as a console
script (`[project.scripts] nettools = ...`) and the Makefile targets become
one-liners delegating to it. Rename `test_mcp_client.py` → something that
doesn't pattern-match pytest (`mcp_smoke.py`, folded in as `nettools inspect`).

**S4: Makefile repeats itself.** `PYTHONPATH=$(WORKSHOP_PYTHONPATH)` on every
line (dies with S1); nine near-identical check targets (collapse with a pattern
rule or delegate to the single CLI); the variable is still called
`WORKSHOP_PYTHONPATH`.

**S5: lint target vs config mismatch.** `make lint` enumerates paths
(`src scripts mcp_server tests lab/inventory.py`) while `pyproject.toml`
`[tool.ruff] src` lists a different set. After S1/S2, `ruff check .` with the
defaults covers everything — delete both lists.

**S6: caches in the tree.** `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`
are present (gitignored, and `make clean` now removes them) — just noting they
will vanish from view once git exists (O11).

### Target layout (after S1–S3, O10–O12)

```
.
├── docs/
│   ├── REVIEW.md             # this document
│   └── devices.md            # lab topology table
├── src/agent_nettools/
│   ├── __init__.py           # full public surface (fixes O6)
│   ├── lab.py                # device map (was lab/inventory.py)
│   ├── inventory.py
│   ├── network_tools.py
│   ├── llm_analysis.py
│   └── cli.py                # single entry point (was scripts/*)
├── mcp_server/
│   ├── server.py
│   └── README.md
├── tests/                    # 4 files (lab tests merged in)
├── .env.example
├── Dockerfile                # slim: runtime deps only
├── LICENSE
├── Makefile                  # thin wrappers over `nettools ...`
├── README.md                 # matches the code
├── pyproject.toml            # [project] + deps + console script + tool config
└── requirements-dev.txt      # pytest, ruff
```

Result: no `PYTHONPATH` anywhere, one package, one CLI, five fewer files at the
top level, and every doc list either matches the code or is generated from it.

---

## 5. Future capabilities

Grouped by theme, roughly ordered by value-for-effort within each group.

### 5.1 Network depth

- **Structured parsing.** Netmiko supports `use_textfsm=True` (ntc-templates),
  turning raw `show` text into JSON — `show interfaces brief` becomes a list of
  `{interface, status, protocol}`. This is the single highest-leverage upgrade:
  it enables thresholds, diffs, and far better LLM grounding (models stop
  misreading whitespace-formatted tables).
- **Wider allowlist.** Natural read-only additions: `show route summary`,
  `show ospf neighbor`, `show mpls ldp neighbor brief`, `show environment`,
  `show platform`, `show processes cpu`, `show logging last 100`. Each is one
  line in `APPROVED_COMMANDS` + one section in `EVIDENCE_COMMANDS`; the safety
  tests police the additions automatically.
- **Fabric-wide everything.** Generalize `check_fabric_bgp` into
  `check_fabric(check_name)` so any check runs across all 9 devices — combined
  with parallel collection (O3) this makes "state of the whole lab" one call.
- **Baselines & drift detection.** Persist each `collect_evidence` run as
  timestamped JSON (`evidence/PE1/2026-07-28T22:00.json`), then add
  `nettools diff PE1` — "what changed since the last snapshot" (interfaces that
  went down, BGP peers that disappeared). Turns the tool from *inspection* into
  *monitoring*, and gives the LLM before/after context, which is where it shines.
- **Pre/post change validation.** Snapshot → make a change by hand → snapshot →
  auto-compare with pass/fail rules (all IS-IS adjacencies restored, BGP peer
  count unchanged). Classic NetDevOps workflow, small step from drift detection.

### 5.2 LLM & agent capabilities

- **A real agent loop.** Today `final_agent_demo` is a fixed pipeline (collect
  everything → analyze once). With the Anthropic SDK's tool runner, the model
  can call the *same approved tools* iteratively: check BGP → see a down peer →
  decide to check the interface → conclude. The allowlist keeps it exactly as
  safe as it is now; bound it with `max_iterations`. This is the natural
  headline feature of the project.
- **Structured verdicts.** Use structured outputs (JSON schema) so analysis
  returns `{severity, findings[], possible_cause, next_check}` instead of
  markdown. Machine-readable verdicts can gate CI, feed dashboards, or trigger
  notifications; render the markdown from the JSON for humans.
- **Cross-device correlation.** Feed fabric-wide evidence and ask for
  topology-level diagnosis ("PE1's missing prefixes and RR1's stale peer are
  the same incident"). Pairs with fabric-wide collection above.
- **Prompt caching.** The troubleshooting prompt + inventory are static per
  session; with iterative analysis, `cache_control` on the fixed prefix cuts
  cost and latency substantially.
- **Evidence budgeting.** Big `show` outputs will eventually blow the prompt
  budget; add per-section truncation with an explicit `[truncated]` marker so
  the model knows evidence is partial (the prompt already instructs it to say
  what's missing).

### 5.3 MCP surface

- **Expose the workflow tools.** `collect_evidence` and `check_fabric_bgp`…
  exist; adding `collect_lab_evidence` and (optionally) `analyze_lab_device`
  as MCP tools lets any MCP client run the full workflow, not just single checks.
- **MCP resources & prompts.** Expose the inventory and `devices.md` as MCP
  *resources* and the troubleshooting prompt as an MCP *prompt* — clients can
  then ground their own conversations without extra tool calls.
- **Tool annotations.** Mark every tool `readOnlyHint=True` so MCP clients can
  display/act on the safety guarantee (some clients skip confirmation prompts
  for annotated read-only tools).
- **Published container.** The Dockerfile already targets `docker run -i`;
  publishing an image + a copy-paste MCP config block for Claude Desktop/Code
  makes setup zero-clone.

### 5.4 Platform & operations

- **CI.** A GitHub Actions workflow running `ruff check` + `pytest` on push —
  after O11 (git) this is 15 lines and stops regressions like F2 cold.
- **Declarative inventory.** Move the device map to YAML with pydantic
  validation, opening the door to importing a containerlab topology file
  directly — the lab is presumably already defined in one (`.clab/` is in
  `.gitignore`).
- **SSH keys / per-device credentials.** `DEVICE_SSH_KEYFILE` support and an
  optional per-device credential override remove the shared-password
  constraint; Netmiko accepts `use_keys`/`key_file` natively.
- **Scheduled checks + notifications.** Cron a `nettools fabric-bgp` (or drift
  check), and push failures to Telegram/Slack/email. Pairs naturally with
  structured verdicts: only notify at `severity >= warning`.
- **Async collection.** If the lab grows, scrapli-asyncio (IOSXRDriver)
  collects the whole fabric concurrently over one event loop — the `sender`
  seam means the tool layer wouldn't change shape.
- **Observability.** A `--log-file` JSONL trail of every command run (device,
  command, duration, bytes returned) — cheap, and invaluable when the lab
  misbehaves.

### Suggested order

1. **Hygiene first** (O7–O14, S1–S5): git init, packaging, single CLI, doc sync — a day of work that makes everything after it easier.
2. **Structured parsing + fabric-wide checks** — biggest capability-per-line-of-code.
3. **Agent loop with structured verdicts** — the differentiating feature.
4. **Baselines/drift + notifications** — turns it into something that runs unattended.
