"""Contract tests for template_parsers.

T-010 ships the contract, the section 0.10 accounting helper, and tests for
both against an empty registry. T-012 adds the first real parser
(``cisco_xr``/``bgp_neighbor``) and the tests below it.

The accounting helper is tested properly at T-010 rather than waiting for a
real parser: it is shared by all six, so a defect in it would be six defects.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from agent_nettools import parsers
from agent_nettools import template_parsers as tp

# --------------------------------------------------------------------------- #
# The registry, empty but well-formed
# --------------------------------------------------------------------------- #


def test_registry_has_the_bgp_neighbor_parser_now_that_t_012_landed():
    """Was ``test_registry_is_empty_until_the_parsers_land`` (T-010).

    T-010 shipped a contract with no parsers; T-012 adds the first one. The
    old assertion (an empty registry) failing was the intended signal to
    update this test to the real expectation, per its own docstring, rather
    than to delete it.
    """

    assert ("cisco_xr", "bgp_neighbor") in tp.TEMPLATE_PARSERS
    assert tp.TEMPLATE_VOLATILE_FIELDS[("cisco_xr", "bgp_neighbor")] == frozenset(
        {"up_for", "messages_received", "messages_sent", "last_reset_ago"}
    )
    assert tp.TEMPLATE_RECORD_KEYS[("cisco_xr", "bgp_neighbor")] == "address_family"


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


# --------------------------------------------------------------------------- #
# T-012: the bgp_neighbor parser
# --------------------------------------------------------------------------- #

from helpers import FIXTURE_DIR  # noqa: E402 -- after the module-level imports by design

_BGP_NEIGHBOR_FIXTURES = sorted(FIXTURE_DIR.glob("cisco_xr/*/healthy/show-bgp-neighbor-*.txt"))

# Every key the meta table in the T-012 spec requires, present in every case
# (found or not) -- ``None`` rather than absent.
_BGP_NEIGHBOR_META_KEYS = (
    "found",
    "reason",
    "neighbor",
    "state",
    "connection_state",
    "previous_state",
    "last_reset_reason",
    "hold_time",
    "keepalive",
    "local_as",
    "remote_as",
    "router_id",
    "up_for",
    "messages_received",
    "messages_sent",
)


def test_at_least_one_fixture_of_each_shape_is_on_disk():
    """A sanity check on the parametrization source below.

    If this ever fails, the round-trip test below would be silently
    parametrized over an empty or lopsided list -- worth failing loudly on
    its own rather than only as a mysteriously-shrunk parametrize count.
    """

    assert len(_BGP_NEIGHBOR_FIXTURES) >= 9  # one full block per device, at minimum


@pytest.mark.parametrize("fixture_path", _BGP_NEIGHBOR_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_committed_bgp_neighbor_fixture_round_trips_clean(fixture_path: Path):
    """Section 0.10, pinned against every real fixture on disk.

    Discovered from the filesystem rather than a hardcoded list, so a future
    ``nettools capture`` run that adds a device or a peer is covered
    automatically instead of silently going unchecked.
    """

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0


def _load_fixture(*parts: str) -> str:
    return FIXTURE_DIR.joinpath(*parts).read_text()


def test_rr1_to_pe1_yields_the_full_established_session():
    """RR1 -> 10.255.0.11 (PE1): a real, healthy, fully-established session."""

    raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-bgp-neighbor-10-255-0-11.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is tp.PARSE_OK
    meta = parsed["meta"]
    assert meta["found"] is True
    assert meta["reason"] is None
    assert meta["neighbor"] == "10.255.0.11"
    assert meta["state"] == "Established"
    assert meta["connection_state"] == "Established"
    assert meta["hold_time"] == "180"
    assert meta["keepalive"] == "60"
    assert meta["remote_as"] == "65000"
    assert meta["local_as"] == "65000"
    assert meta["router_id"] == "10.255.0.11"
    assert len(parsed["records"]) == 5
    assert [r["address_family"] for r in parsed["records"]] == [
        "IPv4 Unicast",
        "VPNv4 Unicast",
        "IPv6 Labeled-unicast",
        "VPNv6 Unicast",
        "L2VPN EVPN",
    ]


def test_a_p_router_with_no_bgp_process_is_found_false_but_parse_ok():
    """The device answering '% BGP instance not active' is a real, well-formed
    result -- never PARSE_FAILED. That distinction is the crux of T-012."""

    raw = _load_fixture("cisco_xr", "P1", "healthy", "show-bgp-neighbor-10-255-0-12.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is tp.PARSE_OK  # asserted explicitly: this is the crux
    assert parsed["meta"]["found"] is False
    assert parsed["meta"]["reason"] == "bgp_not_active"
    assert parsed["records"] == []


def test_a_peer_not_configured_on_this_device_is_found_false_but_parse_ok():
    """'% Neighbor not found' is likewise the device answering correctly."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "show-bgp-neighbor-10-255-0-12.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["found"] is False
    assert parsed["meta"]["reason"] == "neighbor_not_found"
    assert parsed["records"] == []


def test_truncated_output_does_not_raise_and_still_accounts_cleanly():
    """The first 15 lines of a real fixture -- a session block cut off mid-way
    through the header, before any address-family section. Must not raise,
    and whatever is captured must still satisfy the 0.10 accounting."""

    raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-bgp-neighbor-10-255-0-11.txt")
    truncated = "\n".join(raw.splitlines()[:15])

    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", truncated)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["meta"]["found"] is True
    assert parsed["records"] == []


def test_garbage_input_raises_parse_error_and_reports_parse_failed():
    """Something clearly not BGP output must not be silently accepted."""

    garbage = "lorem ipsum dolor sit amet\nconsectetur adipiscing"

    with pytest.raises(tp.ParseError):
        tp.parse_xr_bgp_neighbor(garbage)

    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", garbage)
    assert parsed is None
    assert status is tp.PARSE_FAILED


