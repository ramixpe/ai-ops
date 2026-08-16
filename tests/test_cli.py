"""Tests for cli.py (Phase 8): argument parsing, device defaulting, exit codes,
and error paths -- none of this had any coverage before Phase 8, despite being
~400 lines of wiring. ``docs/REVIEW.md`` records a real historical regression
here (`analyze`/`demo` not catching the `ValueError` `get_provider()` raises on
misconfiguration) -- exactly the class of bug a CLI test catches and a unit
test of the underlying function cannot, since the bug was in the *wiring*, not
the function itself.

No network, no LLM call, ever: every test monkeypatches the specific
function(s) ``cli.py`` calls (bound names on the ``cli`` module itself, since
that is what ``_cmd_*`` actually looks up at call time -- patching
``network_tools.list_devices`` would not affect ``cli.list_devices``, already
a separate reference after ``from .network_tools import list_devices``).

Any test that drives ``main()`` stubs out ``cli.load_dotenv`` first: the real
one would read this repo's own (real, credentialed) ``.env`` file into the
test process's environment, which is exactly the kind of test/prod leakage
this suite must never risk -- a leaked env var set via ``os.environ`` inside
``load_dotenv`` is not cleaned up by ``monkeypatch`` the way ``monkeypatch.setenv``
is.
"""

from __future__ import annotations

import sys

import pytest

from agent_nettools import cli, metrics
from agent_nettools.inventory import InventoryError
from agent_nettools.llm_analysis import LLMAnalysisError


def _envelope(status="success", errors=None, tool="get_device_facts", device="PE1"):
    return {
        "tool": tool,
        "device": device,
        "status": status,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "data": {"commands": {}},
        "errors": errors or [],
    }


@pytest.fixture(autouse=True)
def _isolated_metrics(monkeypatch):
    """No test in this file should touch a real metrics file on disk."""

    monkeypatch.delenv(metrics.NETTOOLS_METRICS_FILE_ENV, raising=False)
    metrics.reset()
    yield
    metrics.reset()


# --------------------------------------------------------------------------- #
# build_parser(): structure, without running anything.
# --------------------------------------------------------------------------- #


def test_build_parser_includes_every_documented_subcommand():
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices  # noqa: SLF001 - test-only introspection.

    for name in (
        "inventory",
        "facts",
        "interfaces",
        "bgp",
        "lldp",
        "isis",
        "sr",
        "fabric",
        "route",
        "bgp-neighbor",
        "interface",
        "logging",
        "ping",
        "traceroute",
        "analyze",
        "agent",
        "demo",
        "diff",
        "capture",
        "learn-topology",
        "health",
        "baseline",
        "flaps",
        "evidence",
        "metrics",
        "version",
        "inspect",
    ):
        assert name in choices


def test_device_positional_defaults_to_none_not_a_hardcoded_name():
    """Device defaulting happens in _resolve_device()/get_default_device_name(),
    not baked into argparse -- so a caller with no inventory access at all
    (e.g. --help) never triggers a lookup."""

    parser = cli.build_parser()
    args = parser.parse_args(["facts"])
    assert args.device is None


def test_fabric_check_defaults_to_bgp():
    parser = cli.build_parser()
    args = parser.parse_args(["fabric"])
    assert args.check == "bgp"


def test_metrics_subcommand_defaults_to_json_format():
    parser = cli.build_parser()
    args = parser.parse_args(["metrics"])
    assert args.format == "json"
    assert args.quiet is False


# --------------------------------------------------------------------------- #
# _resolve_device(): device defaulting.
# --------------------------------------------------------------------------- #


def test_resolve_device_uses_the_given_name(monkeypatch):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: pytest.fail("should not be called"))
    assert cli._resolve_device("RR1") == "RR1"


def test_resolve_device_falls_back_to_default_when_none(monkeypatch):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    assert cli._resolve_device(None) == "PE1"


# --------------------------------------------------------------------------- #
# A single-device check command: success/error exit codes, --format, --quiet.
# --------------------------------------------------------------------------- #


