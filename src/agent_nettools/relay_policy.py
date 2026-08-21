"""Report relay hardening (B-209): threading/grouping, silence windows, de-duplication.

Row: *"Report relay hardening -- threading, per-object grouping, silence
windows. A relay that posts every event unfiltered gets muted within a
week."* T-035 (the relay itself) shipped, so this is the follow-on: connect
the machinery that already exists to the relay path, and build only the one
piece that does not exist yet.

Three modules already do most of this job -- read each one's own docstring
before touching this file, they are not restated here:

* ``health.find_silence`` -- an operator-declared maintenance window that
  must stop a page. **Reused as-is.** ``find_silence``'s own docstring
  already anticipates this exact call: ``rule`` accepts "an exact rule name
  from ``ALL_RULES`` ... or, for a caller silencing an investigate/
  descent-side finding, whatever identifier that caller uses for the same
  purpose". This module passes the descent's own ``finding`` string (e.g.
  ``"interface_line_down"``) as ``rule`` -- the same vocabulary
  ``checks.ALL_RULES`` uses, since a rung's ``finding`` and a health rule's
  name are drawn from the same predicate catalog (``checks.py``'s own
  docstring: "the rules themselves live beside the descent's predicates").
* ``ownership.resolve_ownership`` -- who gets told, as a declarative table
  lookup. **Reused as-is**, including its escalation step (``age_seconds``,
  left to a future caller exactly as ``ownership.py``'s own docstring
  already says -- this module does not fabricate it either; see "What this
  module deliberately leaves alone" below).
* ``incident_correlation`` -- **not reused, and not reimplemented.** It
  answers a different question at a different time: given many diagnoses
  already sitting in the ledger, which of them share a structural fact
  (cause, subject, device)? That is retrospective and batch-shaped -- it
  reads ``ledger.diagnoses()``-shaped rows, not a single in-flight
  notification. This module's job is the opposite shape: **one** call,
  **right now**, deciding whether **this** notification should go out. There
  is no batch of diagnoses here to correlate, and building one (a daemon
  that collects notifications and periodically clusters them the way
  ``incident_correlation`` does) is exactly the "new correlation engine"
  this change was told not to build. Where the two vocabularies overlap
  (grouping by "the same object"), this module borrows the STRUCTURE of
  ``incident_correlation``'s reasoning -- group by the object identity a
  caller already gave us (``device``, ``subject``), never by co-occurrence
  or arrival-time proximity, for the identical reason
  ``incident_correlation.EXCLUDED_CORRELATIONS`` states -- without
  importing the module, because the module's own shape (ledger rows in,
  ``Incident`` clusters out) does not fit a single live call.

What is genuinely new: de-duplication over a window
-----------------------------------------------------
Nothing in ``health.py``, ``ownership.py``, or ``incident_correlation.py``
remembers "have I already told someone about this". Silences are
operator-declared ahead of time; ownership is a stateless table lookup;
correlation reads what the ledger already recorded. None of the three
answers "the same finding on the same device/subject just fired again 90
seconds after the last time -- should this actually re-page?" -- and that
is precisely the "gets muted within a week" failure the backlog row names.
:func:`evaluate_dedup` is the one new mechanism this change adds, and it is
deliberately narrow:

* **Keyed on (device, subject)** -- the object identity :func:`relay`'s own
  caller already supplies, the same two fields ``incident_correlation``
  would call "same subject" (basis 2) if this were a batch of diagnoses
  instead of one live call.
* **Signature is (finding, cause.rung)** -- both code-typed enum-shaped
  strings, never device-authored free text (nothing here reads or compares
  a ``reason``/``claim`` string, so this cannot be defeated -- or
  falsely triggered -- by a device rewording its own log line).
* **A changed signature always sends**, regardless of the window -- an
  "update" (the module docstring's own word for it) is never suppressed.
  Only an *unchanged* repeat within the window is.
* **State is a flat JSON file**, one record per (device, subject) key, read
  and written whole -- the same "declarative file, no daemon" shape
  ``health.py``'s silence table and ``ownership.py``'s routing table already
  use, not a database and not a running process. Single-operator lab scale
  (nine devices), so read-modify-write with no lock is a stated limitation,
  not an oversight -- concurrent CLI invocations racing this file is a
  known, accepted gap (see "What this module deliberately leaves alone").

Fail-closed, and which way closed is (the house rule, applied here)
-----------------------------------------------------------------------
This module sits inside a best-effort relay (:func:`notifier.notify`'s own
governing rule: **never raise, and never let a delivery problem look like
an investigation failure**). Extending that relay with three new decision
points -- silence lookup, ownership lookup, de-dup lookup -- means three new
places a bug could silently drop a page. Each is pinned to fail in the
**same** direction, stated once here so it does not have to be re-derived
at each call site:

1. **Silence file fails to load** (malformed YAML) -- treated as *no
   silences configured*, not as *everything is silenced*. This is the
   opposite of ``health.load_silences``'s own behaviour when called
   directly for ``nettools config check`` (there, raising IS the safety
   property -- an operator staring at a maintenance window they believe is
   filed must see the typo). Here, inside a call that must never raise, a
   bad file degrades to "page as if the feature did not exist" -- a
   spurious page during a declared window is a nuisance; a page that
   silently never arrives because of an unrelated YAML typo is the failure
   this whole change exists to prevent.
2. **Ownership file fails to load** (malformed YAML) -- falls back to
   ``ownership.default_table()`` (the single default-channel owner), not
   to "nobody". Same direction ``ownership.py`` itself already takes for
   "no rule matched" (never resolves to zero owners) extended to "table
   failed to parse" as well.
3. **De-dup state file is missing, empty, or unparseable** -- treated as
   *no prior record for any key*, which means *never suppress*. A
   duplicate page is the failure this feature intentionally accepts to
   avoid the worse one: a corrupt state file silently swallowing a real,
   new page. See :func:`_load_state`'s own docstring.
4. **De-dup state file fails to WRITE** after a real send -- delivery has
   already happened by that point and is never undone; the write failure
   only means a future call has nothing to dedup against, which again
   fails toward *sending again*, never toward *suppressing*.

The one place this module *can* suppress delivery outright is the two
mechanisms built to do exactly that on purpose: a matched silence (an
operator said so) and a de-dup match (the identical thing was already sent
inside the window). Both are visible in the returned record -- see
:func:`relay`'s own docstring -- never a silent drop.

What this module deliberately leaves alone
---------------------------------------------
* **Escalation's ``age_seconds``.** ``ownership.py``'s own docstring already
  states the honest reason: an accurate "how long has this been open" needs
  the diagnosis ledger's ``recorded_at``, and ``ledger.py`` is owned by
  another track this session. :func:`relay` accepts ``age_seconds`` as an
  optional pass-through and does not compute it -- a caller that already has
  it (from a ledger query, once wired) gets escalation; one that does not
  simply never escalates, which is correct per ``ownership.resolve_ownership``'s
  own contract (omitting the fact one has no basis to claim is not a bug).
* **True message threading** (Telegram's ``reply_to_message_id``, editing a
  prior message instead of sending a new one). Considered and deliberately
  not built: it would require ``TelegramNotifier.send`` to parse and return
  the API response's ``message_id`` (it currently discards the response
  body entirely) and this module to persist it per key -- a real feature,
  but a second one, and this backlog row is sized S. The de-dup mechanism
  above already stops "the same object pages five times"; it just does so
  by not sending the repeats, rather than by visually grouping them in the
  chat. Left for a follow-up, named rather than silently absent.
* **Batching multiple different objects into one digest message.** The
  architecture this module operates inside is one-shot: ``nettools
  investigate DEVICE SUBJECT --notify`` is a fresh process per call, with no
  daemon collecting notifications across calls to batch. Building a
  collector to enable batching would be exactly the daemon/state-tracking
  shape ``ownership.py``'s own docstring already declined to build for
  escalation, for the same reason -- there is no running process here to
  hold a batch window open.
* **Wiring a caller.** ``cli.py`` is owned by another track this session
  (identical position B-681's own docstring already took). :func:`relay`
  is a drop-in replacement for the ``notifier.notify(...)`` call at
  ``cli.py``'s ``--notify`` handling -- same required arguments
  (``report``, ``device``, ``subject``, ``finding``), everything else
  optional and additive -- so wiring it is a one-line swap once that file is
  free to make it. Until then this module has no caller in the shipped CLI
  path, and every new ``NETTOOLS_*`` variable it declares changes nothing
  about tonight's live Telegram delivery by default (see below).

New settings, declared here (``settings.py`` is owned elsewhere tonight)
------------------------------------------------------------------------
Same position ``health.py``'s ``NETTOOLS_SILENCE_FILE_ENV`` and
``ownership.py``'s own docstring already take for the identical reason.
All three below are **opt-in by absence** -- unset, each disables exactly
the mechanism it configures rather than guessing a default location, so a
build that has not been told about any of them behaves exactly as it does
today:

* ``NETTOOLS_RELAY_STATE_FILE`` -- the de-dup state file. Unset disables
  de-duplication entirely (every call is treated as unseen, matching
  today's "every event pages" behaviour byte for byte).
* ``NETTOOLS_RELAY_DEDUP_SECONDS`` -- the de-dup window, read through this
  package's shared ``_float_env`` (F2). Only consulted once de-duplication
  is enabled by the state file being set. Default 1800s (30 minutes) -- a
  **stated policy default, not measured**, the same honest admission
  ``admission.py``'s probe budget makes for its own numbers: there is no
  failure threshold to discover by sending more Telegram messages, only an
  operator tolerance to choose.
* ``NETTOOLS_OWNERSHIP_FILE`` -- the declarative ownership table
  (``ownership.py``'s own ``load_ownership_table`` takes a bare path and
  reads no environment variable by design; this is the missing caller-side
  wiring for it). Unset resolves to ``ownership.default_table()`` -- today's
  single-channel behaviour, unchanged.

**Report to the operator**: these three need ``Setting()`` entries added to
``settings.py`` (by whichever track owns it) before ``nettools config
show|check`` will list them -- see the accompanying report for the exact
entries.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import health
from . import notifier as N
from . import ownership as O
from ._env import _float_env

__all__ = [
    "DEFAULT_DEDUP_SECONDS",
    "NETTOOLS_OWNERSHIP_FILE_ENV",
    "NETTOOLS_RELAY_DEDUP_SECONDS_ENV",
    "NETTOOLS_RELAY_STATE_FILE_ENV",
    "evaluate_dedup",
    "relay",
]

NETTOOLS_RELAY_STATE_FILE_ENV = "NETTOOLS_RELAY_STATE_FILE"
NETTOOLS_RELAY_DEDUP_SECONDS_ENV = "NETTOOLS_RELAY_DEDUP_SECONDS"
NETTOOLS_OWNERSHIP_FILE_ENV = "NETTOOLS_OWNERSHIP_FILE"

#: See the module docstring's "New settings" section -- a stated policy
#: default, not measured.
DEFAULT_DEDUP_SECONDS = 1800.0


# --------------------------------------------------------------------------- #
# De-duplication state: a flat JSON file, one record per (device, subject).
# --------------------------------------------------------------------------- #


def _dedup_key(device: str, subject: str) -> str:
    # `\x1f` (unit separator) rather than a printable delimiter: `device` and
    # `subject` are code-controlled (CLI args / event routing output), but
    # neither is guaranteed free of ":" or "|", and a colliding key would
    # silently merge two distinct objects' dedup state into one -- the same
    # "do not build a delimiter someone's own data can smuggle" caution this
    # codebase already applies to `evidence_key`.
    return f"{device}\x1f{subject}"


def _load_state(path: Path) -> tuple[dict[str, Any], bool]:
    """Read the de-dup state file. Returns ``(state, readable)``.

    ``readable=False`` means the file exists but could not be parsed as a
    JSON object -- treated by every caller exactly like "no prior record for
    any key", **never** like "everything matches". See the module
    docstring's fail-direction rule 3: a corrupt state file must fail toward
    sending again, never toward silently suppressing a new page.
    """

    if not path.exists():
        return {}, True
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}, False
    if not raw.strip():
        return {}, True
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}, False
    if not isinstance(data, dict):
        return {}, False
    return data, True


def _save_state(path: Path, state: dict[str, Any]) -> bool:
    """Best-effort, atomic (write-then-replace). Returns whether it succeeded.

    **Never raises.** A failed write must never block delivery -- the send
    this write follows has already happened -- and only means a future call
    has nothing to dedup against, which fails toward *re-notifying*, never
    toward *suppressing* (module docstring, fail-direction rule 4).
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def evaluate_dedup(
    *,
    device: str,
    subject: str,
    finding: str,
    cause: dict[str, Any] | None,
    state_path: str | Path | None,
    window_seconds: float,
    now: datetime,
) -> dict[str, Any]:
    """Has (device, subject) already been notified with this exact signature
    inside ``window_seconds``? **Never raises.**

    Returns a dict that always carries ``"evaluated"`` (bool) -- absence is
    never zero (OBS-181's rule, applied to this decision): "de-dup ran and
    found nothing to suppress" (``evaluated=True, suppressed=False``) is a
    different fact from "de-dup did not run at all"
    (``evaluated=False`` -- ``state_path`` is ``None``, i.e. the feature is
    off), and the two must never render the same to a caller deciding
    whether to trust a "0 suppressed" count.

    ``signature`` is ``[finding, cause.rung]`` -- both code-typed, never a
    device-authored string (see the module docstring). A signature change
    from the prior record always returns ``suppressed=False`` regardless of
    the window: an update is never suppressed, only an unchanged repeat is.
    """

    key = _dedup_key(device, subject)
    if state_path is None:
        return {
            "evaluated": False, "suppressed": False, "state_readable": None,
            "key": key,
            "reason": (
                f"{NETTOOLS_RELAY_STATE_FILE_ENV} not set; de-duplication is off"
            ),
        }

    path = Path(state_path)
    state, readable = _load_state(path)
    signature = [finding, (cause or {}).get("rung")]

    result: dict[str, Any] = {
        "evaluated": True, "state_readable": readable, "key": key,
        "signature": signature, "window_seconds": window_seconds,
    }

    prior = state.get(key)
    if not isinstance(prior, dict):
        result.update(
            suppressed=False,
            reason="no prior notification recorded for this device/subject",
        )
        return result

    if prior.get("signature") != signature:
        result.update(
            suppressed=False,
            reason=(
                f"signature changed ({prior.get('signature')!r} -> "
                f"{signature!r}); an update is never suppressed regardless "
                "of the window"
            ),
        )
        return result

    try:
        last = datetime.fromisoformat(str(prior.get("last_notified_at")))
        if last.tzinfo is None:
            raise ValueError("naive timestamp in de-dup state")
    except (TypeError, ValueError):
        # The record exists but cannot be timed -- absence-never-zero applied
        # to the record itself: this is not evidence of a recent
        # notification, so it is treated exactly like no record at all
        # (fail-direction rule 3), and marked unreadable so the difference
        # from a clean "no match" is visible.
        result.update(
            suppressed=False, state_readable=False,
            reason="prior record has no usable timestamp; treated as unknown, not as recent",
        )
        return result

    age = (now - last).total_seconds()
    if age < window_seconds:
        result.update(
            suppressed=True,
            reason=(
                f"same signature notified {age:.0f}s ago, inside the "
                f"{window_seconds:.0f}s window"
            ),
        )
    else:
        result.update(
            suppressed=False,
            reason=(
                f"same signature but {age:.0f}s >= {window_seconds:.0f}s "
                "window; re-notified as a reminder that this is still open"
            ),
        )
    return result


