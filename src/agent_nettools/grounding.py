"""Verify a model's report against the descent that produced it.

This module is the reason no prompt in `prompts/` asks a model to check its own
work. Evaluation is code that runs every time, whether or not anyone is
watching, and returns the same verdict for the same input. A prompt clause
saying "verify every claim is supported" is a request; this is a gate.

**A failed grounding check means the report is not emitted.** The run returns
the descent result and the grounding failure — never the model's prose. That is
the whole contract: a report that cannot be verified does not reach a human in
any form, not even as "here is what it said, but be careful".

Two checks, and why they are separate
--------------------------------------
:func:`check_grounding` is *citation integrity*: every claim cites something
real, and every citation resolves. It answers "is this report internally
sound?"

:func:`check_chain_coverage` is *completeness against the descent*: every rung
the walk actually read appears as an observation, and the causal chain is
cited. It answers "is this report the argument the descent made?"

A report can pass the first and fail the second, and that failure is the one
worth catching. Consider the measured `broken` descent: five rungs, four of
them the chain that explains the fifth. A report saying only

    "PE2's Gi0/0/0/0 is administratively down."   [obs-1, cited, real key]

is perfectly grounded and is *not the finding*. It has thrown away what makes
the answer trustworthy — the four links an engineer checks one at a time — and
reduced an RCA to an assertion with a label attached. Citation integrity cannot
see that, because nothing in it is false.

So :func:`ground_report` runs both, and **it is the function the runner calls**.
The two components are exported for testing and for callers that genuinely have
only one of the two inputs; a caller reaching for :func:`check_grounding` alone
in the emit path has silently turned the chain requirement off.

Failures never carry the model's prose
---------------------------------------
`GroundingFailure` names a *locus* — `obs-3`, a rung name, an evidence key —
and never a `claim` string. This is deliberate and it is the same reasoning as
`prompt_library`'s (OBS-061): the rule is "a rejected report's prose is not
emitted", and a rule enforced by remembering to redact is a rule that
eventually is not enforced. A failure type that structurally cannot hold a
claim cannot leak one.

Evidence keys *are* included, because an invented key is the thing that failed
and naming it is what makes the failure actionable. They are truncated and
stripped of newlines on the way in, since a key in a rejected report is
model-authored text like any other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .descent import DescentResult

__all__ = [
    "MAX_LOCUS_LENGTH",
    "GroundingFailure",
    "GroundingResult",
    "check_chain_coverage",
    "check_grounding",
    "descent_evidence_keys",
    "ground_report",
    "observation_labels",
]

#: Model-authored text appearing in a failure locus is truncated to this.
MAX_LOCUS_LENGTH = 120


def _locus(value: object) -> str:
    """Render an untrusted value as a single short line.

    Anything from the report is model-authored. A locus is for identifying
    *which* thing failed, so it is capped and flattened rather than passed
    through — a multi-line evidence key would otherwise reformat a log record.
    """

    text = " ".join(str(value).split())
    if len(text) > MAX_LOCUS_LENGTH:
        return text[: MAX_LOCUS_LENGTH - 1] + "…"
    return text


@dataclass(frozen=True)
class GroundingFailure:
    """One reason a report is not emitted.

    ``kind`` is a stable machine-readable slug; ``locus`` identifies what
    failed; ``detail`` explains it in terms of the descent, never in terms of
    what the report said.
    """

    kind: str
    locus: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind} [{self.locus}]: {self.detail}"


@dataclass(frozen=True)
class GroundingResult:
    """The verdict, and enough counts to show it measured something.

    The counts are not decoration. A grounding check over a report with no
    observations and a descent with no cited rungs passes every rule in this
    module by having nothing to test, and `ok is True` would be indistinguishable
    from a real pass. `BUILD-PLAN.md` §0.12: a guardrail that can pass by
    measuring nothing needs the empty case to be visible. Here it is visible in
    the result itself, and :attr:`vacuous` names it.
    """

    failures: tuple[GroundingFailure, ...] = field(default_factory=tuple)
    observations_checked: int = 0
    citations_checked: int = 0
    rungs_covered: int = 0
    rungs_required: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def vacuous(self) -> bool:
        """True when the check passed without examining anything.

        Not an error — an all-`unevaluated` descent legitimately has no rung to
        cite. It is a fact a caller should be able to see rather than infer from
        a bare `ok`.
        """

        return self.ok and not (
            self.observations_checked or self.citations_checked or self.rungs_required
        )

    def merge(self, other: GroundingResult) -> GroundingResult:
        return GroundingResult(
            failures=self.failures + other.failures,
            observations_checked=self.observations_checked + other.observations_checked,
            citations_checked=self.citations_checked + other.citations_checked,
            rungs_covered=self.rungs_covered + other.rungs_covered,
            rungs_required=self.rungs_required + other.rungs_required,
        )

    def summary(self) -> str:
        """What a caller prints instead of the report."""

        if self.ok:
            state = "vacuous pass -- nothing to verify" if self.vacuous else "grounded"
            return (
                f"{state}: {self.observations_checked} observations, "
                f"{self.citations_checked} citations, "
                f"{self.rungs_covered}/{self.rungs_required} rungs cited"
            )
        return f"not grounded ({len(self.failures)} failures): " + "; ".join(
            str(f) for f in self.failures
        )


def observation_labels(count: int) -> tuple[str, ...]:
    """``obs-1``…``obs-N``, the labels `report.v1.txt` tells the model to use.

    Defined here rather than assumed at each use, because the label scheme is a
    contract between the prompt and this module and there is no third place it
    could be written down consistently.
    """

    return tuple(f"obs-{i}" for i in range(1, count + 1))


def descent_evidence_keys(descent: DescentResult) -> frozenset[str]:
    """Every key the descent's checks actually read.

    Deliberately *not* "every key present in the evidence store". A report
    citing `PE2:interface:Gi0/0/0/2` — a real key for an interface nothing in
    this descent looked at — would be an uncited claim wearing a citation. The
    authoritative set is what was read, not what exists.
    """

    keys: set[str] = set(descent.evidence_keys)
    for outcome in descent.outcomes:
        keys.update(outcome.result.evidence_keys)
    return frozenset(keys)


def _sequence(report: object, key: str) -> tuple[list, GroundingFailure | None]:
    """Read a list-valued field from an untrusted report."""

    if not isinstance(report, dict):
        return [], GroundingFailure(
            "malformed_report", key, "the report is not a JSON object"
        )
    value = report.get(key, [])
    if value is None:
        value = []
    if not isinstance(value, list):
        return [], GroundingFailure(
            "malformed_report", key, f"{key!r} must be a list"
        )
    return value, None


def check_grounding(report: dict, evidence_keys: frozenset[str]) -> GroundingResult:
    """Citation integrity. `BUILD-PLAN.md` T-029.

    * Every observation's ``evidence_key`` must be in ``evidence_keys``.
    * Every interpretation's ``based_on`` must reference existing observations.
    * The recommendation is exempt from citation but must carry
      ``requires_human: true``.

    This is one of the two halves of grounding. See :func:`ground_report`.
    """

    failures: list[GroundingFailure] = []

    observations, bad = _sequence(report, "observations")
    if bad is not None:
        return GroundingResult(failures=(bad,))
    interpretations, bad = _sequence(report, "interpretations")
    if bad is not None:
        return GroundingResult(failures=(bad,))

    labels = observation_labels(len(observations))
    known = set(labels)
    citations = 0

    for label, observation in zip(labels, observations, strict=True):
        if not isinstance(observation, dict):
            failures.append(
                GroundingFailure("malformed_observation", label, "not a JSON object")
            )
            continue
        claim = observation.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            failures.append(
                GroundingFailure("empty_claim", label, "observation has no claim")
            )
        key = observation.get("evidence_key")
        if not isinstance(key, str) or not key.strip():
            failures.append(
                GroundingFailure(
                    "uncited_observation",
                    label,
                    "observation cites no evidence key; an observation is a fact read "
                    "off a device and must name where it was read",
                )
            )
            continue
        citations += 1
        if key not in evidence_keys:
            failures.append(
                GroundingFailure(
                    "invented_evidence_key",
                    label,
                    f"cites {_locus(key)!r}, which this descent never read",
                )
            )

    for index, interpretation in enumerate(interpretations, start=1):
        locus = f"interp-{index}"
        if not isinstance(interpretation, dict):
            failures.append(
                GroundingFailure("malformed_interpretation", locus, "not a JSON object")
            )
            continue
        claim = interpretation.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            failures.append(
                GroundingFailure("empty_claim", locus, "interpretation has no claim")
            )
        based_on = interpretation.get("based_on")
        if not isinstance(based_on, list) or not based_on:
            failures.append(
                GroundingFailure(
                    "uncited_interpretation",
                    locus,
                    "interpretation cites no observations; an interpretation is the "
                    "only inferential claim in the report and is exactly the one that "
                    "must be traceable",
                )
            )
            continue
        for reference in based_on:
            citations += 1
            if reference not in known:
                failures.append(
                    GroundingFailure(
                        "dangling_reference",
                        locus,
                        f"cites {_locus(reference)!r}, which is not an observation in "
                        f"this report (it has {len(labels)})",
                    )
                )

    recommendation = report.get("recommendation") if isinstance(report, dict) else None
    if recommendation is not None:
        if not isinstance(recommendation, dict):
            failures.append(
                GroundingFailure(
                    "malformed_recommendation", "recommendation", "not a JSON object"
                )
            )
        elif recommendation.get("requires_human") is not True:
            # `is not True`, not falsiness: the string "false" is truthy, and a
            # recommendation is the one uncited part of the report. It is exempt
            # from citation *because* it is labelled as needing a human, so the
            # label is the entire basis of the exemption.
            failures.append(
                GroundingFailure(
                    "recommendation_not_flagged",
                    "recommendation",
                    "must carry requires_human: true -- it is the only uncited claim "
                    "in the report, and the flag is what buys the exemption",
                )
            )

    return GroundingResult(
        failures=tuple(failures),
        observations_checked=len(observations),
        citations_checked=citations,
    )


def check_chain_coverage(report: dict, descent: DescentResult) -> GroundingResult:
    """Completeness against the descent. The chain is the product.

    `prompts/README.md`: *an uncited rung is an uncited claim, and the report is
    not emitted.* Two rules:

    1. **Every rung the walk read appears as an observation.** A rung is "read"
       when its `CheckResult` carries evidence keys, and the observation must
       cite one of *that rung's* keys — citing some other rung's key is not
       coverage.
    2. **The causal chain is cited by an interpretation.** The union of all
       interpretations' ``based_on`` must include an observation for the cause
       and for every broken rung above it. The union rather than a single
       interpretation, because splitting a five-link chain into two sentences is
       better prose and no weaker an argument; dropping a link is what this
       forbids.

    **`unevaluated` rungs are exempt from rule 1**, and this is a real boundary
    rather than an oversight. Such a rung has no evidence keys — `CheckResult`
    refuses a `healthy` or `broken` verdict with no keys, but "we could not read
    this" has nothing to cite by construction. That the report must *say so*
    is enforced by the prompt's refusal path and its golden case, not here.
    """

    failures: list[GroundingFailure] = []

    observations, bad = _sequence(report, "observations")
    if bad is not None:
        return GroundingResult(failures=(bad,))
    interpretations, _ = _sequence(report, "interpretations")

    labels = observation_labels(len(observations))
    key_to_label: dict[str, str] = {}
    for label, observation in zip(labels, observations, strict=True):
        if isinstance(observation, dict):
            key = observation.get("evidence_key")
            if isinstance(key, str):
                key_to_label.setdefault(key, label)

    required = [o for o in descent.outcomes if o.result.evidence_keys]
    covered = 0
    rung_labels: dict[str, str] = {}
    for outcome in required:
        hit = next(
            (key_to_label[k] for k in outcome.result.evidence_keys if k in key_to_label),
            None,
        )
        if hit is None:
            failures.append(
                GroundingFailure(
                    "uncited_rung",
                    f"rung:{outcome.rung}",
                    f"the walk read this rung on {outcome.device} and found "
                    f"{outcome.status}, but no observation cites any of its evidence "
                    f"keys; an uncited rung is a link the reader cannot check",
                )
            )
            continue
        covered += 1
        rung_labels[outcome.rung] = hit

    # Note the exemption's exact shape: `required` above is keyed on *having
    # evidence keys*, not on the verdict. An `unevaluated` rung that did read
    # something before giving up is therefore still required to be cited; only
    # a rung with nothing to cite is exempt. That is narrower than "unevaluated
    # rungs are exempt" and it is the version that holds.

    chain = [*descent.causal_chain]
    if descent.cause is not None:
        chain.append(descent.cause)

    if chain:
        cited: set[str] = set()
        for interpretation in interpretations:
            if isinstance(interpretation, dict):
                based_on = interpretation.get("based_on")
                if isinstance(based_on, list):
                    cited.update(r for r in based_on if isinstance(r, str))

        for outcome in chain:
            label = rung_labels.get(outcome.rung)
            if label is None:
                # Already reported as uncited_rung; reporting it twice would
                # make one defect look like two.
                continue
            if label not in cited:
                failures.append(
                    GroundingFailure(
                        "chain_link_not_argued",
                        f"rung:{outcome.rung}",
                        f"{label} is present but no interpretation cites it; this rung "
                        f"is part of the causal chain and the chain is the argument, "
                        f"not decoration",
                    )
                )

    return GroundingResult(
        failures=tuple(failures),
        rungs_covered=covered,
        rungs_required=len(required),
    )


def ground_report(report: dict, descent: DescentResult) -> GroundingResult:
    """The gate. Both checks, over the keys the descent actually read.

    **This is the function the emit path calls.** Reaching for
    :func:`check_grounding` alone there passes every internally-consistent
    report, including one that names the cause and drops the four rungs that
    explain it — which is the failure the chain requirement exists to catch.
    """

    keys = descent_evidence_keys(descent)
    return check_grounding(report, keys).merge(check_chain_coverage(report, descent))
