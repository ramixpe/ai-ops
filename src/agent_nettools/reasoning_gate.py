"""The reasoning gate — B-101 (the typed decision object) and B-102 (code-
enumerated candidates), built together per `docs/design/reasoning-gate.md`,
the operator-approved design this module implements exactly and no wider.

**What this module is not.** It does not call a model, and nothing in it
re-enters a descent. That is B-103 — *the narrowing pass* — and it is
deliberately not built here: `reasoning-gate.md` holds it pending B-106, and
`descent.py`'s own rule ("no model call anywhere, ever") is not something this
module gets to relax by proximity. This module only does two things: define
the two shapes a gate decision may take, and enumerate the candidates a
`NarrowRequest` may point at. Wiring either into a live investigation is a
separate, later task.

Why every existing model call is decorative, and why this one cannot be
--------------------------------------------------------------------------
`grounding.py`'s whole argument is that a model paraphrasing a conclusion code
already reached can be deleted with the answer unchanged, because
`ground_report` refuses to emit a paraphrase that fails verification. The gate
is the first place that stops being true: once a `NarrowRequest` is allowed to
change what a later rung collects, the model has started influencing *which
evidence exists*, not merely how it is described. Two runs of the same
investigation can then legitimately differ. That is exactly why the shape has
to be this narrow — the smaller the door, the smaller the blast radius of
letting a model stand behind it.

The shape (B-101)
------------------
One decision, two members, closed:

    NarrowRequest(target: Candidate)   # "look closer at this"
    Stop(reason: str)                  # "nothing more to ask"

There is no third shape, and in particular **no shape that carries a verdict,
a cause, or a finding**. `Stop.reason` is the *only* field either dataclass
carries that a model's own prose may occupy — the same free-text-quoted-not-
trusted status `GroundingFailure.detail` gives evidence-key text. A model
cannot return "the cause is X" through this interface, not because such a
payload is filtered out somewhere, but because neither `NarrowRequest` nor
`Stop` has a field it could occupy. `reasoning-gate.md`'s bound is explicit
about why that matters: *the verdict stays code's* — after a narrowing pass
(when B-103 exists), the descent re-runs its own checks over the enlarged
evidence. The model changes what was looked at, never what it means.

"Unrepresentable", demonstrated rather than asserted
-------------------------------------------------------
Two independent mechanisms, deliberately redundant, because a gate that relied
on only one of them would be one bug away from the failure it exists to
prevent:

1. **The dataclasses themselves.** Neither `NarrowRequest` nor `Stop` accepts
   an unknown keyword — that is what a frozen `@dataclass` with no `**kwargs`
   already gives for free, and it is the same discipline
   `checks.CheckResult` and `grounding.GroundingFailure` already lean on.
   `NarrowRequest(target="GigabitEthernet0/0/0/9")` — a bare string standing
   in for a fabricated target — raises `TypeError` in `__post_init__` before
   the object exists. Not "was refused"; there was never a `NarrowRequest`
   holding that string, at any point, for anything downstream to inspect.

2. **The wire schema `parse_decision` reads has no field for a target's
   identity at all.** A `"narrow"` decision carries an integer `index`, never
   a `target` string. There is no code path in this module that takes text a
   model wrote and turns it into the *identity* of the object being narrowed
   on — the only value that can ever become a `Candidate` is one this module
   already built from observed evidence and put in a list, and the model's
   entire contribution is choosing a position in that list. An index outside
   `range(len(candidates))` cannot degrade into "the nearest real one" or
   silently wrap (`candidates[-1]` on a negative index is checked for
   explicitly, below) — it is refused, in full, with nothing constructed.

Candidates are enumerated by code (B-102)
-------------------------------------------
`Candidate` is never built from a model's text. `candidates_from_evidence`
reads exactly the sections a descent's own checks already read —
`evidence["interfaces"]`, `evidence["bgp"]`, `evidence["isis"]` — and returns
one `Candidate` per object those sections actually named, in the order the
device itself listed them. This is `descent._physical_interfaces`'s own rule
(D7: candidates are computed by code from what was actually seen, never named
by a model), generalised from "physical interfaces for a path rung" to "every
object a `NarrowRequest` might legitimately point at".

`candidates_for_descent` adds the other bound `reasoning-gate.md` states:
**no new devices.** It is not merely documented here — the function has no
parameter through which a caller can name a device outside
`{descent.device} | {outcome.device for outcome in descent.outcomes}`, so a
narrowing pass built on top of it (B-103, not this module) cannot reach a
device the evidence epoch never opened without changing this function's
signature, which is the point: the constraint lives in what the function is
able to ask for, not in a comment asking a future caller to remember it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .checks import evidence_key
from .descent import DescentResult

__all__ = [
    "Candidate",
    "GateDecision",
    "GateRefusal",
    "NarrowRequest",
    "Stop",
    "candidates_for_descent",
    "candidates_from_evidence",
    "parse_decision",
]


@dataclass(frozen=True)
class Candidate:
    """One object a descent's own evidence actually named.

    Never constructed from a model's text — see the module docstring.
    ``evidence_key`` ties the candidate back to the exact section it was read
    from, in :func:`checks.evidence_key`'s own convention, so a future
    narrowing pass (B-103, not built here) has a citation for *why* this
    candidate was even on offer, the same way a `CheckResult` must cite what
    it read.
    """

    #: What kind of object this is -- "interface", "bgp_peer", or
    #: "isis_adjacency" today. Closed only by what `candidates_from_evidence`
    #: currently enumerates, not by an enum -- a new object type comes from
    #: D5's flow registry and needs no change here beyond one more helper.
    kind: str
    #: The device's own spelling -- an interface name as `show interfaces
    #: brief` printed it, a peer address as `show bgp summary` printed it, a
    #: system-id as `show isis neighbors` printed it. Never normalised, for
    #: the same reason `path_interfaces` returns the device's own spelling
    #: (descent.py): a normalised name a check does not recognise is a
    #: candidate nothing downstream can actually act on.
    id: str
    #: Which device this candidate was observed on. Set by
    #: `candidates_from_evidence`, never by a caller -- see
    #: `candidates_for_descent`'s "no new devices" bound.
    device: str
    evidence_key: str


@dataclass(frozen=True)
class NarrowRequest:
    """"Look closer at this." The only field is a :class:`Candidate`.

    The type is the enforcement. ``target`` cannot hold a bare string, a dict,
    or anything else a model might write directly — only an instance of
    :class:`Candidate`, and the only sanctioned way to produce one of those is
    :func:`candidates_from_evidence` / :func:`candidates_for_descent`, which
    read observed evidence and nothing else. See :func:`parse_decision` for
    the one path that turns a model's raw output into this shape, which
    selects a candidate by index rather than ever reading an identity from the
    model's own text.
    """

    target: Candidate

    def __post_init__(self) -> None:
        if not isinstance(self.target, Candidate):
            raise TypeError(
                f"NarrowRequest.target must be a Candidate this module "
                f"enumerated from observed evidence, got "
                f"{type(self.target).__name__!r}; a model may select an index "
                f"into a candidate list, never author a target directly"
            )


@dataclass(frozen=True)
class Stop:
    """"Nothing more to ask." ``reason`` is free text a model wrote.

    The one field either shape carries that a model's own prose may occupy —
    quoted and inert, crossing the projector like any other model output, the
    same discipline `GroundingFailure.detail` gives text that must be shown
    but never trusted. Nothing about this field changes control flow: the
    descent that produced the evidence this gate is offered has already
    reached its own verdict by the time a `Stop` exists, and this module does
    not read `reason` for anything.
    """

    reason: str


#: The whole vocabulary. Nothing else parses -- see `parse_decision`. A
#: verdict, a cause, or a finding has no home in either member, by
#: construction: neither dataclass above declares a field for one.
GateDecision = NarrowRequest | Stop


class GateRefusal(ValueError):
    """Raised when a model's raw output cannot become either shape.

    Never carries anything from the payload beyond short, fixed vocabulary —
    a candidate count, an index, which of "narrow"/"stop" was expected. Never
    the payload's own free-form ``decision`` value or any other field a model
    might have filled with arbitrary text: a refusal object that could carry
    a model's prose would be exactly the leak `GroundingFailure` (grounding.py)
    and `Stop.reason` above are built to avoid elsewhere. The one field a
    model's prose may reach, `Stop.reason`, is only ever populated *after* the
    shape it belongs to has already been accepted.
    """


def candidates_from_evidence(device: str, evidence: Mapping[str, Any]) -> tuple[Candidate, ...]:
    """Every interface, BGP peer, and IS-IS adjacency ``device``'s own
    evidence named — and nothing else.

    This is B-102's whole mechanism. Each kind is read from exactly the
    section its own rung already reads (`evidence["interfaces"]`,
    `evidence["bgp"]`, `evidence["isis"]`), in the device's own listed order,
    de-duplicated by first occurrence. A section that is missing, unparsed, or
    not a dict contributes nothing — silently, the same rule
    `descent._physical_interfaces` and `checks.parsed_records` already apply:
    absence is not an object to offer, not a crash.

    Nothing here decides whether a candidate is a *good* thing to narrow on.
    That is `parse_decision`'s and (eventually) B-103's business. This
    function answers exactly one question: what does the evidence say exists.
    """

    candidates: list[Candidate] = []
    candidates.extend(_interface_candidates(device, evidence))
    candidates.extend(_bgp_peer_candidates(device, evidence))
    candidates.extend(_isis_adjacency_candidates(device, evidence))
    return tuple(candidates)


def _records(evidence: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    section = evidence.get(key)
    if not isinstance(section, dict):
        return []
    data = section.get("data")
    if not isinstance(data, dict):
        return []
    parsed = data.get("parsed")
    if not isinstance(parsed, dict):
        return []
    records = parsed.get("records")
    return records if isinstance(records, list) else []


def _dedup(values: list[str]) -> list[str]:
    """First-occurrence order, not a set -- the device's own table is a list,
    not a bag, and a candidate's position is what a model's index selects."""

    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _interface_candidates(device: str, evidence: Mapping[str, Any]) -> list[Candidate]:
    names = _dedup(
        [r.get("interface") for r in _records(evidence, "interfaces") if r.get("interface")]
    )
    return [
        Candidate("interface", name, device, evidence_key(device, "interfaces", name))
        for name in names
    ]


def _bgp_peer_candidates(device: str, evidence: Mapping[str, Any]) -> list[Candidate]:
    peers = _dedup(
        [r.get("neighbor") for r in _records(evidence, "bgp") if r.get("neighbor")]
    )
    return [
        Candidate("bgp_peer", peer, device, evidence_key(device, "bgp", peer))
        for peer in peers
    ]


def _isis_adjacency_candidates(device: str, evidence: Mapping[str, Any]) -> list[Candidate]:
    system_ids = _dedup(
        [r.get("system_id") for r in _records(evidence, "isis") if r.get("system_id")]
    )
    return [
        Candidate("isis_adjacency", sid, device, evidence_key(device, "isis", sid))
        for sid in system_ids
    ]


def _devices_touched(descent: DescentResult) -> tuple[str, ...]:
    """The closed device set a descent actually opened evidence for.

    `RungOutcome.device` is a single device name for every rung today, but a
    `PATH`-scoped rung (declared, not yet used by any flow -- see
    `flows.DeviceScope.PATH`) joins several with ``", "``
    (`descent.run_descent`'s own `", ".join(devices)`). Split defensively
    rather than let a future PATH rung silently shrink this set to one
    unmatched, comma-containing string that queries nothing.
    """

    seen: list[str] = []
    for entry in (descent.device, *(o.device for o in descent.outcomes)):
        for name in entry.split(", "):
            name = name.strip()
            if name and name not in seen:
                seen.append(name)
    return tuple(seen)


def candidates_for_descent(
    descent: DescentResult,
    evidence_for: Callable[[str], Mapping[str, Any]],
) -> tuple[Candidate, ...]:
    """Every candidate across every device the descent actually touched.

    ``evidence_for`` is called only for devices in
    :func:`_devices_touched` — ``descent.device`` plus every rung outcome's
    own device, the identical closed set `investigation.py` already computes
    for operator notes (`_notes_for_devices`'s ``touched``). This function
    takes no other way to name a device, which is how `reasoning-gate.md`'s
    "no new devices" bound is enforced here rather than merely stated: there
    is no argument through which a caller can widen the set beyond what this
    descent's own evidence epoch already opened.
    """

    candidates: list[Candidate] = []
    for device in _devices_touched(descent):
        candidates.extend(candidates_from_evidence(device, evidence_for(device)))
    return tuple(candidates)


def parse_decision(raw: Mapping[str, Any], candidates: Sequence[Candidate]) -> GateDecision:
    """Turn a model's raw decoded output into a :data:`GateDecision`, or refuse.

    The only function in this module that touches model output, and the only
    one that may raise :class:`GateRefusal`. Two decisions parse:

    * ``{"decision": "stop", "reason": <non-empty str>}`` -> :class:`Stop`.
    * ``{"decision": "narrow", "index": <int>}`` -> :class:`NarrowRequest`,
      built by indexing into ``candidates`` -- **never** by reading any other
      field of ``raw``. There is no ``target``/``id``/``name`` field this
      function reads for a "narrow" decision; the model's only lever is which
      position in a list code already built.

    Anything else refuses: an unknown ``decision``, a missing or non-integer
    ``index``, or an ``index`` outside ``range(len(candidates))``. Both ends
    of the range are checked explicitly — Python's own negative indexing
    would otherwise let ``index=-1`` silently resolve to the *last* enumerated
    candidate instead of being refused, which is precisely a fabricated
    selection wearing a syntactically valid one.

    A boolean ``index`` is refused too: ``bool`` is a subclass of ``int`` in
    Python, and accepting one would quietly turn `true`/`false` into
    `candidates[1]`/`candidates[0]` for any caller that only checked
    ``isinstance(index, int)``.
    """

    if not isinstance(raw, Mapping):
        raise GateRefusal("a gate decision must be a JSON object")

    decision = raw.get("decision")

    if decision == "stop":
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise GateRefusal("a 'stop' decision must carry a non-empty 'reason'")
        return Stop(reason=reason)

    if decision == "narrow":
        index = raw.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise GateRefusal("a 'narrow' decision must carry an integer 'index'")
        if index < 0 or index >= len(candidates):
            raise GateRefusal(
                f"index {index} is out of range for {len(candidates)} enumerated "
                "candidate(s); a narrowing target may only be one code already "
                "offered"
            )
        return NarrowRequest(target=candidates[index])

    raise GateRefusal("decision must be 'narrow' or 'stop'; no third shape parses")
