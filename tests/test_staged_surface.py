"""B-479 -- the staged surface: same functions, same boundary, smaller manifest."""

from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def staged_server(monkeypatch):
    """server.py reloaded with the flag set, restored to classic afterwards."""

    import mcp_server.server as server

    monkeypatch.setenv("NETTOOLS_MCP_SURFACE", "staged")
    importlib.reload(server)
    yield server
    monkeypatch.delenv("NETTOOLS_MCP_SURFACE", raising=False)
    importlib.reload(server)


def _tool_names(server):
    return set(server.mcp._tool_manager._tools)


def test_the_default_surface_is_classic_and_unchanged():
    import mcp_server.server as server

    if os.getenv("NETTOOLS_MCP_SURFACE"):
        importlib.reload(server)  # pragma: no cover -- defensive against env bleed
    names = _tool_names(server)
    assert "investigate_lab_session" in names
    assert "explore_lab" not in names
    assert len(names) >= 23


def test_staged_registers_exactly_the_six(staged_server):
    from mcp_server.staged_surface import STAGED_TOOL_NAMES

    assert _tool_names(staged_server) == set(STAGED_TOOL_NAMES)


def test_the_probe_keeps_its_distinct_annotation_on_staged(staged_server):
    """B-473 must survive consolidation -- folding probes into a passive tool
    would erase the distinction that wave built."""

    tools = staged_server.mcp._tool_manager._tools
    probe = tools["probe_lab"]
    passive = tools["explore_lab"]
    p_ann = getattr(probe, "annotations", None)
    e_ann = getattr(passive, "annotations", None)
    if p_ann is None:  # very old SDK: annotations unsupported; nothing to compare
        pytest.skip("SDK without annotations")
    assert getattr(p_ann, "title", "") and "ACTIVE PROBE" in p_ann.title
    assert getattr(e_ann, "title", None) != getattr(p_ann, "title", None)


def test_sanitisation_holds_on_the_staged_surface(staged_server, monkeypatch):
    """The boundary is the registration machinery, so staged inherits it --
    proven by canary, not asserted by construction."""

    canary = "CANARY -- raw device text must not survive"
    import mcp_server.staged_surface as staged

    monkeypatch.setattr(
        staged, "get_route",
        lambda device, value, **k: {
            "tool": "run_template", "device": device, "status": "success",
            "data": {"commands": {"show route": canary}, "parsed": {"meta": {}}},
            "errors": [],
        },
    )
    # lookup_lab reads the module-level _LOOKUPS dict -- repoint it too.
    monkeypatch.setitem(staged._LOOKUPS, "route", staged.get_route)

    # Call through the registered (sanitised) tool, not the plain function.
    registered = staged_server.mcp._tool_manager._tools["lookup_lab"].fn
    result = registered("PE1", "route", "10.0.0.0/24")

    import json
    assert canary not in json.dumps(result)
    assert "commands_withheld" in json.dumps(result)


def test_the_staged_manifest_is_smaller_than_classic(staged_server):
    """B-113's arithmetic argument, made checkable: the whole point of
    consolidation is context cost, so measure it."""

    staged_chars = sum(
        len(t.description or "") for t in staged_server.mcp._tool_manager._tools.values()
    )
    importlib_mod = importlib.import_module("mcp_server.server")
    # reload back to classic to measure it
    os.environ.pop("NETTOOLS_MCP_SURFACE", None)
    importlib.reload(importlib_mod)
    classic_chars = sum(
        len(t.description or "") for t in importlib_mod.mcp._tool_manager._tools.values()
    )
    assert staged_chars < classic_chars
    assert staged_chars < classic_chars / 2, (
        f"staged {staged_chars} vs classic {classic_chars}: the staged surface "
        "must be a materially smaller manifest, not a marginal one"
    )