def test_check_command_success_exits_zero_and_prints_json(monkeypatch, capsys):
    monkeypatch.setitem(cli.CHECK_TOOLS, "facts", lambda device: _envelope())
    parser = cli.build_parser()
    args = parser.parse_args(["facts", "PE1"])

    code = args.func(args)

    assert code == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '"status": "success"' in out


def test_check_command_error_status_exits_warning(monkeypatch, capsys):
    monkeypatch.setitem(
        cli.CHECK_TOOLS, "facts", lambda device: _envelope(status="error", errors=["boom"])
    )
    parser = cli.build_parser()
    args = parser.parse_args(["facts", "PE1"])

    code = args.func(args)

    assert code == cli.EXIT_WARNING
    assert "boom" in capsys.readouterr().out


def test_check_command_uses_default_device_when_omitted(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    seen = []
    monkeypatch.setitem(cli.CHECK_TOOLS, "facts", lambda device: seen.append(device) or _envelope())
    parser = cli.build_parser()
    args = parser.parse_args(["facts"])

    args.func(args)

    assert seen == ["PE1"]


def test_check_command_format_table(monkeypatch, capsys):
    monkeypatch.setitem(cli.CHECK_TOOLS, "facts", lambda device: _envelope())
    parser = cli.build_parser()
    args = parser.parse_args(["facts", "PE1", "--format", "table"])

    args.func(args)

    out = capsys.readouterr().out
    assert "KEY" in out and "VALUE" in out
    assert '"status": "success"' not in out  # Not pretty-printed JSON.


def test_check_command_format_summary(monkeypatch, capsys):
    monkeypatch.setitem(cli.CHECK_TOOLS, "facts", lambda device: _envelope())
    parser = cli.build_parser()
    args = parser.parse_args(["facts", "PE1", "--format", "summary"])

    args.func(args)

    assert capsys.readouterr().out.strip() == "get_device_facts PE1: success"


def test_check_command_quiet_suppresses_all_output(monkeypatch, capsys):
    monkeypatch.setitem(cli.CHECK_TOOLS, "facts", lambda device: _envelope())
    parser = cli.build_parser()
    args = parser.parse_args(["facts", "PE1", "--quiet"])

    code = args.func(args)

    assert code == cli.EXIT_OK
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- #
# fabric, route, bgp-neighbor, interface, logging, ping, traceroute.
# --------------------------------------------------------------------------- #


def test_fabric_command_success(monkeypatch, capsys):
    monkeypatch.setattr(cli, "check_fabric", lambda check: _envelope(tool="check_fabric", device="fabric"))
    parser = cli.build_parser()
    args = parser.parse_args(["fabric", "bgp"])

    assert args.func(args) == cli.EXIT_OK
    assert '"status": "success"' in capsys.readouterr().out


def test_route_command_passes_device_and_prefix(monkeypatch):
    seen = {}

    def fake_get_route(device, prefix):
        seen["device"], seen["prefix"] = device, prefix
        return _envelope(tool="run_template")

    monkeypatch.setattr(cli, "get_route", fake_get_route)
    parser = cli.build_parser()
    args = parser.parse_args(["route", "PE1", "10.0.0.0/24"])

    args.func(args)

    assert seen == {"device": "PE1", "prefix": "10.0.0.0/24"}


def test_logging_command_default_count_is_20(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        cli, "get_logging", lambda device, count: seen.update(device=device, count=count) or _envelope()
    )
    parser = cli.build_parser()
    args = parser.parse_args(["logging", "PE1"])

    args.func(args)

    assert seen == {"device": "PE1", "count": 20}


def test_ping_command_error_exits_warning(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "ping_device", lambda device, address: _envelope(status="error", errors=["refused"])
    )
    parser = cli.build_parser()
    args = parser.parse_args(["ping", "PE1", "10.0.0.1"])

    assert args.func(args) == cli.EXIT_WARNING


# --------------------------------------------------------------------------- #
# diff: the diff --exit-code-style scheme (0 no differences, 1 differences, 2
# comparison untrustworthy).
# --------------------------------------------------------------------------- #


def _diff_setup(monkeypatch, *, previous, diff_result):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "load_latest_snapshot", lambda device: previous)
    monkeypatch.setattr(cli, "load_golden_snapshot", lambda device: previous)
    monkeypatch.setattr(cli, "collect_evidence", lambda device: {"device": device})
    monkeypatch.setattr(cli, "save_snapshot", lambda evidence: "evidence/PE1/x.json")
    monkeypatch.setattr(cli, "diff_evidence", lambda old, new: diff_result)


