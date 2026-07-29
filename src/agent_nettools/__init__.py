"""Read-only Cisco IOS-XR network tools package."""

from .fixtures import (
    capture_device,
    command_slug,
    fixture_sender,
    load_fixture_evidence,
    scrub_output,
)
from .inventory import (
    InventoryError,
    get_default_device_name,
    get_device,
    load_inventory,
)
from .lab import DEVICES, all_devices
from .llm_analysis import (
    LLMAnalysisError,
    analyze_evidence,
    build_analysis_prompt,
    get_provider,
)
from .network_tools import (
    APPROVED_COMMANDS,
    CHECK_TOOLS,
    EVIDENCE_COMMANDS,
    check_bgp_neighbors,
    check_fabric,
    check_interfaces,
    check_isis_neighbors,
    check_lldp_neighbors,
    check_sr_policies,
    collect_evidence,
    diff_evidence,
    get_device_facts,
    list_devices,
    load_latest_snapshot,
    save_snapshot,
)

__all__ = [
    # inventory
    "InventoryError",
    "load_inventory",
    "get_device",
    "get_default_device_name",
    "DEVICES",
    "all_devices",
    # network tools
    "APPROVED_COMMANDS",
    "EVIDENCE_COMMANDS",
    "CHECK_TOOLS",
    "list_devices",
    "get_device_facts",
    "check_interfaces",
    "check_bgp_neighbors",
    "check_lldp_neighbors",
    "check_isis_neighbors",
    "check_sr_policies",
    "check_fabric",
    "collect_evidence",
    "save_snapshot",
    "load_latest_snapshot",
    "diff_evidence",
    # fixtures
    "command_slug",
    "scrub_output",
    "capture_device",
    "fixture_sender",
    "load_fixture_evidence",
    # analysis
    "LLMAnalysisError",
    "get_provider",
    "build_analysis_prompt",
    "analyze_evidence",
]
