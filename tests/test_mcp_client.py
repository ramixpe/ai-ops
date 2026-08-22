"""`agent_nettools.mcp_client` -- the extracted, reusable MCP transport seam.

Three families of test here:

* **Lazy import.** `mcp` is a required dependency of this package
  (`pyproject.toml`), unlike `neo4j`/`pynetbox`, so "genuinely absent in this
  venv" (the fix `test_graph.py`/`test_netbox.py` use for those) is not
  available -- absence is SIMULATED via `sys.modules["mcp"] = None`, the same
  fix `test_graph.py`'s `write_graph` test already uses for the identical
  reason (its own docstring: "Absence is SIMULATED rather than relied on").
* **`child_server_env`** -- pure, in-process, no subprocess: what it returns
  given a hostile operator environment.
* **`McpToolset` end to end** -- a REAL subprocess (`python -m
  mcp_server.server`), the same transport `_cmd_inspect` uses. Every call
  here is either `list_lab_devices` (a pure inventory read: no SSH, no
  credentials) or a probe call refused BEFORE it reaches a device -- so this
  file needs no lab and no credentials, the same discipline
  `tests/test_mcp_server.py` already follows for its in-memory-transport
  tests.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from agent_nettools.mcp_client import (
    McpToolset,
    ToolCallResult,
    ToolDescriptor,
    _measure_truncated_chars,
    child_server_env,
)

# --------------------------------------------------------------------------- #
# Lazy import
# --------------------------------------------------------------------------- #


def test_module_imports_with_mcp_simulated_absent():
    """A fresh subprocess where `sys.modules["mcp"] = None` is injected
    before the import makes any subsequent `import mcp` (or `from mcp...
    import ...`) raise ImportError immediately -- the same failure a
    genuinely-missing package produces. `agent_nettools.mcp_client` itself
    must still import cleanly under that condition: it imports `mcp` only
    inside `McpToolset.__enter__`, never at module level.
    """

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['mcp'] = None; import agent_nettools.mcp_client",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_module_imports_without_ever_touching_mcp():
    """A stronger version of the test above: not just "does not raise", but
    "does not put `mcp` in sys.modules at all" -- the same second assertion
    `test_graph.py`/`test_netbox.py` make for their own optional imports."""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agent_nettools.mcp_client, sys; assert 'mcp' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_using_the_toolset_without_mcp_fails_at_enter_not_at_import(monkeypatch):
    """The ImportError must happen inside `McpToolset.__enter__`, never at
    `import agent_nettools.mcp_client` -- the in-process mirror of the two
    subprocess tests above, simulating absence via `sys.modules["mcp"] = None`
    the same way `test_graph.py`'s `write_graph` test does for `neo4j`."""

    monkeypatch.setitem(sys.modules, "mcp", None)

    toolset = McpToolset()
    with pytest.raises(ImportError):
        toolset.__enter__()


# --------------------------------------------------------------------------- #
# child_server_env -- pure function, no subprocess needed for these.
# --------------------------------------------------------------------------- #


def test_child_server_env_defaults_to_the_safe_posture():
    env = child_server_env()

    assert env["NETTOOLS_MCP_SURFACE"] == "staged"
    assert env["NETTOOLS_MCP_ALLOW_ACTIVE_PROBES"] == "0"
    assert env["NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES"] == "1"


def test_child_server_env_forces_active_probes_off_even_against_a_hostile_operator_env(
    monkeypatch,
):
    """Positive control (OBS-181): set the operator's own environment to the
    OPPOSITE of what `child_server_env`'s default claims to force, and prove
    the forced value still wins. A version of `child_server_env` that merely
    *defaulted* (rather than *forced*) this value would fail this test --
    the operator's "1" would leak through untouched."""

    monkeypatch.setenv("NETTOOLS_MCP_ALLOW_ACTIVE_PROBES", "1")

    env = child_server_env()

    assert env["NETTOOLS_MCP_ALLOW_ACTIVE_PROBES"] == "0"


def test_child_server_env_forces_surface_staged_even_against_a_hostile_operator_env(
    monkeypatch,
):
    """Same positive control, for the surface flag: an operator's own
    `NETTOOLS_MCP_SURFACE=classic` must not leak into a caller that asked
    `child_server_env` for the narrower default."""

    monkeypatch.setenv("NETTOOLS_MCP_SURFACE", "classic")

    env = child_server_env()

    assert env["NETTOOLS_MCP_SURFACE"] == "staged"


def test_child_server_env_allow_active_probes_true_flips_the_forced_value():
    env = child_server_env(allow_active_probes=True)

    assert env["NETTOOLS_MCP_ALLOW_ACTIVE_PROBES"] == "1"


def test_child_server_env_allow_external_sources_false_flips_the_forced_value():
    env = child_server_env(allow_external_sources=False)

    assert env["NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES"] == "0"


def test_child_server_env_surface_classic_is_accepted_explicitly():
    env = child_server_env(surface="classic")

    assert env["NETTOOLS_MCP_SURFACE"] == "classic"


