"""B-476/P2-02: a declared table of every environment variable this project
reads, and report-only validation of what is actually set.

The problem this closes
------------------------
``network_tools._int_env``/``_float_env`` (and their near-duplicates in
``notifier.py``/``evidence_budget.py``) all follow the same shape: parse the
raw string, and on any ``ValueError`` -- or any out-of-range value the local
helper happens to reject -- silently return the caller's default. That is the
right behavior for *unset*. It is the wrong behavior for *malformed*:
``NETTOOLS_READ_TIMEOUT_SECONDS=1O`` (a letter O, not a zero) silently
becomes ``10.0`` and nothing anywhere says so. A typo becomes an outage
nobody can see coming until a device stops responding and a read timeout
that was never actually configured turns out to be the default after all.

What this module is
--------------------
:data:`SETTINGS` is a second, independent read of the same environment: one
row per variable, naming its type, default, valid range/choices, and which
module actually consumes it. :func:`validate_environment` re-parses every
*set* variable exactly the way its owning module would and returns one
human-readable string per variable that is malformed or out of range.
:func:`effective_config` renders the same table as a value/source/problem
map, suitable for ``nettools config show``.

What this module deliberately is **not**
-----------------------------------------
It does not change how any of the ~20 existing call sites resolve a value.
Every ``_int_env``/``_float_env``/bare ``os.getenv`` call across
``network_tools.py``, ``notifier.py``, ``evidence_budget.py``,
``llm_analysis.py``, ``credential_resolver.py``, ``evidence_store.py``,
``metrics.py``, ``fixtures.py``, and ``inventory_model.py`` is untouched by
this change, on purpose. Rewiring every consumer through one shared
``Settings`` object -- so a malformed value could fail closed instead of
merely being reported -- is real, valuable future work, but it is a second,
separately-reviewable change: silently changing twenty call sites' fallback
behavior in a single commit is exactly the kind of change that turns one
overlooked edge case into an outage, which is the failure this module exists
to make visible in the first place. This wave only adds the report. Nothing
here raises, and nothing in ``cli.main()`` aborts startup because of it --
see ``cli.py``'s call to :func:`validate_environment` right after
``load_dotenv``.

Cross-check
-----------
``tests/test_settings.py`` scans ``src/`` and ``mcp_server/`` for every
string literal that reaches ``os.getenv(...)`` -- directly, or indirectly
through the ``*_env``-named constant/parameter/dict-entry convention this
codebase already uses everywhere (e.g. ``TOKEN_ENV = "TELEGRAM_BOT_TOKEN"``,
``model_env: str = "OPENAI_MODEL"``) -- and asserts the result matches
:data:`SETTINGS` exactly, modulo :data:`EXTERNAL_ONLY_KEYS`. That test is
load-bearing: without it, this table goes stale the first time someone adds
a new env var anywhere in the source tree.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import credential_resolver, notifier

# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Setting:
    """One environment variable's declared shape.

    ``kind`` drives validation in :func:`_problem`: ``"int"``/``"float"``
    parse and range-check; ``"bool"`` checks against a recognized spelling
    (see :data:`_BOOL_SPELLINGS` -- this catches ``NETTOOLS_ALLOW_ACTIVE_PROBES=fasle``,
    which the *code* silently treats as truthy, exactly the P2-02 shape);
    ``"enum"`` checks membership in ``choices``; ``"path"``/``"string"``/
    ``"secret"`` have no format to validate -- presence is all that is ever
    checked, and a ``"secret"``'s value is never inspected beyond that.
    """

    name: str
    kind: str  # "int" | "float" | "bool" | "enum" | "path" | "string" | "secret"
    default: object
    description: str
    module: str  # which module(s) actually call os.getenv for this name
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    secret: bool = False
    # B-493: every "bool" setting up to this point shares one convention --
    # unrecognized means *enabled* (see _BOOL_SPELLINGS below), which is a
    # footgun this module exists to surface, not to repeat. A gate that
    # exists BECAUSE something defaulted open (NETTOOLS_MCP_ALLOW_ACTIVE_PROBES,
    # NETTOOLS_ENABLE_AGENT) cannot use that convention without recreating the
    # exact hazard it was added to close: a typo would silently re-open it.
    # False (default) preserves the existing convention/message for every
    # setting declared before this field existed; True flips both the
    # direction _problem() describes AND is asserted (by the owning module's
    # own parsing) to match the runtime behavior -- see
    # test_unknown_bool_disables_settings_actually_fail_closed.
    unknown_bool_disables: bool = False


# Recognized boolean spellings, case-insensitive. Every var of kind "bool"
# declared before B-493 follows the same convention as
# network_tools._active_probes_allowed: anything in the falsy set disables
# it, and -- this is the footgun -- *every other value, including a typo, is
# silently treated as enabled*. A value outside both sets is flagged here
# even though the underlying code would accept it, because "accepted" is
# exactly the problem.
#
# B-493 added the first exception: a setting whose whole reason for existing
# is to keep something OFF by default (NETTOOLS_MCP_ALLOW_ACTIVE_PROBES,
# NETTOOLS_ENABLE_AGENT) must not reopen on a typo. Those declare
# unknown_bool_disables=True on their Setting(), and _problem() below
# describes the direction that actually applies to each one rather than
# assuming the footgun universally.
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_SPELLINGS = _BOOL_FALSE | _BOOL_TRUE

_LLM_PROVIDER_CHOICES = ("auto", "anthropic", "claude", "openai", "minimax", "ollama")
_EVIDENCE_BACKEND_CHOICES = ("files", "sqlite")

SETTINGS: tuple[Setting, ...] = (
    # -- network_tools.py: audit log, rotation, timeouts/retries, actor, probes --
    Setting(
        "NETTOOLS_LOG", "path", None,
        "JSONL audit log path for every command run. Unset disables logging entirely.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_LOG_MAX_BYTES", "int", 10 * 1024 * 1024,
        "Size-based rotation ceiling for NETTOOLS_LOG. <= 0 is a deliberate "
        "opt-out (unbounded growth), not an error, so no minimum is enforced.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_LOG_BACKUP_COUNT", "int", 5,
        "Rotated backups kept for NETTOOLS_LOG. <= 0 means 'keep none' "
        "(still bounded, just no history), not an error.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_ACTOR", "string", "",
        "Operator label attributed to NETTOOLS_LOG records (provenance only, "
        "never authorization). Falls back to the OS login user, then 'unknown'.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_CONNECT_TIMEOUT_SECONDS", "float", 10.0,
        "Netmiko SSH connect timeout.",
        "network_tools",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_READ_TIMEOUT_SECONDS", "float", 10.0,
        "Netmiko per-command read timeout; a template's own read_timeout "
        "(e.g. ping/traceroute) wins over this when set.",
        "network_tools",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_BANNER_TIMEOUT_SECONDS", "float", 15.0,
        "Netmiko SSH banner timeout.",
        "network_tools",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_COMMAND_RETRIES", "int", 2,
        "Total attempts (not extra retries) for a transient connection/command "
        "failure. Never applied to a command the allowlist refused.",
        "network_tools",
        minimum=1,
        maximum=10,
    ),
    Setting(
        "NETTOOLS_RETRY_BACKOFF_SECONDS", "float", 0.5,
        "Base backoff between retry attempts.",
        "network_tools",
        minimum=0,
        maximum=60,
    ),
    Setting(
        "NETTOOLS_ALLOW_ACTIVE_PROBES", "bool", True,
        "Gates the ping/traceroute templates, the only commands here that "
        "generate device-side traffic. Default enabled.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_EVIDENCE_DIR", "path", "evidence",
        "Where timestamped/golden evidence snapshots are stored (file backend).",
        "evidence_store, network_tools",
    ),
    # -- credential_resolver.py --
    Setting(
        "NETTOOLS_CREDENTIAL_PROVIDER", "enum", "env",
        "How a credential group's named env vars resolve to a secret: 'env' "
        "(value is the secret) or 'file' (value is a path to it).",
        "credential_resolver",
        choices=credential_resolver.known_credential_providers(),
    ),
    # -- evidence_budget.py --
    Setting(
        "NETTOOLS_EVIDENCE_PER_INTENT_CHARS", "int", 4000,
        "Per-intent character ceiling before an evidence section is "
        "truncated in the middle for a fabric-wide analysis prompt.",
        "evidence_budget",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_EVIDENCE_TOTAL_CHARS", "int", 40000,
        "Ceiling across an entire fabric-wide evidence bundle, enforced "
        "after per-intent truncation.",
        "evidence_budget",
        minimum=1,
    ),
    # -- evidence_store.py --
    Setting(
        "NETTOOLS_EVIDENCE_BACKEND", "enum", "files",
        "Evidence storage backend: 'files' (JSON per snapshot) or 'sqlite'.",
        "evidence_store",
        choices=_EVIDENCE_BACKEND_CHOICES,
    ),
    Setting(
        "NETTOOLS_MCP_SURFACE", "enum", "classic",
        "Which MCP tool surface the server registers: 'classic' (the full "
        "per-function set) or 'staged' (five stage-shaped tools plus a probe, "
        "B-479 -- both exist so the selection A/B stays measurable).",
        "mcp_server.server",
        choices=("classic", "staged"),
    ),
    # B-493: MCP-EXPERIMENT.md 12.3 measured a 31B model following a clean
    # descent with an UNPROMPTED get_lab_ping -- the first time a model
    # generated lab traffic without being asked. The engineering was sound
    # (the flow it had just run covers the control plane; "reach" can mean
    # the data plane) but nothing on this surface refused it:
    # NETTOOLS_ALLOW_ACTIVE_PROBES defaults to enabled, and B-473's
    # open_world_hint/title annotations are signalling, not enforcement, by
    # their own comment in server.py. A human typing `nettools ping` has
    # asked for the probe explicitly; an MCP client is a model DECIDING to
    # generate traffic on its own, during whatever it is investigating --
    # possibly a live incident. That is a different trust boundary from the
    # CLI's, so it gets its own gate, defaulting to the opposite value.
    # Deliberately unknown_bool_disables=True: a setting that exists to keep
    # something off by default must not reopen on a typo the way
    # NETTOOLS_ALLOW_ACTIVE_PROBES itself does.
    Setting(
        "NETTOOLS_MCP_ALLOW_ACTIVE_PROBES", "bool", False,
        "Whether an MCP client may invoke get_lab_ping/get_lab_traceroute "
        "(classic surface) or probe_lab (staged surface). Default DISABLED "
        "-- the opposite of NETTOOLS_ALLOW_ACTIVE_PROBES, which still gates "
        "the CLI and defaults enabled. A refusal here is a classified, "
        "structured error (mcp_server/boundary.py ERROR_KINDS), never a "
        "silent no-op.",
        "mcp_server.server",
        unknown_bool_disables=True,
    ),
    # B-512 (Job 2): a THIRD MCP-only gate, for the external-source tools
    # (get_lab_logs/get_lab_interface_rate_history/
    # get_lab_isis_adjacency_history) -- see mcp_server/server.py's own
    # comment above `_external_source_tool` for the full argument. Default
    # ENABLED, the opposite posture from NETTOOLS_MCP_ALLOW_ACTIVE_PROBES:
    # unlike an active probe, a model cannot choose *where* these calls go
    # (the URL is operator-configured, never a tool parameter), so the risk
    # that gate exists to close does not apply here. unknown_bool_disables
    # is left at its default (False) deliberately -- this is NOT a "must
    # stay off" gate the way NETTOOLS_MCP_ALLOW_ACTIVE_PROBES/
    # NETTOOLS_ENABLE_AGENT are, so an unrecognized value follows the
    # ordinary convention (resolves toward the documented default, enabled)
    # rather than the B-493 exception.
    Setting(
        "NETTOOLS_MCP_ALLOW_EXTERNAL_SOURCES", "bool", True,
        "Whether an MCP client may invoke get_lab_logs/"
        "get_lab_interface_rate_history/get_lab_isis_adjacency_history "
        "(the Loki/Prometheus external-source tools). Default ENABLED -- "
        "the opposite of NETTOOLS_MCP_ALLOW_ACTIVE_PROBES, because the "
        "destination is operator-configured (NETTOOLS_LOKI_URL/"
        "NETTOOLS_PROMETHEUS_URL), never a caller-chosen value, so there is "
        "no traffic-steering risk to gate closed by default. A refusal here "
        "is a classified, structured error, never a silent no-op.",
        "mcp_server.server",
    ),
    # -- inventory_model.py --
    Setting(
        "NETTOOLS_INVENTORY", "path", None,
        "Path to the declarative inventory YAML file. Unset resolves to "
        "./inventory/lab.yaml, then a path derived from the installed package.",
        "inventory_model",
    ),
    # -- llm_analysis.py --
    Setting(
        "NETTOOLS_LLM_FALLBACKS", "bool", True,
        "Requests server-side refusal fallbacks on Anthropic calls, only for "
        "models in the Opus-5/Fable-5/Mythos-5 family. No effect elsewhere.",
        "llm_analysis",
    ),
    Setting(
        "LLM_PROVIDER", "enum", "auto",
        "Which LLM backend answers analysis/report prompts. 'auto' picks "
        "Anthropic then OpenAI by whichever API key is present; ollama and "
        "minimax are never auto-selected.",
        "llm_analysis",
        choices=_LLM_PROVIDER_CHOICES,
    ),
    Setting(
        "ANTHROPIC_API_KEY", "secret", None,
        "Anthropic API key. Required when LLM_PROVIDER=anthropic (or 'auto' "
        "resolves to it).",
        "llm_analysis, agent_loop, fabric_analysis",
        secret=True,
    ),
    Setting(
        "ANTHROPIC_MODEL", "string", "claude-opus-5",
        "Anthropic model id. This repo's own .env may pin an older model on "
        "purpose; the default here only applies when unset.",
        "llm_analysis, agent_loop, fabric_analysis",
    ),
    # B-488: `nettools agent` is a free-form, model-driven tool-calling loop
    # -- philosophically at odds with this project's central claim that the
    # model only navigates a menu and never synthesises a diagnosis, and it
    # is Anthropic-only and, per this README, unreliable on local models.
    # Its existence as an apparent peer of `investigate` undercuts that trust
    # story for anyone who meets it first. Kept, but opt-in: default
    # disabled, and unknown_bool_disables=True for the same reason as
    # NETTOOLS_MCP_ALLOW_ACTIVE_PROBES -- a gate that exists to keep
    # something off by default must not reopen on a typo.
    Setting(
        "NETTOOLS_ENABLE_AGENT", "bool", False,
        "Opt-in for `nettools agent`. Default DISABLED; the command refuses "
        "to run and explains what it is, why it is gated, and points at "
        "`nettools investigate` (the deterministic alternative) until this "
        "is set to a truthy value.",
        "cli",
        unknown_bool_disables=True,
    ),
    Setting(
        "OPENAI_API_KEY", "secret", None,
        "OpenAI API key. Required when LLM_PROVIDER=openai.",
        "llm_analysis",
        secret=True,
    ),
    Setting(
        "OPENAI_MODEL", "string", "gpt-5.5",
        "OpenAI model id (Responses API).",
        "llm_analysis",
    ),
    Setting(
        "MINIMAX_API_KEY", "secret", None,
        "MiniMax API key. Required when LLM_PROVIDER=minimax; never selected "
        "by 'auto'.",
        "llm_analysis",
        secret=True,
    ),
    Setting(
        "MINIMAX_BASE_URL", "string", "https://api.minimax.io/v1",
        "MiniMax endpoint base URL (serves the OpenAI Responses API).",
        "llm_analysis",
    ),
    Setting(
        "MINIMAX_MODEL", "string", "MiniMax-M3",
        "MiniMax model id.",
        "llm_analysis",
    ),
    Setting(
        "OLLAMA_HOST", "string", "http://localhost:11434",
        "Local Ollama server base URL. Used when LLM_PROVIDER=ollama.",
        "llm_analysis",
    ),
    Setting(
        "OLLAMA_MODEL", "string", "ornith:9b-q8_0",
        "Local Ollama model tag.",
        "llm_analysis",
    ),
    # -- metrics.py --
    Setting(
        "NETTOOLS_METRICS_FILE", "path", None,
        "Opts `nettools metrics` counters into on-disk persistence across "
        "separate CLI invocations. Unset keeps metrics in-memory only.",
        "metrics",
    ),
    # -- ledger.py --
    Setting(
        "NETTOOLS_DIAGNOSIS_LEDGER_FILE", "path", None,
        "Opts investigation diagnoses into an on-disk accuracy ledger (B-485). "
        "Unset means diagnoses are recorded in memory only and lost at process "
        "exit, so the ledger accumulates nothing across invocations.",
        "ledger",
    ),
    # -- ticket.py --
    Setting(
        "NETTOOLS_TICKET_DIR", "path", "tickets",
        "Directory holding one append-only markdown ticket per interaction "
        "(B-446) -- the escalation-grade run bundle docs/OPERATIONS-REVIEW.md "
        "asks for: question, intent, tool timeline, device interactions, "
        "evidence provenance, context footprint, and the deterministic "
        "answer, with an outcome slot that stays 'unknown' until a human "
        "fills it in. Unlike NETTOOLS_DIAGNOSIS_LEDGER_FILE this has a real "
        "default rather than opt-in-only -- a ticket is the deliverable, not "
        "an accumulating corpus. A write failure degrades "
        "(TicketWriteResult.persisted=False) and never fails the "
        "investigation that triggered it.",
        "ticket",
    ),
    # -- fixtures.py --
    Setting(
        "NETTOOLS_FIXTURE_DIR", "path", "tests/fixtures",
        "Root directory `--from-fixtures`/capture replay reads/writes captured "
        "command output under. Not documented in .env.example or CLAUDE.md as "
        "of B-476 -- a real gap this table closes.",
        "fixtures",
    ),
    # -- notifier.py --
    Setting(
        "NETTOOLS_NOTIFIER", "enum", "none",
        "Outbound report delivery channel. 'none' (default) makes --notify a "
        "no-op; an unrecognized value fails closed at get_notifier() time.",
        "notifier",
        choices=notifier.known_notifiers(),
    ),
    Setting(
        "NETTOOLS_NOTIFIER_TIMEOUT_SECONDS", "float", 10.0,
        "HTTP timeout for a notifier send (e.g. the Telegram API call).",
        "notifier",
        minimum=0.001,
    ),
    Setting(
        "TELEGRAM_BOT_TOKEN", "secret", None,
        "Telegram bot token. Required for the telegram notifier; redacted "
        "from every log line, exception, and audit record it could reach.",
        "notifier",
        secret=True,
    ),
    Setting(
        "TELEGRAM_CHAT_ID", "string", "",
        "Comma-separated allowlist of chat ids reports are sent to. Empty or "
        "unset means send to nobody, never 'send to everybody'.",
        "notifier",
    ),
    Setting(
        "NETTOOLS_TELEGRAM_MAX_CHARS", "int", 3500,
        "Refuse (never truncate) a rendered report over this many characters. "
        "Telegram's own hard limit is 4096.",
        "notifier",
        minimum=1,
        maximum=4096,
    ),
    # -- logs_loki.py (Stage-2 M5): the read-only Loki log adapter --
    Setting(
        "NETTOOLS_LOKI_URL", "string", "http://172.20.250.103:3100",
        "Base URL of the Loki instance `logs_loki.run_named_query` queries. "
        "The default is the lab's management-network address as measured "
        "2026-08-15/18 (discovery-loki.md) -- a container IP, not a "
        "guaranteed-stable service address; override it explicitly in any "
        "environment where that measurement does not hold.",
        "logs_loki",
    ),
    Setting(
        "NETTOOLS_LOKI_TIMEOUT_SECONDS", "float", 10.0,
        "HTTP timeout for a Loki query_range call.",
        "logs_loki",
        minimum=0.001,
    ),
    # -- metrics_prometheus.py (Stage-2 M5b): the read-only Prometheus
    # metrics adapter -- the temporal evidence axis's second source.
    Setting(
        "NETTOOLS_PROMETHEUS_URL", "string", "http://172.20.250.102:9090",
        "Base URL of the Prometheus instance "
        "`metrics_prometheus.run_named_query` queries. The default is the "
        "lab's management-network address as measured 2026-08-15/19 "
        "(discovery-alerting.md) -- a container IP, not a guaranteed-stable "
        "service address; override it explicitly in any environment where "
        "that measurement does not hold.",
        "metrics_prometheus",
    ),
    Setting(
        "NETTOOLS_PROMETHEUS_TIMEOUT_SECONDS", "float", 10.0,
        "HTTP timeout for a Prometheus query_range/series call.",
        "metrics_prometheus",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_PROMETHEUS_EXISTENCE_LOOKBACK_SECONDS", "int", 7 * 24 * 3600,
        "How far back `metrics_prometheus` looks (via /api/v1/series) to "
        "decide whether a series has ever been observed at all, when a "
        "primary range query returns zero points -- the check that tells "
        "'this series never existed' apart from 'it stopped being scraped'. "
        "Defaults to Prometheus's own measured retention window (1 week, "
        "/api/v1/status/runtimeinfo) -- looking back further than the "
        "source retains cannot distinguish the two cases.",
        "metrics_prometheus",
        minimum=1,
    ),
    # -- netbox.py (Stage-2 M3b): the read-only-by-default NetBox inventory
    # collector. NetBox is DERIVED from parsed device evidence, never
    # authored (stage-2-architecture.md §4) -- see the module docstring.
    Setting(
        "NETBOX_URL", "string", None,
        "Base URL of the NetBox instance write_records() pushes into (e.g. "
        "http://netbox:8080 from inside the lab's docker network, or the "
        "Caddy-proxied path). Only read when write_records(dry_run=False) "
        "is actually called -- build_records()/describe_writes() never need it.",
        "netbox",
    ),
    Setting(
        "NETBOX_TOKEN", "secret", None,
        "NetBox API token. Only read when write_records(dry_run=False) is "
        "actually called; never logged, never included in any exception "
        "message this module raises.",
        "netbox",
        secret=True,
    ),
    # unknown_bool_disables=True for the same reason as NETTOOLS_ENABLE_AGENT/
    # NETTOOLS_MCP_ALLOW_ACTIVE_PROBES: this gate exists to keep a real write
    # OFF by default, and a typo must not silently reopen it. It is the
    # SECOND gate write_records() checks -- dry_run=False alone is not
    # enough -- so importing or dry-running netbox.py can never mutate
    # anything regardless of how this variable is spelled.
    Setting(
        "NETTOOLS_NETBOX_WRITE_ENABLED", "bool", False,
        "Opt-in for a real (non-dry-run) NetBox write via netbox.write_records(). "
        "Default DISABLED; dry_run=True (the default) never checks this at all.",
        "netbox",
        unknown_bool_disables=True,
    ),
    # -- netbox.py, the read half (this task): the two MCP tools
    # (get_lab_netbox_inventory/get_lab_netbox_topology, via
    # netbox.run_named_read) read the SAME NETBOX_URL/NETBOX_TOKEN above --
    # never a second credential pair -- so only the read-specific timeout is
    # new here.
    Setting(
        "NETTOOLS_NETBOX_TIMEOUT_SECONDS", "float", 10.0,
        "HTTP timeout for one NetBox read (netbox.run_named_read). Same "
        "default as NETTOOLS_LOKI_TIMEOUT_SECONDS/"
        "NETTOOLS_PROMETHEUS_TIMEOUT_SECONDS.",
        "netbox",
        minimum=0.001,
    ),
    # -- credential_resolver.py, via inventory/lab.yaml's credential group --
    # These three are the *conventional* names this lab's own inventory.yaml
    # configures (username_env/password_env/ssh_keyfile_env) -- see
    # EXTERNAL_ONLY_KEYS below for why a source scan can never discover them.
    Setting(
        "DEVICE_USERNAME", "string", None,
        "Device SSH username. Required for any command that reaches a device. "
        "The actual env var name is configurable per credential group in "
        "inventory/lab.yaml; this is the name this lab's own inventory uses.",
        "credential_resolver (via inventory YAML)",
    ),
    Setting(
        "DEVICE_PASSWORD", "secret", None,
        "Device SSH password. Required unless DEVICE_SSH_KEYFILE is set, in "
        "which case it becomes an optional key passphrase.",
        "credential_resolver (via inventory YAML)",
        secret=True,
    ),
    Setting(
        "DEVICE_SSH_KEYFILE", "path", None,
        "Optional SSH private key path, used instead of a password.",
        "credential_resolver (via inventory YAML)",
    ),
)

# Names that can never be found by scanning src/ or mcp_server/ for a string
# literal reaching os.getenv (directly, or via the *_env naming convention):
# they are read as os.getenv(group.username_env) etc., where the env var
# *name itself* is data parsed from inventory/lab.yaml at runtime, not a
# Python literal anywhere in the source tree. tests/test_settings.py treats
# this set, not a blanket exemption, as the only allowed gap between the
# source scan and SETTINGS.
EXTERNAL_ONLY_KEYS: frozenset[str] = frozenset(
    {"DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE"}
)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _problem(setting: Setting, raw: str) -> str | None:
    """Return a human-readable problem with ``raw`` for ``setting``, or ``None``.

    Never raises. ``raw`` is already known non-empty (callers only invoke
    this once a var is confirmed set); a "secret" is never inspected here
    beyond having already been called with a non-empty value -- its content
    never appears in the returned string.
    """

    text = raw.strip()

    if setting.kind == "int":
        try:
            value: float = int(text)
        except ValueError:
            return (
                f"{setting.name}={raw!r} is not a valid integer (expected a "
                f"whole number, e.g. {setting.default!r})."
            )
        return _range_problem(setting, value)

    if setting.kind == "float":
        try:
            value = float(text)
        except ValueError:
            return (
                f"{setting.name}={raw!r} is not a valid number (expected a "
                f"decimal number, e.g. {setting.default!r})."
            )
        return _range_problem(setting, value)

    if setting.kind == "bool":
        if text.lower() not in _BOOL_SPELLINGS:
            if setting.unknown_bool_disables:
                direction = (
                    "The code treats any unrecognized value as disabled (it "
                    "fails closed), so this typo silently leaves the setting "
                    "*off* rather than on"
                )
            else:
                direction = (
                    "The code treats any unrecognized value as enabled, so "
                    "this typo silently turns the setting *on* rather than "
                    "off"
                )
            return (
                f"{setting.name}={raw!r} is not a recognized boolean spelling "
                f"(expected one of {', '.join(sorted(_BOOL_SPELLINGS))}). "
                f"{direction}."
            )
        return None

    if setting.kind == "enum":
        choices = setting.choices or ()
        if text.lower() not in {choice.lower() for choice in choices}:
            return (
                f"{setting.name}={raw!r} is not one of the recognized values "
                f"({', '.join(choices)})."
            )
        return None

    # "path" / "string" / "secret": no format to validate. Presence is
    # everything these kinds ever check.
    return None


def _range_problem(setting: Setting, value: float) -> str | None:
    import math

    # NaN/inf slip past every comparison below (both `< min` and `> max` are
    # False for NaN), which is precisely the "typo becomes an outage nobody
    # sees" case this validator exists to catch (2026-08-18 review).
    if not math.isfinite(value):
        return f"{setting.name}={value!r} is not a finite number."
    if setting.minimum is not None and value < setting.minimum:
        return (
            f"{setting.name}={value!r} is below the minimum allowed value "
            f"({setting.minimum})."
        )
    if setting.maximum is not None and value > setting.maximum:
        return (
            f"{setting.name}={value!r} is above the maximum allowed value "
            f"({setting.maximum})."
        )
    return None


def validate_environment() -> list[str]:
    """Return one human-readable problem per malformed/out-of-range env value.

    Empty list means clean. Unset variables are never a problem here -- that
    is what "default" means -- and this never raises, regardless of how
    bizarre a value is. A secret's value is read only far enough to know it
    is non-empty; :func:`_problem` never inspects it further, and no message
    this function returns can contain one.
    """

    problems: list[str] = []
    for setting in SETTINGS:
        raw = os.environ.get(setting.name)
        if raw is None or not raw.strip():
            continue
        problem = _problem(setting, raw)
        if problem:
            problems.append(problem)
    return problems


def effective_config() -> dict[str, dict[str, object]]:
    """``{name: {value, source, problem}}`` for every declared setting.

    ``source`` is ``"env"`` when the variable is set (even if malformed) and
    ``"default"`` otherwise. A ``secret`` setting's ``value`` is always
    ``"[SET]"``/``"[UNSET]"`` -- never the actual value, set or default.
    """

    result: dict[str, dict[str, object]] = {}
    for setting in SETTINGS:
        raw = os.environ.get(setting.name)
        present = raw is not None and bool(raw.strip())

        if setting.secret:
            value: object = "[SET]" if present else "[UNSET]"
        elif present:
            value = raw
        else:
            value = setting.default

        result[setting.name] = {
            "value": value,
            "source": "env" if present else "default",
            "problem": _problem(setting, raw) if present else None,
        }
    return result
