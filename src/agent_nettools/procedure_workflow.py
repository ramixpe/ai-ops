"""Single-operator, dry-run-only procedure approval workflow.

This module intentionally has no imports from transport, MCP, device, or
configuration-writing modules. Approval proves a human accepted one immutable
proposal; simulation records that no action was executed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .event_store import EventStore, EventStoreError
from .procedure_registry import procedure_definition

__all__ = [
    "ApprovalError",
    "ApprovalReceipt",
    "NETTOOLS_PROCEDURE_APPROVAL_KEY_ENV",
    "ProcedureProposal",
    "SimulationResult",
    "VerifiedApproval",
    "approve_proposal",
    "configured_approval_secret",
    "simulate_approved_procedure",
    "verify_approval",
]


class ApprovalError(ValueError):
    """A proposal or approval receipt does not satisfy the dry-run contract."""


NETTOOLS_PROCEDURE_APPROVAL_KEY_ENV = "NETTOOLS_PROCEDURE_APPROVAL_KEY"
MIN_APPROVAL_SECRET_BYTES = 32


def _require_secret(secret: str) -> str:
    if len(secret.encode("utf-8")) < MIN_APPROVAL_SECRET_BYTES:
        raise ApprovalError(
            f"approval secret must contain at least {MIN_APPROVAL_SECRET_BYTES} bytes"
        )
    return secret


def configured_approval_secret() -> str:
    """Return the lab-only shared approval key without logging its value."""

    secret = os.getenv(NETTOOLS_PROCEDURE_APPROVAL_KEY_ENV, "")
    return _require_secret(secret)


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ProcedureProposal:
    procedure: str
    version: int
    parameters: dict[str, str]
    digest: str

    @classmethod
    def create(cls, procedure: str, parameters: Mapping[str, str]) -> "ProcedureProposal":
        definition = procedure_definition(procedure)
        if definition is None:
            raise ApprovalError("unknown procedure")
        normalized = dict(parameters)
        if set(normalized) != definition.required_parameters or any(
            not isinstance(value, str) or not value for value in normalized.values()
        ):
            raise ApprovalError("procedure parameters do not match the declared contract")
        payload = {
            "procedure": definition.name,
            "version": definition.version,
            "parameters": normalized,
            "execution": "dry_run_only",
        }
        return cls(definition.name, definition.version, normalized, hashlib.sha256(_canonical(payload).encode()).hexdigest())


@dataclass(frozen=True)
class ApprovalReceipt:
    proposal_digest: str
    nonce: str
    expires_at: datetime
    signature: str


_VERIFIED_APPROVAL_TOKEN = object()


@dataclass(frozen=True, init=False)
class VerifiedApproval:
    """Receipt type constructible only by :func:`verify_approval`."""

    receipt: ApprovalReceipt

    def __init__(self, receipt: ApprovalReceipt, *, _token: object | None = None) -> None:
        if _token is not _VERIFIED_APPROVAL_TOKEN:
            raise ApprovalError("verified approvals can only come from verify_approval")
        object.__setattr__(self, "receipt", receipt)


@dataclass(frozen=True)
class SimulationResult:
    proposal_digest: str
    status: str
    network_activity: bool
    device_writes: bool
    verification: str


def _receipt_body(proposal: ProcedureProposal, nonce: str, expires_at: datetime) -> bytes:
    return _canonical(
        {"proposal_digest": proposal.digest, "nonce": nonce, "expires_at": expires_at.isoformat()}
    ).encode()


def approve_proposal(
    proposal: ProcedureProposal,
    *,
    secret: str,
    now: datetime | None = None,
    ttl_seconds: int = 300,
    store: EventStore | None = None,
) -> ApprovalReceipt:
    """Create an approval receipt; callers must store its secret out of band."""

    secret = _require_secret(secret)
    if ttl_seconds < 1:
        raise ApprovalError("approval ttl must be positive")
    issued = now or datetime.now(timezone.utc)
    if issued.tzinfo is None:
        raise ApprovalError("approval time must be timezone-aware")
    nonce = secrets.token_hex(16)
    expires_at = issued + timedelta(seconds=ttl_seconds)
    signature = hmac.new(secret.encode(), _receipt_body(proposal, nonce, expires_at), hashlib.sha256).hexdigest()
    receipt = ApprovalReceipt(proposal.digest, nonce, expires_at, signature)
    if store is not None:
        store.record_procedure_approval(
            proposal_digest=proposal.digest,
            procedure=proposal.procedure,
            version=proposal.version,
            parameters=proposal.parameters,
            nonce=receipt.nonce,
            expires_at=receipt.expires_at,
            signature=receipt.signature,
            now=issued,
        )
    return receipt


def verify_approval(
    proposal: ProcedureProposal,
    receipt: ApprovalReceipt,
    *,
    secret: str,
    used_nonces: set[str],
    now: datetime | None = None,
    store: EventStore | None = None,
) -> VerifiedApproval:
    """Verify exact proposal, expiry, HMAC, and one-time nonce use."""

    secret = _require_secret(secret)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ApprovalError("approval verification time must be timezone-aware")
    if receipt.expires_at.tzinfo is None or receipt.expires_at.utcoffset() is None:
        raise ApprovalError("approval expiry must be timezone-aware")
    if receipt.proposal_digest != proposal.digest:
        raise ApprovalError("approval proposal digest does not match")
    if receipt.expires_at <= current:
        raise ApprovalError("approval receipt is expired")
    if store is None and receipt.nonce in used_nonces:
        raise ApprovalError("approval nonce is replayed")
    expected = hmac.new(secret.encode(), _receipt_body(proposal, receipt.nonce, receipt.expires_at), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(receipt.signature, expected):
        raise ApprovalError("approval signature is invalid")
    if store is not None:
        try:
            store.consume_procedure_approval(
                proposal_digest=proposal.digest,
                nonce=receipt.nonce,
                now=current,
            )
        except EventStoreError as exc:
            raise ApprovalError(str(exc)) from exc
    else:
        used_nonces.add(receipt.nonce)
    return VerifiedApproval(receipt, _token=_VERIFIED_APPROVAL_TOKEN)


def simulate_approved_procedure(
    proposal: ProcedureProposal,
    approval: VerifiedApproval,
    *,
    store: EventStore | None = None,
    now: datetime | None = None,
) -> SimulationResult:
    """Return a plan-only outcome; no execution capability exists here."""

    if not isinstance(approval, VerifiedApproval):
        raise ApprovalError("procedure simulation requires a verified approval")
    receipt = approval.receipt
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ApprovalError("procedure simulation time must be timezone-aware")
    if receipt.expires_at <= current:
        raise ApprovalError("approval receipt is expired")
    if receipt.proposal_digest != proposal.digest:
        raise ApprovalError("approval proposal digest does not match")
    result = SimulationResult(
        proposal_digest=proposal.digest,
        status="not_executed",
        network_activity=False,
        device_writes=False,
        verification="not_executed",
    )
    if store is not None:
        store.record_procedure_simulation(
            proposal_digest=result.proposal_digest,
            status=result.status,
            network_activity=result.network_activity,
            device_writes=result.device_writes,
            verification=result.verification,
            now=current,
        )
    return result
