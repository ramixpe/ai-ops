"""Drives the MCP server through an actual client session (Phase 8).

``tests/test_safety.py`` only ever asserts certain tool names are *absent*
from the server's public surface -- nothing before this file actually calls a
tool through the protocol. This uses the SDK's in-memory transport (no
subprocess, no stdio pipes -- fast and reliable under pytest) to run the real
low-level server against a real ``ClientSession`` in the same process, and
asserts on the returned result envelope, not just on the server module's
Python attributes.

No ``pytest-asyncio`` (not a project dependency): each test defines a small
``async def`` and drives it with ``asyncio.run()``, the same pattern
``cli.py``'s ``_cmd_inspect`` already uses for its own stdio smoke test.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

import mcp_server.server as server


async def _run_session(coro_body):
    """Wire a real ClientSession to the real server over in-memory streams.

    ``coro_body(session)`` is awaited once the session is initialized; the
    server side runs concurrently in a background task for the duration of
    the ``async with`` block, then is cancelled -- a session is scoped to one
    call, not shared across tests.
    """

    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams

        server_task = asyncio.create_task(
            server.mcp._lowlevel_server.run(
                server_read,
                server_write,
                server.mcp._lowlevel_server.create_initialization_options(),
            )
        )
        try:
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                return await coro_body(session)
        finally:
            server_task.cancel()
            try:
                await server_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - test teardown only.
                pass


def _content_text(result) -> str:
    """Join every text block in a tool/resource/prompt result's content list."""

    return "".join(getattr(block, "text", "") for block in result.content)


def test_list_tools_includes_the_new_phase_8_tools_with_read_only_hints():
    async def body(session: ClientSession):
        return await session.list_tools()

    result = asyncio.run(_run_session(body))
    names = {tool.name for tool in result.tools}

    assert "list_lab_devices" in names
    assert "diff_lab_device_against_latest" in names
    assert "assess_lab_fabric_health" in names
    assert "detect_lab_flaps" in names
    assert "run_command" not in names  # Same guarantee test_safety.py pins.

    if server.READ_ONLY_ANNOTATIONS_SUPPORTED:
        by_name = {tool.name: tool for tool in result.tools}
        assert by_name["list_lab_devices"].annotations.read_only_hint is True
        assert by_name["assess_lab_fabric_health"].annotations.read_only_hint is True


# --------------------------------------------------------------------------- #
# B-473 -- active probes are annotated distinctly from a passive read
# --------------------------------------------------------------------------- #


def test_active_probe_tools_are_annotated_distinctly_from_a_passive_tool():
    """A client that auto-approves purely on `readOnlyHint` cannot otherwise
    tell `get_lab_ping`/`get_lab_traceroute` apart from a passive `show` read
    -- both generate traffic, unlike every other tool this server exposes.

    `read_only_hint` must still be `True` for the two probes: they genuinely
    change no device state, and flipping it would be lying in the other
    direction rather than fixing the signalling gap (B-473). `open_world_hint`
    and `title` are what actually differ from a passive tool's annotations,
    and the docstring-derived description carries the same signal for a
    client that reads only descriptions.
    """

    async def body(session: ClientSession):
        return await session.list_tools()

    result = asyncio.run(_run_session(body))
    by_name = {tool.name: tool for tool in result.tools}
    passive = by_name["list_lab_devices"]

    if not server.READ_ONLY_ANNOTATIONS_SUPPORTED:
        pytest.skip("installed MCP SDK does not support ToolAnnotations")

    assert passive.description is not None
    assert not passive.description.startswith("ACTIVE PROBE: ")

    for probe_name in ("get_lab_ping", "get_lab_traceroute"):
        probe = by_name[probe_name]
        assert probe.annotations.read_only_hint is True, (
            f"{probe_name} changes no device state and must stay readOnlyHint=True"
        )
        assert probe.annotations.title == "ACTIVE PROBE — generates network traffic"
        assert probe.annotations.title != passive.annotations.title
        assert probe.description.startswith("ACTIVE PROBE: sends ICMP/UDP traffic to the target. ")

        if server.ACTIVE_PROBE_ANNOTATIONS_SUPPORTED:
            assert probe.annotations.open_world_hint is True
            assert passive.annotations.open_world_hint is not True