@pytest.mark.parametrize(
    "device,peer,label",
    [
        ("RR1", "10.255.0.11", "found"),
        ("P1", "10.255.0.12", "bgp_not_active"),
        ("PE1", "10.255.0.12", "neighbor_not_found"),
    ],
)
def test_every_meta_key_is_present_in_every_case(device, peer, label):
    """Every key in the T-012 meta table, present for all three shapes --
    ``None`` rather than absent, so a consumer never has to distinguish
    'missing' from 'not applicable'."""

    raw = _load_fixture("cisco_xr", device, "healthy", f"show-bgp-neighbor-{peer.replace('.', '-')}.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)
    assert status is tp.PARSE_OK
    for key in _BGP_NEIGHBOR_META_KEYS:
        assert key in parsed["meta"], f"{label}: missing {key}"


def test_record_key_and_volatile_fields_are_registered():
    assert tp.template_record_key("cisco_xr", "bgp_neighbor") == "address_family"
    volatile = tp.template_volatile_fields("cisco_xr", "bgp_neighbor")
    assert {"up_for", "messages_received", "messages_sent"} <= volatile


def test_last_reset_splits_the_duration_from_the_reason():
    """`Last reset 1d23h, due to X` must not fold the duration into the reason.

    The duration changes on every capture of an unchanged device. Folded
    together, two captures of a quiet fabric would diff as changed -- the
    100%-false-positive failure CLAUDE.md records from before Phase 2. Split,
    the duration can be declared volatile while a genuine change of *reason*
    still surfaces, which is the signal actually worth having.
    """

    raw = (FIXTURE_DIR / "cisco_xr" / "RR1" / "healthy" / "show-bgp-neighbor-10-255-0-11.txt").read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["last_reset_reason"] == "Address family activated"
    assert parsed["meta"]["last_reset_ago"] == "1d23h"
    # The reason must carry no duration at all.
    assert "1d23h" not in (parsed["meta"]["last_reset_reason"] or "")


def test_last_reset_reason_is_not_volatile_but_the_duration_is():
    """The point of the split: a changed reason is a real difference."""

    volatile = tp.template_volatile_fields("cisco_xr", "bgp_neighbor")
    assert "last_reset_ago" in volatile
    assert "last_reset_reason" not in volatile


def test_last_reset_without_a_due_to_clause_is_still_consumed():
    """A `Last reset <duration>` line with no reason must not go unaccounted."""

    raw = (FIXTURE_DIR / "cisco_xr" / "RR1" / "healthy" / "show-bgp-neighbor-10-255-0-11.txt").read_text()
    stripped = raw.replace("Last reset 1d23h, due to Address family activated", "Last reset 1d23h")
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", stripped)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["last_reset_ago"] == "1d23h"


# --------------------------------------------------------------------------- #
# T-013: the route parser
# --------------------------------------------------------------------------- #

_ROUTE_FIXTURES = sorted(FIXTURE_DIR.glob("cisco_xr/*/healthy/show-route-*.txt"))

# Every key the meta table in the T-013 spec requires, present in both shapes
# (found or not) -- ``None`` rather than absent, except ``path_count`` which
# is always a count string.
_ROUTE_META_KEYS = (
    "found",
    "prefix",
    "protocol",
    "distance",
    "metric",
    "local_label",
    "installed_ago",
    "path_count",
)


def test_at_least_one_route_fixture_of_each_shape_is_on_disk():
    """A sanity check on the parametrization source below.

    If this ever fails, the round-trip test below would be silently
    parametrized over an empty or lopsided list -- worth failing loudly on
    its own rather than only as a mysteriously-shrunk parametrize count.
    """

    assert len(_ROUTE_FIXTURES) == 44


@pytest.mark.parametrize("fixture_path", _ROUTE_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_committed_route_fixture_round_trips_clean(fixture_path: Path):
    """Section 0.10, pinned against every real fixture on disk.

    Discovered from the filesystem rather than a hardcoded list, so a future
    ``nettools capture`` run that adds a device or a prefix is covered
    automatically instead of silently going unchecked.
    """

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "route", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0


def test_rr1_route_to_pe1_loopback_yields_two_paths():
    """RR1 -> 10.255.0.11/32 (PE1): a real, healthy route with two ECMP paths."""

    raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-route-10-255-0-11-32.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "route", raw)
    assert status is tp.PARSE_OK
    meta = parsed["meta"]
    assert meta["found"] is True
    assert meta["prefix"] == "10.255.0.11/32"
    assert meta["protocol"] == "isis CORE"
    assert meta["distance"] == "115"
    assert meta["metric"] == "20"
    assert len(parsed["records"]) == 2
    assert [r["next_hop"] for r in parsed["records"]] == ["10.0.1.17", "10.0.1.19"]
    assert [r["interface"] for r in parsed["records"]] == [
        "GigabitEthernet0/0/0/0",
        "GigabitEthernet0/0/0/1",
    ]


def test_route_not_found_is_parse_ok_with_empty_records():
    """'% Network not in table' is the device answering correctly -- never
    PARSE_FAILED. This is the branch checks.route_present reads."""

    raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-route-192-0-2-1-32.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "route", raw)
    assert status is tp.PARSE_OK  # asserted explicitly: this is the crux
    assert parsed["meta"]["found"] is False
    assert parsed["records"] == []


@pytest.mark.parametrize(
    "device,fixture",
    [
        ("RR1", "show-route-10-255-0-11-32.txt"),
        ("RR1", "show-route-192-0-2-1-32.txt"),
        ("PE4", "show-route-10-255-0-14-32.txt"),  # directly connected loopback
    ],
)
def test_every_route_meta_key_is_present_in_every_case(device, fixture):
    """Every key in the T-013 meta table, present for both shapes -- ``None``
    rather than absent, so a consumer never has to distinguish 'missing'
    from 'not applicable'."""

    raw = _load_fixture("cisco_xr", device, "healthy", fixture)
    parsed, status = tp.parse_template_output("cisco_xr", "route", raw)
    assert status is tp.PARSE_OK
    for key in _ROUTE_META_KEYS:
        assert key in parsed["meta"], f"{device}/{fixture}: missing {key}"


def test_route_volatile_fields_include_installed_ago_but_not_metric_or_distance():
    """The point of the split: a changed metric or distance is a real
    routing event, but installed_ago moves on every capture regardless."""

    volatile = tp.template_volatile_fields("cisco_xr", "route")
    assert "installed_ago" in volatile
    assert "metric" not in volatile
    assert "distance" not in volatile


def test_route_record_key_is_registered():
    assert tp.template_record_key("cisco_xr", "route") == "next_hop"


def test_route_garbage_input_raises_parse_error_and_reports_parse_failed():
    """Something clearly not route output must not be silently accepted."""

    garbage = "lorem ipsum\nnot a route"

    with pytest.raises(tp.ParseError):
        tp.parse_xr_route(garbage)

    parsed, status = tp.parse_template_output("cisco_xr", "route", garbage)
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_route_truncated_output_does_not_raise_and_still_accounts_cleanly():
    """The first 4 lines of a real fixture -- cut off right after the
    "Routing entry for" header, before any descriptor block. Must not raise,
    and whatever is captured must still satisfy the 0.10 accounting."""

    raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-route-10-255-0-11-32.txt")
    truncated = "\n".join(raw.splitlines()[:4])

    parsed, status = tp.parse_template_output("cisco_xr", "route", truncated)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["meta"]["found"] is True
    assert parsed["records"] == []


def test_route_unrecognised_line_surfaces_in_unaccounted_lines():
    """The 0.10 guardrail: a line the template cannot know about must be
    surfaced, not silently swallowed. Proves the accounting is not
    decorative."""

    raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-route-10-255-0-11-32.txt")
    injected = raw.replace(
        "  No advertising protos. ",
        "  No advertising protos. \n  Some New Vendor Field: 42",
    )

    parsed, status = tp.parse_template_output("cisco_xr", "route", injected)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == ["Some New Vendor Field: 42"]


def test_a_connected_route_is_machine_checkable_not_string_matched():
    """`next_hop` is polymorphic for a connected route, so the fact is also
    exposed as a boolean.

    The sentinel `"directly connected"` keeps next_hop usable as the record
    identity, but a consumer calling ipaddress.ip_address() on it would crash.
    `directly_connected` lets downstream code branch on the fact without
    string-matching a sentinel.
    """

    raw = (FIXTURE_DIR / "cisco_xr" / "PE4" / "healthy" / "show-route-10-255-0-14-32.txt").read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "route", raw)

    assert status is tp.PARSE_OK
    assert len(parsed["records"]) == 1
    record = parsed["records"][0]
    assert record["directly_connected"] is True
    assert record["next_hop"] == "directly connected"
    assert record["interface"] == "Loopback0"


