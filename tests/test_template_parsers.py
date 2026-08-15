"""Contract tests for template_parsers.

T-010 ships the contract, the section 0.10 accounting helper, and these tests.
The registry is deliberately empty until T-012 -- so these assert the *shape*
holds and that an empty registry behaves correctly, which is what every parser
from T-012 onward is then written against.

The accounting helper is tested properly here rather than waiting for a real
parser: it is shared by all six, so a defect in it would be six defects.
"""

from __future__ import annotations

import dataclasses

import pytest

from agent_nettools import parsers
from agent_nettools import template_parsers as tp

# --------------------------------------------------------------------------- #
# The registry, empty but well-formed
# --------------------------------------------------------------------------- #


def test_registry_is_empty_until_the_parsers_land():
    """Pins that T-010 ships a contract and no parsers.

    When T-012 adds the first parser this fails, which is the intended signal
    to update it to the real expectation rather than to delete it.
    """

    assert tp.TEMPLATE_PARSERS == {}
    assert tp.TEMPLATE_VOLATILE_FIELDS == {}
    assert tp.TEMPLATE_RECORD_KEYS == {}


def test_registry_keys_are_platform_template_pairs():
    for registry in (tp.TEMPLATE_PARSERS, tp.TEMPLATE_VOLATILE_FIELDS, tp.TEMPLATE_RECORD_KEYS):
        for key in registry:
            assert isinstance(key, tuple) and len(key) == 2
            assert all(isinstance(part, str) for part in key)


def test_status_constants_are_the_ones_parsers_defines_not_copies():
    """One status vocabulary in this package, not two.

    Identity, not equality: a copied string constant would compare equal and
    then drift the first time one of them was edited.
    """

    assert tp.PARSE_OK is parsers.PARSE_OK
    assert tp.PARSE_UNAVAILABLE is parsers.PARSE_UNAVAILABLE
    assert tp.PARSE_FAILED is parsers.PARSE_FAILED
    assert tp.ParseError is parsers.ParseError


def test_lookup_helpers_are_total_over_unknown_keys():
    assert tp.has_template_parser("cisco_xr", "nonexistent") is False
    assert tp.template_volatile_fields("cisco_xr", "nonexistent") == frozenset()
    assert tp.template_record_key("cisco_xr", "nonexistent") is None


# --------------------------------------------------------------------------- #
# parse_template_output
# --------------------------------------------------------------------------- #


def test_unknown_template_is_unavailable_not_failed():
    """`unavailable` and `failed` mean different things and must not be merged.

    No parser defined is not the same as a parser that could not read the
    output, exactly as parsers.parse_intent distinguishes them.
    """

    parsed, status = tp.parse_template_output("cisco_xr", "nonexistent", "some output")
    assert parsed is None
    assert status is tp.PARSE_UNAVAILABLE


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\n  \n\t\n"])
def test_blank_output_for_a_known_template_fails(monkeypatch, blank):
    monkeypatch.setitem(tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), lambda _out: {"meta": {}})
    parsed, status = tp.parse_template_output("cisco_xr", "probe", blank)
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_a_raising_parser_never_propagates(monkeypatch):
    def boom(_output):
        raise RuntimeError("parser bug")

    monkeypatch.setitem(tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), boom)
    parsed, status = tp.parse_template_output("cisco_xr", "probe", "real output")
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_parse_error_is_a_failure(monkeypatch):
    def raiser(_output):
        raise tp.ParseError("cannot read this")

    monkeypatch.setitem(tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), raiser)
    _parsed, status = tp.parse_template_output("cisco_xr", "probe", "real output")
    assert status is tp.PARSE_FAILED


def test_empty_result_from_non_empty_input_is_a_failure_not_a_success(monkeypatch):
    """The ntc-templates defect, pinned. A silent empty parse is a failure."""

    monkeypatch.setitem(
        tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), lambda _out: {"meta": {}, "records": []}
    )
    parsed, status = tp.parse_template_output("cisco_xr", "probe", "device said something")
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_result_without_section_0_10_accounting_is_a_failure(monkeypatch):
    """A parser that bypassed finalize() has unmeasurable coverage.

    This is the guardrail for the whole accounting mechanism: it fails if the
    check is removed, and it is what stops a parser quietly opting out.
    """

    monkeypatch.setitem(
        tp.TEMPLATE_PARSERS,
        ("cisco_xr", "probe"),
        lambda _out: {"meta": {"state": "Established"}, "records": []},
    )
    parsed, status = tp.parse_template_output("cisco_xr", "probe", "state Established")
    assert parsed is None
    assert status is tp.PARSE_FAILED