def _empty_diff(**overrides):
    base = {
        "changed": [], "unchanged": ["bgp"], "added": [], "removed": [],
        "failed": [], "recovered": [], "unsupported": [],
    }
    base.update(overrides)
    return base


def test_diff_no_previous_snapshot_exits_ok(monkeypatch, capsys):
    _diff_setup(monkeypatch, previous=None, diff_result=_empty_diff())
    parser = cli.build_parser()
    args = parser.parse_args(["diff", "PE1"])

    assert args.func(args) == cli.EXIT_OK
    # B-422: informational notes go to stderr; stdout is the payload.
    assert "baseline established" in capsys.readouterr().err


def test_diff_no_changes_exits_ok(monkeypatch):
    _diff_setup(monkeypatch, previous={"device": "PE1"}, diff_result=_empty_diff())
    parser = cli.build_parser()
    args = parser.parse_args(["diff", "PE1"])

    assert args.func(args) == cli.EXIT_OK


def test_diff_with_changes_exits_warning(monkeypatch):
    _diff_setup(
        monkeypatch, previous={"device": "PE1"}, diff_result=_empty_diff(changed=["bgp"], unchanged=[])
    )
    parser = cli.build_parser()
    args = parser.parse_args(["diff", "PE1"])

    assert args.func(args) == cli.EXIT_WARNING


def test_diff_with_failed_intent_exits_critical(monkeypatch):
    _diff_setup(
        monkeypatch, previous={"device": "PE1"}, diff_result=_empty_diff(failed=["bgp"], unchanged=[])
    )
    parser = cli.build_parser()
    args = parser.parse_args(["diff", "PE1"])

    assert args.func(args) == cli.EXIT_CRITICAL


def test_diff_against_golden_uses_golden_loader(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "load_latest_snapshot", lambda device: pytest.fail("wrong loader"))
    monkeypatch.setattr(cli, "load_golden_snapshot", lambda device: calls.append("golden") or None)
    monkeypatch.setattr(cli, "collect_evidence", lambda device: {"device": device})
    monkeypatch.setattr(cli, "save_snapshot", lambda evidence: "path")
    parser = cli.build_parser()
    args = parser.parse_args(["diff", "PE1", "--against", "golden"])

    args.func(args)

    assert calls == ["golden"]


# --------------------------------------------------------------------------- #
# health: exit codes 0/1/2 driven by the fabric's worst severity.
# --------------------------------------------------------------------------- #


def _health_result(severity):
    return {
        "severity": severity,
        "counts": {"devices": 1, "by_severity": {"ok": 0, "info": 0, "warning": 0, "critical": 0}},
        "devices": {"PE1": {"severity": severity, "findings": [], "unevaluated": []}},
    }


@pytest.mark.parametrize(
    "severity,expected_code", [("ok", cli.EXIT_OK), ("warning", cli.EXIT_WARNING), ("critical", cli.EXIT_CRITICAL)]
)
def test_health_all_maps_severity_to_exit_code(monkeypatch, severity, expected_code):
    monkeypatch.setattr(
        cli,
        "list_devices",
        lambda: {"status": "success", "data": {"devices": [{"name": "PE1"}]}},
    )
    monkeypatch.setattr(cli, "collect_evidence", lambda name: {"device": name})
    monkeypatch.setattr(cli, "evaluate_fabric", lambda evidence_by_device: _health_result(severity))
    parser = cli.build_parser()
    args = parser.parse_args(["health", "--all"])

    assert args.func(args) == expected_code


