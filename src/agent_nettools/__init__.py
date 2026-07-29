"""Read-only, multi-vendor network inspection tools."""

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
from .lab import DEVICES, all_devices, platform_for
from .llm_analysis import (
    LLMAnalysisError,
    analyze_evidence,
    build_analysis_prompt,
    get_provider,
)
from .network_tools import (
    CHECK_TOOLS,
    STATUS_ERROR,
    STATUS_SUCCESS,
    STATUS_UNSUPPORTED,
    check_bgp_neighbors,
    check_fabric,
    check_interfaces,
    check_isis_neighbors,
    check_lldp_neighbors,
    check_sr_policies,
    collect_evidence,
    diff_evidence,
    evidence_intents,
    get_device_facts,
    list_devices,
    load_latest_snapshot,
    run_intent,
    save_snapshot,
)
from .normalize import mask_volatile, normalize_output, strip_preamble
from .parsers import (
    PARSE_FAILED,
    PARSE_OK,
    PARSE_UNAVAILABLE,
    ParseError,
    has_parser,
    parse_intent,
    record_key,
    volatile_fields,
)
from .platforms import (
    ALL_APPROVED_COMMANDS,
    APPROVED_COMMANDS,
    DEFAULT_PLATFORM,
    PLATFORM_INTENTS,
    UnknownPlatformError,
    UnsupportedIntentError,
    all_intents,
    commands_for,
    intents_for,
    is_approved,
    known_platforms,
    supports,
)

__all__ = [
    # inventory
    "InventoryError",
    "load_inventory",
    "get_device",
    "get_default_device_name",
    "DEVICES",
    "all_devices",
    "platform_for",
    # platforms: the allowlist, keyed by vendor
    "PLATFORM_INTENTS",
    "APPROVED_COMMANDS",
    "ALL_APPROVED_COMMANDS",
    "DEFAULT_PLATFORM",
    "UnknownPlatformError",
    "UnsupportedIntentError",
    "known_platforms",
    "all_intents",
    "intents_for",
    "commands_for",
    "supports",
    "is_approved",
    # network tools
    "CHECK_TOOLS",
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "STATUS_UNSUPPORTED",
    "list_devices",
    "run_intent",
    "get_device_facts",
    "check_interfaces",
    "check_bgp_neighbors",
    "check_lldp_neighbors",
    "check_isis_neighbors",
    "check_sr_policies",
    "check_fabric",
    "collect_evidence",
    "evidence_intents",
    "save_snapshot",
    "load_latest_snapshot",
    "diff_evidence",
    # parsing: structured records from raw command output
    "parse_intent",
    "has_parser",
    "record_key",
    "volatile_fields",
    "ParseError",
    "PARSE_OK",
    "PARSE_UNAVAILABLE",
    "PARSE_FAILED",
    # normalization: the diff fallback when no parser exists
    "normalize_output",
    "strip_preamble",
    "mask_volatile",
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
