"""T-035 — the report relay, and the guardrail that bounds what leaves the estate.

The first test in this file is the one the task specified be written before the
provider: **the notifier receives the report and not the evidence bundle.**
Everything else protects the contract that a broken notifier cannot fail an
investigation.

No network anywhere. `TelegramNotifier` takes an `opener` so the HTTP boundary
is a seam rather than something to monkeypatch globally.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

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
        "rungs_examined": 5,
        "observations": [
            {"claim": "1/5 bgp_session on RR1 is broken: session to 10.255.0.12 is Idle",
             "evidence_key": "RR1:bgp:10.255.0.12"},
            {"claim": "5/5 interface on PE2 is broken: 1 of 3 members healthy",
             "evidence_key": "PE2:interface:Gi0/0/0/0"},
        ],
        "interpretations": [
            {"claim": "interface_line_down on PE2", "based_on": ["obs-1", "obs-2"]}
        ],
        "recommendation": {"next_check": "Check PE2's uplinks", "requires_human": True},
    }


class _Capture:
    """A fake urlopen that records the request and returns a chosen status."""

    def __init__(self, status: int = 200, raises: Exception | None = None):
        self.status, self.raises, self.requests = status, raises, []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeout = timeout
        if self.raises is not None:
            raise self.raises
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _telegram(monkeypatch, capture, *, token="SECRET-TOKEN-123", chats="4242"):
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, token)
    monkeypatch.setenv(N.TELEGRAM_CHAT_ENV, chats)
    return N.TelegramNotifier(opener=capture)


def _argv(monkeypatch, cli, *extra):
    """Drive `cli.main()` the way `test_cli.py` does -- it reads sys.argv."""

    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", [
        "nettools", "investigate", "RR1", "10.255.0.12",
        "--from-fixtures", "--label", "broken", *extra,
    ])


# --------------------------------------------------------------------------- #
# The guardrail. Written first, per T-035.
# --------------------------------------------------------------------------- #


def test_the_notifier_cannot_receive_an_evidence_bundle():
    """The egress bound, and it is structural rather than filtered.

    `notify()` has no parameter an evidence bundle could arrive in. That is the
    guarantee -- not that a filter strips `data.commands`, but that raw device
    output has no route to this module at all. Same reasoning as
    `prompt_library` taking a `DescentResult` instead of text.

    If someone ever adds an `evidence=` parameter here, this test fails, and it
    should: the addition is the defect, not the test.

    B-681 added `trustworthy`/`cause`/`ticket_id` -- small, code-typed values
    (a bool, a three-field dict, an id), never a route for a bundle -- so the
    expected set below grew to name them explicitly rather than the test
    being loosened to `<=`/a subset check, which would stop catching a FUTURE
    `evidence=` addition just as surely as deleting the test would.
    """

    import inspect

    params = set(inspect.signature(N.notify).parameters)
    assert params == {
        "report", "device", "subject", "finding", "notifier",
        # B-483: a matched silence, which cannot carry evidence either --
        # see SilenceNotice's own docstring.
        "silence",
        # B-681: the RCA the second notification is supposed to carry. All
        # three are small, code-typed values -- a bool, a three-field dict of
        # code-authored strings, an id -- not a route an evidence bundle could
        # arrive through. The point of asserting the EXACT set, rather than a
        # subset, is that widening it is a deliberate act someone must come
        # here and justify.
        "trustworthy", "cause", "ticket_id",
    }

    send = set(inspect.signature(N.Notifier.send).parameters)
    assert send == {
        "self", "report", "subject", "device", "finding",
        "trustworthy", "cause", "ticket_id",
    }


def test_only_report_fields_are_rendered_into_the_message():
    """A bundle smuggled *inside* the report dict still does not render.

    The signature check above stops a bundle arriving as an argument. This
    stops it arriving as a stowaway: `render_report_text` reads named fields,
    never iterates the dict, so an unexpected key contributes nothing.
    """

    report = _report()
    report["commands"] = {"show running-config": "username admin secret hunter2"}
    report["parsed"] = {"records": [{"neighbor": "10.255.0.31"}]}

    text = N.render_report_text(report, device="RR1", subject="10.255.0.12",
                                finding="interface_line_down")

    assert "hunter2" not in text
    assert "running-config" not in text
    assert "interface_line_down" in text
    assert "1 of 3 members healthy" in text


# --------------------------------------------------------------------------- #
# B-681: the RCA, not just the alarm -- trustworthy/cause/ticket_id
# --------------------------------------------------------------------------- #


def test_omitting_the_new_fields_renders_byte_identical_output():
    """Positive control (OBS-181): the additive change changes nothing when
    a caller does not opt into it -- proves the new parameters are truly
    additive rather than a rewrite of the existing contract."""

    kwargs = dict(device="RR1", subject="10.255.0.12", finding="interface_line_down")
    before = N.render_report_text(_report(), **kwargs)
    after = N.render_report_text(
        _report(), trustworthy=None, cause=None, ticket_id=None, **kwargs
    )
    assert before == after


def test_the_cause_rung_and_device_are_rendered_when_localised():
    text = N.render_report_text(
        _report(), device="RR1", subject="10.255.0.12", finding="interface_line_down",
        cause={"rung": "interface", "device": "PE2", "reason": "line protocol down"},
    )
    assert "Cause: interface on PE2" in text


def test_no_cause_line_when_nothing_was_localised():
    """`cause` is `None` for `undetermined`/`no_fault_on_path`/etc -- the
    same "absent, not a fabricated line" rule every other optional field in
    this renderer already follows."""

    text = N.render_report_text(
        _report(), device="RR1", subject="10.255.0.12", finding="all_layers_healthy",
        cause=None,
    )
    assert "Cause:" not in text


def test_trustworthy_true_and_false_render_distinctly():
    yes = N.render_report_text(_report(), device="RR1", subject="10.255.0.12",
                                finding="interface_line_down", trustworthy=True)
    no = N.render_report_text(_report(), device="RR1", subject="10.255.0.12",
                               finding="interface_line_down", trustworthy=False)

    assert "Trustworthy: yes" in yes
    assert "Trustworthy: NO" in no
    assert "verify" in no.lower()


def test_trustworthy_omitted_renders_no_trustworthy_line():
    """`None` (never checked/not passed) must not read as either yes or no."""

    text = N.render_report_text(_report(), device="RR1", subject="10.255.0.12",
                                 finding="interface_line_down")
    assert "Trustworthy" not in text


def test_the_ticket_id_is_rendered_so_the_operator_can_pick_it_up():
    text = N.render_report_text(
        _report(), device="RR1", subject="10.255.0.12", finding="interface_line_down",
        ticket_id="a1b2c3d4e5f6",
    )
    assert "Ticket: a1b2c3d4e5f6" in text


def test_no_ticket_id_omits_the_ticket_line():
    text = N.render_report_text(_report(), device="RR1", subject="10.255.0.12",
                                 finding="interface_line_down")
    assert "Ticket:" not in text


def test_all_three_new_fields_reach_telegram_together(monkeypatch):
    """End to end through `notify()`/`TelegramNotifier`, not just the pure
    renderer -- proves the fields actually reach the wire, not only the
    function under direct test."""

    capture = _Capture()
    record = N.notify(
        _report(), device="RR1", subject="10.255.0.12", finding="interface_line_down",
        trustworthy=False,
        cause={"rung": "bgp_session", "device": "RR1", "reason": "session Idle"},
        ticket_id="deadbeef",
        notifier=_telegram(monkeypatch, capture),
    )

    assert record["ok"] is True
    text = json.loads(capture.requests[0].data)["text"]
    assert "Cause: bgp_session on RR1" in text
    assert "Trustworthy: NO" in text
    assert "Ticket: deadbeef" in text


# --------------------------------------------------------------------------- #
# OBS-381: the `authoritative` guard -- never relay a model's paraphrase as
# if it were the code-rendered finding.
# --------------------------------------------------------------------------- #


def test_a_non_authoritative_report_is_refused_not_relayed():
    report = _report()
    report["authoritative"] = False

    with pytest.raises(N.NotifierError) as exc:
        N.render_report_text(report, device="RR1", subject="10.255.0.12",
                              finding="interface_line_down")
    assert "non-authoritative" in str(exc.value)
    assert "paraphrase" in str(exc.value)


def test_a_non_authoritative_report_is_swallowed_by_notify_not_raised(monkeypatch):
    """The refusal is real (proven above) but must degrade like every other
    send failure -- `notify()` never raises, per this module's central rule."""

    report = _report()
    report["authoritative"] = False
    capture = _Capture()

    record = N.notify(report, device="RR1", subject="10.255.0.12",
                       finding="interface_line_down",
                       notifier=_telegram(monkeypatch, capture))

    assert record["ok"] is False
    assert "non-authoritative" in record["error"]
    assert capture.requests == []


