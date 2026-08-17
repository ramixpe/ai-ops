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
from .epoch import (
    DEFAULT_SKEW_BOUND_SECONDS,
    EvidenceEpoch,
    check_coherence,
    collect_epoch,
    template_calls,
)
from .grounding import GroundingResult, ground_correlation, ground_report
from .log_window import ShapedWindow, coverage_from_logging, shape_window
from .network_tools import collect_evidence, run_template
from .prompt_library import RenderedPrompt, build_correlate_prompt, build_report_prompt
from .render import render_correlation, render_report, report_grounding_note
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

#: A model call: takes a rendered prompt (already split at the cache boundary
#: -- B-421, see `prompt_library.RenderedPrompt`), returns the model's raw
#: text. Passing the split object through, rather than a concatenated string,
#: is what lets `cli.py`'s real analyst hand it straight to
#: `llm_analysis.complete_prompt`, which needs the split intact to cache
#: anything.
Analyst = Callable[[RenderedPrompt], str]

#: Findings that name no cause, for two different reasons.
#:
#: `undetermined` -- the walk stopped, so its lowest broken rung is only "the
#: lowest rung reached before we stopped", and rendering that as a cause would
#: present a stopping point as a conclusion.
#:
#: `no_fault_on_path` -- rung 1 is healthy, so there is no symptom and nothing
#: below can be its cause. The broken rungs are real and are **observations**
#: about the device, not an explanation of anything (B-428).
#:
#: `temporally_incoherent` -- the rungs were read at instants too far apart, or
#: the fabric moved between the walk and the re-read. The lowest broken rung is
#: a real observation of some instant, and presenting it as the cause of a
#: symptom read at a different instant is exactly the defect the epoch removes.
#: `subject_not_found` -- nothing was walked, so there are no rungs and no
#: cause. The refusal is the whole result.
_FINDINGS_WITHOUT_A_CAUSE = frozenset(
    {
        flows.UNDETERMINED,
        flows.NO_FAULT_ON_PATH,
        flows.TEMPORALLY_INCOHERENT,
        flows.SUBJECT_NOT_FOUND,
    }
)

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

    #: **The authoritative report, rendered by code from the descent (B-439).**
    #: Never a model's output, always present, and never graded -- see
    #: `render.report_grounding_note`.
    report: dict | None = None
    report_status: str = NOT_ATTEMPTED
    report_grounding: GroundingResult = field(default_factory=GroundingResult)

    #: **The authoritative timeline, also rendered by code.** Four of the five
    #: correlate fields are filtering, ordering and counting; only `summary` is
    #: prose, and the rendered one states exactly what it counted.
    correlation: dict | None = None
    correlation_status: str = NOT_ATTEMPTED
    correlation_grounding: GroundingResult = field(default_factory=GroundingResult)
    coverage: Coverage | None = None

    #: A model's readable restatement of the report. **Non-authoritative**, and
    #: structurally separate so nothing downstream can prefer it to `report` by
    #: accident. Graded exactly as the report used to be, and `None` when that
    #: grading failed -- rejected prose still does not leave this module.
    paraphrase: dict | None = None
    paraphrase_status: str = NOT_ATTEMPTED
    paraphrase_grounding: GroundingResult = field(default_factory=GroundingResult)

    #: The same, for the timeline.
    correlation_paraphrase: dict | None = None
    correlation_paraphrase_status: str = NOT_ATTEMPTED

    #: Non-semantic fixes applied to a model response, e.g. a stripped fence.
    repairs: tuple[str, ...] = field(default_factory=tuple)
    #: What the model calls cost, when the analyst reports it (B-425). `None`
    #: for a `--no-model` run and for any analyst that carries no `.usage` --
    #: which is different from zero, and says so.
    usage: object | None = None

    @property
    def finding(self) -> str:
        return self.descent.finding

    def summary(self) -> str:
        """One line. Always available -- the descent needs no model."""

        cause = self.descent.cause
        localised = (
            cause is not None
            and self.descent.finding not in _FINDINGS_WITHOUT_A_CAUSE
        )
        where = f" on {cause.device}" if localised else ""
        # The authoritative report and timeline are always emitted, so saying so
        # adds nothing. What a reader needs from one line is whether a *model*
        # restatement was rejected -- which no longer changes the exit code
        # (B-439), and therefore has to be legible somewhere that is read.
        model = ""
        if NOT_ATTEMPTED != (self.paraphrase_status, self.correlation_paraphrase_status):
            model = (
                f" [paraphrase {self.paraphrase_status}, "
                f"timeline paraphrase {self.correlation_paraphrase_status}]"
            )
        return (
            f"{self.flow} {self.device} -> {self.subject}: "
            f"{self.descent.finding}{where}"
            f" [report {self.report_status}, correlation {self.correlation_status}]"
            f"{model}"
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
            # Suppressed for the findings that name no cause -- see
            # `_FINDINGS_WITHOUT_A_CAUSE`. The rungs are still all present under
            # "rungs", so a broken-but-off-path rung is reported, just not as an
            # explanation.
            "cause": (
                {"rung": descent.cause.rung, "device": descent.cause.device,
                 "reason": descent.cause.result.reason}
                if descent.cause is not None
                and descent.finding not in _FINDINGS_WITHOUT_A_CAUSE
                else None
            ),
            "causal_chain": [
                {"rung": o.rung, "device": o.device, "reason": o.result.reason}
                for o in descent.causal_chain
            ],
            # Numbered, with a stated total, for the same reason the rendered
            # report is (OBS-115): a restatement that drops one is then visibly
            # short to anyone reading this beside it. Detectable, not enforced.
            "rungs_examined": len(descent.outcomes),
            "rungs": [
                {"position": n, "of": len(descent.outcomes),
                 "rung": o.rung, "device": o.device, "status": o.status,
                 "reason": o.result.reason,
                 "evidence_keys": list(o.result.evidence_keys)}
                for n, o in enumerate(descent.outcomes, start=1)
            ],
            "report": {
                "status": self.report_status,
                "content": self.report,
                "authoritative": True,
                # Deliberately not a grounding verdict. Grading a report
                # generated from the descent's own typed fields would compare
                # code against itself and always pass -- shape 5 in the one
                # place built to prevent shape 5 (B-439).
                "grounding": report_grounding_note(),
                "paraphrase": {
                    "status": self.paraphrase_status,
                    "content": self.paraphrase,
                    "authoritative": False,
                    "grounding": self.paraphrase_grounding.summary(),
                },
            },
            "correlation": {
                "status": self.correlation_status,
                "content": self.correlation,
                "authoritative": True,
                "grounding": report_grounding_note(),
                "caveat": self.caveat,
                "paraphrase": {
                    "status": self.correlation_paraphrase_status,
                    "content": self.correlation_paraphrase,
                    "authoritative": False,
                    "grounding": self.correlation_grounding.summary(),
                },
            },
            "off_path": list(self.off_path),
            # Present whatever the outcome, including a comfortable pass. A
            # bound that only speaks when violated says nothing about how close
            # we routinely run, and a threshold with no observed distribution
            # behind it can only ever be revised on argument (design §2.3a).
            "coherence": (
                descent.coherence.as_dict() if descent.coherence is not None else None
            ),
            "usage": self.usage.as_dict() if self.usage is not None else None,
            "coverage": self.coverage.as_dict() if self.coverage is not None else None,
            "repairs": list(self.repairs),
            "trustworthy": self.trustworthy,
        }

    @property
    def off_path(self) -> tuple[dict, ...]:
        """Broken rungs that are real but explain nothing.

        Only populated for `no_fault_on_path`. They are reported so the run does
        not read as "nothing was found" -- an interface really is down; it is
        simply not on the path between this device and this subject.
        """

        if self.descent.finding != flows.NO_FAULT_ON_PATH:
            return ()
        return tuple(
            {"rung": o.rung, "device": o.device, "reason": o.result.reason}
            for o in self.descent.outcomes
            if o.status == "broken"
        )

    @property
    def caveat(self) -> str | None:
        """The qualification a `coverage_limited` correlation must carry.

        Explicitly **not** a qualification of the descent. The finding is
        deterministic and was reached without a model; only the *timeline* is
        limited by what the log source could show. Conflating the two would
        make a correlation shortfall read as doubt about the diagnosis.
        """

        if self.correlation_paraphrase_status != COVERAGE_LIMITED:
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
        # `temporally_incoherent` is an answer problem, not a network problem:
        # the fabric may be fine or broken and this run cannot say which. Exit 2
        # by the same rule as `undetermined`, and for the same reason -- the
        # observations were real, and they do not describe one state.
        if self.descent.finding == flows.TEMPORALLY_INCOHERENT:
            return False
        # No investigation happened, so there is no answer about the network to
        # trust. The refusal itself is reliable -- the device was read and said
        # it has no such object -- but that is a statement about the *question*,
        # and exit 2 is "no trustworthy answer about the fabric" (B-459).
        if self.descent.finding == flows.SUBJECT_NOT_FOUND:
            return False
        # The authoritative report is always produced, so it can no longer be
        # withheld. A rejected *paraphrase* does not make the answer
        # untrustworthy -- the answer is the rendered report, and the model
        # failing to restate it well is a prompt problem, not a doubt about the
        # finding. That is a real change from before B-439 and is the point of
        # it: the trustworthy answer no longer depends on a model call
        # succeeding.
        return True

    def withheld_because(self) -> tuple[str, ...]:
        """Why a model output is not here, in terms of the descent.

        Never in terms of what the model said. A caller printing this to a user
        gets loci and rules, not the rejected prose.
        """

        reasons: list[str] = []
        if self.paraphrase_status == WITHHELD:
            reasons.extend(str(f) for f in self.paraphrase_grounding.failures)
        if self.correlation_paraphrase_status in (WITHHELD, COVERAGE_LIMITED):
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


