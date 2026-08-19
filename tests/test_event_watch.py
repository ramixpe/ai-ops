"""Tests for `event_watch.py`: the B-201/B-630 dry-run trigger watcher.

No network anywhere — every test drives `watch_device`/`watch_fabric` through
the `fetcher=` seam `logs_loki.run_named_query` already exposes (never the
real `_http_fetcher`), the same idiom `tests/test_logs_loki.py` uses. Canned
responses reuse REAL message text captured from this fabric's own fixtures
(`tests/fixtures/cisco_xr/PE2/broken/show-logging-last-200.txt`), wrapped in
Loki's wire shape (host-prefixed) the way `logs_loki._LOKI_LOG_LINE` expects
— never hand-invented log lines.

OBS-181's rule is followed deliberately: `test_positive_control_*` proves the
mechanism CAN emit a routable decision, using the real, reviewed
`event_routing.MNEMONIC_FLOW_TABLE` and a real flow name, with only the
`trigger.fires` policy bit flipped via the injection seam — because as of
this measurement (2026-08-19) the live, reviewed `mnemonics.yaml` table has
zero `fires: true` entries (every one is excluded for a stated, measured
reason — see that file's own header and `docs` this task produced), and a
suite that only ever proves refusals is not proof the accept path works.
"""

from __future__ import annotations

import copy
import json

import pytest

from agent_nettools import event_routing, logs_loki
from agent_nettools import event_watch as W
from agent_nettools.event_routing import RoutingDecision
from agent_nettools.knowledge import load_mnemonic_table

# --------------------------------------------------------------------------- #
# Canned Loki responses -- same shape/helpers as tests/test_logs_loki.py.
# --------------------------------------------------------------------------- #

_PE2_IP = "172.20.250.22"  # inventory/lab.yaml: PE2 -> mgmt_ip 172.20.250.22


def _stream(labels: dict, values: list[tuple[str, str]]) -> dict:
    return {"stream": labels, "values": [[ts, line] for ts, line in values]}


def _loki_ok(streams: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "streams", "result": streams}}


def _canned_fetcher(response: dict, *, calls: list | None = None):
    def fetcher(base_url: str, params: dict) -> dict:
        if calls is not None:
            calls.append((base_url, dict(params)))
        return response

    return fetcher


def _raising_fetcher(exc: Exception):
    def fetcher(base_url: str, params: dict) -> dict:
        raise exc

    return fetcher


_PE2_LABELS = {
    "host": "PE2.sota-xrd", "job": "sota-routers", "severity": "err", "source_ip": _PE2_IP,
}

# Verbatim message bodies from tests/fixtures/cisco_xr/PE2/broken/
# show-logging-last-200.txt, lines 186-189 and 206 -- a real Down/Up flap on
# one interface, plus a real BGP adjacency change. Only the Loki host prefix
# ("PE2.sota-xrd ") is added, matching logs_loki._LOKI_LOG_LINE's wire shape.
_LINK_DOWN = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 15 23:07:29.168 UTC: ifmgr[236]: "
    "%PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, changed state to Down "
)
_LINK_UP = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 15 23:07:29.189 UTC: ifmgr[236]: "
    "%PKT_INFRA-LINK-3-UPDOWN : Interface GigabitEthernet0/0/0/0, changed state to Up "
)
_LINEPROTO_DOWN = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 15 23:07:29.168 UTC: ifmgr[236]: "
    "%PKT_INFRA-LINEPROTO-5-UPDOWN : Line protocol on Interface GigabitEthernet0/0/0/0, "
    "changed state to Down "
)
_LINEPROTO_UP = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 15 23:07:29.190 UTC: ifmgr[236]: "
    "%PKT_INFRA-LINEPROTO-5-UPDOWN : Line protocol on Interface GigabitEthernet0/0/0/0, "
    "changed state to Up "
)
_BGP_PEER_31 = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 16 07:44:28.097 UTC: bgp[1084]: "
    "%ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.31 Down - BGP Notification sent, "
    "hold time expired (VRF: default) (AS: 65000) "
)
# Second peer's real fixture wording (tests/fixtures/cisco_xr/RR1/broken/...),
# re-hosted under PE2's stream to build a synthetic two-peer window -- the
# message BODY is real fixture text either way; only the pairing (two
# different real lines inside one canned response) is synthetic, the same
# thing tests/test_logs_loki.py's own _LINE_A/_LINE_B already do.
_BGP_PEER_12 = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 16 07:44:40.000 UTC: bgp[1084]: "
    "%ROUTING-BGP-5-ADJCHANGE : neighbor 10.255.0.12 Down - BGP Notification sent, "
    "hold time expired (VRF: default) (AS: 65000) "
)
_SSH_ERR = (
    "PE2.sota-xrd RP/0/RP0/CPU0:Aug 19 03:52:56.214 UTC: sshd[363565]: "
    "%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL : sshd[363565]: Read error from "
    "remote host 172.20.250.1 port 50086: Connection reset by peer "
)
_GARBAGE_LINE = "this line matches nothing logs_loki._LOKI_LOG_LINE recognises"