def test_a_report_with_no_authoritative_key_at_all_still_renders():
    """OBS-181: the refusal test above needs a positive control, and this is
    it -- a report with NO `authoritative` key at all (every caller that
    predates the flag, and every OTHER test in this file via `_report()`,
    which sets it `True`) still renders: the check is `is False`, not
    `is not True`."""

    report = _report()
    del report["authoritative"]

    text = N.render_report_text(report, device="RR1", subject="10.255.0.12",
                                 finding="interface_line_down")
    assert "interface_line_down" in text


# --------------------------------------------------------------------------- #
# Device text and the projector -- checked, not assumed (see the module
# docstring's "Device text and the projector" section for the full reasoning
# this test pins as a deliberate, tested decision rather than a silent gap).
# --------------------------------------------------------------------------- #


def test_a_bgp_last_reset_reason_embedded_in_a_claim_renders_verbatim_by_design():
    """`checks.bgp_transport`'s `_last_reset_note` folds a device's own
    `last_reset_reason` into a rung's `reason`, which `render.render_report`
    then folds into an observation's `claim` -- there is no longer a named
    field to apply `model_egress.quote_device_text` to by the time this
    module receives it, and redacting the CONTENT of an already-composed
    claim string would make this module the redaction filter its own
    docstring refuses to be. Pinned here so this is a checked, tested
    decision, not an unexamined one -- see the module docstring."""

    report = _report()
    report["observations"].append({
        "claim": (
            "1/5 bgp_session on RR1 is broken: session to 10.255.0.12 is Idle; "
            "the device last recorded a reset 3m ago with reason "
            "'BGP Notification received: administrative shutdown' "
            "(history, not current state)"
        ),
        "evidence_key": "RR1:bgp:10.255.0.12",
    })

    text = N.render_report_text(report, device="RR1", subject="10.255.0.12",
                                 finding="interface_line_down")

    assert "administrative shutdown" in text
    assert "(history, not current state)" in text


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def test_the_default_provider_is_a_no_op(monkeypatch):
    monkeypatch.delenv(N.NOTIFIER_ENV, raising=False)
    assert isinstance(N.get_notifier(), N.NoOpNotifier)


