"""B-480 -- event routing: a table lookup, never a model, never a guess."""

from __future__ import annotations

import json
from pathlib import Path

from agent_nettools import event_routing as er

FIXTURES = Path(__file__).parent / "fixtures" / "cisco_xr"


def _real_line(fragment: str, device: str = "RR1") -> str:
    """A verbatim line from the committed fixtures -- never a hand-typed one."""

    text = (FIXTURES / device / "broken" / "show-logging-last-200.txt").read_text()
    for line in text.splitlines():
        if fragment in line:
            return line.strip()
    raise AssertionError(f"no fixture line contains {fragment!r}")


# --------------------------------------------------------------------------- #
# Syslog
# --------------------------------------------------------------------------- #


def test_a_real_bgp_adjchange_line_routes_to_the_bgp_flow():
    line = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    d = er.route_syslog_line(line, device="RR1")

    assert d.routable is True
    assert d.flow == "bgp_session"
    assert d.subject == "10.255.0.12"
    assert d.suggested_command() == [
        "nettools", "investigate", "RR1", "10.255.0.12", "--flow", "bgp_session"
    ]


def test_a_real_link_updown_line_routes_to_the_interface_flow():
    line = _real_line("PKT_INFRA-LINK-3-UPDOWN", device="PE2")
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is True
    assert d.flow == "interface"
    assert d.subject and d.subject.startswith("GigabitEthernet")


def test_an_ipv6_peer_is_not_a_subject():
    """The only implemented subjects are IPv4; v6 must refuse, not approximate.

    The fixture genuinely contains a v6 adjacency change, so this is a real
    line, not a synthetic worry.
    """

    line = _real_line("neighbor 2001:db8:ffff::12")
    d = er.route_syslog_line(line, device="RR1")

    assert d.routable is False
    assert "subject" in d.reason


def test_ssh_noise_is_deliberately_unrouted():
    d = er.route_syslog_line(
        "RP/0/RP0/CPU0:Aug 16 07:00:00.000 UTC: sshd[123]: "
        "%SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL : Accepted authentication for clab",
        device="PE2",
    )
    assert d.routable is False
    assert "deliberately unrouted" in d.reason


def test_garbage_is_unrecognised_not_an_error():
    d = er.route_syslog_line("not a log line at all", device="PE2")
    assert d.routable is False
    assert d.source_kind == "syslog"


def test_the_device_never_comes_from_message_text():
    """B-467's rule applied here: message text is device-authored.

    A perfectly routable line with no device metadata is unroutable -- the
    device must come from the receiver's transport knowledge, never from
    anything an attacker can put in a log message.
    """

    line = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    d = er.route_syslog_line(line, device=None)

    assert d.routable is False
    assert "metadata" in d.reason


def test_an_unknown_device_is_a_stated_refusal():
    line = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    d = er.route_syslog_line(line, device="PE99")

    assert d.routable is False
    assert "not in the inventory" in d.reason


# --------------------------------------------------------------------------- #
# Alertmanager -- the payload shape T-005 recorded
# --------------------------------------------------------------------------- #


def _alert(status="firing", **labels):
    return {"version": "4", "groupKey": "gk", "status": status, "receiver": "webhook",
            "alerts": [{"status": status, "labels": labels, "annotations": {},
                        "startsAt": "t", "fingerprint": "f"}]}


def test_a_labelled_alert_routes():
    ds = er.route_alertmanager(
        _alert(alertname="BgpSessionDown", device="PE2", subject="10.255.0.31")
    )
    assert len(ds) == 1 and ds[0].routable
    assert ds[0].suggested_command() == [
        "nettools", "investigate", "PE2", "10.255.0.31", "--flow", "bgp_session"
    ]


def test_the_t005_gap_is_named_when_the_device_label_is_missing():
    ds = er.route_alertmanager(_alert(alertname="BgpSessionDown", subject="10.255.0.31"))
    assert not ds[0].routable
    assert "T-005" in ds[0].reason


def test_a_resolved_alert_never_triggers_an_investigation():
    ds = er.route_alertmanager(
        _alert(status="resolved", alertname="BgpSessionDown",
               device="PE2", subject="10.255.0.31")
    )
    assert not ds[0].routable
    assert "resolution" in ds[0].reason


def test_an_unknown_alertname_is_unrouted():
    ds = er.route_alertmanager(_alert(alertname="DiskFull", device="PE2"))
    assert not ds[0].routable
    assert "ALERTNAME_FLOW_TABLE" in ds[0].reason


# --------------------------------------------------------------------------- #
# The sniffing entry point + CLI
# --------------------------------------------------------------------------- #


def test_route_event_sniffs_json_and_lines():
    assert er.route_event(json.dumps(
        _alert(alertname="BgpSessionDown", device="PE2", subject="10.255.0.31")
    ))[0].routable
    assert er.route_event("", device="PE2")[0].routable is False


def test_cli_exit_codes_branch_for_an_orchestrator(tmp_path):
    from agent_nettools import cli
    routable = json.dumps(_alert(alertname="BgpSessionDown",
                                 device="PE2", subject="10.255.0.31"))
    p = tmp_path / "e.json"
    p.write_text(routable)

    import argparse
    args = argparse.Namespace(file=str(p), device=None, format="json", quiet=True)
    assert cli._cmd_route_event(args) == cli.EXIT_OK

    p.write_text('{"alerts":[{"status":"resolved","labels":{"alertname":"BgpSessionDown"}}]}')
    assert cli._cmd_route_event(args) == cli.EXIT_WARNING
