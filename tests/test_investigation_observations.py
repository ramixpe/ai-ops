"""W4c: `InvestigationResult.observations` -- the per-command data (device,
source, command, timing) `ticket.py`'s `record_tool_event`/
`record_evidence_source` need (W4d, `cli.py`) that `session_summary` (a
pre-aggregated dict) does not carry.

Mirrors `tests/test_epoch.py`'s own `session_summary` precedent verbatim --
`test_investigation_result_carries_the_epochs_session_summary` (populated on
the real epoch path) and `test_an_injected_collector_also_gets_no_session_
summary` (`None`, not a vacuous empty tuple, on the `collector=` injection
path where no epoch is built) -- applied to this new field instead of
re-deriving the same two facts from scratch. A new file rather than an
addition to `test_epoch.py`: that file is outside this lane's ownership this
wave.
"""

from __future__ import annotations

from agent_nettools import investigation
from agent_nettools.epoch import Observation
from agent_nettools.fixtures import fixture_sender

SUBJECT = "10.255.0.12"  # PE2's loopback -- the flow's subject, on the far end


def _resolver(subject):
    return {"10.255.0.12": "PE2"}[subject]


def test_investigation_result_carries_the_epochs_observations():
    """The observations reach `InvestigationResult` unmodified -- straight
    off `EvidenceEpoch.observations`, so this field cannot itself drift from
    what the epoch actually collected."""

    result = investigation.investigate(
        "RR1", SUBJECT, sender=fixture_sender(label="broken"), resolver=_resolver,
    )

    assert result.observations is not None
    assert len(result.observations) > 0
    devices_seen = {obs.device for obs in result.observations}
    assert devices_seen == {"RR1", "PE2"}, (
        "bgp_session's own documented shape: RR1 and PE2 (test_epoch.py's "
        "test_session_counts_reflect_the_real_session_cost_per_device pins "
        "the same two devices for the same fixture/flow)"
    )
    # Not every observation's `.envelope` is a dict: `EvidenceEpoch.
    # commands_run`'s own docstring documents three pass-through entries
    # (`key` in "device"/"platform"/"timestamp") whose `.envelope` is a
    # plain string, mirrored into `for_device()`'s dict for `checks.py`'s
    # benefit. At least one real command envelope (a dict) must still be
    # present, or this test would be vacuously satisfied by only the
    # pass-through strings.
    dict_envelopes = [obs for obs in result.observations if isinstance(obs.envelope, dict)]
    assert dict_envelopes, "at least one observation must carry a real command envelope"
    for obs in result.observations:
        assert isinstance(obs, Observation)
        assert obs.collected_at
        assert obs.duration >= 0


def test_an_injected_collector_also_gets_no_observations():
    """Same reasoning as `session_summary`'s own None-on-collector-path
    precedent (`test_epoch.py`): no epoch was built on this path, so there
    is nothing to report. `None`, not an empty tuple a reader could mistake
    for "collected nothing"."""

    result = investigation.investigate(
        "RR1", SUBJECT, resolver=_resolver,
        collector=lambda d, r, s: investigation._collect_for_rung(
            d, r, s, sender=fixture_sender(label="broken")
        ),
    )

    assert result.observations is None
