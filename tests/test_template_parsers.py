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
    actually observed on this fabric are the small, fixed set discovery-loki.md
    found (T-004): 3, 6, and 7. This pins that guarantee against all 1800
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