def _values(*lines: str, start_ns: int = 1_755_000_000_000_000_000) -> list[tuple[str, str]]:
    """Loki delivers ``direction=backward`` (newest first); callers pass
    lines oldest-to-newest for readability, this reverses and stamps
    ascending ingest timestamps, matching what a real backward-ordered
    response looks like once un-reversed for display."""

    ns = [str(start_ns + i * 1000) for i in range(len(lines))]
    return list(zip(reversed(ns), reversed(lines), strict=True))


def _table_with_override(mnemonic: str, **trigger_overrides) -> list[dict]:
    """A deep copy of the REAL, reviewed mnemonics.yaml table with one
    entry's `trigger` block overridden -- the injection seam
    `build_trigger_index`/`validate_trigger_table` document, used here to
    prove the mechanism accepts a `fires: true` entry without needing one to
    exist in the live table today (OBS-181's positive-control rule)."""

    table = copy.deepcopy(list(load_mnemonic_table()))
    found = False
    for entry in table:
        if entry["mnemonic"] == mnemonic:
            entry["trigger"] = {**entry["trigger"], **trigger_overrides}
            found = True
    assert found, f"fixture bug: {mnemonic!r} is not in the real table"
    return table


# --------------------------------------------------------------------------- #
# The live, reviewed table: zero fires today, and why -- pinned so a future
# change to mnemonics.yaml has to consciously touch this test, not silently
# start (or stop) triggering.
# --------------------------------------------------------------------------- #


def test_the_live_table_has_zero_fires_true_entries_as_of_the_2026_08_19_sweep():
    """Pins the measured state this whole change reports: every mnemonic
    this fabric's Loki feed carries was checked against the trigger bar and
    excluded, each for its own stated reason. This is expected to change the
    day a mnemonic is actually observed clearing the bar -- and that day
    should touch this assertion deliberately, with a fresh measurement
    beside it, not as a side effect of an unrelated edit."""

    fires = [e["mnemonic"] for e in load_mnemonic_table() if e["trigger"]["fires"]]
    assert fires == []


def test_every_table_entry_has_a_well_shaped_trigger_block():
    for entry in load_mnemonic_table():
        trigger = entry.get("trigger")
        assert isinstance(trigger, dict), entry["mnemonic"]
        assert isinstance(trigger.get("fires"), bool), entry["mnemonic"]
        assert trigger.get("reason", "").strip(), entry["mnemonic"]
        assert trigger.get("measured", "").strip(), entry["mnemonic"]


def test_the_real_table_validates_with_no_inconsistency():
    W.validate_trigger_table()  # must not raise


# --------------------------------------------------------------------------- #
# validate_trigger_table: the loud-config-error guard, proven non-vacuous.
# --------------------------------------------------------------------------- #


def test_fires_true_with_no_flow_table_entry_is_a_loud_error_not_a_silent_false():
    """PKT_INFRA-LINK-5-CHANGED has a real, non-null `investigate_with` (an
    interface flow genuinely applies) but is deliberately NOT in
    event_routing.MNEMONIC_FLOW_TABLE — that table's extractors are only
    built for the mnemonics actually reviewed and wired. Forcing
    fires: true on it must be a loud configuration error, not a quietly
    ignored flag, and must be a DIFFERENT error than the null-investigate_with
    case below (this is the second, independent check)."""

    bad_table = _table_with_override("PKT_INFRA-LINK-5-CHANGED", fires=True)
    with pytest.raises(W.TriggerTableInconsistency, match="MNEMONIC_FLOW_TABLE"):
        W.validate_trigger_table(bad_table)


def test_fires_true_with_null_investigate_with_is_also_a_loud_error():
    bad_table = _table_with_override("MGBL-SYS-5-CONFIG_I", fires=True)
    with pytest.raises(W.TriggerTableInconsistency, match="investigate_with"):
        W.validate_trigger_table(bad_table)