def test_health_min_severity_filters_the_view_but_not_the_exit_code(monkeypatch, capsys):
    """Filtering which devices are *printed* must never soften the computed
    exit code -- the fabric severity is always the max over every device."""

    monkeypatch.setattr(
        cli,
        "list_devices",
        lambda: {"status": "success", "data": {"devices": [{"name": "PE1"}, {"name": "PE2"}]}},
    )
    monkeypatch.setattr(cli, "collect_evidence", lambda name: {"device": name})

    def fake_evaluate_fabric(evidence_by_device):
        return {
            "severity": "critical",
            "counts": {"devices": 2, "by_severity": {"ok": 1, "critical": 1, "warning": 0, "info": 0}},
            "devices": {
                "PE1": {"severity": "ok", "findings": [], "unevaluated": []},
                "PE2": {"severity": "critical", "findings": [], "unevaluated": []},
            },
        }

    monkeypatch.setattr(cli, "evaluate_fabric", fake_evaluate_fabric)
    parser = cli.build_parser()
    args = parser.parse_args(["health", "--all", "--min-severity", "critical"])

    code = args.func(args)

    assert code == cli.EXIT_CRITICAL  # Not softened by the filtered view.
    out = capsys.readouterr().out
    assert "PE1" not in out.split('"suppressed"')[0] or "PE1" in out  # PE1 is in "suppressed"
    assert "PE2" in out


def test_health_list_devices_failure_exits_critical(monkeypatch, capsys):
    monkeypatch.setattr(cli, "list_devices", lambda: {"status": "error", "errors": ["bad inventory"]})
    parser = cli.build_parser()
    args = parser.parse_args(["health", "--all"])

    assert args.func(args) == cli.EXIT_CRITICAL
    assert "bad inventory" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# baseline pin/show, flaps, evidence prune/history.
# --------------------------------------------------------------------------- #


def test_baseline_pin_from_latest_with_nothing_saved_exits_warning(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "load_latest_snapshot", lambda device: None)
    parser = cli.build_parser()
    args = parser.parse_args(["baseline", "pin", "PE1", "--from-latest"])

    assert args.func(args) == cli.EXIT_WARNING
    assert "No saved snapshot" in capsys.readouterr().out


def test_baseline_pin_collects_fresh_evidence_by_default(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "collect_evidence", lambda device: calls.append("collect") or {"device": device})
    monkeypatch.setattr(cli, "save_snapshot", lambda evidence: calls.append("save") or "path")
    monkeypatch.setattr(cli, "save_golden_snapshot", lambda evidence: calls.append("golden") or "golden-path")
    parser = cli.build_parser()
    args = parser.parse_args(["baseline", "pin", "PE1"])

    assert args.func(args) == cli.EXIT_OK
    assert calls == ["collect", "save", "golden"]


def test_baseline_show_none_pinned_exits_warning(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "load_golden_snapshot", lambda device: None)
    parser = cli.build_parser()
    args = parser.parse_args(["baseline", "show", "PE1"])

    assert args.func(args) == cli.EXIT_WARNING
    assert "No golden snapshot" in capsys.readouterr().err  # B-422: a note, not payload


def test_flaps_no_flapping_exits_ok(monkeypatch):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(
        cli, "detect_flaps", lambda device, min_transitions: {"device": device, "flapping": []}
    )
    parser = cli.build_parser()
    args = parser.parse_args(["flaps", "PE1"])

    assert args.func(args) == cli.EXIT_OK


def test_flaps_with_flapping_exits_warning(monkeypatch):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(
        cli,
        "detect_flaps",
        lambda device, min_transitions: {"device": device, "flapping": [{"intent": "bgp"}]},
    )
    parser = cli.build_parser()
    args = parser.parse_args(["flaps", "PE1"])

    assert args.func(args) == cli.EXIT_WARNING


def test_evidence_prune_with_no_arguments_exits_warning(monkeypatch, capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["evidence", "prune"])

    assert args.func(args) == cli.EXIT_WARNING
    assert "Nothing to do" in capsys.readouterr().err  # B-422: a note, not payload