def test_an_unknown_provider_fails_closed(monkeypatch):
    """Selection is the one thing here that raises, and deliberately.

    A send failure is swallowed because delivery is best-effort. A typo in the
    provider name is not a delivery failure -- it is a configuration that will
    silently never deliver, which is the case worth surfacing.
    """

    monkeypatch.setenv(N.NOTIFIER_ENV, "slakc")
    with pytest.raises(N.NotifierError) as exc:
        N.get_notifier()
    assert "slakc" in str(exc.value)
    assert "none" in str(exc.value) and "telegram" in str(exc.value)


def test_notify_under_the_default_provider_is_a_no_op_not_an_error(monkeypatch):
    """`--notify` must be safe in a cron entry written before a provider exists."""

    monkeypatch.delenv(N.NOTIFIER_ENV, raising=False)
    record = N.notify(_report(), device="RR1", subject="10.255.0.12",
                      finding="interface_line_down")

    assert record["ok"] is True
    assert record["attempted"] is False       # not attempted, and not a failure
    assert record["provider"] == "none"
    assert record["error"] is None


def test_notify_swallows_an_unknown_provider_too(monkeypatch):
    """`get_notifier` raises; `notify` does not. It is the investigation path."""

    monkeypatch.setenv(N.NOTIFIER_ENV, "slakc")
    record = N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x")

    assert record["ok"] is False
    assert "slakc" in record["error"]


# --------------------------------------------------------------------------- #
# Best-effort, never fatal
# --------------------------------------------------------------------------- #


def test_a_provider_that_raises_is_swallowed_and_recorded(monkeypatch):
    class Exploding(N.Notifier):
        name = "exploding"

        def send(self, report, *, subject, device, finding,
                  trustworthy=None, cause=None, ticket_id=None):
            raise RuntimeError("the channel is on fire")

    record = N.notify(_report(), device="RR1", subject="10.255.0.12",
                      finding="interface_line_down", notifier=Exploding())

    assert record["ok"] is False
    assert record["attempted"] is True
    assert "the channel is on fire" in record["error"]


def test_a_transport_failure_is_swallowed(monkeypatch):
    capture = _Capture(raises=urllib.error.URLError("no route to host"))
    record = N.notify(_report(), device="RR1", subject="10.255.0.12",
                      finding="interface_line_down",
                      notifier=_telegram(monkeypatch, capture))

    assert record["ok"] is False
    assert "no route to host" in record["error"]


# --------------------------------------------------------------------------- #
# Telegram: fail closed, and never leak the token
# --------------------------------------------------------------------------- #


def test_an_empty_chat_allowlist_sends_to_nobody(monkeypatch):
    """Empty means nobody, not everybody -- the allowlist rule, applied here."""

    capture = _Capture()
    record = N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x",
                      notifier=_telegram(monkeypatch, capture, chats=""))

    assert record["ok"] is False
    assert "refusing to send to nobody" in record["error"]
    assert capture.requests == []


def test_a_missing_token_refuses_rather_than_posting_anonymously(monkeypatch):
    capture = _Capture()
    record = N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x",
                      notifier=_telegram(monkeypatch, capture, token=""))

    assert record["ok"] is False
    assert capture.requests == []


def test_every_chat_in_the_allowlist_gets_the_message(monkeypatch):
    capture = _Capture()
    record = N.notify(_report(), device="RR1", subject="10.255.0.12",
                      finding="interface_line_down",
                      notifier=_telegram(monkeypatch, capture, chats="11, 22 ,33"))

    assert record["ok"] is True
    assert len(capture.requests) == 3
    sent = [json.loads(r.data)["chat_id"] for r in capture.requests]
    assert sent == ["11", "22", "33"]


