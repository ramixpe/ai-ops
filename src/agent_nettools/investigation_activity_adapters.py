"""Adapters from authoritative investigation contracts to presentation activities."""

from __future__ import annotations

from .event_reporting import InvestigationReceipt
from .event_routing import RoutingDecision
from .investigation_activity import ActivityKind, ActivityStatus, InvestigationActivity

__all__ = ["activities_for_receipt", "started_activity", "visible_reasoning_tail_activity"]


def tool_selected_activity(
    *, incident_id: str, event_id: str, sequence: int, tool: str, stage: str
) -> InvestigationActivity:
    return InvestigationActivity(
        activity_id=f"{event_id}:tool-selected:{sequence}",
        incident_id=incident_id,
        event_id=event_id,
        sequence=sequence,
        kind=ActivityKind.TOOL_SELECTED,
        status=ActivityStatus.RUNNING,
        stage=stage,
        tool=tool,
    )


def command_preview_activity(
    *, incident_id: str, event_id: str, sequence: int, tool: str, device: str, command_label: str
) -> InvestigationActivity:
    return InvestigationActivity(
        activity_id=f"{event_id}:command-preview:{sequence}",
        incident_id=incident_id,
        event_id=event_id,
        sequence=sequence,
        kind=ActivityKind.COMMAND_PREVIEW,
        status=ActivityStatus.RUNNING,
        stage=tool,
        tool=tool,
        device=device,
        command_label=command_label,
    )


def tool_result_activity(
    *,
    incident_id: str,
    event_id: str,
    sequence: int,
    tool: str,
    device: str,
    is_error: bool,
    duration_ms: int,
) -> InvestigationActivity:
    return InvestigationActivity(
        activity_id=f"{event_id}:tool-result:{sequence}",
        incident_id=incident_id,
        event_id=event_id,
        sequence=sequence,
        kind=ActivityKind.TOOL_RESULT_SUMMARY,
        status=ActivityStatus.ANOMALY if is_error else ActivityStatus.HEALTHY,
        stage=tool,
        tool=tool,
        device=device,
        duration_ms=duration_ms,
    )


def started_activity(
    decision: RoutingDecision,
    *,
    incident_id: str,
    sequence: int = 0,
) -> InvestigationActivity:
    """Create the first renderer-safe activity from a routing decision."""

    return InvestigationActivity(
        activity_id=f"{decision.event_id}:started",
        incident_id=incident_id,
        event_id=decision.event_id,
        sequence=sequence,
        kind=ActivityKind.STARTED,
        status=ActivityStatus.QUEUED,
        stage=decision.flow or "investigation",
    )


def visible_reasoning_tail_activity(
    *,
    incident_id: str,
    event_id: str,
    sequence: int,
    finding: str,
    trustworthy: bool,
    coverage_complete: bool | None,
) -> InvestigationActivity:
    """Create a labelled public summary without accepting model or device prose."""

    if not finding:
        raise ValueError("visible reasoning tail requires a deterministic finding")
    limitation = (
        "Evidence coverage is incomplete; this is not a final causal explanation."
        if coverage_complete is False
        else "Evidence is deterministic but this is not a final causal explanation."
    )
    if not trustworthy:
        limitation = "Evidence is not trustworthy; this is not a final causal explanation."
    return InvestigationActivity(
        activity_id=f"{event_id}:reasoning-tail:{sequence}",
        incident_id=incident_id,
        event_id=event_id,
        sequence=sequence,
        kind=ActivityKind.VISIBLE_REASONING_TAIL,
        status=ActivityStatus.INCONCLUSIVE,
        finding=finding,
        visible_summary=(
            f"Deterministic investigation classified the event as {finding}.",
            limitation,
        ),
    )


def activities_for_receipt(
    receipt: InvestigationReceipt,
    *,
    incident_id: str,
    event_id: str,
    sequence_start: int,
) -> tuple[InvestigationActivity, ...]:
    """Project validated rungs and final classification into closed activities."""

    activities: list[InvestigationActivity] = []
    sequence = sequence_start
    for rung in receipt.rungs:
        sequence += 1
        status = ActivityStatus.HEALTHY if rung["status"] == "healthy" else ActivityStatus.ANOMALY
        if rung["status"] == "unevaluated":
            status = ActivityStatus.INCONCLUSIVE
        activities.append(
            InvestigationActivity(
                activity_id=f"{event_id}:rung:{rung['position']}",
                incident_id=incident_id,
                event_id=event_id,
                sequence=sequence,
                kind=ActivityKind.RUNG_OBSERVED,
                status=status,
                stage=receipt.flow,
                rung=rung["rung"],
                device=rung["device"],
                finding=receipt.finding,
            )
        )

    classification = _classification(receipt)
    limitation = _limitation(receipt)
    activities.append(
        InvestigationActivity(
            activity_id=f"{event_id}:terminal",
            incident_id=incident_id,
            event_id=event_id,
            sequence=sequence + 1,
            kind=ActivityKind.COMPLETED,
            status=ActivityStatus.COMPLETED,
            stage=receipt.flow,
            finding=receipt.finding,
            classification=classification,
            limitation=limitation,
        )
    )
    return tuple(activities)


def _classification(receipt: InvestigationReceipt) -> str:
    if receipt.finding in {"all_layers_healthy", "no_fault_on_path"}:
        return "NO_ACTIVE_FAULT"
    if receipt.finding == "subject_not_found":
        return "SUBJECT_INVALID"
    if receipt.finding in {"undetermined", "temporally_incoherent"} or not receipt.trustworthy:
        return "INCONCLUSIVE"
    return "ACTIVE_FAULT_LOCALIZED" if receipt.cause is not None else "INCONCLUSIVE"


def _limitation(receipt: InvestigationReceipt) -> str | None:
    if not receipt.trustworthy:
        return "deterministic evidence is not trustworthy"
    if receipt.coherence is not None and receipt.coherence.get("refuses") is True:
        return "evidence coherence refused a final diagnosis"
    if receipt.coverage is not None and receipt.coverage.get("complete") is False:
        return "evidence coverage is incomplete"
    return None