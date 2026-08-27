"""Code-observed investigation receipts for tickets and event delivery.

The event agent treats an MCP response as untrusted transport data until this
module validates that it has the shape and closed finding vocabulary emitted by
the deterministic investigation surface. Model prose is deliberately absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from . import flows

__all__ = ["InvestigationReceipt", "ReceiptValidationError"]


class ReceiptValidationError(ValueError):
    """An investigate result cannot support a deterministic ticket answer."""


def _flow_findings() -> frozenset[str]:
    return frozenset().union(*(flow.findings for flow in flows.FLOWS.values()))


_DECLARED_FINDINGS = _flow_findings()
_NO_CAUSE_FINDINGS = frozenset(
    {
        flows.ALL_LAYERS_HEALTHY,
        flows.CAUSE_NOT_LOCALISED,
        flows.NO_FAULT_ON_PATH,
        flows.SUBJECT_NOT_FOUND,
        flows.TEMPORALLY_INCOHERENT,
        flows.UNDETERMINED,
    }
)
_VALID_RUNG_STATUS = frozenset({"healthy", "broken", "unevaluated"})


@dataclass(frozen=True)
class InvestigationReceipt:
    """Validated deterministic result suitable for ticket and Telegram views."""

    device: str
    subject: str
    flow: str
    finding: str
    reason: str | None
    trustworthy: bool
    cause: dict[str, Any] | None
    causal_chain: tuple[dict[str, Any], ...]
    rungs: tuple[dict[str, Any], ...]
    coherence: dict[str, Any] | None
    coverage: dict[str, Any] | None
    sessions: dict[str, Any] | None
    operator_notes: tuple[dict[str, Any], ...]
    off_path: tuple[dict[str, Any], ...]
    report_status: str | None
    correlation_status: str | None
    narrowing_shadow: dict[str, Any] | None

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "InvestigationReceipt":
        if payload.get("tool") != "investigate":
            raise ReceiptValidationError("investigation receipt has an unexpected tool")
        device = _required_text(payload, "device")
        subject = _required_text(payload, "subject")
        flow = _required_text(payload, "flow")
        if flow not in flows.FLOWS:
            raise ReceiptValidationError(f"investigation receipt has unsupported flow {flow!r}")
        finding = _required_text(payload, "finding")
        if finding not in _DECLARED_FINDINGS:
            raise ReceiptValidationError(f"investigation receipt has undeclared finding {finding!r}")
        trustworthy = payload.get("trustworthy")
        if not isinstance(trustworthy, bool):
            raise ReceiptValidationError("investigation receipt trustworthy must be a bool")
        rungs = _rungs(payload.get("rungs"), payload.get("rungs_examined"))
        cause = _object_or_none(payload.get("cause"), "cause")
        if finding in _NO_CAUSE_FINDINGS and cause is not None:
            raise ReceiptValidationError(f"finding {finding!r} must not carry a cause")
        if cause is not None:
            _validate_cause(cause, rungs)
        causal_chain = _object_sequence(payload.get("causal_chain"), "causal_chain")
        for entry in causal_chain:
            _validate_cause(entry, rungs)
        coherence = _object_or_none(payload.get("coherence"), "coherence")
        if coherence is not None and coherence.get("refuses") is True and trustworthy:
            raise ReceiptValidationError("coherence refusal cannot be trustworthy")
        coverage = _object_or_none(payload.get("coverage"), "coverage")
        sessions = _object_or_none(payload.get("sessions"), "sessions")
        notes = _object_sequence(payload.get("operator_notes"), "operator_notes")
        off_path = _object_sequence(payload.get("off_path"), "off_path")
        report = _object_or_none(payload.get("report"), "report")
        correlation = _object_or_none(payload.get("correlation"), "correlation")
        narrowing_shadow = _object_or_none(payload.get("narrowing_shadow"), "narrowing_shadow")
        if narrowing_shadow is not None and narrowing_shadow.get("active") is not False:
            raise ReceiptValidationError("investigation receipt narrowing shadow must be inactive")
        return cls(
            device=device,
            subject=subject,
            flow=flow,
            finding=finding,
            reason=payload.get("reason") if isinstance(payload.get("reason"), str) else None,
            trustworthy=trustworthy,
            cause=cause,
            causal_chain=causal_chain,
            rungs=rungs,
            coherence=coherence,
            coverage=coverage,
            sessions=sessions,
            operator_notes=notes,
            off_path=off_path,
            report_status=report.get("status") if report is not None and isinstance(report.get("status"), str) else None,
            correlation_status=(
                correlation.get("status")
                if correlation is not None and isinstance(correlation.get("status"), str)
                else None
            ),
            narrowing_shadow=narrowing_shadow,
        )

    def ticket_extra(self) -> dict[str, Any]:
        """Code-observed drill fields added to a ticket Answer section."""

        return {
            "causal_chain": list(self.causal_chain),
            "reason": self.reason,
            "rungs": list(self.rungs),
            "coverage": self.coverage,
            "sessions": self.sessions,
            "operator_notes": list(self.operator_notes),
            "off_path": list(self.off_path),
        }


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReceiptValidationError(f"investigation receipt {key!r} must be a non-empty string")
    return value


def _object_or_none(value: Any, name: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ReceiptValidationError(f"investigation receipt {name} must be an object or null")
    return dict(value)


def _object_sequence(value: Any, name: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ReceiptValidationError(f"investigation receipt {name} must be an array of objects")
    return tuple(dict(item) for item in value)


def _rungs(value: Any, declared_count: Any) -> tuple[dict[str, Any], ...]:
    rungs = _object_sequence(value, "rungs")
    if not isinstance(declared_count, int) or declared_count != len(rungs) or not rungs:
        raise ReceiptValidationError("investigation receipt rungs_examined must equal a non-empty rungs list")
    for position, rung in enumerate(rungs, start=1):
        if rung.get("position") != position or rung.get("of") != len(rungs):
            raise ReceiptValidationError("investigation receipt rungs must have contiguous positions")
        if not isinstance(rung.get("rung"), str) or not isinstance(rung.get("device"), str):
            raise ReceiptValidationError("investigation receipt rung identity is invalid")
        if rung.get("status") not in _VALID_RUNG_STATUS:
            raise ReceiptValidationError("investigation receipt rung status is invalid")
        if not isinstance(rung.get("reason"), str):
            raise ReceiptValidationError("investigation receipt rung reason is invalid")
        if not isinstance(rung.get("evidence_keys"), list) or not all(
            isinstance(key, str) for key in rung["evidence_keys"]
        ):
            raise ReceiptValidationError("investigation receipt rung evidence keys are invalid")
    return rungs


def _validate_cause(cause: Mapping[str, Any], rungs: tuple[dict[str, Any], ...]) -> None:
    rung = cause.get("rung")
    device = cause.get("device")
    reason = cause.get("reason")
    if not all(isinstance(value, str) and value for value in (rung, device, reason)):
        raise ReceiptValidationError("investigation receipt cause is invalid")
    if not any(
        item["rung"] == rung and item["device"] == device and item["status"] == "broken"
        for item in rungs
    ):
        raise ReceiptValidationError("investigation receipt cause must reference a broken rung")