def test_edit_text_targets_one_allowlisted_card(monkeypatch):
    capture = _Capture()
    capture.read = lambda: b'{"ok": true, "result": {"message_id": 91}}'
    notifier = _telegram(monkeypatch, capture, chats="11,22")

    returned = notifier.edit_text(chat_id="22", message_id=91, text="incident card")

    request = capture.requests[0]
    assert request.full_url.endswith("/editMessageText")
    assert json.loads(request.data) == {"chat_id": "22", "message_id": 91, "text": "incident card"}
    assert returned == 91


def test_edit_text_refuses_a_destination_outside_the_allowlist(monkeypatch):
    capture = _Capture()
    notifier = _telegram(monkeypatch, capture, chats="11")

    with pytest.raises(N.NotifierError, match="allowlist"):
        notifier.edit_text(chat_id="22", message_id=91, text="incident card")

    assert capture.requests == []


def test_the_token_never_appears_in_the_returned_error(monkeypatch):
    """Telegram puts the token in the URL, so urllib puts it in the exception.

    That is the leak this guards. The token reaches the wire and nothing else.
    """

    token = "1234567:AAH-super-secret-value"
    capture = _Capture(raises=urllib.error.URLError(
        f"<urlopen error [Errno -2] Name or service not known> "
        f"for https://api.telegram.org/bot{token}/sendMessage"
    ))
    record = N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x",
                      notifier=_telegram(monkeypatch, capture, token=token))

    assert record["ok"] is False
    assert token not in record["error"]
    assert token not in json.dumps(record)
    assert "[REDACTED]" in record["error"]


def test_telegram_429_exposes_retry_after_to_the_durable_delivery_layer(monkeypatch):
    response = BytesIO(b'{"ok":false,"parameters":{"retry_after":17}}')
    error = urllib.error.HTTPError("https://example.invalid", 429, "rate limited", {}, response)
    notifier = _telegram(monkeypatch, _Capture(raises=error))

    with pytest.raises(N.NotifierError, match="HTTP 429") as raised:
        notifier.send_text("bounded lifecycle message")

    assert raised.value.retry_after_seconds == 17.0


def test_a_report_over_the_limit_is_refused_rather_than_truncated(monkeypatch):
    """A truncated RCA is a different claim from the one that was grounded.

    And it arrives looking complete, which is worse than not arriving.
    """

    report = _report()
    report["observations"] = [
        {"claim": f"{i}/400 a very long claim " + "x" * 40, "evidence_key": "k"}
        for i in range(400)
    ]
    capture = _Capture()
    record = N.notify(report, device="RR1", subject="10.255.0.12", finding="x",
                      notifier=_telegram(monkeypatch, capture))

    assert record["ok"] is False
    assert "refusing to send rather than truncating" in record["error"]
    assert capture.requests == []


def test_the_message_carries_the_chain_not_only_the_finding(monkeypatch):
    """A report without its rungs has thrown away what makes it trustworthy.

    `prompts/README.md` makes this a rule for the report; it holds for the
    relay too, and for the same reason -- the chain is the product.
    """

    capture = _Capture()
    N.notify(_report(), device="RR1", subject="10.255.0.12",
             finding="interface_line_down",
             notifier=_telegram(monkeypatch, capture))

    text = json.loads(capture.requests[0].data)["text"]
    assert "interface_line_down" in text
    assert "1/5 bgp_session" in text
    assert "5/5 interface" in text
    assert "requires human judgement" in text


def test_the_configured_timeout_reaches_the_transport(monkeypatch):
    monkeypatch.setenv(N.TIMEOUT_ENV, "3.5")
    capture = _Capture()
    N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x",
             notifier=_telegram(monkeypatch, capture))
    assert capture.timeout == 3.5


# --------------------------------------------------------------------------- #
# The CLI contract: a broken channel cannot change what the investigation said
# --------------------------------------------------------------------------- #


def test_a_broken_notifier_does_not_change_the_investigation_exit_code(monkeypatch, capsys):
    """The `NETTOOLS_LOG` contract, applied to delivery.

    An investigation that found the fault and failed to post about it has
    still found the fault. This is the whole reason `notify` cannot raise, and
    testing it at the unit level would not prove it -- the risk is a caller
    that wraps the call badly, so the check belongs at the CLI boundary.
    """

    from agent_nettools import cli

    monkeypatch.setenv(N.NOTIFIER_ENV, "telegram")
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    monkeypatch.delenv(N.TELEGRAM_CHAT_ENV, raising=False)   # fails closed

    _argv(monkeypatch, cli, "--notify")
    code = cli.main()

    # `broken` is a real fault on the path: exit 1, exactly as without --notify.
    assert code == 1
    captured = capsys.readouterr()
    # Notes go to stderr so stdout stays parseable JSON -- a delivery failure
    # must not corrupt the machine-readable result either.
    assert "Notification failed" in captured.err
    assert "SECRET-TOKEN-123" not in captured.err
    assert "SECRET-TOKEN-123" not in captured.out