def test_every_other_tool_still_carries_read_only_hint_and_no_open_world_hint():
    """The active-probe annotation (B-473) is additive for exactly two tools,
    not a change to what every other registration gets by default."""

    async def body(session: ClientSession):
        return await session.list_tools()

    result = asyncio.run(_run_session(body))

    if not server.READ_ONLY_ANNOTATIONS_SUPPORTED:
        pytest.skip("installed MCP SDK does not support ToolAnnotations")

    active_probes = {"get_lab_ping", "get_lab_traceroute"}
    checked = 0
    for tool in result.tools:
        if tool.name in active_probes:
            continue
        assert tool.annotations.read_only_hint is True, f"{tool.name} lost readOnlyHint"
        assert tool.annotations.open_world_hint is not True, (
            f"{tool.name} is not an active probe and must not claim open_world_hint"
        )
        checked += 1

    assert checked >= 18, "expected at least 18 non-probe tools to have been checked"


def test_call_list_lab_devices_through_a_real_session_returns_the_envelope():
    """No credentials or fake transport needed: list_devices() never touches
    the network, so this exercises the full protocol round trip (request ->
    server dispatch -> tool execution -> structured response -> client
    deserialization) end to end."""

    async def body(session: ClientSession):
        return await session.call_tool("list_lab_devices", {})

    result = asyncio.run(_run_session(body))

    assert result.is_error is not True
    payload = json.loads(_content_text(result))
    assert payload["status"] == "success"
    assert payload["data"]["devices"][0]["name"] == "P1"
    names = [device["name"] for device in payload["data"]["devices"]]
    assert "PE1" in names and "RR1" in names


def test_call_assess_lab_device_health_through_a_real_session(monkeypatch):
    """Exercises a Phase 8 tool end to end. No credentials in the environment
    on purpose: collect_evidence()'s underlying commands will fail to reach a
    real device, which is fine -- this test asserts on the envelope shape
    (a structured, non-crashing result), not on live device data."""

    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    async def body(session: ClientSession):
        return await session.call_tool("assess_lab_device_health", {"device_name": "PE1"})

    result = asyncio.run(_run_session(body))

    assert result.is_error is not True
    payload = json.loads(_content_text(result))
    assert payload["device"] == "PE1"
    assert "severity" in payload
    assert payload["severity"] in ("ok", "info", "warning", "critical")


# --------------------------------------------------------------------------- #
# B-475/P1-08 -- assess_lab_fabric_health collects devices concurrently
# --------------------------------------------------------------------------- #


def test_assess_lab_fabric_health_collects_devices_concurrently(monkeypatch):
    """This was `{name: collect_evidence(name) for name in names}` -- nine
    sequential logins end to end, one device's connect latency paid nine times
    in series.

    A wall-clock timing assertion here would be flaky under load. Instead this
    proves concurrency deterministically: a `threading.Barrier` sized to
    exactly the device count blocks every fake collection until *all* of them
    have started. That can only complete if the pool schedules every device's
    call before any of them returns -- a reintroduced serial loop would call
    the first device, block forever waiting for the other three to also have
    started (which they never would, being unreached), and this test would
    time out and fail loudly rather than pass quietly.

    Four devices, not the real inventory's nine, so this stays exactly one
    barrier cycle regardless of the pool's own worker cap (`min(8, len(names))`)
    -- with nine devices and eight workers the ninth call is queued behind the
    others and would deadlock a nine-party barrier for a reason that has
    nothing to do with whether the fix works.
    """

    import threading

    fake_names = ["D1", "D2", "D3", "D4"]
    monkeypatch.setattr(
        server,
        "list_devices",
        lambda: {"status": "success", "data": {"devices": [{"name": n} for n in fake_names]}},
    )

    barrier = threading.Barrier(len(fake_names), timeout=5)
    lock = threading.Lock()
    started: list[str] = []

    def fake_collect_evidence(device_name):
        with lock:
            started.append(device_name)
        barrier.wait()  # only returns once every device's call has arrived
        return {"device": device_name, "status": "success", "data": {}, "errors": []}

    monkeypatch.setattr(server, "collect_evidence", fake_collect_evidence)
    monkeypatch.setattr(
        server, "evaluate_fabric", lambda evidence_by_device: {"devices": dict(evidence_by_device)}
    )

    result = server.assess_lab_fabric_health()

    assert sorted(started) == sorted(fake_names), "every device must have been collected"
    assert list(result["devices"]) == fake_names, "inventory order is preserved in the result"


