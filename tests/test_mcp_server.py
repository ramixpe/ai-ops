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
