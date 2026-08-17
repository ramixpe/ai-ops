# Repository inventory

**740 tracked files at `098da8f`.** Every file, what it is for, the last commit that
touched it, and whether anything references it.

> **This is evidence, not a proposal.** Nothing here recommends deleting, merging or
> tidying anything, and nothing should be acted on before publication. It exists because
> *"not needed"* is a judgement made without the artifact, and this build has been wrong
> about exactly that six times.
>
> **Read the "referenced by" column carefully.** A test file that nothing references is
> normal — pytest discovers by filename and nothing imports it. A payload that nothing
> references is normal — it is data. **Zero references means "no other file names this
> one", which is a fact, not a verdict**, and §7 lists every zero with why.

| | |
|---|---|
| Tracked files | **740** |
| Captured fixtures | 563 (`tests/fixtures/cisco_xr/…`) — summarised in §5, not enumerated |
| Everything else | 177 |
| Reference index built over | all 177 non-fixture files, by filename and module name |

---

## 1. Root — 10 files

| File | Purpose | Last commit | Refs |
|---|---|---|---|
| `README.md` | The front door. Opens with a runnable zero-dependency demo that CI machine-checks | `1beb612` 08-17 | 29 |
| `CLAUDE.md` | Agent instructions and the rules that must not break. Was 941 lines; the architecture half is now `docs/design/architecture.md` | `1beb612` 08-17 | 22 |
| `CONTRIBUTING.md` | The seven non-negotiables with the incident behind each, and honest provenance | `1beb612` 08-17 | 4 |
| `LICENSE` | MIT | `c9806f3` 07-28 | 4 |
| `pyproject.toml` | Package metadata, deps, ruff and pytest config. `authors` deliberately absent | `08adfb9` 08-17 | 9 |
| `Makefile` | `setup`, `test`, `lint`, and thin wrappers over the `nettools` script | `85191f8` 07-29 | 3 |
| `.env.example` | The full environment surface, annotated. **No secrets** — names only | `808f9b4` 08-17 | 10 |
| `.gitignore` | Annotated, because a blanket rule here cost round 7 its samples (OBS-131) | `d50fa60` 08-17 | 11 |
| `Dockerfile` | Container build, core package without the `llm` extra | `c9806f3` 07-28 | 2 |
| `.dockerignore` | Build context exclusions | `a41c48a` 07-28 | 2 |

**Note on the two July files.** `Dockerfile` and `.dockerignore` have not been touched
since 2026-07-28 and predate the whole investigation layer. **They are not verified against
the current package** — nothing in CI builds the image. That is a gap, recorded here rather
than fixed, and it is the only place in this inventory where "last touched" points at a
real question.

---

## 2. `src/agent_nettools/` — 35 modules

Ordered by how much of the tree depends on them, which is the useful ordering for someone
deciding what they can safely not read.

### The safety boundary and what resolves before it

| Module | Purpose | Last commit | Refs |
|---|---|---|---|
| `lab.py` | The credential-free read path onto the inventory. **Why the allowlist can be checked before credentials load** | `11cf00d` 07-29 | 91 |
| `platforms.py` | The per-platform allowlist and intent table. **FROZEN** | `4a30b91` 07-29 | 31 |
| `templates.py` | Validated parameterised commands, five layered defences. **FROZEN** | `4a30b91` 07-29 | 42 |
| `inventory_model.py` | Pydantic schema for `lab.yaml`, `extra="forbid"` throughout | `9b2baa6` 08-16 | 24 |
| `inventory.py` | Joins the parsed inventory with credentials. The only place secrets are touched | `85191f8` 07-29 | 66 |
| `credential_resolver.py` | Pluggable credential providers (`env`, `file`) | `85191f8` 07-29 | 8 |
| `network_tools.py` | The allowlist enforcement point, SSH transport, evidence collection, snapshots, diffing | `722e9b0` 08-17 | 46 |

### The investigation layer

