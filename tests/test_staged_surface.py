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


def test_unrecognized_surface_value_fails_closed_to_staged(monkeypatch):
    """EER-008b: an unrecognized NETTOOLS_MCP_SURFACE (e.g. a typo like
    'stage') used to fall back to 'classic' -- the WIDER surface -- with only
    a `logging.warning`. It must now fail CLOSED to 'staged', the narrower
    one, matching `_mcp_active_probes_allowed`'s idiom: a typo must not
    silently grant more tool surface than was asked for."""

    import mcp_server.server as server
    from mcp_server.staged_surface import STAGED_TOOL_NAMES

    monkeypatch.setenv("NETTOOLS_MCP_SURFACE", "stage")  # typo for "staged"
    try:
        importlib.reload(server)
        assert server.ACTIVE_SURFACE == "staged"
        assert _tool_names(server) == set(STAGED_TOOL_NAMES)
    finally:
        monkeypatch.delenv("NETTOOLS_MCP_SURFACE", raising=False)
        importlib.reload(server)


def test_a_genuinely_unset_surface_still_defaults_to_classic(monkeypatch):
    """The positive control for the test above: the DEFAULT (unset) is
    unaffected by the fail-closed change -- only an explicitly-set,
    unrecognized value now resolves to the narrower surface, never the
    absence of a value at all."""

    import mcp_server.server as server

    monkeypatch.delenv("NETTOOLS_MCP_SURFACE", raising=False)
    importlib.reload(server)

    assert server.ACTIVE_SURFACE == "classic"
    assert "investigate_lab_session" in _tool_names(server)


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


# --------------------------------------------------------------------------- #
# F1 -- the six hand-authored error envelopes must carry the same key set as
# every other envelope in the surface (tool/device/status/data/errors), not a
# bare {"status": "error", "errors": [...]}. Exercised directly on the plain
# functions (no MCP registration/session needed -- these are ordinary Python
# calls) so each test targets exactly one branch.
# --------------------------------------------------------------------------- #

_ENVELOPE_KEYS = {"tool", "device", "status", "data", "errors"}


def test_check_lab_unknown_intent_carries_the_full_envelope():
    import mcp_server.staged_surface as staged

    result = staged.check_lab("PE1", intent="bogus-intent")

    assert set(result) == _ENVELOPE_KEYS
    assert result["tool"] == "check_lab"
    assert result["device"] == "PE1"
    assert result["status"] == "error"
    assert result["data"] == {}
    assert "bogus-intent" in result["errors"][0]


def test_check_lab_unknown_device_carries_the_full_envelope():
    import mcp_server.staged_surface as staged

    result = staged.check_lab("not-a-real-device")

    assert set(result) == _ENVELOPE_KEYS
    assert result["tool"] == "check_lab"
    assert result["device"] == "not-a-real-device"
    assert result["status"] == "error"
    assert result["data"] == {}
    assert "not-a-real-device" in result["errors"][0]


def test_lookup_lab_unknown_kind_carries_the_full_envelope():
    import mcp_server.staged_surface as staged

    result = staged.lookup_lab("PE1", "bogus-kind", "10.0.0.0/24")

    assert set(result) == _ENVELOPE_KEYS
    assert result["tool"] == "lookup_lab"
    assert result["device"] == "PE1"
    assert result["status"] == "error"
    assert result["data"] == {}
    assert "bogus-kind" in result["errors"][0]


def test_history_lab_unknown_mode_carries_the_full_envelope():
    import mcp_server.staged_surface as staged

    result = staged.history_lab("PE1", mode="bogus-mode")

    assert set(result) == _ENVELOPE_KEYS
    assert result["tool"] == "history_lab"
    assert result["device"] == "PE1"
    assert result["status"] == "error"
    assert result["data"] == {}
    assert "bogus-mode" in result["errors"][0]


def test_history_lab_no_saved_snapshot_carries_the_full_envelope(monkeypatch):
    import mcp_server.staged_surface as staged

    # Deterministic regardless of what evidence store state happens to exist
    # on disk -- the branch under test is "no baseline found", not "the repo
    # happens to have none right now".
    monkeypatch.setattr(staged, "load_latest_snapshot", lambda device: None)

    result = staged.history_lab("PE1", mode="latest_diff")

    assert set(result) == _ENVELOPE_KEYS
    assert result["tool"] == "history_lab"
    assert result["device"] == "PE1"
    assert result["status"] == "error"
    assert result["data"] == {}
    assert "no saved snapshot exists for PE1" in result["errors"][0]


def test_probe_lab_unknown_kind_carries_the_full_envelope():
    import mcp_server.staged_surface as staged

    result = staged.probe_lab("PE1", "bogus-kind", "10.0.0.1")

    assert set(result) == _ENVELOPE_KEYS
    assert result["tool"] == "probe_lab"
    assert result["device"] == "PE1"
    assert result["status"] == "error"
    assert result["data"] == {}
    assert "bogus-kind" in result["errors"][0]


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
