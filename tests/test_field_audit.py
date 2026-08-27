"""What every check reads, against what its inputs contain (B-433).

The detection method for silent-failure **shape 7** (`PROCESS.md` §0.13):
*evidence collected, parsed, carried in the envelope, and never read.* It is
invisible to any check that grades output, because the output is correct — round
3 reported `transport_blocked`, which was true, while the same parsed record
carried `last_reset_reason: "BGP Notification received: administrative shutdown"`
and nothing looked at it.

So this file does not grade output. **It compares what a parser emits against
what any check reads**, and holds the answer as a committed number.

Two things it deliberately is not
----------------------------------
**It is not a rule that every parsed field must be read.** `mac_address` and
`bandwidth_kbps` are not diagnostic, and failing on them would reproduce exactly
the noise-generating over-correction T-029c refused — a rule against unread
evidence that generates unread warnings has defeated itself the same way.

**It is not a judgement about which fields matter.** That is per-check work and
belongs with whoever owns the check. This file records the split, names the
fields whose *purpose* is to explain a state, and fails when the numbers move —
so a parser gaining a field nothing reads is a decision someone makes, not a
drift nobody notices.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from agent_nettools import template_parsers as tp

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "cisco_xr" / "RR1" / "broken"
CHECKS = pathlib.Path(__file__).resolve().parent.parent / "src" / "agent_nettools" / "checks.py"

#: One real capture per diagnostic template. Fixtures, not synthetic input --
#: a field a parser only emits on real output is exactly the kind that gets
#: missed.
SOURCES = {
    "bgp_neighbor": "show-bgp-neighbor-10-255-0-12.txt",
    "route": "show-route-10-255-0-12-32.txt",
    "interface": "show-interfaces-gi0-0-0-0.txt",
}

# `config_ldp` is collected by the LDP flow but has no committed real-output
# fixture in this RR1 operational-state corpus. Its bounded parser and the
# semantic configured-versus-silent decision are covered in
# test_config_section.py and test_checks.py instead; do not invent a fixture
# merely to make this field audit look complete.
CONFIG_TEMPLATES_AUDITED_ELSEWHERE = frozenset({"config_ldp"})

#: Measured 2026-08-16. `(fields parsed, fields any check reads)`.
#:
#: These are pinned so the ratio cannot drift silently in either direction: a
#: parser gaining an unread field, or a check quietly dropping one it used to
#: read. When a number changes, update it **and** say which field moved and why
#: in the commit.
EXPECTED = {
    # 23 -> 25 at B-432: socket_armed_read/write promoted from IgnoreRule to
    # parsed. 7 -> 9 read: those two, plus state_reason, minus none.
    "bgp_neighbor": (25, 9),
    "route": (8, 2),
    "interface": (14, 5),
}

#: Unread fields whose entire purpose is to explain *why* something is in the
#: state it is in. Not defects on their own -- a deliberate decision not to read
#: one is fine -- but they are the population shape 7 is drawn from, and round 3
#: came out of the first entry here.
#:
#: An AS mismatch and a hold-timer mismatch both produce the `Active` state
#: round 3 produced. The tool cannot distinguish either from an administrative
#: shutdown, while parsing and discarding the fields that would.
EXPLANATORY = {
    # `last_reset_reason` and `last_reset_ago` left this set at B-430 -- the
    # audit caught the move, which is what it is for. They now qualify the
    # transport finding and have their own tests.
    # `state_reason` left this set at B-432 -- it now qualifies the transport
    # finding with the device's own current reason.
    "bgp_neighbor": {
        "previous_state",
        "remote_as", "local_as", "hold_time", "keepalive",
    },
    "interface": {"last_link_flapped", "state_transitions"},
    "route": {"protocol", "distance", "metric"},
}


def _parsed_fields(template: str) -> set[str]:
    raw = (FIXTURES / SOURCES[template]).read_text(encoding="utf-8")
    parsed, status = tp.parse_template_output("cisco_xr", template, raw)
    assert status is tp.PARSE_OK, f"{template} fixture must parse"

    fields = set(parsed.get("meta") or {})
    for record in parsed.get("records") or []:
        fields |= set(record)
    return fields - {"unaccounted_lines", "unparsed_rows"}


def _fields_read_by_any_check() -> str:
    return CHECKS.read_text(encoding="utf-8")


def _split(template: str) -> tuple[set[str], set[str]]:
    """(read, unread) for one template."""

    source = _fields_read_by_any_check()
    fields = _parsed_fields(template)
    read = {f for f in fields if re.search(rf'["\']{re.escape(f)}["\']', source)}
    return read, fields - read


@pytest.mark.parametrize("template", sorted(SOURCES), ids=sorted(SOURCES))
def test_the_read_versus_parsed_split_is_what_was_measured(template):
    """The audit itself, held as a number.

    Failing here is not necessarily a defect. It means the split moved, and the
    question to answer in the commit is *which field, and was that deliberate?*
    """

    read, unread = _split(template)
    parsed_count, read_count = EXPECTED[template]

    assert len(read) + len(unread) == parsed_count, (
        f"{template} now parses {len(read) + len(unread)} fields, expected "
        f"{parsed_count}. Which field was added, and does any check read it?"
    )
    assert len(read) == read_count, (
        f"{template}: {len(read)} fields read, expected {read_count}. "
        f"read={sorted(read)} unread={sorted(unread)}"
    )


@pytest.mark.parametrize("template", sorted(EXPLANATORY), ids=sorted(EXPLANATORY))
def test_the_explanatory_fields_are_enumerated_and_still_unread(template):
    """The population shape 7 is drawn from, held explicitly.

    Every field here exists to say *why* something is in a state. Reading one is
    a change worth noticing, so this fails when a check starts reading it --
    prompting the entry to move out of `EXPLANATORY` and into a real check with
    tests, rather than being read incidentally by a string match.
    """

    read, unread = _split(template)
    declared = EXPLANATORY[template]

    assert declared <= (read | unread), (
        f"{template}: EXPLANATORY names fields the parser no longer emits: "
        f"{sorted(declared - (read | unread))}"
    )

    now_read = declared & read
    assert not now_read, (
        f"{template}: {sorted(now_read)} is now read by a check. Good -- move it "
        f"out of EXPLANATORY and make sure the check has its own test."
    )


def test_the_audit_is_not_a_rule_that_everything_must_be_read():
    """The boundary, asserted so nobody tightens this into noise.

    T-029c's lesson applies directly: a rule against unread evidence that
    generates unread warnings has defeated itself. Most unread fields are
    legitimately not diagnostic, and this file must keep passing while they are
    unread.
    """

    _, unread = _split("interface")

    assert {"mac_address", "bandwidth_kbps"} <= unread
    assert _split("interface")[0], "and some fields ARE read -- the audit is not vacuous"


def test_every_diagnostic_template_is_covered_by_the_audit():
    """§0.12. A template added to the flow and not to `SOURCES` would leave this
    file passing over a shrinking fraction of the surface."""

    from agent_nettools import flows

    used = {
        step.name
        for flow in (flows.flow_for(o) for o in flows.FLOWS)
        for rung in flow.descent
        for step in rung.collect
        if step.is_template
    }

    assert used, "no templates in any flow -- the audit would be vacuous"
    unaudited = used - set(SOURCES) - CONFIG_TEMPLATES_AUDITED_ELSEWHERE
    assert not unaudited, (
        f"templates used by a flow but absent from the audit: {sorted(unaudited)}"
    )


# --------------------------------------------------------------------------- #
# B-434 -- the other reservoir: lines never parsed at all
# --------------------------------------------------------------------------- #


#: Ignore rules whose line carries something real that no check has needed yet.
#: Pinned by count and by reason so the set is a reviewed decision, not a drift.
#:
#: B-432's TCP signal spent the whole build in a rule labelled "socket
#: bookkeeping" -- correctly declared, and filed as though the decision were
#: permanent when it was only unexamined (OBS-100).
#:
#: 15 -> 27 (B-515): `sr_policy_detail`'s twelve NOT_NEEDED_YET rules --
#: candidate-path constraints/weight/metric bookkeeping, the LSP block's own
#: id/local-label, and the policy `Attributes:` block's forward-class/
#: steering/IPv6/invalidation/standby-path fields. Each has real content and
#: a stated reason it is not needed for THIS template's purpose (naming
#: which SID/segment list a down policy is missing) -- reviewed, not a
#: default; see `SR_POLICY_DETAIL_IGNORES` in template_parsers.py for each
#: one's own comment.
EXPECTED_DEFERRED = 27


def _ignore_rules():
    from agent_nettools import template_parsers

    return [
        (name.replace("_IGNORES", "").lower(), rule)
        for name in dir(template_parsers)
        if name.endswith("_IGNORES")
        for rule in getattr(template_parsers, name)
    ]


def test_every_ignore_rule_declares_which_kind_of_decision_it_is():
    """§0.10 required them declared. B-434 requires them *classified*.

    "This line has nothing in it" and "nobody has needed this line yet" are
    different decisions, and only the first should be permanent.
    """

    from agent_nettools.template_parsers import IgnoreKind

    rules = _ignore_rules()
    assert len(rules) >= 70, "the corpus of ignore rules should not have shrunk"
    for _, rule in rules:
        assert isinstance(rule.kind, IgnoreKind)
        assert rule.reason, "and every one still states why"


def test_the_deferred_set_is_the_reviewed_one():
    """The audit's second half, held as a number like the first.

    Failing here means a rule changed kind. That is either someone promoting a
    deferred line into a parsed field -- good, decrement it -- or someone
    marking a new line deferred, which should be a deliberate note rather than
    a default.
    """

    from agent_nettools.template_parsers import IgnoreKind

    deferred = [(t, r) for t, r in _ignore_rules() if r.kind is IgnoreKind.NOT_NEEDED_YET]

    assert len(deferred) == EXPECTED_DEFERRED, (
        f"{len(deferred)} deferred ignore rules, expected {EXPECTED_DEFERRED}:\n  "
        + "\n  ".join(f"{t}: {r.reason[:70]}" for t, r in deferred)
    )


def test_the_deferred_ones_are_the_diagnostic_ones():
    """Spot-checks, so the classification is not just a label.

    Each of these carries a signal a check could plausibly want, and each is
    currently unread: a duplex mismatch, a slow peer, a policy denying every
    prefix, a session flapping, an interface up but carrying nothing.
    """

    from agent_nettools.template_parsers import IgnoreKind

    deferred = " ".join(
        r.pattern for _, r in _ignore_rules() if r.kind is IgnoreKind.NOT_NEEDED_YET
    )

    for signal in ("Full-duplex", "Slow Peer State", "prefixes denied",
                   "Connections established", "reliability", "minute input rate"):
        assert signal in deferred, f"{signal!r} should be classified as deferred"


def test_the_permanent_ones_really_are_structural():
    """The other direction, so `NO_EXTRACTABLE_FIELD` is not a dumping ground."""

    from agent_nettools.template_parsers import IgnoreKind

    permanent = " ".join(
        r.pattern for _, r in _ignore_rules() if r.kind is IgnoreKind.NO_EXTRACTABLE_FIELD
    )

    for header in ("Neighbor capabilities:", "Routing Descriptor Blocks",
                   "Notification data sent:", "Redist Advertisers:"):
        assert header in permanent, f"{header!r} is a section header, not deferred content"
