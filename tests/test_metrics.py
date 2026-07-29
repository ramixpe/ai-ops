"""Metrics collection (Phase 8): per-device outcomes/latency/retries, health
verdict counts by severity, and both output forms (JSON, Prometheus text)."""

from __future__ import annotations

import json

from helpers import install_fake_netmiko, set_device_environment

from agent_nettools import metrics
from agent_nettools.health import evaluate_device
from agent_nettools.inventory_model import Device
from agent_nettools.network_tools import collect_evidence


def test_record_collection_aggregates_success_and_failure(tmp_path):
    collector = metrics.MetricsCollector(path=str(tmp_path / "metrics.json"))

    collector.record_collection("PE1", success=True, duration_s=0.5, retries=0)
    collector.record_collection("PE1", success=True, duration_s=1.5, retries=1)
    collector.record_collection("PE1", success=False, duration_s=2.0, retries=0)

    snap = collector.snapshot()
    pe1 = snap["collections"]["PE1"]
    assert pe1["success"] == 2
    assert pe1["failure"] == 1
    assert pe1["retries_total"] == 1
    assert pe1["latency_count"] == 3
    assert pe1["latency_total_s"] == 4.0
    assert pe1["latency_avg_s"] == round(4.0 / 3, 6)
    assert snap["totals"] == {"success": 2, "failure": 1, "retries_total": 1}


def test_record_verdict_counts_by_severity(tmp_path):
    collector = metrics.MetricsCollector(path=str(tmp_path / "metrics.json"))

    collector.record_verdict("ok")
    collector.record_verdict("ok")
    collector.record_verdict("critical")
    collector.record_verdict("not-a-real-severity")  # silently ignored

    snap = collector.snapshot()
    assert snap["verdicts"] == {"ok": 2, "info": 0, "warning": 0, "critical": 1}


def test_snapshot_with_no_data_is_empty_but_well_shaped(tmp_path):
    collector = metrics.MetricsCollector(path=str(tmp_path / "metrics.json"))

    snap = collector.snapshot()

    assert snap["collections"] == {}
    assert snap["totals"] == {"success": 0, "failure": 0, "retries_total": 0}
    assert snap["verdicts"] == {"ok": 0, "info": 0, "warning": 0, "critical": 0}


def test_reset_clears_in_memory_state(tmp_path):
    collector = metrics.MetricsCollector(path=str(tmp_path / "metrics.json"))
    collector.record_collection("PE1", success=True, duration_s=1.0)
    collector.record_verdict("warning")

    collector.reset()

    snap = collector.snapshot()
    assert snap["collections"] == {}
    assert snap["verdicts"]["warning"] == 0


def test_persists_across_separate_collector_instances_when_a_path_is_configured(tmp_path):
    """Simulates two separate `nettools` process invocations sharing a
    NETTOOLS_METRICS_FILE: the second instance must see the first's counts."""

    path = str(tmp_path / "metrics.json")

    first = metrics.MetricsCollector(path=path)
    first.record_collection("PE1", success=True, duration_s=1.0, retries=2)
    first.record_verdict("critical")

    second = metrics.MetricsCollector(path=path)
    snap = second.snapshot()

    assert snap["collections"]["PE1"]["success"] == 1
    assert snap["collections"]["PE1"]["retries_total"] == 2
    assert snap["verdicts"]["critical"] == 1

    # And the second instance's own mutations accumulate on top.
    second.record_collection("PE1", success=True, duration_s=1.0)
    third = metrics.MetricsCollector(path=path)
    assert third.snapshot()["collections"]["PE1"]["success"] == 2


