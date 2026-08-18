"""The authoritative report, rendered by code from typed fields (B-439).

Reviewer A, §3.2: `check_grounding` proves citation *topology*, not truth. A
report can cite an interface-down key while asserting a chassis power failure
and pass every check, because nothing inspects the relationship between a claim
and the result it cites. Deterministic entailment over natural language is not
available, so the response is **structural**: stop asking a model to state the
finding, and generate the authoritative statement from the typed fields the
descent already produced.

What this actually changes
---------------------------
Everything the report prompt asked the model for was already in
:class:`~.descent.DescentResult`. The prompt asked it to *transcribe* typed
data into prose and cite the keys it came from — and then a gate checked whether
the transcription was faithful. **Rendering it directly removes both the step
and the need for the gate**, which is the difference between verifying an
answer and not needing to.

Three consequences, and the third is the one worth arguing about:

1. **The authoritative report cannot be wrong about the descent**, because it is
   a projection of the descent. Not "checked and found correct" — unable to
   differ. That is the §3.2 answer.
2. **It removes a model call from the interactive path.** Measured: ~35 s of a
   40 s single-call run, ~105 s of round 1's 114 s (OBS-108). B's speed
   objection is answered here, and it was never answered by the epoch.
3. **Grounding the authoritative report is now vacuous by construction**, and
   that must be labelled rather than reported as a pass. A gate that checks code
   against itself and reports success is silent-failure shape 5 wearing the
   badge of the shape it was built to prevent. See :func:`report_grounding_note`.

What is left for a model
-------------------------
Prose, and only prose. A rendered report is accurate and flat; an engineer
reading twenty of them wants the sentence that says which one matters. So a
model may still produce a **paraphrase**, and it is marked
``authoritative: false`` and grounded exactly as before. Nothing downstream may
prefer it: the payload carries the rendered report under ``content`` and the
paraphrase beside it, never instead of it.

On the correlation
-------------------
The same argument applies further than §3.2 claimed, and the operator asked for
it to be assessed: *finding events in a window matching an object, in order, is
deterministic — only the prose is not.* Measured against the correlate contract,
four of its five fields are code (see :func:`render_correlation`). Only
``summary`` is prose.
"""

from __future__ import annotations

import re
from typing import Any

from . import flows
from .descent import DescentResult, RungOutcome
from .log_window import ShapedWindow

__all__ = [
    "COMMIT_MNEMONICS",
    "next_check_for",
    "render_correlation",
    "render_report",
    "report_grounding_note",
]

#: Mnemonics that mean *a human changed the configuration*. Declared rather than
#: pattern-matched on the word "commit", the same discipline as
#: `log_window.NoiseRule` and `interface_kind`: a reviewable table, and anything
#: not in it is simply not a commit.
COMMIT_MNEMONICS: tuple[str, ...] = (
    "CONFIG-6-DB_COMMIT",
    "MGBL-CONFIG-6-DB_COMMIT",
    "CONFIG-3-DB_COMMIT",
)