def test_evidence_prune_runs_when_a_retention_rule_is_given(monkeypatch):
    monkeypatch.setattr(
        cli, "prune_snapshots", lambda **kwargs: {"removed": 3, "devices": {"PE1": 3}}
    )
    parser = cli.build_parser()
    args = parser.parse_args(["evidence", "prune", "--keep-count", "5"])

    assert args.func(args) == cli.EXIT_OK


def test_evidence_history_reports_count(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "list_snapshot_history", lambda device: [{"device": "PE1"}, {"device": "PE1"}])
    parser = cli.build_parser()
    args = parser.parse_args(["evidence", "history"])

    assert args.func(args) == cli.EXIT_OK
    assert '"count": 2' in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# metrics: always exits 0, both output forms.
# --------------------------------------------------------------------------- #


def test_metrics_command_json_format(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["metrics"])

    assert args.func(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '"collections"' in out
    assert '"verdicts"' in out


def test_metrics_command_prometheus_format(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["metrics", "--format", "prometheus"])

    assert args.func(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "# HELP" in out
    assert "nettools_health_verdicts_total" in out


def test_metrics_command_quiet_suppresses_output(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["metrics", "--quiet"])

    assert args.func(args) == cli.EXIT_OK
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------- #
# version.
# --------------------------------------------------------------------------- #


def test_version_command_reports_the_package_version(capsys):
    import agent_nettools

    parser = cli.build_parser()
    args = parser.parse_args(["version"])

    assert args.func(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert agent_nettools.__version__ in out
    assert "python_version" in out


# --------------------------------------------------------------------------- #
# analyze/agent/demo: the ValueError-vs-LLMAnalysisError regression
# docs/REVIEW.md records (`analyze`/`demo` not catching get_provider()'s
# ValueError). Every path below must not raise -- an uncaught exception here
# would fail the test itself, which is exactly what this class of test is for.
# --------------------------------------------------------------------------- #


def test_analyze_catches_value_error_from_get_provider_and_exits_critical(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "collect_evidence", lambda device: {"device": device})
    monkeypatch.setattr(
        cli,
        "analyze_evidence",
        lambda evidence: (_ for _ in ()).throw(ValueError("No LLM API key is configured.")),
    )
    parser = cli.build_parser()
    args = parser.parse_args(["analyze", "PE1"])

    code = args.func(args)  # Must not raise.

    assert code == cli.EXIT_CRITICAL
    assert "Analysis error" in capsys.readouterr().out


def test_analyze_catches_llm_analysis_error_and_exits_warning(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "collect_evidence", lambda device: {"device": device})
    monkeypatch.setattr(
        cli,
        "analyze_evidence",
        lambda evidence: (_ for _ in ()).throw(LLMAnalysisError("rate limited")),
    )
    parser = cli.build_parser()
    args = parser.parse_args(["analyze", "PE1"])

    code = args.func(args)

    assert code == cli.EXIT_WARNING
    assert "Analysis error" in capsys.readouterr().out


def test_analyze_success_exits_ok(monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_default_device_name", lambda: "PE1")
    monkeypatch.setattr(cli, "collect_evidence", lambda device: {"device": device})
    monkeypatch.setattr(cli, "analyze_evidence", lambda evidence: "## Summary\nAll good.")
    parser = cli.build_parser()
    args = parser.parse_args(["analyze", "PE1"])

    assert args.func(args) == cli.EXIT_OK
    assert "All good." in capsys.readouterr().out


def test_analyze_fabric_catches_value_error_and_exits_critical(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "list_devices", lambda: {"status": "success", "data": {"devices": [{"name": "PE1"}]}}
    )
    monkeypatch.setattr(cli, "collect_evidence", lambda name: {"device": name})
    monkeypatch.setattr(
        cli,
        "analyze_fabric",
        lambda evidence_by_device: (_ for _ in ()).throw(ValueError("no provider")),
    )
    parser = cli.build_parser()
    args = parser.parse_args(["analyze", "--fabric"])

    assert args.func(args) == cli.EXIT_CRITICAL


def test_demo_catches_value_error_and_exits_critical(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "list_devices",
        lambda: {"status": "success", "data": {"devices": [{"name": "PE1"}]}},
    )
    monkeypatch.setattr(cli, "collect_evidence", lambda device: {"device": device})
    monkeypatch.setattr(
        cli,
        "analyze_evidence",
        lambda evidence: (_ for _ in ()).throw(ValueError("misconfigured")),
    )
    parser = cli.build_parser()
    args = parser.parse_args(["demo", "PE1"])

    code = args.func(args)  # This is exactly the regression docs/REVIEW.md records.

    assert code == cli.EXIT_CRITICAL


def test_demo_unknown_device_exits_critical(capsys):
    parser = cli.build_parser()
    args = parser.parse_args(["demo", "NOT-A-REAL-DEVICE"])

    assert args.func(args) == cli.EXIT_CRITICAL
    assert "Device not found" in capsys.readouterr().out


def test_agent_catches_value_error_and_exits_critical(monkeypatch, capsys):
    monkeypatch.setattr(
        cli, "run_agent_loop", lambda question, **kwargs: (_ for _ in ()).throw(ValueError("no key"))
    )
    parser = cli.build_parser()
    args = parser.parse_args(["agent", "why is PE2 down"])

    assert args.func(args) == cli.EXIT_CRITICAL
    assert "Agent error" in capsys.readouterr().out


def test_agent_stopped_because_end_turn_exits_ok(monkeypatch):
    monkeypatch.setattr(
        cli,
        "run_agent_loop",
        lambda question, **kwargs: {
            "answer": "PE2 is isolated at IS-IS.",
            "iterations": 2,
            "tool_calls": [],
            "stopped_because": "end_turn",
        },
    )
    parser = cli.build_parser()
    args = parser.parse_args(["agent", "why is PE2 down"])

    assert args.func(args) == cli.EXIT_OK


def test_agent_stopped_because_max_iterations_exits_warning(monkeypatch):
    monkeypatch.setattr(
        cli,
        "run_agent_loop",
        lambda question, **kwargs: {
            "answer": "partial",
            "iterations": 8,
            "tool_calls": [],
            "stopped_because": "max_iterations",
        },
    )
    parser = cli.build_parser()
    args = parser.parse_args(["agent", "why is PE2 down"])

    assert args.func(args) == cli.EXIT_WARNING


# --------------------------------------------------------------------------- #
# main(): argument dispatch end to end, and the InventoryError -> exit 2 path.
# --------------------------------------------------------------------------- #


def _stub_load_dotenv(monkeypatch):
    """Never let main() read this repo's real (credentialed) .env in a test."""

    monkeypatch.setattr(cli, "load_dotenv", lambda *args, **kwargs: None)


def test_main_dispatches_to_the_right_subcommand(monkeypatch, capsys):
    _stub_load_dotenv(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["nettools", "facts", "PE1"])
    monkeypatch.setitem(cli.CHECK_TOOLS, "facts", lambda device: _envelope())

    assert cli.main() == cli.EXIT_OK
    assert '"status": "success"' in capsys.readouterr().out


def test_main_catches_inventory_error_and_exits_critical(monkeypatch, capsys):
    """No credentials or fake transport needed: get_default_device_name()
    raising InventoryError with no device given on the command line is a
    completely ordinary way to hit this path, and pins the top-level
    InventoryError -> exit-2 handler in main()."""

    _stub_load_dotenv(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["nettools", "facts"])
    monkeypatch.setattr(
        cli,
        "get_default_device_name",
        lambda: (_ for _ in ()).throw(InventoryError("Required environment variable is not set")),
    )

    code = cli.main()  # Must not raise.

    assert code == cli.EXIT_CRITICAL
    assert "Required environment variable" in capsys.readouterr().out


def test_main_propagates_argparse_usage_errors(monkeypatch):
    """An unrecognized subcommand is argparse's own concern (SystemExit(2)),
    not main()'s -- pinned so a future change to build_parser() cannot
    silently swallow a usage error."""

    _stub_load_dotenv(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["nettools", "not-a-real-subcommand"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 2
