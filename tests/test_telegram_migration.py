from __future__ import annotations

import json
import stat

from agent_nettools.event_store import EventStore
from agent_nettools.telegram_migration import apply_legacy_migration, inspect_legacy_state


def _legacy(path, entry: dict) -> None:
    path.write_text(json.dumps({"event-1": entry}), encoding="utf-8")


def test_legacy_state_inspection_refuses_unsent_or_ambiguous_entries(tmp_path):
    path = tmp_path / "legacy.json"
    _legacy(path, {"ticket_id": "INC-1", "status": "pending", "root_message_ids": [44]})

    report = inspect_legacy_state(path)

    assert report.ready is False
    assert report.refusals == ("event-1: legacy notification is not sent",)


def test_apply_legacy_migration_creates_receipt_and_secure_backup(tmp_path):
    path = tmp_path / "legacy.json"
    _legacy(path, {"ticket_id": "INC-1", "status": "sent", "root_message_ids": [44]})
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="event-1", device="PE2", payload={})

    report = apply_legacy_migration(store, path=path, chat_ids=("123",))

    assert report.ready is True
    assert store.telegram_card_receipt(event_id="event-1", chat_id="123").message_id == 44
    backup = path.with_suffix(path.suffix + ".telegram-card-backup")
    assert backup.read_bytes() == path.read_bytes()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_apply_legacy_migration_refuses_missing_durable_event(tmp_path):
    path = tmp_path / "legacy.json"
    _legacy(path, {"ticket_id": "INC-1", "status": "sent", "root_message_ids": [44]})

    report = apply_legacy_migration(EventStore(tmp_path / "events.sqlite3"), path=path, chat_ids=("123",))

    assert report.ready is False
    assert report.refusals == ("event-1: durable event is missing",)