def test_in_memory_only_when_no_path_is_configured(monkeypatch, tmp_path):
    """Without NETTOOLS_METRICS_FILE, nothing is ever written to disk -- a
    fresh instance in the same process starts from zero."""

    monkeypatch.delenv(metrics.NETTOOLS_METRICS_FILE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    collector = metrics.MetricsCollector()
    collector.record_collection("PE1", success=True, duration_s=1.0)

    assert list(tmp_path.iterdir()) == []  # No file appeared anywhere.
    assert metrics.MetricsCollector().snapshot()["collections"] == {}


def test_default_collector_reads_the_env_var_lazily_not_at_import(monkeypatch, tmp_path):
    """Regression guard: the module-level default_collector is constructed at
    import time, long before cli.main()/mcp_server load .env -- so the path
    must be resolved on first *use*, never cached at construction."""

    path = tmp_path / "metrics.json"
    monkeypatch.setenv(metrics.NETTOOLS_METRICS_FILE_ENV, str(path))
    metrics.reset()

    metrics.record_collection("PE1", success=True, duration_s=1.0)

    assert path.is_file()
    metrics.reset()


def test_render_json_is_the_snapshot(tmp_path):
    collector = metrics.MetricsCollector(path=str(tmp_path / "metrics.json"))
    collector.record_collection("PE1", success=True, duration_s=1.0)

    assert json.loads(collector.render_json()) == collector.snapshot()


def test_render_prometheus_contains_expected_families_and_values(tmp_path):
    collector = metrics.MetricsCollector(path=str(tmp_path / "metrics.json"))
    collector.record_collection("PE1", success=True, duration_s=1.5, retries=1)
    collector.record_collection("PE1", success=False, duration_s=0.5)
    collector.record_verdict("warning")

    text = collector.render_prometheus()

    assert 'nettools_device_collections_total{device="PE1",outcome="success"} 1' in text
    assert 'nettools_device_collections_total{device="PE1",outcome="failure"} 1' in text
    assert 'nettools_device_collection_latency_seconds_sum{device="PE1"} 2.0' in text
    assert 'nettools_device_collection_latency_seconds_count{device="PE1"} 2' in text
    assert 'nettools_device_retries_total{device="PE1"} 1' in text
    assert 'nettools_health_verdicts_total{severity="warning"} 1' in text
    assert "# HELP" in text and "# TYPE" in text


def test_prometheus_label_values_are_escaped():
    snap = {
        "collections": {
            'weird"device\\name': {
                "success": 1,
                "failure": 0,
                "retries_total": 0,
                "latency_total_s": 1.0,
                "latency_count": 1,
                "latency_avg_s": 1.0,
            }
        },
        "verdicts": {"ok": 0, "info": 0, "warning": 0, "critical": 0},
    }

    text = metrics.render_prometheus(snap)

    assert 'device="weird\\"device\\\\name"' in text


# --------------------------------------------------------------------------- #
# Wiring: the real collection/health paths record into the default collector,
# without changing any result envelope.
# --------------------------------------------------------------------------- #


def test_collect_evidence_records_a_metrics_event_without_changing_the_envelope(monkeypatch):
    """sender= bypasses _netmiko_send_commands entirely (the existing
    audit-log precedent), so this uses the fake-netmiko seam instead, exactly
    like the audit log rotation tests."""

    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)
    metrics.reset()

    evidence = collect_evidence("PE1")

    assert evidence["device"] == "PE1"  # Envelope shape is unaffected.
    snap = metrics.snapshot()
    assert snap["collections"]["PE1"]["success"] == 1
    metrics.reset()


def test_collect_evidence_records_failure_on_a_failed_connection(monkeypatch):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch, fail_connect=True)
    metrics.reset()

    evidence = collect_evidence("PE1")

    assert evidence["device"] == "PE1"
    snap = metrics.snapshot()
    assert snap["collections"]["PE1"]["failure"] == 1
    assert snap["collections"]["PE1"]["success"] == 0
    metrics.reset()


def test_sender_injection_path_never_records_metrics(monkeypatch):
    """Matches the audit log's own documented behavior: sender= is a pure
    test seam and must never touch real observability side channels."""

    metrics.reset()
    result = collect_evidence("PE1", sender=lambda device, command: "output")
    assert result["device"] == "PE1"
    assert metrics.snapshot()["collections"] == {}


def _device(name: str = "PE1", role: str = "edge") -> Device:
    return Device(name=name, mgmt_ip="10.0.0.1", role=role, site="lab")


def test_evaluate_device_records_verdict_severity_without_changing_the_verdict():
    metrics.reset()
    evidence = {"platform": "cisco_xr"}  # No health intents present at all.

    verdict = evaluate_device(evidence, _device())

    assert verdict["severity"] == "critical"  # Unaffected by metrics recording.
    assert metrics.snapshot()["verdicts"]["critical"] == 1
    metrics.reset()