def _record_notification(
    *, device: str, subject: str, finding: str, cause: dict[str, Any] | None,
    state_path: str | Path, now: datetime,
) -> bool:
    """Persist "this signature was sent, now" for (device, subject).

    Called only after a real send (module docstring: state updates only
    follow an actual delivery, never a no-op or a failed attempt -- see
    :func:`relay`). Merges into whatever is already on disk rather than
    overwriting the whole file, so an unreadable/corrupt prior file simply
    starts a fresh state under this key rather than losing every other
    key's history.
    """

    path = Path(state_path)
    state, _readable = _load_state(path)
    key = _dedup_key(device, subject)
    prior = state.get(key) if isinstance(state.get(key), dict) else {}
    count = int(prior.get("notify_count", 0)) + 1 if isinstance(prior, dict) else 1
    state[key] = {
        "signature": [finding, (cause or {}).get("rung")],
        "last_notified_at": now.isoformat(),
        "notify_count": count,
        # Not read back by any logic above -- kept only so a human opening
        # the state file can identify a row without decoding the key.
        "device": device, "subject": subject,
    }
    return _save_state(path, state)


# --------------------------------------------------------------------------- #
# Silence / ownership resolution -- loading only. The matching/routing logic
# itself is entirely health.find_silence / ownership.resolve_ownership.
# --------------------------------------------------------------------------- #


