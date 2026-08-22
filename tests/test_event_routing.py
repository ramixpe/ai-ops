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
    """Positive control (B-711): a Down line must still produce a fully
    routable decision with the right flow and subject -- a test suite that
    only checks refusals would pass just as well if routing were broken
    outright."""

    line = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    d = er.route_syslog_line(line, device="RR1")

    assert d.routable is True
    assert d.flow == "bgp_session"
    assert d.subject == "10.255.0.12"
    assert d.transition == "down"
    assert d.suggested_command() == [
        "nettools", "investigate", "RR1", "10.255.0.12", "--flow", "bgp_session"
    ]


def test_a_real_link_updown_line_routes_to_the_interface_flow():
    """Positive control (B-711), interface side: a Down line still routes
    fully, with the direction now recorded alongside it."""

    line = _real_line("PKT_INFRA-LINK-3-UPDOWN", device="PE2")
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is True
    assert d.flow == "interface"
    assert d.subject and d.subject.startswith("GigabitEthernet")
    assert d.transition == "down"


def test_a_bgp_down_decision_is_byte_for_byte_unchanged_apart_from_transition():
    """B-711 requirement 4: a Down decision must be identical to what it was
    before the fix, apart from the new field. Compares the full `as_dict()`
    payload rather than a handful of fields, so a regression anywhere in the
    decision (not just in `transition`) would be caught here."""

    line = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    d = er.route_syslog_line(line, device="RR1")
    payload = d.as_dict()

    assert payload.pop("transition") == "down"
    assert payload == {
        "routable": True,
        "flow": "bgp_session",
        "device": "RR1",
        "subject": "10.255.0.12",
        "reason": "ROUTING-BGP-5-ADJCHANGE routes to the bgp_session flow",
        "source_kind": "syslog",
        "matched": "ROUTING-BGP-5-ADJCHANGE",
        "suggested_command": [
            "nettools", "investigate", "RR1", "10.255.0.12", "--flow", "bgp_session"
        ],
    }


def test_a_bgp_recovery_is_refused_not_investigated():
    """B-711's core defect: `neighbor X Up` used to produce the identical
    `RoutingDecision` as `neighbor X Down` because the direction was matched
    and discarded. No fixture captures a real BGP Up line (only Down ones
    were captured), so this is built from the real Down line at
    `tests/fixtures/cisco_xr/PE2/broken/show-logging-last-200.txt` -- same
    device, process, timestamp and peer (10.255.0.31), the direction and
    trailing explanation swapped for the "Up" shape verified against real
    Loki output in OBS-702/FINDINGS.md and BACKLOG.md's B-711 entry."""

    line = (
        "RP/0/RP0/CPU0:Aug 16 07:44:28.097 UTC: bgp[1084]: "
        "%ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Up"
    )
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is False
    assert d.transition == "up"
    assert d.flow == "bgp_session"
    assert d.subject == "10.255.0.31"
    assert "recovery" in d.reason
    assert d.suggested_command() is None


def test_a_link_updown_recovery_is_refused_not_investigated():
    """The interface analogue of B-711 -- verified against the module
    docstring's warning that this "almost certainly" had the same bug: it
    did. Real fixture line, `PKT_INFRA-LINK-3-UPDOWN ... changed state to Up`."""

    line = _real_line("PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, "
                       "changed state to Up", device="PE2")
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is False
    assert d.transition == "up"
    assert d.flow == "interface"
    assert d.subject == "GigabitEthernet0/0/0/0"
    assert "recovery" in d.reason
    assert d.suggested_command() is None


def test_a_lineproto_updown_recovery_is_refused_not_investigated():
    """`PKT_INFRA-LINEPROTO-5-UPDOWN` is a separate mnemonic from
    `PKT_INFRA-LINK-3-UPDOWN` sharing the same `_interface_subject`/
    `_interface_transition` extractors -- checked independently since the
    task explicitly warns against fixing one interface mnemonic and leaving
    the other broken."""

    line = _real_line("PKT_INFRA-LINEPROTO-5-UPDOWN : Line protocol on Interface "
                       "GigabitEthernet0/0/0/0, changed state to Up", device="PE2")
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is False
    assert d.transition == "up"
    assert d.flow == "interface"
    assert d.subject == "GigabitEthernet0/0/0/0"
    assert "recovery" in d.reason


