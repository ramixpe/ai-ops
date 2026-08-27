"""Versioned single-host event, incident, and notification transaction store.

The Version-2 event agent and Telegram presentation path use this repository
for durable lifecycle state.  SQLite stays behind the domain methods here so
workers never need to coordinate with ad-hoc SQL.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from ._persist import _SECURE_FILE_MODE, _secure_mkdir
from .incident_case import IncidentIdentity, IncidentState

__all__ = [
    "DEFAULT_EVENT_DB_PATH",
    "NETTOOLS_EVENT_DB_PATH_ENV",
    "EventRecord",
    "EventAttempt",
    "EventStore",
    "EventStoreError",
    "EventLeaseError",
    "IncidentRecord",
    "NarrowingShadowRecord",
    "ProcedureApprovalRecord",
    "ProcedureSimulationRecord",
    "OutboxRecord",
    "TelegramCardReceipt",
    "TelegramCardState",
    "OperatorCallbackResult",
    "RetryRecord",
    "AdmissionShadowRecord",
    "EventTransitionError",
    "event_db_path",
]

NETTOOLS_EVENT_DB_PATH_ENV = "NETTOOLS_EVENT_DB_PATH"
DEFAULT_EVENT_DB_PATH = "~/.local/state/agent-nettools/events.sqlite3"
SCHEMA_VERSION = 5

EventState = Literal[
    "received",
    "admitted",
    "running",
    "completed",
    "retryable_failed",
    "terminal_failed",
    "dead_letter",
]

_ALLOWED_TRANSITIONS: dict[EventState, frozenset[EventState]] = {
    "received": frozenset({"admitted", "terminal_failed"}),
    "admitted": frozenset({"running", "retryable_failed", "terminal_failed"}),
    "running": frozenset({"completed", "retryable_failed", "terminal_failed"}),
    "retryable_failed": frozenset({"admitted", "dead_letter"}),
    "terminal_failed": frozenset({"dead_letter"}),
    "completed": frozenset(),
    "dead_letter": frozenset(),
}


class EventStoreError(RuntimeError):
    """The durable event store cannot satisfy its contract."""


class EventTransitionError(EventStoreError):
    """A requested event state transition is not allowed."""


class EventLeaseError(EventStoreError):
    """An event lease is held by another live worker or cannot be acquired."""


class _ClosingConnection(sqlite3.Connection):
    """Make ``with connection`` close as well as commit or roll back."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool | None:
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    device: str
    state: EventState
    payload: dict[str, Any]
    created_at: str
    updated_at: str
    ticket_id: str | None
    lease_owner: str | None
    lease_expires_at: str | None
    lease_epoch: int
    terminal_reason: str | None
    retry_at: str | None


@dataclass(frozen=True)
class IncidentRecord:
    """Durable incident projection stored alongside event lifecycle state."""

    incident_id: str
    identity: IncidentIdentity
    state: IncidentState
    event_ids: tuple[str, ...]
    opened_at: str
    updated_at: str
    resolved_at: str | None


@dataclass(frozen=True)
class NarrowingShadowRecord:
    """One non-authoritative narrowing observation; SQLite enforces inactive state."""

    event_id: str
    mode: str
    candidates: tuple[dict[str, Any], ...]
    decision: dict[str, Any] | None
    refusal: str | None
    active: bool
    recorded_at: str


@dataclass(frozen=True)
class ProcedureApprovalRecord:
    proposal_digest: str
    procedure: str
    version: int
    parameters: dict[str, str]
    nonce: str
    expires_at: str
    approved_at: str
    consumed_at: str | None


@dataclass(frozen=True)
class ProcedureSimulationRecord:
    proposal_digest: str
    status: str
    network_activity: bool
    device_writes: bool
    verification: str
    recorded_at: str


@dataclass(frozen=True)
class EventAttempt:
    """One immutable lifecycle transition recorded for an event."""

    state: EventState
    recorded_at: str
    reason: str | None


@dataclass(frozen=True)
class OutboxRecord:
    """One idempotent destination-scoped notification delivery intent."""

    outbox_id: int
    event_id: str
    destination: str
    kind: str
    payload: dict[str, Any]
    state: str
    attempts: int
    lease_owner: str | None
    lease_expires_at: str | None
    retry_at: str | None
    created_at: str
    updated_at: str
    last_error: str | None


@dataclass(frozen=True)
class TelegramCardReceipt:
    """The one editable Telegram message owned by an event/chat pair."""

    event_id: str
    chat_id: str
    message_id: int
    render_hash: str
    updated_at: str


@dataclass(frozen=True)
class TelegramCardState:
    """Durable canonical view projection awaiting a rendered card update."""

    event_id: str
    payload: dict[str, Any]
    dirty_at: str | None
    updated_at: str


@dataclass(frozen=True)
class OperatorCallbackResult:
    callback_id: str
    event_id: str
    result: dict[str, Any]
    recorded_at: str


@dataclass(frozen=True)
class RetryRecord:
    event_id: str
    attempt_count: int
    retry_at: str | None
    terminal: bool


