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

Silence (B-483) -- narrower here than in ``health.py``
--------------------------------------------------------
``health.py``'s rule for a silenced finding is "reported differently, never
vanished" -- a report keeps every finding, tagged. This module's rule is
different, and deliberately narrower, because paging is a different kind of
artifact from a report: there is exactly one channel per call, and the whole
job of :func:`notify` is deciding whether to use it. When the caller passes
``silence=`` (a :class:`SilenceNotice`, built from a `health.Silence` that
matched), :func:`notify` **does not attempt delivery** -- paging during a
window the operator already knows about is precisely the "screaming that is
correct and useless" B-483 exists to stop. The returned record says so
plainly (``"silenced": True``, with the reason/who/until alongside it)
rather than looking like the default-provider no-op, so a caller printing a
note (as the CLI does, on stderr) can say "silenced (<reason>), not sent"
instead of the ambiguous "did nothing".

Ownership routing (B-484)
----------------------------
:func:`notify_owner` executes a routing *decision* ``ownership.py`` already
made -- it never makes one. ``owner.channel``'s sentinel
(``ownership.DEFAULT_CHANNEL``) means "unchanged": resolve the provider from
``NETTOOLS_NOTIFIER``/env exactly as :func:`notify` already does, so a table
that only declares the default owner behaves identically to having no table
at all. Any other channel is ``"<provider>:<destination>"`` --
``TelegramNotifier``'s ``chat_ids=`` constructor override is what makes a
per-owner destination possible without a per-owner environment variable, and
that override defaults to ``None`` (fall back to `TELEGRAM_CHAT_ID`) so
nothing about :func:`notify`'s existing behaviour changes for a caller that
never touches ownership at all.
The RCA, not just the alarm (B-681)
------------------------------------
The operator's own scenario is *event > notification > investigation > rca >
notification* -- the SECOND notification, after an investigation ran, is
supposed to carry what the deterministic layer determined, not merely that
something happened. A finding alone sends the operator straight to LM Studio
to re-learn what `nettools investigate` already worked out. So
:func:`render_report_text`/:func:`notify` now accept three optional,
additively-added fields alongside the original ``report``/``device``/
``subject``/``finding``: ``trustworthy`` (bool), ``cause`` (a dict shaped
exactly like ``InvestigationResult.to_payload()["cause"]`` --
``{"rung", "device", "reason"}``, or ``None`` when nothing was localised),
and ``ticket_id`` (the ticket's ``run_id`` -- the exact string
`mcp_server.server.read_lab_ticket` takes, so a Telegram message already
carries what an operator needs to pick the investigation back up). All three
default to ``None`` and are rendered only when given, matching this module's
existing "omit rather than default" rule -- a caller that does not pass them
gets byte-identical output to before this change.

**Wiring a caller to pass them is explicitly out of scope for this change.**
The one caller today (`cli.py`'s ``--notify`` handling) is another agent's
owned file this session; extending `notify()`'s signature is this module's
job, connecting `cli.py` to it is not. `payload["cause"]`,
`result.trustworthy`, and the ticket's `run_id` (already in hand at the
`_record_in_ticket` call, right above the existing `notify` call) are all
already available at that call site for whoever wires it next.

