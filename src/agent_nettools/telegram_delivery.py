"""Durable delivery worker for the outbound-only Telegram live card."""

from __future__ import annotations

import logging
from datetime import datetime

from .event_store import EventLeaseError, EventStore, OutboxRecord
from .investigation_activity import InvestigationActivity
from .notifier import NotifierError, TelegramNotifier
from .telegram_chat_renderer import render_activity_message
from .telegram_renderer import TelegramRender

__all__ = [
    "deliver_activity",
    "deliver_card",
    "drain_notifications",
    "enqueue_activity",
    "enqueue_card",
]

LOG = logging.getLogger(__name__)


def enqueue_card(
    store: EventStore,
    *,
    event_id: str,
    chat_id: str,
    render: TelegramRender,
) -> OutboxRecord | None:
    """Enqueue a changed card revision; unchanged receipts are coalesced."""

    receipt = store.telegram_card_receipt(event_id=event_id, chat_id=chat_id)
    if receipt is not None and receipt.render_hash == render.render_hash:
        return None
    record, _created = store.enqueue_notification(
        event_id=event_id,
        destination=f"telegram:{chat_id}",
        kind=f"card:{render.render_hash}",
        payload={"chat_id": chat_id, "text": render.text, "render_hash": render.render_hash},
    )
    return record


def deliver_card(
    store: EventStore,
    *,
    outbox_id: int,
    notifier: TelegramNotifier,
    owner: str,
    lease_seconds: float = 30.0,
    now: datetime | None = None,
) -> OutboxRecord:
    """Send or edit one claimed card revision, preserving at-least-once semantics."""

    claimed = store.claim_notification(
        outbox_id,
        owner=owner,
        lease_seconds=lease_seconds,
        now=now,
    )
    payload = claimed.payload
    chat_id = payload.get("chat_id")
    text = payload.get("text")
    render_hash = payload.get("render_hash")
    if not all(isinstance(value, str) and value for value in (chat_id, text, render_hash)):
        return store.finish_notification(
            outbox_id,
            sent=False,
            error="invalid Telegram card payload",
            now=now,
            owner=owner,
        )
    try:
        receipt = store.telegram_card_receipt(event_id=claimed.event_id, chat_id=chat_id)
        message_id = (
            notifier.send_text_to(chat_id=chat_id, text=text)
            if receipt is None
            else notifier.edit_text(chat_id=chat_id, message_id=receipt.message_id, text=text)
        )
        store.record_telegram_card(
            event_id=claimed.event_id,
            chat_id=chat_id,
            message_id=message_id,
            render_hash=render_hash,
        )
        return store.finish_notification(outbox_id, sent=True, now=now, owner=owner)
    except NotifierError as exc:
        return store.finish_notification(
            outbox_id,
            sent=False,
            error=str(exc),
            now=now,
            retry_after_seconds=exc.retry_after_seconds,
            owner=owner,
        )


def enqueue_activity(
    store: EventStore, *, chat_id: str, activity: InvestigationActivity
) -> OutboxRecord | None:
    """Queue one meaningful chat-style activity beneath the incident card."""

    text = render_activity_message(activity)
    if text is None:
        return None
    record, _created = store.enqueue_notification(
        event_id=activity.event_id,
        destination=f"telegram:{chat_id}",
        kind=f"activity:{activity.activity_id}",
        payload={"chat_id": chat_id, "text": text, "activity_id": activity.activity_id},
    )
    return record


def deliver_activity(
    store: EventStore,
    *,
    outbox_id: int,
    notifier: TelegramNotifier,
    owner: str,
    now: datetime | None = None,
) -> OutboxRecord:
    """Deliver a durable activity reply using the card root receipt."""

    claimed = store.claim_notification(
        outbox_id,
        owner=owner,
        lease_seconds=30,
        now=now,
    )
    chat_id, text = claimed.payload.get("chat_id"), claimed.payload.get("text")
    if not isinstance(chat_id, str) or not isinstance(text, str):
        return store.finish_notification(
            outbox_id,
            sent=False,
            error="invalid Telegram activity payload",
            now=now,
            owner=owner,
        )
    receipt = store.telegram_card_receipt(event_id=claimed.event_id, chat_id=chat_id)
    if receipt is None:
        return store.finish_notification(
            outbox_id,
            sent=False,
            error="card root receipt is unavailable",
            now=now,
            owner=owner,
        )
    try:
        notifier.send_reply_text(chat_id=chat_id, reply_to_message_id=receipt.message_id, text=text)
        return store.finish_notification(outbox_id, sent=True, now=now, owner=owner)
    except NotifierError as exc:
        return store.finish_notification(
            outbox_id,
            sent=False,
            error=str(exc),
            now=now,
            retry_after_seconds=exc.retry_after_seconds,
            owner=owner,
        )


def drain_notifications(
    store: EventStore,
    *,
    notifier: TelegramNotifier,
    owner: str,
    now: datetime | None = None,
    limit: int = 100,
) -> tuple[OutboxRecord, ...]:
    """Claim and deliver bounded eligible outbox work in durable order."""

    outcomes: list[OutboxRecord] = []
    for record in store.eligible_notifications(now=now, limit=limit):
        try:
            if record.kind.startswith("card:"):
                outcome = deliver_card(
                    store,
                    outbox_id=record.outbox_id,
                    notifier=notifier,
                    owner=owner,
                    now=now,
                )
            elif record.kind.startswith("activity:"):
                outcome = deliver_activity(
                    store,
                    outbox_id=record.outbox_id,
                    notifier=notifier,
                    owner=owner,
                    now=now,
                )
            else:
                store.claim_notification(
                    record.outbox_id,
                    owner=owner,
                    lease_seconds=30,
                    now=now,
                )
                outcome = store.finish_notification(
                    record.outbox_id,
                    sent=False,
                    error=f"unsupported notification kind {record.kind!r}",
                    now=now,
                    owner=owner,
                )
            outcomes.append(outcome)
            if outcome.state != "sent":
                LOG.warning(
                    "notification delivery deferred: outbox_id=%s state=%s attempts=%s error=%s",
                    outcome.outbox_id,
                    outcome.state,
                    outcome.attempts,
                    outcome.last_error,
                )
        except EventLeaseError:
            continue
        except Exception:
            LOG.exception(
                "notification delivery crashed: outbox_id=%s kind=%s",
                record.outbox_id,
                record.kind,
            )
    return tuple(outcomes)
