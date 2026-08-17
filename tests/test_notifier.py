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

import pytest

from agent_nettools import notifier as N

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
    """

    import inspect

    params = set(inspect.signature(N.notify).parameters)
    assert params == {"report", "device", "subject", "finding", "notifier"}

    send = set(inspect.signature(N.Notifier.send).parameters)
    assert send == {"self", "report", "subject", "device", "finding"}


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

        def send(self, report, *, subject, device, finding):
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

        def send(self, report, *, subject, device, finding):
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