#: Collect-per-rung, the pre-epoch behaviour. Kept for one reason and one only:
#: a caller that injects its own ``collector`` opts out of the epoch, and the
#: descent then behaves exactly as it did before item 3 -- which is what lets
#: every test written against the old contract keep passing unmodified, and is
#: the evidence that the evidence *shape* did not change.
#:
#: It is not the live path. `investigate()` builds an epoch.
def origin_prefix_for(device: str) -> str:
    """The host prefix of ``device``'s own loopback, for a reverse-route read.

    `EACH_PATH_INTERFACE` needs the route the **subject** device holds back
    toward where the investigation started, and that route is keyed by the
    origin's loopback. This is the inverse of `inventory_resolver`: name to
    router ID rather than router ID to name.

    Arithmetic over the inventory, never an inference -- the same rule the
    resolver follows, and the same refusal when it cannot be satisfied. A
    device with no router ID raises rather than returning a guess, because a
    guessed prefix would read a real route to somewhere nobody asked about.
    """

    from .inventory_model import load_inventory_file

    for entry in load_inventory_file().devices:
        if entry.name == device:
            router_id = getattr(entry, "router_id", None)
            if router_id:
                return f"{router_id}/32"
            raise ValueError(
                f"{device!r} has no router_id in the inventory, so the route back "
                f"toward it cannot be looked up; a path-scoped rung is unevaluated "
                f"rather than guessing a prefix"
            )
    raise ValueError(f"{device!r} is not in the inventory")


