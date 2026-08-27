"""Deterministic outbound chat-style transcript rendering for activities."""

from __future__ import annotations

from .investigation_activity import ActivityKind, ActivityStatus, InvestigationActivity

__all__ = ["render_activity_message"]


def render_activity_message(activity: InvestigationActivity) -> str | None:
    """Render one safe, meaningful activity or suppress low-value activity."""

    if activity.kind is ActivityKind.VISIBLE_REASONING_TAIL:
        return "🧠 Reasoning summary (non-authoritative)\n" + " ".join(activity.visible_summary)
    if activity.kind is ActivityKind.TOOL_SELECTED:
        return f"🔎 Selecting {activity.tool} for {activity.stage or 'the investigation'}."
    if activity.kind is ActivityKind.COMMAND_PREVIEW:
        return f"🔧 Running approved check on {activity.device or 'the target'}: {activity.command_label}."
    if activity.kind is ActivityKind.TOOL_RESULT_SUMMARY:
        marker = "✓" if activity.status is ActivityStatus.HEALTHY else "⚠"
        duration = f" ({activity.duration_ms} ms)" if activity.duration_ms is not None else ""
        evidence = f" Evidence: {', '.join(activity.evidence_keys)}." if activity.evidence_keys else ""
        finding = f" Finding: {activity.finding}." if activity.finding else ""
        return f"{marker} {activity.tool} completed{duration}.{finding}{evidence}"
    if activity.kind is ActivityKind.TOOL_REFUSED:
        return f"✗ Approved tool request refused: {activity.limitation or 'policy or bounds prevented the check.'}"
    if activity.kind is ActivityKind.RETRYING:
        return f"↻ Retrying {activity.tool or 'the approved check'}: {activity.limitation or 'transient failure.'}"
    return None