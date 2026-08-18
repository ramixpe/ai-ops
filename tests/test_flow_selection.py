"""B-112 -- free-text flow selection: a declared table, never a model.

House style matches `test_event_routing.py`: the positive cases exist mostly
to prove the machinery works at all. **The negative cases are the point** --
`select_flow` must refuse cleanly on hostile, ambiguous, and unrecognised
input rather than guess, exactly like `event_routing.route_syslog_line`
refuses rather than guesses.
"""

from __future__ import annotations

from agent_nettools import flow_selection as fsel
from agent_nettools import flows

# --------------------------------------------------------------------------- #
# Positive cases -- the machinery works
# --------------------------------------------------------------------------- #


def test_the_canonical_example_from_the_backlog_item():
    """The exact sentence B-112 is written around."""

    d = fsel.select_flow("why can't RR1 reach 10.255.0.12?")

    assert d.matched is True
    assert d.flow == "bgp_session"
    assert d.device == "RR1"
    assert d.subject == "10.255.0.12"
    assert d.suggested_command() == [
        "nettools", "investigate", "RR1", "10.255.0.12", "--flow", "bgp_session"
    ]


def test_an_interface_sentence_with_a_full_interface_name():
    d = fsel.select_flow("the interface GigabitEthernet0/0/0/1 on PE2 is down")

    assert d.matched is True
    assert d.flow == "interface"
    assert d.device == "PE2"
    assert d.subject == "GigabitEthernet0/0/0/1"


def test_device_and_flow_language_are_case_insensitive():
    """A human does not type in the device's own casing convention."""

    d = fsel.select_flow("why is bgp session down on rr1, peer 10.255.0.12?")

    assert d.matched is True
    assert d.device == "RR1", "resolved to the inventory's own casing, not the typed one"


def test_two_genuinely_different_sentences_select_two_genuinely_different_flows():
    """BUILD-PLAN.md 0.12's parameterised-test guard, applied directly: a
    degenerate selector that always returns one flow would still pass every
    single-sentence test above. This is the companion that would catch it."""

    bgp = fsel.select_flow("why can't RR1 reach 10.255.0.12?")
    interface = fsel.select_flow("the interface GigabitEthernet0/0/0/1 on PE2 is down")

    assert bgp.flow == "bgp_session"
    assert interface.flow == "interface"
    assert bgp.flow != interface.flow


def test_suggested_command_is_a_list_never_a_shell_string():
    d = fsel.select_flow("why can't RR1 reach 10.255.0.12?")
    cmd = d.suggested_command()

    assert isinstance(cmd, list)
    assert all(isinstance(part, str) for part in cmd)


def test_as_dict_carries_every_field_including_the_suggested_command():
    d = fsel.select_flow("why can't RR1 reach 10.255.0.12?")
    payload = d.as_dict()

    assert payload["matched"] is True
    assert payload["flow"] == "bgp_session"
    assert payload["device"] == "RR1"
    assert payload["subject"] == "10.255.0.12"
    assert payload["suggested_command"] == d.suggested_command()


# --------------------------------------------------------------------------- #
# looks_like_sentence -- the CLI's trigger, tested on its own
# --------------------------------------------------------------------------- #


def test_looks_like_sentence_is_true_only_for_whitespace():
    assert fsel.looks_like_sentence("why can't RR1 reach 10.255.0.12?") is True
    assert fsel.looks_like_sentence("10.255.0.12") is False
    assert fsel.looks_like_sentence("GigabitEthernet0/0/0/1") is False
    assert fsel.looks_like_sentence("") is False
    assert fsel.looks_like_sentence(None) is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Negative cases -- never guess. These matter more than the positive ones.
# --------------------------------------------------------------------------- #


def test_a_device_not_in_the_inventory_is_a_stated_refusal():
    """Same phrasing as `event_routing._validated_device` -- imported, not
    reimplemented, so the two paths cannot silently disagree on what "known"
    means."""

    d = fsel.select_flow("why is bgp down on PE99")

    assert d.matched is False
    assert "not in the inventory" in d.reason
    assert d.suggested_command() is None


def test_a_sentence_matching_two_flows_worth_of_language_is_ambiguous():
    d = fsel.select_flow("the BGP interface is flapping on PE2")

    assert d.matched is False
    assert "more than one flow" in d.reason
    assert d.candidates, "an ambiguous refusal must still say what to try instead"


def test_a_sentence_naming_two_devices_is_ambiguous():
    d = fsel.select_flow("why is the bgp session down between PE1 and PE2")

    assert d.matched is False
    assert "more than one device" in d.reason


def test_a_sentence_naming_two_peer_addresses_is_ambiguous():
    d = fsel.select_flow(
        "why is the bgp peer 10.255.0.12 different from 10.255.0.31 on RR1"
    )

    assert d.matched is False
    assert "more than one address" in d.reason


def test_no_declared_language_at_all_is_unmatched_not_a_guess():
    d = fsel.select_flow("why is the sky blue")

    assert d.matched is False
    assert d.flow is None and d.device is None and d.subject is None
    assert d.candidates, "an unmatched sentence must say what could be said instead"


def test_a_bare_address_with_no_flow_language_is_unmatched():
    """A subject shape alone is not language -- `select_flow` must not infer
    a flow just because an IPv4 address happens to be present."""

    d = fsel.select_flow("is 10.255.0.12 up")

    assert d.matched is False


def test_empty_and_whitespace_only_input():
    for text in ("", "   ", "\n\t"):
        d = fsel.select_flow(text)
        assert d.matched is False
        assert d.reason == "empty input"


