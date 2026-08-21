"""Tests for `log_window.py`'s temporal-shape addition: B-418.

`shape_window`, `dedupe`, `drop_collector_noise` and friends are already
exercised indirectly through `test_investigation.py`, `test_grounding.py`,
`test_correlate_prompt.py` and `test_end_to_end_offline.py` -- this file adds
the one thing none of those cover: `aggregate_by_mnemonic` and the timestamp
arithmetic (`parse_device_timestamp`, `seconds_between`) it is built on.

Structured around the design note both the backlog row and
`evidence-reduction.md` §3.4 give for why this exists: "60 events evenly
spread over an hour is a chronic condition; 60 events in ninety seconds is an
incident" -- same `count`, opposite meaning, and `max_rate_1m`/`bursts` are
what tells them apart. Every abstention (a `None` timing field) is paired
with the positive case that produces a real value, per OBS-181.
"""

from __future__ import annotations

from agent_nettools import log_window, template_parsers

# --------------------------------------------------------------------------- #
# parse_device_timestamp / seconds_between
# --------------------------------------------------------------------------- #


def test_parse_device_timestamp_reads_the_device_string():
    parsed = log_window.parse_device_timestamp("Aug 16 07:41:54.688 UTC")
    assert parsed == (8, 16, 7, 41, 54, 688)


def test_parse_device_timestamp_refuses_unrecognised_text():
    """Positive control for the refusal above: a well-formed string parses,
    a malformed one -- and the empty string `logs_loki` uses for a line its
    own regex could not match -- does not, rather than guessing."""

    assert log_window.parse_device_timestamp("") is None
    assert log_window.parse_device_timestamp("not a timestamp") is None
    assert log_window.parse_device_timestamp("2026-08-16T07:41:54Z") is None


def test_seconds_between_computes_the_measured_154_second_gap():
    """The exact gap `evidence-reduction.md` names as the design constraint:
    interface event at 07:41:54.748, BGP event at 07:44:28.097."""

    earlier = log_window.parse_device_timestamp("Aug 16 07:41:54.748 UTC")
    later = log_window.parse_device_timestamp("Aug 16 07:44:28.097 UTC")
    gap = log_window.seconds_between(earlier, later)
    assert round(gap, 3) == 153.349
    assert 154 - gap < 1  # rounds to the "154 seconds" the docs and mnemonics.yaml cite


def test_seconds_between_is_zero_for_identical_timestamps():
    t = log_window.parse_device_timestamp("Aug 16 07:41:54.688 UTC")
    assert log_window.seconds_between(t, t) == 0.0


def test_seconds_between_reanchors_a_year_boundary_forward():
    """The one case a missing year can invert: a window spanning New Year's.
    31 Dec 23:59:59 -> 1 Jan 00:00:01 must read as a 2-second gap, never as a
    ~365-day negative one."""

    earlier = log_window.parse_device_timestamp("Dec 31 23:59:59.000 UTC")
    later = log_window.parse_device_timestamp("Jan  1 00:00:01.000 UTC")
    gap = log_window.seconds_between(earlier, later)
    assert gap == 2.0


# --------------------------------------------------------------------------- #
# aggregate_by_mnemonic -- synthetic records, the design note's own example
# --------------------------------------------------------------------------- #


def _record(mnemonic: str, timestamp: str, text: str = "") -> dict:
    return {"mnemonic": mnemonic, "timestamp": timestamp, "text": text or mnemonic}


def test_a_singleton_has_no_rate_and_no_window_not_a_zero_one():
    """The core "absence is never zero" assertion for this module: one
    occurrence has no interval to compute a rate over, so `window_seconds`
    and `rate_per_hour` must be `None` -- reading `0.0` here would assert
    "this never happens", a claim one observation cannot support."""

    records = [_record("ROUTING-BGP-5-NSR_STATE_CHANGE", "Aug 16 07:44:44.782 UTC")]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.count == 1
    assert agg.window_seconds is None
    assert agg.rate_per_hour is None
    # Positive control: max_rate_1m/bursts ARE measured even for one record
    # (a real "1 occurrence in its own 1-minute window", "0 bursts" -- not a
    # refusal, because there is nothing ambiguous about counting one thing).
    assert agg.max_rate_1m == 1
    assert agg.bursts == 0


def test_two_simultaneous_occurrences_are_a_measured_zero_window():
    """Distinct from the singleton case above: TWO occurrences at the same
    millisecond is a real, measured zero-length interval, not an absence --
    `window_seconds == 0.0` here is correct, and `rate_per_hour` still
    refuses (undefined at a zero-length window) rather than reporting
    infinity."""

    records = [
        _record("PKT_INFRA-LINK-5-CHANGED", "Aug 16 07:41:54.688 UTC"),
        _record("PKT_INFRA-LINK-5-CHANGED", "Aug 16 07:41:54.688 UTC"),
    ]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.window_seconds == 0.0
    assert agg.rate_per_hour is None
    assert agg.max_rate_1m == 2
    assert agg.bursts == 1


def test_chronic_condition_scores_zero_bursts():
    """`evidence-reduction.md` §3.4's own worked example: 60 events evenly
    spread across an hour (one per minute) is chronic, not an incident.
    Exact 60-second spacing must NOT read as a burst -- `gap < window`, not
    `<=`, is what this test pins."""

    records = [
        _record("PKT_INFRA-PQMON-6-QUEUE_DROP", f"Aug 16 07:{minute:02d}:00.000 UTC")
        for minute in range(60)
    ]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.count == 60
    assert agg.bursts == 0
    assert agg.max_rate_1m == 2  # a rolling 60s window straddles at most two 1/min ticks
    # 60 occurrences span 59 one-minute intervals (first to last), so the
    # exact rate is 60 / (59/60) hours -- close to, not exactly, 60/hr.
    assert round(agg.rate_per_hour, 2) == round(60 * 3600 / 3540, 2)


