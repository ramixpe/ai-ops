"""Offline contracts for B-492's read-only SSH concurrency measurement."""

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "concurrent_ssh_measurement.py"
_SPEC = importlib.util.spec_from_file_location("concurrent_ssh_measurement", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
measurement = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(measurement)


def test_measurement_plan_keeps_same_device_and_fabric_modes_distinct():
    same_device = measurement.measurement_plan(mode="same-device", level=4, devices=("PE1", "PE2"))
    fabric = measurement.measurement_plan(mode="fabric", level=4, devices=("PE1", "PE2", "PE3", "PE4"))

    assert same_device == ("PE1", "PE1", "PE1", "PE1")
    assert fabric == ("PE1", "PE2", "PE3", "PE4")


def test_measurement_plan_refuses_invalid_or_insufficient_input():
    try:
        measurement.measurement_plan(mode="unknown", level=1, devices=("PE1",))
    except ValueError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("unknown mode was accepted")

    try:
        measurement.measurement_plan(mode="fabric", level=2, devices=("PE1",))
    except ValueError as exc:
        assert "devices" in str(exc)
    else:
        raise AssertionError("insufficient fabric devices were accepted")


def test_summarize_records_reports_failure_and_retry_rates_without_payloads():
    records = (
        {"status": "success", "duration_seconds": 1.0, "retries": 0},
        {"status": "success", "duration_seconds": 3.0, "retries": 1},
        {"status": "error", "duration_seconds": 2.0, "retries": 1},
        {"status": "admission_refused", "duration_seconds": 0.1, "retries": 0},
    )

    summary = measurement.summarize_records(records)

    assert summary["attempts"] == 4
    assert summary["successes"] == 2
    assert summary["failures"] == 2
    assert summary["failure_rate"] == 0.5
    assert summary["retry_attempts"] == 2
    assert summary["retry_recovery_rate"] == 0.5
    assert summary["latency_seconds"]["p50"] == 1.5
    assert "payload" not in str(summary)


def test_classify_result_reads_section_retry_counts():
    evidence = {
        "interfaces": {"status": "success", "data": {"retries": 2}},
        "bgp": {"status": "success", "data": {"retries": 1}},
    }

    assert measurement._classify_result(evidence) == ("success", 3)