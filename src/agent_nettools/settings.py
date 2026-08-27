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
from ._env import _BOOL_FALSE, _BOOL_TRUE

# --------------------------------------------------------------------------- #
# The declaration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Setting:
    """One environment variable's declared shape.

    ``kind`` drives validation in :func:`_problem`: ``"int"``/``"float"``
    parse and range-check; ``"bool"`` checks against a recognized spelling
    (see :data:`_BOOL_SPELLINGS`). The original example here was
    ``NETTOOLS_ALLOW_ACTIVE_PROBES=fasle``, "which the *code* silently treats
    as truthy" -- true when written (the P2-02 shape), no longer true since
    EER-008a made that gate fail closed. The validator still earns its keep:
    it reports the typo either way, and a gate that fails closed on a typo is
    still a gate behaving differently than the operator wrote down;
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
#
# EER-015: `_BOOL_FALSE`/`_BOOL_TRUE` themselves moved to `_env.py`.
# mcp_server/server.py declared the truthy half of this same pair twice
# more, once per MCP-only gate (`_MCP_ACTIVE_PROBES_TRUTHY`,
# `_MCP_EXTERNAL_SOURCES_TRUTHY`) -- four definitions of two literal tuples,
# now one each; see `_env.py`'s docstring for the full accounting.
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
        "generate device-side traffic. Default enabled -- but an unrecognised "
        "value that IS set fails closed (disabled) since EER-008a. It used to "
        "do the opposite: the gate read `value not in {falsy}`, so `=fasle` "
        "silently ENABLED device-side traffic. Default-on and "
        "typo-fails-closed are independent properties; this has both.",
        "network_tools",
        unknown_bool_disables=True,
    ),
    Setting(
        "NETTOOLS_SSH_KNOWN_HOSTS", "path", "~/.config/nettools/known_hosts",
        "Trust store for SSH host-key verification (EER-002) -- netmiko's "
        "alt_key_file, loaded with alt_host_keys=True. Deliberately never the "
        "operator's own ~/.ssh/known_hosts (system_host_keys is always False), "
        "which would silently inherit unrelated trust. The only writer of a "
        "real entry is scripts/enroll_host_keys.py, run deliberately.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_SSH_STRICT", "bool", True,
        "Whether an unrecognised SSH host key is rejected rather than "
        "silently trusted (netmiko ssh_strict). Fail-closed in BOTH "
        "directions: unset means enabled, and an unrecognised set value also "
        "means enabled. Note this is why unknown_bool_disables is NOT set "
        "here -- for this gate 'enabled' is the secure state, so 'unknown "
        "disables' would be exactly backwards.",
        "network_tools",
    ),
    Setting(
        "NETTOOLS_EVIDENCE_DIR", "path", "evidence",
        "Where timestamped/golden evidence snapshots are stored (file backend).",
        "evidence_store, network_tools",
    ),
    # -- admission.py (B-408/B-444): admission control for concurrent
    # collection, and active-probe rate budgeting. See admission.py's module
    # docstring for the measured numbers behind the two concurrency defaults
    # -- they are the boundary this lab's own data supports, not round
    # numbers; the two probe-budget defaults below them are a stated policy
    # choice instead, since there is no failure threshold to discover by
    # sending more ICMP at the lab.
    Setting(
        "NETTOOLS_ADMISSION_DIR", "path", "admission",
        "Directory holding the flock lock files admission.py uses to gate "
        "concurrent collection and active-probe rate budgets. Deliberately "
        "filesystem-based, not in-memory: event_routing.py's own routed "
        "investigations are separate OS processes, so only a cross-process "
        "primitive actually protects against an event storm.",
        "admission",
    ),
    Setting(
        "NETTOOLS_MAX_CONCURRENT_PER_DEVICE", "int", 1,
        "Concurrent live collections admitted against one device at once. "
        "Measured 2026-08-19: 3+ concurrent SSH sessions to one device in "
        "this lab produced real connection failures roughly a third of the "
        "time (banner-read errors, connection resets) that a retry budget "
        "could not reliably clear while contention persisted.",
        "admission",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_MAX_CONCURRENT_FABRIC", "int", 4,
        "Concurrent live collections admitted fabric-wide at once, "
        "independent of and in addition to the per-device cap. Measured "
        "2026-08-19: 2 and 4 concurrent collections against different "
        "devices completed in a tight ~1.8s band with zero failures; 8 and "
        "9 also completed with zero failures but with tail latency up to "
        "~20x that band. 4 is the widest measured value that stayed inside "
        "the clean band, not an extrapolation past what this 9-device lab "
        "could exercise.",
        "admission",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_ADMISSION_WAIT_SECONDS", "float", 0.0,
        "How long a caller waits for a busy admission slot before being "
        "refused. Default 0 -- refuse immediately rather than queue, since "
        "a second concurrent read of the same device buys nothing over "
        "waiting for the first to finish and report.",
        "admission",
        minimum=0,
    ),
    Setting(
        "NETTOOLS_MAX_ACTIVE_PROBES_PER_DEVICE", "int", 3,
        "Active probes (ping/traceroute) admitted against one device per "
        "NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS. A stated policy default, not "
        "a measured ceiling -- see NETTOOLS_MAX_CONCURRENT_PER_DEVICE for "
        "the distinction. Checked only when NETTOOLS_ALLOW_ACTIVE_PROBES "
        "already allows probes at all; this is the rate on top of that "
        "on/off gate, not a replacement for it.",
        "admission",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_MAX_ACTIVE_PROBES_FABRIC", "int", 10,
        "Active probes admitted fabric-wide per "
        "NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS, independent of and in "
        "addition to the per-device probe budget. A stated policy default, "
        "not a measured ceiling.",
        "admission",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_ACTIVE_PROBE_WINDOW_SECONDS", "float", 60.0,
        "Sliding-window width the two active-probe budgets above are "
        "counted over.",
        "admission",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_MAX_EVENT_RUNS_PER_DEVICE", "int", 6,
        "Maximum live event-woken model runs started for one device within "
        "NETTOOLS_EVENT_RUN_WINDOW_SECONDS. Cross-process rate limit.",
        "admission",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_MAX_EVENT_RUNS_FABRIC", "int", 30,
        "Maximum live event-woken model runs started fabric-wide within "
        "NETTOOLS_EVENT_RUN_WINDOW_SECONDS. Cross-process rate limit.",
        "admission",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_EVENT_RUN_WINDOW_SECONDS", "float", 3600.0,
        "Sliding-window duration for the live event-run budgets.",
        "admission",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_EVENT_IDEMPOTENCY_WINDOW_SECONDS", "float", 3600.0,
        "Replay-suppression window for one stable live event ID. The first "
        "receiver claims it; later deliveries are refused before MCP/model use.",
        "admission",
        minimum=0.001,
    ),
    Setting(
        "NETTOOLS_MAX_EVENT_AGE_SECONDS", "float", 300.0,
        "Maximum age of a Loki-ingested event allowed to start a model/MCP "
        "run. Direct receiver events are immediate; malformed Loki ingest "
        "timestamps are refused.",
        "event_agent",
        minimum=0,
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
    Setting(
        "NETTOOLS_MCP_TRANSPORT", "enum", "stdio",
        "MCP transport: 'stdio' for local subprocess clients, 'streamable-http' "
        "for native HTTP clients, or 'sse' for compatible legacy clients. HTTP "
        "transports require NETTOOLS_MCP_HTTP_BEARER_TOKEN.",
        "mcp_server.server",
        choices=("stdio", "streamable-http", "sse"),
    ),
    Setting(
        "NETTOOLS_MCP_HOST", "string", "127.0.0.1",
        "Bind address for native MCP HTTP transports. Use a LAN/VPN address only "
        "with a bearer token and trusted network controls.",
        "mcp_server.server",
    ),
    Setting(
        "NETTOOLS_MCP_PORT", "int", 8000,
        "TCP port for native MCP HTTP transports.",
        "mcp_server.server",
        minimum=1,
        maximum=65535,
    ),
    Setting(
        "NETTOOLS_MCP_HTTP_BEARER_TOKEN", "secret", None,
        "Bearer token required before any MCP HTTP request reaches the tool surface. "
        "It is unused by the default stdio transport and never logged.",
        "mcp_server.server",
        secret=True,
    ),
    Setting(
        "NETTOOLS_PROCEDURE_APPROVAL_KEY", "secret", None,
        "Single-operator shared key (minimum 32 bytes) for dry-run procedure approval receipts. "
        "This is not per-user identity, RBAC, or a router-write authorization.",
        "procedure_workflow",
        secret=True,
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
        "is a classified, structured error, never a silent no-op. "
        "An unrecognised value that IS set fails closed (disabled) since "
        "EER-008b -- the default-on applies to unset, not to a typo.",
        "mcp_server.server",
        unknown_bool_disables=True,
    ),
    Setting(
        "NETTOOLS_MCP_OBJECT_EVIDENCE_TTL_SECONDS", "float", 10.0,
        "Maximum age of evidence used to validate MCP BGP-peer and interface "
        "identifiers. Set 0 for always-live validation during active "
        "troubleshooting; use a longer value in production to reduce repeated "
        "device collections.",
        "mcp_server.server",
        minimum=0,
    ),
    Setting(
        "NETTOOLS_REASONING_GATE_MODE", "enum", "off",
        "B-114 typed narrowing observation mode: 'off' (default) or 'shadow'. "
        "Shadow records code-enumerated candidates but cannot dispatch collection "
        "or alter a deterministic finding; active narrowing is unsupported.",
        "narrowing_pass",
        choices=("off", "shadow"),
    ),
    # -- B-710 measurement-only OpenRouter lane --
    Setting(
        "OPENROUTER_API_KEY", "string", None,
        "API key for the B-710 second-provider adversarial measurement. It is "
        "not used by normal provider selection or production event handling.",
        "llm_analysis",
    ),
    Setting(
        "OPENROUTER_BASE_URL", "string", "https://openrouter.ai/api/v1",
        "OpenRouter's OpenAI-compatible endpoint used only by the B-710 "
        "measurement caller.",
        "llm_analysis",
    ),
    Setting(
        "OPENROUTER_MODEL", "string", "deepseek/deepseek-v4-flash-vision-exp",
        "OpenRouter model used by the B-710 second-provider measurement lane.",
        "llm_analysis",
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
        "NETTOOLS_LLM_TIMEOUT_SECONDS", "float", 60.0,
        "Timeout (seconds) applied to every LLM provider client/call in "
        "llm_analysis.py (Anthropic, OpenAI, MiniMax, Ollama) when a caller "
        "does not pass its own explicit timeout -- `nettools agent` passes "
        "its per-turn remaining wall-clock budget instead. Replaces Ollama's "
        "previous hardcoded timeout=120, which nothing could configure: "
        "every other outbound call in this project already had a settable "
        "deadline, and the model calls were the one path that could block "
        "for an unbounded time (EER-010).",
        "llm_analysis",
        minimum=0.001,
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
    # -- health.py (B-483): the maintenance-window/silence layer's own table.
    # Read once, inside health.py's own verdict-production helpers
    # (`evaluate_device_with_silences`/`evaluate_fabric_with_silences`), not
    # by a bare `os.getenv` scattered elsewhere -- the same "one place reads
    # it" discipline every other opt-in path variable here follows.
    Setting(
        "NETTOOLS_SILENCE_FILE", "path", None,
        "Declarative YAML table of active maintenance windows (B-483). "
        "`evaluate_device_with_silences`/`evaluate_fabric_with_silences` load "
        "and apply it automatically. Unset means no silences are loaded -- "
        "every finding pages exactly as if the feature did not exist, never "
        "an error. A silenced finding always stays in the verdict's "
        "`findings`, tagged `silenced: true`, never removed -- see "
        "health.py's module docstring.",
        "health",
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
    Setting(
        "NETTOOLS_INCIDENT_DIR", "path", "~/.local/state/agent-nettools/incidents",
        "Global incident-ID counter directory. Independent from ticket artifact "
        "directories so campaign and production tickets share one readable "
        "operator-facing incident sequence.",
        "ticket",
    ),
    # -- session_memory.py --
    Setting(
        "NETTOOLS_SESSION_MEMORY_DIR", "path", "session_memory",
        "Directory holding one small JSON file per interactive session (B-407) "
        "-- a pointer to the device/subject/flow/ticket of that session's most "
        "recent `nettools investigate` turn, so a later 'it' in the same "
        "session can resolve without the human restating it. Overwritten each "
        "turn, never appended -- this is not a history. Same precedence as "
        "NETTOOLS_TICKET_DIR: a real default rather than opt-in-only, because "
        "the whole point is surviving between separate `nettools` process "
        "invocations. A write failure degrades (RecordWriteResult.persisted="
        "False) and never fails the investigation that triggered it.",
        "session_memory",
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
        "NETTOOLS_EVENT_NOTIFY", "bool", False,
        "Enable the B-707 event ticket notification lifecycle. Default off: "
        "a promoted trigger remains investigation-only until explicitly enabled.",
        "event_notification",
    ),
    Setting(
        "NETTOOLS_EVENT_NOTIFICATION_STATE_FILE", "path", "~/.local/state/agent-nettools/event-notifications.json",
        "Persistent event lifecycle state. Defaults outside the checkout; "
        "an explicit path is supported for managed deployments.",
        "event_notification",
    ),
    Setting(
        "NETTOOLS_TELEGRAM_LIVE_CARD", "bool", False,
        "Enable the Version 2 editable Telegram investigation card. Default off; "
        "the existing reply-chain lifecycle remains active until test-chat acceptance.",
        "event_notification",
    ),
    Setting(
        "NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS", "string", "",
        "Comma-separated test-chat subset for editable Telegram cards. Values outside "
        "TELEGRAM_CHAT_ID are ignored rather than widening delivery destinations.",
        "event_notification",
    ),
    Setting(
        "NETTOOLS_TELEGRAM_SHOW_REASONING_TAIL", "bool", False,
        "Testing-only non-authoritative Telegram summary tail derived from deterministic "
        "receipt fields. Default off; model and raw device prose are never eligible.",
        "event_notification",
    ),
    Setting(
        "NETTOOLS_TELEGRAM_WEBHOOK_SECRET", "secret", None,
        "Telegram inbound webhook secret. Required by the local callback handler; "
        "never log or send it through Telegram.",
        "telegram_operator",
        secret=True,
    ),
    Setting(
        "NETTOOLS_OPERATOR_POLICY_FILE", "path", "~/.config/agent-nettools/telegram-operators.yaml",
        "Versioned Telegram user-ID to role policy. Unknown or disabled users are refused.",
        "telegram_operator",
    ),
    Setting(
        "NETTOOLS_TELEGRAM_WEBHOOK_ENABLED", "bool", False,
        "Enable authenticated Telegram callback handling. Default off until a public HTTPS endpoint is approved.",
        "telegram_operator",
    ),
    Setting(
        "NETTOOLS_OPERATOR_AUDIT_LOG", "path", "~/.local/state/agent-nettools/operator-actions.jsonl",
        "Append-only audit log for authorized and refused Telegram operator actions.",
        "telegram_operator",
    ),
    Setting(
        "NETTOOLS_EVENT_DB_PATH", "path", "~/.local/state/agent-nettools/events.sqlite3",
        "Version 2 event transaction database shared by the event worker and recovery sidecar.",
        "event_store",
    ),
    Setting(
        "NETTOOLS_RECOVERY_SWEEP_SECONDS", "float", 15.0,
        "Cadence for the persistent durable event and Telegram outbox recovery sweep.",
        "recovery_service",
        minimum=1,
    ),
    Setting(
        "NETTOOLS_RECOVERY_LEASE_SECONDS", "float", 180.0,
        "Recovery worker lease duration; must retain a margin beyond the event-agent budget.",
        "recovery_service",
        minimum=150,
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
    # -- graph.py (B-517): the neo4j topology collector/reader. neo4j is
    # DERIVED, never authored (stage-2-architecture.md §2.3) -- see the
    # module docstring. `write_graph` (the operator-run collector) and
    # `run_named_read` (behind get_lab_graph_topology, MCP) read the SAME
    # four variables below -- never a second credential pair for the read
    # half, the same discipline NETBOX_URL/NETBOX_TOKEN above follow.
    # `graph._ENV_URI`/`_ENV_USER`/`_ENV_PASSWORD`/`_ENV_DATABASE` name these
    # literally (`NEO4J_URI` etc.) rather than through an `*_ENV` indirection,
    # which is why `tests/test_settings.py`'s source scan does not
    # independently rediscover them the way it rediscovers
    # NETTOOLS_NEO4J_TIMEOUT_SECONDS below -- declared here anyway, for the
    # same reason every other credential this project reads is: `nettools
    # config show` should list it.
    Setting(
        "NEO4J_URI", "string", None,
        "neo4j Bolt URI, e.g. bolt://neo4j:7687 from a connected Docker "
        "network. A host-run process may need a reachable published endpoint "
        "instead. No default is offered -- guessing wrong means silently "
        "writing to, or reading \"success\" from, the wrong database, worse "
        "than refusing to run.",
        "graph",
    ),
    Setting(
        "NEO4J_USER", "string", "neo4j",
        "neo4j account name. Defaults to \"neo4j\" only because that is the "
        "fixed, non-secret account name every stock neo4j deployment uses "
        "-- the *password* is the secret, the username is not.",
        "graph",
    ),
    Setting(
        "NEO4J_PASSWORD", "secret", None,
        "neo4j password. Read from the container's own NEO4J_AUTH (docker "
        "inspect the neo4j container's Config.Env) -- never committed, "
        "never logged, redacted from every exception message write_graph()/"
        "run_named_read() can raise.",
        "graph",
        secret=True,
    ),
    Setting(
        "NEO4J_DATABASE", "string", None,
        "Optional non-default neo4j database name. Unset uses neo4j's own "
        "default (\"neo4j\").",
        "graph",
    ),
    Setting(
        "NETTOOLS_NEO4J_TIMEOUT_SECONDS", "float", 10.0,
        "Connection and read-transaction-retry timeout for one neo4j read "
        "(graph.run_named_read, behind get_lab_graph_topology). Measured "
        "2026-08-19: with no bound, the neo4j driver's own connect-retry "
        "backoff left an unreachable-server call still retrying past 60s. "
        "Same default as NETTOOLS_LOKI_TIMEOUT_SECONDS/"
        "NETTOOLS_PROMETHEUS_TIMEOUT_SECONDS/NETTOOLS_NETBOX_TIMEOUT_SECONDS.",
        "graph",
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
    # B-209. All three default to today's behaviour, deliberately: the relay
    # hardening ships inert and an operator opts into each piece. Note the
    # asymmetry with the gates above -- those fail CLOSED because an unset
    # value must not grant a capability, while these fail toward SENDING,
    # because for a notifier the dangerous direction is a page that never
    # arrives, not one that arrives twice.
    Setting(
        "NETTOOLS_CLOCK_SKEW_BOUND_SECONDS", "float", 15.0,
        "How far two devices' own clocks may disagree before clock_skew "
        "reports it as a finding (B-415). **A stated policy default, not a "
        "measured one** -- the 2026-08-21 measurement found this fabric's "
        "per-device medians spanning 1.0s, which describes a healthy lab and "
        "is not a failure threshold. Deliberately NOT epoch's 30s bound: that "
        "one is derived from the IS-IS hold timer and answers how long OUR "
        "collection window may run on our own monotonic clock, which is a "
        "different question from whether two devices' timestamps can be "
        "ordered against each other at all.",
        "clock_skew",
        minimum=0.0,
    ),
    Setting(
        "NETTOOLS_RELAY_STATE_FILE", "path", None,
        "De-duplication state for the outbound relay (B-209). Unset disables "
        "de-duplication entirely -- every notification sends, which is exactly "
        "the behaviour before B-209 and is why leaving it unset is safe. The "
        "state records a signature only after a send actually succeeded, so a "
        "provider outage cannot mark a page delivered and swallow it.",
        "relay_policy",
    ),
    Setting(
        "NETTOOLS_RELAY_DEDUP_SECONDS", "float", 1800.0,
        "De-duplication window in seconds (B-209), consulted only when "
        "NETTOOLS_RELAY_STATE_FILE is set. A repeat of the same "
        "(device, subject) with the same (finding, cause rung) inside the "
        "window is suppressed; a changed signature always sends. **A stated "
        "policy default, not a measured one** -- nothing has yet measured how "
        "long an operator wants between reminders for the same condition.",
        "relay_policy",
    ),
    Setting(
        "NETTOOLS_OWNERSHIP_FILE", "path", None,
        "Declarative ownership and escalation table (B-484). Unset resolves to "
        "ownership.default_table(), today's single-channel behaviour -- never "
        "to nobody. A malformed file falls back the same way rather than "
        "raising, for the fail-toward-sending reason above.",
        "relay_policy",
    ),
)

# Names that can never be found by scanning src/ or mcp_server/ for a string
# literal reaching os.getenv (directly, or via the *_env naming convention):
# they are read as os.getenv(group.username_env) etc., where the env var
# *name itself* is data parsed from inventory/lab.yaml at runtime, not a
# Python literal anywhere in the source tree. tests/test_settings.py treats
# this set, not a blanket exemption, as the only allowed gap between the
# source scan and SETTINGS.
#
# NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD/NEO4J_DATABASE are here for a DIFFERENT
# reason, not the YAML-indirection one above: graph.py reads them via
# `os.environ.get(_ENV_URI)` where `_ENV_URI = "NEO4J_URI"` is a real Python
# string literal, just named with the env-var-name word BEFORE `_ENV`
# (`_ENV_URI`) rather than after it (`*_ENV`), the convention
# `_RE_ENV_ASSIGN` below actually matches. Renaming graph.py's four constants
# to fit the scanner would be changing working code to satisfy a test rather
# than the other way around -- listed here instead, the same "known,
# reviewed gap" this set already exists for.
EXTERNAL_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE",
        "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE",
    }
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