def test_child_server_env_refuses_an_unrecognized_surface():
    """Validated at the edge rather than silently forwarded for the
    server's own `_select_surface` fail-closed logic to catch downstream --
    see `child_server_env`'s own docstring."""

    with pytest.raises(ValueError):
        child_server_env(surface="staged ")  # trailing space -- not the exact literal


def test_child_server_env_forwards_nettools_and_device_vars(monkeypatch):
    monkeypatch.setenv("NETTOOLS_INVENTORY", "/tmp/custom-lab.yaml")
    monkeypatch.setenv("DEVICE_USERNAME", "clab")
    monkeypatch.delenv("SOME_UNRELATED_VAR", raising=False)

    env = child_server_env()

    assert env["NETTOOLS_INVENTORY"] == "/tmp/custom-lab.yaml"
    assert env["DEVICE_USERNAME"] == "clab"


def test_child_server_env_does_not_forward_unrelated_vars(monkeypatch):
    monkeypatch.setenv("SOME_UNRELATED_VAR", "leaked?")

    env = child_server_env()

    assert "SOME_UNRELATED_VAR" not in env


# --------------------------------------------------------------------------- #
# _measure_truncated_chars -- absence is never zero.
# --------------------------------------------------------------------------- #


def test_truncated_chars_is_a_measured_zero_when_nothing_was_withheld():
    text = '{"tool": "list_lab_devices", "status": "success", "data": {"devices": []}}'

    assert _measure_truncated_chars(text) == 0


def test_truncated_chars_sums_every_commands_withheld_entry():
    text = (
        '{"data": {"commands_withheld": {'
        '"show bgp summary": {"chars": 748, "lines": 21}, '
        '"show interfaces": {"chars": 252, "lines": 10}'
        '}}}'
    )

    assert _measure_truncated_chars(text) == 1000


def test_truncated_chars_finds_commands_withheld_nested_under_a_fabric_wide_result():
    """A fabric-wide envelope nests one device envelope per device -- the
    walk must be recursive, not only look at the top level."""

    text = (
        '{"devices": {"PE1": {"data": {"commands_withheld": '
        '{"show bgp summary": {"chars": 100, "lines": 3}}}}}}'
    )

    assert _measure_truncated_chars(text) == 100


def test_truncated_chars_is_none_when_something_was_withheld_with_unknown_size():
    """`unaccounted_lines_withheld` records a LINE count, not characters --
    real withholding this function cannot size. Reporting `0` here would
    read as "nothing was withheld", which is false."""

    text = '{"data": {"parsed": {"meta": {"unaccounted_lines_withheld": 5}}}}'

    assert _measure_truncated_chars(text) is None


def test_truncated_chars_measured_zero_survives_a_zero_valued_unaccounted_marker():
    """The real server shape (confirmed against a live call): every parsed
    envelope carries `unaccounted_lines_withheld`, almost always `0`. A `0`
    under that key is a genuinely measured absence, not "unknown size" --
    it must not force the whole result to `None`."""

    text = (
        '{"data": {"commands_withheld": {"show bgp summary": {"chars": 748, "lines": 21}}, '
        '"parsed": {"meta": {"unaccounted_lines_withheld": 0}}}}'
    )

    assert _measure_truncated_chars(text) == 748


def test_truncated_chars_is_a_known_lower_bound_not_none_when_mixed_with_unmeasured_withholding():
    """`commands_withheld` gives a real, positive, known char count; a
    SEPARATE `_withheld` marker of unknown size also fires. The known sum is
    an honest lower bound (never a false claim of "zero withheld"), so it is
    returned as-is rather than also collapsing to `None`."""

    text = (
        '{"data": {"commands_withheld": {"show bgp summary": {"chars": 748, "lines": 21}}, '
        '"parsed": {"meta": {"unaccounted_lines_withheld": 5}}}}'
    )

    assert _measure_truncated_chars(text) == 748


def test_truncated_chars_is_none_for_text_that_never_went_through_sanitize():
    """An SDK/protocol-level message like "Unknown tool: ..." is not JSON at
    all -- it never passed through `boundary.sanitize`, so there is nothing
    to measure absence against."""

    assert _measure_truncated_chars("Unknown tool: list_lab_devices") is None


# --------------------------------------------------------------------------- #
# McpToolset end to end -- a real subprocess, no lab or credentials needed
# (every call below is either lab-free or refused before touching a device).
# --------------------------------------------------------------------------- #


def test_toolset_enter_exit_with_no_calls_does_not_raise():
    """Regression pin: an earlier version of `McpToolset` crashed on
    `__exit__` with anyio's "different task" RuntimeError because opening
    and closing `stdio_client`/`ClientSession` were split across two
    separate `run_until_complete` calls (two different asyncio Tasks). This
    is the minimal reproduction -- enter, then immediately exit, no calls in
    between."""

    with McpToolset():
        pass


