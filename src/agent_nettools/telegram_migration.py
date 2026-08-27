"""Safe migration helpers for legacy Telegram notification state."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .event_store import EventStore

__all__ = ["LegacyCardMapping", "LegacyMigrationReport", "apply_legacy_migration", "inspect_legacy_state"]


@dataclass(frozen=True)
class LegacyCardMapping:
    event_id: str
    ticket_id: str
    message_ids: tuple[int, ...]


@dataclass(frozen=True)
class LegacyMigrationReport:
    mappings: tuple[LegacyCardMapping, ...]
    refusals: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return bool(self.mappings) and not self.refusals


def inspect_legacy_state(path: Path) -> LegacyMigrationReport:
    """Read legacy state without modifying it, refusing ambiguous entries."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return LegacyMigrationReport((), ("legacy notification state file does not exist",))
    except (OSError, ValueError) as exc:
        return LegacyMigrationReport((), (f"legacy notification state is unreadable: {type(exc).__name__}",))
    if not isinstance(payload, dict):
        return LegacyMigrationReport((), ("legacy notification state root is not an object",))

    mappings: list[LegacyCardMapping] = []
    refusals: list[str] = []
    for event_id, entry in sorted(payload.items()):
        if not isinstance(event_id, str) or not isinstance(entry, dict):
            refusals.append("legacy notification state contains a malformed event entry")
            continue
        ticket_id = entry.get("ticket_id")
        message_ids = entry.get("root_message_ids")
        if entry.get("status") != "sent":
            refusals.append(f"{event_id}: legacy notification is not sent")
            continue
        if not isinstance(ticket_id, str) or not ticket_id:
            refusals.append(f"{event_id}: ticket_id is missing")
            continue
        if not isinstance(message_ids, list) or not message_ids or not all(isinstance(value, int) and value > 0 for value in message_ids):
            refusals.append(f"{event_id}: root_message_ids are invalid")
            continue
        mappings.append(LegacyCardMapping(event_id, ticket_id, tuple(message_ids)))
    return LegacyMigrationReport(tuple(mappings), tuple(refusals))


def apply_legacy_migration(
    store: EventStore,
    *,
    path: Path,
    chat_ids: tuple[str, ...],
) -> LegacyMigrationReport:
    """Copy validated legacy receipts into the durable store after backup.

    Every mapping needs exactly one configured chat/message pair and an
    existing durable event. The source JSON remains untouched; rollback is
    deleting the newly created database receipts and returning to legacy mode.
    """

    report = inspect_legacy_state(path)
    if report.refusals:
        return report
    if len(chat_ids) != 1:
        return LegacyMigrationReport((), ("legacy migration requires exactly one configured chat",))
    if any(len(mapping.message_ids) != 1 for mapping in report.mappings):
        return LegacyMigrationReport((), ("legacy state has multi-chat message mappings; migrate manually",))
    unknown = [mapping.event_id for mapping in report.mappings if store.get(mapping.event_id) is None]
    if unknown:
        return LegacyMigrationReport((), tuple(f"{event_id}: durable event is missing" for event_id in unknown))

    backup = path.with_suffix(path.suffix + ".telegram-card-backup")
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    for mapping in report.mappings:
        store.record_telegram_card(
            event_id=mapping.event_id,
            chat_id=chat_ids[0],
            message_id=mapping.message_ids[0],
            render_hash="legacy-receipt",
        )
    return report