def test_watch_device_itself_refuses_a_bad_trigger_table_before_fetching_anything():
    """Not just a standalone function nobody calls -- wired into the real
    entry point, and validated BEFORE the network call."""

    bad_table = _table_with_override("IP-TCP-3-NOAUTH", fires=True)
    calls: list = []
    with pytest.raises(W.TriggerTableInconsistency):
        W.watch_device(
            "PE2", fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
            trigger_table=bad_table,
        )
    assert calls == [], "a bad trigger table must be caught before Loki is ever queried"


# --------------------------------------------------------------------------- #
# Positive control (OBS-181) + its negative-control companion.
# --------------------------------------------------------------------------- #


def test_positive_control_a_cleared_mnemonic_produces_a_routable_decision():
    """PKT_INFRA-LINK-3-UPDOWN, real message, real MNEMONIC_FLOW_TABLE
    extractor, only the policy bit flipped. Proves the accept path exists —
    without this, every other "excluded" assertion in this suite (and in
    mnemonics.yaml) would be indistinguishable from a mechanism that simply
    never routes anything."""

    table = _table_with_override("PKT_INFRA-LINK-3-UPDOWN", fires=True)
    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok([_stream(_PE2_LABELS, _values(_LINK_DOWN))])),
        trigger_table=table,
    )

    assert report.status == "success"
    assert report.routable_count() == 1
    [obs] = report.observations
    assert obs.decision.routable is True
    assert obs.decision.flow == "interface"
    assert obs.decision.device == "PE2"
    assert obs.decision.subject == "GigabitEthernet0/0/0/0"
    assert obs.decision.source_kind == "loki"
    assert obs.decision.suggested_command() == [
        "nettools", "investigate", "PE2", "GigabitEthernet0/0/0/0", "--flow", "interface",
    ]


def test_negative_control_the_same_line_is_unrouted_against_the_real_table():
    """The exact same line as the positive control above, this time against
    the REAL, reviewed table (no override) — must NOT route. Shows the
    default is a true negative, not a mechanism that cannot say yes."""

    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok([_stream(_PE2_LABELS, _values(_LINK_DOWN))])),
    )

    assert report.status == "success"
    assert report.routable_count() == 0
    [obs] = report.observations
    assert obs.decision.routable is False
    assert "not observed via Loki" in obs.decision.reason


# --------------------------------------------------------------------------- #
# Collapse before deciding: the dedup design, exercised.
# --------------------------------------------------------------------------- #


def test_repeated_lines_for_one_subject_collapse_into_one_observation_with_a_count():
    """A Down-then-Up flap on ONE interface -- two PKT_INFRA-LINK-3-UPDOWN
    lines, same subject -- must be one decision with occurrence_count=2, not
    two separate routing suggestions for one root cause."""

    table = _table_with_override("PKT_INFRA-LINK-3-UPDOWN", fires=True)
    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok(
            [_stream(_PE2_LABELS, _values(_LINK_DOWN, _LINK_UP))]
        )),
        trigger_table=table,
    )

    assert report.routable_count() == 1
    [obs] = report.observations
    assert obs.occurrence_count == 2
    assert obs.first_seen and obs.last_seen
    assert obs.first_seen != obs.last_seen


def test_different_mnemonics_that_pair_are_not_fused_by_this_mechanism():
    """LINK-3-UPDOWN and LINEPROTO-5-UPDOWN fire together on every real flap
    (mnemonics.yaml's own severity_note), but this module deliberately does
    NOT fuse across different mnemonics (see event_watch's module docstring,
    'What is NOT implemented') -- two mnemonics, two groups, even though
    only one (LINK-3-UPDOWN here) is cleared to trigger."""

    table = _table_with_override("PKT_INFRA-LINK-3-UPDOWN", fires=True)
    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok([_stream(
            _PE2_LABELS,
            _values(_LINK_DOWN, _LINEPROTO_DOWN, _LINK_UP, _LINEPROTO_UP),
        )])),
        trigger_table=table,
    )

    assert len(report.observations) == 2
    assert report.routable_count() == 1
    by_mnemonic = {o.decision.matched: o for o in report.observations}
    assert by_mnemonic["PKT_INFRA-LINK-3-UPDOWN"].decision.routable is True
    assert by_mnemonic["PKT_INFRA-LINK-3-UPDOWN"].occurrence_count == 2
    assert by_mnemonic["PKT_INFRA-LINEPROTO-5-UPDOWN"].decision.routable is False
    assert by_mnemonic["PKT_INFRA-LINEPROTO-5-UPDOWN"].occurrence_count == 2


