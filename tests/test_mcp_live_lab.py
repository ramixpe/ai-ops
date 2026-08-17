"""Every MCP tool, once, against the live lab, through the real protocol.

    NETTOOLS_LIVE_LAB=1 pytest -m live_lab tests/test_mcp_live_lab.py

Why this file exists
---------------------
`epoch.py`, `render.py`, `investigation.py` and `network_tools.py` all changed
underneath this server in one session — a new collection contract, a new report
renderer, a new combined transport runner, a new boundary — and **nothing proved
the MCP path still runs.** Every existing MCP test either replays fixtures or
drives one tool; none of them opens a socket, and the unit suite would stay
green through a signature change that breaks every live call.

So this is the coverage that cannot be faked: each tool called once, over a real
`ClientSession`, against real devices, asserting only that it returns a
well-formed result and that **nothing raw comes back**.

What it deliberately does not assert
-------------------------------------
**Not the fabric's state.** A smoke test that asserted `bgp` was healthy would
fail whenever someone was running an injection round, and would be read as a
tool failure. It asserts the *shape* of what comes back and the boundary
property — the two things that are about this code rather than about the lab.

Ordering note: the tools run in one session and each opens SSH. On this fabric
consecutive logins cost ~8 s (B-455), so the whole file takes a few minutes.
That is a property of the lab, not of the test.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

pytestmark = pytest.mark.live_lab

if not os.getenv("NETTOOLS_LIVE_LAB"):
    pytest.skip(
        "live lab tests need NETTOOLS_LIVE_LAB=1 and a reachable fabric",
        allow_module_level=True,
    )

from mcp import ClientSession  # noqa: E402
from mcp.shared.memory import create_client_server_memory_streams  # noqa: E402

from mcp_server import server  # noqa: E402
from mcp_server.boundary import RAW_TEXT_KEYS  # noqa: E402

DEVICE = "RR1"
SUBJECT = "10.255.0.12"

#: Every tool, with arguments that are valid on this fabric. A tool missing from
#: here is caught by `test_every_registered_tool_is_smoke_tested` rather than
#: silently going untested -- §0.12, the rule that a table quietly covering a
#: shrinking fraction of its domain passes forever.
CALLS: dict[str, dict] = {
    "list_lab_devices": {},
    "get_lab_device_facts": {"device_name": DEVICE},
    "check_lab_interfaces": {"device_name": DEVICE},
    "check_lab_bgp_neighbors": {"device_name": DEVICE},
    "check_lab_lldp_neighbors": {"device_name": DEVICE},
    "check_lab_isis_neighbors": {"device_name": DEVICE},
    "check_lab_sr_policies": {"device_name": DEVICE},
    "check_lab_fabric": {"check": "bgp"},
    "collect_lab_evidence": {"device_name": "PE2"},
    "get_lab_route": {"device_name": DEVICE, "prefix": f"{SUBJECT}/32"},
    "get_lab_bgp_neighbor": {"device_name": DEVICE, "address": SUBJECT},
    "get_lab_interface": {"device_name": "PE2", "name": "Gi0/0/0/0"},
    "get_lab_logging": {"device_name": "PE2", "count": 20},
    "get_lab_ping": {"device_name": DEVICE, "address": SUBJECT},
    "get_lab_traceroute": {"device_name": DEVICE, "address": SUBJECT},
    "diff_lab_device_against_latest": {"device_name": DEVICE},
    "diff_lab_device_against_golden": {"device_name": DEVICE},
    "assess_lab_device_health": {"device_name": DEVICE},
    "assess_lab_fabric_health": {},
    "detect_lab_flaps": {"device_name": DEVICE},
    "investigate_lab_session": {"device": DEVICE, "subject": SUBJECT},
}


async def _session(body):
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        client_read, client_write = client_streams
        server_read, server_write = server_streams
        task = asyncio.create_task(
            server.mcp._lowlevel_server.run(
                server_read, server_write,
                server.mcp._lowlevel_server.create_initialization_options(),
            )
        )
        try:
            async with ClientSession(client_read, client_write) as session:
                await session.initialize()
                return await body(session)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - teardown
                pass


def _text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def _raw_keys(payload, path="") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            where = f"{path}.{key}" if path else key
            if key in RAW_TEXT_KEYS:
                found.append(where)
            found.extend(_raw_keys(value, where))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(_raw_keys(item, f"{path}[{index}]"))
    return found


def test_every_registered_tool_is_smoke_tested():
    """§0.12. A tool added and not listed in `CALLS` would leave this file
    passing over a shrinking fraction of the surface -- which is exactly how the
    doc-sync check stopped covering `investigate_lab_session`."""

    async def body(session):
        return await session.list_tools()

    registered = {tool.name for tool in asyncio.run(_session(body)).tools}

    assert registered, "no tools registered at all"
    assert registered <= set(CALLS), f"not smoke-tested: {sorted(registered - set(CALLS))}"


@pytest.mark.parametrize("tool", sorted(CALLS), ids=sorted(CALLS))
def test_the_tool_runs_against_the_live_lab(tool):
    """One real call, over the real protocol, to real devices.

    Asserts a well-formed result and **no raw device text** -- not the fabric's
    state, which is not this code's business and which an injection round would
    legitimately change under us.
    """

    async def body(session):
        return await session.call_tool(tool, CALLS[tool])

    result = asyncio.run(_session(body))
    text = _text(result)

    assert text, f"{tool} returned no content at all"

    payload = json.loads(text)
    leaks = _raw_keys(payload)
    assert not leaks, f"{tool} leaked raw device text at {leaks}"


def test_investigate_returns_a_descent_and_a_rendered_report():
    """The new tool, end to end and live.

    Both halves are derived -- the descent's rung table and a report rendered
    from its typed fields -- so it passes the boundary unchanged, and this
    asserts that rather than assuming it.
    """

    async def body(session):
        return await session.call_tool(
            "investigate_lab_session", {"device": DEVICE, "subject": SUBJECT}
        )

    payload = json.loads(_text(asyncio.run(_session(body))))

    assert payload["tool"] == "investigate"
    assert payload["finding"], "a finding is always produced"
    assert isinstance(payload["trustworthy"], bool)

    assert len(payload["rungs"]) >= 1
    for rung in payload["rungs"]:
        assert rung["status"] in {"healthy", "broken", "unevaluated"}

    report = payload["report"]
    assert report["authoritative"] is True
    assert report["content"]["generated_by"] == "code"
    assert report["content"]["observations"], "the rendered report cites its rungs"

    # No model was called, so no paraphrase exists -- the CLI default, honoured.
    assert report["paraphrase"]["status"] == "not_attempted"
    assert report["paraphrase"]["content"] is None

    assert not _raw_keys(payload)


def test_the_worst_offender_is_actually_bounded_live():
    """`get_lab_logging` returned 37,962 characters of raw buffer before the
    boundary existed. Measured live rather than trusted, because the fixture
    version of this proves the stripper works and only a live call proves it is
    *in the path*."""

    async def body(session):
        return await session.call_tool(
            "get_lab_logging", {"device_name": "PE2", "count": 200}
        )

    payload = json.loads(_text(asyncio.run(_session(body))))

    assert not _raw_keys(payload)
    withheld = payload["data"]["commands_withheld"]
    assert withheld, "and the model is told text existed, not left to infer it"
    entry = next(iter(withheld.values()))
    assert entry["chars"] > 0, "a real buffer was read and withheld"
    assert payload["data"]["parsed"]["records"], "while the parsed records survive"
