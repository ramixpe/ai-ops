from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_nettools.temporal_correlation import (
    ChangeEvidence,
    CorrelationClass,
    EventOnset,
    correlate_change,
)


def _onset(*, complete: bool = True, clock: str = "trusted") -> EventOnset:
    return EventOnset("event-1", datetime(2026, 8, 24, 12, tzinfo=timezone.utc), complete, clock)


def test_change_correlation_is_classified_without_a_causal_claim():
    onset = _onset()
    change = ChangeEvidence("commit-1", onset.observed_at - timedelta(seconds=60), "config_commit", True)

    result = correlate_change(onset, (change,))

    assert result.classification is CorrelationClass.PRECEDING_CHANGE
    assert result.change_id == "commit-1"
    assert result.delta_seconds == 60


def test_correlation_refuses_incomplete_or_untrusted_time_evidence():
    change = ChangeEvidence("commit-1", _onset().observed_at, "config_commit", True)

    assert correlate_change(_onset(complete=False), (change,)).classification is CorrelationClass.COVERAGE_LIMITED
    assert correlate_change(_onset(clock="unknown"), (change,)).classification is CorrelationClass.TEMPORALLY_INCOHERENT


def test_no_match_is_only_a_negative_over_complete_coverage():
    result = correlate_change(_onset(), ())

    assert result.classification is CorrelationClass.NO_MATCH_COMPLETE_COVERAGE