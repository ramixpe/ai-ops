"""Shared MCP profile policy.

The server owns registration mechanics while model ingress owns pinned
arguments. This module owns stable surface values, the guided tool set, and
fail-closed surface selection without importing ``mcp_server``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "CLASSIC_SURFACE",
    "DEFAULT_SURFACE",
    "GUIDED_CAPABILITIES",
    "GUIDED_TOOL_NAMES",
    "CLASSIC_CAPABILITIES",
    "CLASSIC_TOOL_NAMES",
    "Capability",
    "ModelPolicy",
    "MCPProfile",
    "ObjectContract",
    "RegistrationClass",
    "STAGED_SURFACE",
    "profile_for",
]

CLASSIC_SURFACE = "classic"
STAGED_SURFACE = "staged"
DEFAULT_SURFACE = CLASSIC_SURFACE

class RegistrationClass(Enum):
    """The MCP boundary annotation and gate a capability requires."""

    PASSIVE = "passive"
    ACTIVE_PROBE = "active_probe"
    EXTERNAL_SOURCE = "external_source"


class ObjectContract(Enum):
    """Whether a capability acts on an asserted or lookup identifier."""

    NONE = "none"
    ASSERTED = "asserted"
    LOOKUP = "lookup"


class ModelPolicy(Enum):
    """Whether the guided event-model offer includes the capability."""

    PINNED = "pinned"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class Capability:
    """One guided capability's stable public and registration contract."""

    name: str
    registration: RegistrationClass
    object_contract: ObjectContract
    model_policy: ModelPolicy


GUIDED_CAPABILITIES = (
    Capability("explore_lab", RegistrationClass.PASSIVE, ObjectContract.NONE, ModelPolicy.PINNED),
    Capability("check_lab", RegistrationClass.PASSIVE, ObjectContract.NONE, ModelPolicy.PINNED),
    Capability("lookup_lab", RegistrationClass.PASSIVE, ObjectContract.LOOKUP, ModelPolicy.EXCLUDED),
    Capability("investigate_lab", RegistrationClass.PASSIVE, ObjectContract.ASSERTED, ModelPolicy.PINNED),
    Capability("expand_lab_evidence", RegistrationClass.PASSIVE, ObjectContract.NONE, ModelPolicy.EXCLUDED),
    Capability("history_lab", RegistrationClass.PASSIVE, ObjectContract.NONE, ModelPolicy.PINNED),
    Capability("probe_lab", RegistrationClass.ACTIVE_PROBE, ObjectContract.NONE, ModelPolicy.PINNED),
)

GUIDED_TOOL_NAMES = tuple(capability.name for capability in GUIDED_CAPABILITIES)

# Expert/classic remains a compatibility surface registered directly by
# mcp_server.server. This inventory makes its policy coverage explicit before
# registration is generated from the registry in a later migration slice.
_CLASSIC_ACTIVE_PROBES = frozenset({"get_lab_ping", "get_lab_traceroute"})
_CLASSIC_EXTERNAL = frozenset(
    {
        "get_lab_logs",
        "get_lab_interface_rate_history",
        "get_lab_isis_adjacency_history",
        "get_lab_ldp_session_history",
        "get_lab_device_uptime_history",
        "get_lab_netbox_inventory",
        "get_lab_netbox_topology",
        "get_lab_graph_topology",
    }
)
_CLASSIC_LOOKUPS = frozenset({"get_lab_route", "get_lab_sr_policy_detail"})
_CLASSIC_ASSERTED = frozenset({"get_lab_bgp_neighbor", "get_lab_interface"})
_CLASSIC_TOOL_NAMES = (
    "assess_lab_device_health", "assess_lab_fabric_health", "check_lab_bgp_neighbors",
    "check_lab_bgp_vpnv4_neighbors", "check_lab_fabric", "check_lab_interfaces",
    "check_lab_isis_neighbors", "check_lab_ldp_discovery", "check_lab_ldp_neighbors",
    "check_lab_lldp_neighbors", "check_lab_sr_policies", "collect_lab_evidence",
    "detect_lab_flaps", "diff_lab_device_against_golden", "diff_lab_device_against_latest",
    "explain_lab_mnemonic", "get_lab_bgp_neighbor", "get_lab_device_facts",
    "get_lab_device_uptime_history", "get_lab_graph_topology", "get_lab_interface",
    "get_lab_interface_rate_history", "get_lab_isis_adjacency_history",
    "get_lab_ldp_session_history", "get_lab_logging", "get_lab_logs",
    "get_lab_netbox_inventory", "get_lab_netbox_topology", "get_lab_ping", "get_lab_route",
    "get_lab_sr_policy_detail", "get_lab_traceroute", "investigate_lab_session",
    "list_lab_devices", "list_lab_tickets", "read_lab_ticket", "search_lab_knowledge",
)
CLASSIC_CAPABILITIES = tuple(
    Capability(
        name,
        RegistrationClass.ACTIVE_PROBE if name in _CLASSIC_ACTIVE_PROBES else (
            RegistrationClass.EXTERNAL_SOURCE if name in _CLASSIC_EXTERNAL else RegistrationClass.PASSIVE
        ),
        ObjectContract.ASSERTED if name in _CLASSIC_ASSERTED else (
            ObjectContract.LOOKUP if name in _CLASSIC_LOOKUPS else ObjectContract.NONE
        ),
        ModelPolicy.EXCLUDED,
    )
    for name in _CLASSIC_TOOL_NAMES
)
CLASSIC_TOOL_NAMES = tuple(capability.name for capability in CLASSIC_CAPABILITIES)


@dataclass(frozen=True)
class MCPProfile:
    """One public MCP profile, independent of registration mechanics."""

    surface: str
    label: str
    tool_names: tuple[str, ...] | None
    recommended_for_local_model: bool


_PROFILES = {
    CLASSIC_SURFACE: MCPProfile(
        surface=CLASSIC_SURFACE,
        label="expert",
        tool_names=None,
        recommended_for_local_model=False,
    ),
    STAGED_SURFACE: MCPProfile(
        surface=STAGED_SURFACE,
        label="guided",
        tool_names=GUIDED_TOOL_NAMES,
        recommended_for_local_model=True,
    ),
}


def profile_for(raw: str | None) -> MCPProfile:
    """Return the selected profile; an explicit unknown value fails closed."""

    if raw is None or not raw.strip():
        return _PROFILES[DEFAULT_SURFACE]
    return _PROFILES.get(raw.strip().lower(), _PROFILES[STAGED_SURFACE])
