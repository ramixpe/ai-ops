"""Tests for `log_episodes.py`: event episodes, B-416.

The flagship acceptance test this module exists for is named directly in
`evidence-reduction.md` §12's own failure-mode table: **"Sequence loss ...
A known causal chain must appear as one episode in the correct order, across
a 154-second gap"** -- listed there as "Not covered -- B-416". This file
covers it against the real captured `broken` fixture, not a synthetic
stand-in, because the 154-second gap and the six-event chain it must not
sever are themselves measured facts, not invented ones.

Every abstention (a bound falling back to the default rather than a declared
timer; `coverage=None`; an unordered record) is paired with the positive
case that produces the opposite, per OBS-181.
"""

from __future__ import annotations

from agent_nettools import log_episodes, log_window, template_parsers
from agent_nettools.coverage import Coverage

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _record(mnemonic: str, timestamp: str, text: str = "") -> dict:
    facility = mnemonic.rsplit("-", 2)[0] if mnemonic else None
    return {
        "mnemonic": mnemonic,
        "facility": facility,
        "timestamp": timestamp,
        "text": text or mnemonic,
    }


def _pe2_broken_shaped() -> log_window.ShapedWindow:
    raw = open("tests/fixtures/cisco_xr/PE2/broken/show-logging-last-200.txt").read()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    assert status is template_parsers.PARSE_OK
    return log_window.shape_window(parsed["records"])


# --------------------------------------------------------------------------- #
# Protocol timer table -- declared, cited, and the two the backlog names
# --------------------------------------------------------------------------- #


def test_bgp_hold_timer_is_180_seconds_and_isis_is_30():
    by_protocol = {t.protocol: t for t in log_episodes.PROTOCOL_HOLD_TIMERS}
    assert by_protocol["bgp"].hold_seconds == 180
    assert by_protocol["bgp"].facility == "ROUTING-BGP"
    assert by_protocol["isis"].hold_seconds == 30
    assert by_protocol["isis"].facility == "ROUTING-ISIS"
    # Every declared timer carries a non-empty citation -- a number with no
    # reason attached is exactly the "intuition wearing a constant's clothing"
    # the module docstring refuses to ship.
    assert all(t.citation.strip() for t in log_episodes.PROTOCOL_HOLD_TIMERS)


# --------------------------------------------------------------------------- #
# The flagship test: the real measured six-event chain across 154 seconds
# --------------------------------------------------------------------------- #


def test_the_measured_154_second_causal_chain_survives_as_one_episode():
    """The exact failure mode `evidence-reduction.md` §12 names as
    uncovered before B-416: the interface, IS-IS and BGP events measured on
    PE2's `broken` window must land in ONE episode, in order, even though
    the BGP event follows the config commit by ~153.4s -- comfortably past
    any "a few seconds" proximity guess and comfortably inside BGP's 180s
    hold timer, which is the whole point."""

    shaped = _pe2_broken_shaped()
    result = log_episodes.build_episodes(list(shaped.records), device="PE2")

    documented_chain = [
        "PKT_INFRA-LINK-5-CHANGED",
        "PKT_INFRA-LINK-5-CHANGED",
        "ROUTING-ISIS-5-ADJCHANGE",
        "ROUTING-ISIS-5-ADJCHANGE",
        "MGBL-CONFIG-6-DB_COMMIT",
        "ROUTING-BGP-5-ADJCHANGE",
    ]

    # The chain must appear together, in order, inside exactly one episode --
    # never split across two by a proximity guess that ignored BGP's timer.
    hosting = [
        ep
        for ep in result.episodes
        if _is_subsequence_in_order(documented_chain, list(ep.mnemonics))
    ]
    assert len(hosting) == 1, (
        f"expected exactly one episode to carry the documented six-event "
        f"chain in order; found {len(hosting)} "
        f"(episodes: {[ep.mnemonics for ep in result.episodes]})"
    )
    episode = hosting[0]

    # The join that bridges the 154s gap must have used the BGP-derived
    # 180s bound, not a smaller one -- find the join whose gap is ~153.4s.
    wide_joins = [j for j in episode.joins if j.gap_seconds > 60]
    assert len(wide_joins) == 1
    join = wide_joins[0]
    assert round(join.gap_seconds, 1) == 153.3 or round(join.gap_seconds, 1) == 153.4
    assert join.bound_seconds == 180
    assert "bgp" in join.reason.lower()
    assert "180" in join.reason

    # The interface -> BGP gap itself, independent of episode construction,
    # is the measured design constraint the backlog row names.
    assert 153 <= join.gap_seconds <= 155


