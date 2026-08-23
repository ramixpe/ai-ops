# GPT Findings Review — IOS-XR Nettools

**Review date:** 2026-08-23  
**Reviewer:** GitHub Copilot  
**Scope:** Application-level review of the Python package, CLI, MCP surfaces, prompt/LLM paths, safety boundaries, packaging, tests, and documentation drift.

## Executive Summary

The application has a strong core architecture for a safety-sensitive network tool: device commands are allowlisted or rendered from typed templates, raw command buffers are withheld before model egress, MCP tools are sanitized at registration, and the deterministic `investigate` path keeps model prose non-authoritative. The repository also has a notably large and safety-aware test suite.

The main issues found in this pass are not in the deterministic descent logic. They are boundary and release-artifact gaps: one runtime prompt used by the event agent is missing from the packaged fallback, fabric-wide Anthropic analysis bypasses the shared LLM timeout setting, and several model/operator-facing docs now contradict current code. There is also a deployment caveat around admission locks using a relative default directory.

## Findings

### High — Installed-package event agent can fail to load its prompt

`src/agent_nettools/event_agent.py` calls `load_prompt("event_agent", 1)` inside `_dispatch()`, but `prompts/event_agent.v1.txt` has no byte-identical packaged fallback under `src/agent_nettools/data/prompts/`. Forcing `prompt_library.PROMPTS_DIR` to a nonexistent path and calling `load_prompt("event_agent", 1)` raises `PromptNotFoundError`.

The packaging guard is stale: `tests/test_packaging_prompts.py` only enumerates report/correlate prompts, while `pyproject.toml` still documents the packaged prompt set as nine runtime-required files. The source prompt tree currently has ten prompt files; the packaged fallback has nine.

**Impact:** installed wheels or executions outside a source checkout can fail when `event_agent.run_event()` reaches prompt loading. This affects unattended/event-driven usage more than the basic `route-event` CLI, which only routes events and does not call `event_agent`.

**Recommended fix:** package `event_agent.v1.txt`; add it to the packaging drift test; preferably derive the expected packaged prompt set from actual prompt files or all `load_prompt(...)` runtime uses rather than maintaining a hand list.

### High — Fabric-wide Anthropic analysis bypasses the LLM timeout setting

`src/agent_nettools/fabric_analysis.py` constructs `anthropic.Anthropic(api_key=...)` with no `timeout` and calls `_call_anthropic_or_raise(...)` without a per-call timeout. Other LLM entry points use `_resolve_llm_timeout`, honoring `NETTOOLS_LLM_TIMEOUT_SECONDS` and explicit `timeout=` arguments.

I verified the gap with a fake Anthropic module: with `NETTOOLS_LLM_TIMEOUT_SECONDS=17.5`, `analyze_fabric()` constructed the client as `{"api_key": "x"}` and the stream call had no `timeout` key.

**Impact:** a hung Anthropic fabric analysis can ignore the timeout control that users are told protects LLM calls. This is especially relevant because fabric analysis is a broad, potentially expensive path.

**Recommended fix:** thread `_resolve_llm_timeout()` into the fabric Anthropic client or pass an explicit per-call timeout into `_call_anthropic_or_raise`; add a fake-Anthropic regression test mirroring the existing timeout tests in `tests/test_llm_provider.py`.

### Medium — Loki MCP tool description understates current severity coverage

`mcp_server/server.py` tells MCP clients that `get_lab_logs` only sees IOS-XR severities 3 and 4. Current code pins `logs_loki.MEASURED_SEVERITY_AVAILABLE` to `(2, 3, 4, 5)`, and `tests/test_logs_loki.py` asserts that value.

**Impact:** the text a model reads can make it overstate coverage gaps or avoid using valid critical/notice evidence. Tool descriptions are operational input for model tool selection, not just human documentation.

**Recommended fix:** update the `get_lab_logs` docstring and stale test comment in `tests/test_mcp_boundary.py` to match `(2, 3, 4, 5)`.

### Medium — MCP README describes external-source gate behavior incorrectly

`mcp_server/README.md` says an unrecognized `NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES` value stays enabled. Current code defaults unset to enabled, but an explicitly set unrecognized value fails closed because `_mcp_external_sources_allowed()` checks membership in the shared truthy set. `settings.py` also documents `unknown_bool_disables=True` for this variable.