def test_non_string_input_never_crashes():
    for value in (None, 42, [], {"subject": "10.255.0.12"}):
        d = fsel.select_flow(value)  # type: ignore[arg-type]
        assert d.matched is False


# --------------------------------------------------------------------------- #
# Hostile input -- the injection-shaped cases
# --------------------------------------------------------------------------- #


def test_shell_metacharacters_trailing_a_valid_address_are_stripped_by_extraction():
    """The subject is reconstructed from a matched token, never sliced out of
    the raw sentence -- so trailing shell metacharacters are simply not part
    of what gets extracted. This is the injection-safety property, not a
    workaround for it."""

    d = fsel.select_flow("why can't RR1 reach 10.255.0.12; rm -rf / #")

    assert d.matched is True
    assert d.subject == "10.255.0.12"
    assert ";" not in d.subject and "rm" not in d.subject
    assert d.suggested_command() == [
        "nettools", "investigate", "RR1", "10.255.0.12", "--flow", "bgp_session"
    ]


def test_a_subject_that_is_pure_shell_metacharacters_is_refused_not_sanitised():
    """No IPv4-shaped or interface-shaped token exists in this sentence at
    all, so there is nothing to reconstruct -- the correct answer is a
    refusal, not an empty or partially-sanitised subject."""

    d = fsel.select_flow("the interface $(rm -rf /) is down on PE2")

    assert d.matched is False
    assert d.subject is None
    assert "no valid interface name" in d.reason
    assert d.suggested_command() is None


def test_an_oversized_interface_token_is_refused_not_truncated():
    """The interface-family regex has no length cap of its own -- it is
    `_validated_subject` (reconstruction through the same anchored charset
    `templates.py` uses, capped at 63 characters) that actually bounds it.
    Found by mutation-testing that guard: deleting the `_validated_subject`
    call let an 85-character token through with every other test still
    green, because the earlier hostile-input cases only exercised the
    charset, never the length."""

    oversized = "GigabitEthernet" + "0" * 70
    d = fsel.select_flow(f"why is interface {oversized} down on PE2")

    assert d.matched is False
    assert "no valid interface name" in d.reason


def test_a_device_field_carrying_injection_syntax_never_resolves():
    """`PE2; rm -rf /` -- the device token regex requires 1-6 letters then
    1-3 digits ending at a word boundary, so "PE2" is captured on its own and
    the trailing shell syntax is simply never part of the token, proving the
    extractor works on tokens, not substrings."""

    d = fsel.select_flow("why can't PE2; rm -rf / # reach 10.255.0.12")

    assert d.matched is True
    assert d.device == "PE2"
    assert ";" not in d.device and "rm" not in d.device


def test_an_interface_family_word_embedded_in_injection_syntax_is_bounded():
    """The interface token regex only consumes the charset templates.py
    itself allows -- `$`, `(`, `)` are not in it, so a match stops exactly at
    the boundary instead of swallowing the injected suffix."""

    d = fsel.select_flow(
        "why is interface GigabitEthernet0/0/0/1$(touch /tmp/pwned) down on PE2"
    )

    assert d.matched is True
    assert d.subject == "GigabitEthernet0/0/0/1"
    assert "$" not in d.subject and "touch" not in d.subject


# --------------------------------------------------------------------------- #
# The abbreviated-interface / device-token collision, and why it is safe
# --------------------------------------------------------------------------- #


def test_an_abbreviated_interface_reference_does_not_masquerade_as_a_second_device():
    """`Gi0/0/0/1` is not a supported interface form (only the full IOS-XR
    name is), but it must not be misread as a *device* named "Gi0" either --
    that would turn "no interface" into a confusing "ambiguous device"
    refusal. The `(?!/)` guard on `_DEVICE_TOKEN` exists for exactly this.

    Needs an explicit "interface" keyword to even reach `_extract_device` --
    without one, `select_flow` refuses at the flow-language stage before the
    device extractor runs at all, and the assertions below would pass
    vacuously regardless of the guard (caught by mutation-testing the guard:
    deleting `(?!/)` left this test green until the keyword was added)."""

    d = fsel.select_flow("why is interface Gi0/0/0/1 down on PE2")

    assert d.matched is False
    assert "more than one device" not in d.reason
    assert d.device == "PE2" or d.device is None


# --------------------------------------------------------------------------- #
# Declared-table consistency -- guards against silently adding a keyword row
# for a flow that is not actually implemented
# --------------------------------------------------------------------------- #


def test_every_selectable_flow_has_a_subject_extractor():
    table_flows = {rule.flow for rule in fsel.SENTENCE_FLOW_TABLE}
    assert table_flows == set(fsel.SUBJECT_EXTRACTORS)


def test_every_selectable_flow_is_actually_implemented():
    """A row in SENTENCE_FLOW_TABLE naming a declared-but-unimplemented flow
    would make `select_flow` match, then `investigate()` raise
    `NotImplementedError` deep inside the CLI's try/except -- the same trap
    `MNEMONIC_FLOW_TABLE`'s own comment names for the syslog path."""

    for rule in fsel.SENTENCE_FLOW_TABLE:
        assert flows.FLOWS.get(rule.flow) is not None, (
            f"{rule.name!r} routes to {rule.flow!r}, which is not implemented"
        )


def test_the_table_is_not_vacuous():
    """BUILD-PLAN.md 0.12: a guardrail that iterates an empty table passes by
    measuring nothing."""

    assert len(fsel.SENTENCE_FLOW_TABLE) > 0
    assert {rule.flow for rule in fsel.SENTENCE_FLOW_TABLE} == {"bgp_session", "interface"}