def test_a_normal_route_is_not_flagged_connected():
    """The other half: the flag must be present and False on ordinary paths,
    so a consumer never has to distinguish absent from false."""

    raw = (FIXTURE_DIR / "cisco_xr" / "RR1" / "healthy" / "show-route-10-255-0-11-32.txt").read_text()
    parsed, _status = tp.parse_template_output("cisco_xr", "route", raw)

    assert parsed["records"], "expected descriptor blocks"
    for record in parsed["records"]:
        assert record["directly_connected"] is False
        assert record["next_hop"] != "directly connected"


# --------------------------------------------------------------------------- #
# T-014: the interface parser
# --------------------------------------------------------------------------- #

# Excludes show-interfaces-brief.txt deliberately: that is a different,
# static-intent command already handled by parsers.py, not this template.
_INTERFACE_FIXTURES = sorted(
    p
    for p in FIXTURE_DIR.glob("cisco_xr/*/healthy/show-interfaces-*.txt")
    if p.name != "show-interfaces-brief.txt"
)

# Every key the T-014 spec's meta table requires, present for all three
# shapes (physical / loopback / VLAN subinterface) -- ``None`` where not
# applicable, never absent.
_INTERFACE_META_KEYS = (
    "interface",
    "admin_state",
    "line_state",
    "description",
    "mtu",
    "bandwidth_kbps",
    "mac_address",
    "encapsulation",
    "ip_address",
    "state_transitions",
    "last_link_flapped",
    "hardware_type",
)


def test_at_least_one_interface_fixture_of_each_shape_is_on_disk():
    """A sanity check on the parametrization source below.

    45 fixtures: 34 physical GigabitEthernet, 9 Loopback, 2 VLAN
    subinterface. If this ever fails, the round-trip test below would be
    silently parametrized over an empty or lopsided list -- worth failing
    loudly on its own rather than only as a mysteriously-shrunk parametrize
    count.
    """

    assert len(_INTERFACE_FIXTURES) == 45