def test_notify_is_a_no_op_under_the_default_provider_at_the_cli(monkeypatch, capsys):
    from agent_nettools import cli

    monkeypatch.delenv(N.NOTIFIER_ENV, raising=False)
    _argv(monkeypatch, cli, "--notify")
    code = cli.main()

    assert code == 1
    assert "did nothing" in capsys.readouterr().err


def test_the_cli_hands_the_notifier_the_report_and_nothing_else(monkeypatch):
    """End to end: what actually reaches `send` on a real fixture run.

    The signature test proves a bundle cannot be passed. This proves the CLI
    does not pass one anyway by stuffing it into `report`.
    """

    from agent_nettools import cli

    seen = {}

    class Recording(N.Notifier):
        name = "recording"

        def send(self, report, *, subject, device, finding,
                  trustworthy=None, cause=None, ticket_id=None):
            seen["report"] = report
            seen["finding"] = finding

    monkeypatch.setitem(N._NOTIFIERS, "recording", Recording)
    monkeypatch.setenv(N.NOTIFIER_ENV, "recording")

    _argv(monkeypatch, cli, "--notify", "--quiet")
    cli.main()

    assert seen["finding"] == "interface_line_down"
    report = seen["report"]
    assert set(report) <= {
        "authoritative", "generated_by", "finding", "rungs_examined",
        "observations", "interpretations", "recommendation",
    }
    # The evidence bundle's own keys, absent by construction.
    for leaked in ("commands", "parsed", "sections", "evidence", "data"):
        assert leaked not in report


# --------------------------------------------------------------------------- #
# Silence (B-483): delivery is a deliberate no-op, and it says so plainly
# --------------------------------------------------------------------------- #


def _notice(id="maint-1", reason="planned migration", created_by="rami",
           expires_at="2026-08-20T00:00:00+00:00") -> N.SilenceNotice:
    return N.SilenceNotice(id=id, reason=reason, created_by=created_by, expires_at=expires_at)


def test_a_silenced_notify_never_attempts_delivery(monkeypatch):
    capture = _Capture()
    record = N.notify(_report(), device="RR1", subject="10.255.0.12",
                      finding="interface_line_down",
                      notifier=_telegram(monkeypatch, capture), silence=_notice())

    assert record["ok"] is True
    assert record["attempted"] is False
    assert capture.requests == []


def test_a_silenced_record_says_so_plainly_not_the_ambiguous_did_nothing(monkeypatch):
    """The record must be distinguishable from the default-provider no-op --
    a caller printing a note needs to say WHY nothing was sent."""

    record = N.notify(_report(), device="RR1", subject="10.255.0.12",
                      finding="x", silence=_notice(reason="planned migration"))

    assert record["silenced"] is True
    assert record["silence"]["reason"] == "planned migration"
    assert record["silence"]["id"] == "maint-1"
    assert record["silence"]["created_by"] == "rami"

    unsilenced = N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x")
    assert unsilenced["silenced"] is False
    assert unsilenced["silence"] is None
    assert unsilenced != record


def test_silence_takes_effect_even_under_a_real_provider_that_would_otherwise_send(monkeypatch):
    """Silenced beats everything else -- even a fully configured, healthy
    provider must not be called."""

    capture = _Capture()
    telegram = _telegram(monkeypatch, capture)
    record = N.notify(_report(), device="RR1", subject="10.255.0.12", finding="x",
                      notifier=telegram, silence=_notice())
    assert record["provider"] is None  # never even resolved -- delivery was never approached
    assert capture.requests == []


def test_the_notifier_still_cannot_receive_an_evidence_bundle_via_silence():
    """SilenceNotice's own shape is the guard: it has exactly four string
    fields, none of which is a place a report/evidence bundle could hide."""

    import dataclasses

    fields = {f.name for f in dataclasses.fields(N.SilenceNotice)}
    assert fields == {"id", "reason", "created_by", "expires_at"}


# --------------------------------------------------------------------------- #
# Ownership routing (B-484): notify_owner executes a decision, never makes one
# --------------------------------------------------------------------------- #


def test_notify_owner_with_the_default_channel_defers_to_notify_unchanged(monkeypatch):
    monkeypatch.delenv(N.NOTIFIER_ENV, raising=False)
    owner = O.Owner(name="noc", channel=O.DEFAULT_CHANNEL)

    record = N.notify_owner(_report(), owner, device="RR1", subject="10.255.0.12",
                            finding="x")

    assert record["provider"] == "none"
    assert record["owner"] == "noc"


