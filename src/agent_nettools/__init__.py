"""Read-only, multi-vendor network inspection tools."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

try:
    __version__ = _package_version("agent-nettools")
except PackageNotFoundError:  # Running from a source checkout with no install metadata.
    __version__ = "0.0.0+unknown"

# --------------------------------------------------------------------------- #
# P4 (release-1.0 cleanup): lazy re-exports (PEP 562).
#
# This package used to import every submodule below eagerly, at `import
# agent_nettools` time, purely to re-export ~150 names for anyone using this
# as a library (`from agent_nettools import X`) -- no code IN this repo
# actually does that (every internal caller imports from the specific
# submodule, e.g. `from agent_nettools.fixtures import capture_device`;
# confirmed by an AST sweep of tests/, mcp_server/, and scripts/ before this
# change), so the eager cost was paid by every `nettools` invocation and
# every test collection for a surface only an external consumer reads.
#
# `__version__` stays eager, deliberately: it is metadata, not a submodule
# import, costs nothing, and `nettools version` (among other callers) expects
# `from agent_nettools import __version__` to work with zero submodule
# loading -- exactly the behaviour this rewrite preserves.
#
# Every other name in `__all__` now resolves through `__getattr__` below,
# imported on FIRST access and cached on this module's own `__dict__`
# afterward (`globals()[name] = value`), so a second access is a plain
# attribute lookup, not a second import. The map is `name -> (submodule,
# real_name_in_that_submodule)` -- `real_name` differs from `name` only for
# the handful of re-exports this package renamed on the way out (e.g.
# `AGENT_TOOLS` is `agent_loop.TOOLS`; `record_metrics_collection` is
# `metrics.record_collection`), which is exactly why this is a dict of pairs
# rather than a bare `name -> submodule` map.
# --------------------------------------------------------------------------- #

import importlib
from typing import Any

_LAZY: dict[str, tuple[str, str]] = {
    # agent_loop (bounded, read-only tool-calling loop)
    "AGENT_TOOLS": (".agent_loop", "TOOLS"),
    "run_agent_loop": (".agent_loop", "run_agent_loop"),
    # credential_resolver (Phase 8: pluggable credential resolution)
    "NETTOOLS_CREDENTIAL_PROVIDER_ENV": (".credential_resolver", "NETTOOLS_CREDENTIAL_PROVIDER_ENV"),
    "DEFAULT_CREDENTIAL_PROVIDER": (".credential_resolver", "DEFAULT_CREDENTIAL_PROVIDER"),
    "CredentialResolver": (".credential_resolver", "CredentialResolver"),
    "EnvCredentialResolver": (".credential_resolver", "EnvCredentialResolver"),
    "FileCredentialResolver": (".credential_resolver", "FileCredentialResolver"),
    "get_resolver": (".credential_resolver", "get_resolver"),
    "known_credential_providers": (".credential_resolver", "known_credential_providers"),
    # devices_doc
    "render_devices_doc": (".devices_doc", "render_devices_doc"),
    # evidence_budget (Phase 6)
    "DEFAULT_PER_INTENT_CHAR_BUDGET": (".evidence_budget", "DEFAULT_PER_INTENT_CHAR_BUDGET"),
    "DEFAULT_TOTAL_CHAR_BUDGET": (".evidence_budget", "DEFAULT_TOTAL_CHAR_BUDGET"),
    "PER_INTENT_CHAR_BUDGET_ENV": (".evidence_budget", "PER_INTENT_CHAR_BUDGET_ENV"),
    "TOTAL_CHAR_BUDGET_ENV": (".evidence_budget", "TOTAL_CHAR_BUDGET_ENV"),
    "budget_device_evidence": (".evidence_budget", "budget_device_evidence"),
    "budget_fabric_evidence": (".evidence_budget", "budget_fabric_evidence"),
    "render_budgeted_evidence": (".evidence_budget", "render_budgeted_evidence"),
    # evidence_store (Phase 7)
    "DEFAULT_EVIDENCE_BACKEND": (".evidence_store", "DEFAULT_EVIDENCE_BACKEND"),
    "EVIDENCE_BACKEND_ENV": (".evidence_store", "EVIDENCE_BACKEND_ENV"),
    "EvidenceStore": (".evidence_store", "EvidenceStore"),
    "FileEvidenceStore": (".evidence_store", "FileEvidenceStore"),
    "SQLiteEvidenceStore": (".evidence_store", "SQLiteEvidenceStore"),
    "get_store": (".evidence_store", "get_store"),
    "save_snapshot": (".network_tools", "save_snapshot"),
    "load_latest_snapshot": (".network_tools", "load_latest_snapshot"),
    "list_snapshot_history": (".network_tools", "list_snapshot_history"),
    "prune_snapshots": (".network_tools", "prune_snapshots"),
    "diff_evidence": (".network_tools", "diff_evidence"),
    # fabric_analysis (Phase 6)
    "FABRIC_ANALYSIS_PROMPT": (".fabric_analysis", "FABRIC_ANALYSIS_PROMPT"),
    "analyze_fabric": (".fabric_analysis", "analyze_fabric"),
    "build_fabric_prompt": (".fabric_analysis", "build_fabric_prompt"),
    # fixtures
    "capture_device": (".fixtures", "capture_device"),
    "command_slug": (".fixtures", "command_slug"),
    "fixture_sender": (".fixtures", "fixture_sender"),
    "load_fixture_evidence": (".fixtures", "load_fixture_evidence"),
    "scrub_output": (".fixtures", "scrub_output"),
    # health (Phase 4: deterministic verdicts over collected evidence)
    "ALL_RULES": (".health", "ALL_RULES"),
    "BASELINE_RULES": (".health", "BASELINE_RULES"),
    "META_RULES": (".health", "META_RULES"),
    "ROLE_INVARIANT_RULES": (".health", "ROLE_INVARIANT_RULES"),
    "SEVERITY_ORDER": (".health", "SEVERITY_ORDER"),
    "evaluate_device": (".health", "evaluate_device"),
    "evaluate_fabric": (".health", "evaluate_fabric"),
    "exit_code_for_severity": (".health", "exit_code_for_severity"),
    "severity_rank": (".health", "severity_rank"),
    # inventory
    "InventoryError": (".inventory", "InventoryError"),
    "get_default_device_name": (".inventory", "get_default_device_name"),
    "get_device": (".inventory", "get_device"),
    "load_inventory": (".inventory", "load_inventory"),
    # inventory_model (Phase 3: declarative inventory schema)
    "InventoryFile": (".inventory_model", "InventoryFile"),
    "Device": (".inventory_model", "Device"),
    "Defaults": (".inventory_model", "Defaults"),
    "CredentialGroup": (".inventory_model", "CredentialGroup"),
    "Expected": (".inventory_model", "Expected"),
    "parse_inventory": (".inventory_model", "parse_inventory"),
    "load_inventory_file": (".inventory_model", "load_inventory_file"),
    "resolve_inventory_path": (".inventory_model", "resolve_inventory_path"),
    "reset_inventory_cache": (".inventory_model", "reset_inventory_cache"),
    # lab (static, credential-free device/platform data)
    "DEVICES": (".lab", "DEVICES"),
    "PLATFORMS": (".lab", "PLATFORMS"),
    "all_devices": (".lab", "all_devices"),
    "platform_for": (".lab", "platform_for"),
    # topology: derived expected counts and the fabric anomaly report
    "derive_expected": (".topology", "derive_expected"),
    "build_anomaly_report": (".topology", "build_anomaly_report"),
    "format_anomaly_report": (".topology", "format_anomaly_report"),
    "find_lldp_disagreements": (".topology", "find_lldp_disagreements"),
    "find_neighbors_not_in_inventory": (".topology", "find_neighbors_not_in_inventory"),
    "find_zero_adjacency_devices": (".topology", "find_zero_adjacency_devices"),
    "update_expected_in_yaml": (".topology", "update_expected_in_yaml"),
    # platforms: the allowlist, keyed by vendor
    "PLATFORM_INTENTS": (".platforms", "PLATFORM_INTENTS"),
    "APPROVED_COMMANDS": (".platforms", "APPROVED_COMMANDS"),
    "ALL_APPROVED_COMMANDS": (".platforms", "ALL_APPROVED_COMMANDS"),
    "DEFAULT_PLATFORM": (".platforms", "DEFAULT_PLATFORM"),
    "UnknownPlatformError": (".platforms", "UnknownPlatformError"),
    "UnsupportedIntentError": (".platforms", "UnsupportedIntentError"),
    "known_platforms": (".platforms", "known_platforms"),
    "all_intents": (".platforms", "all_intents"),
    "intents_for": (".platforms", "intents_for"),
    "commands_for": (".platforms", "commands_for"),
    "supports": (".platforms", "supports"),
    "is_approved": (".platforms", "is_approved"),
    # templates: validated, parameterized commands (Phase 5)
    "PLATFORM_TEMPLATES": (".platforms", "PLATFORM_TEMPLATES"),
    "VERB_ALLOWLIST": (".platforms", "VERB_ALLOWLIST"),
    "Template": (".platforms", "Template"),
    "ParamType": (".platforms", "ParamType"),
    "IPv4AddressParam": (".platforms", "IPv4AddressParam"),
    "IPv4PrefixParam": (".platforms", "IPv4PrefixParam"),
    "InterfaceNameParam": (".platforms", "InterfaceNameParam"),
    "BoundedIntParam": (".platforms", "BoundedIntParam"),
    "TemplateValidationError": (".platforms", "TemplateValidationError"),
    "UnknownTemplateError": (".platforms", "UnknownTemplateError"),
    "render_command": (".platforms", "render_command"),
    "supports_template": (".platforms", "supports_template"),
    "template_for": (".platforms", "template_for"),
    "known_platform_templates": (".platforms", "known_platform_templates"),
    "is_safe_rendered_command": (".platforms", "is_safe_rendered_command"),
    # network tools
    "CHECK_TOOLS": (".network_tools", "CHECK_TOOLS"),
    "STATUS_SUCCESS": (".network_tools", "STATUS_SUCCESS"),
    "STATUS_ERROR": (".network_tools", "STATUS_ERROR"),
    "STATUS_UNSUPPORTED": (".network_tools", "STATUS_UNSUPPORTED"),
    "list_devices": (".network_tools", "list_devices"),
    "run_intent": (".network_tools", "run_intent"),
    "run_template": (".network_tools", "run_template"),
    "NETTOOLS_ALLOW_ACTIVE_PROBES_ENV": (".network_tools", "NETTOOLS_ALLOW_ACTIVE_PROBES_ENV"),
    "get_device_facts": (".network_tools", "get_device_facts"),
    "check_interfaces": (".network_tools", "check_interfaces"),
    "check_bgp_neighbors": (".network_tools", "check_bgp_neighbors"),
    "check_lldp_neighbors": (".network_tools", "check_lldp_neighbors"),
    "check_isis_neighbors": (".network_tools", "check_isis_neighbors"),
    "check_sr_policies": (".network_tools", "check_sr_policies"),
    "check_fabric": (".network_tools", "check_fabric"),
    "iter_fabric": (".network_tools", "iter_fabric"),
    "get_route": (".network_tools", "get_route"),
    "get_bgp_neighbor": (".network_tools", "get_bgp_neighbor"),
    "get_interface": (".network_tools", "get_interface"),
    "get_logging": (".network_tools", "get_logging"),
    "ping_device": (".network_tools", "ping_device"),
    "traceroute_device": (".network_tools", "traceroute_device"),
    "collect_evidence": (".network_tools", "collect_evidence"),
    "evidence_intents": (".network_tools", "evidence_intents"),
    # connection timeouts and bounded retries (Phase 7)
    "NETTOOLS_CONNECT_TIMEOUT_ENV": (".network_tools", "NETTOOLS_CONNECT_TIMEOUT_ENV"),
    "NETTOOLS_READ_TIMEOUT_ENV": (".network_tools", "NETTOOLS_READ_TIMEOUT_ENV"),
    "NETTOOLS_BANNER_TIMEOUT_ENV": (".network_tools", "NETTOOLS_BANNER_TIMEOUT_ENV"),
    "NETTOOLS_COMMAND_RETRIES_ENV": (".network_tools", "NETTOOLS_COMMAND_RETRIES_ENV"),
    "NETTOOLS_RETRY_BACKOFF_ENV": (".network_tools", "NETTOOLS_RETRY_BACKOFF_ENV"),
    "DEFAULT_CONNECT_TIMEOUT_SECONDS": (".network_tools", "DEFAULT_CONNECT_TIMEOUT_SECONDS"),
    "DEFAULT_READ_TIMEOUT_SECONDS": (".network_tools", "DEFAULT_READ_TIMEOUT_SECONDS"),
    "DEFAULT_BANNER_TIMEOUT_SECONDS": (".network_tools", "DEFAULT_BANNER_TIMEOUT_SECONDS"),
    "DEFAULT_COMMAND_RETRIES": (".network_tools", "DEFAULT_COMMAND_RETRIES"),
    "DEFAULT_RETRY_BACKOFF_SECONDS": (".network_tools", "DEFAULT_RETRY_BACKOFF_SECONDS"),
    # audit log rotation (Phase 7)
    "NETTOOLS_LOG_MAX_BYTES_ENV": (".network_tools", "NETTOOLS_LOG_MAX_BYTES_ENV"),
    "NETTOOLS_LOG_BACKUP_COUNT_ENV": (".network_tools", "NETTOOLS_LOG_BACKUP_COUNT_ENV"),
    "DEFAULT_LOG_MAX_BYTES": (".network_tools", "DEFAULT_LOG_MAX_BYTES"),
    "DEFAULT_LOG_BACKUP_COUNT": (".network_tools", "DEFAULT_LOG_BACKUP_COUNT"),
    # audit actor (Phase 8, provenance only)
    "NETTOOLS_ACTOR_ENV": (".network_tools", "NETTOOLS_ACTOR_ENV"),
    # golden (pinned) snapshots and flap detection
    "GOLDEN_SNAPSHOT_FILENAME": (".network_tools", "GOLDEN_SNAPSHOT_FILENAME"),
    "save_golden_snapshot": (".network_tools", "save_golden_snapshot"),
    "load_golden_snapshot": (".network_tools", "load_golden_snapshot"),
    "detect_flaps": (".network_tools", "detect_flaps"),
    # normalization: the diff fallback when no parser exists
    "normalize_output": (".normalize", "normalize_output"),
    "strip_preamble": (".normalize", "strip_preamble"),
    "mask_volatile": (".normalize", "mask_volatile"),
    # output formatting (Phase 8): json (default)/table/summary
    "OUTPUT_FORMATS": (".output", "FORMATS"),
    "render_output": (".output", "render"),
    "render_output_table": (".output", "render_table"),
    "render_output_summary": (".output", "render_summary"),
    # parsing: structured records from raw command output
    "parse_intent": (".parsers", "parse_intent"),
    "has_parser": (".parsers", "has_parser"),
    "record_key": (".parsers", "record_key"),
    "volatile_fields": (".parsers", "volatile_fields"),
    "ParseError": (".parsers", "ParseError"),
    "PARSE_OK": (".parsers", "PARSE_OK"),
    "PARSE_UNAVAILABLE": (".parsers", "PARSE_UNAVAILABLE"),
    "PARSE_FAILED": (".parsers", "PARSE_FAILED"),
    # analysis
    "LLMAnalysisError": (".llm_analysis", "LLMAnalysisError"),
    "get_provider": (".llm_analysis", "get_provider"),
    "build_analysis_prompt": (".llm_analysis", "build_analysis_prompt"),
    "analyze_evidence": (".llm_analysis", "analyze_evidence"),
    "analyze_with_anthropic": (".llm_analysis", "analyze_with_anthropic"),
    "analyze_with_openai": (".llm_analysis", "analyze_with_openai"),
    "analyze_with_ollama": (".llm_analysis", "analyze_with_ollama"),
    "ANTHROPIC_MODEL_DEFAULT": (".llm_analysis", "ANTHROPIC_MODEL_DEFAULT"),
    "ANTHROPIC_MAX_OUTPUT_TOKENS": (".llm_analysis", "ANTHROPIC_MAX_OUTPUT_TOKENS"),
    # metrics (Phase 8)
    "NETTOOLS_METRICS_FILE_ENV": (".metrics", "NETTOOLS_METRICS_FILE_ENV"),
    "MetricsCollector": (".metrics", "MetricsCollector"),
    "render_prometheus": (".metrics", "render_prometheus"),
    "record_metrics_collection": (".metrics", "record_collection"),
    "record_metrics_verdict": (".metrics", "record_verdict"),
    "metrics_snapshot": (".metrics", "snapshot"),
    "reset_metrics": (".metrics", "reset"),
}


def __getattr__(name: str) -> Any:
    """PEP 562: resolve a lazy re-export on first access, then cache it.

    Raises the plain `AttributeError` Python's own import machinery expects
    -- critically, for a name NOT in `_LAZY` too (e.g. `health`, `cli`,
    `graph`; every actual submodule name), so `from agent_nettools import
    health` keeps working exactly as before: that statement never consulted
    this map (`health` was never one of the re-exported names above), it
    resolves via Python's own "import the submodule" fallback once this
    raises, and this function must not swallow that fallback by raising
    anything else or by matching too broadly.
    """

    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, real_name = target
    module = importlib.import_module(module_name, __name__)
    value = getattr(module, real_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))


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
