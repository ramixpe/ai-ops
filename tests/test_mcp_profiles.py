"""Shared MCP profile policy remains compatible with public surface values."""

from agent_nettools.mcp_profiles import (
    CLASSIC_CAPABILITIES,
    CLASSIC_SURFACE,
    CLASSIC_TOOL_NAMES,
    DEFAULT_SURFACE,
    GUIDED_CAPABILITIES,
    GUIDED_TOOL_NAMES,
    STAGED_SURFACE,
    ModelPolicy,
    ObjectContract,
    RegistrationClass,
    profile_for,
)


def test_unset_surface_keeps_the_classic_compatibility_default():
    assert DEFAULT_SURFACE == CLASSIC_SURFACE
    assert profile_for(None).surface == CLASSIC_SURFACE


def test_explicit_unknown_surface_fails_closed_to_guided():
    profile = profile_for("stage")

    assert profile.surface == STAGED_SURFACE
    assert profile.recommended_for_local_model is True
    assert profile.tool_names == GUIDED_TOOL_NAMES


def test_guided_surface_has_exactly_the_six_registration_names():
    assert profile_for("staged").tool_names == (
        "explore_lab",
        "check_lab",
        "lookup_lab",
        "investigate_lab",
        "history_lab",
        "probe_lab",
    )


def test_guided_capability_registry_declaratively_marks_the_only_probe():
    by_name = {capability.name: capability for capability in GUIDED_CAPABILITIES}

    assert tuple(by_name) == GUIDED_TOOL_NAMES
    assert by_name["probe_lab"].registration is RegistrationClass.ACTIVE_PROBE
    assert all(
        capability.registration is RegistrationClass.PASSIVE
        for capability in GUIDED_CAPABILITIES
        if capability.name != "probe_lab"
    )


def test_guided_registry_declares_model_and_object_contracts():
    by_name = {capability.name: capability for capability in GUIDED_CAPABILITIES}

    assert by_name["investigate_lab"].object_contract is ObjectContract.ASSERTED
    assert by_name["lookup_lab"].object_contract is ObjectContract.LOOKUP
    assert by_name["lookup_lab"].model_policy is ModelPolicy.EXCLUDED
    assert all(
        capability.model_policy is ModelPolicy.PINNED
        for capability in GUIDED_CAPABILITIES
        if capability.name != "lookup_lab"
    )


def test_guided_object_contracts_are_compatible_with_direct_mcp_contracts():
    from mcp_server import server

    by_name = {capability.name: capability for capability in GUIDED_CAPABILITIES}
    direct = server._OBJECT_REQUEST_CONTRACTS

    assert by_name["investigate_lab"].object_contract is ObjectContract.ASSERTED
    assert direct["get_lab_bgp_neighbor"] == "asserted"
    assert direct["get_lab_interface"] == "asserted"
    assert by_name["lookup_lab"].object_contract is ObjectContract.LOOKUP
    assert direct["get_lab_route"] == "lookup"
    assert direct["get_lab_sr_policy_detail"] == "lookup"


def test_classic_registry_inventory_matches_registered_compatibility_surface():
    from mcp_server import server

    registered = set(server.mcp._tool_manager._tools)

    assert set(CLASSIC_TOOL_NAMES) == registered
    by_name = {capability.name: capability for capability in CLASSIC_CAPABILITIES}
    assert by_name["get_lab_ping"].registration is RegistrationClass.ACTIVE_PROBE
    assert by_name["get_lab_logs"].registration is RegistrationClass.EXTERNAL_SOURCE
    assert by_name["get_lab_bgp_neighbor"].object_contract is ObjectContract.ASSERTED