def test_notify_owner_routes_telegram_channel_to_its_own_destination(monkeypatch):
    capture = _Capture()
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    # No TELEGRAM_CHAT_ID at all -- the owner's own channel must be used
    # instead, never falling back to (or requiring) the env-wide allowlist.
    monkeypatch.delenv(N.TELEGRAM_CHAT_ENV, raising=False)
    monkeypatch.setattr(N.urllib.request, "urlopen", capture)
    owner = O.Owner(name="net-eng", channel="telegram:999")

    record = N.notify_owner(_report(), owner, device="RR1", subject="10.255.0.12",
                            finding="interface_line_down")

    assert record["ok"] is True
    assert record["owner"] == "net-eng"
    assert len(capture.requests) == 1
    assert json.loads(capture.requests[0].data)["chat_id"] == "999"


def test_notify_owner_reports_an_unimplemented_channel_as_a_delivery_error_not_a_raise():
    owner = O.Owner(name="pager", channel="pagerduty:abc123")
    record = N.notify_owner(_report(), owner, device="RR1", subject="10.255.0.12",
                            finding="x")
    assert record["ok"] is False
    assert "pagerduty:abc123" in record["error"]
    assert record["owner"] == "pager"


def test_notify_owner_silenced_never_attempts_delivery_to_any_channel(monkeypatch):
    capture = _Capture()
    monkeypatch.setattr(N.urllib.request, "urlopen", capture)
    owner = O.Owner(name="net-eng", channel="telegram:999")

    record = N.notify_owner(_report(), owner, device="RR1", subject="10.255.0.12",
                            finding="x", silence=_notice())

    assert record["silenced"] is True
    assert record["ok"] is True
    assert capture.requests == []


def test_resolve_ownership_then_notify_owner_end_to_end(monkeypatch):
    """The whole B-484 loop: a table routes a finding to an owner, and that
    owner's own channel actually receives it -- proving the pieces this
    change built (ownership.py's decision, notifier.py's execution) connect,
    without touching cli.py (owned elsewhere this session)."""

    capture = _Capture()
    monkeypatch.setenv(N.TELEGRAM_TOKEN_ENV, "SECRET-TOKEN-123")
    monkeypatch.setattr(N.urllib.request, "urlopen", capture)

    table = O.OwnershipTable(
        rules=(O.OwnershipRule(name="edge", owner="noc", role="edge"),),
        owners={
            "noc": O.Owner(name="noc", channel="telegram:555"),
            "net-eng": O.Owner(name="net-eng", channel="telegram:111"),
        },
        default_owner="net-eng",
    )

    routing = O.resolve_ownership(table, device="PE2", role="edge", finding="bgp_session_down")
    assert routing.matched_rule == "edge"

    records = [
        N.notify_owner(_report(), owner, device="PE2", subject="10.255.0.12",
                       finding="bgp_session_down")
        for owner in routing.owners
    ]
    assert len(records) == 1
    assert records[0]["ok"] is True
    assert json.loads(capture.requests[0].data)["chat_id"] == "555"


# --------------------------------------------------------------------------- #
# B-209b: `relay_policy.relay()` wired into the CLI's `--notify` handling.
#
# `relay()` shipped in B-209 fully tested against itself (`test_relay_policy.
# py`); the risk this section actually guards against is the WIRING -- did
# `cli.py`'s `_cmd_investigate` call it with the right arguments, and did it
# turn `relay()`'s (differently-shaped) record into the same notes `--notify`
# always printed. `relay_policy.relay`'s own discrimination (a silence for
# the wrong device does not match, a changed signature is never suppressed,
# ...) is already pinned in `test_relay_policy.py` and is not re-proven here.
# --------------------------------------------------------------------------- #


class _Recording(N.Notifier):
    """A notifier that records every keyword `notify()`/`notify_owner()` pass
    it, and never talks to a network. Registered under `_NOTIFIERS` per test
    (`monkeypatch.setitem`) rather than module scope, matching `test_the_cli_
    hands_the_notifier_the_report_and_nothing_else` above."""

    name = "recording"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send(self, report, *, subject, device, finding,
              trustworthy=None, cause=None, ticket_id=None) -> None:
        self.calls.append({
            "report": report, "subject": subject, "device": device,
            "finding": finding, "trustworthy": trustworthy, "cause": cause,
            "ticket_id": ticket_id,
        })


def _no_relay_hardening_configured(monkeypatch) -> None:
    """The `.env`-default state every B-209b default-behaviour test starts
    from: no de-dup state file, no ownership table, no silence file."""

    monkeypatch.delenv(R.NETTOOLS_RELAY_STATE_FILE_ENV, raising=False)
    monkeypatch.delenv(R.NETTOOLS_OWNERSHIP_FILE_ENV, raising=False)
    monkeypatch.delenv(H.NETTOOLS_SILENCE_FILE_ENV, raising=False)