def test_a_link_updown_down_is_unaffected_by_the_fix():
    """The interface-mnemonic analogue of the byte-for-byte test above."""

    line = _real_line("PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, "
                       "changed state to Down", device="PE2")
    d = er.route_syslog_line(line, device="PE2")
    payload = d.as_dict()

    assert payload.pop("transition") == "down"
    assert payload == {
        "routable": True,
        "flow": "interface",
        "device": "PE2",
        "subject": "GigabitEthernet0/0/0/0",
        "reason": "PKT_INFRA-LINK-3-UPDOWN routes to the interface flow",
        "source_kind": "syslog",
        "matched": "PKT_INFRA-LINK-3-UPDOWN",
        "suggested_command": [
            "nettools", "investigate", "PE2", "GigabitEthernet0/0/0/0",
            "--flow", "interface",
        ],
    }


def test_a_direction_that_does_not_parse_is_unknown_not_none():
    """B-711 requirement 1: a mnemonic with a direction concept whose
    direction text does not parse must not collapse into `None` -- `None` is
    reserved for a mnemonic with no direction concept at all. Chosen shape:
    the literal string ``"unknown"``, and the decision refuses to route
    rather than guess fault-vs-recovery. Constructed text: a real device
    process/timestamp/mnemonic prefix (matches the fixture format) with a
    trailing word that is neither "Down" nor "Up" -- plausible (a line get
    truncated, or a future IOS-XR variant), and it is exactly the case the
    stricter `_INTERFACE_TRANSITION` regex (vs. the looser subject regex)
    exists to catch: the interface name still parses, the direction does not."""

    line = (
        "RP/0/RP0/CPU0:Aug 16 07:44:06.366 UTC: ifmgr[236]: "
        "%PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, "
        "changed state to Reset"
    )
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is False
    assert d.transition == "unknown"
    assert d.transition is not None
    assert d.subject == "GigabitEthernet0/0/0/0"  # subject parsing is unaffected
    assert "could not be determined" in d.reason
    assert d.suggested_command() is None


def test_a_mnemonic_not_in_the_flow_table_has_no_transition_concept():
    """`transition=None` means "this mnemonic has no direction concept" --
    distinct from "unknown". A mnemonic absent from `MNEMONIC_FLOW_TABLE`
    entirely is the clearest case: routing never even reaches a transition
    extractor for it."""

    d = er.route_syslog_line(
        "RP/0/RP0/CPU0:Aug 16 07:00:00.000 UTC: sshd[123]: "
        "%SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL : Accepted authentication for clab",
        device="PE2",
    )
    assert d.routable is False
    assert d.transition is None


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
# Loki transport framing -- syslog-ng's leading host token (OBS-702/B-711
# follow-up). Every line above is `show logging` shape (no host); these use
# the *other* real shape, the one the overnight campaign actually measured
# in Loki and that `route_syslog_line` used to reject outright.
# --------------------------------------------------------------------------- #


def test_the_real_loki_bgp_line_measured_live_routes_correctly():
    """Verbatim from the overnight campaign's live measurement: this exact
    line, host prefix and all, used to come back `routable=False` with
    reason "not an IOS-XR log line this fabric's parser recognises". It must
    now route exactly as the equivalent `show logging`-shape line does."""

    line = (
        "PE2.sota-xrd RP/0/RP0/CPU0:Aug 22 21:31:55.194 UTC: bgp[1084]: "
        "%ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Down - Admin. "
        "shutdown (CEASE notification...)"
    )
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is True
    assert d.flow == "bgp_session"
    assert d.subject == "10.255.0.31"
    assert d.transition == "down"
    assert d.device == "PE2"


