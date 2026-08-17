"""Outbound report delivery. T-035, and the delivery half only.

MVP-0 has no conversation to have. The descent is deterministic and terminal:
name a device and a subject, get a report. That is a command, not a dialogue.
So this module ships the *delivery* half and there is deliberately **no inbound
surface** -- no command handler, no webhook listener, no polling loop. An
inbound path is an unauthenticated trigger surface, and this project has no
identity provider to put in front of one (`interfaces.md`, and B-301 is where
that starts).

If this module ever grows a way for a message to *cause* something, it has
become MVP-1 work and the change should stop and ask.

What may leave the estate, and why it is structural
---------------------------------------------------
**A notifier receives the report object and nothing else.** Not the evidence
bundle, not `data.commands`, not parsed records -- so raw device output and
configuration fragments cannot leak through this path by accident. That bound
is enforced by the *shape of the call*: :func:`notify` takes a
`report: dict` and there is no parameter a bundle could arrive in.

That is deliberately not a redaction filter. A filter is a list of things
someone remembered; this is a channel the evidence cannot reach. Same reasoning
as `prompt_library` (which takes a `DescentResult` rather than text) and
`mcp_server/boundary` (which rebuilds from safe parts rather than truncating),
and `tests/test_notifier.py` pins it.

Best-effort, never fatal
------------------------
A notification failure is swallowed and recorded, never raised. This follows
the `NETTOOLS_LOG` contract exactly: **a bad notifier configuration must not
turn a successful investigation into a reported failure.** An investigation
that found the fault and failed to post about it has still found the fault.

The one exception is *selection*: an unknown `NETTOOLS_NOTIFIER` value fails
closed with a structured error at `get_notifier()` time, because a typo that
silently disables delivery is the failure this whole module exists to make
visible.

Providers
---------
``none`` (the default, a no-op) and ``telegram``. Only one real provider is
implemented, per T-035 -- the residency decision (Q-007, resolved 2026-08-17)
chose Telegram, and building both would mean shipping an untested path.

``mattermost`` is the self-hosted swap and is deliberately absent rather than
stubbed. Adding it is a subclass plus one line in :data:`_NOTIFIERS`; the
report object it would receive is identical, which is the whole point of the
interface being here rather than in the Telegram class.

Credentials
-----------
`TELEGRAM_BOT_TOKEN` is a credential. It is read from the environment, sent in
the URL path Telegram's API requires, and **never appears in a log line, an
audit record, an exception message, or a repr** -- `_redact` is applied to
every string that can reach any of those, and a test asserts the token is
absent from all of them.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

__all__ = [
    "DEFAULT_NOTIFIER",
    "DEFAULT_TIMEOUT_SECONDS",
    "NOTIFIER_ENV",
    "NotifierError",
    "Notifier",
    "NoOpNotifier",
    "TelegramNotifier",
    "get_notifier",
    "known_notifiers",
    "notify",
    "render_report_text",
]

NOTIFIER_ENV = "NETTOOLS_NOTIFIER"
TIMEOUT_ENV = "NETTOOLS_NOTIFIER_TIMEOUT_SECONDS"
TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ENV = "TELEGRAM_CHAT_ID"
TELEGRAM_MAX_CHARS_ENV = "NETTOOLS_TELEGRAM_MAX_CHARS"

DEFAULT_NOTIFIER = "none"
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Telegram rejects a message over 4096 characters. The default sits under it
#: with room for the header, and a report that would exceed it is **refused
#: rather than truncated** -- a truncated RCA is a different claim from the one
#: that was grounded, and it arrives looking complete.
DEFAULT_MAX_CHARS = 3500


class NotifierError(Exception):
    """Configuration is wrong, or a send failed.

    Raised by :func:`get_notifier` for an unknown provider and by a provider's
    ``send``. **Never raised out of :func:`notify`**, which is the only entry
    point a caller in the investigation path should use.
    """


def _redact(text: str) -> str:
    """Remove the bot token from anything that might be logged or raised.

    Telegram puts the token in the URL path, so it lands in `urllib`'s
    exception strings for free. This is the reason the module never formats a
    URL into a message without passing it through here first.
    """

    token = os.getenv(TELEGRAM_TOKEN_ENV)
    if token:
        text = text.replace(token, "[REDACTED]")
    return text


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# --------------------------------------------------------------------------- #
# Rendering: report object -> readable text
# --------------------------------------------------------------------------- #


def render_report_text(report: dict, *, device: str, subject: str, finding: str) -> str:
    """One readable message from the report object.

    Reads only fields the authoritative report is *generated* from, so this
    cannot render something the descent did not produce. Anything absent is
    omitted rather than defaulted -- a missing recommendation is not the same
    as an empty one, and a message saying nothing is better than one implying
    something was checked.
    """

    lines = [f"{finding} — {device} → {subject}"]

    observations = report.get("observations")
    if isinstance(observations, list) and observations:
        lines.append("")
        for obs in observations:
            if isinstance(obs, dict) and isinstance(obs.get("claim"), str):
                lines.append(f"• {obs['claim']}")

    interpretations = report.get("interpretations")
    if isinstance(interpretations, list) and interpretations:
        lines.append("")
        for interp in interpretations:
            if isinstance(interp, dict) and isinstance(interp.get("claim"), str):
                lines.append(interp["claim"])

    recommendation = report.get("recommendation")
    if isinstance(recommendation, dict):
        nxt = recommendation.get("next_check")
        if isinstance(nxt, str) and nxt.strip():
            lines.append("")
            lines.append(f"Next: {nxt}")
        # The recommendation is the model's advice, explicitly for a human and
        # exempt from citation. Saying so in the message is not decoration --
        # it is the one part of a report a reader must not act on unchecked.
        if recommendation.get("requires_human") is True:
            lines.append("(recommendation requires human judgement)")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class Notifier(ABC):
    """Send one report somewhere. The report object only."""

    @abstractmethod
    def send(self, report: dict, *, subject: str, device: str, finding: str) -> None:
        """Deliver, or raise :class:`NotifierError`.

        Raising is correct here and is swallowed one level up, in
        :func:`notify`. A provider that swallowed its own failures would leave
        nothing to record.
        """


class NoOpNotifier(Notifier):
    """The default. Delivers nothing and succeeds.

    Not a stub: it is what makes ``--notify`` safe in a cron entry written
    before a provider is configured. `NETTOOLS_NOTIFIER=none` with `--notify`
    is a no-op rather than an error, deliberately.
    """

    name = "none"

    def send(self, report: dict, *, subject: str, device: str, finding: str) -> None:
        return None


class TelegramNotifier(Notifier):
    """Post to one or more Telegram chats over the Bot API.

    ``TELEGRAM_CHAT_ID`` accepts a **comma-separated allowlist**, and an empty
    or unset value means *send to nobody* rather than *send to everybody* --
    the same fail-closed rule `APPROVED_COMMANDS` uses, for the same reason.

    **This allowlist is delivery, not authorization.** It controls where output
    goes. It grants nothing, because there is no inbound path to grant it on.
    If one is ever added, this must not be mistaken for the thing guarding it.
    """

    name = "telegram"
    api_base = "https://api.telegram.org"

    def __init__(self, *, opener=None) -> None:
        # `opener` exists so tests drive this without a network and without
        # monkeypatching urllib globally. Production leaves it None.
        self._opener = opener or urllib.request.urlopen

    def _chat_ids(self) -> list[str]:
        raw = os.getenv(TELEGRAM_CHAT_ENV, "")
        return [part.strip() for part in raw.split(",") if part.strip()]

    def send(self, report: dict, *, subject: str, device: str, finding: str) -> None:
        token = os.getenv(TELEGRAM_TOKEN_ENV, "").strip()
        if not token:
            raise NotifierError(
                f"{TELEGRAM_TOKEN_ENV} is not set; the telegram notifier cannot send"
            )

        chat_ids = self._chat_ids()
        if not chat_ids:
            raise NotifierError(
                f"{TELEGRAM_CHAT_ENV} is empty; refusing to send to nobody rather "
                "than guessing a destination"
            )

        text = render_report_text(report, device=device, subject=subject, finding=finding)
        max_chars = _int_env(TELEGRAM_MAX_CHARS_ENV, DEFAULT_MAX_CHARS)
        if len(text) > max_chars:
            raise NotifierError(
                f"report renders to {len(text)} characters, over the "
                f"{max_chars} limit; refusing to send rather than truncating a "
                "report that was grounded as a whole"
            )

        timeout = _float_env(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)
        url = f"{self.api_base}/bot{token}/sendMessage"
        failures: list[str] = []

        for chat_id in chat_ids:
            payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self._opener(request, timeout=timeout) as response:
                    status = getattr(response, "status", None)
                    if status is not None and not (200 <= int(status) < 300):
                        failures.append(f"chat {chat_id}: HTTP {status}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                # _redact, because the token is in the URL and urllib puts the
                # URL in the exception.
                failures.append(f"chat {chat_id}: {_redact(str(exc))}")

        if failures:
            raise NotifierError("; ".join(failures))


_NOTIFIERS: dict[str, type[Notifier]] = {
    NoOpNotifier.name: NoOpNotifier,
    TelegramNotifier.name: TelegramNotifier,
}


def known_notifiers() -> tuple[str, ...]:
    return tuple(sorted(_NOTIFIERS))


def get_notifier(provider: str | None = None) -> Notifier:
    """Return the configured notifier.

    ``provider`` overrides ``NETTOOLS_NOTIFIER`` (env-then-default ``"none"``),
    the same resolution order every other env-then-default setting here uses.

    **Fails closed on an unknown name**, unlike everything else in this module.
    A send failure is swallowed because delivery is best-effort; a *typo in the
    provider name* is not a delivery failure, it is a configuration that will
    silently never deliver, and that is the case worth raising on.
    """

    name = (provider or os.getenv(NOTIFIER_ENV, DEFAULT_NOTIFIER)).strip().lower()
    try:
        notifier_cls = _NOTIFIERS[name]
    except KeyError as exc:
        raise NotifierError(
            f"Unknown {NOTIFIER_ENV}: {name!r}. "
            f"Known providers: {', '.join(known_notifiers())}."
        ) from exc
    return notifier_cls()


def notify(
    report: dict,
    *,
    device: str,
    subject: str,
    finding: str,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Deliver one report, best-effort. **Never raises.**

    This is the only function the investigation path should call. Returns a
    small record of what happened -- ``{"attempted", "provider", "ok",
    "error"}`` -- so a caller can log the outcome without needing to catch
    anything. `error` is redacted.

    Note the signature: there is **no parameter an evidence bundle could arrive
    in**. That is the egress bound, and it is a property of this function's
    shape rather than of anyone remembering to strip something.
    """

    record: dict[str, Any] = {
        "attempted": False, "provider": None, "ok": False, "error": None,
    }
    try:
        target = notifier or get_notifier()
    except NotifierError as exc:
        record["error"] = _redact(str(exc))
        return record

    record["provider"] = getattr(target, "name", type(target).__name__)
    if isinstance(target, NoOpNotifier):
        # Not attempted, and not a failure. `--notify` under the default
        # provider is a deliberate no-op, and the record says which it was.
        record["ok"] = True
        return record

    record["attempted"] = True
    try:
        target.send(report, subject=subject, device=device, finding=finding)
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 -- delivery is never fatal
        record["error"] = _redact(f"{exc.__class__.__name__}: {exc}")
    return record