| Module | Purpose | Last commit | Refs |
|---|---|---|---|
| `template_parsers.py` | TTP parsers plus §0.10 line accounting | `2759d6f` 08-16 | 26 |
| `checks.py` | Pure predicates over parsed records, **and** the health rule tables since B-403 | `75b1582` 08-17 | 64 |
| `flows.py` | The ladder: `Rung`, `DeviceScope`, `SubjectRule`, `Aggregation` | `7236f1b` 08-17 | 36 |
| `descent.py` | `run_descent()`. **No model call anywhere in this module** | `503b2ac` 08-17 | 79 |
| `epoch.py` | One observation window; re-reads symptom and cause. B-436 | `503b2ac` 08-17 | 25 |
| `coverage.py` | What a source could have told you; `gaps()` is why a negative may not be assertable | `a5ae7e2` 08-16 | 62 |
| `log_window.py` | Shapes a log window by *attribution*, never by content | `a5ae7e2` 08-16 | 22 |
| `prompt_library.py` | Versioned prompts. **Structurally cannot receive device text** | `88a87eb` 08-16 | 22 |
| `grounding.py` | The gate: citation integrity, chain coverage, timeline, absence, **identifier containment** | `3b1da03` 08-17 | 55 |
| `render.py` | The authoritative report, generated from typed fields. B-439 | `ee733a2` 08-17 | 22 |
| `investigation.py` | `investigate()` — the runner | `484fe3d` 08-17 | 62 |
| `interface_kind.py` | One declared interface taxonomy, replacing three string-prefix checks. B-431 | `503b2ac` 08-17 | 10 |

### Everything else

| Module | Purpose | Last commit | Refs |
|---|---|---|---|
| `parsers.py` | Raw command output → records, per intent. Strict: no silent empty success | `8a8f695` 08-17 | 32 |
| `health.py` | Re-export shim since B-403 merged the rules into `checks.py` | `40cc184` 08-16 | 58 |
| `topology.py` | Derived expected counts and the anomaly report. **B-435 landed here** | `945bc94` 08-17 | 38 |
| `fixtures.py` | Capture and replay. `scrub_output` and its documented limits | `a79295f` 08-16 | 69 |
| `cli.py` | The `nettools` entry point, exit-code scheme, `--notify` | `808f9b4` 08-17 | 21 |
| `output.py` | `json`/`table`/`summary` rendering. Never invents or softens | `79c6777` 08-17 | 96 |
| `notifier.py` | **T-035.** Outbound report relay, Telegram. Egress bounded by signature | `808f9b4` 08-17 | 9 |
| `llm_analysis.py` | Provider layer: Anthropic, OpenAI, Ollama | `27dbcc0` 08-16 | 25 |
| `agent_loop.py` | Bounded tool-calling loop. **The explicitly untrusted path** | `0611dff` 08-16 | 16 |
| `fabric_analysis.py` | Fabric-wide correlation over budgeted evidence | `0611dff` 08-16 | 11 |
| `evidence_budget.py` | Character budgets, middle-truncation with an explicit marker | `3cfcd50` 07-29 | 9 |
| `evidence_store.py` | File and SQLite snapshot backends | `11cf00d` 07-29 | 9 |
| `metrics.py` | Thread-safe counters; JSON and hand-written Prometheus text | `484fe3d` 08-17 | 20 |
| `normalize.py` | Volatile-field masking for text diffs | `2417a9c` 07-29 | 7 |
| `devices_doc.py` | Renders `docs/devices.md` from the inventory | `c5c519a` 07-29 | 5 |
| `__init__.py` | Package marker | `85191f8` 07-29 | 14 |

---

## 3. `mcp_server/` — 4 files

| File | Purpose | Last commit | Refs |
|---|---|---|---|
| `server.py` | 21 read-only tools, 2 resources, 1 prompt. Every tool sanitised at registration | `6637368` 08-17 | 12 |
| `boundary.py` | **B-458.** Rebuilds safe payloads rather than truncating unsafe ones | `484fe3d` 08-17 | 6 |
| `README.md` | The exposed surface, pinned to the code by `test_mcp_readme_lists_exactly_the_exposed_tools` | `f3007fc` 08-17 | 30 |
| `__init__.py` | Package marker | `c9806f3` 07-28 | 3 |

---

## 4. `docs/` — 37 files

Mapped in full by `docs/README.md`, which was regenerated from the tree on 2026-08-17
after being found to list 15 of 32 and point at two files that did not exist.

| Area | Files | Note |
|---|---|---|
| `docs/design/` | 9 | Reference. `architecture.md` was split out of `CLAUDE.md` on 08-17 |
| `docs/build/` | 21 | The build's own record, including this file |
| `docs/` root | 7 | `devices.md` (generated), `REVIEW.md`, three external reviews, the drawio source |

**The three largest, and none of them should be tidied.**