def test_two_different_subjects_of_one_mnemonic_stay_two_separate_decisions():
    """Two different BGP peers going down is two different root causes —
    grouping by mnemonic alone would wrongly merge them."""

    table = _table_with_override("ROUTING-BGP-5-ADJCHANGE", fires=True)
    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok([_stream(
            _PE2_LABELS, _values(_BGP_PEER_31, _BGP_PEER_12),
        )])),
        trigger_table=table,
    )

    assert report.routable_count() == 2
    subjects = {o.decision.subject for o in report.observations}
    assert subjects == {"10.255.0.31", "10.255.0.12"}
    assert all(o.occurrence_count == 1 for o in report.observations)


def test_the_operators_own_worked_example_stays_excluded_at_any_volume():
    """SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL, the mnemonic the brief itself
    names, against the REAL table -- must never route, however many DISTINCT
    lines arrive in one window (distinct ports/timestamps, like the real
    measured burst -- 20 byte-IDENTICAL lines would instead exercise
    `logs_loki`'s own upstream pipeline-redelivery dedup, B-206b's concern,
    not this module's root-cause collapse; this test is about the latter)."""

    lines = [
        "PE2.sota-xrd RP/0/RP0/CPU0:Aug 19 03:52:{:02d}.000 UTC: sshd[363565]: "
        "%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL : sshd[363565]: Read error from "
        "remote host 172.20.250.1 port {}: Connection reset by peer ".format(i, 50000 + i)
        for i in range(20)
    ]
    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok([_stream(_PE2_LABELS, _values(*lines))])),
    )

    assert report.routable_count() == 0
    [obs] = report.observations  # 20 distinct lines, same mnemonic, no subject -> one group
    assert obs.occurrence_count == 20
    assert "steady state" in obs.decision.reason


# --------------------------------------------------------------------------- #
# Honesty: absence, coverage, unparsed lines, fetch failure.
# --------------------------------------------------------------------------- #


def test_unparsed_lines_are_counted_and_never_silently_dropped():
    report = W.watch_device(
        "PE2",
        fetcher=_canned_fetcher(_loki_ok([_stream(
            _PE2_LABELS, _values(_GARBAGE_LINE, _LINK_DOWN),
        )])),
    )

    assert report.unparsed_lines == 1
    assert len(report.observations) == 1  # only the recognised line groups


def test_coverage_travels_with_every_successful_report_and_states_the_severity_gap():
    report = W.watch_device(
        "PE2", fetcher=_canned_fetcher(_loki_ok([_stream(_PE2_LABELS, _values(_SSH_ERR))])),
    )

    assert report.coverage is not None
    assert report.coverage["source"] == "loki"
    assert report.coverage["severity_available"] == [3, 4]
    assert any("severities" in gap for gap in report.coverage["gaps"])


def test_a_fetch_failure_is_a_structured_error_never_a_crash():
    report = W.watch_device(
        "PE2", fetcher=_raising_fetcher(logs_loki.LokiTransportError("loki is down")),
    )

    assert report.status == "error"
    assert report.coverage is None
    assert report.observations == ()
    assert any("loki is down" in e for e in report.errors)


def test_an_unknown_device_is_a_structured_error_and_the_fetcher_is_never_called():
    calls: list = []
    report = W.watch_device(
        "NOT-A-REAL-DEVICE", fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )
    assert report.status == "error"
    assert calls == []


def test_an_empty_window_is_a_success_with_no_observations_not_an_error():
    """Absence is not zero, and it is also not a failure -- these are three
    different states this module must keep distinguishable."""

    report = W.watch_device("PE2", fetcher=_canned_fetcher(_loki_ok([])))
    assert report.status == "success"
    assert report.observations == ()
    assert report.coverage["query_complete"] is True


# --------------------------------------------------------------------------- #
# watch_fabric: every inventory device, or a caller-given subset.
# --------------------------------------------------------------------------- #


def test_watch_fabric_defaults_to_every_inventory_device():
    reports = W.watch_fabric(fetcher=_canned_fetcher(_loki_ok([])))
    assert {r.device for r in reports} == {
        "P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1",
    }


def test_watch_fabric_honours_an_explicit_device_subset():
    reports = W.watch_fabric(devices=["PE2"], fetcher=_canned_fetcher(_loki_ok([])))
    assert [r.device for r in reports] == ["PE2"]


def test_watch_fabric_also_validates_the_trigger_table_once_up_front():
    bad_table = _table_with_override("IP-TCP-3-NOAUTH", fires=True)
    calls: list = []
    with pytest.raises(W.TriggerTableInconsistency):
        W.watch_fabric(
            devices=["PE2"], fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
            trigger_table=bad_table,
        )
    assert calls == []