@pytest.mark.parametrize("missing", ["unaccounted_lines", "unparsed_rows"])
def test_partial_accounting_is_also_a_failure(monkeypatch, missing):
    meta = {"state": "x", "unaccounted_lines": [], "unparsed_rows": 0}
    del meta[missing]
    monkeypatch.setitem(tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), lambda _o: {"meta": meta})
    _parsed, status = tp.parse_template_output("cisco_xr", "probe", "x")
    assert status is tp.PARSE_FAILED


def test_a_well_formed_result_parses(monkeypatch):
    raw = "BGP neighbor is 10.255.0.12\n  BGP state = Established\n"

    def parser(output):
        return tp.finalize(
            raw=output,
            meta={"state": "Established"},
            records=[{"afi": "ipv4 unicast"}],
            consumed=output.splitlines(),
        )

    monkeypatch.setitem(tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), parser)
    parsed, status = tp.parse_template_output("cisco_xr", "probe", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["state"] == "Established"
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["records"] == [{"afi": "ipv4 unicast"}]


def test_a_non_dict_result_is_a_failure(monkeypatch):
    monkeypatch.setitem(tp.TEMPLATE_PARSERS, ("cisco_xr", "probe"), lambda _o: ["not", "a", "dict"])
    _parsed, status = tp.parse_template_output("cisco_xr", "probe", "x")
    assert status is tp.PARSE_FAILED


# --------------------------------------------------------------------------- #
# Section 0.10 line accounting
# --------------------------------------------------------------------------- #


def test_the_xr_timestamp_banner_is_ignored_by_default():
    """Every IOS-XR show command emits it; no parser should have to declare it."""

    raw = "Wed Jul 29 11:59:14.406 UTC\nBGP router identifier 10.255.0.31\n"
    assert tp.account_lines(raw, consumed=["BGP router identifier 10.255.0.31"]) == []


def test_blank_lines_are_never_unaccounted():
    assert tp.account_lines("\n\n   \n\t\n") == []


def test_an_unrecognised_line_is_surfaced_not_dropped():
    raw = "Wed Jul 29 11:59:14.406 UTC\nBGP state = Established\nSOMETHING NEW FROM THE VENDOR\n"
    unaccounted = tp.account_lines(raw, consumed=["BGP state = Established"])
    assert unaccounted == ["SOMETHING NEW FROM THE VENDOR"]


def test_a_declared_ignore_rule_accounts_for_a_line():
    raw = "Wed Jul 29 11:59:14.406 UTC\nDecorative header\nBGP state = Established\n"
    rules = (tp.IgnoreRule(r"^Decorative header$", "known decorative header"),)
    assert tp.account_lines(raw, consumed=["BGP state = Established"], ignores=rules) == []


def test_accounting_compares_on_the_stripped_line():
    """Captured fixtures carry trailing whitespace; it must not create phantoms."""

    raw = "  BGP state = Established   \n"
    assert tp.account_lines(raw, consumed=["BGP state = Established"]) == []


def test_include_common_false_drops_the_shared_rules():
    """The shared rules are a convenience, not a hidden mandate.

    A parser whose command has genuinely different framing can opt out and
    declare everything itself -- still explicit, which is what 0.10 requires.
    """

    raw = "Wed Jul 29 11:59:14.406 UTC\n"
    assert tp.account_lines(raw, include_common=True) == []
    assert tp.account_lines(raw, include_common=False) == ["Wed Jul 29 11:59:14.406 UTC"]


def test_finalize_attaches_accounting_and_preserves_meta():
    raw = "Wed Jul 29 11:59:14.406 UTC\nknown\nmystery line\n"
    result = tp.finalize(raw=raw, meta={"found": True}, records=[{"a": 1}], consumed=["known"])
    assert result["meta"]["found"] is True
    assert result["meta"]["unaccounted_lines"] == ["mystery line"]
    assert result["meta"]["unparsed_rows"] == 0
    assert result["records"] == [{"a": 1}]


def test_finalize_keeps_unaccounted_and_unparsed_separate():
    """Two different failures. Collapsing them would hide a vendor change
    behind a malformed-row count."""

    raw = "Wed Jul 29 11:59:14.406 UTC\nmystery\n"
    result = tp.finalize(raw=raw, meta={}, records=[], unparsed_rows=3)
    assert result["meta"]["unaccounted_lines"] == ["mystery"]
    assert result["meta"]["unparsed_rows"] == 3


def test_ignore_rule_is_frozen_and_carries_a_reason():
    rule = tp.IgnoreRule(r"^x$", "because")
    assert rule.reason == "because"
    with pytest.raises(dataclasses.FrozenInstanceError):
        rule.reason = "changed"  # type: ignore[misc]


def test_every_shared_ignore_rule_explains_itself():
    """0.10 requires the accounting be reviewable, which a bare regex is not."""

    for rule in tp.XR_COMMON_IGNORES:
        assert rule.reason.strip()