def test_a_naive_few_second_threshold_would_have_split_this_chain():
    """Negative control proving the 154s gap is NOT bridgeable by a "same
    instant" guess -- it is only bridgeable because BGP's own 180s hold
    timer covers it. If this assertion ever failed, the flagship test above
    would be passing for the wrong reason."""

    shaped = _pe2_broken_shaped()
    result = log_episodes.build_episodes(list(shaped.records), device="PE2")
    documented_chain = [
        "PKT_INFRA-LINK-5-CHANGED", "PKT_INFRA-LINK-5-CHANGED",
        "ROUTING-ISIS-5-ADJCHANGE", "ROUTING-ISIS-5-ADJCHANGE",
        "MGBL-CONFIG-6-DB_COMMIT", "ROUTING-BGP-5-ADJCHANGE",
    ]
    episode = next(
        ep for ep in result.episodes
        if _is_subsequence_in_order(documented_chain, list(ep.mnemonics))
    )
    widest_gap = max(j.gap_seconds for j in episode.joins)
    assert widest_gap > 5  # a "few seconds" bound would have severed this join


def test_the_prior_days_events_are_not_merged_into_the_incident():
    """The window also holds an unrelated interface flap and adjacency
    reset from the previous day (23:01-23:07 on 15 Aug), roughly 8.5 hours
    before the incident. Episode construction must not bridge that gap under
    any declared timer -- it is a different, older event, and merging it in
    would be exactly the false-correlation failure this module exists to
    avoid, not produce."""

    shaped = _pe2_broken_shaped()
    result = log_episodes.build_episodes(list(shaped.records), device="PE2")
    for ep in result.episodes:
        if len(ep.records) < 2:
            continue
        for join in ep.joins:
            assert join.gap_seconds < 3600, (
                f"episode {ep.episode_id} bridged an implausible "
                f"{join.gap_seconds}s gap"
            )