# --------------------------------------------------------------------------- #
# The dry-run surface: python -m agent_nettools.event_watch
# --------------------------------------------------------------------------- #


def test_main_prints_json_and_exits_ok_when_something_routes(monkeypatch, capsys):
    routable = RoutingDecision(
        routable=True, flow="interface", device="PE2", subject="GigabitEthernet0/0/0/0",
        source_kind="loki", matched="PKT_INFRA-LINK-3-UPDOWN", reason="test",
    )
    report = W.WatchReport(
        device="PE2", status="success", coverage={"source": "loki"},
        observations=(W.LokiObservation(routable, 1, "t1", "t1"),),
    )
    monkeypatch.setattr(W, "watch_fabric", lambda **kw: (report,))

    exit_code = W._main([])
    out = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert out[0]["device"] == "PE2"
    assert out[0]["routable_count"] == 1


def test_main_exits_1_when_nothing_routes():
    def fake_watch_fabric(**kw):
        return (W.WatchReport(device="PE2", status="success", coverage={}, observations=()),)

    import agent_nettools.event_watch as ew

    original = ew.watch_fabric
    ew.watch_fabric = fake_watch_fabric
    try:
        assert ew._main([]) == 1
    finally:
        ew.watch_fabric = original


def test_main_exits_2_when_every_device_errors():
    def fake_watch_fabric(**kw):
        return (W.WatchReport(device="PE2", status="error", coverage=None, errors=("x",)),)

    import agent_nettools.event_watch as ew

    original = ew.watch_fabric
    ew.watch_fabric = fake_watch_fabric
    try:
        assert ew._main([]) == 2
    finally:
        ew.watch_fabric = original


def test_main_parses_device_and_window_flags(monkeypatch):
    captured = {}

    def fake_watch_fabric(**kw):
        captured.update(kw)
        return ()

    monkeypatch.setattr(W, "watch_fabric", fake_watch_fabric)
    W._main(["--device", "PE2", "--device", "RR1", "--since-seconds", "300", "--limit", "50"])

    assert captured["devices"] == ["PE2", "RR1"]
    assert captured["since_seconds"] == 300
    assert captured["limit"] == 50


# --------------------------------------------------------------------------- #
# Structural: never wired to auto-execute anything.
# --------------------------------------------------------------------------- #


def test_the_watcher_never_imports_investigation_or_cli():
    """B-201's brief: emit the decision and stop. This module must have no
    import path to the function that would actually run an investigation or
    to the CLI at all -- checked at the source level so a future edit cannot
    quietly add one without this test noticing."""

    import inspect

    source = inspect.getsource(W)
    forbidden = (
        "from .investigation import", "from . import investigation",
        "from .cli import", "from . import cli",
        "import agent_nettools.cli", "import agent_nettools.investigation",
    )
    for needle in forbidden:
        assert needle not in source, needle


def test_default_window_is_a_short_poll_not_the_measurement_ceiling():
    assert 0 < W.DEFAULT_WATCH_WINDOW_SECONDS < 3600
    assert W.DEFAULT_WATCH_LIMIT < 1000  # logs_loki's own per-query ceiling


# --------------------------------------------------------------------------- #
# MNEMONIC_FLOW_TABLE lookup helper, unit-level.
# --------------------------------------------------------------------------- #


def test_flow_and_extractor_lookup_matches_event_routing_directly():
    found = W._flow_and_extractor_for("PKT_INFRA-LINK-3-UPDOWN")
    assert found is not None
    flow, extract = found
    assert flow == "interface"
    assert extract("Interface GigabitEthernet0/0/0/0, changed state to Down") == (
        "GigabitEthernet0/0/0/0"
    )


def test_flow_and_extractor_lookup_is_none_for_an_unrouted_mnemonic():
    assert W._flow_and_extractor_for("IP-TCP-3-NOAUTH") is None


def test_grouping_matches_event_routing_mnemonic_flow_table_exactly():
    """Sanity: every mnemonic event_watch can route is also one
    event_routing already reviewed -- no parallel, drifting table."""

    routed_by_watch = {
        entry["mnemonic"]
        for entry in load_mnemonic_table()
        if entry["trigger"]["fires"]
    }
    routed_by_event_routing = {m for m, _f, _e in event_routing.MNEMONIC_FLOW_TABLE}
    assert routed_by_watch <= routed_by_event_routing
