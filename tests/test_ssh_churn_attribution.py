"""Offline contracts for B-206b's passive SSH churn attribution instrument."""

import importlib.util
import pathlib

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "ssh_churn_attribution.py"
_SPEC = importlib.util.spec_from_file_location("ssh_churn_attribution", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
attribution = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(attribution)


def test_parse_syn_records_only_ssh_connection_attempts_to_inventory_addresses():
    lines = (
        "1710000000.010 IP 172.20.250.1.52341 > 172.20.250.22.22: Flags [S], seq 1",
        "1710000001.010 IP 172.20.250.1.52342 > 172.20.250.22.443: Flags [S], seq 1",
        "1710000002.010 IP 172.20.250.9.52343 > 172.20.250.21.22: Flags [S.], seq 1",
    )

    records = attribution.parse_syn_records(lines, router_addresses=frozenset({"172.20.250.21", "172.20.250.22"}))

    assert records == (
        {"timestamp": 1710000000.01, "source_ip": "172.20.250.1", "destination_ip": "172.20.250.22"},
    )


def test_summarize_syn_records_reports_rates_by_source_and_destination():
    records = (
        {"timestamp": 0.0, "source_ip": "172.20.250.1", "destination_ip": "172.20.250.22"},
        {"timestamp": 10.0, "source_ip": "172.20.250.1", "destination_ip": "172.20.250.22"},
        {"timestamp": 20.0, "source_ip": "172.20.250.2", "destination_ip": "172.20.250.21"},
    )

    summary = attribution.summarize_syn_records(records)

    assert summary["attempts"] == 3
    assert summary["window_seconds"] == 20.0
    assert summary["sources"] == {"172.20.250.1": 2, "172.20.250.2": 1}
    assert summary["destinations"] == {"172.20.250.21": 1, "172.20.250.22": 2}