def test_note_relay_outcome_matches_pre_b209b_notify_wording_for_all_three_branches(capsys):
    """`relay_policy.relay()`'s top-level record (`"decision"`/`"deliveries"`)
    has a different shape from `notifier.notify()`'s own
    (`"attempted"`/`"provider"`/`"ok"`/`"error"`) -- this pins that
    `cli._note_relay_outcome`, the function that bridges the two, still
    prints the exact three literal notes the pre-B-209b direct `notify()`
    call produced, for the single-delivery case every default configuration
    produces. Isolated from the full CLI/investigation machinery so the
    three branches are each exercised directly rather than depending on a
    fixture producing the right finding to hit all three."""

    from agent_nettools import cli

    args = SimpleNamespace(quiet=False)

    cli._note_relay_outcome(
        {"decision": "failed", "deliveries": [
            {"attempted": True, "provider": "telegram", "ok": False,
             "error": "boom", "owner": "default"},
        ]},
        args,
    )
    assert capsys.readouterr().err == "# Notification failed (telegram): boom\n"

    cli._note_relay_outcome(
        {"decision": "sent", "deliveries": [
            {"attempted": True, "provider": "telegram", "ok": True,
             "error": None, "owner": "default"},
        ]},
        args,
    )
    assert capsys.readouterr().err == "# Notification sent via telegram.\n"

    cli._note_relay_outcome(
        {"decision": "no_provider", "deliveries": [
            {"attempted": False, "provider": "none", "ok": True,
             "error": None, "owner": "default"},
        ]},
        args,
    )
    assert capsys.readouterr().err == "# NETTOOLS_NOTIFIER is 'none'; --notify did nothing.\n"


def test_notify_default_behaviour_is_byte_identical_via_relay(monkeypatch, capsys):
    """The house rule this wiring is bound by: with `NETTOOLS_RELAY_STATE_FILE`
    unset (its actual state in `.env`), delivery through `relay_policy.relay()`
    must be byte-identical to the pre-B-209b direct `notifier.notify()` call --
    not merely similar. `relay()` resolves to the single default-channel owner
    with no ownership table configured, and `notify_owner` on that channel
    defers to `notify()` unchanged (see its own docstring) -- this is the
    end-to-end proof that fallback chain actually lands on the exact same
    note the CLI printed before this file existed."""

    from agent_nettools import cli

    _no_relay_hardening_configured(monkeypatch)
    monkeypatch.setitem(N._NOTIFIERS, _Recording.name, _Recording)
    monkeypatch.setenv(N.NOTIFIER_ENV, _Recording.name)

    _argv(monkeypatch, cli, "--notify")
    code = cli.main()

    assert code == 1  # `broken`: a real fault on the path, unaffected by --notify
    assert capsys.readouterr().err.splitlines()[-1] == "# Notification sent via recording."


def test_the_cli_now_threads_trustworthy_cause_and_ticket_id_through_relay(monkeypatch):
    """Before this wiring, `_cmd_investigate`'s `--notify` handling called
    `notify()` with only `report`/`device`/`subject`/`finding` -- `notify()`
    had accepted `trustworthy`/`cause`/`ticket_id` since B-681, but nothing
    in the shipped CLI path ever supplied them (`notifier.py`'s own docstring,
    "Wiring a caller to pass them is explicitly out of scope for this
    change", names this exact gap). This is the test that closes it: all
    three now reach the notifier's `send()`, taken from the same descent
    result and ticket the JSON payload/ticket file already record."""

    from agent_nettools import cli

    _no_relay_hardening_configured(monkeypatch)
    recorder = _Recording()
    monkeypatch.setitem(N._NOTIFIERS, _Recording.name, lambda: recorder)
    monkeypatch.setenv(N.NOTIFIER_ENV, _Recording.name)

    _argv(monkeypatch, cli, "--notify", "--quiet")
    cli.main()

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    # The `broken` fixture's own known answer (also `_report()`'s shape at the
    # top of this file): a real, trustworthy finding with a resolved cause.
    assert call["trustworthy"] is True
    assert call["cause"] == {
        "rung": "interface", "device": "PE2",
        "reason": "1 of 3 members healthy (all required)",
    }
    assert isinstance(call["ticket_id"], str) and call["ticket_id"]


