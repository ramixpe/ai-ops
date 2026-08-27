from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_nettools.procedure_registry import procedure_definition
from agent_nettools.procedure_workflow import (
    ApprovalError,
    ApprovalReceipt,
    ProcedureProposal,
    VerifiedApproval,
    approve_proposal,
    configured_approval_secret,
    simulate_approved_procedure,
    verify_approval,
)

SECRET = "test-key-material-that-is-at-least-32-bytes"


def _proposal() -> ProcedureProposal:
    return ProcedureProposal.create("collect_device_evidence", {"device": "PE1"})


def test_approval_receipt_verifies_exact_proposal_and_is_dry_run_only():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    proposal = _proposal()
    receipt = approve_proposal(proposal, secret=SECRET, now=now)

    verified = verify_approval(
        proposal,
        receipt,
        secret=SECRET,
        used_nonces=set(),
        now=now,
    )
    result = simulate_approved_procedure(proposal, verified, now=now)

    assert result.status == "not_executed"
    assert result.network_activity is False
    assert result.device_writes is False


def test_approval_refuses_modified_proposal_expiry_and_replayed_nonce():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    proposal = _proposal()
    receipt = approve_proposal(proposal, secret=SECRET, now=now, ttl_seconds=1)

    with pytest.raises(ApprovalError, match="digest"):
        verify_approval(
            ProcedureProposal.create("collect_device_evidence", {"device": "PE2"}),
            receipt,
            secret=SECRET,
            used_nonces=set(),
            now=now,
        )
    with pytest.raises(ApprovalError, match="expired"):
        verify_approval(proposal, receipt, secret=SECRET, used_nonces=set(), now=now + timedelta(seconds=2))
    used: set[str] = set()
    verify_approval(proposal, receipt, secret=SECRET, used_nonces=used, now=now)
    with pytest.raises(ApprovalError, match="replayed"):
        verify_approval(proposal, receipt, secret=SECRET, used_nonces=used, now=now)


def test_unknown_procedure_and_missing_secret_are_refused():
    with pytest.raises(ApprovalError, match="unknown procedure"):
        ProcedureProposal.create("router_write", {"device": "PE1"})
    with pytest.raises(ApprovalError, match="secret"):
        approve_proposal(_proposal(), secret="", now=datetime(2026, 8, 26, tzinfo=timezone.utc))


def test_proposal_digest_is_bound_to_the_versioned_registry_contract():
    proposal = _proposal()
    definition = procedure_definition("collect_device_evidence")

    assert definition is not None
    assert proposal.version == definition.version
    assert proposal.parameters.keys() == definition.required_parameters


def test_configured_approval_secret_requires_an_explicit_environment_value(monkeypatch):
    monkeypatch.delenv("NETTOOLS_PROCEDURE_APPROVAL_KEY", raising=False)
    with pytest.raises(ApprovalError, match="secret"):
        configured_approval_secret()

    monkeypatch.setenv("NETTOOLS_PROCEDURE_APPROVAL_KEY", SECRET)
    assert configured_approval_secret() == SECRET

    monkeypatch.setenv("NETTOOLS_PROCEDURE_APPROVAL_KEY", "short")
    with pytest.raises(ApprovalError, match="32 bytes"):
        configured_approval_secret()


def test_durable_store_consumes_approval_nonce_and_records_simulation(tmp_path):
    from agent_nettools.event_store import EventStore

    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    store = EventStore(tmp_path / "events.sqlite3")
    proposal = _proposal()
    receipt = approve_proposal(proposal, secret=SECRET, now=now, store=store)

    verified = verify_approval(
        proposal,
        receipt,
        secret=SECRET,
        used_nonces=set(),
        now=now,
        store=store,
    )
    result = simulate_approved_procedure(proposal, verified, store=store, now=now)

    assert result.status == "not_executed"
    with pytest.raises(ApprovalError, match="replayed"):
        verify_approval(proposal, receipt, secret=SECRET, used_nonces=set(), now=now, store=store)


def test_simulation_refuses_an_unverified_or_forged_receipt():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    proposal = _proposal()
    forged = ApprovalReceipt(
        proposal.digest,
        "unknown-nonce",
        now + timedelta(days=1),
        "bogus-signature",
    )

    with pytest.raises(ApprovalError, match="verified approval"):
        simulate_approved_procedure(proposal, forged)  # type: ignore[arg-type]
    with pytest.raises(ApprovalError, match="only come from"):
        VerifiedApproval(forged)


def test_verified_receipt_cannot_be_simulated_after_expiry():
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)
    proposal = _proposal()
    receipt = approve_proposal(proposal, secret=SECRET, now=now, ttl_seconds=1)
    verified = verify_approval(
        proposal,
        receipt,
        secret=SECRET,
        used_nonces=set(),
        now=now,
    )

    with pytest.raises(ApprovalError, match="expired"):
        simulate_approved_procedure(
            proposal,
            verified,
            now=now + timedelta(seconds=2),
        )
