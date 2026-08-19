"""Ownership / escalation routing (B-484).

Two guarantees this file exists to pin, both stated explicitly in the
module's own docstring:

1. **Never silence.** An unmatched finding always resolves to at least one
   owner -- the default -- never to an empty destination list.
2. **Declarative, inspectable, no acknowledgement fabricated.** Escalation is
   a pure function of an explicit ``age_seconds`` the caller supplies; there
   is no hidden clock, no state, and nothing here claims a human did or did
   not see anything.
"""

from __future__ import annotations

import pytest

from agent_nettools import ownership as O


def _table(**overrides) -> O.OwnershipTable:
    owners = {
        "net-eng": O.Owner(name="net-eng", channel="telegram:111"),
        "noc": O.Owner(name="noc", channel="telegram:222",
                       escalate_to="net-eng", escalate_after_seconds=900.0),
    }
    rules = (
        O.OwnershipRule(name="pe-devices", owner="noc", role="edge"),
        O.OwnershipRule(name="rr1-specific", owner="net-eng", device="RR1"),
    )
    return O.OwnershipTable(rules=rules, owners=owners, default_owner="noc")


# --------------------------------------------------------------------------- #
# default_table -- zero configuration reproduces today's single channel
# --------------------------------------------------------------------------- #


def test_default_table_has_one_owner_and_no_rules():
    table = O.default_table()
    assert table.rules == ()
    assert set(table.owners) == {O.DEFAULT_OWNER_NAME}
    assert table.owners[O.DEFAULT_OWNER_NAME].channel == O.DEFAULT_CHANNEL


def test_resolving_against_the_default_table_always_returns_the_default_owner():
    table = O.default_table()
    result = O.resolve_ownership(table, device="PE2", finding="bgp_session_down")
    assert [o.name for o in result.owners] == [O.DEFAULT_OWNER_NAME]
    assert result.matched_rule is None


# --------------------------------------------------------------------------- #
# resolve_ownership -- matching, first rule wins
# --------------------------------------------------------------------------- #


def test_a_matching_rule_routes_to_its_named_owner():
    table = _table()
    result = O.resolve_ownership(table, device="PE2", role="edge", finding="bgp_session_down")
    assert result.matched_rule == "pe-devices"
    assert [o.name for o in result.owners] == ["noc"]


def test_first_matching_rule_wins_declaration_order():
    table = O.OwnershipTable(
        rules=(
            O.OwnershipRule(name="specific", owner="net-eng", device="RR1"),
            O.OwnershipRule(name="general", owner="noc", role="edge"),
        ),
        owners={
            "net-eng": O.Owner(name="net-eng", channel="telegram:111"),
            "noc": O.Owner(name="noc", channel="telegram:222"),
        },
        default_owner="noc",
    )
    result = O.resolve_ownership(table, device="RR1", role="edge", finding="x")
    assert result.matched_rule == "specific"
    assert [o.name for o in result.owners] == ["net-eng"]


# --------------------------------------------------------------------------- #
# "Never silence" -- the central guarantee
# --------------------------------------------------------------------------- #


def test_an_unmatched_finding_falls_back_to_the_default_owner_never_to_nobody():
    table = _table()
    result = O.resolve_ownership(table, device="UNKNOWN-DEVICE", role="core", finding="whatever")
    assert result.matched_rule is None
    assert len(result.owners) >= 1
    assert [o.name for o in result.owners] == ["noc"]
    assert "default" in result.reason.lower() or "noc" in result.reason


def test_a_table_with_no_default_owner_declared_refuses_to_construct():
    with pytest.raises(O.OwnershipError, match="default_owner"):
        O.OwnershipTable(
            rules=(),
            owners={"noc": O.Owner(name="noc", channel="telegram:1")},
            default_owner="nonexistent",
        )


def test_a_rule_naming_an_undeclared_owner_refuses_to_construct():
    with pytest.raises(O.OwnershipError, match="not declared"):
        O.OwnershipTable(
            rules=(O.OwnershipRule(name="r1", owner="ghost"),),
            owners={"noc": O.Owner(name="noc", channel="telegram:1")},
            default_owner="noc",
        )