def test_read_lab_inventory_resource_through_a_real_session():
    async def body(session: ClientSession):
        return await session.read_resource("lab://inventory")

    result = asyncio.run(_run_session(body))

    text = "".join(
        content.text for content in result.contents if hasattr(content, "text")
    )
    payload = json.loads(text)
    assert payload["status"] == "success"
    names = [device["name"] for device in payload["data"]["devices"]]
    assert "PE1" in names


def test_read_lab_expected_topology_resource_through_a_real_session():
    async def body(session: ClientSession):
        return await session.read_resource("lab://topology/expected")

    result = asyncio.run(_run_session(body))

    text = "".join(
        content.text for content in result.contents if hasattr(content, "text")
    )
    payload = json.loads(text)
    assert "PE1" in payload


def test_get_troubleshooting_prompt_through_a_real_session():
    async def body(session: ClientSession):
        return await session.get_prompt("troubleshooting_prompt")

    result = asyncio.run(_run_session(body))

    messages = result.messages
    assert messages
    joined = " ".join(
        getattr(message.content, "text", "") for message in messages
    )
    assert "troubleshooting" in joined.lower()


# --------------------------------------------------------------------------- #
# B-438 -- the MCP surface cannot write
# --------------------------------------------------------------------------- #


def test_the_mcp_module_cannot_reach_a_write_function():
    """Structural, not a decorator's promise (B-438, review §3.1).

    `pin_lab_golden_snapshot` was exposed behind `_read_only_tool` and wrote to
    the system's own epistemic ground truth: a model could pin an outage state
    as golden, after which drift comparison suppresses that fault indefinitely.
    `save_lab_snapshot` and both diff tools appended to the snapshot history
    that `detect_lab_flaps` reads.

    A decorator named `_read_only_tool` asserted the property and enforced
    nothing. **This asserts it where it cannot be worked around**: if the module
    does not import a write function, no tool it exposes can call one -- the
    same containment argument as `prompt_library` never holding device text.
    """

    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent / "mcp_server" / "server.py"
    ).read_text(encoding="utf-8")

    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)

    forbidden = {
        "save_snapshot",
        "save_golden_snapshot",
        "prune_snapshots",
        "update_expected_in_yaml",
    }
    assert not (imported & forbidden), (
        f"mcp_server/server.py imports {sorted(imported & forbidden)}; a model-visible "
        f"surface must not be able to reach a persistent write"
    )


def test_the_two_writing_tools_are_gone_from_the_surface():
    """Named explicitly, so re-adding one is a deliberate act that fails a test
    rather than a plausible-looking addition."""

    from mcp_server import server

    for gone in ("save_lab_snapshot", "pin_lab_golden_snapshot"):
        assert not hasattr(server, gone), (
            f"{gone} is a persistent write and must not be on the model-visible "
            f"surface (B-438). Pinning a golden snapshot is a human action: "
            f"`nettools baseline pin DEVICE`."
        )


def test_pinning_a_golden_snapshot_is_still_possible_for_a_human():
    """The companion. Removing the capability from the model must not remove it
    from the operator, or the fix has broken the feature instead of scoping it.
    """

    from agent_nettools import cli

    parser = cli.build_parser()
    action = next(a for a in parser._subparsers._group_actions)  # noqa: SLF001
    assert "baseline" in action.choices, "the human path must survive"


