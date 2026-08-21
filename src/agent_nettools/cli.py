"""Single command-line entry point for the read-only IOS-XR network tools.

Exposed as the ``nettools`` console script. Subcommands:

    nettools inventory
    nettools facts | interfaces | bgp | bgp_vpnv4 | lldp | isis | ldp |
             ldp_discovery | sr  [DEVICE]
    nettools fabric [CHECK]
    nettools route DEVICE PREFIX
    nettools bgp-neighbor DEVICE ADDRESS
    nettools interface DEVICE NAME
    nettools logging DEVICE [--count N]
    nettools ping DEVICE ADDRESS
    nettools traceroute DEVICE ADDRESS
    nettools investigate DEVICE SUBJECT
                         [--flow bgp_session|interface|isis_adjacency|ldp_session]
                         [--from-fixtures [--label L]] [--paraphrase] [--notify]
                         [--session ID]
                         (SUBJECT may be a sentence, e.g. "why can't RR1 reach
                         10.255.0.12?", when --flow is omitted -- B-112; DEVICE
                         and/or SUBJECT may be the literal word "it" to resolve
                         against this session's last turn -- B-407)
    nettools audit [--from-fixtures [--label L]]
    nettools analyze [DEVICE] [--show-evidence] [--save]
    nettools analyze --fabric [--show-evidence] [--save]
    nettools agent "QUESTION" [--device DEVICE] [--max-iterations N] [--time-budget SECONDS]
                         (disabled by default -- B-488; NETTOOLS_ENABLE_AGENT=true to enable)
    nettools demo [DEVICE]
    nettools diff [DEVICE] [--against golden|latest]
    nettools capture [DEVICE ...] [--all] [--label t0] [--out DIR] [--no-scrub] [--templates]
    nettools learn-topology [--from-fixtures|--live] [--label t0] [--out PATH] [--write]
    nettools health [DEVICE ...] [--all] [--from-fixtures] [--label t0] [--min-severity S]
                    [--silence-file PATH]
    nettools baseline pin [DEVICE] [--from-latest]
    nettools baseline show [DEVICE]
    nettools flaps [DEVICE]
    nettools evidence prune [--keep-days N] [--keep-count M] [--device DEVICE]
    nettools evidence history [DEVICE]
    nettools metrics [--format json|prometheus] [--quiet]
    nettools config show
    nettools config check [--quiet]
    nettools route-event [--file PATH] [--device DEVICE]
    nettools watch [DEVICE] [--all] [--since-seconds N] [--limit N]
                   (read-only dry run -- never runs investigate, never opens
                   a ticket; W6)
    nettools inspect [DEVICE]
    nettools version

Most commands that print a structured result accept ``--format
json|table|summary`` (default: ``json``, so nothing that already parses this
tool's JSON output breaks) and ``--quiet``/``-q`` (suppress all output; only
the exit code carries the outcome) -- see ``agent_nettools.output`` for what
each format shows. **A table or summary view never invents or softens data**:
the exit code below is always computed from the full result before
rendering, so ``--format summary`` on a critical fabric still exits
non-zero exactly like ``--format json`` would. Commands whose output is
narrative text rather than one structured payload (``analyze``, ``agent``,
``demo``, ``capture``, ``learn-topology``, ``inspect``) do not take
``--format``/``--quiet``.

Exit codes
------------
``nettools health`` set the shape this project follows everywhere else:
``0`` (ok/info, nothing actionable), ``1`` (warning), ``2`` (critical).
Applied consistently:

- ``0`` -- success; nothing actionable.
- ``1`` -- the command ran but reports a problem: a tool/fabric result with
  ``status: "error"`` (one or more commands/devices failed), a health
  verdict at ``warning``, or (``diff``) real drift was found.
- ``2`` -- the command could not run at all, or reports the worst possible
  outcome: a missing/invalid inventory or LLM configuration
  (``InventoryError``, or ``get_provider()``'s ``ValueError``), a health
  verdict at ``critical``, or (``diff``) an intent failed to collect on
  either side, so the comparison itself is not trustworthy.

``diff`` follows the Unix ``diff --exit-code`` convention on top of that
scheme: ``0`` no differences, ``1`` differences found, ``2`` could not fully
compare.

``route``/``bgp-neighbor``/``interface``/``logging``/``ping``/``traceroute``
are validated, parameterized templates (Phase 5): the prefix/address/interface
name/count argument is never passed through to the device as text -- it is
parsed into a typed object and the command is rendered from that object's own
canonical form (see ``agent_nettools.templates``, "canonicalize by
reconstruction"). ``ping``/``traceroute`` generate traffic (unlike every other
command here) and are gated by ``NETTOOLS_ALLOW_ACTIVE_PROBES`` (default
enabled).

``nettools evidence prune`` (Phase 7) deletes timestamped snapshots outside a
retention window (age and/or count; golden snapshots are never touched),
against whichever evidence backend ``NETTOOLS_EVIDENCE_BACKEND`` selects
(JSON files, the default, or SQLite).

``nettools metrics`` (Phase 8) reports the current process's operational
metrics -- per-device collection success/failure, latency, retries, and
health verdict counts by severity -- as JSON (default) or Prometheus text
exposition format (``--format prometheus``). Counters are in-memory only
unless ``NETTOOLS_METRICS_FILE`` is set, in which case they persist across
separate ``nettools`` invocations too; see ``agent_nettools.metrics``.

``nettools version`` (Phase 8) prints the installed package version plus the
Python/platform it is running on.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv

# `load_dotenv`/`find_dotenv` stay a real, eager module-level import
# (unlike everything below) for the same reason `find_dotenv`/`load_dotenv`
# themselves are tiny and third-party rather than one of this project's own
# heavy submodules: dozens of existing tests do
# `monkeypatch.setattr(cli, "load_dotenv", ...)` / `mock.patch.object(cli,
# "find_dotenv", ...)`, and python-dotenv itself costs essentially nothing
# to import (no netmiko/anthropic/openai transitively behind it).
#
# `__version__` also stays eager -- see agent_nettools/__init__.py's own
# module-level comment for why (P4, half 1): other code very likely does
# `from agent_nettools import __version__` expecting zero-cost access, and
# it now genuinely IS zero-cost (that package's own __init__.py no longer
# imports anything else just to produce this one string).
from . import __version__

# --------------------------------------------------------------------------- #
# P4 (release-1.0 cleanup), half 2: lazy submodule imports (PEP 562).
#
# This file used to import ~12 of this project's own submodules eagerly, at
# `import agent_nettools.cli` time -- `agent_loop`, `fabric_analysis`,
# `fixtures`, `flow_selection`, `health`, `inventory`, `inventory_model`,
# `investigation`, `llm_analysis`, `network_tools`, `templates`, `topology`,
# plus whole-module references to `flows`/`metrics`/`output`/`settings` --
# paid by every `nettools` invocation (including `--help`) and by importing
# this module as a library, even when the command that ran never touches
# most of them (`nettools version` does not need netmiko, `nettools ping`
# does not need the Anthropic/OpenAI SDKs `llm_analysis` pulls in, ...).
#
# Every one of those ~50 names now resolves through `__getattr__` below, on
# first access, cached on this module's own globals() afterward exactly like
# `agent_nettools/__init__.py`'s own P4 half-1 rewrite.
#
# Why this could NOT simply become "move each import statement into the one
# function that uses it" (the more obvious lazy-import shape, and this
# task's own first instinct): dozens of existing tests do
# `monkeypatch.setattr(cli, "collect_evidence", fake)` et al -- patching
# THIS module's own global binding, not the source submodule
# (`network_tools.collect_evidence`). A local `from .network_tools import
# collect_evidence` inside a function body creates a function-LOCAL name
# that shadows the module global for that call, so the monkeypatch would be
# silently ignored and the REAL function would run instead -- in a suite
# whose own docstring's central claim is "no network, no LLM call, ever"
# (tests/test_cli.py). Confirmed empirically before choosing this shape:
# PEP 562's module `__getattr__` fires only on EXTERNAL attribute access
# (`cli.name`, `from .cli import name`, and -- load-bearing here --
# `monkeypatch.setattr`'s own internal `getattr(target, name)` save-the-old-
# value step) and is NEVER consulted for a bare name a function inside this
# same module references internally; that resolves via a plain globals()
# dict lookup and raises `NameError` immediately if absent. `_require()`
# below is the fix for exactly that gap: every function that reads one of
# these names as a bare global calls it once, at the top of its own body,
# naming what it uses -- a no-op when a test (or an earlier call in this
# same process) already populated the name, and a real, cached import
# otherwise.
# --------------------------------------------------------------------------- #

_LAZY: dict[str, tuple[str, str | None]] = {
    # Whole-module references, used as `flows.X`, `output.render(...)`, etc.
    # `real_name=None` means "bind the submodule itself under this name".
    "flows": (".flows", None),
    "metrics": (".metrics", None),
    "output": (".output", None),
    "relay_policy": (".relay_policy", None),
    "settings": (".settings", None),
    # agent_loop
    "run_agent_loop": (".agent_loop", "run_agent_loop"),
    # fabric_analysis
    "analyze_fabric": (".fabric_analysis", "analyze_fabric"),
    # fixtures
    "capture_device": (".fixtures", "capture_device"),
    "load_fixture_evidence": (".fixtures", "load_fixture_evidence"),
    # flow_selection
    "looks_like_sentence": (".flow_selection", "looks_like_sentence"),
    "select_flow": (".flow_selection", "select_flow"),
    # health
    "evaluate_fabric_with_silences": (".health", "evaluate_fabric_with_silences"),
    "exit_code_for_severity": (".health", "exit_code_for_severity"),
    "severity_rank": (".health", "severity_rank"),
    # inventory
    "InventoryError": (".inventory", "InventoryError"),
    "get_default_device_name": (".inventory", "get_default_device_name"),
    # inventory_model
    "resolve_inventory_path": (".inventory_model", "resolve_inventory_path"),
    # investigation
    "investigate": (".investigation", "investigate"),
    # llm_analysis
    "LLMAnalysisError": (".llm_analysis", "LLMAnalysisError"),
    "TokenUsage": (".llm_analysis", "TokenUsage"),
    "analyze_evidence": (".llm_analysis", "analyze_evidence"),
    "complete_prompt": (".llm_analysis", "complete_prompt"),
    "get_provider": (".llm_analysis", "get_provider"),
    # network_tools
    "CHECK_TOOLS": (".network_tools", "CHECK_TOOLS"),
    "check_fabric": (".network_tools", "check_fabric"),
    "collect_evidence": (".network_tools", "collect_evidence"),
    "detect_flaps": (".network_tools", "detect_flaps"),
    "diff_evidence": (".network_tools", "diff_evidence"),
    "get_bgp_neighbor": (".network_tools", "get_bgp_neighbor"),
    "get_interface": (".network_tools", "get_interface"),
    "get_logging": (".network_tools", "get_logging"),
    "get_route": (".network_tools", "get_route"),
    "list_devices": (".network_tools", "list_devices"),
    "list_snapshot_history": (".network_tools", "list_snapshot_history"),
    "load_golden_snapshot": (".network_tools", "load_golden_snapshot"),
    "load_latest_snapshot": (".network_tools", "load_latest_snapshot"),
    "ping_device": (".network_tools", "ping_device"),
    "prune_snapshots": (".network_tools", "prune_snapshots"),
    "run_template": (".network_tools", "run_template"),
    "save_golden_snapshot": (".network_tools", "save_golden_snapshot"),
    "save_snapshot": (".network_tools", "save_snapshot"),
    "traceroute_device": (".network_tools", "traceroute_device"),
    # templates
    "TemplateValidationError": (".templates", "TemplateValidationError"),
    "split_sr_policy_id": (".templates", "split_sr_policy_id"),
    # topology
    "build_anomaly_report": (".topology", "build_anomaly_report"),
    "derive_expected": (".topology", "derive_expected"),
    "format_anomaly_report": (".topology", "format_anomaly_report"),
    "update_expected_in_yaml": (".topology", "update_expected_in_yaml"),
}

# Every name in `_LAZY`, pre-bound to `None` -- a real module-level binding,
# not a runtime trick, so a static checker (this project's own `ruff check`)
# sees every bare `collect_evidence(...)`, `output.render(...)`, etc. below
# as a real, known name rather than flagging ~50 false-positive "undefined
# name" hits across every function that uses one. `_require()` treats `is
# None` as "not resolved yet" (never `name not in globals()`, which this
# would make permanently false) -- correct because every REAL value these
# ever resolve to is a module, a class, or a function, never actually
# `None`, and it is exactly what lets `monkeypatch.setattr(cli, name, fake)`
# keep working unchanged: pytest's own internal `getattr(cli, name)` (its
# save-the-old-value step) now finds this placeholder directly rather than
# going through `__getattr__` at all, which is fine -- the placeholder is
# about to be overwritten with `fake` either way.
flows = metrics = output = relay_policy = settings = None
run_agent_loop = None
analyze_fabric = None
capture_device = load_fixture_evidence = None
looks_like_sentence = select_flow = None
evaluate_fabric_with_silences = exit_code_for_severity = severity_rank = None
InventoryError = get_default_device_name = None
resolve_inventory_path = None
investigate = None
LLMAnalysisError = TokenUsage = analyze_evidence = complete_prompt = get_provider = None
CHECK_TOOLS = check_fabric = collect_evidence = detect_flaps = diff_evidence = None
get_bgp_neighbor = get_interface = get_logging = get_route = list_devices = None
list_snapshot_history = load_golden_snapshot = load_latest_snapshot = None
ping_device = prune_snapshots = run_template = None
save_golden_snapshot = save_snapshot = traceroute_device = None
TemplateValidationError = split_sr_policy_id = None
build_anomaly_report = derive_expected = format_anomaly_report = update_expected_in_yaml = None


def __getattr__(name: str) -> Any:
    """PEP 562: resolve a lazy name, then cache it.

    Raises plain `AttributeError` for anything not in `_LAZY` -- this file
    has no other names that need it. In practice this is called two ways:
    directly, by `_require()` below (the path every real `_cmd_*` function
    actually takes, since every one of `_LAZY`'s names already exists as the
    `None` placeholder above -- see that block's own comment for why plain
    external attribute access no longer reaches this function via Python's
    own PEP 562 magic); and defensively, for a genuinely unknown name, which
    still raises correctly and is the same contract `agent_nettools/
    __init__.py`'s own `__getattr__` documents.
    """

    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, real_name = target
    module = importlib.import_module(module_name, __package__)
    value = module if real_name is None else getattr(module, real_name)
    globals()[name] = value
    return value


def _require(*names: str) -> None:
    """Force each name to resolve via `__getattr__` if it is still the
    `None` placeholder. See this file's own P4 module-level comment above
    for the full reasoning -- in short, a bare name a function in THIS
    module reads internally is never routed through `__getattr__`
    automatically (confirmed empirically; Python resolves it via a plain
    globals() dict lookup), so every function using one of `_LAZY`'s names
    calls this once, at the top of its own body, naming exactly what it uses
    below.
    """

    g = globals()
    for name in names:
        if g.get(name) is None:
            g[name] = __getattr__(name)


# Exit codes, applied consistently across every command -- see the module
# docstring's "Exit codes" section for the full rationale.
EXIT_OK = 0
EXIT_WARNING = 1
EXIT_CRITICAL = 2

# B-488: `nettools agent` is a free-form, model-driven tool-calling loop --
# philosophically at odds with this project's central claim that the model
# only navigates a menu and never synthesises a diagnosis, Anthropic-only,
# and this README says outright it is unreliable on local models. As a
# first-class peer of `investigate` in --help/the subcommand list, it
# undercuts that trust story for anyone who meets it before its disclaimer
# (operator-experience review, HOLISTIC 2026-08-18). Kept, not deleted --
# quarantined behind an explicit opt-in that defaults OFF.
#
# unknown_bool_disables in settings.py: a value outside the recognized
# truthy set (including a typo) leaves the gate closed, the same fail-closed
# choice B-493 makes for NETTOOLS_MCP_ALLOW_ACTIVE_PROBES and for the same
# reason -- a gate whose entire purpose is "off unless asked for" must not
# reopen on a misspelling.
NETTOOLS_ENABLE_AGENT_ENV = "NETTOOLS_ENABLE_AGENT"
_ENABLE_AGENT_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _agent_enabled() -> bool:
    return os.getenv(NETTOOLS_ENABLE_AGENT_ENV, "0").strip().lower() in _ENABLE_AGENT_TRUTHY


def _emit(payload: dict, args: argparse.Namespace) -> None:
    """Print one structured result, honoring ``--format``/``--quiet``.

    Never affects the exit code: every ``_cmd_*`` function computes its
    return value from the full, unfiltered ``payload`` before (or without
    regard to) calling this -- a table/summary rendering is strictly a
    reformat, never a softer read of the same facts.
    """

    if getattr(args, "quiet", False):
        return
    _require("output")
    print(output.render(payload, getattr(args, "format", "json")))


def _note(message: str, args: argparse.Namespace) -> None:
    """Print an informational (non-payload) line, suppressed by ``--quiet``.

    **To stderr, not stdout** (B-422). These lines are commentary — a snapshot
    path, a fixture-replay banner, a grounding failure — and stdout carries the
    *payload*. Mixing them meant

        nettools investigate ... --format json | jq

    failed on the first note, so the JSON output was not actually consumable by
    the tool everyone reaches for. The `#` prefix made the lines look like
    comments, which is true of very little and not of JSON.

    The split is the ordinary Unix one: **stdout is data, stderr is about the
    run.** A human sees both interleaved exactly as before; a pipe gets only the
    payload; `2>/dev/null` gets the payload alone even when notes exist.
    """

    if not getattr(args, "quiet", False):
        print(message, file=sys.stderr)


def _error_envelope(tool: str, *, device: str | None = None, errors: list[str], **extra: Any) -> dict[str, Any]:
    """One ``{"status": "error", ...}`` envelope shape, factored out of the
    seven hand-built error dicts this file used to construct separately
    (release-1.0 cleanup, C3).

    Pure de-duplication -- every call site's key SET and every value are
    unchanged from what it built by hand; only key insertion order may now
    differ, which is not a guarantee any caller of ``_emit`` relies on
    (JSON objects, and this project's own tests, compare by key/value, never
    by order).

    ``device`` is omitted from the envelope entirely when ``None`` rather
    than written as a null field -- `_cmd_route_event`'s error envelope
    never carried a ``device`` key at all, and adding one here would change
    what is emitted, not just how it is built. ``**extra`` carries whatever
    else one call site needs beyond ``tool``/``device``/``status``/
    ``errors`` -- ``subject`` for most `investigate` refusals, ``data: {}``
    for `sr_policy_detail`'s.
    """

    envelope: dict[str, Any] = {"tool": tool, "status": "error", "errors": errors}
    if device is not None:
        envelope["device"] = device
    envelope.update(extra)
    return envelope


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    _require("output")
    parser.add_argument(
        "--format",
        choices=output.FORMATS,
        default="json",
        help="Output format for the structured result (default: json).",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress all output; only the exit code carries the outcome.",
    )


def _resolve_device(name: str | None) -> str:
    _require("get_default_device_name")
    return name or get_default_device_name()


def _envelope_exit(result: dict) -> int:
    """Exit code for a single tool envelope. B-468.

    ``unsupported`` exits 0: the project's documented position -- in three
    places -- is that a platform lacking a command for an intent *"is a
    property of the fabric, not a failure"*, and `check_fabric` already stays
    green on it. Before this helper the per-device commands had only two
    buckets, so the same fact was green from `nettools fabric` and red from
    `nettools sr <junos-device>`; the front ends disagreed about a contract
    the code itself states.
    """

    status = result.get("status")
    if status in ("success", "unsupported"):
        return EXIT_OK
    return EXIT_WARNING


def _cmd_inventory(args: argparse.Namespace) -> int:
    _require("list_devices")
    result = list_devices()
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_check(args: argparse.Namespace) -> int:
    _require("CHECK_TOOLS")
    device = _resolve_device(args.device)
    result = CHECK_TOOLS[args.check](device)
    _emit(result, args)
    return _envelope_exit(result)


def _cmd_fabric(args: argparse.Namespace) -> int:
    _require("check_fabric")
    result = check_fabric(args.check)
    _emit(result, args)
    return EXIT_OK if result.get("status") == "success" else EXIT_WARNING


def _cmd_route(args: argparse.Namespace) -> int:
    _require("get_route")
    result = get_route(args.device, args.prefix)
    _emit(result, args)
    return _envelope_exit(result)


def _cmd_bgp_neighbor(args: argparse.Namespace) -> int:
    _require("get_bgp_neighbor")
    result = get_bgp_neighbor(args.device, args.address)
    _emit(result, args)
    return _envelope_exit(result)


def _cmd_interface(args: argparse.Namespace) -> int:
    # B-519 audit note: `args.name` is deliberately NOT canonicalised here.
    # It renders straight into `show interfaces {name}`, which IOS-XR accepts
    # in either spelling (confirmed live, 2026-08-19, against PE2) -- there is
    # no comparison in this call path for a rewrite to fix, and rewriting it
    # would only make `--from-fixtures`-style replay miss a file captured
    # under the spelling the caller did not type (`tests/fixtures/` keys
    # interface captures by the SHORT form). See
    # tests/test_interface_canonicalization.py for the full audit.
    _require("get_interface")
    result = get_interface(args.device, args.name)
    _emit(result, args)
    return _envelope_exit(result)


def _cmd_sr_policy(args: argparse.Namespace) -> int:
    """B-515: which SID/segment list backs one SR-TE policy, or why it does
    not have one. `args.policy_id` is "<color>:<endpoint>", e.g.
    "20:10.255.0.13" -- the same shape `nettools sr` (check_sr_policies)'
    own `policy` field reports, so a value copied from that output works
    here."""

    _require("TemplateValidationError", "split_sr_policy_id", "run_template")
    try:
        color, endpoint = split_sr_policy_id(args.policy_id)
    except TemplateValidationError as exc:
        result = _error_envelope(
            "sr_policy_detail", device=args.device, data={}, errors=[str(exc)]
        )
        _emit(result, args)
        return EXIT_WARNING

    result = run_template(args.device, "sr_policy_detail", color=color, endpoint=endpoint)
    _emit(result, args)
    return _envelope_exit(result)


def _cmd_logging(args: argparse.Namespace) -> int:
    _require("get_logging")
    result = get_logging(args.device, args.count)
    _emit(result, args)
    return _envelope_exit(result)


def _probe_exit(result: dict) -> int:
    """Exit code for an active probe: the PROBE's outcome, not the transport's. B-468.

    Four decades of `ping && next-step` mean *the target answered*. Before
    this, `nettools ping` exited 0 whenever the SSH session to the device
    worked -- measured on the committed fixtures, a ping with `loss_pct: 100`
    and 0 of 5 received exited success, inverting the convention at the exact
    place an operator's muscle memory is strongest. The loss percentage was
    parsed, carried in the payload, and never read by the exit computation --
    shape 7 (`PROCESS.md` §0.13's catalogue) in the CLI's own logic.

    The rule: transport or parse failure exits 1 as before; a parsed probe
    with **zero received** exits 1; anything received exits 0. Partial loss
    exits 0 deliberately -- one lost packet of five is a quality signal, not
    "the target is unreachable", and the payload carries the percentages for
    a caller that wants a stricter policy.

    `unsupported` exits 0, same as `_envelope_exit` and for the same reason.
    """

    status = result.get("status")
    if status == "unsupported":
        return EXIT_OK
    if status != "success":
        return EXIT_WARNING
    data = result.get("data") or {}
    if data.get("parse_status") != "ok":
        # The probe ran and its output could not be read: no basis for exit 0.
        return EXIT_WARNING
    meta = (data.get("parsed") or {}).get("meta") or {}
    try:
        received = int(meta.get("received", 0))
    except (TypeError, ValueError):
        return EXIT_WARNING
    return EXIT_OK if received > 0 else EXIT_WARNING


def _cmd_ping(args: argparse.Namespace) -> int:
    _require("ping_device")
    result = ping_device(args.device, args.address)
    _emit(result, args)
    return _probe_exit(result)


def _cmd_traceroute(args: argparse.Namespace) -> int:
    _require("traceroute_device")
    result = traceroute_device(args.device, args.address)
    _emit(result, args)
    # Traceroute has no received-count semantics -- an incomplete trace is
    # still information -- so its exit stays envelope-shaped.
    return _envelope_exit(result)


def _cmd_analyze(args: argparse.Namespace) -> int:
    _require(
        "list_devices", "collect_evidence", "save_snapshot", "output",
        "analyze_fabric", "LLMAnalysisError", "analyze_evidence",
    )
    if args.fabric:
        listed = list_devices()
        if listed.get("status") != "success":
            print(output.render(listed, "json"))
            return EXIT_CRITICAL
        names = [device["name"] for device in listed["data"]["devices"]]
        evidence_by_device = {name: collect_evidence(name) for name in names}
        if args.save:
            for evidence in evidence_by_device.values():
                path = save_snapshot(evidence)
                print(f"# Snapshot saved: {path}")
        if args.show_evidence:
            print("# Evidence")
            print(output.render(evidence_by_device, "json"))
            print("\n# Analysis")
        try:
            result = analyze_fabric(evidence_by_device)
        except ValueError as exc:
            # Provider misconfiguration from get_provider(): nothing could be attempted.
            print(f"Analysis error: {exc}")
            return EXIT_CRITICAL
        except LLMAnalysisError as exc:
            print(f"Analysis error: {exc}")
            return EXIT_WARNING
        print(result["analysis"])
        if result["truncated"]:
            print(
                f"\n[Note: evidence was truncated for {len(result['truncated'])} "
                "section(s) to stay within the evidence budget; see the "
                "programmatic 'truncated' report for details.]"
            )
        return EXIT_OK

    device = _resolve_device(args.device)
    evidence = collect_evidence(device)
    if args.save:
        path = save_snapshot(evidence)
        print(f"# Snapshot saved: {path}")
    if args.show_evidence:
        print("# Evidence")
        print(output.render(evidence, "json"))
        print("\n# Analysis")
    try:
        print(analyze_evidence(evidence))
    except ValueError as exc:
        # Provider misconfiguration from get_provider(): nothing could be attempted.
        print(f"Analysis error: {exc}")
        return EXIT_CRITICAL
    except LLMAnalysisError as exc:
        print(f"Analysis error: {exc}")
        return EXIT_WARNING
    return EXIT_OK


def _cmd_agent(args: argparse.Namespace) -> int:
    # B-488: quarantined behind an explicit opt-in (default off). Checked
    # first, before run_agent_loop is even imported-from-called, so a refusal
    # never touches the LLM provider, never opens a device connection, and
    # costs nothing. The message states WHAT this command is, WHY it is
    # gated, and points at the deterministic alternative -- see
    # NETTOOLS_ENABLE_AGENT_ENV's comment above for the reasoning.
    if not _agent_enabled():
        print(
            "Agent error: `nettools agent` is disabled by default.\n"
            "\n"
            "WHAT this is: a free-form, model-driven tool-calling loop "
            "(Anthropic only) that chooses its own tools and writes its own "
            "prose answer -- not a deterministic diagnosis.\n"
            "WHY it is gated: this project's central claim is that the model "
            "only navigates a menu and never synthesises a diagnosis; this "
            "command is the one place that is not true, and it is unreliable "
            "on local models (see the README).\n"
            "\n"
            "Prefer `nettools investigate` instead: a deterministic "
            "dependency descent with no model in the diagnosis path.\n"
            "\n"
            "To run this command anyway, set NETTOOLS_ENABLE_AGENT=true (or 1)."
        )
        return EXIT_CRITICAL

    # Only resolved past the gate above -- a refusal must cost nothing, and
    # now genuinely does not even import agent_loop.py (which itself pulls
    # in the Anthropic SDK).
    _require("run_agent_loop", "LLMAnalysisError")
    try:
        result = run_agent_loop(
            args.question,
            device=args.device,
            max_iterations=args.max_iterations,
            time_budget_s=args.time_budget,
        )
    except ValueError as exc:
        # Provider misconfiguration from get_provider(): nothing could be attempted.
        print(f"Agent error: {exc}")
        return EXIT_CRITICAL
    except LLMAnalysisError as exc:
        print(f"Agent error: {exc}")
        return EXIT_WARNING

    # B-471/P0-03: this loop is a model choosing its own tools and prose, not
    # the deterministic dependency descent `nettools investigate` runs -- the
    # answer below must never be mistaken for that grounded report. Printed
    # to stderr, before the answer, using `_note` exactly like every other
    # informational line in this module (B-422's stdout-is-data/stderr-is-
    # commentary split) -- so `nettools agent "..." | ...` still gets only
    # the answer on stdout, but nobody reading the terminal misses the label.
    _note("[exploratory — model-selected tools; not the deterministic investigate path]", args)
    print(result["answer"])
    print(
        f"\n[{result['iterations']} iteration(s), {len(result['tool_calls'])} tool call(s), "
        f"stopped_because={result['stopped_because']}]"
    )
    # "end_turn" is the only recognized, complete stop reason (see
    # run_agent_loop's `complete` field) -- every other `stopped_because`,
    # including B-471's new `unknown_stop:<reason>` (an unrecognized
    # stop_reason used to be silently reported as "end_turn", which made this
    # comparison wrongly return EXIT_OK for it; run_agent_loop no longer does
    # that), already falls through to EXIT_WARNING here unchanged.
    return EXIT_OK if result["stopped_because"] == "end_turn" else EXIT_WARNING



def _ledger_for_cli():
    """The on-disk ledger if one is configured, else an in-memory one.

    `NETTOOLS_DIAGNOSIS_LEDGER_FILE` unset means diagnoses are recorded and
    then lost at process exit -- which is the honest default for a tool that
    should not start writing files nobody asked for.
    """

    from . import ledger as _ledger

    path = os.getenv("NETTOOLS_DIAGNOSIS_LEDGER_FILE")
    # `default_ledger` is a module-level INSTANCE (ledger.py's own
    # `default_ledger = DiagnosisLedger()`), not a factory -- calling it as
    # `default_ledger()` raised `TypeError: 'DiagnosisLedger' object is not
    # callable`. With the env var unset that hit `_record_diagnosis_in_ledger`'s
    # broad `except Exception`, so every `nettools investigate` ever recorded
    # nothing and said so only in a stderr note; `nettools ledger summary`/
    # `verdict` had no such guard and crashed outright. Verified 2026-08-18:
    # `nettools investigate RR1 10.255.0.12 --from-fixtures` with the env var
    # unset printed "# accuracy ledger not updated: 'DiagnosisLedger' object
    # is not callable" on stderr; `nettools ledger summary` raised the same
    # TypeError uncaught, to a full traceback.
    return _ledger.DiagnosisLedger(path=Path(path)) if path else _ledger.default_ledger


def _open_ticket_for(args, subject, flow, entry_point):
    """Open a flight-recorder ticket, or return None. Never raises.

    The ticket observes an investigation; it must never be able to fail one.
    `ticket.py` degrades internally too, so this catch is the belt to its
    braces -- an import error or a bad argument here still cannot reach the
    caller (OBS-169: the one guarantee this file must not undermine).
    """

    try:
        from . import ticket as _ticket

        return _ticket.open_ticket(
            subject=subject, entry_point=entry_point,
            device=getattr(args, "device", None), flow=flow,
        )
    except Exception as exc:  # noqa: BLE001 -- bookkeeping never fails a run
        _note(f"# ticket not opened: {exc}", args)
        return None


def _record_in_ticket(handle, args, result, subject, flow, question=None, analyst=None) -> None:
    """Fill a ticket from an InvestigationResult. Never raises.

    Everything written here is CODE-OBSERVED: the finding and cause come from
    the descent, the session counts/commands run/latency from the evidence
    epoch, the coherence from the epoch's own re-read. Nothing is taken from a
    model's account of itself (OBS-165 -- a true and a false self-report read
    identically).

    `retries` is deliberately not passed to `record_device_interaction` below:
    no seam between here and a device carries a measured per-device retry
    count today (`network_tools._section_from_combined` and the template
    runners drop `_netmiko_send_commands`'s retry counts before they reach an
    `Observation`), so it defaults to `None` -- "not measured," not "measured
    zero." Passing `0` here would be exactly OBS-188's defect class.
    """

    if handle is None:
        return
    try:
        if question is not None:
            handle.record_question(question, device=getattr(args, "device", None),
                                   subject=subject, flow_hint=flow)
        # W4a: how the raw question became this resolved intent. `resolver`
        # is hardcoded to the literal "inventory_resolver" -- not a value
        # this function invents, but the one true fact of the CLI path:
        # `_cmd_investigate` never passes `resolver=` to `investigate()`
        # (see that call, a few lines above this one's own caller), so
        # `investigate()`'s own `resolve = resolver or inventory_resolver`
        # always falls through to the module-level default here. Nothing on
        # `InvestigationResult` names which resolver ran, so a caller-
        # supplied `resolver=` (were the CLI ever to grow one) would need a
        # new field to report honestly instead of this literal.
        handle.record_intent(flow=flow, resolved_subject=subject, resolver="inventory_resolver")
        # W4d: the tool timeline and per-evidence provenance, straight off
        # `result.observations` (W4c) -- `None` on the `collector=` path,
        # same reason `session_summary` is `None` there.
        #
        # NOT every observation's `.envelope` is a dict: `EvidenceEpoch.
        # collect_epoch` also emits three pass-through entries per device
        # (`key` in "device"/"platform"/"timestamp") whose `.envelope` is a
        # plain string, mirrored into `for_device()`'s dict for `checks.py`'s
        # benefit -- `EvidenceEpoch.commands_run`'s own property already
        # skips exactly these (`if not isinstance(o.envelope, dict): continue`)
        # and this loop follows the same precedent; without it,
        # `obs.envelope.get(...)` below would raise `AttributeError` on a str.
        #
        # The envelope shape (verified against `network_tools._base_result`,
        # what every real observation's envelope actually is): `tool`,
        # `device`, `status`, `timestamp`, `source`, `data`, `errors` -- no
        # top-level `command`/`commands` key at all (per-command detail lives
        # nested under `data["commands"]`, keyed by the command string), so
        # `record_tool_event`'s `command=` is left at its own default.
        if result.observations is not None:
            evidence_keys = set(result.descent.evidence_keys)
            for obs in result.observations:
                if not isinstance(obs.envelope, dict):
                    continue
                handle.record_tool_event(
                    tool=obs.envelope.get("tool", obs.key),
                    status=obs.envelope.get("status", "unknown"),
                    device=obs.device,
                    duration_ms=obs.duration * 1000,
                    started_at=obs.collected_at,
                )
                # Only the evidence that actually fed the descent, not every
                # observation collected. `descent.evidence_keys` entries are
                # built by `checks.evidence_key(device, intent_or_template,
                # subject)` -> `f"{device}:{intent_or_template}[:subject]"`
                # (verified by reading that helper) -- a DIFFERENT string
                # from `obs.key` (the bare intent/template name, no device
                # prefix), so a literal `obs.key in evidence_keys` check
                # would never match anything at all. Reconstructed here as
                # `f"{obs.device}:{obs.key}"`, matched either exactly (a
                # template observation already keyed by its own subject,
                # e.g. "interface:Gi0/0/0/0") or as a prefix of a longer
                # evidence key (a whole-intent observation, e.g. "bgp",
                # further narrowed to one peer's own citation by the check
                # that read it) -- confirmed against a real fixture run
                # (`--from-fixtures --label broken`): this reconstruction
                # picks exactly the 7 observations behind the 7 real
                # `evidence_keys` entries, one for one, neither more nor
                # fewer.
                prefix = f"{obs.device}:{obs.key}"
                fed_the_descent = any(
                    ek == prefix or ek.startswith(prefix + ":") for ek in evidence_keys
                )
                if fed_the_descent:
                    handle.record_evidence_source(
                        obs.key, device=obs.device,
                        source=obs.envelope.get("source", "unknown"),
                        collected_at=obs.collected_at,
                    )
        sessions = getattr(result, "session_summary", None)
        if sessions:
            commands_by_device = (sessions.get("commands_run") or {}).get("by_device") or {}
            latency_by_device = (sessions.get("latency_ms") or {}).get("by_device") or {}
            for device, count in (sessions.get("by_device") or {}).items():
                handle.record_device_interaction(
                    device, session_count=count,
                    commands_run=commands_by_device.get(device),
                    latency_ms=latency_by_device.get(device),
                )
        # W4b: what actually crossed into a model prompt. `chars_sent` is a
        # real measurement -- the total length of every exchange's own
        # `user_payload` (0 when `result.exchanges` is empty, e.g.
        # `--no-model`/`--from-fixtures` with no `--paraphrase`, which is a
        # true zero, not a stand-in for one never taken).
        # `chars_withheld` is `None` here, always -- NOT `0`. `0` would claim
        # "measured, and nothing was withheld"; the true fact on this path is
        # "not measured at all". No evidence-budget mechanism
        # (`evidence_budget.budget_device_evidence`/`budget_fabric_evidence`)
        # runs on the `investigate()` path today -- verified by reading
        # `evidence_budget.py`'s only callers (`fabric_analysis.py`/
        # `model_egress.py`) and `investigation.py`'s own imports, which pull
        # in neither -- so there is no real input-truncation signal to
        # report, the identical "no seam carries this measurement" shape
        # `record_device_interaction`'s `retries` documents for itself (see
        # `ticket.py`'s `record_context_footprint` docstring, updated
        # alongside this call). This is deliberately NOT
        # `paraphrase_status == WITHHELD`: that describes the model's OUTPUT
        # being rejected by grounding, a different fact from something
        # withheld from its INPUT, and conflating the two would misreport a
        # rejected answer as a truncated prompt.
        chars_sent = sum(len(exchange.user_payload) for exchange in getattr(result, "exchanges", ()))
        handle.record_context_footprint(chars_sent=chars_sent, chars_withheld=None)
        # Every model exchange, recorded verbatim. This is the half of the
        # flight recorder that was missing: the deterministic path was well
        # instrumented and the model path invisible, which is backwards for
        # a dataset whose whole purpose is studying how context is built.
        for exchange in getattr(result, "exchanges", ()):
            handle.record_model_exchange(
                purpose=exchange.purpose,
                model=getattr(analyst, "model", None),
                provider=getattr(analyst, "provider", None),
                system_prompt=exchange.system_prompt,
                system_prompt_ref=exchange.prompt_ref,
                user_payload=exchange.user_payload,
                user_payload_ref=exchange.prompt_ref,
                response_text=exchange.response_text,
                stop_reason=exchange.stop_reason,
                tokens=exchange.usage,
                grounding_ok=exchange.grounding_ok,
                grounding_summary=exchange.grounding_summary,
                grounding_failures=list(exchange.grounding_failures),
            )
        cause = result.descent.cause
        coherence = result.descent.coherence
        # B-446 (Lane B2): every rung's own outcome, not just the cause --
        # the extra half of `prompt_library.descent_payload`'s own "rungs"
        # shape, computed here rather than imported (this module has no
        # dependency on `prompt_library.py`, and the reduction is three
        # lines). This is what lets a LATER run's handover section tell
        # "recovered" apart from "still broken" for a rung that was never
        # the cause -- `cause` alone only ever names the lowest ONE. Passed
        # as `extra=` rather than a new `record_answer` parameter: still the
        # descent's own field (`result.descent.outcomes`), still passed
        # through unchanged, so `record_answer`'s own "never re-derived"
        # contract is unaffected.
        rungs = [
            {"rung": o.rung, "device": o.device, "status": o.status, "reason": o.result.reason}
            for o in result.descent.outcomes
        ]
        handle.record_answer(
            finding=result.descent.finding,
            trustworthy=result.trustworthy,
            cause=({"rung": cause.rung, "device": cause.device,
                    "reason": cause.result.reason} if cause is not None else None),
            coherence=(coherence.as_dict() if coherence is not None else None),
            report_status=result.report_status,
            correlation_status=result.correlation_status,
            extra={"rungs": rungs},
        )
        _record_handover(handle, args, result, subject, flow)
        handle.close()
    except Exception as exc:  # noqa: BLE001 -- bookkeeping never fails a run
        _note(f"# ticket incomplete: {exc}", args)


def _record_handover(handle, args, result, subject, flow) -> None:
    """B-446 (Lane B2): what changed since the previous ticket for this same
    (device, subject, flow), and what recovered. Never raises -- the same
    "bookkeeping must not take down a diagnosis" posture
    `_record_diagnosis_in_ledger`/`_record_session_turn` already take for
    theirs, and deliberately its OWN try/except rather than sharing
    `_record_in_ticket`'s: a bug in this brand-new comparison must not be
    able to suppress the `Closed` marker for a ticket whose question/intent/
    timeline/evidence/answer all wrote successfully.

    Called from `_record_in_ticket`, after `record_answer` (whose `extra=
    {"rungs": [...]}` this run's own comparison needs) and before
    `handle.close()` -- a handover recorded after `close()` would still
    round-trip correctly (`read_ticket` takes the LAST section of each kind,
    and `close()` is idempotent), but a ticket whose last recorded action is
    `Closed` is the honest shape for "this interaction is over".
    """

    if handle is None:
        return
    try:
        from . import ticket as _ticket
        from . import ticket_read as _ticket_read
        from .checks import BROKEN, HEALTHY

        device = getattr(args, "device", None)
        prev_path = _ticket_read.find_previous_ticket_path(
            device, subject, flow, exclude_run_id=handle.run_id,
        )
        if prev_path is None:
            handle.record_handover(
                status=_ticket.HANDOVER_FIRST_RUN,
                notes=(
                    f"no previous ticket found for device={device!r} "
                    f"subject={subject!r} flow={flow!r} -- first run recorded"
                ),
            )
            return

        prev_parsed = _ticket.read_ticket(prev_path)
        prev_header = prev_parsed.get("header") or {}
        prev_answer = prev_parsed.get("answer")
        if not prev_answer:
            handle.record_handover(
                status=_ticket.HANDOVER_CANNOT_COMPARE,
                previous_run_id=prev_parsed.get("run_id"),
                previous_ticket_path=str(prev_path),
                previous_opened_at=prev_header.get("opened_at_utc"),
                notes=(
                    "a previous ticket exists for this device/subject/flow "
                    "but recorded no answer to compare against (an "
                    "incomplete or crashed run)"
                ),
            )
            return

        cause = result.descent.cause
        current_cause = (
            {"rung": cause.rung, "device": cause.device} if cause is not None else None
        )
        current_reason = cause.result.reason if cause is not None else None
        current_finding = result.descent.finding
        current_trustworthy = result.trustworthy

        prev_finding = prev_answer.get("finding")
        prev_trustworthy = prev_answer.get("trustworthy")
        prev_cause_raw = prev_answer.get("cause") or {}
        prev_cause = (
            {"rung": prev_cause_raw.get("rung"), "device": prev_cause_raw.get("device")}
            if prev_answer.get("cause") is not None else None
        )
        prev_reason = prev_cause_raw.get("reason") if prev_answer.get("cause") is not None else None
        prev_rungs = prev_answer.get("rungs")

        finding_changed = current_finding != prev_finding
        cause_changed = current_cause != prev_cause
        trustworthy_flipped = (
            prev_trustworthy is not None and prev_trustworthy != current_trustworthy
        )

        recovered: list[dict] | None = None
        newly_broken: list[dict] | None = None
        rung_comparison = "unavailable"
        if isinstance(prev_rungs, list):
            rung_comparison = "available"
            prev_by_key = {
                (r.get("rung"), r.get("device")): r
                for r in prev_rungs if isinstance(r, dict)
            }
            recovered = []
            newly_broken = []
            for outcome in result.descent.outcomes:
                prev_entry = prev_by_key.get((outcome.rung, outcome.device))
                if prev_entry is None:
                    continue
                prev_status = prev_entry.get("status")
                entry = {
                    "rung": outcome.rung, "device": outcome.device,
                    "previous_reason": prev_entry.get("reason"),
                    "current_reason": outcome.result.reason,
                }
                if prev_status == BROKEN and outcome.status == HEALTHY:
                    recovered.append(entry)
                elif prev_status == HEALTHY and outcome.status == BROKEN:
                    newly_broken.append(entry)

        summary = []
        if finding_changed:
            summary.append(f"finding changed: {prev_finding!r} -> {current_finding!r}")
        if cause_changed:
            summary.append(f"cause changed: {prev_cause!r} -> {current_cause!r}")
        if trustworthy_flipped:
            summary.append(f"trustworthy flipped: {prev_trustworthy!r} -> {current_trustworthy!r}")
        if recovered:
            summary.append(f"{len(recovered)} rung(s) recovered")
        if newly_broken:
            summary.append(f"{len(newly_broken)} rung(s) newly broken")
        notes = "; ".join(summary) if summary else "no change since the previous run"

        handle.record_handover(
            status=_ticket.HANDOVER_COMPARED,
            previous_run_id=prev_parsed.get("run_id"),
            previous_ticket_path=str(prev_path),
            previous_opened_at=prev_header.get("opened_at_utc"),
            previous_finding=prev_finding,
            current_finding=current_finding,
            finding_changed=finding_changed,
            previous_cause=prev_cause,
            current_cause=current_cause,
            previous_reason=prev_reason,
            current_reason=current_reason,
            cause_changed=cause_changed,
            previous_trustworthy=prev_trustworthy,
            current_trustworthy=current_trustworthy,
            trustworthy_flipped=trustworthy_flipped,
            rung_comparison=rung_comparison,
            recovered=recovered,
            newly_broken=newly_broken,
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001 -- bookkeeping never fails a run
        _note(f"# handover not recorded: {exc}", args)


def _record_diagnosis_in_ledger(result, args, subject, flow, run_id=None) -> None:
    """Append this investigation to the accuracy ledger. Never raises."""

    from . import ledger as _ledger

    try:
        cause = result.descent.cause
        write = _ledger.record_diagnosis(
            device=args.device,
            subject=subject,
            flow=flow,
            finding=result.descent.finding,
            trustworthy=result.trustworthy,
            # Required, no default: a fixture replay must never be counted as a
            # live diagnosis in the corpus this ledger exists to build.
            source=(_ledger.SOURCE_FIXTURE if getattr(args, "from_fixtures", False)
                    else _ledger.SOURCE_LIVE),
            # `subject` here is the CAUSE rung's own `CheckResult.subject`
            # (the peer, the interface) -- not this function's own `subject`
            # parameter (the investigation's top-level subject, already
            # passed above) -- see `incident_correlation.py`'s module
            # docstring ("The ledger integration gap (B-486), and what W3a
            # closed"), which names this exact addition:
            # `"subject": cause.result.subject` beside `rung`/`device`/
            # `reason`. `correlate_by_cause` reads it back as `cause_subject`
            # and needs it to tell apart two diagnoses that share a device
            # and rung name but not the same underlying object.
            cause=({"rung": cause.rung, "device": cause.device,
                    "reason": cause.result.reason,
                    "subject": cause.result.subject} if cause is not None else None),
            reason=result.descent.reason,
            report_status=result.report_status,
            correlation_status=result.correlation_status,
            run_id=run_id,
            ledger=_ledger_for_cli(),
        )
    except Exception as exc:  # noqa: BLE001 -- bookkeeping never fails a diagnosis
        _note(f"# accuracy ledger not updated: {exc}", args)
        return
    # `write.id` used to be captured and read only for `.warning` -- never
    # printed, never returned -- even though `ledger verdict --help` already
    # called its argument "the id `investigate` reported" (`diagnosis_id`'s
    # own help text below). Surfaced the same way `write.warning` already is:
    # a stderr note (B-422 -- commentary about the run, not part of the
    # descent's own payload, so it does not belong in `to_payload()`, which is
    # already emitted by the time this runs). Printed even when `write.persisted`
    # is false, matching `_cmd_ledger`'s own "recorded anyway, so a mismatch
    # stays visible" stance a few lines below for the read side of this same id.
    _note(
        f"# Diagnosis recorded: id={write.id} -- to record a human verdict, run "
        f"`nettools ledger verdict {write.id} <confirmed_correct|incorrect|unknown> "
        "--by <name>`",
        args,
    )
    if write.warning:
        _note(f"# {write.warning}", args)


def _session_id(args: argparse.Namespace) -> str:
    """One id per interactive sitting (B-407) -- `session_memory.py`'s own
    docstring names three CLI-layer choices for minting it ("a shell PID, an
    explicit --session flag, a generated value cached in a dotfile") and
    leaves the pick to this file. This is the one that needs no extra state:
    an explicit ``--session`` wins; otherwise the parent process id, which is
    typically the shell that invoked `nettools` and so is stable across every
    invocation from one terminal and distinct across separate ones.
    """

    explicit = getattr(args, "session", None)
    return explicit or str(os.getppid())


def _resolve_it_reference(args: argparse.Namespace) -> int | None:
    """Resolve a literal ``"it"`` DEVICE or SUBJECT against session memory
    (B-407), mutating ``args.device``/``args.subject``/``args.flow`` in
    place on success -- exactly as if the human had typed the resolved
    values directly, so every line below this call in `_cmd_investigate`
    (ticket, ledger, notify, the descent itself) needs no awareness that
    resolution happened. Returns an exit code if the run must abort instead
    (no session recorded, or the store could not be read), or ``None`` to
    continue.

    Case-insensitive exact-token match only -- not sentence parsing. A bare
    "it" inside a free-text question (B-112's `"why can't RR1 reach
    10.255.0.12?"`) is `flow_selection.select_flow`'s problem; this only
    ever fires when DEVICE or SUBJECT *is* the word "it", the shape
    `session_memory.py`'s own docstring names ("a bare 'it' in a follow-up").
    """

    wants_device = args.device.casefold() == "it"
    wants_subject = args.subject.casefold() == "it"
    if not (wants_device or wants_subject):
        return None

    from . import session_memory

    session_id = _session_id(args)
    try:
        recalled = session_memory.recall(session_id)
    except ValueError as exc:
        _emit(_error_envelope(
            "investigate", device=args.device, subject=args.subject,
            errors=[f"invalid --session {session_id!r}: {exc}"],
        ), args)
        return EXIT_CRITICAL

    if recalled.outcome != session_memory.FOUND:
        # NOT_FOUND and CANNOT_RECALL both refuse -- OBS-188/OBS-202's shape,
        # applied here: a store that could not be read must never be treated
        # as "no prior turn" and quietly fall through to reading "it" as a
        # literal (nonexistent) device or subject name.
        reason = (
            "no earlier turn is recorded for this session"
            if recalled.outcome == session_memory.NOT_FOUND
            else recalled.reason
        )
        _emit(_error_envelope(
            "investigate", device=args.device, subject=args.subject,
            errors=[
                f"'it' does not resolve for session {session_id!r}: {reason}",
                "give DEVICE/SUBJECT explicitly, or run an investigation "
                "first so a later 'it' has a turn to point at",
            ],
        ), args)
        return EXIT_CRITICAL

    turn = recalled.turn
    missing = [
        name for name, wants, have in (
            ("device", wants_device, turn.device),
            ("subject", wants_subject, turn.subject),
        )
        if wants and not have
    ]
    if missing:
        _emit(_error_envelope(
            "investigate", device=args.device, subject=args.subject,
            errors=[
                f"'it' does not resolve: the last recorded turn for session "
                f"{session_id!r} has no {' or '.join(missing)}",
            ],
        ), args)
        return EXIT_CRITICAL

    if wants_device:
        _note(
            f"# 'it' resolved to device {turn.device!r} from this session's "
            f"last turn ({turn.recorded_at}).", args,
        )
        args.device = turn.device
    if wants_subject:
        _note(
            f"# 'it' resolved to subject {turn.subject!r} from this session's "
            f"last turn ({turn.recorded_at}).", args,
        )
        args.subject = turn.subject
    # Only when the caller did not already name one -- an explicit --flow is
    # what the human asked for, and a recalled flow must not override it.
    if args.flow is None and turn.flow:
        args.flow = turn.flow
    return None


def _record_session_turn(handle, args, subject, flow) -> None:
    """Remember this turn so a later "it" in the same session can resolve it
    (B-407). Never raises -- a pointer this small must not be able to take
    an investigation down, the same posture `_record_in_ticket` and
    `_record_diagnosis_in_ledger` already take for their own bookkeeping.

    Called after `_open_ticket_for` returns and `subject`/`flow` are
    resolved -- exactly the call `session_memory.py`'s own docstring
    specified in advance.
    """

    from . import session_memory

    try:
        write = session_memory.record_turn(
            _session_id(args),
            device=getattr(args, "device", None),
            subject=subject,
            flow=flow,
            run_id=getattr(handle, "run_id", None),
            ticket_path=str(handle.path) if handle is not None else None,
        )
    except ValueError as exc:  # invalid --session, or a run recording nothing
        _note(f"# session memory not recorded: {exc}", args)
        return
    if not write.persisted:
        _note(f"# session memory not recorded: {write.warning}", args)


def _default_actor() -> str:
    from .network_tools import _resolve_actor

    return _resolve_actor()


def _cmd_ledger(args) -> int:
    """`nettools ledger summary|verdict` -- read the record, or add a human's
    verdict to one diagnosis.

    The tool writes diagnoses; a person writes verdicts. `--by` is required in
    substance (it defaults to the same actor `NETTOOLS_LOG` already records)
    because an accuracy claim with nobody's name on it is not evidence.
    """

    from . import ledger as _ledger

    store = _ledger_for_cli()
    if args.ledger_command == "summary":
        _emit(_ledger.summary(ledger=store), args)
        return 0

    by = args.by or _default_actor()
    result = _ledger.record_verdict(
        args.diagnosis_id, args.outcome, by=by, note=args.note, ledger=store,
    )
    _emit({"tool": "ledger verdict", "id": result.id,
           "diagnosis_id": args.diagnosis_id, "outcome": args.outcome,
           "persisted": result.persisted,
           "diagnosis_found": result.diagnosis_found,
           "warning": result.warning}, args)
    if not result.diagnosis_found:
        _note(f"# no diagnosis {args.diagnosis_id!r} in this ledger -- recorded anyway, "
              "so a mismatch stays visible rather than being refused away", args)

    # W4f: best-effort mirror of this verdict onto the ticket that produced
    # the diagnosis, via the run_id join key W3c wires up. Never affects the
    # ledger verdict's own success above -- same "bookkeeping never fails
    # the primary action" posture `_open_ticket_for`/`_record_in_ticket`
    # already take for their own writes. Wrapped broadly on purpose: a
    # ticket mirror is commentary about where else this verdict landed, not
    # part of the ledger write this command exists to perform.
    try:
        diagnosis_row = next(
            (d for d in store.diagnoses() if d.get("id") == args.diagnosis_id), None
        )
        run_id = diagnosis_row.get("run_id") if diagnosis_row is not None else None
        if run_id is None:
            # Absence is never zero: a diagnosis recorded before W3c wired
            # run_id through (or an unknown diagnosis_id) genuinely has no
            # ticket to mirror onto -- skip silently-but-notably, never an
            # error, and never a fabricated ticket.
            _note(
                "# ledger verdict not mirrored to a ticket: this diagnosis "
                "carries no run_id (recorded before run_id was wired through, "
                "or diagnosis_id not found in this ledger)",
                args,
            )
        else:
            from . import ticket_read as _ticket_read

            path = _ticket_read.find_ticket_path_by_run_id(run_id)
            if path is None:
                _note(
                    f"# ledger verdict not mirrored to a ticket: no ticket "
                    f"found for run_id {run_id!r}",
                    args,
                )
            else:
                from . import ticket as _ticket

                outcome_write = _ticket.record_ticket_outcome(
                    path, args.outcome, by=by, note=args.note,
                )
                if not outcome_write.persisted:
                    _note(f"# ticket outcome write failed: {outcome_write.warning}", args)
    except Exception as exc:  # noqa: BLE001 -- a ticket mirror must never fail the ledger verdict
        _note(f"# ledger verdict not mirrored to a ticket: {exc}", args)

    return 0


def _config_reconciliation_for(args, result, sender) -> dict | None:
    """W5: config_diff.py's intent-vs-observed comparison, opt-in only.

    Returns ``None`` whenever reconciliation did not run for a real reason --
    ``--reconcile-config`` was not given, the finding/flow do not qualify, or
    the underlying evidence could not be gathered. **Absence is never zero**:
    a caller must never mistake this for "checked, found nothing". The
    caller (`_cmd_investigate`) only attaches this as the sibling
    ``"config_reconciliation"`` payload key at all when the flag was given --
    with the flag omitted, the key is absent from the JSON entirely, not
    present-and-null (see that call site).

    Why this precondition and not another
    --------------------------------------
    `config_diff.gather_reconciliation_evidence` needs a SECOND live SSH
    session (B-455: ~8s extra login cost beyond the one investigate() already
    pays for its own evidence epoch) -- firing it on every investigation
    whose finding is `flows.CAUSE_NOT_LOCALISED` would silently add live
    network cost to a run this CLI's own docs describe as
    `--from-fixtures`-compatible and no-lab-required. Hence the explicit
    opt-in, checked first and cheaply, before anything below it runs.

    `interface_kind.interface_scoped_flows()` names every flow whose SUBJECT
    is an interface name -- `interface`, `isis_adjacency`, `ldp_session`,
    never `bgp_session` -- which is exactly what `config_diff.
    diff_isis_adjacency`/`diff_interface_admin_state` both need (a device +
    interface pair). In practice only `isis_adjacency`/`ldp_session` can ever
    reach `cause_not_localised` here: `flows.INTERFACE_FLOW` has a single
    rung, and `descent.py`'s own rule for this finding requires more than one
    rung in the walk (`lowest_index == 0 and len(outcomes) > 1`), so a
    one-rung flow can never produce it. The flow check below is written
    generally against `interface_scoped_flows()` rather than hand-listing the
    two that happen to qualify today, for the same "don't hand-maintain a
    second copy of a fact code already knows" reason that function's own
    docstring gives.

    `sender` is the exact same seam `_cmd_investigate` already built for
    `investigate()` itself -- a fixture-replay sender under `--from-fixtures`,
    `None` (real SSH) otherwise -- so a fixture-backed run stays fixture-
    backed for this axis too, and a missing fixture surfaces the same
    structured way any other fixture-replay gap does: `config_diff`'s own
    `require_parsed` gate turns it into `CANNOT_COMPARE` fields with a stated
    reason, never a crash and never a silently-empty pass (confirmed against
    `tests/fixtures/cisco_xr/*/broken/`, which has no config_isis/
    config_interface captures at all -- only `isis-broken` and `healthy` do).
    """

    if not getattr(args, "reconcile_config", False):
        return None

    _require("flows")
    if result.descent.finding != flows.CAUSE_NOT_LOCALISED:
        _note(
            f"# --reconcile-config: not applicable (finding is "
            f"{result.descent.finding!r}, not cause_not_localised)", args,
        )
        return None

    from . import interface_kind

    if result.flow not in interface_kind.interface_scoped_flows():
        _note(
            f"# --reconcile-config: not applicable ({result.flow!r}'s subject "
            "is not an interface name)", args,
        )
        return None

    cause = result.descent.cause
    device = cause.device if cause is not None else result.device
    interface = result.subject

    try:
        # `from .config_diff import ...` (not `from . import config_diff`)
        # deliberately: `docs/diagrams/facts.config_diff_consumers()` -- the
        # scanner d1.py's self-invalidating guard reads -- only recognises an
        # `ImportFrom` node whose own `module` names `config_diff` (or a
        # plain `Import`), not a bare `from . import config_diff`. Using the
        # form the guard actually detects is deliberate here: this file
        # genuinely becomes config_diff.py's first live consumer, and the
        # diagram's "unwired" pill label is now stale -- see this task's
        # final report for the exact orchestrator hand-off (docs/diagrams/
        # is out of scope for this lane to edit).
        from .config_diff import gather_reconciliation_evidence, reconcile_interface

        evidence = gather_reconciliation_evidence(device, [interface], sender=sender)
        reconciliation = reconcile_interface(evidence, device, interface)
    except Exception as exc:  # noqa: BLE001 -- an opt-in enrichment must never fail the investigation
        _note(f"# --reconcile-config: could not gather reconciliation evidence: {exc}", args)
        return None

    return {
        "device": reconciliation.device,
        "subject": reconciliation.subject,
        "has_disagreement": reconciliation.has_disagreement,
        "fields": [
            {
                "field": f.field,
                "outcome": f.outcome,
                "intent": f.intent,
                "observed": f.observed,
                "reason": f.reason,
                "intent_evidence_key": f.intent_evidence_key,
                "observed_evidence_key": f.observed_evidence_key,
            }
            for f in reconciliation.fields
        ],
    }


def _note_relay_outcome(record: dict, args: argparse.Namespace) -> None:
    """Turn a `relay_policy.relay()` record (B-209b) into `--notify`'s notes.

    **Byte-identical to the pre-B-209b `notifier.notify()` notes whenever
    there is exactly one delivery and it was neither silenced nor
    suppressed** -- which is every default configuration
    (`NETTOOLS_RELAY_STATE_FILE` unset, no ownership table configured):
    `relay()` then resolves to the single default-channel owner, and
    `notify_owner` on that channel defers to `notify()` unchanged (see that
    function's own docstring), so `record["deliveries"][0]` IS `notify()`'s
    own record, plus `"owner"`. `test_notify_default_behaviour_is_byte_
    identical_via_relay` in `tests/test_notifier.py` pins exactly this.

    Two new outcomes have no pre-B-209b equivalent -- `relay()` can decide
    not to attempt delivery at all, either because an operator declared a
    silence (`decision == "silenced"`) or because the de-dup window matched
    an unchanged signature (`decision == "suppressed"`) -- and get their own
    note rather than being forced through the three branches above.
    """

    decision = record["decision"]
    if decision == "silenced":
        silence = record.get("silence") or {}
        _note(
            f"# Notification silenced ({silence.get('reason', 'no reason given')}"
            f", by {silence.get('created_by', 'unknown')}); --notify did not "
            "attempt delivery.",
            args,
        )
        return
    if decision == "suppressed":
        reason = (record.get("dedup") or {}).get("reason", "duplicate within the de-dup window")
        _note(f"# Notification suppressed (de-dup): {reason}", args)
        return

    deliveries = record["deliveries"]
    multi = len(deliveries) > 1
    for delivery in deliveries:
        suffix = f" (owner {delivery['owner']})" if multi and delivery.get("owner") else ""
        if delivery["error"]:
            _note(f"# Notification failed ({delivery['provider']}){suffix}: {delivery['error']}", args)
        elif delivery["attempted"]:
            _note(f"# Notification sent via {delivery['provider']}{suffix}.", args)
        else:
            _note(f"# NETTOOLS_NOTIFIER is 'none'; --notify did nothing{suffix}.", args)


def _cmd_investigate(args: argparse.Namespace) -> int:
    """Run one deterministic investigation and report what it found.

    **Exit codes here answer "is the network broken", not "is the answer
    trustworthy" — and never both.**

    ==== =========================================================
    Code Meaning
    ==== =========================================================
    0    no fault on the dependency path -- `all_layers_healthy`,
         or `no_fault_on_path` (something on the device is broken
         and it does not lie between this device and this subject)
    1    the descent completed and found a fault on the path
    2    no trustworthy answer was produced
    ==== =========================================================

    **No model is called unless ``--paraphrase`` is given (B-439).** The
    finding, the causal chain, the recommendation and the timeline are all
    rendered from the descent's typed fields, so the authoritative answer needs
    no model and does not wait for one — measured live, ~70s of a ~110s run was
    two model calls producing a restatement nothing depends on. A paraphrase is
    still available, still graded, and marked non-authoritative.

    Exit 1 is a problem with the **network**. Exit 2 is a problem with the
    **answer** — `undetermined`, `temporally_incoherent`, a
    collection failure, a flow that could not run. **A withheld paraphrase is
    not on that list**: it cannot be, now that the answer does not come from a
    model.

    `temporally_incoherent` is worth naming here because it is the one that
    reads like a network fault and is not: the rungs were all read, and each
    verdict was true of the instant it was taken. What is missing is any basis
    for treating them as one state, so the fabric may be fine or broken and this
    run cannot say which. That is an answer problem (B-436, `epoch.py`).

    Conflating them is the failure this scheme exists
    to prevent, and the decisive case is not the obvious one: if a grounding
    failure exited 1, a *systematic* grounding regression (a prompt change, a
    model change) would hide in the noise of routine faults. Faults are normal.
    Exit 1 is normal. A model layer that has quietly stopped producing
    verifiable output would look exactly like a fabric with intermittent
    problems, forever.

    Matches `nettools diff`, which already uses "2 = the comparison itself is
    not trustworthy". **It does not match `nettools health`**, where 2 is the
    worst *network* outcome — see the note in `_add_investigate_parser`.

    `coverage_limited` follows the descent's own outcome (0 or 1) and carries
    its caveat in the payload. The descent is deterministic and reached with no
    model; only the correlation is qualified, and downgrading the exit code for
    it would report doubt about a diagnosis that has none.
    """

    _require("flows", "looks_like_sentence", "select_flow", "investigate", "LLMAnalysisError")

    # B-407: a literal "it" DEVICE/SUBJECT resolves against this session's
    # last turn before anything else runs -- no fixtures loaded, no ticket
    # opened, no ledger row, exactly the same posture the REFUSED-flow check
    # just below takes for a named-but-unbuilt flow.
    abort = _resolve_it_reference(args)
    if abort is not None:
        return abort

    # A REFUSED flow is answered before any work starts: no fixtures loaded, no
    # ticket opened, no ledger row. The operator named a real concept and the
    # honest reply is "that is not a descent, here is the surface that answers
    # it" -- not argparse's "invalid choice", which says the word was wrong.
    if args.flow in flows.REFUSED_OBJECT_TYPES:
        print(flows.REFUSED_OBJECT_TYPES[args.flow], file=sys.stderr)
        return 2

    analyst = None
    sender = None

    if args.from_fixtures:
        from .fixtures import fixture_sender

        sender = fixture_sender(label=args.label)
        _note(f"# Fixture replay: label={args.label}, no lab and no model", args)
    elif args.paraphrase and not args.no_model:
        try:
            analyst = _build_analyst()
        except (ValueError, LLMAnalysisError) as exc:
            _note(f"# No model configured ({exc}); running the descent alone.", args)

    # B-112: SUBJECT is read as a sentence only when --flow was omitted *and*
    # it contains whitespace -- a real subject (an IPv4 address or an
    # interface name) never does, so an existing invocation cannot misfire
    # into this path (`looks_like_sentence`'s own docstring). DEVICE is never
    # taken from the sentence: it stays exactly what was passed positionally,
    # cross-checked against the sentence's own device mention rather than
    # replaced by it -- this wiring's job is choosing the FLOW (and, once
    # chosen, the SUBJECT) from prose, not overriding an argument the caller
    # already gave explicitly.
    flow = args.flow
    subject = args.subject
    # The question AS ASKED, before free-text selection resolves it into a
    # typed subject. Without this the sentence a human actually typed is lost
    # (it is overwritten below), and the ticket's whole purpose is to record
    # what was asked, not only what was answered.
    raw_subject = args.subject
    if flow is None and looks_like_sentence(args.subject):
        selection = select_flow(args.subject)
        if not selection.matched:
            # Unmatched is an answer, not an exception (flow_selection.py's
            # module docstring): say what was not understood and what to say
            # instead, the same shape every other error envelope here uses.
            _emit(_error_envelope(
                "investigate", device=args.device, subject=args.subject,
                errors=[
                    f"free-text flow selection did not understand the subject: "
                    f"{selection.reason}",
                    *(f"try: {candidate}" for candidate in selection.candidates),
                ],
            ), args)
            return EXIT_CRITICAL
        flow, subject = selection.flow, selection.subject
        _note(f"# Free-text selection: {selection.reason}", args)
        if selection.device and selection.device != args.device:
            _note(
                f"# Note: the sentence named device {selection.device!r}; "
                f"investigating from {args.device!r} (the DEVICE argument) instead.",
                args,
            )
    elif flow is None:
        flow = "bgp_session"  # the pre-B-112 default, unchanged

    # B-519 audit note: `subject` is deliberately NOT canonicalised here for
    # `interface`/`isis_adjacency`/`ldp_session`
    # (`interface_kind.interface_scoped_flows()`). Every one of those flows'
    # "interface" rung uses `SubjectRule.AS_IS`: `subject` is rendered
    # straight into a `show interfaces {subject}` device command IOS-XR
    # accepts in either spelling, and separately matched by
    # `checks.interface_exists` through `same_interface`, which already
    # tolerates either spelling on its own. Rewriting it here would fix no
    # comparison and would only make `--from-fixtures` (captures keyed by
    # the SHORT spelling) miss a file for a caller who typed the long one.
    # See tests/test_interface_canonicalization.py for the full audit.

    try:
        result = investigate(
            args.device, subject, flow=flow, analyst=analyst, sender=sender
        )
    except (ValueError, KeyError) as exc:
        # A flow that does not exist, or a subject no device owns. The run
        # produced no answer at all, which is exit 2 by the rule above.
        _emit(_error_envelope(
            "investigate", device=args.device, subject=subject, errors=[str(exc)]
        ), args)
        return EXIT_CRITICAL

    # W5: sibling key, never inside `result.to_payload()` -- that dict's
    # shape (and, transitively, `prompt_library.descent_payload()`'s own
    # pinned key set) is frozen behavior this enrichment must not touch.
    # Present (possibly `null`) only when the flag was actually given --
    # absence is never zero, so a run that never asked for this leaves the
    # key out of the JSON entirely rather than always advertising it.
    payload = result.to_payload()
    if getattr(args, "reconcile_config", False):
        payload["config_reconciliation"] = _config_reconciliation_for(args, result, sender)
    _emit(payload, args)

    # B-446: the flight recorder. One markdown file per interaction, every
    # field code-observed. Opened here rather than at the top of the command so
    # the resolved flow/subject are known -- the ticket records what was
    # actually investigated, not what was typed. Opened BEFORE the ledger
    # write below (W3c) so the ledger row can carry this ticket's own
    # `run_id` as its join key -- `ticket.Ticket.run_id`'s own docstring
    # names this the reason it exists.
    ticket_handle = _open_ticket_for(args, subject, flow, entry_point="cli:investigate")

    # B-485: record WHAT was diagnosed. Never whether it was right -- there is
    # no parameter for that, and only a named human can add a verdict later.
    # Bookkeeping must never take down a diagnosis, so a write failure is a
    # note on stderr and nothing more.
    _record_diagnosis_in_ledger(
        result, args, subject, flow, run_id=getattr(ticket_handle, "run_id", None)
    )

    _record_in_ticket(
        ticket_handle, args, result, subject, flow,
        question=(raw_subject if raw_subject != subject else None),
    )
    # B-407: the pointer a later "it" in this same session resolves through
    # -- the same resolved device/subject/flow the ticket above just
    # recorded, plus that ticket's own run_id/path so a later turn can follow
    # it straight to the content, never a second copy of it.
    _record_session_turn(ticket_handle, args, subject, flow)

    for repair in result.repairs:
        _note(f"# Repaired a model response: {repair}", args)
    for reason in result.withheld_because():
        _note(f"# {reason}", args)
    # A qualified answer must never print as an unqualified one (B-454). The
    # caveat is a field on the result rather than something composed here, so a
    # second front end cannot forget it -- the same reasoning as the correlation
    # caveat beside it.
    coherence = result.descent.coherence
    if coherence is not None and coherence.caveat:
        _note(f"# {coherence.caveat}", args)

    # T-035. Delivery is best-effort and structurally cannot carry evidence:
    # `relay` takes the report object and has no parameter a bundle could
    # arrive in. It never raises, so nothing below this line can change because
    # a channel was down -- a run that found the fault and failed to post about
    # it has still found the fault.
    #
    # B-209b: `relay_policy.relay()` is the hardened drop-in for
    # `notifier.notify()` -- same required arguments (`report`, `device=`,
    # `subject=`, `finding=`), with silence/ownership/de-dup layered in front
    # of delivery. `trustworthy`/`cause`/`ticket_id` are passed through so an
    # owner-routed message carries the RCA, not just the alarm (B-681/B-209 --
    # `notifier.notify_owner`'s own docstring names what was silently dropped
    # on a routed channel before that fix). `cause`/`ticket_id` are already in
    # hand here: `payload["cause"]` from the `to_payload()` above, and
    # `ticket_handle.run_id` from the ticket this function already opened.
    if getattr(args, "notify", False):
        _require("relay_policy")

        report = (payload.get("report") or {}).get("content") or {}
        record = relay_policy.relay(
            report,
            device=args.device,
            # The resolved subject, not the raw sentence (B-112): a
            # notification about "why can't RR1 reach 10.255.0.12?" should
            # name "10.255.0.12", same as it would for a normal invocation.
            subject=subject,
            finding=result.descent.finding,
            trustworthy=result.trustworthy,
            cause=payload.get("cause"),
            ticket_id=getattr(ticket_handle, "run_id", None),
        )
        _note_relay_outcome(record, args)

    if not result.trustworthy:
        return EXIT_CRITICAL
    # `no_fault_on_path` is exit 0 alongside `all_layers_healthy`: both mean no
    # fault on the dependency path between this device and this subject. The
    # difference is that the second found broken rungs elsewhere on the device
    # and reports them as observations -- see `--help` and B-428.
    if result.descent.finding in (flows.ALL_LAYERS_HEALTHY, flows.NO_FAULT_ON_PATH):
        return EXIT_OK
    return EXIT_WARNING


def _build_analyst():
    """A model call bound to the configured provider, or raise.

    Deliberately thin. `investigation.investigate` takes `(prompt) -> str` and
    knows nothing about providers, so a provider change never reaches the
    runner and the runner stays testable with a scripted callable.

    `get_provider()` is called here rather than inside the callable so a
    missing key fails *before* the descent runs -- reporting "no model" after
    thirty seconds of device collection is a worse experience than reporting it
    immediately, and the descent then runs deliberately rather than by
    accident.
    """

    _require("get_provider", "complete_prompt")
    provider = get_provider()
    # model/provider ride on the callable so a ticket can record WHICH model
    # produced an exchange, without `investigation.investigate` growing a
    # parameter for it -- the same reason `.usage` rides here.
    return _UsageRecordingAnalyst(
        complete_prompt, model=os.getenv("ANTHROPIC_MODEL"), provider=provider
    )


class _UsageRecordingAnalyst:
    """A ``(RenderedPrompt) -> str`` callable that remembers what its calls cost.

    `investigation.Analyst` stays `Callable[[RenderedPrompt], str]` -- the
    runner must remain drivable by a plain scripted function in tests, and
    threading a usage type through that signature would make every test
    analyst carry machinery it does not need.

    So the usage rides on the *callable* instead, and the runner reads it
    duck-typed after the calls. An analyst without a `.usage` attribute is
    fine and reports nothing, which is what a scripted test analyst is.

    The prompt is passed straight through to `complete_prompt` unopened --
    B-421 depends on that: `complete_prompt` needs the `RenderedPrompt`'s
    `system`/`user` split intact to build a cacheable request, so nothing
    here may collapse it back into one string first.
    """

    def __init__(self, complete, *, model=None, provider=None):
        _require("TokenUsage")
        self.model = model
        self.provider = provider
        self._complete = complete
        self.usage = TokenUsage()

    def __call__(self, prompt) -> str:
        completion = self._complete(prompt)
        self.usage = self.usage + completion.usage
        return completion.text


def _cmd_demo(args: argparse.Namespace) -> int:
    _require("list_devices", "collect_evidence", "analyze_evidence", "LLMAnalysisError")
    device = _resolve_device(args.device)

    print("## Agent Step 1: Discover devices")
    devices = list_devices()
    names = [d["name"] for d in devices.get("data", {}).get("devices", [])]
    print(f"Available devices: {', '.join(names)}")
    if device not in names:
        # C4: name the valid set in the refusal itself -- `names` is right
        # there, already computed, and the line above prints it too, but a
        # refusal that stops at "not found" without repeating what WOULD
        # have worked makes a reader scroll back up to answer the very
        # question the message exists to answer.
        print(f"Device not found: {device!r}; known devices: {', '.join(sorted(names))}")
        return EXIT_CRITICAL

    print("\n## Agent Step 2: Collect approved evidence")
    evidence = collect_evidence(device)
    print("Facts, interfaces, BGP, LLDP, IS-IS, and SR-TE state collected.")

    print("\n## Agent Step 3: Analyze evidence")
    try:
        print(analyze_evidence(evidence))
    except ValueError as exc:
        # Provider misconfiguration from get_provider(): nothing could be attempted.
        print(f"Analysis error: {exc}")
        return EXIT_CRITICAL
    except LLMAnalysisError as exc:
        print(f"Analysis error: {exc}")
        return EXIT_WARNING
    return EXIT_OK


def _cmd_diff(args: argparse.Namespace) -> int:
    _require(
        "load_golden_snapshot", "load_latest_snapshot", "collect_evidence",
        "save_snapshot", "diff_evidence",
    )
    device = _resolve_device(args.device)
    if args.against == "golden":
        previous = load_golden_snapshot(device)
        missing_message = (
            f"No golden snapshot pinned for {device}; run `nettools baseline pin {device}` first."
        )
    else:
        previous = load_latest_snapshot(device)
        missing_message = f"No previous snapshot for {device}; baseline established."

    current = collect_evidence(device)
    path = save_snapshot(current)
    _note(f"# Snapshot saved: {path}", args)
    if previous is None:
        _note(missing_message, args)
        return EXIT_OK

    result = diff_evidence(previous, current)
    _emit(result, args)

    # Unix `diff --exit-code`-style: 0 nothing differs, 1 real differences,
    # 2 the comparison itself is not trustworthy (an intent failed to collect
    # on one side or the other).
    if result["failed"]:
        return EXIT_CRITICAL
    if result["changed"] or result["added"] or result["removed"]:
        return EXIT_WARNING
    return EXIT_OK


def _cmd_baseline_pin(args: argparse.Namespace) -> int:
    _require("load_latest_snapshot", "collect_evidence", "save_snapshot", "save_golden_snapshot")
    device = _resolve_device(args.device)
    if args.from_latest:
        evidence = load_latest_snapshot(device)
        if evidence is None:
            print(
                f"No saved snapshot for {device} to pin; run `nettools diff {device}` "
                "first, or omit --from-latest to collect fresh evidence now."
            )
            return EXIT_WARNING
    else:
        evidence = collect_evidence(device)
        save_snapshot(evidence)

    path = save_golden_snapshot(evidence)
    print(f"# Golden snapshot pinned: {path}")
    return EXIT_OK


def _cmd_baseline_show(args: argparse.Namespace) -> int:
    _require("load_golden_snapshot")
    device = _resolve_device(args.device)
    evidence = load_golden_snapshot(device)
    if evidence is None:
        _note(f"No golden snapshot pinned for {device}.", args)
        return EXIT_WARNING
    _emit(evidence, args)
    return EXIT_OK


def _cmd_flaps(args: argparse.Namespace) -> int:
    _require("detect_flaps")
    device = _resolve_device(args.device)
    result = detect_flaps(device, min_transitions=args.min_transitions)
    _emit(result, args)
    return EXIT_OK if not result["flapping"] else EXIT_WARNING


def _cmd_evidence_prune(args: argparse.Namespace) -> int:
    if args.keep_days is None and args.keep_count is None:
        _note("Nothing to do: pass --keep-days and/or --keep-count.", args)
        return EXIT_WARNING
    _require("prune_snapshots")
    result = prune_snapshots(
        device_name=args.device, keep_days=args.keep_days, keep_count=args.keep_count
    )
    _emit(result, args)
    return EXIT_OK


def _cmd_evidence_history(args: argparse.Namespace) -> int:
    _require("list_snapshot_history")
    device = _resolve_device(args.device)
    history = list_snapshot_history(device)
    _emit({"device": device, "count": len(history), "history": history}, args)
    return EXIT_OK


def _cmd_health(args: argparse.Namespace) -> int:
    _require(
        "list_devices", "load_fixture_evidence", "collect_evidence",
        "evaluate_fabric_with_silences", "severity_rank", "exit_code_for_severity", "output",
    )
    if args.all:
        listed = list_devices()
        if listed.get("status") != "success":
            print(output.render(listed, "json"))
            return EXIT_CRITICAL
        names = [device["name"] for device in listed["data"]["devices"]]
    else:
        names = args.devices or [_resolve_device(None)]

    if args.from_fixtures:
        evidence_by_device = {name: load_fixture_evidence(name, label=args.label) for name in names}
    else:
        evidence_by_device = {name: collect_evidence(name) for name in names}

    # W1/W2b (release-1.0 cleanup): every finding still passes through
    # `evaluate_device`/`evaluate_fabric` unchanged -- this only adds the
    # annotation pass `evaluate_fabric_with_silences` already performs on top
    # of it. `--silence-file` (W2b), when given, wins; omitted, resolution
    # falls through to this project's own env-var fallback for the same
    # purpose (`health._resolve_silences`'s own documented order). With
    # neither configured this is byte-for-byte the old `evaluate_fabric`
    # result plus the always-present `silenced: False` tag and the
    # `raw_severity`/`counts`/`silences_applied` fields the annotation pass
    # always adds.
    result = evaluate_fabric_with_silences(evidence_by_device, silence_path=args.silence_file)

    # One JSON document, like every other subcommand: a stream of concatenated
    # pretty-printed objects is not parseable, and `nettools health --all | jq`
    # is the whole point of having exit codes. --min-severity filters which
    # devices appear, but the fabric severity is always computed over all of
    # them, so filtering the view can never soften the verdict.
    threshold = severity_rank(args.min_severity)
    reported = {
        name: verdict
        for name, verdict in result["devices"].items()
        if severity_rank(verdict["severity"]) >= threshold
    }
    _emit(
        {
            "severity": result["severity"],
            "counts": result.get("counts", {}),
            "devices": reported,
            "suppressed": sorted(set(result["devices"]) - set(reported)),
        },
        args,
    )
    return exit_code_for_severity(result["severity"])


def _cmd_capture(args: argparse.Namespace) -> int:
    _require("list_devices", "capture_device", "output")
    if args.all:
        listed = list_devices()
        if listed.get("status") != "success":
            print(output.render(listed, "json"))
            return EXIT_CRITICAL
        devices = [device["name"] for device in listed["data"]["devices"]]
    else:
        devices = args.devices or [_resolve_device(None)]

    captures = [
        capture_device(
            name,
            label=args.label,
            base_dir=args.out,
            scrub=not args.no_scrub,
            templates=args.templates,
        )
        for name in devices
    ]
    print(output.render({"label": args.label, "captures": captures}, "json"))
    # A partial capture must not look like a clean one.
    return EXIT_OK if all(not capture["errors"] for capture in captures) else EXIT_WARNING


def _cmd_learn_topology(args: argparse.Namespace) -> int:
    """Derive expected topology counts from evidence and report fabric anomalies.

    Reports by default and writes nothing: the inventory YAML is hand-maintained
    and carries explanatory comments that a PyYAML round-trip would silently
    discard. ``--out`` writes a generated draft to a new path for review;
    ``--write`` edits the resolved inventory in place and says what it costs.

    Always exits 0 -- an inconsistent fabric is not a tool failure -- but the
    anomaly report is printed unconditionally so it cannot be missed even when
    every device's counts derive cleanly.
    """

    _require(
        "list_devices", "collect_evidence", "load_fixture_evidence", "derive_expected",
        "resolve_inventory_path", "update_expected_in_yaml", "output",
        "format_anomaly_report", "build_anomaly_report",
    )
    listed = list_devices()
    if listed.get("status") != "success":
        print(output.render(listed, "json"))
        return EXIT_CRITICAL
    names = [device["name"] for device in listed["data"]["devices"]]

    if args.live:
        evidence_by_device = {name: collect_evidence(name) for name in names}
    else:
        evidence_by_device = {name: load_fixture_evidence(name, label=args.label) for name in names}

    derived = derive_expected(evidence_by_device)
    source_path = resolve_inventory_path()

    if args.out or args.write:
        out_path = Path(args.out) if args.out else source_path
        update_expected_in_yaml(source_path, derived, out_path=out_path)
        print(f"# Wrote expected topology for {len(derived)} device(s) to {out_path}")
        print("# Note: comments in the source YAML are not preserved by this rewrite.\n")
    else:
        print(f"# Derived expected topology for {len(derived)} device(s) (nothing written).")
        print(f"# Compare against {source_path}; use --out PATH for a draft or --write to apply.\n")
        print(output.render(derived, "json"))
        print()

    print(format_anomaly_report(build_anomaly_report(evidence_by_device)))
    return EXIT_OK


def _cmd_metrics(args: argparse.Namespace) -> int:
    """Report operational metrics: always exits 0 -- this is a report, not a check."""

    if args.quiet:
        return EXIT_OK
    _require("metrics")
    if args.format == "prometheus":
        print(metrics.render_prometheus_text(), end="")
    else:
        print(metrics.render_json())
    return EXIT_OK


def _cmd_audit(args: argparse.Namespace) -> int:
    """Deterministic fabric audit: the fabric judged against itself. B-477.

    Cross-device consistency — duplicate router-IDs, MTU mismatch across
    adjacencies, configured-but-dead BGP — none of it visible from any single
    device's evidence. Exit codes on the health scheme: 0 ok/info, 1 warning,
    2 critical.
    """

    from .audit import run_audit
    from .lab import all_devices  # credential-free: fixture replay needs no .env

    _require("load_fixture_evidence", "collect_evidence")
    names = list(all_devices())
    if args.from_fixtures:
        evidence_by_device = {
            name: load_fixture_evidence(name, label=args.label) for name in names
        }
        _note(f"# Fixture replay: label={args.label}, no lab", args)
    else:
        import concurrent.futures as _cf
        workers = max(1, min(8, len(names)))
        with _cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {name: pool.submit(collect_evidence, name) for name in names}
            evidence_by_device = {name: futures[name].result() for name in names}

    result = run_audit(evidence_by_device)
    _emit(result, args)
    if result["severity"] == "critical":
        return EXIT_CRITICAL
    if result["severity"] == "warning":
        return EXIT_WARNING
    return EXIT_OK


def _cmd_config(args: argparse.Namespace) -> int:
    """Inspect (``show``) or validate (``check``) the environment configuration.

    B-476/P2-02: report-only. ``show`` renders ``settings.effective_config()``
    through the standard ``output.render`` path, same as every other
    structured command. ``check`` prints each problem ``settings.
    validate_environment()`` finds and exits ``1``; a clean environment
    prints nothing extra and exits ``0`` -- the same 0/1 shape ``nettools
    health`` uses for "nothing actionable" vs. "found a problem".
    """

    _require("settings")
    if args.config_command == "check":
        problems = settings.validate_environment()
        if not getattr(args, "quiet", False):
            if problems:
                for problem in problems:
                    print(problem)
            else:
                print("OK: no configuration problems found.")
        return EXIT_WARNING if problems else EXIT_OK

    _emit(settings.effective_config(), args)
    return EXIT_OK


def _cmd_route_event(args: argparse.Namespace) -> int:
    """Route one inbound event to an investigation, or say why not. B-480.

    Reads the raw event from --file or stdin. Exit codes let an orchestrator
    branch without parsing: 0 at least one decision is routable, 1 none is,
    2 the input could not be read at all. The routing itself is
    `event_routing.route_event` — a pure table lookup, never a model.
    """

    from .event_routing import route_event

    try:
        if getattr(args, "file", None):
            raw = Path(args.file).read_text(encoding="utf-8")
        else:
            raw = sys.stdin.read()
    except OSError as exc:
        _emit(_error_envelope("route_event", errors=[str(exc)]), args)
        return EXIT_CRITICAL

    decisions = route_event(raw, device=getattr(args, "device", None))
    _emit({
        "tool": "route_event",
        "status": "success",
        "decisions": [d.as_dict() for d in decisions],
        "routable_count": sum(1 for d in decisions if d.routable),
    }, args)
    return EXIT_OK if any(d.routable for d in decisions) else EXIT_WARNING


def _cmd_watch(args: argparse.Namespace) -> int:
    """W6: the read-only dry-run surface `event_watch.py` already builds,
    reachable through `nettools` instead of only `python -m agent_nettools.
    event_watch`. **Never runs `investigate`, never opens a ticket, never
    writes anything** -- `watch_device`/`watch_fabric` fetch a Loki window,
    collapse repeated lines from one root cause, and return a routing
    DECISION, never a call (see event_watch.py's own module docstring,
    "What this module is not"). This command only prints that decision.

    Exit codes follow this project's own scheme, applied to the same three
    outcomes `event_watch._main` already distinguishes: 2 -- every device's
    fetch failed, so nothing here is trustworthy (the "could not run at all"
    bucket); 1 -- at least one collapsed group is routable (something WOULD
    fire, if a human wired this up to `investigate`); 0 -- ran cleanly and
    nothing is routable.
    """

    from .event_watch import watch_device, watch_fabric

    if args.all:
        reports = watch_fabric(since_seconds=args.since_seconds, limit=args.limit)
    else:
        device = _resolve_device(args.device)
        reports = (watch_device(device, since_seconds=args.since_seconds, limit=args.limit),)

    _emit({
        "tool": "watch",
        "reports": [r.as_dict() for r in reports],
        "routable_count": sum(r.routable_count() for r in reports),
    }, args)

    if reports and all(r.status == "error" for r in reports):
        return EXIT_CRITICAL
    if sum(r.routable_count() for r in reports):
        return EXIT_WARNING
    return EXIT_OK


def _cmd_version(args: argparse.Namespace) -> int:
    import platform as platform_module

    payload = {
        "nettools_version": __version__,
        "python_version": platform_module.python_version(),
        "platform": platform_module.platform(),
    }
    _emit(payload, args)
    return EXIT_OK


def _cmd_inspect(args: argparse.Namespace) -> int:
    # Imported lazily so the rest of the CLI works without the MCP SDK installed.
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    device = _resolve_device(args.device)

    async def run() -> None:
        # The MCP SDK's stdio_client forwards only get_default_environment()
        # plus an explicit env= -- NOT the caller's os.environ. Without this,
        # `NETTOOLS_MCP_SURFACE=staged nettools inspect` silently listed the
        # classic surface: the one smoke test that existed could never test
        # the surface it was asked for (operator walkthrough, stumble 7).
        import os as _os

        forwarded = {
            name: value for name, value in _os.environ.items()
            if name.startswith("NETTOOLS_") or name in (
                "DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE",
            )
        }
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_server.server"], env=forwarded
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                print("=== TOOLS EXPOSED BY THE SERVER ===")
                for tool in tools.tools:
                    summary = (tool.description or "").splitlines()
                    print(f"  - {tool.name}: {summary[0] if summary else ''}")

                resources = await session.list_resources()
                print("\n=== RESOURCES EXPOSED BY THE SERVER ===")
                for resource in resources.resources:
                    print(f"  - {resource.uri}: {resource.description or ''}")

                prompts = await session.list_prompts()
                print("\n=== PROMPTS EXPOSED BY THE SERVER ===")
                for prompt in prompts.prompts:
                    print(f"  - {prompt.name}: {prompt.description or ''}")

                print("\n=== CALL: list_lab_devices ===")
                result = await session.call_tool("list_lab_devices", {})
                for block in result.content:
                    print(getattr(block, "text", block))

                print(f"\n=== CALL: check_lab_bgp_neighbors (device_name={device}) ===")
                result = await session.call_tool(
                    "check_lab_bgp_neighbors", {"device_name": device}
                )
                for block in result.content:
                    print(getattr(block, "text", block))

                print(f"\n=== CALL: assess_lab_device_health (device_name={device}) ===")
                result = await session.call_tool(
                    "assess_lab_device_health", {"device_name": device}
                )
                for block in result.content:
                    print(getattr(block, "text", block))

    asyncio.run(run())
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    # `CHECK_TOOLS` drives the per-check subparsers below and `fabric`'s own
    # choices; `flows` drives `investigate`'s --flow choices further down.
    # Neither is imported at `import agent_nettools.cli` time any more, but
    # every real invocation calls this function (from `main()`), so this is
    # the one place both genuinely need to resolve, once, regardless of
    # which subcommand is actually invoked.
    _require("CHECK_TOOLS", "flows")
    parser = argparse.ArgumentParser(
        prog="nettools",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inventory = sub.add_parser("inventory", help="List devices without credentials.")
    _add_output_arguments(p_inventory)
    p_inventory.set_defaults(func=_cmd_inventory)

    for check in sorted(CHECK_TOOLS):
        p = sub.add_parser(check, help=f"Run the {check} check on a device.")
        p.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
        _add_output_arguments(p)
        p.set_defaults(func=_cmd_check, check=check)

    p_fabric = sub.add_parser("fabric", help="Run a check across the whole inventory.")
    p_fabric.add_argument(
        "check", nargs="?", default="bgp", choices=sorted(CHECK_TOOLS), help="Check to run."
    )
    _add_output_arguments(p_fabric)
    p_fabric.set_defaults(func=_cmd_fabric)

    p_route = sub.add_parser("route", help="Look up a specific route (validated template).")
    p_route.add_argument("device", help="Device name.")
    p_route.add_argument("prefix", help="IPv4 address or prefix, e.g. 10.0.0.0/24.")
    _add_output_arguments(p_route)
    p_route.set_defaults(func=_cmd_route)

    p_bgp_neighbor = sub.add_parser(
        "bgp-neighbor", help="Look up a specific BGP neighbor (validated template)."
    )
    p_bgp_neighbor.add_argument("device", help="Device name.")
    p_bgp_neighbor.add_argument("address", help="Neighbor IPv4 address.")
    _add_output_arguments(p_bgp_neighbor)
    p_bgp_neighbor.set_defaults(func=_cmd_bgp_neighbor)

    p_interface = sub.add_parser(
        "interface", help="Look up a specific interface's status (validated template)."
    )
    p_interface.add_argument("device", help="Device name.")
    p_interface.add_argument("name", help="Interface name, e.g. GigabitEthernet0/0/0/1.")
    _add_output_arguments(p_interface)
    p_interface.set_defaults(func=_cmd_interface)

    p_sr_policy = sub.add_parser(
        "sr-policy",
        help="Look up one SR-TE policy's candidate path/SID detail (validated template).",
    )
    p_sr_policy.add_argument("device", help="Device name.")
    p_sr_policy.add_argument(
        "policy_id",
        help='SR-TE policy id as "<color>:<endpoint>", e.g. "20:10.255.0.13" -- '
        'the same shape `nettools sr`\'s own "policy" field reports.',
    )
    _add_output_arguments(p_sr_policy)
    p_sr_policy.set_defaults(func=_cmd_sr_policy)

    p_logging = sub.add_parser(
        "logging", help="Show recent log lines (validated template)."
    )
    p_logging.add_argument("device", help="Device name.")
    p_logging.add_argument(
        "--count", type=int, default=20, help="Number of log lines, 1-500 (default: 20)."
    )
    _add_output_arguments(p_logging)
    p_logging.set_defaults(func=_cmd_logging)

    p_ping = sub.add_parser(
        "ping",
        help="Ping an IPv4 address from a device (validated template; active probe -- see "
        "NETTOOLS_ALLOW_ACTIVE_PROBES).",
    )
    p_ping.add_argument("device", help="Device name.")
    p_ping.add_argument("address", help="Target IPv4 address.")
    _add_output_arguments(p_ping)
    p_ping.set_defaults(func=_cmd_ping)

    p_traceroute = sub.add_parser(
        "traceroute",
        help="Traceroute to an IPv4 address from a device (validated template; active probe -- "
        "see NETTOOLS_ALLOW_ACTIVE_PROBES).",
    )
    p_traceroute.add_argument("device", help="Device name.")
    p_traceroute.add_argument("address", help="Target IPv4 address.")
    _add_output_arguments(p_traceroute)
    p_traceroute.set_defaults(func=_cmd_traceroute)

    p_analyze = sub.add_parser("analyze", help="Collect evidence and analyze with the LLM.")
    p_analyze.add_argument("device", nargs="?", help="Device name; defaults to PE1. Ignored with --fabric.")
    p_analyze.add_argument("--show-evidence", action="store_true", help="Print evidence first.")
    p_analyze.add_argument("--save", action="store_true", help="Save an evidence snapshot.")
    p_analyze.add_argument(
        "--fabric",
        action="store_true",
        help="Analyze every inventory device together, correlating findings across devices.",
    )
    p_analyze.set_defaults(func=_cmd_analyze)

    p_agent = sub.add_parser(
        "agent",
        help=(
            "DISABLED BY DEFAULT (NETTOOLS_ENABLE_AGENT=true to enable). Answer a "
            "question with a bounded, tool-calling agent loop (Anthropic only). "
            "Exploratory: model-selected tools and prose, not the deterministic "
            "`investigate` path -- prefer `nettools investigate` unless you "
            "specifically need this."
        ),
        description=(
            "B-488: quarantined behind an explicit opt-in, off by default. WHAT this "
            "is: a free-form, model-driven tool-calling loop (Anthropic only) that "
            "chooses its own tools and writes its own prose answer. WHY it is gated: "
            "this project's central claim is that the model only navigates a menu "
            "and never synthesises a diagnosis -- this command is the one place "
            "that is not true, and it is unreliable on local models (see the "
            "README). Prefer `nettools investigate`: a deterministic dependency "
            "descent with no model in the diagnosis path. Set "
            "NETTOOLS_ENABLE_AGENT=true (or 1) to run this command anyway."
        ),
    )
    p_agent.add_argument("question", help="The question to investigate and answer.")
    p_agent.add_argument("--device", help="Optional device to focus the investigation on.")
    p_agent.add_argument(
        "--max-iterations", type=int, default=8, help="Maximum agent loop iterations (default: 8)."
    )
    p_agent.add_argument(
        "--time-budget",
        type=float,
        default=120,
        help=(
            "Wall-clock budget in seconds (default: 120). Enforced at every turn "
            "boundary and before every tool dispatch; a single in-flight model call "
            "is bounded only by a timeout ceiling, not this budget exactly -- see "
            "the returned 'overran_budget' field."
        ),
    )
    p_agent.set_defaults(func=_cmd_agent)

    p_investigate = sub.add_parser(
        "investigate",
        help="Deterministically descend a flow's dependency stack and report the cause.",
        description=(
            "Exit codes answer a different question here than in `nettools health`. "
            "0 = no fault on the dependency path -- either everything is healthy, or "
            "something on the device is broken but does not lie between this device and "
            "this subject (`no_fault_on_path`); 1 = a fault was found on the path "
            "(a problem with the NETWORK); 2 = no trustworthy answer was produced "
            "-- undetermined, a withheld report, or a run that could not complete (a "
            "problem with the ANSWER). This matches `nettools diff`. It does NOT match "
            "`nettools health`, where 2 is the worst network outcome; a script calling "
            "both must not assume one scheme.\n\n"
            "B-112: if --flow is omitted and SUBJECT contains whitespace, it is read "
            "as a sentence (e.g. \"why can't RR1 reach 10.255.0.12?\") and a declared "
            "table of phrases -- never a model -- selects the flow and re-derives the "
            "device and subject from it; an unmatched sentence refuses with a reason "
            "and never guesses. A SUBJECT with no whitespace behaves exactly as before."
        ),
    )
    p_investigate.add_argument("device", help="Device the investigation starts from.")
    p_investigate.add_argument(
        "subject",
        help=(
            "The object under investigation, e.g. a peer address -- or, with --flow "
            "omitted, a sentence naming the flow, e.g. \"why can't RR1 reach "
            "10.255.0.12?\" (B-112)."
        ),
    )
    p_investigate.add_argument(
        "--flow", default=None,
        # Refused flows are accepted as CHOICES so the parser does not
        # reject them as unknown words -- `_cmd_investigate` answers them
        # with the reason and a pointer to the real surface. See
        # flows.REFUSED_OBJECT_TYPES.
        choices=sorted(set(flows.FLOWS) | set(flows.REFUSED_OBJECT_TYPES)),
        help=(
            "Which dependency ladder to descend (default: bgp_session, unless "
            "SUBJECT reads as a sentence, in which case B-112 free-text "
            "selection picks the flow -- see `nettools investigate --help`'s "
            "description)."
        ),
    )
    p_investigate.add_argument(
        "--from-fixtures", action="store_true",
        help="Replay committed fixtures instead of touching the lab. Needs no devices "
             "and no API key, and skips the model steps entirely.",
    )
    p_investigate.add_argument(
        "--label", default="broken",
        help="Fixture label to replay with --from-fixtures (default: broken).",
    )
    p_investigate.add_argument(
        "--no-model", action="store_true",
        help=(
            "Accepted and now the default: no model is called unless --paraphrase "
            "is given. Kept so existing scripts and habits keep working."
        ),
    )
    p_investigate.add_argument(
        "--paraphrase", action="store_true",
        help=(
            "Additionally ask a model for a readable restatement of the report and "
            "timeline. Non-authoritative, graded, and roughly 70s slower. The "
            "finding, the causal chain and the timeline are produced without it."
        ),
    )
    _add_output_arguments(p_investigate)
    p_investigate.add_argument(
        "--notify",
        action="store_true",
        help="Send the report to the channel NETTOOLS_NOTIFIER selects. Delivery is "
             "best-effort: a failure is reported and never changes the exit code. "
             "With the default provider ('none') this is a no-op, not an error, so "
             "the flag is safe in a cron entry written before a channel exists.",
    )
    p_investigate.add_argument(
        "--session", default=None,
        help="Session id session memory (B-407) is keyed by -- ties a literal "
             "\"it\" DEVICE/SUBJECT to an earlier turn in the same sitting, and "
             "labels the turn this run records for a later one to resolve. "
             "Default: the parent process id (stable across every invocation "
             "from one shell, distinct across separate ones).",
    )
    p_investigate.add_argument(
        "--reconcile-config",
        action="store_true",
        help=(
            "W5, opt-in only (default off): compare configured intent against "
            "observed state (config_diff.py) for the interface this descent "
            "bottomed out on, when -- and only when -- the finding is "
            "cause_not_localised on a flow whose subject is an interface name "
            "(isis_adjacency, ldp_session). This is a SECOND live SSH login "
            "(~8s extra per B-455's own measurement), never fired unless asked "
            "for. Adds a sibling 'config_reconciliation' key to the emitted "
            "JSON; absent entirely without this flag, and null when the flag "
            "is given but does not apply or the evidence could not be read."
        ),
    )
    p_investigate.set_defaults(func=_cmd_investigate)

    p_demo = sub.add_parser("demo", help="Run the narrated agent demo.")
    p_demo.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_demo.set_defaults(func=_cmd_demo)

    p_diff = sub.add_parser(
        "diff",
        help="Diff current evidence against a saved snapshot. Exit codes: 0 no differences, "
        "1 differences found, 2 could not fully compare.",
    )
    p_diff.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_diff.add_argument(
        "--against",
        choices=("golden", "latest"),
        default="latest",
        help="Compare against the pinned golden snapshot or the most recent one (default: latest).",
    )
    _add_output_arguments(p_diff)
    p_diff.set_defaults(func=_cmd_diff)

    p_capture = sub.add_parser("capture", help="Capture real device output as test fixtures.")
    p_capture.add_argument("devices", nargs="*", help="Device names; defaults to PE1.")
    p_capture.add_argument("--all", action="store_true", help="Capture every inventory device.")
    p_capture.add_argument("--label", default="t0", help="Capture label (default: t0).")
    p_capture.add_argument("--out", help="Fixture root; defaults to tests/fixtures.")
    p_capture.add_argument(
        "--templates",
        action="store_true",
        help=(
            "Also capture parameterized template output (show bgp neighbor, show route, "
            "show interfaces <name>, show logging, ping, traceroute). Off by default so the "
            "existing intent-only capture is unchanged. Adds one SSH session per device, not "
            "one per command."
        ),
    )
    p_capture.add_argument(
        "--no-scrub",
        action="store_true",
        help="Write raw output without scrubbing. For local inspection only; never commit.",
    )
    p_capture.set_defaults(func=_cmd_capture)

    p_learn = sub.add_parser(
        "learn-topology",
        help="Derive expected topology counts from evidence and update the inventory.",
    )
    source = p_learn.add_mutually_exclusive_group()
    source.add_argument(
        "--from-fixtures",
        action="store_false",
        dest="live",
        default=False,
        help="Derive from committed test fixtures (default).",
    )
    source.add_argument(
        "--live", action="store_true", dest="live", help="Derive from a live collection."
    )
    p_learn.add_argument("--label", default="t0", help="Fixture label to use (default: t0).")
    p_learn.add_argument("--out", help="Write a generated draft inventory to this new path.")
    p_learn.add_argument(
        "--write",
        action="store_true",
        help="Rewrite the resolved inventory in place. Discards its comments.",
    )
    p_learn.set_defaults(func=_cmd_learn_topology)

    p_health = sub.add_parser(
        "health",
        help="Evaluate deterministic health verdicts. Exit codes: 0 ok/info, 1 warning, 2 critical.",
    )
    p_health.add_argument("devices", nargs="*", help="Device names; defaults to PE1 (or use --all).")
    p_health.add_argument("--all", action="store_true", help="Evaluate every inventory device.")
    p_health.add_argument(
        "--from-fixtures",
        action="store_true",
        help="Evaluate committed test fixtures instead of a live collection.",
    )
    p_health.add_argument(
        "--label", default="t0", help="Fixture label to use with --from-fixtures (default: t0)."
    )
    p_health.add_argument(
        "--min-severity",
        default="ok",
        choices=("ok", "info", "warning", "critical"),
        help="Only print devices at or above this severity (default: ok, i.e. every device).",
    )
    p_health.add_argument(
        "--silence-file",
        default=None,
        help=(
            "Path to a silence rules file (W2b). An explicit flag here wins over "
            "this project's own env-var fallback for the same purpose (see "
            "health._resolve_silences); omit it and that fallback still applies "
            "when it is set. Neither present: no silences are applied, unchanged "
            "from before this flag existed."
        ),
    )
    _add_output_arguments(p_health)
    p_health.set_defaults(func=_cmd_health)

    p_baseline = sub.add_parser("baseline", help="Manage per-device pinned golden snapshots.")
    baseline_sub = p_baseline.add_subparsers(dest="baseline_command", required=True)

    p_baseline_pin = baseline_sub.add_parser("pin", help="Pin a golden snapshot for a device.")
    p_baseline_pin.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_baseline_pin.add_argument(
        "--from-latest",
        action="store_true",
        help="Pin the most recently saved snapshot instead of collecting a fresh one.",
    )
    p_baseline_pin.set_defaults(func=_cmd_baseline_pin)

    p_baseline_show = baseline_sub.add_parser("show", help="Print a device's pinned golden snapshot.")
    p_baseline_show.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    _add_output_arguments(p_baseline_show)
    p_baseline_show.set_defaults(func=_cmd_baseline_show)

    p_flaps = sub.add_parser(
        "flaps", help="Detect oscillating fields across a device's saved snapshot history."
    )
    p_flaps.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_flaps.add_argument(
        "--min-transitions",
        type=int,
        default=3,
        help="Minimum value changes before a field is reported as flapping (default: 3).",
    )
    _add_output_arguments(p_flaps)
    p_flaps.set_defaults(func=_cmd_flaps)

    p_evidence = sub.add_parser("evidence", help="Manage stored evidence snapshots (Phase 7).")
    evidence_sub = p_evidence.add_subparsers(dest="evidence_command", required=True)

    p_evidence_prune = evidence_sub.add_parser(
        "prune", help="Delete timestamped snapshots outside a retention window."
    )
    p_evidence_prune.add_argument(
        "--keep-days", type=float, default=None, help="Keep snapshots from the last N days."
    )
    p_evidence_prune.add_argument(
        "--keep-count", type=int, default=None, help="Keep the N most recent snapshots per device."
    )
    p_evidence_prune.add_argument(
        "--device", help="Prune only this device; defaults to every device in the store."
    )
    _add_output_arguments(p_evidence_prune)
    p_evidence_prune.set_defaults(func=_cmd_evidence_prune)

    p_evidence_history = evidence_sub.add_parser(
        "history", help="List a device's saved timestamped snapshots, oldest first."
    )
    p_evidence_history.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    _add_output_arguments(p_evidence_history)
    p_evidence_history.set_defaults(func=_cmd_evidence_history)

    p_metrics = sub.add_parser(
        "metrics",
        help="Report operational metrics: per-device collection outcomes/latency/retries and "
        "health verdict counts by severity. Always exits 0.",
    )
    p_metrics.add_argument(
        "--format",
        choices=("json", "prometheus"),
        default="json",
        help="Output form (default: json).",
    )
    p_metrics.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress output; still exits 0."
    )
    p_metrics.set_defaults(func=_cmd_metrics)

    # B-485. Two verbs, deliberately: the tool appends diagnoses on its own and
    # a human appends verdicts on them. There is no verb that lets the tool
    # score itself.
    p_ledger = sub.add_parser(
        "ledger",
        help="The diagnosis accuracy ledger: `summary` reports the record "
             "(including how many diagnoses nobody has judged yet), `verdict` "
             "records a human's judgement of one diagnosis. Always exits 0.",
    )
    ledger_sub = p_ledger.add_subparsers(dest="ledger_command", required=True)

    p_ledger_summary = ledger_sub.add_parser(
        "summary", help="Counts by outcome; `unknown` is always shown."
    )
    p_ledger_verdict = ledger_sub.add_parser(
        "verdict", help="Record whether one diagnosis was right. Only a human does this."
    )
    p_ledger_verdict.add_argument("diagnosis_id", help="The id `investigate` reported.")
    p_ledger_verdict.add_argument(
        "outcome", choices=("confirmed_correct", "incorrect", "unknown"),
        help="Typo-proofed at the shell rather than deep in the module.",
    )
    p_ledger_verdict.add_argument(
        "--by", default=None,
        help="Who is making this call. Defaults to the same actor NETTOOLS_LOG records.",
    )
    p_ledger_verdict.add_argument("--note", default=None, help="Why, in one line.")
    for _p in (p_ledger_summary, p_ledger_verdict):
        _p.add_argument("--format", choices=("json", "table", "summary"), default="json")
        _p.add_argument("--quiet", "-q", action="store_true")
        _p.set_defaults(func=_cmd_ledger)

    p_audit = sub.add_parser(
        "audit",
        help="Deterministic fabric audit — five rules: duplicate router-IDs, MTU "
             "mismatch across adjacencies, configured-but-dead BGP, hostname/"
             "inventory drift, mixed local-AS. Exit 0 ok/info, 1 warning, 2 critical.",
    )
    p_audit.add_argument("--from-fixtures", action="store_true",
                         help="Replay committed captures instead of the live lab.")
    p_audit.add_argument("--label", default="healthy",
                         help="Fixture label with --from-fixtures (default: healthy).")
    _add_output_arguments(p_audit)
    p_audit.set_defaults(func=_cmd_audit)

    p_config = sub.add_parser(
        "config", help="Inspect and validate the environment-variable configuration (B-476)."
    )
    config_sub = p_config.add_subparsers(dest="config_command", required=True)

    p_config_show = config_sub.add_parser(
        "show", help="Print every declared setting's effective value, source, and any problem."
    )
    _add_output_arguments(p_config_show)
    p_config_show.set_defaults(func=_cmd_config)

    p_config_check = config_sub.add_parser(
        "check",
        help="Validate the environment: print problems and exit 1 if any are found, 0 if clean.",
    )
    p_config_check.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress output; only the exit code carries the outcome."
    )
    p_config_check.set_defaults(func=_cmd_config)

    p_route = sub.add_parser(
        "route-event",
        help="Route an Alertmanager webhook or IOS-XR syslog line to an investigation "
             "(table lookup, no model). Exit 0 if routable, 1 if not, 2 unreadable.",
    )
    p_route.add_argument("--file", help="Read the event from a file instead of stdin.")
    p_route.add_argument(
        "--device",
        help="The originating device, from the receiver's transport metadata "
             "(required for syslog lines — the line's own text never supplies it).",
    )
    _add_output_arguments(p_route)
    p_route.set_defaults(func=_cmd_route_event)

    p_watch = sub.add_parser(
        "watch",
        help="Read-only dry run: fetch each device's recent Loki window, collapse "
             "repeated lines from one root cause, and print what WOULD route. "
             "Never runs investigate, never opens a ticket -- a human reads this.",
    )
    p_watch.add_argument("device", nargs="?", help="Device name; defaults to PE1 (or use --all).")
    p_watch.add_argument("--all", action="store_true", help="Watch every inventory device.")
    # Defaults mirror `event_watch.DEFAULT_WATCH_WINDOW_SECONDS`/`DEFAULT_WATCH_LIMIT`
    # literally rather than importing that module at parser-build time -- the
    # values themselves are what a --help reader needs, and `_cmd_watch` below
    # already imports the real module lazily, only when `watch` actually runs.
    p_watch.add_argument(
        "--since-seconds", type=int, default=900,
        help="Lookback window in seconds (default: 900).",
    )
    p_watch.add_argument(
        "--limit", type=int, default=500,
        help="Maximum log lines fetched per device (default: 500).",
    )
    _add_output_arguments(p_watch)
    p_watch.set_defaults(func=_cmd_watch)

    p_version = sub.add_parser("version", help="Print the installed nettools version.")
    _add_output_arguments(p_version)
    p_version.set_defaults(func=_cmd_version)

    p_inspect = sub.add_parser("inspect", help="Smoke-test the MCP server over stdio.")
    p_inspect.add_argument("device", nargs="?", help="Device name; defaults to PE1.")
    p_inspect.set_defaults(func=_cmd_inspect)

    return parser


def main() -> int:
    # Two lookups so .env is found both from the current directory upward and
    # next to an editable install of the package.
    load_dotenv(find_dotenv(usecwd=True)) or load_dotenv()
    parser = build_parser()
    args = parser.parse_args()
    # B-476/P2-02: report-only. Warn loudly on a malformed/out-of-range env
    # value instead of silently falling back to a default -- but never fail
    # startup over it. Failing closed on a bad value is the settings-model
    # rewire's job (see settings.py's module docstring), not this
    # validator's; this only makes the problem visible.
    #
    # Printed after parse_args() (moved from before it), deliberately: this
    # banner must honor --quiet ("suppress all output; only the exit code
    # carries the outcome" -- module docstring above), and --quiet is a
    # per-subcommand argparse flag we cannot read before parsing finishes.
    # A pre-scan of sys.argv for "--quiet"/"-q" was the alternative and was
    # rejected: it cannot distinguish a subcommand that has no --quiet at all
    # (analyze/agent/demo/capture/learn-topology/inspect) from one that does,
    # and it risks matching those tokens inside a positional value (e.g. an
    # `investigate` SUBJECT sentence, B-112) rather than an actual flag. Using
    # the parsed args.quiet reuses the same getattr(args, "quiet", False)
    # idiom _print_info() already uses below, so a subcommand without
    # --quiet still gets the warning -- it has no way to ask for silence, so
    # none is manufactured for it. The warning is still emitted on every
    # subcommand that has one, still never blocks startup, and still prints
    # before the command's own output -- only its position relative to
    # argparse's own parsing moved, not its content or default visibility.
    _require("settings", "output", "InventoryError")
    quiet = getattr(args, "quiet", False)
    for problem in settings.validate_environment():
        if not quiet:
            print(f"# config warning: {problem}", file=sys.stderr)
    try:
        return args.func(args)
    except InventoryError as exc:
        # Nothing could even be resolved (bad/missing inventory or
        # credentials) -- the worst-possible, "could not run at all" outcome.
        print(output.render({"status": "error", "errors": [str(exc)]}, "json"))
        return EXIT_CRITICAL


if __name__ == "__main__":
    raise SystemExit(main())