#: What a human should look at next, per terminal finding. One line each, keyed
#: by finding so a new finding cannot silently inherit another's advice.
#:
#: `requires_human` is **always** true. D3's ceiling: this tool localises, it
#: does not remediate, and a rendered "next check" that read as an instruction
#: to a machine would be the first step across that line.
_NEXT_CHECK: dict[str, str] = {
    "peer_not_established": (
        "Read the peer's own view of the session: the far end may be refusing, "
        "or configured with a different AS or authentication."
    ),
    "transport_blocked": (
        "The TCP socket to port 179 is not established. Check for an ACL, a "
        "control-plane policy, or an administratively shut neighbour."
    ),
    "peer_unreachable_no_route": (
        "There is no route to the peer's loopback. Check the IGP on the path "
        "before looking at BGP again."
    ),
    "igp_isolated": (
        "The subject device has no IS-IS adjacencies. Check its interfaces and "
        "IS-IS configuration on the device itself."
    ),
    "interface_line_down": (
        "A physical interface on the path is down. Check the port, the optic "
        "and the far end before anything above it."
    ),
    # B-107. The one finding the isis_adjacency flow can reach that no other
    # flow produces: the interface underneath is healthy and the adjacency
    # still has not formed, which puts the cause on the configuration axis this
    # build deliberately does not read (B-104).
    "adjacency_not_up": (
        "The IS-IS adjacency on this interface has not formed, though the "
        "interface itself is healthy. Check IS-IS configuration on both ends -- "
        "area, authentication, network type, or whether IS-IS is enabled on "
        "this interface at all."
    ),
    flows.CAUSE_NOT_LOCALISED: (
        "Every layer beneath the symptom is healthy, so the cause is not in this "
        "ladder. Check configuration and policy on both ends."
    ),
    flows.ALL_LAYERS_HEALTHY: (
        "No fault on this dependency path. If a problem is being reported, it is "
        "about something this flow does not cover."
    ),
    flows.NO_FAULT_ON_PATH: (
        "The session is healthy. The broken rungs below are real and are not on "
        "the path between these two endpoints -- check them on their own terms."
    ),
    flows.UNDETERMINED: (
        "A rung could not be read, so nothing below it was evaluated. Fix the "
        "collection failure and run again."
    ),
    flows.SUBJECT_NOT_FOUND: (
        "This device has no such object. Check the name or address against the "
        "device's own inventory of them -- the reason lists what it does have. "
        "Nothing was investigated, so nothing here is a statement about the "
        "network."
    ),
    flows.TEMPORALLY_INCOHERENT: (
        "The fabric changed while it was being read, so these observations do "
        "not describe one state. Run again once it has settled."
    ),
}


def next_check_for(finding: str) -> str:
    """The recommendation for one finding, or an honest fallback.

    A finding with no entry gets a generic line rather than a `KeyError` -- but
    the generic line **says** it is generic, so an unmapped finding is visible
    in the output instead of reading as considered advice.
    """

    return _NEXT_CHECK.get(
        finding,
        f"No specific guidance is registered for {finding!r}. Read the rung table "
        f"below and treat the lowest broken rung as the starting point.",
    )


def _first_key(outcome: RungOutcome) -> str | None:
    keys = outcome.result.evidence_keys
    return keys[0] if keys else None