def test_an_escalation_target_naming_an_undeclared_owner_refuses_to_construct():
    with pytest.raises(O.OwnershipError, match="not a declared owner"):
        O.OwnershipTable(
            rules=(),
            owners={
                "noc": O.Owner(name="noc", channel="telegram:1",
                               escalate_to="ghost", escalate_after_seconds=60.0),
            },
            default_owner="noc",
        )


# --------------------------------------------------------------------------- #
# Escalation -- age-based, never acknowledgement-based
# --------------------------------------------------------------------------- #


def test_escalation_fires_once_age_meets_the_threshold():
    table = _table()
    result = O.resolve_ownership(table, device="PE2", role="edge",
                                 finding="bgp_session_down", age_seconds=900.0)
    assert result.escalated is True
    assert [o.name for o in result.owners] == ["noc", "net-eng"]


def test_escalation_does_not_fire_below_the_threshold():
    table = _table()
    result = O.resolve_ownership(table, device="PE2", role="edge",
                                 finding="bgp_session_down", age_seconds=100.0)
    assert result.escalated is False
    assert [o.name for o in result.owners] == ["noc"]


def test_escalation_never_fires_with_no_age_supplied():
    """No acknowledgement mechanism exists (notifier.py has no inbound
    surface); a caller with nothing to measure age against gets no
    escalation, ever -- not a guess."""

    table = _table()
    result = O.resolve_ownership(table, device="PE2", role="edge", finding="bgp_session_down")
    assert result.escalated is False
    assert [o.name for o in result.owners] == ["noc"]


def test_an_owner_with_a_threshold_but_no_target_is_refused_at_construction():
    with pytest.raises(O.OwnershipError, match="together"):
        O.Owner(name="noc", channel="telegram:1", escalate_after_seconds=60.0)


def test_an_owner_with_a_target_but_no_threshold_is_refused_at_construction():
    with pytest.raises(O.OwnershipError, match="together"):
        O.Owner(name="noc", channel="telegram:1", escalate_to="net-eng")


def test_a_non_positive_escalation_threshold_is_refused():
    with pytest.raises(O.OwnershipError, match="> 0"):
        O.Owner(name="noc", channel="telegram:1",
                escalate_to="net-eng", escalate_after_seconds=0.0)


# --------------------------------------------------------------------------- #
# parse_ownership_table / load_ownership_table
# --------------------------------------------------------------------------- #


def test_parse_ownership_table_requires_a_default_owner():
    with pytest.raises(O.OwnershipError, match="default_owner"):
        O.parse_ownership_table({
            "owners": [{"name": "noc", "channel": "telegram:1"}],
        })


def test_parse_ownership_table_requires_at_least_one_owner():
    with pytest.raises(O.OwnershipError, match="owner"):
        O.parse_ownership_table({"owners": [], "default_owner": "noc"})


def test_a_real_ownership_file_loads(tmp_path):
    path = tmp_path / "ownership.yaml"
    path.write_text(
        "owners:\n"
        "  - name: noc\n"
        "    channel: telegram:222\n"
        "  - name: net-eng\n"
        "    channel: telegram:111\n"
        "rules:\n"
        "  - name: edge-devices\n"
        "    role: edge\n"
        "    owner: noc\n"
        "default_owner: noc\n"
    )
    table = O.load_ownership_table(path)
    result = O.resolve_ownership(table, device="PE2", role="edge", finding="x")
    assert result.matched_rule == "edge-devices"
    assert [o.name for o in result.owners] == ["noc"]


def test_no_path_configured_returns_the_default_table():
    table = O.load_ownership_table(None)
    assert table.default_owner == O.DEFAULT_OWNER_NAME


def test_a_nonexistent_path_returns_the_default_table(tmp_path):
    table = O.load_ownership_table(tmp_path / "missing.yaml")
    assert table.default_owner == O.DEFAULT_OWNER_NAME


def test_a_malformed_ownership_file_raises_rather_than_loading_as_default(tmp_path):
    path = tmp_path / "ownership.yaml"
    path.write_text("owners: []\ndefault_owner: noc\n")
    with pytest.raises(O.OwnershipError):
        O.load_ownership_table(path)
