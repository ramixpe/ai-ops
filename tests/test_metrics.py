"""Metrics collection (Phase 8): per-device outcomes/latency/retries, health
verdict counts by severity, and both output forms (JSON, Prometheus text)."""

from __future__ import annotations

import json
import threading

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


def test_concurrent_increments_across_collectors_lose_nothing(tmp_path):
    """EER-011 regression guard: genuine concurrent read-modify-write against
    one shared metrics file must not lose an increment.

    The sequential test above (`test_persists_across_separate_collector_
    instances_when_a_path_is_configured`) only ever has one collector
    mutating at a time, so it exercises the accumulate path but not the lost-
    update path: two collectors both reading `success: 10` and both writing
    back `success: 11` is a race a purely sequential test can never trigger.

    Each thread here gets its OWN `MetricsCollector` instance -- its own
    `threading.Lock`, its own in-memory baseline -- so the only thing
    coordinating them is the cross-process flock on the shared file, the
    same shape as N separate `nettools` OS processes racing on
    NETTOOLS_METRICS_FILE (see admission.py's "forty syslog lines, forty
    processes" scenario, which this module's own docstring cross-references).
    A `threading.Barrier` starts every thread's burst at once to maximize
    actual overlap rather than relying on scheduling luck.

    This assertion is exact, not "at least" or "roughly": with the
    cross-process lock in place there is no window left in which an update
    can be lost, so the total is deterministic regardless of how the OS
    happens to interleave the threads -- which is what keeps this test fast
    and non-flaky rather than a race dressed up as a probability.
    """

    path = str(tmp_path / "metrics.json")
    threads_n = 8
    increments_per_thread = 15
    barrier = threading.Barrier(threads_n)

    def worker() -> None:
        collector = metrics.MetricsCollector(path=path)
        barrier.wait()  # every thread starts its burst together
        for _ in range(increments_per_thread):
            collector.record_collection("PE1", success=True, duration_s=0.001, retries=1)

    workers = [threading.Thread(target=worker) for _ in range(threads_n)]
    for worker_thread in workers:
        worker_thread.start()
    for worker_thread in workers:
        worker_thread.join()

    expected = threads_n * increments_per_thread
    final = metrics.MetricsCollector(path=path).snapshot()["collections"]["PE1"]
    assert final["success"] == expected
    assert final["latency_count"] == expected
    assert final["retries_total"] == expected


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


# --------------------------------------------------------------------------- #
# B-457 -- paraphrase grounding as a tool-health signal
# --------------------------------------------------------------------------- #


def test_paraphrase_outcomes_are_counted(monkeypatch):
    """**A field nobody aggregates is not detection.**

    Before B-439 a report failing grounding exited 2, so a systematic grounding
    regression showed up in exit codes. The authoritative report is now rendered
    from typed fields and cannot fail, so a rejected paraphrase correctly no
    longer changes the exit code -- which removed the only signal a *systematic*
    regression had.

    The per-run status was always in the payload. What was missing is a rate,
    and a rate is what this failure looks like.
    """

    from agent_nettools import metrics

    metrics.default_collector.reset()
    for outcome in ("emitted", "withheld", "withheld", "coverage_limited"):
        metrics.record_paraphrase(outcome)

    counts = metrics.snapshot()["paraphrases"]
    assert counts == {"emitted": 1, "withheld": 2, "coverage_limited": 1, "not_attempted": 0}


def test_an_unknown_outcome_is_ignored_not_counted():
    """Same rule as `record_verdict`: a typo must not invent a category."""

    from agent_nettools import metrics

    metrics.default_collector.reset()
    metrics.record_paraphrase("definitely-not-a-status")

    assert sum(metrics.snapshot()["paraphrases"].values()) == 0


def test_a_real_run_records_its_paraphrase_outcome():
    """Wired to the runner, not merely available.

    The counter existing and nothing calling it is the shape B-457 is about.
    """

    from agent_nettools import metrics
    from agent_nettools.fixtures import fixture_sender
    from agent_nettools.investigation import investigate

    metrics.default_collector.reset()

    investigate(
        "RR1", "10.255.0.12", sender=fixture_sender(label="broken"),
        resolver=lambda _s: "PE2", analyst=lambda _p: "this is not JSON",
    )

    counts = metrics.snapshot()["paraphrases"]
    assert counts["withheld"] >= 1, "an ungroundable paraphrase is counted"
    assert counts["emitted"] == 0


def test_the_metric_is_labelled_as_tool_health_not_network_health():
    """Reviewer B's framing, pinned in the exposition text.

    Wiring this to anything that pages would be exactly the conflation the exit
    code scheme exists to prevent -- exit 1 is the network, exit 2 is the answer,
    and this is neither.
    """

    from agent_nettools import metrics

    metrics.default_collector.reset()
    metrics.record_paraphrase("withheld")
    text = metrics.render_prometheus(metrics.snapshot())

    assert "nettools_paraphrase_outcomes_total" in text
    assert "TOOL-HEALTH" in text
    assert "Never page on this." in text
