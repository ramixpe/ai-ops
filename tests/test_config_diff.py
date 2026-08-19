"""B-106 -- the intent-vs-observed diff (D16's payoff). Tests for `config_diff.py`.

Four concerns:

1. The three-outcome discipline holds structurally: `FieldDiff` refuses an
   unknown `outcome` and refuses a `cannot_compare` with no `reason` -- with
   a positive control proving each guard is not vacuous (OBS-181).
2. The worked example this whole axis exists for -- B-496's `isis-broken`
   pair (PE3 <-> P2) -- produces `disagrees` on both ends, end to end,
   through `gather_reconciliation_evidence` + `reconcile_interface` exactly
   as a real caller would use them, not just at the parser layer
   `test_config_section.py` already covers.
3. The healthy contrast case (RR1) produces `agrees` throughout, so the
   worked example is evidence of something -- not just what a diff that
   always says `disagrees` would also produce.
4. `cannot_compare` is genuinely distinct from both other outcomes: reached
   two different ways (a missing section on either side, and a field with no
   observed counterpart at all) and never rendered as either of the other two.

Uses only the fixtures already committed for PE3/P2 under `isis-broken` and
RR1 under `healthy` -- no new fixture is captured here.
"""

from __future__ import annotations

import pytest

from agent_nettools import config_diff as cd
from agent_nettools.fixtures import fixture_sender

# --------------------------------------------------------------------------- #
# The three-outcome discipline, with positive controls (OBS-181)
# --------------------------------------------------------------------------- #


def test_a_valid_outcome_constructs_fine():
    """Positive control for the outcome-enum guard below: a legitimate
    `FieldDiff` must not be rejected by the same check that rejects a bogus
    one."""

    d = cd.FieldDiff(field="x", outcome=cd.AGREES)
    assert d.outcome == cd.AGREES


def test_an_unknown_outcome_is_rejected():
    with pytest.raises(ValueError):
        cd.FieldDiff(field="x", outcome="not-a-real-outcome")


def test_cannot_compare_with_a_reason_constructs_fine():
    """Positive control for the reason-required guard: a properly-formed
    `cannot_compare` result must not be rejected by the same check that
    rejects a bare one."""

    d = cd.FieldDiff(field="x", outcome=cd.CANNOT_COMPARE, reason="no section")
    assert d.outcome == cd.CANNOT_COMPARE
    assert d.reason == "no section"


def test_cannot_compare_without_a_reason_is_rejected():
    """The guard B-106's brief exists to enforce: an absence must never
    render as an unexplained value (OBS-188, OBS-202)."""

    with pytest.raises(ValueError):
        cd.FieldDiff(field="x", outcome=cd.CANNOT_COMPARE)


def test_reconciliation_result_properties_partition_the_fields():
    result = cd.ReconciliationResult(
        device="PE3",
        subject="Gi0/0/0/0",
        fields=(
            cd.FieldDiff(field="a", outcome=cd.AGREES),
            cd.FieldDiff(field="b", outcome=cd.DISAGREES, reason="x"),
            cd.FieldDiff(field="c", outcome=cd.CANNOT_COMPARE, reason="y"),
        ),
    )
    assert [f.field for f in result.disagreements] == ["b"]
    assert [f.field for f in result.incomparable] == ["c"]
    assert result.has_disagreement is True

    all_agree = cd.ReconciliationResult(
        device="RR1", subject="Gi0/0/0/0", fields=(cd.FieldDiff(field="a", outcome=cd.AGREES),)
    )
    assert all_agree.has_disagreement is False


# --------------------------------------------------------------------------- #
# The worked example, end to end: PE3 <-> P2, `isis-broken`
# --------------------------------------------------------------------------- #


def test_pe3_gi0_0_0_0_disagrees_on_isis_adjacency():
    """B-496's own root cause, reproduced through the diff rather than by
    hand-reading two fixtures: PE3's Gi0/0/0/0 is enabled under the
    configured IS-IS process but shows no Up adjacency. `descent.py` alone
    could only report `cause_not_localised` here -- this is the fact it was
    missing."""

    sender = fixture_sender(label="isis-broken")
    evidence = cd.gather_reconciliation_evidence("PE3", ["Gi0/0/0/0"], sender=sender)
    result = cd.reconcile_interface(evidence, "PE3", "Gi0/0/0/0")

    by_field = {f.field: f for f in result.fields}

    isis = by_field["isis_adjacency"]
    assert isis.outcome == cd.DISAGREES
    assert isis.intent is True
    assert isis.observed is False
    assert "PE3:Gi0/0/0/0" in isis.reason

    # The interface itself is not the fault -- not shut, and observed up.
    admin = by_field["interface_admin_state"]
    assert admin.outcome == cd.AGREES
    assert admin.intent is False
    assert admin.observed == "up"

    # No ipv4 address configured, and no observed intent could confirm or
    # contradict that even if there were one.
    ipv4 = by_field["ipv4_address"]
    assert ipv4.outcome == cd.CANNOT_COMPARE
    assert ipv4.intent is None


