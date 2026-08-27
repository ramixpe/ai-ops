"""Deterministic rendering for one Telegram investigation card."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .investigation_activity import ActivityStatus
from .investigation_view import InvestigationView

__all__ = ["MAX_TELEGRAM_CARD_CHARS", "TelegramRender", "render_card"]

MAX_TELEGRAM_CARD_CHARS = 4096

_STATUS_MARKERS = {
    ActivityStatus.QUEUED: "⏳",
    ActivityStatus.RUNNING: "🔎",
    ActivityStatus.HEALTHY: "✓",
    ActivityStatus.ANOMALY: "⚠",
    ActivityStatus.REFUSED: "✗",
    ActivityStatus.RETRYING: "↻",
    ActivityStatus.INCONCLUSIVE: "?",
    ActivityStatus.COMPLETED: "🏁",
    ActivityStatus.FAILED: "✗",
}


@dataclass(frozen=True)
class TelegramRender:
    text: str
    render_hash: str
    terminal: bool


def render_card(view: InvestigationView, *, max_chars: int = MAX_TELEGRAM_CARD_CHARS) -> TelegramRender:
    """Render one bounded card, retaining identity and terminal result first."""

    if max_chars < 160:
        raise ValueError("max_chars must preserve the incident header and final state")
    status = view.status.value.replace("_", " ").upper()
    header = f"🚨 NETWORK INVESTIGATION\n{view.incident_id}\nStatus: {status}"
    body = [header]
    if view.stages:
        body.append("Stages")
        for stage in view.stages:
            marker = _STATUS_MARKERS[stage.status]
            detail = ""
            if stage.rung:
                detail = f" · {stage.rung}"
            elif stage.finding:
                detail = f" · {stage.finding}"
            if stage.device:
                detail += f" @ {stage.device}"
            body.append(f"{marker} {stage.name}{detail}")
    body.append(f"Progress: {view.completed_checks} complete · {view.running_checks} active")
    if view.finding:
        body.append(f"Finding: {view.finding}")
    if view.classification:
        body.append(f"Classification: {view.classification}")
    if view.limitation:
        body.append(f"Limitation: {view.limitation}")
    text = "\n".join(body)
    if len(text) > max_chars:
        # Pack complete lines by priority. A terminal classification is never
        # tail-truncated into a different or unreadable result.
        header = f"🚨 {view.incident_id}\nStatus: {status}"
        essential = [header]
        if view.classification:
            essential.append(f"Classification: {view.classification}")
        if view.finding:
            essential.append(f"Finding: {view.finding}")
        if view.limitation:
            essential.append(f"Limitation: {view.limitation}")
        essential.append(f"Progress: {view.completed_checks} complete · {view.running_checks} active")
        text = "\n".join(essential)
        if len(text) > max_chars:
            raise ValueError("max_chars cannot preserve the terminal card contract")
    return TelegramRender(
        text=text,
        render_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        terminal=view.terminal,
    )