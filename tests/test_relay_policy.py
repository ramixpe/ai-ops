"""B-209 -- report relay hardening: silence + ownership + de-dup, wired together.

Every suppression path here (`relay_policy.relay` returning ``"silenced"`` or
``"suppressed"`` instead of ``"sent"``) is paired with a positive control per
OBS-181: a relay that suppresses everything must not pass this file. And
every new fail-direction claim in `relay_policy.py`'s own docstring
(malformed silence/ownership file, corrupt de-dup state, a failed or no-op
send never marking a signature as delivered) gets its own test here, because
those are exactly the decisions where "silently dropping a page" would be
worse than the alternative -- see the module docstring's "Fail-closed, and
which way closed is" section.

No network: `_Capture` is the same fake `urlopen` shape `test_notifier.py`
uses, kept local rather than imported across test modules (the project's
convention keeps cross-test-file sharing in `tests/helpers.py`, and this
fixture is small enough not to earn a place there).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent_nettools import health as H
from agent_nettools import notifier as N
from agent_nettools import ownership as O
from agent_nettools import relay_policy as R

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _report() -> dict:
    return {
        "authoritative": True,
        "finding": "interface_line_down",
        "observations": [{"claim": "1/5 interface on PE2 is broken", "evidence_key": "k"}],
        "interpretations": [],
        "recommendation": {},
    }


class _Capture:
    def __init__(self, status: int = 200, raises: Exception | None = None):
        self.status, self.raises, self.requests = status, raises, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.raises is not None:
            raise self.raises
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _telegram_env(monkeypatch, *, token="SECRET-TOKEN-123", chats="4242"):
    monkeypatch.setenv(N.NOTIFIER_ENV, "telegram")
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, token)
    monkeypatch.setenv(N.TELEGRAM_CHAT_ENV, chats)


NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _relay(monkeypatch, capture, **kwargs):
    monkeypatch.setattr(N.urllib.request, "urlopen", capture)
    kwargs.setdefault("now", NOW)
    return R.relay(
        _report(), device=kwargs.pop("device", "PE2"),
        subject=kwargs.pop("subject", "Gi0/0/0/0"),
        finding=kwargs.pop("finding", "interface_line_down"),
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Baseline: no silence, no ownership table, no de-dup configured -> unchanged
# behaviour from a bare `notifier.notify` call.
# --------------------------------------------------------------------------- #


def test_relay_sends_with_nothing_configured(monkeypatch):
    """The control every suppression test below is measured against."""

    _telegram_env(monkeypatch)
    capture = _Capture()
    record = _relay(monkeypatch, capture)

    assert record["decision"] == "sent"
    assert len(capture.requests) == 1
    assert record["silence"] is None
    assert record["dedup"]["evaluated"] is False
    assert record["dedup"]["suppressed"] is False
    assert record["ownership"]["owners"] == ["default"]


def test_relay_under_the_default_provider_is_no_provider_not_a_failure(monkeypatch):
    monkeypatch.delenv(N.NOTIFIER_ENV, raising=False)
    capture = _Capture()
    record = _relay(monkeypatch, capture)

    assert record["decision"] == "no_provider"
    assert record["deliveries"][0]["ok"] is True
    assert capture.requests == []


# --------------------------------------------------------------------------- #
# Silence (reused: health.find_silence, unmodified)
# --------------------------------------------------------------------------- #


def test_a_matching_silence_blocks_delivery_on_every_owner_channel(monkeypatch):
    _telegram_env(monkeypatch)
    capture = _Capture()
    silence = H.Silence(
        id="maint-1", reason="planned", created_by="rami",
        expires_at=NOW + timedelta(hours=1), device="PE2", rule="interface_line_down",
    )
    record = _relay(monkeypatch, capture, silences=(silence,))

    assert record["decision"] == "silenced"
    assert record["silence"]["id"] == "maint-1"
    assert capture.requests == []
    # Silenced before de-dup is ever consulted -- absence-never-zero: this is
    # NOT "evaluated, found nothing", it is "not evaluated at all".
    assert record["dedup"]["evaluated"] is False
    # Ownership is still resolved and named, even though nothing was sent.
    assert record["ownership"]["owners"] == ["default"]


def test_positive_control_a_silence_for_a_different_device_does_not_match(monkeypatch):
    """OBS-181: proves the silence check discriminates rather than always
    suppressing -- the one thing every suppression test here must show."""

    _telegram_env(monkeypatch)
    capture = _Capture()
    silence = H.Silence(
        id="maint-1", reason="planned", created_by="rami",
        expires_at=NOW + timedelta(hours=1), device="PE9",  # not PE2
    )
    record = _relay(monkeypatch, capture, silences=(silence,))

    assert record["decision"] == "sent"
    assert len(capture.requests) == 1


def test_an_expired_silence_does_not_match(monkeypatch):
    _telegram_env(monkeypatch)
    capture = _Capture()
    silence = H.Silence(
        id="maint-1", reason="planned", created_by="rami",
        expires_at=NOW - timedelta(hours=1), device="PE2",
    )
    record = _relay(monkeypatch, capture, silences=(silence,))

    assert record["decision"] == "sent"


def test_a_malformed_silence_file_degrades_to_no_silences_not_to_no_delivery(monkeypatch, tmp_path):
    """Fail-direction rule 1: a bad silence file must not silently drop a page."""

    bad = tmp_path / "silences.yaml"
    bad.write_text("{not: valid: yaml: [", encoding="utf-8")
    _telegram_env(monkeypatch)
    capture = _Capture()

    record = _relay(monkeypatch, capture, silence_path=str(bad))

    assert record["decision"] == "sent"
    assert record["silence_source"]["error"] is not None


# --------------------------------------------------------------------------- #
# Ownership (reused: ownership.resolve_ownership, unmodified)
# --------------------------------------------------------------------------- #


def test_ownership_table_routes_to_its_own_telegram_channel(monkeypatch):
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    monkeypatch.delenv(N.TELEGRAM_CHAT_ENV, raising=False)
    capture = _Capture()
    table = O.OwnershipTable(
        rules=(O.OwnershipRule(name="edge", owner="noc", device="PE2"),),
        owners={
            "noc": O.Owner(name="noc", channel="telegram:555"),
            "net-eng": O.Owner(name="net-eng", channel="telegram:111"),
        },
        default_owner="net-eng",
    )
    record = _relay(monkeypatch, capture, ownership_table=table)

    assert record["decision"] == "sent"
    assert record["ownership"]["owners"] == ["noc"]
    assert json.loads(capture.requests[0].data)["chat_id"] == "555"


def test_positive_control_an_unmatched_device_routes_to_the_default_owner(monkeypatch):
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    monkeypatch.delenv(N.TELEGRAM_CHAT_ENV, raising=False)
    capture = _Capture()
    table = O.OwnershipTable(
        rules=(O.OwnershipRule(name="edge", owner="noc", device="PE9"),),  # not PE2
        owners={
            "noc": O.Owner(name="noc", channel="telegram:555"),
            "net-eng": O.Owner(name="net-eng", channel="telegram:111"),
        },
        default_owner="net-eng",
    )
    record = _relay(monkeypatch, capture, ownership_table=table)

    assert record["ownership"]["owners"] == ["net-eng"]
    assert json.loads(capture.requests[0].data)["chat_id"] == "111"


def test_a_malformed_ownership_file_falls_back_to_the_default_owner_not_to_nobody(
    monkeypatch, tmp_path
):
    """Fail-direction rule 2: a bad ownership file must not drop the finding."""

    bad = tmp_path / "owners.yaml"
    bad.write_text("not a mapping at all\n- just a list", encoding="utf-8")
    _telegram_env(monkeypatch)
    capture = _Capture()

    record = _relay(monkeypatch, capture, ownership_path=str(bad))

    assert record["decision"] == "sent"
    assert record["ownership"]["owners"] == [O.DEFAULT_OWNER_NAME]
    assert record["ownership_source"]["error"] is not None


def test_escalation_pages_both_the_primary_and_the_escalation_target(monkeypatch):
    _telegram_env(monkeypatch)
    capture = _Capture()
    table = O.OwnershipTable(
        rules=(),
        owners={
            "noc": O.Owner(name="noc", channel=O.DEFAULT_CHANNEL,
                            escalate_to="lead", escalate_after_seconds=60),
            "lead": O.Owner(name="lead", channel="telegram:999"),
        },
        default_owner="noc",
    )
    record = _relay(monkeypatch, capture, ownership_table=table, age_seconds=120)

    assert record["ownership"]["owners"] == ["noc", "lead"]
    assert record["ownership"]["escalated"] is True
    assert len(record["deliveries"]) == 2
    assert len(capture.requests) == 2


# --------------------------------------------------------------------------- #
# De-duplication (the one genuinely new mechanism)
# --------------------------------------------------------------------------- #


def test_dedup_is_off_by_default_two_identical_calls_both_send(monkeypatch):
    """Nothing about tonight's live Telegram delivery changes unless an
    operator opts in by setting NETTOOLS_RELAY_STATE_FILE."""

    _telegram_env(monkeypatch)
    capture = _Capture()
    monkeypatch.delenv(R.NETTOOLS_RELAY_STATE_FILE_ENV, raising=False)

    _relay(monkeypatch, capture)
    _relay(monkeypatch, capture)

    assert len(capture.requests) == 2


def test_dedup_suppresses_an_identical_repeat_within_the_window(monkeypatch, tmp_path):
    _telegram_env(monkeypatch)
    capture = _Capture()
    state = tmp_path / "relay_state.json"

    first = _relay(monkeypatch, capture, dedup_state_path=str(state))
    second = _relay(monkeypatch, capture, dedup_state_path=str(state),
                     now=NOW + timedelta(seconds=30))

    assert first["decision"] == "sent"
    assert second["decision"] == "suppressed"
    assert second["dedup"]["evaluated"] is True
    assert len(capture.requests) == 1  # the second call never reached the wire


def test_positive_control_a_changed_finding_is_never_suppressed(monkeypatch, tmp_path):
    """OBS-181: proves de-dup keys on the signature, not a blanket per-object
    mute -- an update must always get through."""

    _telegram_env(monkeypatch)
    capture = _Capture()
    state = tmp_path / "relay_state.json"

    _relay(monkeypatch, capture, dedup_state_path=str(state))
    changed = _relay(monkeypatch, capture, dedup_state_path=str(state),
                      finding="all_layers_healthy", now=NOW + timedelta(seconds=30))

    assert changed["decision"] == "sent"
    assert len(capture.requests) == 2


def test_positive_control_a_different_subject_is_never_suppressed(monkeypatch, tmp_path):
    _telegram_env(monkeypatch)
    capture = _Capture()
    state = tmp_path / "relay_state.json"

    _relay(monkeypatch, capture, dedup_state_path=str(state), subject="Gi0/0/0/0")
    other = _relay(monkeypatch, capture, dedup_state_path=str(state),
                    subject="Gi0/0/0/1", now=NOW + timedelta(seconds=30))

    assert other["decision"] == "sent"
    assert len(capture.requests) == 2


def test_dedup_resends_as_a_reminder_once_the_window_elapses(monkeypatch, tmp_path):
    _telegram_env(monkeypatch)
    capture = _Capture()
    state = tmp_path / "relay_state.json"

    _relay(monkeypatch, capture, dedup_state_path=str(state), dedup_window_seconds=60)
    later = _relay(monkeypatch, capture, dedup_state_path=str(state),
                    dedup_window_seconds=60, now=NOW + timedelta(seconds=61))

    assert later["decision"] == "sent"
    assert len(capture.requests) == 2


def test_a_corrupt_dedup_state_file_never_suppresses(monkeypatch, tmp_path):
    """Fail-direction rule 3, pinned: the failure this module accepts
    (a possible duplicate) is deliberately preferred over the failure it
    refuses (a silently dropped page) when the state file cannot be read."""

    _telegram_env(monkeypatch)
    capture = _Capture()
    state = tmp_path / "relay_state.json"
    state.write_text("{not valid json", encoding="utf-8")

    record = _relay(monkeypatch, capture, dedup_state_path=str(state))

    assert record["decision"] == "sent"
    assert record["dedup"]["evaluated"] is True
    assert record["dedup"]["state_readable"] is False
    assert record["dedup"]["suppressed"] is False


def test_a_dedup_record_with_no_usable_timestamp_is_treated_as_unknown(monkeypatch, tmp_path):
    _telegram_env(monkeypatch)
    capture = _Capture()
    state = tmp_path / "relay_state.json"
    key = R._dedup_key("PE2", "Gi0/0/0/0")
    state.write_text(json.dumps({
        key: {"signature": ["interface_line_down", None], "last_notified_at": "not-a-timestamp"}
    }), encoding="utf-8")

    record = _relay(monkeypatch, capture, dedup_state_path=str(state))

    assert record["decision"] == "sent"
    assert record["dedup"]["state_readable"] is False


def test_dedup_state_is_not_recorded_when_delivery_fails(monkeypatch, tmp_path):
    """Fail-direction rule 4, extended: a failed send must not be remembered
    as "already told", or a real outage would suppress the real page once
    the channel recovers."""

    monkeypatch.setenv(N.NOTIFIER_ENV, "telegram")
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    monkeypatch.setenv(N.TELEGRAM_CHAT_ENV, "4242")
    state = tmp_path / "relay_state.json"

    import urllib.error
    failing = _Capture(raises=urllib.error.URLError("no route to host"))
    first = _relay(monkeypatch, failing, dedup_state_path=str(state))
    assert first["decision"] == "failed"

    working = _Capture()
    second = _relay(monkeypatch, working, dedup_state_path=str(state),
                     now=NOW + timedelta(seconds=5))

    assert second["decision"] == "sent"
    assert len(working.requests) == 1


def test_dedup_state_is_not_recorded_under_the_default_no_op_provider(monkeypatch, tmp_path):
    monkeypatch.delenv(N.NOTIFIER_ENV, raising=False)
    state = tmp_path / "relay_state.json"

    first = _relay(monkeypatch, _Capture(), dedup_state_path=str(state))
    assert first["decision"] == "no_provider"
    assert not state.exists()


def test_dedup_can_be_force_disabled_even_with_a_state_path_given(monkeypatch, tmp_path):
    _telegram_env(monkeypatch)
    state = tmp_path / "relay_state.json"
    capture = _Capture()

    _relay(monkeypatch, capture, dedup_state_path=str(state), dedup_enabled=False)
    second = _relay(monkeypatch, capture, dedup_state_path=str(state), dedup_enabled=False,
                     now=NOW + timedelta(seconds=1))

    assert second["decision"] == "sent"
    assert len(capture.requests) == 2


# --------------------------------------------------------------------------- #
# notify_owner passthrough fix (B-681 completeness, closed as part of B-209)
# --------------------------------------------------------------------------- #


def test_the_rca_fields_reach_an_owner_routed_message_too(monkeypatch):
    """Before this change, `notify_owner` silently dropped trustworthy/cause/
    ticket_id on every routed (non-default-channel) delivery -- the exact
    "reads correct evidence, reports it worse" defect shape this codebase
    refuses elsewhere. Proven end to end through `relay()`."""

    _telegram_env(monkeypatch)
    capture = _Capture()
    table = O.OwnershipTable(
        rules=(),
        owners={"net-eng": O.Owner(name="net-eng", channel="telegram:111")},
        default_owner="net-eng",
    )
    record = _relay(
        monkeypatch, capture, ownership_table=table,
        trustworthy=False,
        cause={"rung": "interface", "device": "PE2", "reason": "line protocol down"},
        ticket_id="deadbeef",
    )

    assert record["decision"] == "sent"
    text = json.loads(capture.requests[0].data)["text"]
    assert "Cause: interface on PE2" in text
    assert "Trustworthy: NO" in text
    assert "Ticket: deadbeef" in text


def test_notify_owner_omitting_the_rca_fields_is_unchanged(monkeypatch):
    """OBS-181 positive control for the passthrough fix: a caller that does
    not pass the new fields (every caller before B-209) gets byte-identical
    output to before."""

    from agent_nettools import ownership as O2

    capture = _Capture()
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    monkeypatch.setattr(N.urllib.request, "urlopen", capture)
    owner = O2.Owner(name="net-eng", channel="telegram:111")

    record = N.notify_owner(_report(), owner, device="PE2", subject="Gi0/0/0/0",
                             finding="interface_line_down")

    assert record["ok"] is True
    text = json.loads(capture.requests[0].data)["text"]
    assert "Cause:" not in text
    assert "Trustworthy" not in text
    assert "Ticket:" not in text


# --------------------------------------------------------------------------- #
# The egress bound still holds through the new entry point
# --------------------------------------------------------------------------- #


def test_relay_still_cannot_receive_an_evidence_bundle():
    """Same structural guarantee `test_notifier.py` pins on `notify` --
    `relay()`'s own signature has no parameter an evidence bundle could
    arrive in either."""

    import inspect

    params = set(inspect.signature(R.relay).parameters)
    assert "evidence" not in params
    assert "commands" not in params
    assert "parsed" not in params
    # `report` stays the only bundle-shaped positional; everything else is a
    # scalar, an id, a small code-typed dict, or a loader override.
    assert params == {
        "report", "device", "subject", "finding", "trustworthy", "cause",
        "ticket_id", "role", "age_seconds", "silences", "silence_path",
        "ownership_table", "ownership_path", "dedup_enabled",
        "dedup_state_path", "dedup_window_seconds", "now",
    }