def _is_subsequence_in_order(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(any(x == n for x in it) for n in needle)


# --------------------------------------------------------------------------- #
# Per-pair bound derivation, in isolation from the real fixture
# --------------------------------------------------------------------------- #


def test_neither_side_declares_a_protocol_falls_back_to_the_smallest_timer():
    """Positive/negative pair: two records naming no known protocol fall
    back to the smallest DECLARED timer (30s, IS-IS) -- never an invented
    number -- and the reason says so explicitly."""

    records = [
        _record("MGBL-CONFIG-6-DB_COMMIT", "Aug 16 07:41:54.712 UTC"),
        _record("MGBL-SYS-5-CONFIG_I", "Aug 16 07:41:54.748 UTC"),
    ]
    result = log_episodes.build_episodes(records, device="PE2")
    assert len(result.episodes) == 1
    (join,) = result.episodes[0].joins
    assert join.bound_seconds == 30
    assert "smallest declared" in join.reason


def test_a_declared_protocol_on_either_side_is_used_over_the_default():
    """The positive case: when one side of the pair DOES name a declared
    protocol, its timer governs -- not the default floor."""

    records = [
        _record("MGBL-CONFIG-6-DB_COMMIT", "Aug 16 07:41:54.712 UTC"),
        _record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 07:44:28.097 UTC"),
    ]
    result = log_episodes.build_episodes(records, device="PE2")
    assert len(result.episodes) == 1  # 153s gap, bridged by BGP's 180s
    (join,) = result.episodes[0].joins
    assert join.bound_seconds == 180


def test_a_gap_wider_than_the_declared_bound_splits_the_episode():
    """The other half of the same case: push the same two mnemonics far
    enough apart (beyond even BGP's 180s) and they must NOT merge."""

    records = [
        _record("MGBL-CONFIG-6-DB_COMMIT", "Aug 16 07:41:54.712 UTC"),
        _record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 07:45:10.000 UTC"),  # ~195s later
    ]
    result = log_episodes.build_episodes(records, device="PE2")
    assert len(result.episodes) == 2


# --------------------------------------------------------------------------- #
# Singleton, ordering, and "no minimum" behaviour
# --------------------------------------------------------------------------- #


def test_no_minimum_count_an_isolated_event_is_its_own_episode():
    records = [_record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 07:44:28.097 UTC")]
    result = log_episodes.build_episodes(records, device="PE2")
    assert len(result.episodes) == 1
    assert result.episodes[0].is_singleton
    assert result.episodes[0].joins == ()


def test_episodes_are_chronologically_ordered_regardless_of_input_order():
    records = [
        _record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 07:44:28.097 UTC"),
        _record("PKT_INFRA-LINK-5-CHANGED", "Aug 16 07:41:54.688 UTC",
                 "Interface GigabitEthernet0/0/0/0, changed state to Administratively Down"),
    ]
    result = log_episodes.build_episodes(records, device="PE2")
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode.mnemonics == ("PKT_INFRA-LINK-5-CHANGED", "ROUTING-BGP-5-ADJCHANGE")
    assert episode.subject == "GigabitEthernet0/0/0/0"


def test_episode_ids_are_deterministic_and_sequential():
    records = [
        _record("PKT_INFRA-LINK-5-CHANGED", "Aug 15 10:00:00.000 UTC"),
        _record("ROUTING-BGP-5-ADJCHANGE", "Aug 16 10:00:00.000 UTC"),
    ]
    result = log_episodes.build_episodes(records, device="PE2")
    assert [ep.episode_id for ep in result.episodes] == ["EP-0001", "EP-0002"]


# --------------------------------------------------------------------------- #
# Absence is never zero -- coverage passthrough and unordered records
# --------------------------------------------------------------------------- #


def test_no_coverage_means_not_evaluated_never_measured_zero():
    """`coverage=None` (the caller never measured this window -- e.g. the
    device was unreachable) must not be conflated with `episodes=()`
    (measured, and genuinely found none)."""

    result = log_episodes.build_episodes([], device="PE2", coverage=None)
    assert result.episodes == ()
    assert result.evaluated is False


def test_real_coverage_with_zero_records_is_measured_zero():
    """Positive control: when coverage IS present, an empty episode set is a
    real finding, not a gap."""

    coverage = Coverage(device="PE2", source="device_buffer", query_complete=True)
    result = log_episodes.build_episodes([], device="PE2", coverage=coverage)
    assert result.episodes == ()
    assert result.evaluated is True


def test_unordered_records_are_counted_not_silently_dropped():
    records = [
        _record("ROUTING-BGP-5-ADJCHANGE", "garbage, not a timestamp"),
        _record("PKT_INFRA-LINK-5-CHANGED", "Aug 16 07:41:54.688 UTC"),
    ]
    result = log_episodes.build_episodes(records, device="PE2")
    assert result.records_unordered == 1
    assert result.records_considered == 2
    assert sum(len(ep.records) for ep in result.episodes) == 1


def test_all_records_unordered_produces_no_episodes_but_says_why():
    records = [_record("ROUTING-BGP-5-ADJCHANGE", "not a timestamp")]
    result = log_episodes.build_episodes(records, device="PE2")
    assert result.episodes == ()
    assert result.records_unordered == 1
    assert result.records_considered == 1
    assert "1 unordered" in result.summary()
