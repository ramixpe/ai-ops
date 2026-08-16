"""The MVP-0 runner: one investigation, end to end.

    resolve scope -> run descent -> correlate -> write report -> ground -> emit

The descent is deterministic and terminal. **There is no gate and no narrowing
pass in MVP-0** — the model never influences what is collected or which rung is
checked next. It renders an argument that code has already made, and places it
on a timeline. `agent_loop.py` is untouched and remains the general-purpose
bounded loop for questions that do not map to a flow.

What this module is really enforcing
-------------------------------------
Three rules that are easy to state and easy to lose in wiring:

**1. A report that fails grounding is not emitted, and its prose does not
leave this module.** `InvestigationResult.report` is `None` whenever grounding
failed. Not "populated but flagged" — a caller that prints
`result.report or "..."` must be structurally unable to print rejected prose.
Same reasoning as `GroundingFailure` having no `claim` field (OBS-061): a rule
enforced by remembering to check a flag is a rule that eventually is not
enforced.

**2. Grounding runs through `ground_report`, never `check_grounding`.** The
latter is citation integrity alone and passes every internally consistent
report, including one that names the cause and drops the four rungs explaining
it. T-029's acceptance depends on this call, which is why it is one function
and not two.

**3. A coverage shortfall downgrades a claim; it does not discard it.**
`absence_claim_exceeds_coverage` means the model gave a real answer at the wrong
strength — "no correlating events" over a buffer that returned 200 of 593
records. Throwing that away would lose a usable result; emitting it as a
negative would overstate it. It is emitted as `coverage_limited`, which is an
`unevaluated` and reads as one.

Correlation runs against the device where the *cause* is
---------------------------------------------------------
Not the device the investigation started from. `RR1 -> 10.255.0.12` finds its
cause on **PE2**, and PE2's buffer is where the interface and IS-IS events are.
Reading RR1's logs would correlate a PE2 interface event against a device that
never saw it, and would return "no correlating events" with perfect confidence.

No model configured is a mode, not a failure
---------------------------------------------
`investigate(...)` with no `analyst` runs the descent and stops, and says so:
both model outputs report `NOT_ATTEMPTED` rather than being absent. A descent is
a complete, useful result on its own — it is the part with no model in it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import flows
from .coverage import Coverage
from .descent import DescentResult, run_descent
from .grounding import GroundingResult, ground_correlation, ground_report
from .log_window import ShapedWindow, coverage_from_logging, shape_window
from .network_tools import collect_evidence, run_template
from .prompt_library import build_correlate_prompt, build_report_prompt
from .template_parsers import PARSE_OK, parse_template_output

__all__ = [
    "COVERAGE_LIMITED",
    "EMITTED",
    "InvestigationResult",
    "NOT_ATTEMPTED",
    "WITHHELD",
    "Analyst",
    "investigate",
    "inventory_resolver",
]

#: A model output that was produced and passed its grounding gate.
EMITTED = "emitted"
#: Produced, failed grounding, and therefore discarded. The prose is gone.
WITHHELD = "withheld"
#: Produced and grounded, but the source could not support the strength of the
#: claim. Emitted as an ``unevaluated``, never as a negative.
COVERAGE_LIMITED = "coverage_limited"
#: No model was configured, or there was nothing for it to do.
NOT_ATTEMPTED = "not_attempted"

#: A model call: takes a rendered prompt, returns the model's raw text.
Analyst = Callable[[str], str]

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n?\s*```\s*$", re.DOTALL)


def _decode(text: str) -> tuple[Any, tuple[str, ...]]:
    """Parse a model's response into JSON, and say what had to be done to it.

    A markdown fence is stripped — it is a known, deterministic transport
    wrapper, and killing a run over one would be brittle for no safety gain.
    **Nothing else is repaired.** The line between the two is that stripping a
    fence cannot change what the JSON says, whereas anything that reaches into
    the content is the model's judgement being second-guessed by a regex.

    The repair is returned rather than silently applied, because a model that
    keeps ignoring "no markdown fences" is a prompt problem someone should see.

    On failure the raw text is *not* returned. A malformed response is prose
    that failed to be a report, and prose does not leave this module.
    """

    repairs: list[str] = []
    body = text
    if match := _FENCE.match(text):
        body = match["body"]
        repairs.append("stripped a markdown fence the prompt forbids")
    try:
        return json.loads(body), tuple(repairs)
    except (ValueError, TypeError):
        return None, (*repairs, "the response was not valid JSON")


@dataclass(frozen=True)
class InvestigationResult:
    """One investigation. The descent always; the model's work only if grounded."""

    device: str
    subject: str
    flow: str
    descent: DescentResult

    report: dict | None = None
    report_status: str = NOT_ATTEMPTED
    report_grounding: GroundingResult = field(default_factory=GroundingResult)

    correlation: dict | None = None
    correlation_status: str = NOT_ATTEMPTED
    correlation_grounding: GroundingResult = field(default_factory=GroundingResult)
    coverage: Coverage | None = None

    #: Non-semantic fixes applied to a model response, e.g. a stripped fence.
    repairs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def finding(self) -> str:
        return self.descent.finding

    def summary(self) -> str:
        """One line. Always available -- the descent needs no model."""

        cause = self.descent.cause
        localised = cause is not None and self.descent.finding != flows.UNDETERMINED
        where = f" on {cause.device}" if localised else ""
        return (
            f"{self.flow} {self.device} -> {self.subject}: "
            f"{self.descent.finding}{where} "
            f"[report {self.report_status}, correlation {self.correlation_status}]"
        )

    def to_payload(self) -> dict:
        """The structured result a CLI or MCP caller renders.

        Carries the descent in full, the model's work only where it was
        emitted, and **the caveat wherever a claim is qualified** -- a
        `coverage_limited` correlation that renders without its caveat is the
        overstatement the downgrade exists to prevent, so the caveat is a field
        rather than something a renderer is trusted to add.
        """

        descent = self.descent
        return {
            "tool": "investigate",
            "device": self.device,
            "subject": self.subject,
            "flow": self.flow,
            "finding": descent.finding,
            "reason": descent.reason,
            # Suppressed when the walk did not localise one. `DescentResult.cause`
            # is "the lowest broken rung", which for an `undetermined` walk is
            # only "the lowest broken rung reached before we stopped" -- naming
            # that as a cause would present a stopping point as a conclusion.
            "cause": (
                {"rung": descent.cause.rung, "device": descent.cause.device,
                 "reason": descent.cause.result.reason}
                if descent.cause is not None
                and descent.finding != flows.UNDETERMINED
                else None
            ),
            "causal_chain": [
                {"rung": o.rung, "device": o.device, "reason": o.result.reason}
                for o in descent.causal_chain
            ],
            "rungs": [
                {"rung": o.rung, "device": o.device, "status": o.status,
                 "reason": o.result.reason,
                 "evidence_keys": list(o.result.evidence_keys)}
                for o in descent.outcomes
            ],
            "report": {
                "status": self.report_status,
                "content": self.report,
                "grounding": self.report_grounding.summary(),
            },
            "correlation": {
                "status": self.correlation_status,
                "content": self.correlation,
                "grounding": self.correlation_grounding.summary(),
                "caveat": self.caveat,
            },
            "coverage": self.coverage.as_dict() if self.coverage is not None else None,
            "repairs": list(self.repairs),
            "trustworthy": self.trustworthy,
        }

    @property
    def caveat(self) -> str | None:
        """The qualification a `coverage_limited` correlation must carry.

        Explicitly **not** a qualification of the descent. The finding is
        deterministic and was reached without a model; only the *timeline* is
        limited by what the log source could show. Conflating the two would
        make a correlation shortfall read as doubt about the diagnosis.
        """

        if self.correlation_status != COVERAGE_LIMITED:
            return None
        gaps = "; ".join(self.coverage.gaps()) if self.coverage else "coverage unknown"
        return (
            f"The finding is deterministic and unaffected. The timeline is "
            f"limited: {gaps}. Read the correlation as 'nothing found in what "
            f"was available', not as 'nothing happened'."
        )

    @property
    def trustworthy(self) -> bool:
        """Did this run produce an answer a caller may act on?

        The axis the exit code is built from, and deliberately *not* "is the
        network healthy". An `undetermined` descent and a withheld report are
        both untrustworthy answers about a fabric that may be perfectly fine;
        `interface_line_down` with a grounded report is a trustworthy answer
        about a fabric that is not.

        A `coverage_limited` correlation is trustworthy. It is a qualified
        answer, not an absent one, and the qualification travels in
        :attr:`caveat`.
        """

        if self.descent.finding == flows.UNDETERMINED:
            return False
        if self.report_status == WITHHELD or self.correlation_status == WITHHELD:
            return False
        return True

    def withheld_because(self) -> tuple[str, ...]:
        """Why a model output is not here, in terms of the descent.

        Never in terms of what the model said. A caller printing this to a user
        gets loci and rules, not the rejected prose.
        """

        reasons: list[str] = []
        if self.report_status == WITHHELD:
            reasons.extend(str(f) for f in self.report_grounding.failures)
        if self.correlation_status in (WITHHELD, COVERAGE_LIMITED):
            reasons.extend(str(f) for f in self.correlation_grounding.failures)
        return tuple(reasons)