def _collect_for_rung(device: str, rung: flows.Rung, subject: str, *, sender=None) -> dict:
    """Everything one rung needs, keyed by the convention `checks.py` reads.

    Intents land under their own name (`evidence["bgp"]`); templates land under
    `"<template>:<param>"`. `EACH_PHYSICAL_INTERFACE` rungs need one template
    call per physical interface, which is why the interface list is read out of
    the already-collected `interfaces` intent rather than guessed.
    """

    evidence = dict(collect_evidence(device, sender=sender))
    # One definition of how a template parameter is filled, shared with the
    # epoch builder and the re-read. Three copies of this rule is what B-431
    # was about.
    for step in rung.collect:
        if not step.is_template:
            continue
        for key, kwargs in template_calls(step, subject, evidence):
            evidence[key] = run_template(device, step.name, sender=sender, **kwargs)
    return evidence


def _log_window(device: str, *, sender=None, count: int = 200) -> ShapedWindow:
    """The shaped window for one device, with its coverage record.

    A failed or unparseable read is not an empty window -- it is a window with
    no coverage, which `check_absence_coverage` refuses to let anything claim
    absence over. That distinction is the whole point of T-029a and it must not
    be flattened here into "we got nothing back".
    """

    # `count` must be a *string*: every template parameter is parsed from text by
    # `render_command` (canonicalize by reconstruction), so an int is rejected at
    # the boundary rather than coerced. Passing 200 instead of "200" made every
    # log read fail with `count: expected a string, got int` -- OBS-077.
    result = run_template(device, "logging", sender=sender, count=str(count))

    # `commands`, not `outputs`. `run_template` stores output under the same key
    # `run_intent` uses, keyed by the rendered command, precisely so a generic
    # consumer does not have to know which produced it. This function knew the
    # wrong one and silently found nothing.
    commands = (result.get("data") or {}).get("commands") or {}
    raw = next(iter(commands.values()), "") if isinstance(commands, dict) else ""

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
    skew_bound_seconds: float = DEFAULT_SKEW_BOUND_SECONDS,
) -> InvestigationResult:
    """Run one investigation end to end.

    ``analyst`` is the only model in the path. Omit it and the descent runs
    alone, which is a complete result -- the deterministic half is the half that
    finds the cause.

    Evidence is collected **once per device** into an :class:`~.epoch.EvidenceEpoch`
    and reused across every rung, and the symptom and proposed cause are re-read
    at the end. See `epoch.py` for why that is a correctness measure and not
    only an efficiency one.

    ``collector`` opts out of both: a caller supplying its own collector gets the
    pre-epoch behaviour, one collection per rung and no coherence check. That is
    for tests and fixture-driven callers that already hold their evidence -- a
    coherence check over evidence that never came from a device would be
    measuring the test harness.
    """

    the_flow = flows.flow_for(flow)
    resolve = resolver or inventory_resolver

    # The route the *subject* device holds back toward here, for a path-scoped
    # rung. A device with no router ID has no reverse route to look up, and the
    # rung is `unevaluated` rather than falling back to every interface.
    try:
        origin = origin_prefix_for(device)
    except (ValueError, Exception):  # noqa: BLE001 -- an inventory gap, not a crash
        origin = None

    epoch: EvidenceEpoch | None = None
    coherence = None
    if collector is not None:
        collect = collector
    else:
        epoch = collect_epoch(
            the_flow, device, subject, resolver=resolve, sender=sender,
            bound_seconds=skew_bound_seconds, origin_prefix=origin,
        )
        # Every rung reads the same window. `for_device` hands back exactly the
        # dict shape `checks.py` already read, which is what keeps this a change
        # to *when* evidence is gathered rather than to what a check sees.
        collect = lambda d, _rung, _subject: epoch.for_device(d)  # noqa: E731
        coherence = lambda outcomes: check_coherence(  # noqa: E731
            the_flow, outcomes, epoch, subject,
            device=device, resolver=resolve, sender=sender,
        )

    # -- Does the subject exist? Asked before anything is walked (B-459). ----
    #
    # **The input side of the containment boundary.** Everything else in this
    # build guards what a tool *returns*; nothing guarded what a caller
    # *supplies*, and a fabricated peer address produces a fully grounded,
    # correctly cited investigation of a session that does not exist -- with no
    # component malfunctioning, which is why no gate caught it.
    #
    # Only on the epoch path. An injected collector has already decided what
    # evidence exists, so asking it whether the subject is real would be asking
    # the test harness to validate the test.
    if epoch is not None and the_flow.subject_present is not None:
        presence = the_flow.subject_present(epoch.for_device(device), subject)
        if presence.status == "broken":
            refused = DescentResult(
                flow=the_flow.object_type, device=device, subject=subject,
                finding=flows.SUBJECT_NOT_FOUND,
                evidence_keys=tuple(presence.evidence_keys),
                reason=presence.reason,
            )
            return InvestigationResult(
                device=device, subject=subject, flow=flow, descent=refused,
                # Still rendered. The refusal is the answer, and B-439's
                # contract is that the authoritative half is always produced --
                # a `None` here would send a caller to a model's prose for the
                # one result whose value is that it is *not* a claim about the
                # network.
                report=render_report(refused), report_status=EMITTED,
            )

    descent = run_descent(
        the_flow, device, subject,
        collector=collect, resolver=resolve, coherence=coherence,
        origin_prefix=origin,
    )

    # -- The authoritative report. No model, always produced (B-439). --------
    #
    # Rendered from the descent's typed fields, so it cannot differ from what
    # the walk found -- not "checked and found faithful", unable to differ. It
    # is produced whether or not an analyst is configured, which is what makes
    # `--no-model` a complete result rather than a reduced one.
    report = render_report(descent)
    report_status = EMITTED

    cause = descent.cause
    correlation: dict | None = None
    correlation_status = NOT_ATTEMPTED
    coverage: Coverage | None = None
    shaped: ShapedWindow | None = None

    if cause is not None:
        read_window = window or (lambda d: _log_window(d, sender=sender))
        shaped = read_window(cause.device)
        coverage = shaped.coverage
        correlation = render_correlation(descent, shaped)
        correlation_status = EMITTED

    if analyst is None:
        return InvestigationResult(
            device=device, subject=subject, flow=flow, descent=descent,
            report=report, report_status=report_status,
            correlation=correlation, correlation_status=correlation_status,
            coverage=coverage,
        )

    # -- The paraphrase. A model, and nothing downstream may prefer it. ------
    #
    # Graded exactly as the report used to be, and carried in its own field. A
    # consumer that wants the finding reads `report`; a consumer that wants a
    # readable sentence reads `paraphrase` and knows it is not authoritative.
    repairs: list[str] = []

    decoded, fixes = _decode(analyst(build_report_prompt(descent)))
    repairs.extend(fixes)
    paraphrase_grounding = ground_report(decoded, descent)
    paraphrase = decoded if paraphrase_grounding.ok else None
    paraphrase_status = EMITTED if paraphrase_grounding.ok else WITHHELD
    if isinstance(paraphrase, dict):
        paraphrase["authoritative"] = False

    correlation_paraphrase: dict | None = None
    correlation_paraphrase_status = NOT_ATTEMPTED
    correlation_grounding = GroundingResult()

    if cause is not None and shaped is not None:
        decoded, fixes = _decode(analyst(build_correlate_prompt(descent, shaped)))
        repairs.extend(fixes)
        correlation_grounding = ground_correlation(decoded, coverage, shaped)

        if correlation_grounding.ok:
            correlation_paraphrase = decoded
            correlation_paraphrase_status = EMITTED
        elif all(
            f.kind == "absence_claim_exceeds_coverage"
            for f in correlation_grounding.failures
        ):
            # A real answer at the wrong strength. Keeping it and labelling it
            # is strictly better than discarding it: the reader learns both
            # that nothing was found and that the source could not have shown
            # it, which is more than either half alone.
            correlation_paraphrase = decoded
            correlation_paraphrase_status = COVERAGE_LIMITED
        else:
            correlation_paraphrase_status = WITHHELD
        if isinstance(correlation_paraphrase, dict):
            correlation_paraphrase["authoritative"] = False

    return InvestigationResult(
        device=device, subject=subject, flow=flow, descent=descent,
        report=report, report_status=report_status,
        correlation=correlation, correlation_status=correlation_status,
        correlation_grounding=correlation_grounding, coverage=coverage,
        paraphrase=paraphrase, paraphrase_status=paraphrase_status,
        paraphrase_grounding=paraphrase_grounding,
        correlation_paraphrase=correlation_paraphrase,
        correlation_paraphrase_status=correlation_paraphrase_status,
        repairs=tuple(repairs),
        # Duck-typed: an analyst that does not record usage simply has none.
        usage=getattr(analyst, "usage", None),
    )
