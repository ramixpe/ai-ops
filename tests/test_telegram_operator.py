from __future__ import annotations

import json

import pytest

from agent_nettools.event_store import EventStore
from agent_nettools.telegram_operator import (
    OperatorWorkflow,
    TelegramOperatorError,
    handle_configured_update,
    load_operator_policy,
    parse_callback_update,
)


def _update(
    *, action: str = "acknowledge", event_id: str = "abc123", user_id: int = 42, callback_id: str = "cb-1"
) -> dict:
    return {"callback_query": {"id": callback_id, "from": {"id": user_id}, "data": f"v1:{action}:{event_id}"}}


def _policy(tmp_path, role: str = "incident_manager"):
    path = tmp_path / "operators.yaml"
    path.write_text(f"version: 1\noperators:\n  - telegram_user_id: 42\n    role: {role}\n    enabled: true\n")
    return load_operator_policy(path)


def test_callback_parser_fails_closed_on_invalid_secret_or_action():
    with pytest.raises(TelegramOperatorError, match="secret"):
        parse_callback_update(_update(), supplied_secret="bad", expected_secret="expected")
    with pytest.raises(TelegramOperatorError, match="unsupported"):
        parse_callback_update(_update(action="delete"), supplied_secret="expected", expected_secret="expected")


def test_retry_requires_authorized_operator_and_retryable_event(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="abc123", device="PE2", payload={})
    store.transition("abc123", "admitted")
    store.transition("abc123", "running")
    store.transition("abc123", "retryable_failed")
    workflow = OperatorWorkflow(store, policies=_policy(tmp_path), audit_path=tmp_path / "audit.jsonl")
    callback = parse_callback_update(_update(action="retry"), supplied_secret="secret", expected_secret="secret")

    result = workflow.handle(callback)

    assert result["outcome"] == "accepted"
    assert store.get("abc123").state == "admitted"
    assert json.loads((tmp_path / "audit.jsonl").read_text())["action"] == "retry"


def test_viewer_cannot_acknowledge_and_silence_requires_input(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="abc123", device="PE2", payload={})
    viewer = OperatorWorkflow(store, policies=_policy(tmp_path, "viewer"), audit_path=tmp_path / "audit.jsonl")
    acknowledged = viewer.handle(parse_callback_update(_update(), supplied_secret="s", expected_secret="s"))
    operator = OperatorWorkflow(store, policies=_policy(tmp_path, "operator"), audit_path=tmp_path / "audit.jsonl")
    silence = operator.handle(
        parse_callback_update(_update(action="silence", callback_id="cb-2"), supplied_secret="s", expected_secret="s")
    )

    assert acknowledged["outcome"] == "refused"
    assert silence["outcome"] == "input_required"


def test_duplicate_callback_returns_recorded_result_without_repeating_retry(tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="abc123", device="PE2", payload={})
    store.transition("abc123", "admitted")
    store.transition("abc123", "running")
    store.transition("abc123", "retryable_failed")
    workflow = OperatorWorkflow(store, policies=_policy(tmp_path), audit_path=tmp_path / "audit.jsonl")
    callback = parse_callback_update(_update(action="retry"), supplied_secret="s", expected_secret="s")

    first = workflow.handle(callback)
    second = workflow.handle(callback)

    assert first == second
    assert store.get("abc123").state == "admitted"
    assert len((tmp_path / "audit.jsonl").read_text().splitlines()) == 1


def test_configured_handler_requires_enabled_webhook_and_local_policy(monkeypatch, tmp_path):
    store = EventStore(tmp_path / "events.sqlite3")
    store.create_or_get(event_id="abc123", device="PE2", payload={})
    monkeypatch.setenv("NETTOOLS_TELEGRAM_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("NETTOOLS_TELEGRAM_WEBHOOK_SECRET", "secret")
    policy_path = tmp_path / "operators.yaml"
    policy_path.write_text("version: 1\noperators:\n  - telegram_user_id: 42\n    role: operator\n    enabled: true\n")
    monkeypatch.setenv("NETTOOLS_OPERATOR_POLICY_FILE", str(policy_path))
    monkeypatch.setenv("NETTOOLS_OPERATOR_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    result = handle_configured_update(_update(action="acknowledge"), supplied_secret="secret", store=store)

    assert result["outcome"] == "accepted"