def render_report(descent: DescentResult) -> dict[str, Any]:
    """The authoritative report, generated from the descent alone.

    Same schema as `prompts/report.v1.txt` asks a model for, so every existing
    consumer -- the payload, the grounding functions, the CLI renderer -- reads
    it unchanged.

    **Every observation cites the key its own check read.** Not a key selected
    to look supporting: `CheckResult.evidence_keys` records what the predicate
    actually consulted, so the citation is a fact about the code path rather
    than a claim about relevance. A rung that recorded no keys emits no
    citation, which `check_grounding` treats as a missing citation -- correctly,
    and visibly.
    """

    total = len(descent.outcomes)
    observations: list[dict[str, Any]] = []
    for position, outcome in enumerate(descent.outcomes, start=1):
        key = _first_key(outcome)
        # `1/5`, and the device on every line. See `rungs_examined` below for
        # why the numbering is in the prose rather than only in the structure.
        claim = (
            f"{position}/{total} {outcome.rung} on {outcome.device} is {outcome.status}"
            + (f": {outcome.result.reason}" if outcome.result.reason else "")
        )
        entry: dict[str, Any] = {"claim": claim, "position": position, "of": total}
        if key is not None:
            entry["evidence_key"] = key
        observations.append(entry)

    index = {o.rung: n for n, o in enumerate(descent.outcomes, start=1)}
    interpretations: list[dict[str, Any]] = []

    cause = descent.cause
    names_a_cause = cause is not None and descent.finding not in {
        flows.UNDETERMINED, flows.NO_FAULT_ON_PATH, flows.TEMPORALLY_INCOHERENT,
    }

    if names_a_cause:
        chain = descent.causal_chain
        based_on = [f"obs-{index[o.rung]}" for o in (*chain, cause) if o.rung in index]
        if chain:
            consequences = ", ".join(o.rung for o in chain)
            claim = (
                f"{cause.rung} on {cause.device} is the lowest broken layer, and "
                f"{consequences} {'are' if len(chain) > 1 else 'is'} broken above it -- "
                f"consistent with {cause.rung} being the cause of the reported symptom."
            )
        else:
            claim = (
                f"{cause.rung} on {cause.device} is the only broken layer and nothing "
                f"beneath it is broken, so the cause is at this layer."
            )
        interpretations.append({"claim": claim, "based_on": based_on})
    elif descent.finding == flows.NO_FAULT_ON_PATH:
        broken = [o for o in descent.outcomes if o.status == "broken"]
        interpretations.append({
            "claim": (
                f"The symptom rung is healthy, so nothing beneath it explains anything. "
                f"{len(broken)} broken rung(s) were observed and are not on the "
                f"dependency path between {descent.device} and {descent.subject}."
            ),
            "based_on": [f"obs-{index[o.rung]}" for o in ([descent.outcomes[0]] + broken)
                         if o.rung in index],
        })
    elif descent.finding == flows.ALL_LAYERS_HEALTHY:
        interpretations.append({
            "claim": (
                f"Every layer between {descent.device} and {descent.subject} is healthy. "
                f"No fault was found on this dependency path."
            ),
            "based_on": [f"obs-{n}" for n in index.values()],
        })
    elif descent.finding == flows.UNDETERMINED:
        interpretations.append({
            "claim": (
                f"The walk stopped at an unread rung, so nothing below it was "
                f"evaluated and no cause can be named. Reason: {descent.reason}"
            ),
            "based_on": [f"obs-{n}" for n in index.values()],
        })
    elif descent.finding == flows.SUBJECT_NOT_FOUND:
        # No rungs, so no observations to cite. The refusal *is* the report, and
        # it must still be a report rather than a null -- B-439's contract is
        # that the authoritative half is always produced, and a caller handed
        # `None` here would fall back to a model's prose for the one result
        # whose whole value is that it is not a claim about the network.
        interpretations.append({
            "claim": (
                f"No investigation was run: {descent.reason} Nothing here is a "
                f"statement about the network."
            ),
            "based_on": [],
        })
    elif descent.finding == flows.TEMPORALLY_INCOHERENT:
        interpretations.append({
            "claim": (
                "The fabric changed between the walk and the re-read, so these "
                "observations do not describe one state and no causal claim is "
                "supported by them."
            ),
            "based_on": [f"obs-{n}" for n in index.values()],
        })

    return {
        "authoritative": True,
        "generated_by": "code",
        "finding": descent.finding,
        # **A stated count, and numbered observations.** Not enforcement, and it
        # must not be described as one: it makes an omission *detectable* at a
        # boundary where nothing can be enforced.
        #
        # Observed 2026-08-17 (OBS-115): a chat client restated a correct
        # five-rung report as four, dropping `route_to_peer`, and attributed two
        # PE2 rungs to RR1. That happened outside our `paraphrase` field and
        # outside every gate this build has -- on a surface we do not own. The
        # only defence available there is a report shape in which "5 rungs
        # examined", "1/5 ... on RR1" through "5/5 ... on PE2" makes a
        # four-item restatement visibly incomplete to a human reading both.
        "rungs_examined": total,
        "observations": observations,
        "interpretations": interpretations,
        "recommendation": {
            "next_check": next_check_for(descent.finding),
            # Always. D3's ceiling -- this tool localises and hands over.
            "requires_human": True,
        },
    }


def report_grounding_note() -> str:
    """Why the authoritative report is not graded, said out loud.

    Running `ground_report` over :func:`render_report`'s output would compare
    code against itself and report a pass. **That is not a weaker check, it is a
    misleading one** -- shape 5 in the one place in this build whose job is to
    prevent shape 5. So it is not run, and the reason is emitted in its place.
    """

    return (
        "not graded: the authoritative report is generated from the descent's "
        "typed fields, so grounding it would check code against itself and "
        "always pass. The model paraphrase, where present, is graded normally."
    )


# --------------------------------------------------------------------------- #
# The correlation
# --------------------------------------------------------------------------- #