def test_toolset_lists_the_classic_surface_by_default():
    with McpToolset() as toolset:
        tools = toolset.list_tools()

    assert isinstance(tools, tuple)
    names = {t.name for t in tools}
    assert "list_lab_devices" in names
    assert "explore_lab" not in names  # staged-only
    assert all(isinstance(t, ToolDescriptor) for t in tools)
    assert all(isinstance(t.input_schema, dict) for t in tools)


def test_env_overrides_reach_the_spawned_servers_surface_selection():
    """The whole point of `env_overrides`/`child_server_env` is that they
    change what the CHILD PROCESS does -- proved here by actually spawning
    it and listing tools, not by reading `server.py`'s source and trusting
    it. If `McpToolset` silently dropped `env_overrides` on the floor, both
    assertions below would see the SAME (classic, unset-default) tool set."""

    with McpToolset(env_overrides=child_server_env(surface="staged")) as toolset:
        staged_names = {t.name for t in toolset.list_tools()}

    with McpToolset(env_overrides=child_server_env(surface="classic")) as toolset:
        classic_names = {t.name for t in toolset.list_tools()}

    assert staged_names == {
        "explore_lab", "check_lab", "lookup_lab",
        "investigate_lab", "history_lab", "probe_lab",
    }
    assert "list_lab_devices" in classic_names
    assert "explore_lab" not in classic_names


def test_env_overrides_reach_the_active_probe_gate_even_against_a_hostile_operator_env(
    monkeypatch,
):
    """Positive control, end to end: set the OPERATOR's environment (what
    `_forwarded_operator_env` would otherwise forward unmodified) to allow
    active probes, then prove `child_server_env`'s forced "0" still reaches
    the spawned child and the call is refused BEFORE any device is touched
    -- so this needs no lab and no credentials. If `env_overrides` were
    silently ignored by `McpToolset`, the operator's "1" would reach the
    child unopposed and this specific refusal text would not appear.
    """

    monkeypatch.setenv("NETTOOLS_MCP_ALLOW_ACTIVE_PROBES", "1")

    with McpToolset(env_overrides=child_server_env(surface="staged")) as toolset:
        result = toolset.call_tool(
            "probe_lab",
            {"device_name": "PE1", "kind": "ping", "address": "10.255.0.31"},
            timeout_s=15.0,
        )

    # NOTE: this is a registered tool returning its own status:"error"
    # envelope, not an MCP protocol-level failure -- `is_error` (CallToolResult
    # .isError) stays False here; the refusal is classified INSIDE the JSON
    # body (`_active_probes_refused` -> `boundary.sanitize` -> `_classify_errors`).
    # Measured directly against this server, not assumed: an actually-unknown
    # tool name (see the "Unknown tool" test below) DOES set is_error True,
    # so the two failure classes are genuinely distinguishable at this layer.
    assert result.is_error is False
    assert "active probes" in result.text.lower()
    assert "disabled" in result.text.lower()
    assert result.truncated_chars == 0  # refused before any device text existed to withhold


def test_unknown_tool_name_is_an_mcp_protocol_level_error():
    """Contrast case for the note above: a tool name the server never
    registered at all fails at the PROTOCOL level (`is_error=True`), unlike
    a registered tool's own classified `status:"error"` envelope."""

    with McpToolset() as toolset:
        result = toolset.call_tool("this_tool_does_not_exist", {}, timeout_s=10.0)

    assert result.is_error is True
    assert "this_tool_does_not_exist" in result.text


def test_call_tool_result_carries_the_arguments_as_dispatched():
    arguments = {"device_name": "PE1", "kind": "ping", "address": "10.255.0.31"}

    with McpToolset(env_overrides=child_server_env(surface="staged")) as toolset:
        result = toolset.call_tool("probe_lab", arguments, timeout_s=15.0)

    assert isinstance(result, ToolCallResult)
    assert result.name == "probe_lab"
    assert result.arguments == arguments
    assert result.arguments is not arguments  # a copy, not the caller's own dict
    assert result.elapsed_s >= 0.0


def test_toolset_supports_multiple_calls_in_one_session():
    """The whole reason `list_tools`/`call_tool` are ordinary methods rather
    than one-shot context managers: several calls in the same `with` block,
    against the same spawned server."""

    with McpToolset() as toolset:
        tools = toolset.list_tools()
        first = toolset.call_tool("list_lab_devices", {}, timeout_s=10.0)
        second = toolset.call_tool("list_lab_devices", {}, timeout_s=10.0)

    assert len(tools) > 0
    assert first.is_error is False
    assert second.is_error is False
    assert first.truncated_chars == 0
    assert second.truncated_chars == 0


def test_list_resources_and_list_prompts_are_populated():
    with McpToolset() as toolset:
        resources = toolset.list_resources()
        prompts = toolset.list_prompts()

    assert any(r.uri == "lab://inventory" for r in resources)
    assert any(p.name == "troubleshooting_prompt" for p in prompts)