| File | Lines | Why it is that size |
|---|---|---|
| `FINDINGS.md` | ~3,900 | Append-only. **Its value is the order things were learned in**, wrong turns included. `docs/README.md` says so explicitly and H4 declined to narrativise it |
| `BUILD-PLAN.md` | ~1,600 | Part 0 is binding governance; the rest is the 35-task plan with statuses edited in place |
| `BACKLOG.md` | ~350 | 98 items, each with a reconciled state and its evidence |

---

## 5. `tests/` — 609 files

| Group | Count | Note |
|---|---|---|
| Captured fixtures | **563** | `cisco_xr/<device>/<label>/<command>.txt`. 9 devices × 4 labels. Audited 2026-08-17: no secrets, no public addresses, no serials |
| Test modules | 45 | |
| Helpers and cases | 1 | `helpers.py` — deliberately not `test_*` so pytest does not collect it |

**Fixtures are summarised rather than enumerated** because 563 rows of
`show-bgp-summary.txt` is not evidence anyone can use. `tests/fixtures/README.md` documents
what each label means and how to reproduce it; the content audit is in
`docs/build/FINDINGS.md` and the commit for `760c37b`.

---

## 6. `scripts/`, `prompts/`, and the rest

| File | Purpose | Last commit | Refs |
|---|---|---|---|
| `scripts/preflight.sh` | Pre-lab-window checks, non-zero on anything unexpected | new | 1 |
| `scripts/archive.sh` | Payload → `evidence-archive/`, verified with `git ls-files` | new | 2 |
| `scripts/round5_sampler.py`, `round7_sampler.py` | Round samplers. Read-only; they never touch a fault | `d50fa60` 08-17 | 1, 3 |
| `scripts/probe_minimax.py` | T-002's provider contract checks | `f1ecb55` 08-15 | 3 |
| `scripts/README.md` | What is here, and **why the injector deliberately is not** | new | 30 |
| `prompts/report.v1.txt`, `correlate.v{1,2,3}.txt` | Versioned prompts. **Superseded versions stay in the tree** | 08-16 | 3–10 |
| `prompts/tests/cases/*.json` | Golden cases, drawn from fixtures, never hand-written | 08-16 | 1–3 |
| `inventory/lab.yaml` | The declarative inventory. Credential *names* only, never values | `945bc94` 08-17 | 29 |
| `.github/workflows/ci.yml` | `ruff` + `pytest`, and the `offline-demo` job that machine-checks the README | `635c2e1` 08-16 | 3 |
| `evidence-archive/` | 29 files: rounds 5, 7, 8 payloads + `round8.py`. Excluded from ruff — a formatter must not rewrite an artefact | 08-17 | see §7 |

---

## 7. Files nothing references — 27, and why each is fine

**None of these is a candidate for deletion.** The column measures textual reference, and
three whole categories are unreferenced by design.

| Category | Count | Why zero is correct |
|---|---|---|
| **Test modules** | 12 | pytest discovers by filename. Nothing imports a test module and nothing should. `test_agent_loop`, `test_epoch`, `test_evidence_budget`, `test_evidence_store`, `test_fabric_analysis`, `test_interface_kind`, `test_inventory_model`, `test_metrics`, `test_output`, `test_parsers`, `test_render`, `test_topology` |
| **Round 5 payloads** | 13 | `probe-*.json` are data. `ROUND-5.md` cites the directory, not each probe — which is exactly right, and the directory *is* referenced |
| **`prompts/tests/cases/.gitkeep`** | 1 | Keeps an otherwise-empty directory. Zero references is its entire function |
| **This report's sibling** | 1 | `VERIFICATION.md`, written the same hour and not yet linked from anywhere |

**The one that would be a real finding if it were here, and is not:** a *source* module with
zero references. There are none. Every file in `src/` is referenced by at least 5 others,
and the lowest are `devices_doc.py` (5) and `normalize.py` (7) — both narrow utilities with
exactly the reach you would expect.

---

## 8. What this inventory does not tell you

- **Reference count is not importance.** `output.py` shows 96 references and is a rendering
  helper; `descent.py` shows 79 and is the module the entire architectural claim rests on.
  The count measures how often a *name* appears in text, and common words score high.
- **It does not detect dead code inside a file.** A module can be referenced everywhere and
  contain a function nothing calls. That needs a different tool and a different question.
- **"Last commit" is not "last verified".** `Dockerfile` was last touched 2026-07-28 and has
  never been built in CI. `platforms.py` was last touched 2026-07-29 and is verified on
  every single run, because it is frozen and hash-compared.