The `authoritative` guard (OBS-381)
--------------------------------------
`render.render_report()`'s own docstring: a model's non-authoritative
paraphrase is marked ``"authoritative": False`` specifically so a consumer
can tell it apart from the code-rendered, invariant-respecting report this
module exists to relay. Nothing before this change ever checked that flag
here -- this module predates the convention (T-035 shipped before
`render.py`'s authoritative/paraphrase split existed), so a caller that
mixed up ``payload["report"]["content"]`` (safe -- generated by code from
typed fields, B-439) with ``payload["report"]["paraphrase"]`` (a model's own
prose, sharing the exact same ``observations``/``interpretations``/
``recommendation`` schema by design, per `render.py`'s own docstring) would
have had it relayed to Telegram as if it were the authoritative finding,
with nothing here to notice. `render_report_text` now raises
:class:`NotifierError` when ``report.get("authoritative") is False`` --
caught by `notify`'s existing best-effort handling, so this degrades exactly
like any other send failure rather than crashing an investigation. A report
with no ``authoritative`` key at all (every existing caller and fixture)
still renders: the check is ``is False``, not ``is not True``, so a caller
that has simply never set the flag is not newly refused.

Device text and the projector -- checked, not assumed
------------------------------------------------------------
Checked directly: can a ``report`` dict this module is handed carry
device-authored free text at all, given it never receives the evidence
bundle? Yes, on one narrow, reviewed path. `checks.bgp_transport`'s
``_last_reset_note`` helper appends the device's own ``last_reset_reason``
(a BGP peer's Notification text, e.g. ``'administrative shutdown'``) to a
rung's ``reason`` string -- *"appended to the reason, marked as history"*,
per that function's own docstring (B-430/OBS-092) -- which
`render.render_report()` then folds into an ``observations``/
``interpretations`` entry's ``claim`` prose alongside code-authored text.
That fragment reaches this module unprojected: `model_egress.
quote_device_text` operates on a named field within a structured record
(`FREE_TEXT_FIELDS`'s ``(context, field)`` table), and there is no such
field left to quote once `render_report()` has already composed it into one
sentence -- the device fragment and the code's own words are, by that point,
the same string.

**Left as is, deliberately, not silently.** Three reasons converge: (1) it
is never the primary claim -- `_last_reset_note`'s own docstring is explicit
that a reset reason "qualifies the finding; it never becomes one", so nothing
here can be spoofed into naming a false cause via this fragment; (2) it is
always historical and always `repr()`-quoted (visibly a quoted device string
to a human reader, never bare prose); (3) redacting or re-marking it here
would require this module to parse and rewrite the *content* of a `claim`
string -- exactly the "redaction filter" this module's own docstring already
refuses to be, and undoing the fix `_last_reset_note` exists for (OBS-092: a
diagnostician logging into a far device to learn what the near device had
already reported). Pinned by
`tests/test_notifier.py::test_a_bgp_last_reset_reason_embedded_in_a_claim_
renders_verbatim_by_design` so this is a visible, tested decision rather than
an unexamined gap. Invariant 4 itself is not implicated either way: nothing
downstream of this module is a model -- Telegram has no model reading this
text, and if that ever changes, whatever adds a model-reading consumer of a
notification inherits the same "a new path to a model needs the guarantee
built into it" rule CLAUDE.md already states for MCP.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Sequence

from ._env import _float_env, _int_env
from .ownership import DEFAULT_CHANNEL, Owner

__all__ = [
    "DEFAULT_NOTIFIER",
    "DEFAULT_TIMEOUT_SECONDS",
    "NOTIFIER_ENV",
    "NotifierError",
    "Notifier",
    "NoOpNotifier",
    "SilenceNotice",
    "TelegramNotifier",
    "get_notifier",
    "known_notifiers",
    "notify",
    "notify_owner",
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

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _telegram_retry_after(exc: urllib.error.HTTPError) -> float | None:
    """Extract Telegram's 429 retry hint without exposing response content."""

    if exc.code != 429:
        return None
    try:
        parsed = json.loads(exc.read().decode("utf-8", errors="replace"))
        value = parsed.get("parameters", {}).get("retry_after")
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return None


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


# --------------------------------------------------------------------------- #
# Rendering: report object -> readable text
# --------------------------------------------------------------------------- #


def render_report_text(
    report: dict,
    *,
    device: str,
    subject: str,
    finding: str,
    trustworthy: bool | None = None,
    cause: dict | None = None,
    ticket_id: str | None = None,
) -> str:
    """One readable message from the report object.

    Reads only fields the authoritative report is *generated* from, so this
    cannot render something the descent did not produce. Anything absent is
    omitted rather than defaulted -- a missing recommendation is not the same
    as an empty one, and a message saying nothing is better than one implying
    something was checked.

    ``trustworthy``/``cause``/``ticket_id`` are the B-681 additions (see the
    module docstring's "The RCA, not just the alarm" section): all three
    default to ``None`` and, when omitted, change nothing about the output --
    this is additive, not a rewrite of the existing contract.

    Raises :class:`NotifierError` when ``report.get("authoritative") is
    False`` -- see the module docstring's "The `authoritative` guard"
    section. Safe to call from a `Notifier.send` implementation without a
    surrounding `try`: `notify()` already wraps every `send()` call in a
    broad `except Exception`.
    """

    if report.get("authoritative") is False:
        raise NotifierError(
            "refusing to relay a non-authoritative report: this dict's "
            "'authoritative' field is explicitly False, which means it is a "
            "MODEL's paraphrase (render.py/investigation.py mark it this "
            "way), not the code-rendered finding this module exists to "
            "relay -- pass payload['report']['content'], never "
            "payload['report']['paraphrase']"
        )

    lines = [f"{finding} — {device} → {subject}"]

    if isinstance(cause, dict) and cause.get("rung"):
        lines.append(f"Cause: {cause['rung']} on {cause.get('device', device)}")

    if trustworthy is not None:
        lines.append(
            "Trustworthy: yes" if trustworthy
            else "Trustworthy: NO — verify before acting on this"
        )

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

    if ticket_id:
        lines.append("")
        lines.append(f"Ticket: {ticket_id}")

    return "\n".join(lines)


@dataclass(frozen=True)
class SilenceNotice:
    """What :func:`notify` needs to know a finding is silenced, and nothing else.

    Deliberately not `health.Silence` itself and not a bare dict: a
    purpose-built shape holding exactly these four fields is the same
    reasoning ``test_the_notifier_cannot_receive_an_evidence_bundle`` (T-035)
    already applies to :func:`notify`'s own signature -- what this module can
    receive is bounded by what the type can hold, not by a filter someone has
    to remember to apply. `health.find_silence` returns the fuller `Silence`
    (which also carries `device`/`rule`/`subject` -- the matching criteria);
    a caller wiring this in constructs a `SilenceNotice` from the matched
    `Silence` once matching has already happened, so this module never needs
    to know how a silence was matched, only that one was.
    """

    id: str
    reason: str
    created_by: str
    expires_at: str  # already-rendered text (Silence.as_dict()'s own ISO string)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #


class Notifier(ABC):
    """Send one report somewhere. The report object only."""

    @abstractmethod
    def send(
        self,
        report: dict,
        *,
        subject: str,
        device: str,
        finding: str,
        trustworthy: bool | None = None,
        cause: dict | None = None,
        ticket_id: str | None = None,
    ) -> list[int] | None:
        """Deliver, or raise :class:`NotifierError`.

        Raising is correct here and is swallowed one level up, in
        :func:`notify`. A provider that swallowed its own failures would leave
        nothing to record.

        ``trustworthy``/``cause``/``ticket_id`` are the B-681 additions --
        see :func:`render_report_text`'s docstring. Every implementation
        accepts them (even `NoOpNotifier`, which ignores everything) so
        `notify()` can pass them uniformly regardless of which provider is
        configured.
        """


class NoOpNotifier(Notifier):
    """The default. Delivers nothing and succeeds.

    Not a stub: it is what makes ``--notify`` safe in a cron entry written
    before a provider is configured. `NETTOOLS_NOTIFIER=none` with `--notify`
    is a no-op rather than an error, deliberately.
    """

    name = "none"

    def send(
        self,
        report: dict,
        *,
        subject: str,
        device: str,
        finding: str,
        trustworthy: bool | None = None,
        cause: dict | None = None,
        ticket_id: str | None = None,
    ) -> list[int] | None:
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

    def __init__(self, *, opener=None, chat_ids: Sequence[str] | None = None) -> None:
        # `opener` exists so tests drive this without a network and without
        # monkeypatching urllib globally. Production leaves it None.
        self._opener = opener or urllib.request.urlopen
        # `chat_ids` (B-484): an explicit destination override, for
        # `notify_owner` routing one owner's message to that owner's own
        # chat(s) without needing a dedicated environment variable per owner.
        # `None` (the default) falls back to `TELEGRAM_CHAT_ID` exactly as
        # before -- nothing about direct `TelegramNotifier()` construction
        # changes.
        self._chat_ids_override = list(chat_ids) if chat_ids is not None else None

    def _chat_ids(self) -> list[str]:
        if self._chat_ids_override is not None:
            return self._chat_ids_override
        raw = os.getenv(TELEGRAM_CHAT_ENV, "")
        return [part.strip() for part in raw.split(",") if part.strip()]

    def send_text(
        self,
        text: str,
        *,
        reply_to_message_ids: Sequence[int] = (),
        require_message_ids: bool = True,
    ) -> list[int]:
        """Send bounded, code-authored lifecycle text and return message IDs.

        ``reply_to_message_ids`` follows the configured chat-id order, so an
        event lifecycle can append progress beneath one root notification in
        each configured Telegram chat without persisting a destination.
        """

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
        retry_delays: list[float] = []
        message_ids: list[int] = []
        for index, chat_id in enumerate(chat_ids):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
            if index < len(reply_to_message_ids):
                payload["reply_to_message_id"] = reply_to_message_ids[index]
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self._opener(request, timeout=timeout) as response:
                    status = getattr(response, "status", None)
                    if status is not None and not (200 <= int(status) < 300):
                        failures.append(f"chat {chat_id}: HTTP {status}")
                        continue
                    reader = getattr(response, "read", None)
                    body = reader().decode("utf-8", errors="replace") if callable(reader) else ""
                    parsed = json.loads(body) if body else {}
                    message_id = parsed.get("result", {}).get("message_id") if isinstance(parsed, dict) else None
                    if isinstance(message_id, int):
                        message_ids.append(message_id)
                    elif require_message_ids:
                        failures.append(f"chat {chat_id}: response omitted message_id")
            except urllib.error.HTTPError as exc:
                retry_after = _telegram_retry_after(exc)
                if retry_after is not None:
                    retry_delays.append(retry_after)
                failures.append(f"chat {chat_id}: HTTP {exc.code}")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                failures.append(f"chat {chat_id}: {_redact(str(exc))}")
        if failures:
            raise NotifierError(
                "; ".join(failures),
                retry_after_seconds=max(retry_delays) if retry_delays else None,
            )
        return message_ids

    def edit_text(self, *, chat_id: str, message_id: int, text: str) -> int:
        """Edit one allowlisted investigation card through ``editMessageText``.

        This deliberately takes one destination/message pair rather than
        reusing ``send_text``'s configured-chat fan-out: an incident card has a
        distinct provider receipt per chat, and editing the wrong pair would
        overwrite an unrelated incident surface.
        """

        token = os.getenv(TELEGRAM_TOKEN_ENV, "").strip()
        if not token:
            raise NotifierError(
                f"{TELEGRAM_TOKEN_ENV} is not set; the telegram notifier cannot edit"
            )
        if chat_id not in self._chat_ids():
            raise NotifierError("chat is not in the configured Telegram destination allowlist")
        if not isinstance(message_id, int) or message_id <= 0:
            raise NotifierError("Telegram message_id must be a positive integer")
        max_chars = _int_env(TELEGRAM_MAX_CHARS_ENV, DEFAULT_MAX_CHARS)
        if len(text) > max_chars:
            raise NotifierError(
                f"card renders to {len(text)} characters, over the {max_chars} limit"
            )
        request = urllib.request.Request(
            f"{self.api_base}/bot{token}/editMessageText",
            data=json.dumps({"chat_id": chat_id, "message_id": message_id, "text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=_float_env(TIMEOUT_ENV, DEFAULT_TIMEOUT_SECONDS)) as response:
                status = getattr(response, "status", None)
                if status is not None and not (200 <= int(status) < 300):
                    raise NotifierError(f"chat {chat_id}: HTTP {status}")
                reader = getattr(response, "read", None)
                body = reader().decode("utf-8", errors="replace") if callable(reader) else ""
                parsed = json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            raise NotifierError(
                f"chat {chat_id}: HTTP {exc.code}",
                retry_after_seconds=_telegram_retry_after(exc),
            ) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise NotifierError(f"chat {chat_id}: {_redact(str(exc))}") from exc
        result = parsed.get("result") if isinstance(parsed, dict) else None
        returned_id = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(returned_id, int):
            raise NotifierError(f"chat {chat_id}: edit response omitted message_id")
        return returned_id

    def send_text_to(self, *, chat_id: str, text: str) -> int:
        """Send one initial live card to one configured destination."""

        if chat_id not in self._chat_ids():
            raise NotifierError("chat is not in the configured Telegram destination allowlist")
        sent = TelegramNotifier(opener=self._opener, chat_ids=[chat_id]).send_text(text)
        if len(sent) != 1:
            raise NotifierError(f"chat {chat_id}: send response did not produce one message_id")
        return sent[0]

    def send_reply_text(self, *, chat_id: str, reply_to_message_id: int, text: str) -> int:
        """Send one bounded activity reply beneath a persisted incident card."""

        if chat_id not in self._chat_ids():
            raise NotifierError("chat is not in the configured Telegram destination allowlist")
        sent = TelegramNotifier(opener=self._opener, chat_ids=[chat_id]).send_text(
            text,
            reply_to_message_ids=(reply_to_message_id,),
        )
        if len(sent) != 1:
            raise NotifierError(f"chat {chat_id}: reply response did not produce one message_id")
        return sent[0]

    def send(
        self,
        report: dict,
        *,
        subject: str,
        device: str,
        finding: str,
        trustworthy: bool | None = None,
        cause: dict | None = None,
        ticket_id: str | None = None,
    ) -> list[int] | None:
        text = render_report_text(
            report, device=device, subject=subject, finding=finding,
            trustworthy=trustworthy, cause=cause, ticket_id=ticket_id,
        )
        return self.send_text(text, require_message_ids=False)


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
    silence: SilenceNotice | None = None,
    trustworthy: bool | None = None,
    cause: dict | None = None,
    ticket_id: str | None = None,
    notifier: Notifier | None = None,
) -> dict[str, Any]:
    """Deliver one report, best-effort. **Never raises.**

    This is the only function the investigation path should call. Returns a
    small record of what happened -- ``{"attempted", "provider", "ok",
    "error", "silenced", "silence"}`` -- so a caller can log the outcome
    without needing to catch anything. `error` is redacted.

    Note the signature: there is **no parameter an evidence bundle could arrive
    in**. That is the egress bound, and it is a property of this function's
    shape rather than of anyone remembering to strip something. ``silence``
    does not weaken that: see :class:`SilenceNotice`'s own docstring for why
    it cannot carry one either.

    B-483. ``silence`` (built from a `health.Silence` that matched this
    finding) makes delivery a deliberate no-op: the record reports
    ``"silenced": True`` and ``"ok": True`` without ever calling a provider's
    ``send``. See the module docstring's "Silence" section for why that is
    the right amount of suppression for a paging channel, and why it is a
    narrower rule than `health.py`'s "never vanish from the report".

    `trustworthy`/`cause`/`ticket_id` (B-681) are small, code-typed values --
    a bool, a three-field dict of code-authored strings, an id -- not a route
    for evidence; see the module docstring's "The RCA, not just the alarm"
    section for what each renders as.
    """

    record: dict[str, Any] = {
        "attempted": False, "provider": None, "ok": False, "error": None,
        "silenced": False, "silence": None, "message_ids": [],
    }
    if silence is not None:
        record["silenced"] = True
        record["silence"] = {
            "id": silence.id, "reason": silence.reason,
            "created_by": silence.created_by, "expires_at": silence.expires_at,
        }
        record["ok"] = True
        return record
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
        message_ids = target.send(
            report, subject=subject, device=device, finding=finding,
            trustworthy=trustworthy, cause=cause, ticket_id=ticket_id,
        )
        if isinstance(message_ids, list) and all(isinstance(value, int) for value in message_ids):
            record["message_ids"] = message_ids
        record["ok"] = True
    except Exception as exc:  # noqa: BLE001 -- delivery is never fatal
        record["error"] = _redact(f"{exc.__class__.__name__}: {exc}")
    return record


# --------------------------------------------------------------------------- #
# Ownership routing (B-484): execute a decision `ownership.py` already made.
# --------------------------------------------------------------------------- #


def notify_owner(
    report: dict,
    owner: Owner,
    *,
    device: str,
    subject: str,
    finding: str,
    silence: SilenceNotice | None = None,
    trustworthy: bool | None = None,
    cause: dict | None = None,
    ticket_id: str | None = None,
) -> dict[str, Any]:
    """:func:`notify`, routed to one :class:`ownership.Owner`. **Never raises.**

    Interprets ``owner.channel`` (see the module docstring's "Ownership
    routing" section): the sentinel :data:`ownership.DEFAULT_CHANNEL` defers
    to :func:`notify` unchanged; anything else must be
    ``"telegram:<chat id(s)>"`` -- the only real provider this module ships
    (see "Providers" above) -- and a channel this build cannot interpret is a
    delivery failure recorded in ``error``, exactly like a transport failure,
    never a raise and never a silent drop.

    The returned record adds ``"owner": owner.name`` to :func:`notify`'s own
    shape, so a caller fanning this out over several owners (a primary plus
    an escalation target) can tell the records apart.

    ``trustworthy``/``cause``/``ticket_id`` (B-681, B-209) are forwarded to
    :func:`notify` on every routed path -- **this closes a gap left when
    B-681 added the three fields to** :func:`notify` **but not to this
    function**: before B-209, an owner-routed message (any channel other
    than the default) silently rendered without the RCA `notify()`'s own
    unrouted callers already got, and nothing surfaced the difference --
    the exact "reads correct evidence, reports it worse" shape this
    codebase repeatedly refuses elsewhere. All three still default to
    ``None`` and are omitted when absent, so a caller that does not pass
    them (today's only caller, until B-209's wiring report is acted on)
    sees no change in output.
    """

    if silence is not None:
        return {
            "attempted": False, "provider": None, "ok": True, "error": None,
            "silenced": True,
            "silence": {
                "id": silence.id, "reason": silence.reason,
                "created_by": silence.created_by, "expires_at": silence.expires_at,
            },
            "owner": owner.name,
        }

    if owner.channel == DEFAULT_CHANNEL:
        record = notify(
            report, device=device, subject=subject, finding=finding,
            trustworthy=trustworthy, cause=cause, ticket_id=ticket_id,
        )
        record["owner"] = owner.name
        return record

    provider, _, destination = owner.channel.partition(":")
    chat_ids = [c.strip() for c in destination.split(",") if c.strip()]
    if provider != "telegram" or not chat_ids:
        return {
            "attempted": False, "provider": None, "ok": False,
            "error": (
                f"owner {owner.name!r} names channel {owner.channel!r}, which this "
                f"build cannot deliver to (only 'telegram:<chat id(s)>' and "
                f"{DEFAULT_CHANNEL!r} are implemented)"
            ),
            "silenced": False, "silence": None, "owner": owner.name,
        }

    target = TelegramNotifier(chat_ids=chat_ids)
    record = notify(
        report, device=device, subject=subject, finding=finding, notifier=target,
        trustworthy=trustworthy, cause=cause, ticket_id=ticket_id,
    )
    record["owner"] = owner.name
    return record