def test_a_loki_format_link_updown_line_routes_to_the_interface_flow():
    """Same Loki shape, the interface mnemonic: a real fixture body (never
    hand-typed) with a syslog-ng host token prepended, same as syslog-ng
    actually does before syslog-ng-forwarded lines land in Loki."""

    body = _real_line(
        "PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, "
        "changed state to Down", device="PE2",
    )
    line = f"PE2.sota-xrd {body}"
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is True
    assert d.flow == "interface"
    assert d.subject == "GigabitEthernet0/0/0/0"
    assert d.transition == "down"


def test_a_loki_format_link_updown_up_recovery_is_still_refused():
    """The B-711 recovery-vs-fault rule must keep holding for the Loki
    shape, not just the `show logging` shape it was proven against."""

    body = _real_line(
        "PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, "
        "changed state to Up", device="PE2",
    )
    line = f"PE2.sota-xrd {body}"
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is False
    assert d.transition == "up"
    assert "recovery" in d.reason


def test_the_host_prefix_and_bare_forms_of_one_line_decide_identically():
    """Positive control, Loki edition: stripping the transport framing must
    change nothing about the decision except that it now succeeds at all --
    same full payload either way, since both supply `device` from the
    caller, never from text."""

    body = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    bare = er.route_syslog_line(body, device="RR1")
    prefixed = er.route_syslog_line(f"RR1.sota-xrd {body}", device="RR1")

    assert bare.as_dict() == prefixed.as_dict()


def test_a_parsed_host_never_overrides_the_caller_supplied_device():
    """B-467's rule, re-affirmed for the new host token: even when the
    parsed host names a *different* device than the caller supplied,
    `device` on the decision is the caller's -- the parsed host is transport
    framing this fix strips and discards, never a second vote."""

    body = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    d = er.route_syslog_line(f"PE2.sota-xrd {body}", device="RR1")

    assert d.routable is True
    assert d.device == "RR1"


def test_junk_between_a_host_token_and_the_rp_marker_still_refuses():
    """Conservative-stripping guard: `_SYSLOG_HOST_PREFIX` only ever
    consumes a single leading token immediately followed by whitespace then
    `RP/0/RP0/CPU0:`. It must NOT scan forward for that marker anywhere in
    the string -- a line with stray tokens in between is genuinely
    malformed and must keep refusing, not be "rescued"."""

    body = _real_line("ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12")
    line = f"PE2 stray extra {body}"
    d = er.route_syslog_line(line, device="PE2")

    assert d.routable is False
    assert d.reason == "not an IOS-XR log line this fabric's parser recognises"


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


def test_an_alertmanager_subject_carrying_shell_metacharacters_is_refused():
    """2026-08-18 P0: the Alertmanager subject was never validated (the syslog
    path is), so `"10.0.0.1; touch /tmp/x #"` flowed into suggested_command.
    Now validated by reconstruction, same as the syslog path."""

    ds = er.route_alertmanager({"alerts": [{"status": "firing", "labels": {
        "alertname": "BgpSessionDown", "device": "PE2",
        "subject": "10.0.0.1; touch /tmp/x #"}}]})

    assert not ds[0].routable
    assert "not a valid" in ds[0].reason
    assert ds[0].suggested_command() is None


def test_a_valid_alertmanager_subject_still_routes():
    ds = er.route_alertmanager({"alerts": [{"status": "firing", "labels": {
        "alertname": "BgpSessionDown", "device": "PE2", "subject": "10.255.0.31"}}]})
    assert ds[0].routable and ds[0].subject == "10.255.0.31"


def test_malformed_alertmanager_json_never_crashes_the_router():
    """A non-dict alert entry, and a non-string device label, both crashed the
    CLI with a raw traceback (2026-08-18 P1). Both are now stated refusals."""

    assert not er.route_event('{"alerts":["not-a-dict"]}')[0].routable
    ds = er.route_alertmanager({"alerts": [{"status": "firing", "labels": {
        "alertname": "BgpSessionDown", "device": ["PE1"], "subject": "10.0.0.1"}}]})
    assert not ds[0].routable
