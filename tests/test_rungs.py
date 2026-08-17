"""What makes a rung a rung — the discrimination criterion, audited (B-437).

Reviewer A, §2.2: rungs are easy to define by *available CLI view* and that is a
menu, not a ladder. A rung is a falsifiable dependency hypothesis, and the part
of that which a test can hold is the **separating case**: at least one captured
or injected fault where this rung is broken and the one below it healthy.

Without such a case a boundary is notional. The two rungs might be reading the
same underlying state and would never be observed disagreeing, which is exactly
the defect B-432 found at rungs 1 and 2 and which nothing would have surfaced.

**This audit is the enforcement.** A rung added later with no separating
evidence fails here rather than being noticed at review, which is what "binding
on every future rung" has to mean to be worth writing down.
"""

from __future__ import annotations

import pytest

from agent_nettools import flows

#: Every rung vector this project has observed or injected, and where from.
#:
#: `synthetic` marks a vector that was **composed** rather than captured. It is
#: kept and labelled rather than excluded: a synthetic vector is fine for
#: exercising the finding logic and is **not** a separating case, because the
#: thing it would prove — that the fabric can produce this boundary — is the
#: thing it assumes.
VECTORS: list[tuple[str, str, bool]] = [
    ("fixture-healthy-label", "HHHHH", False),
    ("fixture-broken-label", "BBBBB", False),
    ("fixture-RR1-to-PE1-broken", "BBBHH", False),
    ("round-1-igp-shut-PE3", "BBBBH", False),
    ("round-2-transport-PE1", "BBHHH", False),
    ("round-3-bgp-admin-shut-PE2", "BBHHH", False),
    ("round-4-true-negative-PE2", "HHHHB", False),
    ("cause-not-localised", "BHHHH", True),
]

#: Boundaries with no captured separating case, and why. Pinned as a set so a
#: round that closes one fails this test — the same shape as
#: `test_field_audit.py`'s counts, where the number moving is the signal.
UNSEPARATED: set[tuple[int, int]] = {(1, 2)}


def _flow_rungs():
    return flows.flow_for("bgp_session").descent


def test_the_vector_table_matches_the_flow_it_describes():
    """Anti-vacuity. A five-rung ladder audited with four-character vectors
    would silently stop covering the last boundary."""

    width = len(_flow_rungs())
    assert width == 5
    for name, vector, _ in VECTORS:
        assert len(vector) == width, f"{name} has {len(vector)} rungs, flow has {width}"
        assert set(vector) <= {"B", "H"}, name


@pytest.mark.parametrize("boundary", range(4), ids=[f"rung{n+1}|rung{n+2}" for n in range(4)])
def test_each_rung_boundary_is_separated_by_a_captured_case(boundary):
    """The criterion's third part, per boundary.

    A boundary is separated when some **captured** case has this rung broken and
    the one below it healthy. `UNSEPARATED` records the ones that are not, so
    the gap is a reviewed decision rather than an absence nobody counted.
    """

    rungs = _flow_rungs()
    pair = (boundary + 1, boundary + 2)

    captured = [
        name for name, vector, synthetic in VECTORS
        if not synthetic and vector[boundary] == "B" and vector[boundary + 1] == "H"
    ]

    if pair in UNSEPARATED:
        assert not captured, (
            f"{rungs[boundary].name}|{rungs[boundary + 1].name} is now separated by "
            f"{captured} -- remove it from UNSEPARATED and say which round closed it"
        )
        pytest.skip(f"{pair} is a known gap: see B-463")

    assert captured, (
        f"no captured case separates {rungs[boundary].name} from "
        f"{rungs[boundary + 1].name}; the boundary is notional"
    )


def test_the_only_gap_is_the_one_b432_predicted():
    """B-432 gave rung 2 a distinct subsystem and noted no fault yet separates
    it. Measured across every vector this project has, that is still the only
    unseparated boundary -- and round 3, which was meant to close it, produced
    `BBHHH` instead because the admin shutdown broke the socket too."""

    assert UNSEPARATED == {(1, 2)}

    synthetic_only = [
        name for name, vector, synthetic in VECTORS
        if synthetic and vector[0] == "B" and vector[1] == "H"
    ]
    assert synthetic_only == ["cause-not-localised"], (
        "the 1|2 boundary is separated only by a composed vector, which assumes "
        "what it would need to prove"
    )


def test_every_rung_observes_a_distinct_source():
    """The criterion's second part, as far as a test can reach it.

    Not "a distinct subsystem" -- that is a judgement about what the device is
    doing. This checks the weaker, mechanical thing: no two rungs draw their
    verdict from an identical set of collect steps, which would make them
    unable to disagree by construction.
    """

    seen: dict[tuple[str, ...], str] = {}
    for rung in _flow_rungs():
        sources = tuple(sorted(step.name for step in rung.collect))
        clash = seen.get(sources)
        assert clash is None, (
            f"{rung.name} and {clash} read exactly the same sources {sources}; "
            f"they cannot disagree, so the boundary between them is notional"
        )
        seen[sources] = rung.name