#: Tokens worth matching a log line against, extracted from a cause. Interface
#: names and IPv4 addresses -- the two identifier shapes IOS-XR log text carries.
_IDENTIFIER = re.compile(
    r"\b(?:[A-Za-z]{2,}[\d/.\-]+\d|(?:\d{1,3}\.){3}\d{1,3})\b"
)


def _subjects_of(descent: DescentResult) -> tuple[str, ...]:
    """Identifiers a log line must mention to be about this cause.

    Taken from the cause's own reason and evidence keys, not from the whole
    descent: a timeline for `interface_line_down on Gi0/0/0/1` should not
    collect every line mentioning the BGP peer address.
    """

    cause = descent.cause
    if cause is None:
        return ()
    haystack = " ".join(
        [cause.result.reason or "", *(cause.result.evidence_keys or ())]
    )
    found = {m.group(0) for m in _IDENTIFIER.finditer(haystack)}
    return tuple(sorted(found))


def render_correlation(descent: DescentResult, window: ShapedWindow) -> dict[str, Any]:
    """A timeline built by code, and the assessment the operator asked for.

    **Four of the correlate contract's five fields are deterministic:**

    ==================  =======================================================
    ``timeline``        log records whose text or mnemonic mentions an
                        identifier belonging to the cause, in the order the
                        device emitted them. Filtering and ordering, no
                        judgement
    ``found``           whether that list is non-empty
    ``followed_a_commit`` whether a :data:`COMMIT_MNEMONICS` line precedes the
                        first matched event in the window
    ``recurrence``      how many matched events there are, and how many distinct
                        mnemonics -- counting, not interpretation
    ``summary``         **prose. The only part a model is needed for**
    ==================  =======================================================

    So the operator's reading holds: the correlate model call buys one sentence.
    That sentence has value -- it is what makes a timeline readable -- but it is
    not load-bearing and must not be, which is why it is the *only* field a
    paraphrase may supply.

    **The honest limit of the deterministic version.** Identifier matching finds
    events *mentioning* the object; it does not know whether an event is
    causally related to the fault. A model did not know that either -- it was
    inferring it from the same text -- but it produced fluent sentences that
    read as though it did. Code that says "3 events mention Gi0/0/0/1" claims
    exactly what it checked.
    """

    subjects = _subjects_of(descent)
    timeline: list[dict[str, Any]] = []
    commit_before = False
    first_match_index: int | None = None

    for position, record in enumerate(window.records):
        text = f"{record.get('text', '')} {record.get('mnemonic', '')}"
        if record.get("mnemonic", "") in COMMIT_MNEMONICS and first_match_index is None:
            commit_before = True
        if subjects and any(s in text for s in subjects):
            if first_match_index is None:
                first_match_index = position
            timeline.append({
                "at": record.get("timestamp", ""),
                "event": record.get("text", ""),
                "mnemonic": record.get("mnemonic", ""),
            })

    mnemonics = {entry["mnemonic"] for entry in timeline}
    found = bool(timeline)

    if found:
        summary = (
            f"{len(timeline)} log event(s) on {descent.cause.device} mention "
            f"{', '.join(subjects)}, across {len(mnemonics)} distinct mnemonic(s)."
        )
        recurrence = (
            f"{len(timeline)} matching event(s), {len(mnemonics)} distinct mnemonic(s), "
            f"within the {len(window.records)} retained records"
        )
    else:
        summary = (
            f"No log event in the retained window mentions "
            f"{', '.join(subjects) if subjects else 'the cause'}."
        )
        recurrence = "none in the retained window"

    return {
        "authoritative": True,
        "generated_by": "code",
        "matched_identifiers": list(subjects),
        "timeline": timeline,
        "correlation": {
            "found": found,
            "summary": summary,
            # Only assertable as true when a commit line actually precedes the
            # first matched event. Absence of a commit line in a *partial*
            # window is not evidence there was none -- that is what the coverage
            # record beside this is for, and why this is never asserted false
            # with any confidence.
            "followed_a_commit": commit_before and found,
            "recurrence": recurrence,
        },
    }
