from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_nettools.epoch import EvidenceEpoch, Observation
from agent_nettools.evidence_cache import EvidenceCache, EvidenceCacheMiss
from agent_nettools.mcp_epoch_validation import validate_asserted_object


def _epoch(collected_at: str) -> EvidenceEpoch:
    return EvidenceEpoch(
        observations=(Observation("bgp", "RR1", 0.0, 1.0, {"status": "success"}, collected_at),)
    )


def test_cache_returns_only_evidence_within_explicit_freshness_window():
    cache = EvidenceCache()
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    cache.put_epoch(_epoch(started.isoformat()))

    entry = cache.require_fresh(device="RR1", key="bgp", maximum_age_seconds=10, now=started + timedelta(seconds=10))

    assert entry.envelope == {"status": "success"}


def test_cache_refuses_missing_and_stale_evidence():
    cache = EvidenceCache()
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    with pytest.raises(EvidenceCacheMiss, match="not cached"):
        cache.require_fresh(device="RR1", key="bgp", maximum_age_seconds=1, now=started)
    cache.put_epoch(_epoch(started.isoformat()))
    with pytest.raises(EvidenceCacheMiss, match="stale"):
        cache.require_fresh(device="RR1", key="bgp", maximum_age_seconds=1, now=started + timedelta(seconds=2))


def test_epoch_validation_refuses_stale_bundle_before_object_predicate():
    cache = EvidenceCache()
    started = datetime(2026, 8, 24, tzinfo=timezone.utc)
    cache.put_epoch(_epoch(started.isoformat()))

    reason = validate_asserted_object(
        cache,
        device="RR1",
        kind="bgp_peer",
        identifier="10.255.0.12",
        maximum_age_seconds=1,
        now=started + timedelta(seconds=2),
    )

    assert reason == "fresh evidence is unavailable: cached evidence is stale"