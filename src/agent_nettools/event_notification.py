"""B-707 event notification lifecycle: one ticket, one Telegram reply chain.

This module sends only code-authored lifecycle messages. It never receives raw
device output, model prose, or an evidence bundle. Delivery is explicitly gated
by ``NETTOOLS_EVENT_NOTIFY`` and disabled unless an operator enables it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import notifier, relay_policy
from ._env import _BOOL_TRUE
from .event_reporting import InvestigationReceipt
from .event_store import EventStore, OutboxRecord
from .flows import FLOWS
from .telegram_delivery import drain_notifications, enqueue_activity

NETTOOLS_EVENT_NOTIFY_ENV = "NETTOOLS_EVENT_NOTIFY"
NETTOOLS_EVENT_NOTIFICATION_STATE_FILE_ENV = "NETTOOLS_EVENT_NOTIFICATION_STATE_FILE"
NETTOOLS_TELEGRAM_LIVE_CARD_ENV = "NETTOOLS_TELEGRAM_LIVE_CARD"
NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS_ENV = "NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS"
NETTOOLS_TELEGRAM_SHOW_REASONING_TAIL_ENV = "NETTOOLS_TELEGRAM_SHOW_REASONING_TAIL"
DEFAULT_EVENT_NOTIFICATION_STATE_FILE = "event-notifications.json"
PENDING_ROOT_LEASE_SECONDS = 60.0
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventNotification:
    event_id: str
    ticket_id: str
    root_message_ids: tuple[int, ...]
    enabled: bool


def notifications_enabled() -> bool:
    return os.getenv(NETTOOLS_EVENT_NOTIFY_ENV, "0").strip().lower() in _BOOL_TRUE


def reasoning_tail_enabled() -> bool:
    """Whether testing-only, non-authoritative public summaries may be sent."""

    return os.getenv(NETTOOLS_TELEGRAM_SHOW_REASONING_TAIL_ENV, "0").strip().lower() in _BOOL_TRUE


def live_card_chat_ids() -> tuple[str, ...]:
    """Configured live-card test destinations, intersected with delivery allowlist."""

    if os.getenv(NETTOOLS_TELEGRAM_LIVE_CARD_ENV, "0").strip().lower() not in _BOOL_TRUE:
        return ()
    allowed = {
        value.strip()
        for value in os.getenv(notifier.TELEGRAM_CHAT_ENV, "").split(",")
        if value.strip()
    }
    requested = tuple(
        value.strip()
        for value in os.getenv(NETTOOLS_TELEGRAM_LIVE_CARD_CHAT_IDS_ENV, "").split(",")
        if value.strip()
    )
    return tuple(chat_id for chat_id in requested if chat_id in allowed)


def live_card_replaces_legacy() -> bool:
    """Whether cards can safely replace legacy fan-out for this process.

    The existing relay policy addresses every configured chat and cannot yet
    exclude an individual destination. Until it gains that policy primitive,
    a partial live-card subset would dual-send to selected chats. Fail closed:
    only an exact allowlist match replaces the legacy presentation.
    """

    allowed = {
        value.strip()
        for value in os.getenv(notifier.TELEGRAM_CHAT_ENV, "").split(",")
        if value.strip()
    }
    selected = set(live_card_chat_ids())
    return bool(allowed) and selected == allowed


def deliver_live_card(store: EventStore, record: OutboxRecord | None) -> None:
    """Best-effort immediate delivery for an initial or terminal card revision.

    Intermediate card revisions remain queued for a later debounced flush. A
    provider failure only records retryable outbox state; it must never alter
    the event/ticket outcome that produced the card.
    """

    sender = _telegram_sender()
    if sender is None:
        return
    try:
        drain_notifications(
            store,
            notifier=sender,
            owner=f"event-notification:{os.getpid()}",
        )
    except Exception:  # noqa: BLE001 -- delivery must not consume the incident transaction
        LOG.exception(
            "live-card outbox drain failed: outbox_id=%s",
            record.outbox_id if record is not None else None,
        )


def deliver_live_activity(store: EventStore, *, chat_id: str, activity: Any) -> None:
    """Best-effort activity reply; durable outbox preserves retry on failure."""

    record = enqueue_activity(store, chat_id=chat_id, activity=activity)
    if record is None:
        return
    sender = _telegram_sender()
    if sender is None:
        return
    try:
        drain_notifications(
            store,
            notifier=sender,
            owner=f"event-notification:{os.getpid()}",
        )
    except Exception:  # noqa: BLE001 -- never consume incident on presentation failure
        LOG.exception("live-activity outbox drain failed: outbox_id=%s", record.outbox_id)


def _state_path() -> Path:
    configured = os.getenv(NETTOOLS_EVENT_NOTIFICATION_STATE_FILE_ENV)
    if configured:
        return Path(configured)
    return Path.home() / ".local" / "state" / "agent-nettools" / DEFAULT_EVENT_NOTIFICATION_STATE_FILE


def _load_state(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"WARNING: event notification state is unreadable: {exc}", file=sys.stderr)
        return {}
    if not isinstance(parsed, dict):
        print("WARNING: event notification state has an invalid root object", file=sys.stderr)
        return {}
    return parsed


def _save_state(path: Path, state: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                temporary.write(json.dumps(state, indent=2, sort_keys=True))
                temporary.flush()
                os.fsync(temporary.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, path)
            os.chmod(path, 0o600)
            directory = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return True
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    except OSError as exc:
        print(f"WARNING: event notification state was not persisted: {exc}", file=sys.stderr)
        return False


@contextlib.contextmanager
def _locked_state(path: Path) -> Iterator[dict[str, Any] | None]:
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        lock_path = path.with_suffix(path.suffix + ".lock")
        handle = open(lock_path, "a+b")
        os.chmod(lock_path, 0o600)
    except OSError as exc:
        print(f"WARNING: event notification state lock is unavailable: {exc}", file=sys.stderr)
        yield None
        return
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield _load_state(path)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _telegram_sender() -> notifier.TelegramNotifier | None:
    selected = notifier.get_notifier()
    return selected if isinstance(selected, notifier.TelegramNotifier) else None


def _send(sender: notifier.TelegramNotifier, text: str, reply_to: tuple[int, ...] = ()) -> tuple[int, ...]:
    return tuple(sender.send_text(text, reply_to_message_ids=reply_to))


def _root_message_ids(relay_record: dict[str, Any]) -> tuple[int, ...]:
    if relay_record.get("decision") != "sent":
        return ()
    for delivery in relay_record.get("deliveries", []):
        if not isinstance(delivery, dict) or not delivery.get("ok"):
            continue
        message_ids = delivery.get("message_ids")
        if isinstance(message_ids, list) and all(isinstance(value, int) for value in message_ids):
            return tuple(message_ids)
    return ()


def _record_audit(
    notification: EventNotification,
    *,
    action: str,
    outcome: str | None = None,
    finding: str | None = None,
) -> None:
    state_path = _state_path()
    with _locked_state(state_path) as state:
        if state is None:
            return
        entry = state.get(notification.event_id)
        if not isinstance(entry, dict) or entry.get("ticket_id") != notification.ticket_id:
            return
        audit = entry.setdefault("audit", [])
        if not isinstance(audit, list):
            return
        record: dict[str, Any] = {"action": action}
        if outcome is not None:
            record["outcome"] = outcome
        if finding is not None:
            record["finding"] = finding
        audit.append(record)
        _save_state(state_path, state)


def open_notification(event_id: str, *, ticket_id: str, device: str, subject: str) -> EventNotification:
    """Send or recover the one initial message for an admitted event."""

    if not notifications_enabled():
        return EventNotification(event_id, ticket_id, (), False)
    state_path = _state_path()
    with _locked_state(state_path) as state:
        if state is None:
            return EventNotification(event_id, ticket_id, (), False)
        prior = state.get(event_id)
        if isinstance(prior, dict) and prior.get("ticket_id") == ticket_id:
            ids = prior.get("root_message_ids")
            if prior.get("status") == "sent" and isinstance(ids, list) and all(
                isinstance(value, int) for value in ids
            ):
                return EventNotification(event_id, ticket_id, tuple(ids), True)
            if prior.get("status") == "pending":
                pending_at = prior.get("updated")
                if isinstance(pending_at, (int, float)) and time.time() - pending_at < PENDING_ROOT_LEASE_SECONDS:
                    return EventNotification(event_id, ticket_id, (), False)
        state[event_id] = {
            "ticket_id": ticket_id,
            "status": "pending",
            "updated": time.time(),
            "audit": [{"action": "pending", "provider": "telegram"}],
        }
        if not _save_state(state_path, state):
            return EventNotification(event_id, ticket_id, (), False)
    try:
        relay_record = relay_policy.relay(
            {
                "authoritative": True,
                "observations": [{"claim": "Investigation accepted"}],
            },
            device=device,
            subject=subject,
            finding="event_investigation_accepted",
            trustworthy=True,
            ticket_id=ticket_id,
        )
        ids = _root_message_ids(relay_record)
        if not ids:
            with _locked_state(state_path) as state:
                if state is not None and isinstance(state.get(event_id), dict):
                    state[event_id]["status"] = "failed"
                    state[event_id]["updated"] = time.time()
                    _save_state(state_path, state)
            _record_audit(EventNotification(event_id, ticket_id, (), True), action="send_failed")
            return EventNotification(event_id, ticket_id, (), False)
    except (notifier.NotifierError, OSError, ValueError):
        with _locked_state(state_path) as state:
            if state is not None and isinstance(state.get(event_id), dict):
                state[event_id]["status"] = "failed"
                state[event_id]["updated"] = time.time()
                _save_state(state_path, state)
        _record_audit(EventNotification(event_id, ticket_id, (), True), action="send_failed")
        return EventNotification(event_id, ticket_id, (), False)
    with _locked_state(state_path) as state:
        if state is None:
            return EventNotification(event_id, ticket_id, (), False)
        entry = state.get(event_id)
        if not isinstance(entry, dict) or entry.get("ticket_id") != ticket_id:
            return EventNotification(event_id, ticket_id, (), False)
        entry["status"] = "sent"
        entry["updated"] = time.time()
        entry["root_message_ids"] = list(ids)
        audit = entry.setdefault("audit", [])
        if isinstance(audit, list):
            audit.append({"action": "opened", "message_ids": list(ids), "provider": "telegram"})
        if not _save_state(state_path, state):
            return EventNotification(event_id, ticket_id, (), False)
    return EventNotification(event_id, ticket_id, ids, True)


def progress(notification: EventNotification, stage: str) -> None:
    """Append one bounded lifecycle milestone beneath the initial message."""

    if notification.enabled and notification.root_message_ids:
        try:
            sender = _telegram_sender()
            if sender is not None:
                _send(sender, f"Ticket {notification.ticket_id}: {stage}", notification.root_message_ids)
                _record_audit(notification, action="progress")
        except (notifier.NotifierError, OSError, ValueError):
            return


def investigation_plan(
    notification: EventNotification,
    *,
    flow: str,
    max_iterations: int,
    max_tool_calls: int,
    time_budget_s: float,
) -> None:
    """Append the code-observed investigation scope before any tool call."""

    if not notification.enabled or not notification.root_message_ids:
        return
    declared_flow = FLOWS.get(flow)
    rungs = ", ".join(rung.name for rung in declared_flow.descent) if declared_flow else flow
    text = (
        f"Incident {notification.ticket_id}: investigation plan\n"
        f"Flow: {flow}\n"
        f"Deterministic rungs: {rungs}\n"
        f"Bounds: {max_iterations} turns, {max_tool_calls} tool calls, {time_budget_s:g}s"
    )
    try:
        sender = _telegram_sender()
        if sender is not None:
            _send(sender, text, notification.root_message_ids)
            _record_audit(notification, action="investigation_plan")
    except (notifier.NotifierError, OSError, ValueError):
        return


def deterministic_drill(notification: EventNotification, receipt: InvestigationReceipt) -> None:
    """Append the code-observed rung drill; never model narrative or raw text."""

    if not notification.enabled or not notification.root_message_ids:
        return
    lines = [f"Incident {notification.ticket_id}: deterministic drill"]
    for rung in receipt.rungs:
        lines.append(
            f"{rung['position']}/{rung['of']} {rung['rung']} @ {rung['device']}: "
            f"{str(rung['status']).upper()}"
        )
    if receipt.coherence is not None:
        status = receipt.coherence.get("status", "not measured")
        lines.append(f"Coherence: {status}")
    try:
        sender = _telegram_sender()
        if sender is not None:
            _send(sender, "\n".join(lines), notification.root_message_ids)
            _record_audit(notification, action="deterministic_drill", finding=receipt.finding)
    except (notifier.NotifierError, OSError, ValueError):
        return


def final_diagnosis(notification: EventNotification, receipt: InvestigationReceipt) -> None:
    """Append the final code-observed classification and RCA when one exists."""

    if not notification.enabled or not notification.root_message_ids:
        return
    if receipt.finding in {"all_layers_healthy", "no_fault_on_path"}:
        classification = "NO_ACTIVE_FAULT"
        detail = "No current fault was localized on the investigated path."
    elif receipt.finding in {"undetermined", "temporally_incoherent"} or not receipt.trustworthy:
        classification = "INCONCLUSIVE"
        detail = "Evidence cannot support one trustworthy current diagnosis."
    elif receipt.finding == "subject_not_found":
        classification = "SUBJECT_INVALID"
        detail = "The requested object is not present in current evidence."
    elif receipt.cause is not None:
        classification = "ACTIVE_FAULT_LOCALIZED"
        detail = f"RCA: lowest broken rung is {receipt.cause['rung']} @ {receipt.cause['device']}"
    else:
        classification = "INCONCLUSIVE"
        detail = "No deterministic root cause was localized."
    lines = [
        f"Incident {notification.ticket_id}: {classification}",
        f"Finding: {receipt.finding}",
        detail,
        f"Confidence: {'trustworthy' if receipt.trustworthy else 'limited'}",
    ]
    if receipt.causal_chain:
        lines.append("Chain: " + " -> ".join(item["rung"] for item in receipt.causal_chain))
    try:
        sender = _telegram_sender()
        if sender is not None:
            _send(sender, "\n".join(lines), notification.root_message_ids)
            _record_audit(notification, action="final_diagnosis", outcome=classification, finding=receipt.finding)
    except (notifier.NotifierError, OSError, ValueError):
        return


def close(notification: EventNotification, *, outcome: str, finding: str | None = None) -> None:
    """Append a code-authored terminal outcome to the notification thread."""

    if notification.enabled and notification.root_message_ids:
        if finding == "device_unreachable":
            _record_audit(
                notification,
                action="terminal_suppressed",
                outcome=outcome,
                finding=finding,
            )
            return
        try:
            sender = _telegram_sender()
            if sender is not None:
                suffix = f"\nFinding: {finding}" if finding else ""
                _send(sender, f"Ticket {notification.ticket_id}: {outcome}{suffix}", notification.root_message_ids)
                _record_audit(notification, action="closed", outcome=outcome, finding=finding)
        except (notifier.NotifierError, OSError, ValueError):
            return