@pytest.mark.parametrize("fixture_path", _INTERFACE_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_committed_interface_fixture_round_trips_clean(fixture_path: Path):
    """Section 0.10, pinned against every real fixture on disk.

    Discovered from the filesystem rather than a hardcoded list, so a future
    ``nettools capture`` run that adds a device or an interface is covered
    automatically instead of silently going unchecked.
    """

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "interface", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0


def test_a_physical_interface_yields_the_full_counter_block():
    """P1's Gi0/0/0/0: a real, healthy physical interface."""

    raw = _load_fixture("cisco_xr", "P1", "healthy", "show-interfaces-gi0-0-0-0.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "interface", raw)
    assert status is tp.PARSE_OK
    meta = parsed["meta"]
    assert meta["interface"] == "GigabitEthernet0/0/0/0"
    assert meta["admin_state"] == "up"
    assert meta["line_state"] == "up"
    assert meta["mtu"] == "1514"
    assert meta["bandwidth_kbps"] == "1000000"
    assert meta["encapsulation"] == "ARPA"
    assert meta["description"] == "TO-P2"
    counters = {r["counter"] for r in parsed["records"]}
    assert "input_errors" in counters
    assert "carrier_transitions" in counters


def test_pe1_gi0_0_0_2_300_is_the_only_line_down_interface_fixture():
    """PE1's Gi0/0/0/2.300: admin up, line protocol down.

    This is the only broken-state interface fixture that exists anywhere in
    the committed set -- every other one of the 45 is line-up. T-020's
    ``interface_state`` check depends on this exact fixture to exercise its
    "admin up, line down" branch, so the admin/line state split is asserted
    explicitly here rather than folded into a generic meta-keys check.
    """

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "show-interfaces-gi0-0-0-2-300.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "interface", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["admin_state"] == "up"
    assert parsed["meta"]["line_state"] == "down"


def test_a_loopback_has_no_counter_block_but_still_parses_ok():
    """A Loopback reports no rate, no duplex/ARP detail, and IOS-XR emits no
    counter block for it at all -- ``records == []`` is the correct, healthy
    outcome, never a parse failure."""

    raw = _load_fixture("cisco_xr", "P1", "healthy", "show-interfaces-lo0.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "interface", raw)
    assert status is tp.PARSE_OK
    assert parsed["records"] == []
    assert parsed["meta"]["mac_address"] is None
    assert parsed["meta"]["encapsulation"] == "Loopback"


@pytest.mark.parametrize(
    "device,fixture",
    [
        ("P1", "show-interfaces-gi0-0-0-0.txt"),  # physical
        ("P1", "show-interfaces-lo0.txt"),  # loopback
        ("PE1", "show-interfaces-gi0-0-0-2-300.txt"),  # VLAN subinterface, line down
    ],
)
def test_every_interface_meta_key_is_present_in_every_shape(device, fixture):
    """Every key in the T-014 meta table, present for all three shapes --
    ``None`` rather than absent, so a consumer never has to distinguish
    'missing' from 'not applicable'."""

    raw = _load_fixture("cisco_xr", device, "healthy", fixture)
    parsed, status = tp.parse_template_output("cisco_xr", "interface", raw)
    assert status is tp.PARSE_OK
    for key in _INTERFACE_META_KEYS:
        assert key in parsed["meta"], f"{device}/{fixture}: missing {key}"


def test_interface_volatile_fields_split_noisy_counters_from_error_counters():
    """The point of the split: packets/bytes counters grow on any live,
    healthy link and are pure noise, but a change in an error/quality
    counter is exactly the signal interface_state (T-020) exists to catch.
    Both directions are asserted -- the negative half is the point."""

    volatile = tp.template_volatile_fields("cisco_xr", "interface")

    assert "last_link_flapped" in volatile
    assert "packets_input" in volatile
    assert "bytes_input" in volatile
    assert "total_input_drops" in volatile
    assert "packets_output" in volatile
    assert "bytes_output" in volatile
    assert "total_output_drops" in volatile

    assert "input_errors" not in volatile
    assert "crc" not in volatile
    assert "carrier_transitions" not in volatile


def test_interface_record_key_is_registered():
    assert tp.template_record_key("cisco_xr", "interface") == "counter"


def test_interface_garbage_input_raises_parse_error_and_reports_parse_failed():
    """Something clearly not interface output must not be silently accepted."""

    garbage = "lorem ipsum\nnot an interface"

    with pytest.raises(tp.ParseError):
        tp.parse_xr_interface(garbage)

    parsed, status = tp.parse_template_output("cisco_xr", "interface", garbage)
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_interface_truncated_output_does_not_raise_and_still_accounts_cleanly():
    """The first 3 lines of a real fixture -- cut off right after the header,
    before any hardware/address detail. Must not raise, and whatever is
    captured must still satisfy the 0.10 accounting."""

    raw = _load_fixture("cisco_xr", "P1", "healthy", "show-interfaces-gi0-0-0-0.txt")
    truncated = "\n".join(raw.splitlines()[:3])

    parsed, status = tp.parse_template_output("cisco_xr", "interface", truncated)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["meta"]["interface"] == "GigabitEthernet0/0/0/0"
    assert parsed["records"] == []


def test_interface_unrecognised_line_surfaces_in_unaccounted_lines():
    """The 0.10 guardrail: a line the template cannot know about must be
    surfaced, not silently swallowed. Proves the accounting is not
    decorative."""

    raw = _load_fixture("cisco_xr", "P1", "healthy", "show-interfaces-gi0-0-0-0.txt")
    injected = raw.replace(
        "  Last link flapped 2d00h",
        "  Last link flapped 2d00h\n  Some New Vendor Field: 42",
    )

    parsed, status = tp.parse_template_output("cisco_xr", "interface", injected)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == ["Some New Vendor Field: 42"]


# --------------------------------------------------------------------------- #
# T-015: the logging parser
# --------------------------------------------------------------------------- #

_LOGGING_FIXTURES = sorted(FIXTURE_DIR.glob("cisco_xr/*/healthy/show-logging-last-200.txt"))

# Every key the T-015 spec's meta table requires, present in every case --
# ``None`` where not applicable, never absent.
_LOGGING_META_KEYS = (
    "lines",
    "window_start",
    "window_end",
    "syslog_enabled",
    "messages_dropped",
    "console_level",
    "monitor_level",
    "trap_level",
    "buffer_level",
    "logging_to",
    "buffer_size_bytes",
)


def test_at_least_one_logging_fixture_per_device_is_on_disk():
    """A sanity check on the parametrization source below.

    If this ever fails, the round-trip test below would be silently
    parametrized over an empty or lopsided list -- worth failing loudly on
    its own rather than only as a mysteriously-shrunk parametrize count.
    """

    assert len(_LOGGING_FIXTURES) == 9  # one per device


@pytest.mark.parametrize("fixture_path", _LOGGING_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_committed_logging_fixture_round_trips_clean(fixture_path: Path):
    """Section 0.10, pinned against every real fixture on disk.

    Discovered from the filesystem rather than a hardcoded list, so a future
    ``nettools capture`` run that adds a device is covered automatically
    instead of silently going unchecked. All 200 entries per fixture must be
    captured as records -- none dropped, none left unaccounted.
    """

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "logging", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0
    assert len(parsed["records"]) == 200


def test_the_mnemonic_splits_into_facility_severity_and_code():
    """The whole point of T-015: a lookup table keyed on facility+code, or on
    the full mnemonic, must both be possible. The full mnemonic is stored
    without its leading '%'."""

    raw = _load_fixture("cisco_xr", "PE2", "healthy", "show-logging-last-200.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "logging", raw)
    assert status is tp.PARSE_OK

    record = next(
        r for r in parsed["records"] if r["mnemonic"] == "SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL"
    )
    assert record["facility"] == "SECURITY-SSHD_SYSLOG_PRX"
    assert record["severity"] == "6"
    assert record["code"] == "INFO_GENERAL"
    assert not record["mnemonic"].startswith("%")


def test_severity_across_the_real_corpus_is_exactly_what_stage_2_can_route_on():
    """Stage 2 routes an event to a flow by looking the mnemonic up in a
    table -- that only works as a deterministic lookup if the severities
    actually observed on this fabric are the small, fixed set found by the
    original Loki survey (T-004): 3, 6, and 7. This pins that guarantee against all 1800
    entries across all 9 devices, not just one fixture."""

    severities: set[str] = set()
    for fixture_path in _LOGGING_FIXTURES:
        raw = fixture_path.read_text()
        parsed, status = tp.parse_template_output("cisco_xr", "logging", raw)
        assert status is tp.PARSE_OK
        severities.update(r["severity"] for r in parsed["records"])

    assert severities == {"3", "6", "7"}


def test_trap_level_and_collector_address_are_the_obs_041_evidence():
    """The evidence that diagnosed OBS-041: the trap level is
    'informational' while only severities 3 and 4 ever reach the log
    collector, which is how the drop was known to be downstream of the
    device rather than on it. Pinned so a future parser change cannot
    quietly lose either field."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "show-logging-last-200.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "logging", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["trap_level"] == "informational"
    assert parsed["meta"]["logging_to"] == "172.20.250.101"


@pytest.mark.parametrize("fixture_path", _LOGGING_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_logging_meta_key_is_present(fixture_path: Path):
    """Every key in the T-015 meta table, present -- ``None`` rather than
    absent, so a consumer never has to distinguish 'missing' from 'not
    applicable'."""

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "logging", raw)
    assert status is tp.PARSE_OK
    for key in _LOGGING_META_KEYS:
        assert key in parsed["meta"], f"{fixture_path}: missing {key}"


def test_logging_record_key_is_none_because_entries_have_no_stable_identity():
    """A log entry is part of an append-only stream, not a set of named
    objects: two captures share history but the set of entries only grows.
    ``None`` is the contract's documented way to say 'positional, cannot be
    matched by identity' -- do not 'fix' this to 'timestamp', which would
    make diff_evidence produce nonsense."""

    assert tp.template_record_key("cisco_xr", "logging") is None


def test_logging_volatile_fields_are_the_four_that_move_on_every_capture():
    volatile = tp.template_volatile_fields("cisco_xr", "logging")
    assert {"lines", "window_start", "window_end", "messages_dropped"} <= volatile


def test_logging_garbage_input_raises_parse_error_and_reports_parse_failed():
    """Something clearly not logging output must not be silently accepted."""

    garbage = "lorem ipsum\nnot a logging response"

    with pytest.raises(tp.ParseError):
        tp.parse_xr_logging(garbage)

    parsed, status = tp.parse_template_output("cisco_xr", "logging", garbage)
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_a_malformed_log_entry_increments_unparsed_rows_not_unaccounted_lines():
    """Proves the two counters are actually distinct: a line whose outer
    shape (node/timestamp/process[pid]) the template recognises, but whose
    mnemonic does not split into facility-severity-code, is a *known* shape
    with malformed content -- not an *unknown* line. It must therefore be
    consumed (never surface in unaccounted_lines) while still incrementing
    unparsed_rows, and it must not produce a record."""

    raw = _load_fixture("cisco_xr", "PE2", "healthy", "show-logging-last-200.txt")
    mangled = raw.replace(
        "%SECURITY-SSHD_SYSLOG_PRX-6-INFO_GENERAL",
        "%SECURITY_SSHD_SYSLOG_PRX_SIX_INFO_GENERAL",
        1,
    )
    assert mangled != raw  # the replacement actually happened

    parsed, status = tp.parse_template_output("cisco_xr", "logging", mangled)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unparsed_rows"] >= 1
    assert parsed["meta"]["unaccounted_lines"] == []
    assert len(parsed["records"]) == 199
    assert not any(
        r["mnemonic"] == "SECURITY_SSHD_SYSLOG_PRX_SIX_INFO_GENERAL" for r in parsed["records"]
    )


def test_logging_truncated_output_does_not_raise_and_still_accounts_cleanly():
    """The header block alone, with no entries below it -- a real shape
    (a fresh device, or a capture cut off right after the header). Must not
    raise, and whatever is captured must still satisfy the 0.10 accounting."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "show-logging-last-200.txt")
    header_only = "\n".join(
        line
        for line in raw.splitlines()
        if not line.strip().startswith("RP/0/RP0/CPU0:")
    )

    parsed, status = tp.parse_template_output("cisco_xr", "logging", header_only)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["records"] == []
    assert parsed["meta"]["lines"] == "0"
    assert parsed["meta"]["trap_level"] == "informational"


def test_logging_unrecognised_line_surfaces_in_unaccounted_lines():
    """The 0.10 guardrail: a line the template cannot know about must be
    surfaced, not silently swallowed. Proves the accounting is not
    decorative."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "show-logging-last-200.txt")
    injected = raw.replace(
        "Log Buffer (4194303 bytes):",
        "Log Buffer (4194303 bytes):\nSome New Vendor Field: 42",
    )

    parsed, status = tp.parse_template_output("cisco_xr", "logging", injected)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == ["Some New Vendor Field: 42"]


# --------------------------------------------------------------------------- #
# T-016: the ping parser
# --------------------------------------------------------------------------- #

_PING_FIXTURES = sorted(FIXTURE_DIR.glob("cisco_xr/*/healthy/ping-*.txt"))

# Every key the T-016 spec's meta table requires, present in every case --
# ``None`` where not applicable, never absent.
_PING_META_KEYS = (
    "target",
    "sent",
    "received",
    "success_pct",
    "loss_pct",
    "size_bytes",
    "timeout_seconds",
    "result_string",
    "rtt_min",
    "rtt_avg",
    "rtt_max",
)


def test_all_ten_ping_fixtures_are_on_disk():
    """A sanity check on the parametrization source below: 9 real successes
    plus the one deliberately captured total-failure fixture. If this ever
    fails, the round-trip test below would be silently parametrized over a
    shrunk list rather than failing loudly on its own."""

    assert len(_PING_FIXTURES) == 10


@pytest.mark.parametrize("fixture_path", _PING_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_committed_ping_fixture_round_trips_clean(fixture_path: Path):
    """Section 0.10, pinned against every real fixture on disk -- both the
    9 success captures and the one 0%-success capture."""

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "ping", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["records"] == []


def test_a_successful_ping_reports_full_rtt_and_zero_loss():
    """PE1 -> 10.255.0.31: a real, healthy, 100%-success ping."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "ping-10-255-0-31.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "ping", raw)
    assert status is tp.PARSE_OK

    meta = parsed["meta"]
    assert meta["target"] == "10.255.0.31"
    assert meta["sent"] == "5"
    assert meta["received"] == "5"
    assert meta["success_pct"] == "100"
    assert meta["loss_pct"] == "0"
    assert meta["result_string"] == "!!!!!"
    assert meta["rtt_min"] is not None
    assert meta["rtt_avg"] is not None
    assert meta["rtt_max"] is not None


def test_zero_percent_success_omits_the_round_trip_line_entirely():
    """PE1 -> 192.0.2.1: the one deliberately captured total-failure
    fixture. This is the whole reason the fixture exists -- at 0% success
    IOS-XR does not print the 'round-trip min/avg/max' clause at all, so
    rtt_min/avg/max must come back None, never the string '0'. A parser
    that assumed the clause always follows the success-rate line would
    pass on all 9 other fixtures and break on exactly this one."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "ping-192-0-2-1.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "ping", raw)
    assert status is tp.PARSE_OK

    meta = parsed["meta"]
    assert meta["target"] == "192.0.2.1"
    assert meta["sent"] == "5"
    assert meta["received"] == "0"
    assert meta["success_pct"] == "0"
    assert meta["loss_pct"] == "100"
    assert meta["result_string"] == "....."
    assert meta["rtt_min"] is None
    assert meta["rtt_avg"] is None
    assert meta["rtt_max"] is None


@pytest.mark.parametrize("fixture_path", _PING_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_ping_meta_key_is_present(fixture_path: Path):
    """Every key in the T-016 meta table, present -- ``None`` rather than
    absent, in both the success and the 0%-success shape."""

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "ping", raw)
    assert status is tp.PARSE_OK
    for key in _PING_META_KEYS:
        assert key in parsed["meta"], f"{fixture_path}: missing {key}"


def test_ping_volatile_fields_are_the_rtts_and_result_string_not_the_counts():
    """The point of the split: round-trip times and the exact reply pattern
    move run to run on an unchanged network and are noise. success_pct,
    loss_pct, sent, and received are deliberately NOT volatile -- a ping
    going from 100% to 0% is the entire signal this template exists to
    produce, and marking those volatile would discard it (the same trap
    bgp_neighbor's last_reset_reason avoids). Both directions are asserted
    -- the negative half is the point."""

    volatile = tp.template_volatile_fields("cisco_xr", "ping")

    assert "rtt_min" in volatile
    assert "rtt_avg" in volatile
    assert "rtt_max" in volatile
    assert "result_string" in volatile

    assert "success_pct" not in volatile
    assert "loss_pct" not in volatile
    assert "sent" not in volatile
    assert "received" not in volatile


def test_ping_record_key_is_none_because_there_are_no_records():
    """Deliberately None, and for a different reason than logging's None:
    ping has no records at all -- the whole result is one summary -- so
    there is no per-record field for an identity key to name."""

    assert tp.template_record_key("cisco_xr", "ping") is None


def test_ping_garbage_input_raises_parse_error_and_reports_parse_failed():
    """Something clearly not ping output must not be silently accepted."""

    garbage = "lorem ipsum\nnot a ping"

    with pytest.raises(tp.ParseError):
        tp.parse_xr_ping(garbage)

    parsed, status = tp.parse_template_output("cisco_xr", "ping", garbage)
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_a_synthetic_partial_result_string_parses_cleanly():
    """No fixture shows a partial reply pattern -- both real shapes on disk
    are all-'!' or all-'.' -- but a mixed string like '!!.!!' is a legitimate
    device output *format* already proven by those two real shapes, not an
    invented one, and is exactly what a lossy-but-not-dead path looks like
    on a real fabric."""

    raw = (
        "\n"
        "Sat Aug 15 18:06:00.315 UTC\n"
        "Type escape sequence to abort.\n"
        "Sending 5, 100-byte ICMP Echos to 10.255.0.99 timeout is 2 seconds:\n"
        "!!.!!\n"
        "Success rate is 60 percent (3/5), round-trip min/avg/max = 1/2/4 ms\n"
    )

    parsed, status = tp.parse_template_output("cisco_xr", "ping", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0

    meta = parsed["meta"]
    assert meta["result_string"] == "!!.!!"
    assert meta["received"] == "3"
    assert meta["success_pct"] == "60"
    assert meta["loss_pct"] == "40"
    assert meta["rtt_min"] == "1"
    assert meta["rtt_avg"] == "2"
    assert meta["rtt_max"] == "4"


def test_ping_truncated_output_does_not_raise_and_still_accounts_cleanly():
    """Truncated right after the request line -- the first two lines of
    real, ping-specific content ('Type escape sequence to abort.' and
    'Sending N, ...'), before any reply characters or the summary arrive.
    The universal blank-line/timestamp-banner preamble every command's
    output carries is not itself ping content, so it is excluded from what
    is being truncated here -- same convention as every other truncated-
    output test in this module, which all cut right after their own
    template's first substantive line. Must not raise: the device simply
    has not finished answering yet, exactly as parse_xr_logging's
    header-only truncation is not an error either."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "ping-10-255-0-31.txt")
    lines = raw.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "Type escape sequence to abort.")
    truncated = "\n".join(lines[start : start + 2])

    parsed, status = tp.parse_template_output("cisco_xr", "ping", truncated)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["records"] == []
    assert parsed["meta"]["target"] == "10.255.0.31"
    assert parsed["meta"]["result_string"] is None
    assert parsed["meta"]["success_pct"] is None
    assert parsed["meta"]["rtt_min"] is None


def test_ping_unrecognised_line_surfaces_in_unaccounted_lines():
    """The 0.10 guardrail: a line the template cannot know about must be
    surfaced, not silently swallowed. Proves the accounting is not
    decorative."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "ping-10-255-0-31.txt")
    injected = raw.replace(
        "Sending 5, 100-byte ICMP Echos to 10.255.0.31 timeout is 2 seconds:",
        "Sending 5, 100-byte ICMP Echos to 10.255.0.31 timeout is 2 seconds:\n"
        "Some New Vendor Field: 42",
    )

    parsed, status = tp.parse_template_output("cisco_xr", "ping", injected)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == ["Some New Vendor Field: 42"]


# --------------------------------------------------------------------------- #
# T-017: the traceroute parser
# --------------------------------------------------------------------------- #

_TRACEROUTE_FIXTURES = sorted(FIXTURE_DIR.glob("cisco_xr/*/healthy/traceroute-*.txt"))

# Every key the T-017 spec's meta table requires, present in every case --
# ``None`` where not applicable, never absent.
_TRACEROUTE_META_KEYS = (
    "target",
    "hops",
    "completed",
    "max_hop_reached",
)


def test_all_nine_traceroute_fixtures_are_on_disk():
    """A sanity check on the parametrization source below -- one traceroute
    per device. If this ever fails, the round-trip test below would be
    silently parametrized over a shrunk list rather than failing loudly on
    its own."""

    assert len(_TRACEROUTE_FIXTURES) == 9


@pytest.mark.parametrize("fixture_path", _TRACEROUTE_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_committed_traceroute_fixture_round_trips_clean(fixture_path: Path):
    """Section 0.10, pinned against every real fixture on disk."""

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0


def test_pe1_traceroute_carries_an_mpls_label_and_a_lossy_final_hop():
    """PE1 -> 10.255.0.31: two hops, the first carrying an MPLS label, the
    second losing exactly one of its three probes -- the standard shape
    every two-hop fixture in this fabric shares."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "traceroute-10-255-0-31.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", raw)
    assert status is tp.PARSE_OK

    meta = parsed["meta"]
    assert meta["target"] == "10.255.0.31"
    assert meta["hops"] == "2"

    records = parsed["records"]
    assert len(records) == 2
    assert records[0]["hop"] == "1"
    assert records[0]["mpls_label"] == "24010"
    assert records[1]["hop"] == "2"
    assert records[1]["rtt_msec"] == ["2", None, "2"]
    assert records[1]["probes_lost"] == "1"


def test_completed_is_true_on_every_fixture_even_though_none_end_at_the_target():
    """The trap this spec exists to prevent: defining ``completed`` as 'the
    last hop's address equals the target' would call all nine of these
    successful traces incomplete, because every trace in this fabric ends
    at one of RR1's own interface addresses (10.0.1.16 / 10.0.1.18 / the
    RR1-fixture's own 10.0.1.0), never at the target itself -- the
    destination replies from whichever interface the probe arrived on, not
    from its loopback. The correct definition -- the final hop returned at
    least one non-'*' probe -- must be True on all nine real fixtures
    despite that."""

    known_non_target_last_hops = {"10.0.1.16", "10.0.1.18", "10.0.1.0"}

    for fixture_path in _TRACEROUTE_FIXTURES:
        raw = fixture_path.read_text()
        parsed, status = tp.parse_template_output("cisco_xr", "traceroute", raw)
        assert status is tp.PARSE_OK
        assert parsed["meta"]["completed"] is True, fixture_path
        last_hop_address = parsed["records"][-1]["address"]
        assert last_hop_address in known_non_target_last_hops, (
            f"{fixture_path}: last hop {last_hop_address} was not one of the addresses "
            "this test documents as never being the target itself"
        )
        assert parsed["meta"]["target"] not in {r["address"] for r in parsed["records"]}


def test_a_synthetic_all_lost_final_hop_gives_completed_false():
    """No fixture shows a final hop with zero replies -- every one of the 9
    has at least one successful probe at its last hop -- but '*' is a
    legitimate device output token already proven by every one of those
    real fixtures, and three of them in a row is not an invented format,
    just the same proven token repeated. This is the case ``completed``
    exists to catch."""

    raw = (
        "\n"
        "Sat Aug 15 18:06:00.315 UTC\n"
        "\n"
        "Type escape sequence to abort.\n"
        "Tracing the route to 10.255.0.31\n"
        "\n"
        " 1  10.0.0.5 [MPLS: Label 24008 Exp 0] 2 msec  2 msec  2 msec \n"
        " 2  10.0.1.18 * * * \n"
    )

    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0

    assert parsed["meta"]["completed"] is False
    assert parsed["meta"]["hops"] == "2"
    assert parsed["meta"]["max_hop_reached"] == "2"
    assert parsed["records"][-1]["rtt_msec"] == [None, None, None]
    assert parsed["records"][-1]["probes_lost"] == "3"


@pytest.mark.parametrize("fixture_path", _TRACEROUTE_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR)))
def test_every_traceroute_meta_key_is_present(fixture_path: Path):
    """Every key in the T-017 meta table, present -- ``None`` rather than
    absent."""

    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", raw)
    assert status is tp.PARSE_OK
    for key in _TRACEROUTE_META_KEYS:
        assert key in parsed["meta"], f"{fixture_path}: missing {key}"


def test_traceroute_volatile_fields_are_rtt_and_loss_not_address_or_completion():
    """rtt_msec and probes_lost move on every capture of an unchanged path --
    ordinary jitter and transient probe loss, not a signal. address, hops,
    completed, and mpls_label are deliberately NOT volatile: a path
    changing length, a hop's address changing, a trace ceasing to complete,
    or a label changing are all real events worth diffing -- the same trap
    bgp_neighbor's last_reset_reason avoids. Both directions are asserted --
    the negative half is the point."""

    volatile = tp.template_volatile_fields("cisco_xr", "traceroute")

    assert "rtt_msec" in volatile
    assert "probes_lost" in volatile

    assert "address" not in volatile
    assert "hops" not in volatile
    assert "completed" not in volatile
    assert "mpls_label" not in volatile


def test_traceroute_record_key_is_hop_not_address():
    """The hop number, not the address, is the stable identity across two
    captures -- a hop's address can legitimately change when the path
    moves, and that must show up as a difference, not vanish as a changed
    identity."""

    assert tp.template_record_key("cisco_xr", "traceroute") == "hop"


def test_traceroute_garbage_input_raises_parse_error_and_reports_parse_failed():
    """Something clearly not traceroute output must not be silently
    accepted."""

    garbage = "lorem ipsum\nnot a traceroute"

    with pytest.raises(tp.ParseError):
        tp.parse_xr_traceroute(garbage)

    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", garbage)
    assert parsed is None
    assert status is tp.PARSE_FAILED


def test_traceroute_truncated_output_does_not_raise_and_still_accounts_cleanly():
    """Truncated right after the header -- 'Type escape sequence to abort.'
    and 'Tracing the route to ...', before any hop line arrives. Must not
    raise: the device simply has not finished answering yet, exactly as
    parse_xr_logging's and parse_xr_ping's header-only truncations are not
    errors either."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "traceroute-10-255-0-31.txt")
    lines = raw.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "Type escape sequence to abort.")
    truncated = "\n".join(lines[start : start + 2])

    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", truncated)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["records"] == []
    assert parsed["meta"]["hops"] == "0"
    assert parsed["meta"]["completed"] is False
    assert parsed["meta"]["target"] == "10.255.0.31"
    assert parsed["meta"]["max_hop_reached"] is None


def test_traceroute_unrecognised_line_surfaces_in_unaccounted_lines():
    """The 0.10 guardrail: a line the template cannot know about must be
    surfaced, not silently swallowed. Proves the accounting is not
    decorative."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "traceroute-10-255-0-31.txt")
    injected = raw.replace(
        "Tracing the route to 10.255.0.31",
        "Tracing the route to 10.255.0.31\nSome New Vendor Field: 42",
    )

    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", injected)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == ["Some New Vendor Field: 42"]


def test_a_malformed_hop_line_increments_unparsed_rows_not_unaccounted_lines():
    """Proves the two counters are actually distinct: a line whose outer
    shape (a leading hop number followed by more content) the template
    recognises as 'probably a hop line', but whose probe fields do not fit
    the strict three-probe shape, is a *known* shape with malformed content
    -- not an *unknown* line. It must therefore be consumed (never surface
    in unaccounted_lines) while still incrementing unparsed_rows, and it
    must not produce a record."""

    raw = _load_fixture("cisco_xr", "PE1", "healthy", "traceroute-10-255-0-31.txt")
    mangled = raw.replace(
        " 2  10.0.1.16 2 msec  *  2 msec \n",
        " 2  10.0.1.16 2 msec  garbled  2 msec \n",
        1,
    )
    assert mangled != raw  # the replacement actually happened

    parsed, status = tp.parse_template_output("cisco_xr", "traceroute", mangled)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unparsed_rows"] == 1
    assert parsed["meta"]["unaccounted_lines"] == []
    assert len(parsed["records"]) == 1
    assert parsed["records"][0]["hop"] == "1"


# --------------------------------------------------------------------------- #
# The `broken` label (T-011) -- shapes no healthy fixture can produce
# --------------------------------------------------------------------------- #

_BROKEN_TEMPLATE_FIXTURES = sorted(
    p
    for pattern in (
        "show-bgp-neighbor-*.txt",
        "show-route-*.txt",
        "show-interfaces-*.txt",
        "show-logging-last-*.txt",
        "ping-*.txt",
        "traceroute-*.txt",
    )
    for p in FIXTURE_DIR.glob(f"cisco_xr/*/broken/{pattern}")
    if "brief" not in p.name
)

_BROKEN_TEMPLATE_BY_PREFIX = {
    "show-bgp-neighbor-": "bgp_neighbor",
    "show-route-": "route",
    "show-interfaces-": "interface",
    "show-logging-last-": "logging",
    "ping-": "ping",
    "traceroute-": "traceroute",
}


def _template_for_fixture(path):
    for prefix, template in _BROKEN_TEMPLATE_BY_PREFIX.items():
        if path.name.startswith(prefix):
            return template
    raise AssertionError(f"no template maps to {path.name}")


def test_the_broken_label_actually_exists():
    """Guards against the whole suite passing because the label is empty."""

    assert len(_BROKEN_TEMPLATE_FIXTURES) > 140, len(_BROKEN_TEMPLATE_FIXTURES)


@pytest.mark.parametrize(
    "fixture", _BROKEN_TEMPLATE_FIXTURES, ids=lambda p: f"{p.parent.parent.name}/{p.name}"
)
def test_every_broken_fixture_round_trips_cleanly(fixture):
    """§0.10 over the isolated-PE2 capture.

    The `broken` label exposed four parser gaps the healthy set could not
    reach. This is what stops them regressing.
    """

    template = _template_for_fixture(fixture)
    parsed, status = tp.parse_template_output("cisco_xr", template, fixture.read_text())

    assert status is tp.PARSE_OK
    assert parsed["meta"]["unaccounted_lines"] == []
    assert parsed["meta"]["unparsed_rows"] == 0


def test_a_down_bgp_session_captures_why_it_is_down():
    """`BGP state = Idle (No route to multi-hop neighbor)`.

    The parenthetical is the single most diagnostic field in the command --
    it says *why* -- and it cannot appear on an established session, so no
    healthy fixture contains one.
    """

    raw = (
        FIXTURE_DIR / "cisco_xr" / "RR1" / "broken" / "show-bgp-neighbor-10-255-0-12.txt"
    ).read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["state"] == "Idle"
    assert parsed["meta"]["connection_state"] == "Idle"
    assert parsed["meta"]["state_reason"] == "No route to multi-hop neighbor"


def test_an_established_session_has_no_state_reason():
    """The other direction: the key is always present, and None when up."""

    raw = (
        FIXTURE_DIR / "cisco_xr" / "RR1" / "healthy" / "show-bgp-neighbor-10-255-0-11.txt"
    ).read_text()
    parsed, _status = tp.parse_template_output("cisco_xr", "bgp_neighbor", raw)

    assert parsed["meta"]["state"] == "Established"
    assert parsed["meta"]["state_reason"] is None


@pytest.mark.parametrize("interface", ["gi0-0-0-0", "gi0-0-0-1"])
def test_a_shut_interface_reports_admin_down_on_both_states(interface):
    """IOS-XR says "is administratively down, line protocol is administratively down".

    OBS-044 recorded the admin_state normalisation as implemented but
    untested, because all 45 healthy fixtures are admin-up. This is the
    fixture that closes that gap -- and the *line protocol* half was an
    outright parse failure until the `broken` capture found it.
    """

    raw = (
        FIXTURE_DIR / "cisco_xr" / "PE2" / "broken" / f"show-interfaces-{interface}.txt"
    ).read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "interface", raw)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["admin_state"] == "admin-down"
    assert parsed["meta"]["line_state"] == "admin-down"


def test_an_isolated_device_reports_no_route_to_the_route_reflector():
    """PE2 loses its route to RR1's loopback entirely -- the `route_present`
    check's broken branch, now backed by a real capture instead of TEST-NET-1."""

    raw = (
        FIXTURE_DIR / "cisco_xr" / "PE2" / "broken" / "show-route-10-255-0-31-32.txt"
    ).read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "route", raw)

    assert status is tp.PARSE_OK
    assert parsed["meta"]["found"] is False
    assert parsed["records"] == []


# --------------------------------------------------------------------------- #
# The BGP socket line — ROUND-8 §6.2 precondition 1.
#
# Round 8's sampler used a FIRST-MATCH search over this line, which hit the
# `io` field and reported read=not-armed on every Established session. That
# single defect voided §2a.2 and cost a whole lab window. The shipped parser is
# anchored and positional and was never wrong -- but nothing tested it against
# the one line shape that discriminates, so "the shipped one is correct" rested
# on reading it rather than on running it.
#
# The committed corpus cannot close this: read == write in all 16 fixtures
# (14 armed/armed, 2 not-armed/not-armed), so a parser that SWAPPED the two
# would pass the entire suite. That is §0.12's shape -- a corpus uniform in the
# dimension the test discriminates on -- and it needs synthetic input, not a
# new capture.
# --------------------------------------------------------------------------- #

_SOCKET_NEIGHBOR = """BGP neighbor is 10.255.0.31
 Remote AS 65000, local AS 65000, internal link
 BGP state = {state}
 Socket {io} for io, {read} for read, {write} for write
"""


def _socket_meta(*, io, read, write, state="Established"):
    parsed = tp.parse_xr_bgp_neighbor(
        _SOCKET_NEIGHBOR.format(io=io, read=read, write=write, state=state)
    )
    return parsed["meta"]


def test_the_socket_line_is_read_positionally_not_by_first_match():
    """The exact line an Established session prints, and the exact defect.

    `Socket not armed for io, armed for read, armed for write` is what a
    HEALTHY session emits -- io is legitimately not armed. A first-match
    search for "armed" or "not armed" lands on io and concludes the session
    has no read socket, which is precisely backwards.
    """

    meta = _socket_meta(io="not armed", read="armed", write="armed")

    assert meta["socket_armed_read"] is True, (
        "read was misread from the io field -- this is the round-8 sampler defect"
    )
    assert meta["socket_armed_write"] is True


def test_read_and_write_are_not_interchangeable():
    """A parser that swapped read and write would pass every committed fixture.

    All 16 corpus fixtures have read == write, so only synthetic input can tell
    the two apart. Nothing reads `socket_armed_write` today; this is latent, and
    the point is that it stays impossible rather than merely unused.
    """

    mixed = _socket_meta(io="not armed", read="armed", write="not armed")
    assert mixed["socket_armed_read"] is True
    assert mixed["socket_armed_write"] is False

    swapped = _socket_meta(io="not armed", read="not armed", write="armed")
    assert swapped["socket_armed_read"] is False
    assert swapped["socket_armed_write"] is True


def test_a_fully_unarmed_socket_reads_as_unarmed():
    """The Idle case -- 2 of 2 Idle fixtures print this shape."""

    meta = _socket_meta(io="not armed", read="not armed", write="not armed", state="Idle")

    assert meta["socket_armed_read"] is False
    assert meta["socket_armed_write"] is False


# --------------------------------------------------------------------------- #
# cisco_xr: sr_policy_detail (B-515). No committed fixture exists for this
# template (tests/fixtures/ is out of scope for this task) -- these are the
# real command outputs captured LIVE 2026-08-19 against PE1, the only device
# on this fabric with any SR-TE policy configured (verified: swept `sr` on
# all nine devices, only PE1 has any). Reproduced here verbatim, including
# the real trailing spaces IOS-XR prints on "Maximum SID Depth: 10 " and
# "Path Accumulated Metric: 0 " -- not a typo.
# --------------------------------------------------------------------------- #

_SR_POLICY_DETAIL_UP = """
Wed Aug 19 15:03:55.462 UTC

SR-TE policy database
---------------------

Color: 10, End-point: 10.255.0.13
  Name: srte_c_10_ep_10.255.0.13
  Status:
    Admin: up  Operational: up for 1d10h (since Aug 18 05:00:08.295)
  Candidate-paths:
    Preference: 100 (configuration) (active)
      Name: srte_c_10_ep_10.255.0.13
      Requested BSID: dynamic
      Constraints:
        Protection Type: protected-preferred
        Maximum SID Depth: 10
      Explicit: segment-list SL-VIA-P3 (valid)
        Weight: 1, Metric Type: TE
          SID[0]: 16003
          SID[1]: 16013
  LSPs:
    LSP[0]:
      LSP-ID: 25 policy ID: 1 (active)
      Local label: 24030
      State: Programmed
      Binding SID: 24031
  Attributes:
    Binding SID: 24031
    Forward Class: Not Configured
    Steering labeled-services disabled: no
    Steering BGP disabled: no
    IPv6 caps enable: yes
    Invalidation drop enabled: no
    Max Install Standby Candidate Paths: 0
"""

_SR_POLICY_DETAIL_DOWN = """
Wed Aug 19 15:00:21.898 UTC

SR-TE policy database
---------------------

Color: 20, End-point: 10.255.0.13
  Name: srte_c_20_ep_10.255.0.13
  Status:
    Admin: up  Operational: down for 5d21h (since Aug 13 17:17:47.792)
  Candidate-paths:
    Preference: 100 (configuration) (inactive)
      Name: srte_c_20_ep_10.255.0.13
      Requested BSID: dynamic
      Constraints:
        Protection Type: protected-preferred
        Maximum SID Depth: 10
      Dynamic (inactive)
      Last error: No path found
        Metric Type: LATENCY,   Path Accumulated Metric: 0
  Attributes:
    Forward Class: 0
    Steering labeled-services disabled: no
    Steering BGP disabled: no
    IPv6 caps enable: no
    Invalidation drop enabled: no
    Max Install Standby Candidate Paths: 0
"""

# IOS-XR's own "nothing here" shape for a color/endpoint with no matching
# policy: no error text at all, just its usual timestamp banner.
_SR_POLICY_DETAIL_NOT_FOUND = """
Wed Aug 19 15:00:31.352 UTC
"""


def test_sr_policy_detail_up_exposes_the_resolved_segment_list_and_sid_stack():
    """The point of B-515: the SID stack a down-policy diagnosis needs to
    name, and does not get from check_lab_sr_policies' policy-level fields."""

    result = tp.parse_xr_sr_policy_detail(_SR_POLICY_DETAIL_UP)
    meta = result["meta"]

    assert meta["found"] is True
    assert meta["policy"] == "10:10.255.0.13"
    assert meta["admin_state"] == "up"
    assert meta["operational_state"] == "up"
    assert meta["candidate_path_type"] == "explicit"
    assert meta["candidate_path_active"] is True
    assert meta["segment_list_name"] == "SL-VIA-P3"
    assert meta["segment_list_valid"] is True
    assert meta["last_error"] is None
    assert meta["binding_sid"] == "24031"
    assert meta["lsp_state"] == "Programmed"
    assert result["records"] == [
        {"index": "0", "sid": "16003"},
        {"index": "1", "sid": "16013"},
    ]
    assert meta["unaccounted_lines"] == []
    assert meta["unparsed_rows"] == 0


def test_sr_policy_detail_down_exposes_the_last_error_and_no_sids():
    """MCP §14b's exact case: a model that could say "no candidate path
    resolves" and not name why -- `last_error` is why."""

    result = tp.parse_xr_sr_policy_detail(_SR_POLICY_DETAIL_DOWN)
    meta = result["meta"]

    assert meta["found"] is True
    assert meta["policy"] == "20:10.255.0.13"
    assert meta["operational_state"] == "down"
    assert meta["candidate_path_type"] == "dynamic"
    assert meta["candidate_path_active"] is False
    assert meta["segment_list_name"] is None
    assert meta["last_error"] == "No path found"
    assert meta["binding_sid"] is None
    assert meta["lsp_state"] is None
    assert result["records"] == []
    assert meta["unaccounted_lines"] == []
    assert meta["unparsed_rows"] == 0


def test_sr_policy_detail_absence_is_found_false_not_an_error():
    """IOS-XR answers a non-matching color/endpoint with nothing, not an
    error string -- `found: False` must be inferred from that absence,
    the same "device's own honest nothing" shape parse_xr_bgp_neighbor's
    "Neighbor not found"/parse_xr_route's "Network not in table" already
    handle, just with no line to match against here."""

    result = tp.parse_xr_sr_policy_detail(_SR_POLICY_DETAIL_NOT_FOUND)

    assert result["meta"]["found"] is False
    assert result["meta"]["policy"] is None
    assert result["records"] == []
    assert result["meta"]["unaccounted_lines"] == []


def test_sr_policy_detail_last_error_is_declared_as_free_text():
    """The MCP-boundary half of B-515: `last_error` must be quoted crossing
    to a model, not left bare -- pinned here on the declaration
    `mcp_server.boundary`/`model_egress` both read, not on the boundary
    module itself (that is `tests/test_mcp_boundary.py`'s job)."""

    from agent_nettools.model_egress import FREE_TEXT_FIELDS

    assert ("sr_policy_detail", "last_error") in FREE_TEXT_FIELDS


def test_sr_policy_detail_raises_for_genuinely_unrecognised_output():
    with pytest.raises(parsers.ParseError):
        tp.parse_xr_sr_policy_detail("garbage the device never actually prints")