def test_a_silenced_finding_is_noted_and_never_reaches_the_notifier(monkeypatch, tmp_path, capsys):
    """B-209b surfaces two decisions `notify()` alone never produced --
    `relay()` can decide not to attempt delivery at all. This is the first:
    an operator-declared silence, read from `NETTOOLS_SILENCE_FILE` exactly
    as `nettools health --silence-file` already does (`relay_policy.py`'s own
    `_resolve_silences`)."""

    from agent_nettools import cli

    silence_path = tmp_path / "silences.yaml"
    silence_path.write_text(
        "- device: RR1\n"
        "  rule: interface_line_down\n"
        "  reason: planned migration\n"
        "  created_by: rami\n"
        "  expires_at: '2030-01-01T00:00:00+00:00'\n"
    )

    _no_relay_hardening_configured(monkeypatch)
    monkeypatch.setenv(H.NETTOOLS_SILENCE_FILE_ENV, str(silence_path))
    recorder = _Recording()
    monkeypatch.setitem(N._NOTIFIERS, _Recording.name, lambda: recorder)
    monkeypatch.setenv(N.NOTIFIER_ENV, _Recording.name)

    _argv(monkeypatch, cli, "--notify")
    cli.main()

    assert recorder.calls == []
    err = capsys.readouterr().err
    assert "# Notification silenced (planned migration, by rami)" in err


def test_positive_control_a_silence_for_a_different_device_still_sends(monkeypatch, tmp_path, capsys):
    """OBS-181: the silenced-note test above must not pass because `relay()`
    silences everything once a silence file is merely configured -- this is
    the identical file with a device that does not match, and delivery must
    proceed."""

    from agent_nettools import cli

    silence_path = tmp_path / "silences.yaml"
    silence_path.write_text(
        "- device: PE9\n"  # not RR1
        "  rule: interface_line_down\n"
        "  reason: planned migration\n"
        "  created_by: rami\n"
        "  expires_at: '2030-01-01T00:00:00+00:00'\n"
    )

    _no_relay_hardening_configured(monkeypatch)
    monkeypatch.setenv(H.NETTOOLS_SILENCE_FILE_ENV, str(silence_path))
    recorder = _Recording()
    monkeypatch.setitem(N._NOTIFIERS, _Recording.name, lambda: recorder)
    monkeypatch.setenv(N.NOTIFIER_ENV, _Recording.name)

    _argv(monkeypatch, cli, "--notify")
    cli.main()

    assert len(recorder.calls) == 1
    assert capsys.readouterr().err.splitlines()[-1] == "# Notification sent via recording."


def test_a_suppressed_finding_is_noted_and_never_reaches_the_notifier(monkeypatch, tmp_path, capsys):
    """The second decision unique to `relay()`: an unchanged (device, subject,
    finding, cause.rung) signature notified again inside the de-dup window --
    pre-seeded here so the very first `--notify` call in the test already
    lands inside it."""

    from agent_nettools import cli

    state_path = tmp_path / "relay-state.json"
    state_path.write_text(json.dumps({
        "RR1\x1f10.255.0.12": {
            "signature": ["interface_line_down", "interface"],
            "last_notified_at": datetime.now(timezone.utc).isoformat(),
            "notify_count": 1, "device": "RR1", "subject": "10.255.0.12",
        },
    }), encoding="utf-8")

    monkeypatch.delenv(R.NETTOOLS_OWNERSHIP_FILE_ENV, raising=False)
    monkeypatch.setenv(R.NETTOOLS_RELAY_STATE_FILE_ENV, str(state_path))
    recorder = _Recording()
    monkeypatch.setitem(N._NOTIFIERS, _Recording.name, lambda: recorder)
    monkeypatch.setenv(N.NOTIFIER_ENV, _Recording.name)

    _argv(monkeypatch, cli, "--notify")
    cli.main()

    assert recorder.calls == []
    assert "# Notification suppressed (de-dup):" in capsys.readouterr().err


def test_positive_control_a_changed_cause_is_never_suppressed_at_the_cli(monkeypatch, tmp_path, capsys):
    """OBS-181: the suppressed-note test above must not pass because `relay()`
    suppresses on the mere presence of a state file -- a DIFFERENT prior
    signature for the same (device, subject) must still send, exactly as
    `relay_policy.py`'s own de-dup rule states (an update is never
    suppressed, regardless of the window)."""

    from agent_nettools import cli

    state_path = tmp_path / "relay-state.json"
    state_path.write_text(json.dumps({
        "RR1\x1f10.255.0.12": {
            "signature": ["bgp_session_down", "bgp_session"],  # not this run's signature
            "last_notified_at": datetime.now(timezone.utc).isoformat(),
            "notify_count": 1, "device": "RR1", "subject": "10.255.0.12",
        },
    }), encoding="utf-8")

    monkeypatch.delenv(R.NETTOOLS_OWNERSHIP_FILE_ENV, raising=False)
    monkeypatch.setenv(R.NETTOOLS_RELAY_STATE_FILE_ENV, str(state_path))
    recorder = _Recording()
    monkeypatch.setitem(N._NOTIFIERS, _Recording.name, lambda: recorder)
    monkeypatch.setenv(N.NOTIFIER_ENV, _Recording.name)

    _argv(monkeypatch, cli, "--notify")
    cli.main()

    assert len(recorder.calls) == 1
    assert capsys.readouterr().err.splitlines()[-1] == "# Notification sent via recording."