def _resolve_silences(
    silences: Sequence[health.Silence] | None, path: str | Path | None
) -> tuple[Sequence[health.Silence], dict[str, Any]]:
    """Explicit ``silences=`` wins; else ``path=``; else
    ``NETTOOLS_SILENCE_FILE``. A malformed file degrades to "no silences" --
    see the module docstring's fail-direction rule 1 for why that is the
    opposite of, and does not contradict, ``health.load_silences``'s own
    raise-on-malformed contract for its other callers.
    """

    if silences is not None:
        return silences, {"source": "explicit", "error": None}
    resolved = path if path is not None else os.getenv(health.NETTOOLS_SILENCE_FILE_ENV)
    try:
        return health.load_silences(resolved), {"source": resolved, "error": None}
    except health.SilenceError as exc:
        return (), {"source": resolved, "error": str(exc)}


def _resolve_ownership_table(
    table: O.OwnershipTable | None, path: str | Path | None
) -> tuple[O.OwnershipTable, dict[str, Any]]:
    """Explicit ``table=`` wins; else ``path=``; else
    ``NETTOOLS_OWNERSHIP_FILE``. A malformed file falls back to
    ``ownership.default_table()`` -- see the module docstring's
    fail-direction rule 2.
    """

    if table is not None:
        return table, {"source": "explicit", "error": None}
    resolved = path if path is not None else os.getenv(NETTOOLS_OWNERSHIP_FILE_ENV)
    try:
        return O.load_ownership_table(resolved), {"source": resolved, "error": None}
    except O.OwnershipError as exc:
        return O.default_table(), {"source": resolved, "error": str(exc)}


