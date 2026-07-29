"""Output rendering (Phase 8): json (default)/table/summary must never invent
or soften data -- only reformat what a tool/health call already returned."""

from __future__ import annotations

import json

import pytest

from agent_nettools import output


def test_json_format_is_byte_identical_to_json_dumps():
    payload = {"tool": "get_device_facts", "device": "PE1", "status": "success", "data": {}, "errors": []}
    assert output.render(payload, "json") == json.dumps(payload, indent=2)


def test_unknown_format_raises():
    with pytest.raises(ValueError, match="Unknown output format"):
        output.render({}, "bogus")


# --------------------------------------------------------------------------- #
# Single-device tool envelope.
# --------------------------------------------------------------------------- #


def _envelope(status="success", errors=None):
    return {
        "tool": "get_device_facts",
        "device": "PE1",
        "status": status,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "data": {"commands": {}},
        "errors": errors or [],
    }


def test_flat_envelope_table_has_key_value_rows():
    table = output.render_table(_envelope())
    assert "KEY" in table and "VALUE" in table
    assert "status" in table
    assert "success" in table


def test_flat_envelope_summary_reports_status_and_error_count():
    summary = output.render_summary(_envelope(status="error", errors=["show version: boom"]))
    assert "get_device_facts PE1: error (1 error(s))" == summary


def test_flat_envelope_summary_no_errors_has_no_error_suffix():
    summary = output.render_summary(_envelope())
    assert summary == "get_device_facts PE1: success"


# --------------------------------------------------------------------------- #
# Fabric envelope (check_fabric's shape).
# --------------------------------------------------------------------------- #


def _fabric_envelope():
    return {
        "tool": "check_fabric",
        "device": "fabric",
        "status": "error",
        "data": {
            "check": "bgp",
            "devices": {
                "PE1": {"status": "success", "errors": []},
                "PE2": {"status": "error", "errors": ["show bgp summary: timeout"]},
                "P1": {"status": "unsupported", "errors": []},
            },
            "unsupported": ["P1"],
        },
        "errors": ["PE2: bgp check failed"],
    }


def test_fabric_table_has_one_row_per_device():
    table = output.render_table(_fabric_envelope())
    lines = table.splitlines()
    assert any(line.startswith("P1") for line in lines)
    assert any(line.startswith("PE1") for line in lines)
    assert any(line.startswith("PE2") for line in lines)
    assert "timeout" in table


def test_fabric_summary_counts_statuses():
    summary = output.render_summary(_fabric_envelope())
    assert summary.startswith("bgp: 3 device(s) ->")
    assert "1 error" in summary
    assert "1 success" in summary
    assert "1 unsupported" in summary


# --------------------------------------------------------------------------- #
# Health verdicts: single device and fabric.
# --------------------------------------------------------------------------- #


def _health_verdict(severity="critical"):
    return {
        "device": "PE2",
        "role": "edge",
        "platform": "cisco_xr",
        "severity": severity,
        "findings": [{"rule": "isis_isolated", "severity": "critical", "message": "isolated"}],
        "counts": {"critical": 1, "warning": 0, "info": 0},
        "unevaluated": [],
        "unsupported": [],
    }


def _health_fabric():
    return {
        "severity": "critical",
        "counts": {
            "devices": 2,
            "by_severity": {"critical": 1, "warning": 0, "info": 0, "ok": 1},
            "unevaluated_devices": [],
        },
        "devices": {"PE2": _health_verdict(), "PE1": _health_verdict(severity="ok")},
        "suppressed": [],
    }


def test_single_health_verdict_table():
    table = output.render_table(_health_verdict())
    assert "PE2" in table
    assert "CRITICAL" in table
    assert "1" in table  # one finding


def test_single_health_verdict_summary():
    summary = output.render_summary(_health_verdict())
    assert summary == "PE2: CRITICAL (1 finding(s))"


def test_health_fabric_table_lists_every_device():
    table = output.render_table(_health_fabric())
    assert "PE1" in table and "PE2" in table
    assert "OK" in table and "CRITICAL" in table


def test_health_fabric_summary_reports_fabric_severity_and_breakdown():
    summary = output.render_summary(_health_fabric())
    assert summary.startswith("Fabric severity: CRITICAL")
    assert "critical=1" in summary
    assert "ok=1" in summary


def test_health_summary_never_softens_a_critical_fabric():
    """The summary text must still say CRITICAL -- table/summary is a
    reformat, not a different (softer) verdict."""

    summary = output.render_summary(_health_fabric())
    assert "CRITICAL" in summary
    assert "ok" not in summary.split("Fabric severity:")[1].split("(")[0].lower()


# --------------------------------------------------------------------------- #
# Diff result.
# --------------------------------------------------------------------------- #


def _diff_result():
    return {
        "device": "PE1",
        "platform": "cisco_xr",
        "platform_changed": False,
        "old_timestamp": "t0",
        "new_timestamp": "t1",
        "changed": ["bgp"],
        "unchanged": ["isis", "interfaces"],
        "added": [],
        "removed": [],
        "failed": [],
        "recovered": [],
        "unsupported": ["sr"],
        "details": {"bgp": {}},
    }


def test_diff_table_lists_each_bucket():
    table = output.render_table(_diff_result())
    assert "CHANGED" in table
    assert "bgp" in table
    assert "UNCHANGED" in table


def test_diff_summary_counts_each_bucket():
    summary = output.render_summary(_diff_result())
    assert summary == "PE1: 1 changed, 2 unchanged, 0 added, 0 removed, 0 failed, 0 recovered, 1 unsupported"


# --------------------------------------------------------------------------- #
# Inventory listing.
# --------------------------------------------------------------------------- #


def _inventory_result():
    return {
        "tool": "list_devices",
        "device": "inventory",
        "status": "success",
        "data": {
            "devices": [
                {"name": "PE1", "hostname": "172.20.250.21", "platform": "cisco_xr"},
                {"name": "RR1", "hostname": "172.20.250.31", "platform": "cisco_xr"},
            ]
        },
        "errors": [],
    }


def test_inventory_table_has_one_row_per_device():
    table = output.render_table(_inventory_result())
    assert "PE1" in table and "RR1" in table
    assert "172.20.250.21" in table


def test_inventory_summary_reports_a_count():
    assert output.render_summary(_inventory_result()) == "2 device(s) in inventory"


# --------------------------------------------------------------------------- #
# Fallback for an unrecognized shape.
# --------------------------------------------------------------------------- #


def test_unrecognized_shape_falls_back_to_flat_rendering():
    payload = {"some_key": "some_value", "nested": {"a": 1}}
    table = output.render_table(payload)
    assert "some_key" in table and "some_value" in table

    summary = output.render_summary(payload)
    assert json.loads(summary) == payload