def test_an_incident_scores_a_high_peak_rate_and_one_burst():
    """The other half of the same worked example: 60 events in ninety
    seconds is an incident. Same `count` order of magnitude as the chronic
    case is not asserted here (60 vs 60) -- what differs, and must differ, is
    `max_rate_1m` and `bursts`."""

    records = [
        _record("PKT_INFRA-PQMON-6-QUEUE_DROP", f"Aug 16 07:41:{second:02d}.000 UTC")
        for second in range(0, 60, 2)
    ] + [
        _record("PKT_INFRA-PQMON-6-QUEUE_DROP", f"Aug 16 07:42:{second:02d}.000 UTC")
        for second in range(0, 30, 2)
    ]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.count == 45
    assert agg.bursts == 1  # one continuous flurry, not many
    assert agg.max_rate_1m >= 30  # far denser than the chronic case's peak of 2
    assert agg.rate_per_hour > 60.0  # 45 events inside 88 seconds is a much higher rate


def test_untimed_records_are_counted_not_silently_dropped():
    """A record whose own device timestamp does not parse still happened --
    it counts towards `count` and `untimed`, and is excluded from (not
    silently folded into) every timing field."""

    records = [
        _record("SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL", "not a real timestamp"),
        _record("SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL", "Aug 16 07:41:54.688 UTC"),
    ]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.count == 2
    assert agg.untimed == 1
    # One real timed occurrence remains -- still a singleton for timing purposes.
    assert agg.window_seconds is None
    assert agg.first_seen == "Aug 16 07:41:54.688 UTC"


def test_all_untimed_records_produce_no_timing_fields_at_all():
    """Positive control for the case above: when NOTHING timed survives,
    every timing field refuses rather than defaulting to a number that would
    look measured."""

    records = [_record("SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL", "garbage")]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.count == 1
    assert agg.untimed == 1
    assert agg.window_seconds is None
    assert agg.rate_per_hour is None
    assert agg.max_rate_1m is None
    assert agg.bursts is None


def test_order_is_first_appearance_not_alphabetical():
    records = [
        _record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 07:44:28.097 UTC"),
        _record("PKT_INFRA-LINK-5-CHANGED", "Aug 16 07:41:54.688 UTC"),
    ]
    aggs = log_window.aggregate_by_mnemonic(records)
    assert [a.mnemonic for a in aggs] == ["ROUTING-BGP-5-ADJCHANGE", "PKT_INFRA-LINK-5-CHANGED"]


def test_sample_is_the_most_recent_occurrences_verbatim_text():
    records = [
        _record("ROUTING-ISIS-5-ADJCHANGE", "Aug 15 23:07:29.347 UTC", "Adjacency to P1 Up"),
        _record("ROUTING-ISIS-5-ADJCHANGE", "Aug 16 07:41:54.688 UTC", "Adjacency to P1 Down"),
    ]
    (agg,) = log_window.aggregate_by_mnemonic(records)
    assert agg.sample == "Adjacency to P1 Down"


def test_no_minimum_count_threshold_a_singleton_survives():
    """The same rule `shape_window` already pins for the window as a whole,
    asserted here at the aggregation grain: a singleton among many repeated
    mnemonics is never dropped."""

    records = [_record("PKT_INFRA-LINK-5-CHANGED", f"Aug 16 07:{m:02d}:00.000 UTC") for m in range(50)]
    records.append(_record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 08:00:00.000 UTC"))
    aggs = log_window.aggregate_by_mnemonic(records)
    mnemonics = {a.mnemonic for a in aggs}
    assert "ROUTING-BGP-5-ADJCHANGE" in mnemonics
    bgp = next(a for a in aggs if a.mnemonic == "ROUTING-BGP-5-ADJCHANGE")
    assert bgp.count == 1


# --------------------------------------------------------------------------- #
# Integration: the real measured `broken` window
# --------------------------------------------------------------------------- #


def _pe2_broken_shaped() -> log_window.ShapedWindow:
    raw = open("tests/fixtures/cisco_xr/PE2/broken/show-logging-last-200.txt").read()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    assert status is template_parsers.PARSE_OK
    return log_window.shape_window(parsed["records"])


def test_aggregate_over_the_real_broken_window_finds_the_repeated_isis_flap():
    """`ROUTING-ISIS-5-ADJCHANGE` occurs 4 times in the captured `broken`
    window (P1 down, P1 up, P1 down again, P3 down) -- a repeated mnemonic
    real enough to exercise burst/rate math end to end, not a synthetic
    fixture."""

    shaped = _pe2_broken_shaped()
    aggs = {a.mnemonic: a for a in log_window.aggregate_by_mnemonic(shaped.records)}
    isis = aggs["ROUTING-ISIS-5-ADJCHANGE"]
    assert isis.count == 4
    assert isis.untimed == 0
    assert isis.max_rate_1m == 2  # the two near-simultaneous drops in the isolation burst
    assert isis.bursts == 1  # one flurry (the two simultaneous drops); the other two are isolated


def test_aggregate_over_the_real_broken_window_reports_every_record():
    """Every kept record belongs to exactly one mnemonic's `count` -- the
    aggregate is a lossless partition of `shaped.records`, never a filter."""

    shaped = _pe2_broken_shaped()
    aggs = log_window.aggregate_by_mnemonic(shaped.records)
    assert sum(a.count for a in aggs) == len(shaped.records)