# --------------------------------------------------------------------------- #
# stdio transport hygiene
# --------------------------------------------------------------------------- #


def test_protect_stdio_redirects_a_hostile_launchers_stdout_handler():
    """Over stdio, stdout carries JSON-RPC and nothing else.

    Nothing in this package writes to stdout. **But the process is not only this
    package** -- a host launcher we do not control can call
    `logging.basicConfig(stream=sys.stdout)` before this module is imported, and
    one stray line breaks the framing so the client sees a protocol error rather
    than a log message.

    So the server redirects rather than assuming, and this asserts the
    redirection on a deliberately hostile configuration.
    """

    import io
    import logging
    import sys

    from mcp_server.server import protect_stdio

    original = logging.getLogger().handlers[:]
    try:
        logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
        assert any(
            getattr(h, "stream", None) is sys.stdout for h in logging.getLogger().handlers
        ), "the hostile setup must actually point at stdout, or this is vacuous"

        changed = protect_stdio()

        assert changed, "it reports what it moved"
        assert not any(
            getattr(h, "stream", None) is sys.stdout for h in logging.getLogger().handlers
        )

        captured = io.StringIO()
        real, sys.stdout = sys.stdout, captured
        try:
            logging.getLogger("anything").warning("must not reach stdout")
        finally:
            sys.stdout = real
        assert captured.getvalue() == ""
    finally:
        logging.getLogger().handlers[:] = original


def test_protect_stdio_leaves_an_unconfigured_root_logger_safe():
    """A later `basicConfig()` must not be able to install a stdout handler.

    With an explicit stderr handler present, `basicConfig` is a no-op -- so the
    defence survives a library that configures logging after startup, not only
    one that did it before.
    """

    import logging
    import sys

    from mcp_server.server import protect_stdio

    original = logging.getLogger().handlers[:]
    try:
        logging.getLogger().handlers[:] = []
        protect_stdio()

        logging.basicConfig(stream=sys.stdout, level=logging.INFO)

        assert not any(
            getattr(h, "stream", None) is sys.stdout for h in logging.getLogger().handlers
        )
    finally:
        logging.getLogger().handlers[:] = original


def test_importing_the_server_writes_nothing_to_stdout():
    """The import-time half: `load_dotenv` and 21 tool registrations, silent."""

    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import mcp_server.server"],
        capture_output=True, text=True, check=True,
    )

    assert result.stdout == "", f"stdout polluted at import: {result.stdout!r}"


def test_the_investigate_tool_advertises_every_implemented_flow():
    """The tool description is the ONLY thing a model reads when choosing a
    flow, so a flow missing from it is a flow that will never be selected.

    Measured 2026-08-19: the description said "``bgp_session`` (default) or
    ``interface``" long after B-107 and B-109 shipped `isis_adjacency` and
    `ldp_session`. Both were fully built, tested and reachable from the CLI,
    and structurally invisible to every model on the MCP surface -- the same
    shape as OBS-187, where a refusal existed and no operator could reach it.
    A capability the surface does not name does not exist to the caller.
    """

    from agent_nettools import flows

    doc = server.investigate_lab_session.__doc__ or ""
    implemented = sorted(n for n, f in flows.FLOWS.items() if f is not None)

    missing = [name for name in implemented if f"``{name}``" not in doc]
    assert not missing, (
        f"flows implemented but not advertised to a model: {missing}. "
        "Add them to investigate_lab_session's docstring -- a model cannot "
        "select a flow it has never been told exists."
    )


def test_the_investigate_tool_names_the_refused_flow_and_its_replacement():
    """Anti-vacuity companion. The test above is satisfied by listing more
    names; this one pins that the REFUSED flow is described as refused, with
    its replacement named. Otherwise a model that reasons "device_health is
    not listed, so I'll try it" gets an error instead of an answer.
    """

    from agent_nettools import flows

    doc = server.investigate_lab_session.__doc__ or ""
    for refused in flows.REFUSED_OBJECT_TYPES:
        assert f"``{refused}``" in doc, f"{refused} not mentioned"
    assert "assess_lab_device_health" in doc
