"""B-479 -- the staged surface: same functions, same boundary, smaller manifest."""

from __future__ import annotations

import importlib
import os

import pytest
from helpers import set_device_environment

from agent_nettools.evidence_expand import build_log_evidence_key
from agent_nettools.fixtures import fixture_sender
from agent_nettools.investigation import investigate


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


def test_staged_registers_the_declared_guided_tools(staged_server):
    from mcp_server.staged_surface import STAGED_TOOL_NAMES

    assert _tool_names(staged_server) == set(STAGED_TOOL_NAMES)


def test_expansion_context_expands_only_same_run_disclosed_evidence(monkeypatch):
    import mcp_server.staged_surface as staged

    set_device_environment(monkeypatch)
    staged._expansion_contexts.clear()
    result = investigate(
        "RR1", "10.255.0.12", flow="bgp_session",
        resolver=lambda subject: {"10.255.0.12": "PE2"}[subject],
        sender=fixture_sender(label="broken"),
    )
    context_id = staged._store_expansion_context(result)
    assert context_id is not None
    context = staged._expansion_context(context_id)
    assert context is not None
    aggregate = context.disclosed.aggregates[0]

    expanded = staged.expand_lab_evidence(
        context_id, build_log_evidence_key(context.disclosed.device, aggregate.mnemonic)
    )

    assert expanded["status"] == "ok"
    assert "raw_log_text" not in result.to_payload()


def test_expansion_context_expiry_refuses_without_recollection(monkeypatch):
    import mcp_server.staged_surface as staged

    staged._expansion_contexts.clear()
    monkeypatch.setattr(staged.time, "monotonic", lambda: 1000.0)
    result = type("Result", (), {"log_window": object(), "raw_log_text": "raw", "device": "PE1"})()
    result.descent = type("Descent", (), {"cause": None})()
    monkeypatch.setattr(staged, "disclose_log_window", lambda *_: object())
    context_id = staged._store_expansion_context(result)
    monkeypatch.setattr(staged.time, "monotonic", lambda: 2000.0)

    assert staged.expand_lab_evidence(context_id, "logs:PE1:TEST")["errors"] == [
        "unknown or expired evidence expansion context"
    ]


def test_offline_event_manifest_matches_the_live_staged_surface(staged_server):
    """B-703: the planning, pinning, and live schemas must agree.

    ``event_agent`` cannot import ``mcp_server`` at runtime, so its offline
    planning manifest is necessarily a declared copy. That copy must still be
    checked here, where tests may import both sides: a parameter added to the
    staged surface without a PIN_TABLE treatment, or an offline schema that
    no longer matches the live registry, must fail before a model sees either
    manifest.
    """

    from agent_nettools import event_agent, model_ingress
    from agent_nettools.mcp_profiles import GUIDED_CAPABILITIES, ModelPolicy
    from mcp_server.staged_surface import STAGED_TOOL_NAMES

    offline = {entry["name"]: entry["input_schema"] for entry in event_agent._PLANNING_TOOL_SCHEMAS}
    expected = set(STAGED_TOOL_NAMES) - set(model_ingress.STRUCTURALLY_EXCLUDED_TOOLS)

    assert {
        capability.name for capability in GUIDED_CAPABILITIES if capability.model_policy is ModelPolicy.PINNED
    } == expected

    assert set(offline) == expected
    assert set(model_ingress.PIN_TABLE) == expected

    live = staged_server.mcp._tool_manager._tools
    for name in expected:
        live_schema = live[name].parameters
        offline_schema = offline[name]
        declared = {spec.name for spec in model_ingress.PIN_TABLE[name]}

        assert set(live_schema["properties"]) == declared, name
        assert set(offline_schema["properties"]) == declared, name
        assert set(offline_schema.get("required", ())) == set(live_schema.get("required", ())), name


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
