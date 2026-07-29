"""Read-only, multi-vendor network inspection tools."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("agent-nettools")
except PackageNotFoundError:  # Running from a source checkout with no install metadata.
    __version__ = "0.0.0+unknown"

from .agent_loop import TOOLS as AGENT_TOOLS
from .agent_loop import run_agent_loop
from .credential_resolver import (
    DEFAULT_CREDENTIAL_PROVIDER,
    NETTOOLS_CREDENTIAL_PROVIDER_ENV,
    CredentialResolver,
    EnvCredentialResolver,
    FileCredentialResolver,
    get_resolver,
    known_credential_providers,
)
from .devices_doc import render_devices_doc
from .evidence_budget import (
    DEFAULT_PER_INTENT_CHAR_BUDGET,
    DEFAULT_TOTAL_CHAR_BUDGET,
    PER_INTENT_CHAR_BUDGET_ENV,
    TOTAL_CHAR_BUDGET_ENV,
    budget_device_evidence,
    budget_fabric_evidence,
    render_budgeted_evidence,
)
from .evidence_store import (
    DEFAULT_EVIDENCE_BACKEND,
    EVIDENCE_BACKEND_ENV,
    EvidenceStore,
    FileEvidenceStore,
    SQLiteEvidenceStore,
    get_store,
)
from .fabric_analysis import FABRIC_ANALYSIS_PROMPT, analyze_fabric, build_fabric_prompt
from .fixtures import (
    capture_device,
    command_slug,
    fixture_sender,
    load_fixture_evidence,
    scrub_output,
)
from .health import (
    ALL_RULES,
    BASELINE_RULES,
    META_RULES,
    ROLE_INVARIANT_RULES,
    SEVERITY_ORDER,
    evaluate_device,
    evaluate_fabric,
    exit_code_for_severity,
    severity_rank,
)
from .inventory import (
    InventoryError,
    get_default_device_name,
    get_device,
    load_inventory,
)
from .inventory_model import (
    CredentialGroup,
    Defaults,
    Device,
    Expected,
    InventoryFile,
    load_inventory_file,
    parse_inventory,
    reset_inventory_cache,
    resolve_inventory_path,
)
from .lab import DEVICES, PLATFORMS, all_devices, platform_for
from .llm_analysis import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    ANTHROPIC_MODEL_DEFAULT,
    LLMAnalysisError,
    analyze_evidence,
    analyze_with_anthropic,
    analyze_with_ollama,
    analyze_with_openai,
    build_analysis_prompt,
    get_provider,
)
from .metrics import (
    NETTOOLS_METRICS_FILE_ENV,
    MetricsCollector,
    render_prometheus,
)
from .metrics import (
    record_collection as record_metrics_collection,
)
from .metrics import (
    record_verdict as record_metrics_verdict,
)
from .metrics import (
    reset as reset_metrics,
)
from .metrics import (
    snapshot as metrics_snapshot,
)
from .network_tools import (
    CHECK_TOOLS,
    DEFAULT_BANNER_TIMEOUT_SECONDS,
    DEFAULT_COMMAND_RETRIES,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_MAX_BYTES,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    GOLDEN_SNAPSHOT_FILENAME,
    NETTOOLS_ACTOR_ENV,
    NETTOOLS_ALLOW_ACTIVE_PROBES_ENV,
    NETTOOLS_BANNER_TIMEOUT_ENV,
    NETTOOLS_COMMAND_RETRIES_ENV,
    NETTOOLS_CONNECT_TIMEOUT_ENV,
    NETTOOLS_LOG_BACKUP_COUNT_ENV,
    NETTOOLS_LOG_MAX_BYTES_ENV,
    NETTOOLS_READ_TIMEOUT_ENV,
    NETTOOLS_RETRY_BACKOFF_ENV,
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
    detect_flaps,
    diff_evidence,
    evidence_intents,
    get_bgp_neighbor,
    get_device_facts,
    get_interface,
    get_logging,
    get_route,
    iter_fabric,
    list_devices,
    list_snapshot_history,
    load_golden_snapshot,
    load_latest_snapshot,
    ping_device,
    prune_snapshots,
    run_intent,
    run_template,
    save_golden_snapshot,
    save_snapshot,
    traceroute_device,
)
from .normalize import mask_volatile, normalize_output, strip_preamble
from .output import FORMATS as OUTPUT_FORMATS
from .output import render as render_output
from .output import render_summary as render_output_summary
from .output import render_table as render_output_table
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
    PLATFORM_TEMPLATES,
    VERB_ALLOWLIST,
    BoundedIntParam,
    InterfaceNameParam,
    IPv4AddressParam,
    IPv4PrefixParam,
    ParamType,
    Template,
    TemplateValidationError,
    UnknownPlatformError,
    UnknownTemplateError,
    UnsupportedIntentError,
    all_intents,
    commands_for,
    intents_for,
    is_approved,
    is_safe_rendered_command,
    known_platform_templates,
    known_platforms,
    render_command,
    supports,
    supports_template,
    template_for,
)
from .topology import (
    build_anomaly_report,
    derive_expected,
    find_lldp_disagreements,
    find_neighbors_not_in_inventory,
    find_zero_adjacency_devices,
    format_anomaly_report,
    update_expected_in_yaml,
)

__all__ = [
    # package version (Phase 8, `nettools version`)
    "__version__",
    # pluggable credential resolution (Phase 8)
    "NETTOOLS_CREDENTIAL_PROVIDER_ENV",
    "DEFAULT_CREDENTIAL_PROVIDER",
    "CredentialResolver",
    "EnvCredentialResolver",
    "FileCredentialResolver",
    "get_resolver",
    "known_credential_providers",
    # metrics (Phase 8)
    "NETTOOLS_METRICS_FILE_ENV",
    "MetricsCollector",
    "render_prometheus",
    "record_metrics_collection",
    "record_metrics_verdict",
    "metrics_snapshot",
    "reset_metrics",
    # output formatting (Phase 8): json (default)/table/summary
    "OUTPUT_FORMATS",
    "render_output",
    "render_output_table",
    "render_output_summary",
    # audit actor (Phase 8, provenance only -- see network_tools.py)
    "NETTOOLS_ACTOR_ENV",
    # inventory
    "InventoryError",
    "load_inventory",
    "get_device",
    "get_default_device_name",
    "DEVICES",
    "PLATFORMS",
    "all_devices",
    "platform_for",
    # inventory schema and YAML loading (inventory_model.py)
    "InventoryFile",
    "Device",
    "Defaults",
    "CredentialGroup",
    "Expected",
    "parse_inventory",
    "load_inventory_file",
    "resolve_inventory_path",
    "reset_inventory_cache",
    # topology: derived expected counts and the fabric anomaly report
    "derive_expected",
    "build_anomaly_report",
    "format_anomaly_report",
    "find_lldp_disagreements",
    "find_neighbors_not_in_inventory",
    "find_zero_adjacency_devices",
    "update_expected_in_yaml",
    # docs generation
    "render_devices_doc",
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
    # templates: validated, parameterized commands (Phase 5)
    "PLATFORM_TEMPLATES",
    "VERB_ALLOWLIST",
    "Template",
    "ParamType",
    "IPv4AddressParam",
    "IPv4PrefixParam",
    "InterfaceNameParam",
    "BoundedIntParam",
    "TemplateValidationError",
    "UnknownTemplateError",
    "render_command",
    "supports_template",
    "template_for",
    "known_platform_templates",
    "is_safe_rendered_command",
    # network tools
    "CHECK_TOOLS",
    "STATUS_SUCCESS",
    "STATUS_ERROR",
    "STATUS_UNSUPPORTED",
    "list_devices",
    "run_intent",
    "run_template",
    "NETTOOLS_ALLOW_ACTIVE_PROBES_ENV",
    "get_device_facts",
    "check_interfaces",
    "check_bgp_neighbors",
    "check_lldp_neighbors",
    "check_isis_neighbors",
    "check_sr_policies",
    "check_fabric",
    "iter_fabric",
    "get_route",
    "get_bgp_neighbor",
    "get_interface",
    "get_logging",
    "ping_device",
    "traceroute_device",
    "collect_evidence",
    "evidence_intents",
    # connection timeouts and bounded retries (Phase 7)
    "NETTOOLS_CONNECT_TIMEOUT_ENV",
    "NETTOOLS_READ_TIMEOUT_ENV",
    "NETTOOLS_BANNER_TIMEOUT_ENV",
    "NETTOOLS_COMMAND_RETRIES_ENV",
    "NETTOOLS_RETRY_BACKOFF_ENV",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_READ_TIMEOUT_SECONDS",
    "DEFAULT_BANNER_TIMEOUT_SECONDS",
    "DEFAULT_COMMAND_RETRIES",
    "DEFAULT_RETRY_BACKOFF_SECONDS",
    # audit log rotation (Phase 7)
    "NETTOOLS_LOG_MAX_BYTES_ENV",
    "NETTOOLS_LOG_BACKUP_COUNT_ENV",
    "DEFAULT_LOG_MAX_BYTES",
    "DEFAULT_LOG_BACKUP_COUNT",
    # evidence storage (Phase 7): JSON files (default) or SQLite, same shape
    "EVIDENCE_BACKEND_ENV",
    "DEFAULT_EVIDENCE_BACKEND",
    "EvidenceStore",
    "FileEvidenceStore",
    "SQLiteEvidenceStore",
    "get_store",
    "save_snapshot",
    "load_latest_snapshot",
    "list_snapshot_history",
    "prune_snapshots",
    "diff_evidence",
    # golden (pinned) snapshots and flap detection
    "GOLDEN_SNAPSHOT_FILENAME",
    "save_golden_snapshot",
    "load_golden_snapshot",
    "detect_flaps",
    # health: deterministic verdicts over collected evidence (Phase 4)
    "SEVERITY_ORDER",
    "ROLE_INVARIANT_RULES",
    "BASELINE_RULES",
    "META_RULES",
    "ALL_RULES",
    "severity_rank",
    "exit_code_for_severity",
    "evaluate_device",
    "evaluate_fabric",
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
    "analyze_with_anthropic",
    "analyze_with_openai",
    "analyze_with_ollama",
    "ANTHROPIC_MODEL_DEFAULT",
    "ANTHROPIC_MAX_OUTPUT_TOKENS",
    # evidence budget (Phase 6): keep a fabric-wide bundle from blowing the context
    "DEFAULT_PER_INTENT_CHAR_BUDGET",
    "DEFAULT_TOTAL_CHAR_BUDGET",
    "PER_INTENT_CHAR_BUDGET_ENV",
    "TOTAL_CHAR_BUDGET_ENV",
    "budget_device_evidence",
    "budget_fabric_evidence",
    "render_budgeted_evidence",
    # fabric-wide analysis (Phase 6): cross-device correlation
    "FABRIC_ANALYSIS_PROMPT",
    "analyze_fabric",
    "build_fabric_prompt",
    # bounded agent loop (Phase 6): read-only tool-calling over the same safety boundary
    "AGENT_TOOLS",
    "run_agent_loop",
]