def inventory_resolver(subject: str) -> str:
    """Map a subject to the device that owns it, from the inventory.

    Router IDs only, which is what the `bgp_session` flow's subjects are. A
    subject with no owner raises rather than falling back to the local device --
    the same refusal `_resolve_devices` makes for a missing resolver, and for
    the same reason: a wrong-device read looks exactly like a healthy one.
    """

    from .inventory_model import load_inventory_file

    for device in load_inventory_file().devices:
        if getattr(device, "router_id", None) == subject:
            return device.name
    raise ValueError(
        f"no device in the inventory owns {subject!r}; refusing to fall back to "
        "the local device, because reading the wrong device's state produces a "
        "healthy-looking answer about the wrong thing"
    )


def _collect_for_rung(device: str, rung: flows.Rung, subject: str, *, sender=None) -> dict:
    """Everything one rung needs, keyed by the convention `checks.py` reads.

    Intents land under their own name (`evidence["bgp"]`); templates land under
    `"<template>:<param>"`. `EACH_PHYSICAL_INTERFACE` rungs need one template
    call per physical interface, which is why the interface list is read out of
    the already-collected `interfaces` intent rather than guessed.
    """

    evidence = dict(collect_evidence(device, sender=sender))

    for step in rung.collect:
        if not step.is_template:
            continue
        if step.parameter == "prefix":
            key = f"{subject}/32"
            evidence[f"{step.name}:{key}"] = run_template(
                device, step.name, sender=sender, prefix=key
            )
        elif step.parameter == "interface":
            parsed = evidence.get("interfaces", {}).get("data", {}).get("parsed") or {}
            for record in parsed.get("records", []):
                name = record.get("interface", "")
                if name.startswith("Gi") and "." not in name:
                    evidence[f"{step.name}:{name}"] = run_template(
                        device, step.name, sender=sender, interface=name
                    )
        else:
            evidence[f"{step.name}:{subject}"] = run_template(
                device, step.name, sender=sender, **{step.parameter: subject}
            )
    return evidence