def test_p2_gi0_0_0_4_the_far_end_also_disagrees_and_has_an_address():
    """The contrast case from `test_config_section.py`, carried through the
    diff: P2's Gi0/0/0/4 (the other end of the same broken link) shows the
    same `isis_adjacency` disagreement -- an adjacency needs both ends, so
    the far end never forming one either is consistent, not a second
    independent fault -- but DOES carry an IPv4 address, unlike PE3's end.
    Without this contrast, the previous test would be equally consistent
    with a diff that always reports `disagrees`/`None` (OBS-181's rule,
    applied to a semantic finding rather than a safety refusal)."""

    sender = fixture_sender(label="isis-broken")
    evidence = cd.gather_reconciliation_evidence("P2", ["Gi0/0/0/4"], sender=sender)
    result = cd.reconcile_interface(evidence, "P2", "Gi0/0/0/4")
    by_field = {f.field: f for f in result.fields}

    assert by_field["isis_adjacency"].outcome == cd.DISAGREES
    assert by_field["interface_admin_state"].outcome == cd.AGREES

    ipv4 = by_field["ipv4_address"]
    assert ipv4.outcome == cd.CANNOT_COMPARE
    assert ipv4.intent == "10.0.1.9"


# --------------------------------------------------------------------------- #
# The healthy contrast: RR1, both physical links
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("interface", ["Gi0/0/0/0", "Gi0/0/0/1"])
def test_rr1_agrees_on_every_comparable_field(interface: str):
    """Same two templates, a healthy device: every field this axis can
    actually compare agrees. Proves the diff reports the SAME (healthy)
    shape it would need to for a device with nothing wrong, not just the
    specific broken shape it was written against -- the same discipline
    `test_config_section.py`'s own RR1 test uses."""

    sender = fixture_sender(label="healthy")
    evidence = cd.gather_reconciliation_evidence("RR1", [interface], sender=sender)
    result = cd.reconcile_interface(evidence, "RR1", interface)

    by_field = {f.field: f for f in result.fields}
    assert by_field["isis_adjacency"].outcome == cd.AGREES
    assert by_field["isis_adjacency"].intent is True
    assert by_field["isis_adjacency"].observed is True

    assert by_field["interface_admin_state"].outcome == cd.AGREES
    assert by_field["interface_admin_state"].intent is False
    assert by_field["interface_admin_state"].observed == "up"

    # Still cannot_compare -- RR1 having a real address does not change
    # *why* this field can never be compared on this build.
    ipv4 = by_field["ipv4_address"]
    assert ipv4.outcome == cd.CANNOT_COMPARE
    assert ipv4.intent is not None

    assert result.has_disagreement is False


# --------------------------------------------------------------------------- #
# `cannot_compare`, genuinely distinct from `disagrees` and `agrees`,
# reached two different ways.
# --------------------------------------------------------------------------- #


def test_cannot_compare_when_the_intent_side_is_missing():
    """Intent missing (no `config_isis` section at all): CANNOT_COMPARE, not
    a guessed `disagrees` and not a silently-passing `agrees`."""

    sender = fixture_sender(label="isis-broken")
    evidence = cd.gather_reconciliation_evidence("PE3", ["Gi0/0/0/0"], sender=sender)
    del evidence["config_isis"]

    d = cd.diff_isis_adjacency(evidence, "PE3", "Gi0/0/0/0")
    assert d.outcome == cd.CANNOT_COMPARE
    assert "intent" in d.reason
    assert "config_isis" in d.reason
    # Neither of the other outcomes' fields is populated by a guess.
    assert d.intent is None
    assert d.observed is None


def test_cannot_compare_when_the_observed_side_is_missing():
    """Observation missing (no `isis` section at all): CANNOT_COMPARE, the
    asymmetric case -- distinct wording from the intent-missing case, so an
    operator reading the reason knows which side to go re-collect."""

    sender = fixture_sender(label="isis-broken")
    evidence = cd.gather_reconciliation_evidence("PE3", ["Gi0/0/0/0"], sender=sender)
    del evidence["isis"]

    d = cd.diff_isis_adjacency(evidence, "PE3", "Gi0/0/0/0")
    assert d.outcome == cd.CANNOT_COMPARE
    assert "observed" in d.reason
    assert "'isis'" in d.reason


def test_cannot_compare_when_the_interface_is_absent_from_the_observed_table():
    """A third route to CANNOT_COMPARE: both sections parsed fine, but the
    specific subject is not a record in the observed bulk table -- distinct
    from "the section failed to parse" and must not be read as `agrees`
    (vacuously "not shut, not up either, so consistent") or `disagrees`."""

    sender = fixture_sender(label="isis-broken")
    evidence = cd.gather_reconciliation_evidence("PE3", ["Gi0/0/0/0"], sender=sender)

    d = cd.diff_interface_admin_state(evidence, "PE3", "Gi9/9/9/9")
    assert d.outcome == cd.CANNOT_COMPARE
    assert "Gi9/9/9/9" in d.reason


def test_ipv4_address_is_always_cannot_compare_never_agrees_or_disagrees():
    """Structural, not incidental: no observed intent in this build reports
    a per-interface IPv4 address, on any device, ever -- so this field must
    never resolve to `agrees` or `disagrees`, which would imply an
    observed-side comparison that never actually happened."""

    sender = fixture_sender(label="healthy")
    evidence = cd.gather_reconciliation_evidence("RR1", ["Gi0/0/0/0"], sender=sender)
    d = cd.diff_interface_ipv4_address(evidence, "RR1", "Gi0/0/0/0")
    assert d.outcome == cd.CANNOT_COMPARE
    assert d.outcome != cd.AGREES
    assert d.outcome != cd.DISAGREES
