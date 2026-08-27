"""Fail-closed, framework-free Telegram callback authorization and workflows."""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ._persist import _SECURE_FILE_MODE, _secure_mkdir
from .event_store import EventStore, EventStoreError, EventTransitionError

__all__ = [
    "OperatorPolicy",
    "OperatorWorkflow",
    "TelegramOperatorError",
    "load_operator_policy",
    "parse_callback_update",
]

NETTOOLS_TELEGRAM_WEBHOOK_SECRET_ENV = "NETTOOLS_TELEGRAM_WEBHOOK_SECRET"
NETTOOLS_OPERATOR_POLICY_FILE_ENV = "NETTOOLS_OPERATOR_POLICY_FILE"
NETTOOLS_TELEGRAM_WEBHOOK_ENABLED_ENV = "NETTOOLS_TELEGRAM_WEBHOOK_ENABLED"
NETTOOLS_OPERATOR_AUDIT_LOG_ENV = "NETTOOLS_OPERATOR_AUDIT_LOG"

_ROLE_ACTIONS = {
    "viewer": frozenset({"evidence", "timeline"}),
    "operator": frozenset({"evidence", "timeline", "acknowledge", "silence"}),
    "incident_manager": frozenset({"evidence", "timeline", "acknowledge", "silence", "retry"}),
}


class TelegramOperatorError(ValueError):
    """A callback or operator policy cannot satisfy the authorization contract."""


@dataclass(frozen=True)
class OperatorPolicy:
    user_id: int
    role: str
    enabled: bool

    def permits(self, action: str) -> bool:
        return self.enabled and action in _ROLE_ACTIONS.get(self.role, frozenset())


@dataclass(frozen=True)
class Callback:
    callback_id: str
    user_id: int
    action: str
    event_id: str


def load_operator_policy(path: Path) -> dict[int, OperatorPolicy]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise TelegramOperatorError(f"operator policy is unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("operators"), list):
        raise TelegramOperatorError("operator policy must be version 1 with an operators list")
    policies: dict[int, OperatorPolicy] = {}
    for item in data["operators"]:
        if not isinstance(item, dict):
            raise TelegramOperatorError("operator policy entries must be objects")
        user_id, role, enabled = item.get("telegram_user_id"), item.get("role"), item.get("enabled")
        if not isinstance(user_id, int) or user_id <= 0 or role not in _ROLE_ACTIONS or not isinstance(enabled, bool):
            raise TelegramOperatorError("operator policy entry is invalid")
        if user_id in policies:
            raise TelegramOperatorError("operator policy declares a Telegram user twice")
        policies[user_id] = OperatorPolicy(user_id, role, enabled)
    return policies


def parse_callback_update(update: Any, *, supplied_secret: str, expected_secret: str) -> Callback:
    """Verify Telegram secret and parse the only inbound shape this MVP accepts."""

    if not expected_secret or not hmac.compare_digest(supplied_secret, expected_secret):
        raise TelegramOperatorError("Telegram webhook secret is invalid")
    if not isinstance(update, dict):
        raise TelegramOperatorError("Telegram update must be an object")
    query = update.get("callback_query")
    if not isinstance(query, dict):
        raise TelegramOperatorError("only callback_query updates are supported")
    callback_id = query.get("id")
    user_id = (query.get("from") or {}).get("id") if isinstance(query.get("from"), dict) else None
    data = query.get("data")
    if not isinstance(callback_id, str) or not callback_id or not isinstance(user_id, int) or user_id <= 0:
        raise TelegramOperatorError("callback identity is invalid")
    if not isinstance(data, str) or len(data) > 160:
        raise TelegramOperatorError("callback payload is invalid")
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "v1" or parts[1] not in {"evidence", "timeline", "acknowledge", "silence", "retry"}:
        raise TelegramOperatorError("callback action is unsupported")
    event_id = parts[2]
    if not event_id or any(char not in "0123456789abcdef" for char in event_id):
        raise TelegramOperatorError("callback event identity is invalid")
    return Callback(callback_id, user_id, parts[1], event_id)


class OperatorWorkflow:
    """Bounded action service; it never calls a model, MCP, or network device."""

    def __init__(self, store: EventStore, *, policies: dict[int, OperatorPolicy], audit_path: Path) -> None:
        self.store = store
        self.policies = policies
        self.audit_path = audit_path

    def handle(self, callback: Callback) -> dict[str, Any]:
        prior = self.store.operator_callback_result(callback.callback_id)
        if prior is not None:
            return prior.result
        policy = self.policies.get(callback.user_id)
        if policy is None or not policy.permits(callback.action):
            return self._record(callback, policy, "refused", "operator is not authorized")
        event = self.store.get(callback.event_id)
        if event is None:
            return self._record(callback, policy, "refused", "event does not exist")
        if callback.action == "retry":
            if event.state != "retryable_failed":
                return self._record(callback, policy, "refused", "event is not retryable")
            try:
                self.store.transition(event.event_id, "admitted", reason="operator retry requested")
            except (EventStoreError, EventTransitionError) as exc:
                return self._record(callback, policy, "refused", f"retry refused: {type(exc).__name__}")
            return self._record(callback, policy, "accepted", "event readmitted")
        if callback.action == "silence":
            return self._record(callback, policy, "input_required", "silence requires reason and expiry")
        if callback.action == "evidence":
            return self._record(callback, policy, "accepted", "evidence view requested")
        if callback.action == "timeline":
            return self._record(callback, policy, "accepted", "timeline view requested")
        return self._record(callback, policy, "accepted", "incident acknowledged")

    def _record(self, callback: Callback, policy: OperatorPolicy | None, outcome: str, detail: str) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "callback_id": callback.callback_id,
            "event_id": callback.event_id,
            "actor_user_id": callback.user_id,
            "role": policy.role if policy is not None else None,
            "action": callback.action,
            "outcome": outcome,
            "detail": detail,
        }
        _secure_mkdir(self.audit_path.parent)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        os.chmod(self.audit_path, _SECURE_FILE_MODE)
        return self.store.record_operator_callback(
            callback_id=callback.callback_id,
            event_id=callback.event_id,
            result=record,
        ).result


def handle_configured_update(update: Any, *, supplied_secret: str, store: EventStore) -> dict[str, Any]:
    """Environment-backed local webhook adapter; HTTP transport stays separate."""

    if os.getenv(NETTOOLS_TELEGRAM_WEBHOOK_ENABLED_ENV, "0").strip().lower() not in {"1", "true", "yes", "on"}:
        raise TelegramOperatorError("Telegram webhook handling is disabled")
    expected = os.getenv(NETTOOLS_TELEGRAM_WEBHOOK_SECRET_ENV, "")
    policy_path = os.getenv(NETTOOLS_OPERATOR_POLICY_FILE_ENV, "")
    audit_path = os.getenv(NETTOOLS_OPERATOR_AUDIT_LOG_ENV, "")
    if not expected or not policy_path or not audit_path:
        raise TelegramOperatorError("Telegram operator webhook configuration is incomplete")
    callback = parse_callback_update(update, supplied_secret=supplied_secret, expected_secret=expected)
    return OperatorWorkflow(
        store,
        policies=load_operator_policy(Path(policy_path).expanduser()),
        audit_path=Path(audit_path).expanduser(),
    ).handle(callback)