def _log_window(device: str, *, sender=None, count: int = 200) -> ShapedWindow:
    """The shaped window for one device, with its coverage record.

    A failed or unparseable read is not an empty window -- it is a window with
    no coverage, which `check_absence_coverage` refuses to let anything claim
    absence over. That distinction is the whole point of T-029a and it must not
    be flattened here into "we got nothing back".
    """

    result = run_template(device, "logging", sender=sender, count=count)
    outputs = (result.get("data") or {}).get("outputs") or {}
    raw = next(iter(outputs.values()), "") if isinstance(outputs, dict) else ""

    if result.get("status") != "success" or not raw:
        return ShapedWindow(total_in=0, coverage=None)

    parsed, status = parse_template_output("cisco_xr", "logging", raw)
    if status is not PARSE_OK or parsed is None:
        return ShapedWindow(total_in=0, coverage=None)

    return shape_window(
        parsed["records"], coverage=coverage_from_logging(parsed, device)
    )


def investigate(
    device: str,
    subject: str,
    *,
    flow: str = "bgp_session",
    analyst: Analyst | None = None,
    collector: Callable[[str, flows.Rung, str], dict] | None = None,
    resolver: Callable[[str], str | Sequence[str]] | None = None,
    window: Callable[[str], ShapedWindow] | None = None,
    sender=None,
) -> InvestigationResult:
    """Run one investigation end to end.

    ``analyst`` is the only model in the path. Omit it and the descent runs
    alone, which is a complete result -- the deterministic half is the half that
    finds the cause.
    """

    the_flow = flows.flow_for(flow)
    collect = collector or (
        lambda d, r, s: _collect_for_rung(d, r, s, sender=sender)
    )
    resolve = resolver or inventory_resolver

    descent = run_descent(
        the_flow, device, subject, collector=collect, resolver=resolve
    )

    if analyst is None:
        return InvestigationResult(
            device=device, subject=subject, flow=flow, descent=descent
        )

    repairs: list[str] = []

    # -- Correlate. Against the device the cause is on, not the local one. ---
    cause = descent.cause
    correlation: dict | None = None
    correlation_status = NOT_ATTEMPTED
    correlation_grounding = GroundingResult()
    coverage: Coverage | None = None

    if cause is not None:
        read_window = window or (lambda d: _log_window(d, sender=sender))
        shaped = read_window(cause.device)
        coverage = shaped.coverage

        decoded, fixes = _decode(analyst(build_correlate_prompt(descent, shaped)))
        repairs.extend(fixes)
        correlation_grounding = ground_correlation(decoded, coverage)

        if correlation_grounding.ok:
            correlation, correlation_status = decoded, EMITTED
        elif all(
            f.kind == "absence_claim_exceeds_coverage"
            for f in correlation_grounding.failures
        ):
            # A real answer at the wrong strength. Keeping it and labelling it
            # is strictly better than discarding it: the reader learns both
            # that nothing was found and that the source could not have shown
            # it, which is more than either half alone.
            correlation, correlation_status = decoded, COVERAGE_LIMITED
        else:
            correlation_status = WITHHELD

    # -- Report. ------------------------------------------------------------
    decoded, fixes = _decode(analyst(build_report_prompt(descent)))
    repairs.extend(fixes)
    report_grounding = ground_report(decoded, descent)
    report = decoded if report_grounding.ok else None
    report_status = EMITTED if report_grounding.ok else WITHHELD

    return InvestigationResult(
        device=device, subject=subject, flow=flow, descent=descent,
        report=report, report_status=report_status, report_grounding=report_grounding,
        correlation=correlation, correlation_status=correlation_status,
        correlation_grounding=correlation_grounding, coverage=coverage,
        repairs=tuple(repairs),
    )