@dataclass(frozen=True)
class AdmissionShadowRecord:
    event_id: str
    legacy_admitted: bool
    legacy_reason: str | None
    durable_state: str | None
    recorded_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EventStoreError(f"invalid event timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise EventStoreError(f"event timestamp {value!r} has no timezone")
    return parsed


def _require_aware(value: datetime | None, *, label: str) -> datetime:
    """Return a timezone-aware timestamp or fail with one store-level error."""

    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise EventStoreError(f"{label} must be timezone-aware")
    return current


def _require_row(row: sqlite3.Row | None, *, context: str) -> sqlite3.Row:
    """Replace optimization-sensitive assertions on required SQL results."""

    if row is None:
        raise EventStoreError(f"event store lost {context} after writing it")
    return row


def event_db_path() -> Path:
    configured = os.getenv(NETTOOLS_EVENT_DB_PATH_ENV, DEFAULT_EVENT_DB_PATH)
    return Path(configured).expanduser()


class EventStore:
    """SQLite/WAL repository for one-host event transaction state.

    Each mutation uses ``BEGIN IMMEDIATE`` so duplicate creation and lease
    acquisition serialize across worker processes. SQLite remains behind this
    class so later storage can retain the same domain contract.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or event_db_path()
        try:
            _secure_mkdir(self.path.parent)
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            raise EventStoreError(f"could not initialize event store at {self.path}: {exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        self._secure_sqlite_files()
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a transactional connection and always close its descriptor."""

        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _secure_sqlite_files(self) -> None:
        """Keep the database and any WAL/SHM sidecars owner-readable only."""

        for path in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if path.exists():
                os.chmod(path, _SECURE_FILE_MODE)

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            newest_version = connection.execute(
                "SELECT MAX(version) AS version FROM schema_migrations"
            ).fetchone()["version"]
            if newest_version is not None and newest_version > SCHEMA_VERSION:
                raise EventStoreError(
                    f"event store has newer schema version {newest_version}; "
                    f"this binary supports through {SCHEMA_VERSION}"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    device TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ticket_id TEXT,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    terminal_reason TEXT,
                    retry_at TEXT
                );
                CREATE TABLE IF NOT EXISTS event_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    state TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    reason TEXT
                );
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    destination TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    UNIQUE(event_id, destination, kind)
                );
                CREATE TABLE IF NOT EXISTS telegram_card_receipts (
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    render_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(event_id, chat_id)
                );
                CREATE TABLE IF NOT EXISTS telegram_card_states (
                    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
                    payload_json TEXT NOT NULL,
                    dirty_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operator_callbacks (
                    callback_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admission_shadow (
                    shadow_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL REFERENCES events(event_id),
                    legacy_admitted INTEGER NOT NULL,
                    legacy_reason TEXT,
                    durable_state TEXT,
                    recorded_at TEXT NOT NULL
                );
                """
            )
            if newest_version is None or newest_version < 2:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS incidents (
                        incident_id TEXT PRIMARY KEY,
                        device TEXT NOT NULL,
                        cause_rung TEXT NOT NULL,
                        cause_subject TEXT NOT NULL,
                        state TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        resolved_at TEXT
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_incidents_active_identity
                    ON incidents(device, cause_rung, cause_subject)
                    WHERE state IN ('open', 'acknowledged');
                    CREATE INDEX IF NOT EXISTS idx_incidents_state_updated
                    ON incidents(state, updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_incidents_device ON incidents(device);
                    CREATE TABLE IF NOT EXISTS incident_events (
                        incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                        event_id TEXT NOT NULL REFERENCES events(event_id),
                        joined_at TEXT NOT NULL,
                        PRIMARY KEY(incident_id, event_id),
                        UNIQUE(event_id)
                    );
                    CREATE TABLE IF NOT EXISTS incident_transitions (
                        transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
                        state TEXT NOT NULL,
                        recorded_at TEXT NOT NULL,
                        reason TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_incident_transitions_incident
                    ON incident_transitions(incident_id, transition_id);
                    """
                )
            if newest_version is None or newest_version < 3:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS narrowing_shadow_records (
                        record_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL REFERENCES events(event_id),
                        mode TEXT NOT NULL CHECK(mode = 'shadow'),
                        candidates_json TEXT NOT NULL,
                        decision_json TEXT,
                        refusal TEXT,
                        active INTEGER NOT NULL DEFAULT 0 CHECK(active = 0),
                        recorded_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_narrowing_shadow_event
                    ON narrowing_shadow_records(event_id, record_id);
                    """
                )
            if newest_version is None or newest_version < 4:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS procedure_approvals (
                        proposal_digest TEXT PRIMARY KEY,
                        procedure TEXT NOT NULL,
                        version INTEGER NOT NULL,
                        parameters_json TEXT NOT NULL,
                        nonce TEXT NOT NULL UNIQUE,
                        expires_at TEXT NOT NULL,
                        signature TEXT NOT NULL,
                        approved_at TEXT NOT NULL,
                        consumed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_procedure_approvals_expiry
                    ON procedure_approvals(expires_at);
                    CREATE TABLE IF NOT EXISTS procedure_simulations (
                        simulation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        proposal_digest TEXT NOT NULL REFERENCES procedure_approvals(proposal_digest),
                        status TEXT NOT NULL CHECK(status = 'not_executed'),
                        network_activity INTEGER NOT NULL CHECK(network_activity = 0),
                        device_writes INTEGER NOT NULL CHECK(device_writes = 0),
                        verification TEXT NOT NULL CHECK(verification = 'not_executed'),
                        recorded_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_procedure_simulations_proposal
                    ON procedure_simulations(proposal_digest, simulation_id);
                    """
                )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(events)")}
            if "retry_at" not in columns:
                connection.execute("ALTER TABLE events ADD COLUMN retry_at TEXT")
            if newest_version is None or newest_version < 5:
                event_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(events)")
                }
                if "lease_epoch" not in event_columns:
                    connection.execute(
                        "ALTER TABLE events ADD COLUMN lease_epoch INTEGER NOT NULL DEFAULT 0"
                    )
                outbox_columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(notification_outbox)")
                }
                if "retry_at" not in outbox_columns:
                    connection.execute("ALTER TABLE notification_outbox ADD COLUMN retry_at TEXT")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_events_recovery "
                    "ON events(state, retry_at, lease_expires_at)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outbox_delivery "
                    "ON notification_outbox(state, retry_at, lease_expires_at, outbox_id)"
                )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _now()),
            )
        self._secure_sqlite_files()

    @staticmethod
    def _record(row: sqlite3.Row) -> EventRecord:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise EventStoreError(f"event {row['event_id']} has a non-object payload")
        return EventRecord(
            event_id=row["event_id"],
            device=row["device"],
            state=row["state"],
            payload=payload,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            ticket_id=row["ticket_id"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            lease_epoch=row["lease_epoch"],
            terminal_reason=row["terminal_reason"],
            retry_at=row["retry_at"],
        )

    def get(self, event_id: str) -> EventRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return self._record(row) if row is not None else None

    @staticmethod
    def _incident_record(connection: sqlite3.Connection, row: sqlite3.Row) -> IncidentRecord:
        event_rows = connection.execute(
            "SELECT event_id FROM incident_events WHERE incident_id = ? ORDER BY joined_at, event_id",
            (row["incident_id"],),
        ).fetchall()
        return IncidentRecord(
            incident_id=row["incident_id"],
            identity=IncidentIdentity(row["device"], row["cause_rung"], row["cause_subject"]),
            state=IncidentState(row["state"]),
            event_ids=tuple(item["event_id"] for item in event_rows),
            opened_at=row["opened_at"],
            updated_at=row["updated_at"],
            resolved_at=row["resolved_at"],
        )

    def create_or_join_incident(
        self,
        *,
        identity: IncidentIdentity,
        event_id: str,
        now: datetime | None = None,
    ) -> tuple[IncidentRecord, bool]:
        """Atomically create an active exact-cause incident or join it to one event."""

        current = _require_aware(now, label="incident timestamp")
        timestamp = current.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = connection.execute("SELECT event_id FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if event is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"event {event_id!r} does not exist")
            existing = connection.execute(
                """
                SELECT * FROM incidents
                WHERE device = ? AND cause_rung = ? AND cause_subject = ?
                  AND state IN ('open', 'acknowledged')
                """,
                (identity.device, identity.cause_rung, identity.cause_subject),
            ).fetchone()
            created = existing is None
            if existing is None:
                incident_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO incidents(incident_id, device, cause_rung, cause_subject, state, opened_at, updated_at)
                    VALUES (?, ?, ?, ?, 'open', ?, ?)
                    """,
                    (incident_id, identity.device, identity.cause_rung, identity.cause_subject, timestamp, timestamp),
                )
                connection.execute(
                    "INSERT INTO incident_transitions(incident_id, state, recorded_at, reason) VALUES (?, 'open', ?, ?)",
                    (incident_id, timestamp, "incident created"),
                )
            else:
                incident_id = existing["incident_id"]
            prior = connection.execute("SELECT incident_id FROM incident_events WHERE event_id = ?", (event_id,)).fetchone()
            if prior is not None and prior["incident_id"] != incident_id:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"event {event_id!r} already belongs to another incident")
            connection.execute(
                "INSERT OR IGNORE INTO incident_events(incident_id, event_id, joined_at) VALUES (?, ?, ?)",
                (incident_id, event_id, timestamp),
            )
            connection.execute("UPDATE incidents SET updated_at = ? WHERE incident_id = ?", (timestamp, incident_id))
            row = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
            connection.execute("COMMIT")
            return self._incident_record(
                connection,
                _require_row(row, context=f"incident {incident_id!r}"),
            ), created

    def record_narrowing_shadow(
        self,
        *,
        event_id: str,
        mode: str,
        candidates: tuple[dict[str, Any], ...],
        decision: dict[str, Any] | None,
        refusal: str | None,
        now: datetime | None = None,
    ) -> NarrowingShadowRecord:
        """Persist a contained shadow record; active narrowing is unrepresentable."""

        if mode != "shadow":
            raise EventStoreError("only shadow narrowing mode can be persisted")
        current = _require_aware(now, label="narrowing timestamp")
        timestamp = current.isoformat()
        encoded_candidates = json.dumps(candidates, sort_keys=True)
        encoded_decision = json.dumps(decision, sort_keys=True) if decision is not None else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = connection.execute("SELECT event_id FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if event is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"event {event_id!r} does not exist")
            connection.execute(
                """
                INSERT INTO narrowing_shadow_records(event_id, mode, candidates_json, decision_json, refusal, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, mode, encoded_candidates, encoded_decision, refusal, timestamp),
            )
            row = connection.execute(
                "SELECT * FROM narrowing_shadow_records WHERE record_id = last_insert_rowid()"
            ).fetchone()
            connection.execute("COMMIT")
        return self._narrowing_shadow_record(
            _require_row(row, context=f"narrowing record for {event_id!r}")
        )

    def narrowing_shadow_records(self, *, event_id: str) -> tuple[NarrowingShadowRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM narrowing_shadow_records WHERE event_id = ? ORDER BY record_id", (event_id,)
            ).fetchall()
        return tuple(self._narrowing_shadow_record(row) for row in rows)

    @staticmethod
    def _narrowing_shadow_record(row: sqlite3.Row) -> NarrowingShadowRecord:
        candidates = json.loads(row["candidates_json"])
        decision = json.loads(row["decision_json"]) if row["decision_json"] is not None else None
        if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
            raise EventStoreError(f"narrowing shadow for {row['event_id']!r} has invalid candidates")
        if decision is not None and not isinstance(decision, dict):
            raise EventStoreError(f"narrowing shadow for {row['event_id']!r} has invalid decision")
        return NarrowingShadowRecord(
            event_id=row["event_id"],
            mode=row["mode"],
            candidates=tuple(candidates),
            decision=decision,
            refusal=row["refusal"],
            active=bool(row["active"]),
            recorded_at=row["recorded_at"],
        )

    def transition_incident(
        self,
        incident_id: str,
        state: IncidentState,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> IncidentRecord:
        """Record a monotonic durable incident state transition."""

        allowed = {
            IncidentState.OPEN: {IncidentState.ACKNOWLEDGED, IncidentState.RESOLVED},
            IncidentState.ACKNOWLEDGED: {IncidentState.RESOLVED},
            IncidentState.RESOLVED: {IncidentState.ARCHIVED},
            IncidentState.ARCHIVED: set(),
        }
        current = _require_aware(now, label="incident transition timestamp")
        timestamp = current.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"incident {incident_id!r} does not exist")
            previous = IncidentState(row["state"])
            if state not in allowed[previous]:
                connection.execute("ROLLBACK")
                raise EventTransitionError(f"cannot transition incident {incident_id} from {previous} to {state}")
            resolved_at = timestamp if state is IncidentState.RESOLVED else row["resolved_at"]
            connection.execute(
                "UPDATE incidents SET state = ?, updated_at = ?, resolved_at = ? WHERE incident_id = ?",
                (state.value, timestamp, resolved_at, incident_id),
            )
            connection.execute(
                "INSERT INTO incident_transitions(incident_id, state, recorded_at, reason) VALUES (?, ?, ?, ?)",
                (incident_id, state.value, timestamp, reason),
            )
            updated = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
            connection.execute("COMMIT")
            return self._incident_record(
                connection,
                _require_row(updated, context=f"incident {incident_id!r}"),
            )

    def incident(self, incident_id: str) -> IncidentRecord | None:
        """Return one incident with event linkage, or ``None`` when absent."""

        with self._connect() as connection:
            row = connection.execute("SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)).fetchone()
            return self._incident_record(connection, row) if row is not None else None

    def record_procedure_approval(
        self,
        *,
        proposal_digest: str,
        procedure: str,
        version: int,
        parameters: dict[str, str],
        nonce: str,
        expires_at: datetime,
        signature: str,
        now: datetime | None = None,
    ) -> ProcedureApprovalRecord:
        """Durably record an approval receipt without persisting its secret."""

        current = _require_aware(now, label="procedure approval timestamp")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise EventStoreError("procedure approval timestamps must be timezone-aware")
        if not all((proposal_digest, procedure, nonce, signature)) or version < 1:
            raise EventStoreError("procedure approval identity is invalid")
        encoded_parameters = json.dumps(parameters, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO procedure_approvals(
                        proposal_digest, procedure, version, parameters_json, nonce,
                        expires_at, signature, approved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        proposal_digest, procedure, version, encoded_parameters, nonce,
                        expires_at.isoformat(), signature, current.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                raise EventStoreError("procedure approval receipt already exists") from exc
            row = connection.execute(
                "SELECT * FROM procedure_approvals WHERE proposal_digest = ?", (proposal_digest,)
            ).fetchone()
            connection.execute("COMMIT")
        return self._procedure_approval_record(
            _require_row(row, context=f"procedure approval {proposal_digest!r}")
        )

    def consume_procedure_approval(
        self,
        *,
        proposal_digest: str,
        nonce: str,
        now: datetime | None = None,
    ) -> ProcedureApprovalRecord:
        """Atomically consume one valid durable approval nonce exactly once."""

        current = _require_aware(now, label="procedure approval timestamp")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM procedure_approvals WHERE proposal_digest = ? AND nonce = ?",
                (proposal_digest, nonce),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventStoreError("procedure approval receipt does not exist")
            if row["consumed_at"] is not None:
                connection.execute("ROLLBACK")
                raise EventStoreError("procedure approval nonce is replayed")
            if _parse_time(row["expires_at"]) <= current:
                connection.execute("ROLLBACK")
                raise EventStoreError("procedure approval receipt is expired")
            connection.execute(
                "UPDATE procedure_approvals SET consumed_at = ? WHERE proposal_digest = ?",
                (current.isoformat(), proposal_digest),
            )
            updated = connection.execute(
                "SELECT * FROM procedure_approvals WHERE proposal_digest = ?", (proposal_digest,)
            ).fetchone()
            connection.execute("COMMIT")
        return self._procedure_approval_record(
            _require_row(updated, context=f"procedure approval {proposal_digest!r}")
        )

    def record_procedure_simulation(
        self,
        *,
        proposal_digest: str,
        status: str,
        network_activity: bool,
        device_writes: bool,
        verification: str,
        now: datetime | None = None,
    ) -> ProcedureSimulationRecord:
        """Record a dry-run result; database constraints make execution impossible."""

        current = _require_aware(now, label="procedure simulation timestamp")
        if status != "not_executed" or network_activity or device_writes or verification != "not_executed":
            raise EventStoreError("procedure simulation violates dry-run contract")
        with self._connect() as connection:
            approval = connection.execute(
                "SELECT proposal_digest FROM procedure_approvals WHERE proposal_digest = ?",
                (proposal_digest,),
            ).fetchone()
            if approval is None:
                raise EventStoreError("procedure simulation approval does not exist")
            try:
                connection.execute(
                    """
                    INSERT INTO procedure_simulations(
                        proposal_digest, status, network_activity, device_writes, verification, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (proposal_digest, status, int(network_activity), int(device_writes), verification, current.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise EventStoreError("procedure simulation violates dry-run contract") from exc
            row = connection.execute("SELECT * FROM procedure_simulations WHERE simulation_id = last_insert_rowid()").fetchone()
        return self._procedure_simulation_record(
            _require_row(row, context=f"procedure simulation for {proposal_digest!r}")
        )

    @staticmethod
    def _procedure_approval_record(row: sqlite3.Row) -> ProcedureApprovalRecord:
        parameters = json.loads(row["parameters_json"])
        if not isinstance(parameters, dict) or not all(isinstance(value, str) for value in parameters.values()):
            raise EventStoreError("procedure approval has invalid parameters")
        return ProcedureApprovalRecord(
            proposal_digest=row["proposal_digest"], procedure=row["procedure"], version=row["version"],
            parameters=parameters, nonce=row["nonce"], expires_at=row["expires_at"],
            approved_at=row["approved_at"], consumed_at=row["consumed_at"],
        )

    @staticmethod
    def _procedure_simulation_record(row: sqlite3.Row) -> ProcedureSimulationRecord:
        return ProcedureSimulationRecord(
            proposal_digest=row["proposal_digest"], status=row["status"],
            network_activity=bool(row["network_activity"]), device_writes=bool(row["device_writes"]),
            verification=row["verification"], recorded_at=row["recorded_at"],
        )

    def list_incidents(
        self,
        *,
        state: IncidentState | None = None,
        device: str | None = None,
        limit: int = 100,
    ) -> tuple[IncidentRecord, ...]:
        """List bounded incident projections without event payload access."""

        if limit < 1:
            return ()
        clauses: list[str] = []
        parameters: list[Any] = []
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state.value)
        if device is not None:
            clauses.append("device = ?")
            parameters.append(device)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM incidents{where} ORDER BY updated_at DESC, incident_id DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
            return tuple(self._incident_record(connection, row) for row in rows)

    def list_events(
        self,
        *,
        state: EventState | None = None,
        device: str | None = None,
        limit: int = 100,
    ) -> tuple[EventRecord, ...]:
        """Return a bounded, newest-first event list for read-only operators."""

        if not isinstance(limit, int) or limit < 0 or limit > 1_000:
            raise EventStoreError("event list limit must be an integer from 0 through 1000")
        clauses: list[str] = []
        parameters: list[str | int] = []
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state)
        if device is not None:
            clauses.append("device = ?")
            parameters.append(device)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM events{where} ORDER BY updated_at DESC, event_id ASC LIMIT ?",  # noqa: S608
                parameters,
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def event_attempts(self, *, event_id: str) -> tuple[EventAttempt, ...]:
        """Return an event's immutable lifecycle transitions in recorded order."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state, recorded_at, reason FROM event_attempts WHERE event_id = ? ORDER BY attempt_id",
                (event_id,),
            ).fetchall()
        return tuple(EventAttempt(row["state"], row["recorded_at"], row["reason"]) for row in rows)

    def outbox_records(self, *, event_id: str) -> tuple[OutboxRecord, ...]:
        """Return delivery lifecycle records for one event without claiming delivery success."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notification_outbox WHERE event_id = ? ORDER BY outbox_id", (event_id,)
            ).fetchall()
        return tuple(self._outbox_record(row) for row in rows)

    def telegram_card_receipts(self, *, event_id: str) -> tuple[TelegramCardReceipt, ...]:
        """Return editable-card delivery receipts for one event."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM telegram_card_receipts WHERE event_id = ? ORDER BY chat_id", (event_id,)
            ).fetchall()
        return tuple(self._telegram_card_receipt(row) for row in rows)

    def record_admission_shadow(
        self, *, event_id: str, legacy_admitted: bool, legacy_reason: str | None
    ) -> AdmissionShadowRecord:
        current = self.get(event_id)
        if current is None:
            raise EventStoreError(f"event {event_id!r} does not exist")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admission_shadow(event_id, legacy_admitted, legacy_reason, durable_state, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, int(legacy_admitted), legacy_reason, current.state, now),
            )
        return AdmissionShadowRecord(event_id, legacy_admitted, legacy_reason, current.state, now)

    def admission_shadow_records(self, *, event_id: str) -> tuple[AdmissionShadowRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM admission_shadow WHERE event_id = ? ORDER BY shadow_id", (event_id,)
            ).fetchall()
        return tuple(
            AdmissionShadowRecord(
                event_id=row["event_id"],
                legacy_admitted=bool(row["legacy_admitted"]),
                legacy_reason=row["legacy_reason"],
                durable_state=row["durable_state"],
                recorded_at=row["recorded_at"],
            )
            for row in rows
        )

    def health_snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        """A bounded operational summary for shadow-parity and worker health."""

        current = _require_aware(now, label="health snapshot timestamp")
        with self._connect() as connection:
            event_rows = connection.execute("SELECT state, COUNT(*) AS count FROM events GROUP BY state").fetchall()
            outbox_rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM notification_outbox GROUP BY state"
            ).fetchall()
            shadow_rows = connection.execute(
                "SELECT legacy_admitted, COUNT(*) AS count FROM admission_shadow GROUP BY legacy_admitted"
            ).fetchall()
            oldest_retry = connection.execute(
                "SELECT MIN(retry_at) AS retry_at FROM events WHERE state = 'retryable_failed'"
            ).fetchone()["retry_at"]
            oldest_expired_lease = connection.execute(
                "SELECT MIN(lease_expires_at) AS lease_expires_at FROM events "
                "WHERE state = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                (current.isoformat(),),
            ).fetchone()["lease_expires_at"]
        event_counts = {row["state"]: row["count"] for row in event_rows}
        outbox_counts = {row["state"]: row["count"] for row in outbox_rows}
        shadows = {"legacy_admitted": 0, "legacy_refused": 0}
        for row in shadow_rows:
            key = "legacy_admitted" if row["legacy_admitted"] else "legacy_refused"
            shadows[key] = row["count"]
        retry_age_seconds = None
        if isinstance(oldest_retry, str):
            retry_age_seconds = max(0.0, (current - _parse_time(oldest_retry)).total_seconds())
        expired_lease_age_seconds = None
        if isinstance(oldest_expired_lease, str):
            expired_lease_age_seconds = max(
                0.0,
                (current - _parse_time(oldest_expired_lease)).total_seconds(),
            )
        return {
            "events": event_counts,
            "outbox": outbox_counts,
            "admission_shadow": shadows,
            "oldest_retry_at": oldest_retry,
            "oldest_retry_age_seconds": retry_age_seconds,
            "oldest_expired_lease_at": oldest_expired_lease,
            "oldest_expired_lease_age_seconds": expired_lease_age_seconds,
            "dead_letter_count": event_counts.get("dead_letter", 0),
            "outbox_dead_letter_count": outbox_counts.get("dead_letter", 0),
        }

    def create_or_get(
        self,
        *,
        event_id: str,
        device: str,
        payload: dict[str, Any],
        strict: bool = False,
    ) -> tuple[EventRecord, bool]:
        """Atomically create ``received`` or return an existing event.

        ``strict=True`` detects identity collisions. The compatibility default
        permits transports whose duplicate envelope contains a new receipt
        timestamp. Call :meth:`create_or_update` when a domain path
        intentionally owns a mutable projection.
        """

        now = _now()
        serialized_payload = json.dumps(payload, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is not None:
                existing = self._record(row)
                if strict and (existing.device != device or existing.payload != payload):
                    connection.execute("ROLLBACK")
                    raise EventStoreError(
                        f"event {event_id!r} was reused with different content"
                    )
                connection.execute("COMMIT")
                return existing, False
            connection.execute(
                """
                INSERT INTO events(event_id, device, state, payload_json, created_at, updated_at)
                VALUES (?, ?, 'received', ?, ?, ?)
                """,
                (event_id, device, serialized_payload, now, now),
            )
            connection.execute(
                "INSERT INTO event_attempts(event_id, state, recorded_at) VALUES (?, 'received', ?)",
                (event_id, now),
            )
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            connection.execute("COMMIT")
        return self._record(_require_row(row, context=f"event {event_id!r}")), True

    def create_or_update(
        self,
        *,
        event_id: str,
        device: str,
        payload: dict[str, Any],
    ) -> tuple[EventRecord, bool]:
        """Create an event or explicitly refresh its owned mutable payload."""

        now = _now()
        serialized_payload = json.dumps(payload, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO events(event_id, device, state, payload_json, created_at, updated_at)
                    VALUES (?, ?, 'received', ?, ?, ?)
                    """,
                    (event_id, device, serialized_payload, now, now),
                )
                connection.execute(
                    "INSERT INTO event_attempts(event_id, state, recorded_at) VALUES (?, 'received', ?)",
                    (event_id, now),
                )
                created = True
            else:
                if row["device"] != device:
                    connection.execute("ROLLBACK")
                    raise EventStoreError(
                        f"event {event_id!r} cannot move from device {row['device']!r} to {device!r}"
                    )
                if row["state"] != "received":
                    connection.execute("ROLLBACK")
                    raise EventStoreError(
                        f"event {event_id!r} payload is immutable in state {row['state']!r}"
                    )
                connection.execute(
                    "UPDATE events SET payload_json = ?, updated_at = ? WHERE event_id = ?",
                    (serialized_payload, now, event_id),
                )
                created = False
            updated = connection.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            connection.execute("COMMIT")
        return self._record(_require_row(updated, context=f"event {event_id!r}")), created

    @staticmethod
    def _validate_lease_fence(
        row: sqlite3.Row,
        *,
        owner: str | None,
        lease_epoch: int | None,
        now: datetime,
    ) -> None:
        held_by = row["lease_owner"]
        if held_by is None:
            return
        expiry = row["lease_expires_at"]
        if not isinstance(expiry, str) or _parse_time(expiry) <= now:
            raise EventLeaseError(
                f"event {row['event_id']} lease expired and must be reacquired"
            )
        if owner != held_by or lease_epoch != row["lease_epoch"]:
            raise EventLeaseError(
                f"event {row['event_id']} requires lease owner {held_by!r} "
                f"at epoch {row['lease_epoch']}"
            )

    def transition(
        self,
        event_id: str,
        state: EventState,
        *,
        reason: str | None = None,
        ticket_id: str | None = None,
        lease_owner: str | None = None,
        lease_epoch: int | None = None,
        now: datetime | None = None,
    ) -> EventRecord:
        """Validate and record a durable lifecycle transition."""

        current_time = _require_aware(now, label="event transition timestamp")
        now_text = current_time.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"event {event_id!r} does not exist")
            current = row["state"]
            if state not in _ALLOWED_TRANSITIONS[current]:
                connection.execute("ROLLBACK")
                raise EventTransitionError(f"cannot transition event {event_id} from {current} to {state}")
            self._validate_lease_fence(
                row,
                owner=lease_owner,
                lease_epoch=lease_epoch,
                now=current_time,
            )
            connection.execute(
                """
                UPDATE events
                SET state = ?, updated_at = ?, ticket_id = COALESCE(?, ticket_id), retry_at = NULL,
                    terminal_reason = CASE WHEN ? IS NULL THEN terminal_reason ELSE ? END,
                    lease_owner = NULL, lease_expires_at = NULL
                WHERE event_id = ?
                """,
                (state, now_text, ticket_id, reason, reason, event_id),
            )
            connection.execute(
                "INSERT INTO event_attempts(event_id, state, recorded_at, reason) VALUES (?, ?, ?, ?)",
                (event_id, state, now_text, reason),
            )
            updated = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            connection.execute("COMMIT")
        return self._record(_require_row(updated, context=f"event {event_id!r}"))

    def schedule_retry(
        self,
        event_id: str,
        *,
        reason: str,
        ticket_id: str | None = None,
        base_seconds: float = 5.0,
        max_attempts: int = 3,
        now: datetime | None = None,
        lease_owner: str | None = None,
        lease_epoch: int | None = None,
    ) -> RetryRecord:
        """Record retryable failure with bounded exponential backoff or dead-letter it."""

        if base_seconds <= 0 or max_attempts < 1:
            raise EventStoreError("retry policy must be positive")
        current = _require_aware(now, label="event retry timestamp")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"event {event_id!r} does not exist")
            if row["state"] not in {"admitted", "running", "retryable_failed"}:
                connection.execute("ROLLBACK")
                raise EventTransitionError(f"event {event_id} in state {row['state']} cannot be retried")
            self._validate_lease_fence(
                row,
                owner=lease_owner,
                lease_epoch=lease_epoch,
                now=current,
            )
            attempts = connection.execute(
                "SELECT COUNT(*) FROM event_attempts WHERE event_id = ? AND state = 'retryable_failed'",
                (event_id,),
            ).fetchone()[0] + 1
            if attempts >= max_attempts:
                target, retry_at = "dead_letter", None
            else:
                target = "retryable_failed"
                retry_at = (current + timedelta(seconds=base_seconds * (2 ** (attempts - 1)))).isoformat()
            now_text = current.isoformat()
            connection.execute(
                """
                UPDATE events
                SET state = ?, updated_at = ?, retry_at = ?, ticket_id = COALESCE(?, ticket_id),
                    terminal_reason = ?, lease_owner = NULL, lease_expires_at = NULL
                WHERE event_id = ?
                """,
                (target, now_text, retry_at, ticket_id, reason, event_id),
            )
            connection.execute(
                "INSERT INTO event_attempts(event_id, state, recorded_at, reason) VALUES (?, ?, ?, ?)",
                (event_id, target, now_text, reason),
            )
            connection.execute("COMMIT")
        return RetryRecord(event_id, attempts, retry_at, target == "dead_letter")

    def eligible_retries(self, *, now: datetime | None = None) -> tuple[EventRecord, ...]:
        """Retryable events whose durable backoff deadline has elapsed."""

        current = _require_aware(now, label="retry eligibility timestamp").isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE state = 'retryable_failed' AND retry_at IS NOT NULL AND retry_at <= ? ORDER BY retry_at",
                (current,),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def recoverable_events(self, *, now: datetime | None = None) -> tuple[EventRecord, ...]:
        """Return due retries and running events whose worker lease expired."""

        current = _require_aware(now, label="event recovery timestamp").isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE (state = 'retryable_failed' AND retry_at IS NOT NULL AND retry_at <= ?)
                   OR (state = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                ORDER BY COALESCE(retry_at, lease_expires_at), event_id
                """,
                (current, current),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def dead_letters(self) -> tuple[EventRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events WHERE state = 'dead_letter' ORDER BY updated_at").fetchall()
        return tuple(self._record(row) for row in rows)

    def acquire_lease(
        self,
        event_id: str,
        *,
        owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> EventRecord:
        """Acquire or recover a bounded worker lease for an admitted event.

        A live lease is never stolen. An expired ``running`` lease is
        recoverable after a worker crash and remains explicitly recorded as a
        new running attempt.
        """

        if not owner.strip():
            raise EventLeaseError("lease owner must not be empty")
        if lease_seconds <= 0:
            raise EventLeaseError("lease duration must be positive")
        current_time = _require_aware(now, label="event lease timestamp")
        current_text = current_time.isoformat()
        expiry_text = (current_time + timedelta(seconds=lease_seconds)).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventLeaseError(f"event {event_id!r} does not exist")
            state = row["state"]
            if state not in {"admitted", "running", "retryable_failed"}:
                connection.execute("ROLLBACK")
                raise EventLeaseError(f"event {event_id} in state {state} cannot acquire a lease")
            if state == "retryable_failed":
                retry_at = row["retry_at"]
                if not isinstance(retry_at, str) or _parse_time(retry_at) > current_time:
                    connection.execute("ROLLBACK")
                    raise EventLeaseError(f"event {event_id} retry deadline has not elapsed")
            existing_expiry = row["lease_expires_at"]
            if state == "running" and isinstance(existing_expiry, str):
                if _parse_time(existing_expiry) > current_time:
                    connection.execute("ROLLBACK")
                    raise EventLeaseError(
                        f"event {event_id} lease is held by {row['lease_owner']!r} until {existing_expiry}"
                    )
            connection.execute(
                """
                UPDATE events
                SET state = 'running', updated_at = ?, lease_owner = ?, lease_expires_at = ?,
                    lease_epoch = lease_epoch + 1, retry_at = NULL
                WHERE event_id = ?
                """,
                (current_text, owner, expiry_text, event_id),
            )
            connection.execute(
                "INSERT INTO event_attempts(event_id, state, recorded_at, reason) VALUES (?, 'running', ?, ?)",
                (event_id, current_text, f"lease acquired by {owner}"),
            )
            updated = connection.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
            connection.execute("COMMIT")
        return self._record(_require_row(updated, context=f"event lease {event_id!r}"))

    @staticmethod
    def _outbox_record(row: sqlite3.Row) -> OutboxRecord:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise EventStoreError(f"outbox {row['outbox_id']} has a non-object payload")
        return OutboxRecord(
            outbox_id=row["outbox_id"],
            event_id=row["event_id"],
            destination=row["destination"],
            kind=row["kind"],
            payload=payload,
            state=row["state"],
            attempts=row["attempts"],
            lease_owner=row["lease_owner"],
            lease_expires_at=row["lease_expires_at"],
            retry_at=row["retry_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_error=row["last_error"],
        )

    def enqueue_notification(
        self,
        *,
        event_id: str,
        destination: str,
        kind: str,
        payload: dict[str, Any],
    ) -> tuple[OutboxRecord, bool]:
        """Create one delivery intent or return its idempotent predecessor."""

        if not destination.strip() or not kind.strip():
            raise EventStoreError("outbox destination and kind must be non-empty")
        now = _now()
        serialized = json.dumps(payload, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE event_id = ? AND destination = ? AND kind = ?",
                (event_id, destination, kind),
            ).fetchone()
            if row is not None:
                connection.execute("COMMIT")
                return self._outbox_record(row), False
            connection.execute(
                """
                INSERT INTO notification_outbox(event_id, destination, kind, payload_json, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (event_id, destination, kind, serialized, now, now),
            )
            row = connection.execute("SELECT * FROM notification_outbox WHERE outbox_id = last_insert_rowid()").fetchone()
            connection.execute("COMMIT")
        return self._outbox_record(
            _require_row(row, context=f"notification for event {event_id!r}")
        ), True

    def eligible_notifications(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> tuple[OutboxRecord, ...]:
        """Return bounded pending retries and deliveries with expired leases."""

        if not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise EventStoreError("notification eligibility limit must be from 1 through 1000")
        current = _require_aware(now, label="notification eligibility timestamp").isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM notification_outbox
                WHERE (state = 'pending' AND (retry_at IS NULL OR retry_at <= ?))
                   OR (state = 'sending' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
                ORDER BY COALESCE(retry_at, lease_expires_at, created_at), outbox_id
                LIMIT ?
                """,
                (current, current, limit),
            ).fetchall()
        return tuple(self._outbox_record(row) for row in rows)

    def outbox_dead_letters(self) -> tuple[OutboxRecord, ...]:
        """Return notification intents that exhausted their delivery policy."""

        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notification_outbox WHERE state = 'dead_letter' ORDER BY updated_at"
            ).fetchall()
        return tuple(self._outbox_record(row) for row in rows)

    def claim_notification(
        self,
        outbox_id: int,
        *,
        owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> OutboxRecord:
        """Claim pending or expired delivery for at-least-once provider send."""

        if not owner.strip() or lease_seconds <= 0:
            raise EventLeaseError("outbox lease owner and duration must be valid")
        current = _require_aware(now, label="notification lease timestamp")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"outbox {outbox_id} does not exist")
            if row["state"] in {"sent", "dead_letter"}:
                connection.execute("ROLLBACK")
                raise EventLeaseError(f"outbox {outbox_id} is {row['state']}")
            retry_at = row["retry_at"]
            if row["state"] == "pending" and isinstance(retry_at, str) and _parse_time(retry_at) > current:
                connection.execute("ROLLBACK")
                raise EventLeaseError(f"outbox {outbox_id} retry deadline has not elapsed")
            expiry = row["lease_expires_at"]
            if row["state"] == "sending" and isinstance(expiry, str) and _parse_time(expiry) > current:
                connection.execute("ROLLBACK")
                raise EventLeaseError(f"outbox {outbox_id} is leased by {row['lease_owner']!r}")
            now_text = current.isoformat()
            expiry_text = (current + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute(
                """
                UPDATE notification_outbox
                SET state = 'sending', attempts = attempts + 1, lease_owner = ?,
                    lease_expires_at = ?, retry_at = NULL, updated_at = ?
                WHERE outbox_id = ?
                """,
                (owner, expiry_text, now_text, outbox_id),
            )
            updated = connection.execute("SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            connection.execute("COMMIT")
        return self._outbox_record(
            _require_row(updated, context=f"notification lease {outbox_id}")
        )

    def finish_notification(
        self,
        outbox_id: int,
        *,
        sent: bool,
        error: str | None = None,
        now: datetime | None = None,
        retry_after_seconds: float | None = None,
        base_seconds: float = 5.0,
        max_attempts: int = 5,
        owner: str | None = None,
    ) -> OutboxRecord:
        """Record provider outcome with bounded backoff and dead-lettering."""

        if base_seconds <= 0 or max_attempts < 1:
            raise EventStoreError("notification retry policy must be positive")
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise EventStoreError("notification retry delay must not be negative")
        current = _require_aware(now, label="notification outcome timestamp")
        now_text = current.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise EventStoreError(f"outbox {outbox_id} does not exist")
            if row["state"] != "sending":
                connection.execute("ROLLBACK")
                raise EventTransitionError(f"outbox {outbox_id} is not sending")
            if owner != row["lease_owner"]:
                connection.execute("ROLLBACK")
                raise EventLeaseError(
                    f"outbox {outbox_id} requires lease owner {row['lease_owner']!r}"
                )
            if sent:
                state, retry_at = "sent", None
            elif row["attempts"] >= max_attempts:
                state, retry_at = "dead_letter", None
            else:
                delay = (
                    retry_after_seconds
                    if retry_after_seconds is not None
                    else base_seconds * (2 ** max(0, row["attempts"] - 1))
                )
                state = "pending"
                retry_at = (current + timedelta(seconds=delay)).isoformat()
            connection.execute(
                """
                UPDATE notification_outbox
                SET state = ?, lease_owner = NULL, lease_expires_at = NULL,
                    retry_at = ?, updated_at = ?, last_error = ?
                WHERE outbox_id = ?
                """,
                (state, retry_at, now_text, error, outbox_id),
            )
            updated = connection.execute("SELECT * FROM notification_outbox WHERE outbox_id = ?", (outbox_id,)).fetchone()
            connection.execute("COMMIT")
        return self._outbox_record(
            _require_row(updated, context=f"notification outcome {outbox_id}")
        )

    def telegram_card_receipt(self, *, event_id: str, chat_id: str) -> TelegramCardReceipt | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_card_receipts WHERE event_id = ? AND chat_id = ?",
                (event_id, chat_id),
            ).fetchone()
        return self._telegram_card_receipt(row) if row is not None else None

    def telegram_card_state(self, *, event_id: str) -> TelegramCardState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM telegram_card_states WHERE event_id = ?", (event_id,)
            ).fetchone()
        return self._telegram_card_state(row) if row is not None else None

    def operator_callback_result(self, callback_id: str) -> OperatorCallbackResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM operator_callbacks WHERE callback_id = ?", (callback_id,)
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            raise EventStoreError(f"operator callback {callback_id!r} has invalid result")
        return OperatorCallbackResult(callback_id, row["event_id"], result, row["recorded_at"])

    def record_operator_callback(self, *, callback_id: str, event_id: str, result: dict[str, Any]) -> OperatorCallbackResult:
        if not callback_id or not isinstance(result, dict):
            raise EventStoreError("operator callback record is invalid")
        now = _now()
        encoded = json.dumps(result, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operator_callbacks WHERE callback_id = ?", (callback_id,)
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO operator_callbacks(callback_id, event_id, result_json, recorded_at) VALUES (?, ?, ?, ?)",
                    (callback_id, event_id, encoded, now),
                )
                row = connection.execute(
                    "SELECT * FROM operator_callbacks WHERE callback_id = ?", (callback_id,)
                ).fetchone()
            connection.execute("COMMIT")
        row = _require_row(row, context=f"operator callback {callback_id!r}")
        decoded = json.loads(row["result_json"])
        if not isinstance(decoded, dict):
            raise EventStoreError(f"operator callback {callback_id!r} has invalid result")
        return OperatorCallbackResult(callback_id, row["event_id"], decoded, row["recorded_at"])

    def save_telegram_card_state(
        self,
        *,
        event_id: str,
        payload: dict[str, Any],
        dirty_at: str | None,
    ) -> TelegramCardState:
        now = _now()
        serialized = json.dumps(payload, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO telegram_card_states(event_id, payload_json, dirty_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    dirty_at = excluded.dirty_at,
                    updated_at = excluded.updated_at
                """,
                (event_id, serialized, dirty_at, now),
            )
            row = connection.execute(
                "SELECT * FROM telegram_card_states WHERE event_id = ?", (event_id,)
            ).fetchone()
            connection.execute("COMMIT")
        return self._telegram_card_state(
            _require_row(row, context=f"Telegram card state for {event_id!r}")
        )

    def record_telegram_card(
        self,
        *,
        event_id: str,
        chat_id: str,
        message_id: int,
        render_hash: str,
    ) -> TelegramCardReceipt:
        if not chat_id or message_id <= 0 or not render_hash:
            raise EventStoreError("Telegram card receipt fields must be valid")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO telegram_card_receipts(event_id, chat_id, message_id, render_hash, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(event_id, chat_id) DO UPDATE SET
                    message_id = excluded.message_id,
                    render_hash = excluded.render_hash,
                    updated_at = excluded.updated_at
                """,
                (event_id, chat_id, message_id, render_hash, now),
            )
            row = connection.execute(
                "SELECT * FROM telegram_card_receipts WHERE event_id = ? AND chat_id = ?",
                (event_id, chat_id),
            ).fetchone()
            connection.execute("COMMIT")
        return self._telegram_card_receipt(
            _require_row(row, context=f"Telegram card receipt for {event_id!r}")
        )

    @staticmethod
    def _telegram_card_receipt(row: sqlite3.Row) -> TelegramCardReceipt:
        return TelegramCardReceipt(
            event_id=row["event_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            render_hash=row["render_hash"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _telegram_card_state(row: sqlite3.Row) -> TelegramCardState:
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise EventStoreError(f"telegram card state for {row['event_id']} is not an object")
        return TelegramCardState(
            event_id=row["event_id"],
            payload=payload,
            dirty_at=row["dirty_at"],
            updated_at=row["updated_at"],
        )
