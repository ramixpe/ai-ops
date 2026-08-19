"""Maintenance windows / silences (B-483).

The whole point of this feature is a single guarantee: a silenced finding is
**reported differently, never removed**. Every test below that asserts a
verdict's severity/counts changed is paired with an assertion that the
finding itself is still present in ``findings`` -- proving the "vanish"
failure mode (OBS-188, OBS-202's shape, applied to a new feature) cannot
happen here, not merely that it doesn't happen to happen in these fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_nettools import health as H

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def _verdict(**findings_kwargs) -> dict:
    """A minimal `evaluate_device`-shaped verdict, built by hand.

    Deliberately not driven through `evaluate_device` itself -- this file is
    about the silence layer, which operates purely on the verdict shape, and
    building it by hand keeps each test's fixture legible without a fixture
    replay.
    """

    findings = findings_kwargs.get("findings", [])
    counts = {"critical": 0, "warning": 0, "info": 0}
    severity = "ok"
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        if H.severity_rank(f["severity"]) > H.severity_rank(severity):
            severity = f["severity"]
    return {
        "device": findings_kwargs.get("device", "PE2"),
        "role": "edge",
        "platform": "cisco_xr",
        "severity": severity,
        "findings": findings,
        "counts": counts,
        "unevaluated": [],
        "unsupported": [],
    }


def _finding(rule="bgp_session_down", severity="critical", subject="10.255.0.12"):
    return {
        "rule": rule, "severity": severity, "intent": "bgp",
        "message": f"BGP session to {subject} is not Established",
        "expected": "Established", "actual": "Idle", "subject": subject,
    }


# --------------------------------------------------------------------------- #
# Silence.matches -- what it covers, and how long
# --------------------------------------------------------------------------- #


def test_a_silence_with_no_fields_matches_everything_on_that_axis():
    """Omitting device/rule/subject means "any" for that field."""

    s = H.Silence(id="s1", reason="planned change", created_by="rami",
                  expires_at=NOW + timedelta(hours=1))
    assert s.matches(device="PE2", rule="bgp_session_down", subject="10.255.0.12", now=NOW)
    assert s.matches(device="RR1", rule="isis_isolated", subject=None, now=NOW)


def test_a_silence_narrowed_to_device_and_rule_and_subject_matches_only_that():
    s = H.Silence(id="s1", reason="flapping optic", created_by="rami",
                  expires_at=NOW + timedelta(hours=1),
                  device="PE2", rule="bgp_session_down", subject="10.255.0.12")
    assert s.matches(device="PE2", rule="bgp_session_down", subject="10.255.0.12", now=NOW)
    assert not s.matches(device="PE3", rule="bgp_session_down", subject="10.255.0.12", now=NOW)
    assert not s.matches(device="PE2", rule="isis_isolated", subject="10.255.0.12", now=NOW)
    assert not s.matches(device="PE2", rule="bgp_session_down", subject="10.255.0.13", now=NOW)


def test_a_silence_is_inactive_before_its_start_and_after_its_expiry():
    s = H.Silence(id="s1", reason="scheduled maintenance", created_by="rami",
                  starts_at=NOW, expires_at=NOW + timedelta(hours=2))
    before = NOW - timedelta(minutes=1)
    during = NOW + timedelta(hours=1)
    after = NOW + timedelta(hours=3)
    assert not s.matches(device="PE2", rule=None, subject=None, now=before)
    assert s.matches(device="PE2", rule=None, subject=None, now=during)
    assert not s.matches(device="PE2", rule=None, subject=None, now=after)


def test_every_silence_requires_an_expiry():
    """There is no way to construct a silence that never ends -- see the module
    docstring's "How long" section. Enforced at parse time, where a human
    filing one actually interacts with this."""

    with pytest.raises(H.SilenceError, match="expires_at"):
        H.parse_silences([{"reason": "x", "created_by": "rami"}])


def test_a_reason_and_a_creator_are_required():
    with pytest.raises(H.SilenceError, match="reason"):
        H.parse_silences([{
            "created_by": "rami", "expires_at": "2026-08-20T00:00:00+00:00",
        }])
    with pytest.raises(H.SilenceError, match="created_by"):
        H.parse_silences([{
            "reason": "x", "expires_at": "2026-08-20T00:00:00+00:00",
        }])


def test_a_naive_timestamp_is_refused_rather_than_guessed():
    with pytest.raises(H.SilenceError, match="timezone"):
        H.parse_silences([{
            "reason": "x", "created_by": "rami", "expires_at": "2026-08-20T00:00:00",
        }])


def test_a_z_suffixed_timestamp_is_accepted():
    silences = H.parse_silences([{
        "reason": "x", "created_by": "rami", "expires_at": "2026-08-20T00:00:00Z",
    }])
    assert silences[0].expires_at.tzinfo is not None


# --------------------------------------------------------------------------- #
# find_silence -- specificity ordering
# --------------------------------------------------------------------------- #


def test_the_more_specific_silence_wins_regardless_of_table_order():
    broad = H.Silence(id="broad", reason="whole device down for maint",
                      created_by="rami", expires_at=NOW + timedelta(hours=1),
                      device="PE2")
    narrow = H.Silence(id="narrow", reason="just this peer flaps during cutover",
                       created_by="rami", expires_at=NOW + timedelta(hours=1),
                       device="PE2", rule="bgp_session_down", subject="10.255.0.12")

    found_1 = H.find_silence([broad, narrow], device="PE2", rule="bgp_session_down",
                             subject="10.255.0.12", now=NOW)
    found_2 = H.find_silence([narrow, broad], device="PE2", rule="bgp_session_down",
                             subject="10.255.0.12", now=NOW)
    assert found_1.id == "narrow"
    assert found_2.id == "narrow"


def test_find_silence_returns_none_when_nothing_active_matches():
    s = H.Silence(id="s1", reason="x", created_by="rami", expires_at=NOW - timedelta(hours=1))
    assert H.find_silence([s], device="PE2", rule="bgp_session_down", now=NOW) is None


# --------------------------------------------------------------------------- #
# apply_silences -- the central guarantee: reported differently, never removed
# --------------------------------------------------------------------------- #


def test_a_silenced_finding_stays_in_the_findings_list():
    """The load-bearing test. A silence that removed a finding would be the
    OBS-188/OBS-202 defect in a new costume."""

    verdict = _verdict(findings=[_finding()])
    silence = H.Silence(id="maint", reason="planned BGP flap during migration",
                        created_by="rami", expires_at=NOW + timedelta(hours=1),
                        device="PE2", rule="bgp_session_down")

    result = H.apply_silences(verdict, [silence], now=NOW)

    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["rule"] == "bgp_session_down"
    assert finding["subject"] == "10.255.0.12"
    assert finding["silenced"] is True
    assert finding["silence"]["id"] == "maint"
    assert finding["silence"]["reason"] == "planned BGP flap during migration"
    assert finding["silence"]["created_by"] == "rami"


def test_a_silenced_finding_does_not_raise_severity_but_raw_severity_says_what_it_would_have_been():
    verdict = _verdict(findings=[_finding(severity="critical")])
    silence = H.Silence(id="maint", reason="x", created_by="rami",
                        expires_at=NOW + timedelta(hours=1), device="PE2")

    result = H.apply_silences(verdict, [silence], now=NOW)

    assert result["severity"] == "ok"          # nothing pages
    assert result["raw_severity"] == "critical"  # but this is never hidden
    assert result["counts"]["critical"] == 0
    assert result["counts"]["silenced"] == 1
    assert result["silences_applied"] is True


def test_an_unsilenced_finding_still_raises_severity_normally():
    verdict = _verdict(findings=[_finding(severity="critical")])
    result = H.apply_silences(verdict, [], now=NOW)

    assert result["severity"] == "critical"
    assert result["raw_severity"] == "critical"
    assert result["counts"]["critical"] == 1
    assert result["counts"]["silenced"] == 0
    assert result["silences_applied"] is False


def test_only_the_matching_finding_is_silenced_a_second_unrelated_one_still_pages():
    """A device-wide silence for one rule must not blanket-suppress a
    different, real fault on the same device."""

    verdict = _verdict(findings=[
        _finding(rule="bgp_session_down", severity="critical", subject="10.255.0.12"),
        _finding(rule="isis_isolated", severity="critical", subject=None),
    ])
    silence = H.Silence(id="maint", reason="x", created_by="rami",
                        expires_at=NOW + timedelta(hours=1),
                        device="PE2", rule="bgp_session_down")

    result = H.apply_silences(verdict, [silence], now=NOW)

    by_rule = {f["rule"]: f for f in result["findings"]}
    assert by_rule["bgp_session_down"]["silenced"] is True
    assert by_rule["isis_isolated"]["silenced"] is False
    assert result["severity"] == "critical"     # the real, unsilenced fault still pages
    assert result["raw_severity"] == "critical"
    assert result["counts"]["critical"] == 1
    assert result["counts"]["silenced"] == 1


def test_an_expired_silence_no_longer_suppresses_anything():
    verdict = _verdict(findings=[_finding(severity="critical")])
    expired = H.Silence(id="maint", reason="x", created_by="rami",
                        expires_at=NOW - timedelta(minutes=1), device="PE2")

    result = H.apply_silences(verdict, [expired], now=NOW)

    assert result["severity"] == "critical"
    assert result["findings"][0]["silenced"] is False


def test_apply_silences_does_not_mutate_the_input_verdict():
    verdict = _verdict(findings=[_finding(severity="critical")])
    silence = H.Silence(id="maint", reason="x", created_by="rami",
                        expires_at=NOW + timedelta(hours=1), device="PE2")

    H.apply_silences(verdict, [silence], now=NOW)

    assert verdict["severity"] == "critical"
    assert "silenced" not in verdict["findings"][0]


# --------------------------------------------------------------------------- #
# apply_silences_to_fabric -- the roll-up
# --------------------------------------------------------------------------- #


def test_fabric_roll_up_excludes_silenced_devices_from_paging_severity():
    fabric = {
        "severity": "critical",
        "counts": {"devices": 2, "by_severity": {"critical": 1, "warning": 0, "info": 0, "ok": 1},
                   "unevaluated_devices": []},
        "devices": {
            "PE2": _verdict(device="PE2", findings=[_finding(severity="critical")]),
            "RR1": _verdict(device="RR1", findings=[]),
        },
    }
    silence = H.Silence(id="maint", reason="x", created_by="rami",
                        expires_at=NOW + timedelta(hours=1), device="PE2")

    result = H.apply_silences_to_fabric(fabric, [silence], now=NOW)

    assert result["severity"] == "ok"
    assert result["counts"]["by_severity"] == {"critical": 0, "warning": 0, "info": 0, "ok": 2}
    assert result["counts"]["silenced_devices"] == ["PE2"]
    # And the finding is still right there for a human to read.
    assert result["devices"]["PE2"]["findings"][0]["silenced"] is True
    assert result["devices"]["PE2"]["raw_severity"] == "critical"


# --------------------------------------------------------------------------- #
# load_silences -- file handling
# --------------------------------------------------------------------------- #


def test_no_path_configured_means_no_silences_not_an_error():
    assert H.load_silences(None) == ()


def test_a_nonexistent_path_means_no_silences_not_an_error(tmp_path):
    assert H.load_silences(tmp_path / "does-not-exist.yaml") == ()


def test_a_real_silence_file_loads(tmp_path):
    path = tmp_path / "silences.yaml"
    path.write_text(
        "- device: PE2\n"
        "  rule: bgp_session_down\n"
        "  reason: planned migration\n"
        "  created_by: rami\n"
        "  expires_at: '2026-08-20T00:00:00+00:00'\n"
    )
    silences = H.load_silences(path)
    assert len(silences) == 1
    assert silences[0].device == "PE2"
    assert silences[0].reason == "planned migration"


def test_a_malformed_silence_file_raises_rather_than_loading_as_empty(tmp_path):
    """A typo'd silence file must not silently disable the silence -- that
    would page right through the outage it was filed to cover."""

    path = tmp_path / "silences.yaml"
    path.write_text("- reason: x\n  created_by: rami\n")  # missing expires_at
    with pytest.raises(H.SilenceError):
        H.load_silences(path)


def test_a_duplicate_id_is_rejected():
    with pytest.raises(H.SilenceError, match="twice"):
        H.parse_silences([
            {"id": "dup", "reason": "a", "created_by": "rami",
             "expires_at": "2026-08-20T00:00:00+00:00"},
            {"id": "dup", "reason": "b", "created_by": "rami",
             "expires_at": "2026-08-20T00:00:00+00:00"},
        ])