def _summarize(deliveries: Sequence[dict[str, Any]]) -> str:
    """One word for the top-level ``"decision"`` field -- see :func:`relay`."""

    if any(d.get("attempted") and d.get("ok") for d in deliveries):
        return "sent"
    if all(d.get("ok") and not d.get("attempted") for d in deliveries):
        return "no_provider"
    return "failed"


# --------------------------------------------------------------------------- #
# The entry point: connects the three mechanisms and the one new one.
# --------------------------------------------------------------------------- #


def relay(
    report: dict,
    *,
    device: str,
    subject: str,
    finding: str,
    trustworthy: bool | None = None,
    cause: dict | None = None,
    ticket_id: str | None = None,
    role: str | None = None,
    age_seconds: float | None = None,
    silences: Sequence[health.Silence] | None = None,
    silence_path: str | Path | None = None,
    ownership_table: O.OwnershipTable | None = None,
    ownership_path: str | Path | None = None,
    dedup_enabled: bool | None = None,
    dedup_state_path: str | Path | None = None,
    dedup_window_seconds: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """B-209: silence, then ownership, then de-dup, then deliver. **Never raises.**

    A drop-in replacement for ``notifier.notify(report, device=..., subject=...,
    finding=...)`` at a call site that wants the hardening this row asks
    for -- the four required/commonly-passed arguments are the same;
    everything else is optional and additive. See the module docstring for
    what each of the three reused mechanisms and the one new mechanism does,
    and which way each new decision fails.

    Order matters and is deliberate:

    1. **Silence** is checked first, against ``health.find_silence`` (see the
       module docstring's opening section for why ``finding`` is passed as
       ``rule``). A match means delivery is never attempted, on any owner's
       channel -- an operator-declared silence outranks routing and de-dup
       both.
    2. **Ownership** is resolved next (even when silenced -- see below),
       via ``ownership.resolve_ownership``, so the returned record always
       names who *would* have been told.
    3. **De-dup** is evaluated only when not silenced -- a silenced finding
       never reaches this step, and correctly so: there is nothing to
       de-duplicate against a send that was never attempted.
    4. **Delivery** happens through ``notifier.notify_owner``, once per
       resolved owner (normally one; two when ``ownership.resolve_ownership``
       escalated). De-dup state is recorded **only if at least one delivery
       actually attempted and succeeded** -- a no-op provider or a failed
       send must not mark this signature as "already told", or a real
       outage would silently suppress the real page once the channel
       recovers (module docstring, fail-direction rule 4, extended).

    Returns a dict with a top-level ``"decision"`` in
    ``{"silenced", "suppressed", "sent", "no_provider", "failed"}``, plus
    ``"silence"``, ``"dedup"`` (always present, see :func:`evaluate_dedup`'s
    own absence-never-zero note), ``"ownership"``
    (``ownership.RoutingResult.as_dict()``), and ``"deliveries"`` (the list
    of ``notify_owner`` records, one per owner actually or nominally
    addressed -- empty when silenced or suppressed, since nothing was sent
    to anyone).
    """

    now = now or datetime.now(timezone.utc)

    resolved_silences, silence_source = _resolve_silences(silences, silence_path)
    matched = health.find_silence(
        resolved_silences, device=device, rule=finding, subject=subject, now=now
    )
    silence_notice = N.SilenceNotice(**matched.as_dict()) if matched is not None else None

    table, ownership_source = _resolve_ownership_table(ownership_table, ownership_path)
    routing = O.resolve_ownership(
        table, device=device, role=role, finding=finding, age_seconds=age_seconds
    )

    if silence_notice is not None:
        deliveries = [
            N.notify_owner(
                report, owner, device=device, subject=subject, finding=finding,
                silence=silence_notice,
            )
            for owner in routing.owners
        ]
        return {
            "decision": "silenced",
            "silence": matched.as_dict(),
            "dedup": {
                "evaluated": False, "suppressed": False,
                "reason": "not evaluated: silenced before de-dup was considered",
            },
            "ownership": routing.as_dict(),
            "ownership_source": ownership_source,
            "silence_source": silence_source,
            "deliveries": deliveries,
        }

    if dedup_enabled is False:
        state_path: str | Path | None = None
    else:
        state_path = (
            dedup_state_path if dedup_state_path is not None
            else os.getenv(NETTOOLS_RELAY_STATE_FILE_ENV)
        )
    window = (
        dedup_window_seconds if dedup_window_seconds is not None
        else _float_env(NETTOOLS_RELAY_DEDUP_SECONDS_ENV, DEFAULT_DEDUP_SECONDS)
    )

    dedup = evaluate_dedup(
        device=device, subject=subject, finding=finding, cause=cause,
        state_path=state_path, window_seconds=window, now=now,
    )

    if dedup["suppressed"]:
        return {
            "decision": "suppressed",
            "silence": None,
            "dedup": dedup,
            "ownership": routing.as_dict(),
            "ownership_source": ownership_source,
            "silence_source": silence_source,
            "deliveries": [],
        }

    deliveries = [
        N.notify_owner(
            report, owner, device=device, subject=subject, finding=finding,
            trustworthy=trustworthy, cause=cause, ticket_id=ticket_id,
        )
        for owner in routing.owners
    ]

    decision = _summarize(deliveries)
    if decision == "sent" and state_path is not None:
        _record_notification(
            device=device, subject=subject, finding=finding, cause=cause,
            state_path=state_path, now=now,
        )

    return {
        "decision": decision,
        "silence": None,
        "dedup": dedup,
        "ownership": routing.as_dict(),
        "ownership_source": ownership_source,
        "silence_source": silence_source,
        "deliveries": deliveries,
    }