**Impact:** operators reading the README get the wrong mental model for a model-visible egress gate.

**Recommended fix:** update the README section to distinguish unset default-on from malformed explicit values failing closed.

### Medium — MCP README says diff tools persist snapshots, but current MCP code does not

`mcp_server/README.md` says `diff_lab_device_against_latest` / `diff_lab_device_against_golden` collect fresh evidence, save it as the new latest snapshot, and diff it. The implementation explicitly does not persist fresh collections on the MCP surface after B-438; `_diff_against()` returns `snapshot_path: None`.

**Impact:** users may expect MCP diff calls to advance history used by flap detection, but they do not. This can cause confusion when `detect_lab_flaps` does not reflect repeated MCP diff usage.

**Recommended fix:** rewrite the README entry to state that MCP diff compares against a stable saved baseline and does not update snapshot history; keep the CLI/MCP distinction explicit.

### Medium — Admission lock default is relative, weakening cross-process protection across launch contexts

`admission.py` uses filesystem `flock` locks, which is the right primitive for separately spawned `nettools` processes. However, `DEFAULT_ADMISSION_DIR` is the relative path `admission`, resolved against each process's current working directory.

**Impact:** if systemd, n8n, cron, and an interactive shell launch from different working directories without `NETTOOLS_ADMISSION_DIR` set to an absolute shared path, each process gets a separate lock pool and the concurrency cap no longer protects the lab globally.

**Recommended fix:** either default to a stable per-user config/cache path or make the docs/examples set an absolute `NETTOOLS_ADMISSION_DIR`. The systemd example already sets `WorkingDirectory=/opt/nettools`, which helps that path, but the requirement should be explicit for all orchestrators.

### Low — Environment template has stale external-source comments

`.env.example` still says the Loki and Prometheus adapters are "not wired into any rung/check or into the CLI/MCP surface" in their adapter sections, while later lines document the MCP gate and current MCP exposure.

**Impact:** low runtime risk, but confusing for operators enabling/disabling the external-source tools.

**Recommended fix:** update `.env.example` to say they are not descent rungs, but are exposed through MCP external-source tools.

### Low — Local `make test` failed because the venv launcher is stale

`make lint` passed, and `.venv/bin/python -m pytest -q` passed with `3755 passed, 22 skipped`. `make test` failed locally because `.venv/bin/pytest` points at `/home/rami/ai-agent-ops/ios-xr-nettools/.venv312/bin/python`, which does not exist in the current workspace.

**Impact:** local developer experience issue rather than a product defect. CI uses a fresh install and invokes `pytest` from PATH.

**Recommended fix:** recreate the local venv or adjust the Makefile to prefer `$(VENV_BIN)/python -m pytest` when the venv exists.

## Validation Performed

- `make lint`: passed.
- `.venv/bin/python -m pytest -q`: passed, `3755 passed, 22 skipped`.
- `make test`: failed locally because the `.venv/bin/pytest` launcher points at a missing `.venv312` interpreter.
- VS Code diagnostics for reviewed core files: no errors.
- Forced prompt packaged-fallback check for `event_agent.v1`: reproduced `PromptNotFoundError`.
- Fake Anthropic check for fabric analysis: reproduced missing client/per-call timeout.

## Strong Design Observations

- The deterministic `investigate` path is structurally separated from model-written prose.
- MCP sanitization is applied by registration wrappers, so normal tool returns and raised exceptions both pass through the boundary.
- Raw device command buffers are withheld before model egress; parsed free-text fields are quoted with explicit untrusted delimiters.
- Active probes have a separate MCP gate and annotations, with default-off posture for model callers.
- Packaging, evidence storage, path traversal, prompt-file rules, and environment-variable declarations are heavily tested.

## Suggested Fix Order

1. Package `event_agent.v1.txt` and make prompt packaging tests derive from the actual runtime prompt set.
2. Apply `NETTOOLS_LLM_TIMEOUT_SECONDS` to fabric Anthropic analysis and add a timeout regression test.
3. Correct MCP README/docstring drift for Loki severity coverage, external-source gate behavior, and MCP diff persistence.
4. Make the admission lock directory stable across orchestrators or document/set an absolute path in every deployment example.
5. Refresh local venv or Makefile pytest invocation to remove the stale-launcher trap.