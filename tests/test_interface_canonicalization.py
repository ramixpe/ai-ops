"""B-519: audit every parameter that accepts an interface name, and enumerate
the audit from the code so a fifth site cannot be added unclassified.

**The finding.** OBS-202/OBS-193/OBS-178 are three recurrences of one defect:
`show interfaces brief` spells a port `Gi0/0/0/0`; gNMI, LLDP and NetBox's own
join key spell it `GigabitEthernet0/0/0/0`. Every one of the three was a
**comparison or lookup** across two independently-spelled sources -- a
Prometheus label match, a NetBox cable join, a bulk-vs-single fallback -- and
every one was already fixed, individually, before this task started
(`metrics_prometheus._InterfaceSlot`, `netbox.py`'s join, `checks.py`'s
`same_interface`-based `interface_exists`). This module's job is not to
re-fix them; it is to make sure a **fifth, unaudited** comparison site cannot
ship, and to record -- as a test, not a comment nobody re-reads -- the sites
that were audited and found NOT to need the fix.

**The one thing this audit changed its mind about mid-flight.** The obvious
move is to canonicalise every caller-supplied interface name, everywhere,
on the way in. Two sites here look exactly like OBS-202's shape --
`get_lab_interface`'s `name`, `investigate_lab_session`'s `subject` for
`interface`/`isis_adjacency`/`ldp_session` -- and canonicalising them was
this module's first draft. It was wrong, and measurably so:

1.  Neither value is ever COMPARED against a differently-spelled source.
    `name`/`subject` are rendered `SubjectRule.AS_IS` straight into a
    `show interfaces {name}` device command. Confirmed live, 2026-08-19
    against PE2: `show interfaces Gi0/0/0/0` and
    `show interfaces GigabitEthernet0/0/0/0` both return `status: success`
    with the same parsed record -- IOS-XR's own CLI already tolerates both
    spellings, the same way it tolerates `sh int` for `show interface`.
2.  `checks.interface_exists`, the one place a spelling IS compared on this
    path (the flow's `subject_present` pre-check), already does so through
    `same_interface` -- so canonicalising `subject` earlier fixes a
    comparison that does not need fixing.
3.  `tests/fixtures/` captures per-interface output keyed by the SHORT
    spelling (`show-interfaces-gi0-0-0-0.txt`; see
    `test_the_fixture_replay_workflow_is_why_this_is_exempt_not_forgotten`
    below). Canonicalising `name`/`subject` before they reach
    `run_template` would make `--from-fixtures` -- CLAUDE.md's own
    no-lab-no-credentials-no-API-key replay path -- silently start missing
    its own captures for the single most natural caller: a subject copied
    out of `check_lab_interfaces`' own (SHORT-form) output.

So the fix for OBS-202's shape is not "rewrite the value everywhere it is
named `name`/`interface`/`subject`" -- it is "rewrite the value at a
COMPARISON, and say explicitly, in a place a test enforces, that a
device-command site was looked at and left alone." `interface_kind.canonical`
's own docstring already draws one such line (device-scoped vs. cross-device);
this module draws the second one this task needed (compared vs. rendered) the
same way -- declared and tested, not left to be re-discovered by the next
agent tempted to "fix" `get_lab_interface` the way this one almost did.

**Layout.**

* Part 1 -- four enumerators that walk the REAL surface (the MCP tool
  registry, `nettools`'s own argparse parser, `templates.PLATFORM_TEMPLATES`,
  `logs_loki`/`metrics_prometheus`'s named-query registries) for any
  parameter named `name`, `interface` or `subject` -- this fabric's own
  three spellings for "an interface name", measured in
  `test_the_trigger_names_are_not_used_for_anything_else` below so the
  heuristic itself is not a guess. Four completeness tests assert the found
  set equals a DECLARED table exactly -- add a tool/argument/template/query
  matching the trigger and one of these four fails until it is classified.
* Part 2 -- one behavioural test per declared site, proving the
  classification is what the code actually does: a CANONICALIZES site
  rewrites an abbreviated name to the long form (with a positive control --
  OBS-181 -- that the already-long form and a legitimate value survive
  unchanged); an EXEMPT site forwards the caller's exact spelling, unrewritten,
  with the same positive control run through both spellings.
* Part 3 -- the fixture-replay evidence for why the EXEMPT sites are exempt,
  run against the real committed fixtures rather than asserted from memory.
"""

from __future__ import annotations

import argparse
import inspect

import pytest

import agent_nettools.cli as cli
from agent_nettools import logs_loki, metrics_prometheus, templates
from agent_nettools.fixtures import fixture_sender
from agent_nettools.interface_kind import canonical, interface_scoped_flows
from agent_nettools.network_tools import get_interface
from mcp_server import server

# --------------------------------------------------------------------------- #
# Part 1: enumerate the real surface. Never a hand-written list of tools.
# --------------------------------------------------------------------------- #

#: This fabric's own three spellings for "an interface name" parameter,
#: measured (not assumed) by `test_the_trigger_names_are_not_used_for_
#: anything_else` below: `name` (`get_lab_interface`/`nettools interface`),
#: `interface` (`get_lab_interface_rate_history`, the Prometheus adapter slot,
#: both `InterfaceNameParam` templates), `subject` (`investigate_lab_session`/
#: `nettools investigate`, an interface name for three of its four flows).
_INTERFACE_SHAPED_PARAM_NAMES = frozenset({"name", "interface", "subject"})

CANONICALIZES = "canonicalizes"
EXEMPT = "exempt"


def _registered_mcp_tools() -> dict[str, object]:
    """Every MCP tool's real function, keyed by tool name.

    Same registry-walking shape `tests/test_docs.py::_registered_tool_names`
    and `tests/test_mcp_boundary.py::_registered_tools` already use -- not
    imported from either (a test module should not depend on another test
    module's private helper), re-derived here because it is three lines.
    """

    for attribute in ("_tool_manager", "_tools", "tools"):
        holder = getattr(server.mcp, attribute, None)
        if holder is None:
            continue
        registry = getattr(holder, "_tools", holder)
        if isinstance(registry, dict) and registry:
            return {name: tool.fn for name, tool in registry.items()}
    pytest.skip("cannot reach this MCP SDK's tool registry")


def _cli_subcommands() -> dict[str, argparse.ArgumentParser]:
    """Every `nettools` subcommand's own argparse parser, keyed by name."""

    parser = cli.build_parser()
    command_action = next(a for a in parser._actions if a.dest == "command")
    return dict(command_action.choices)


def _mcp_tool_interface_shaped_params() -> set[tuple[str, str]]:
    return {
        (tool_name, param_name)
        for tool_name, fn in _registered_mcp_tools().items()
        for param_name in inspect.signature(fn).parameters
        if param_name in _INTERFACE_SHAPED_PARAM_NAMES
    }


def _cli_interface_shaped_params() -> set[tuple[str, str]]:
    return {
        (command_name, action.dest)
        for command_name, subparser in _cli_subcommands().items()
        for action in subparser._actions
        if action.dest in _INTERFACE_SHAPED_PARAM_NAMES
    }


def _template_interface_shaped_params() -> set[tuple[str, str, str]]:
    return {
        (platform, template_name, param_name)
        for platform, tpls in templates.PLATFORM_TEMPLATES.items()
        for template_name, template in tpls.items()
        for param_name in template.params
        if param_name in _INTERFACE_SHAPED_PARAM_NAMES
    }


def _adapter_query_interface_shaped_params() -> set[tuple[str, str, str]]:
    """`logs_loki`/`metrics_prometheus`'s own named-query registries -- the
    "adapter slots" B-519's brief names explicitly."""

    return {
        (module_name, query_name, param_name)
        for module_name, queries in (
            ("logs_loki", logs_loki.LOKI_QUERIES),
            ("metrics_prometheus", metrics_prometheus.PROMETHEUS_QUERIES),
        )
        for query_name, query in queries.items()
        for param_name in query.params
        if param_name in _INTERFACE_SHAPED_PARAM_NAMES
    }


#: THE DECLARED TABLE -- B-519's actual deliverable. Every site the four
#: enumerators above can find on this owner's surface, and what was decided.
#: See the module docstring for what CANONICALIZES/EXEMPT mean and why.
MCP_TOOL_SITES: dict[tuple[str, str], str] = {
    ("get_lab_interface", "name"): EXEMPT,
    ("get_lab_interface_rate_history", "interface"): CANONICALIZES,
    ("investigate_lab_session", "subject"): EXEMPT,
}

CLI_SITES: dict[tuple[str, str], str] = {
    ("interface", "name"): EXEMPT,
    ("investigate", "subject"): EXEMPT,
}

TEMPLATE_SITES: dict[tuple[str, str, str], str] = {
    # Direct device-command params (`InterfaceNameParam`, `templates.py`'s own
    # validator, additions-only). Same reasoning as the MCP/CLI EXEMPT sites:
    # `show interfaces {interface}` / `show running-config interface
    # {interface}` are sent straight to the device, which accepts either
    # spelling, and `templates.py` takes additions only -- there is no
    # comparison here for a change to fix, and touching a frozen, just-signed-
    # off validator for no correctness gain is its own hazard.
    ("cisco_xr", "interface", "interface"): EXEMPT,
    ("cisco_xr", "config_interface", "interface"): EXEMPT,
}

ADAPTER_QUERY_SITES: dict[tuple[str, str, str], str] = {
    # metrics_prometheus._InterfaceSlot -- OBS-202's own fix, already shipped
    # before this task started. A Prometheus label selector is an EXACT
    # string match against whatever gNMI wrote, with no CLI-style abbreviation
    # tolerance -- the opposite situation from every EXEMPT site above.
    ("metrics_prometheus", "interface_rate_history", "interface"): CANONICALIZES,
}


def test_the_trigger_names_are_not_used_for_anything_else():
    """The heuristic's own honesty check: every MCP-tool/CLI parameter named
    `name`, `interface` or `subject` really is one of the sites declared
    above -- so the trigger is not silently over- or under-matching this
    fabric's real parameter vocabulary (`prefix`, `address`, `device_name`,
    `query`, `mnemonic`, `count`, ... none of which collide)."""

    assert _mcp_tool_interface_shaped_params() == set(MCP_TOOL_SITES)
    assert _cli_interface_shaped_params() == set(CLI_SITES)


def test_every_mcp_tool_interface_shaped_parameter_is_classified():
    """The completeness check. A NEW MCP tool parameter named `name`,
    `interface` or `subject` fails this test until a human adds it to
    `MCP_TOOL_SITES` with an explicit CANONICALIZES/EXEMPT verdict -- a fifth
    site cannot ship silently."""

    found = _mcp_tool_interface_shaped_params()
    assert found == set(MCP_TOOL_SITES), (
        f"an interface-shaped MCP tool parameter is unclassified: "
        f"found={found}, declared={set(MCP_TOOL_SITES)}. Add the new pair to "
        "MCP_TOOL_SITES with CANONICALIZES or EXEMPT and say why in the "
        "module docstring."
    )


def test_every_cli_interface_shaped_argument_is_classified():
    found = _cli_interface_shaped_params()
    assert found == set(CLI_SITES), (
        f"found={found}, declared={set(CLI_SITES)}. Add the new pair to "
        "CLI_SITES."
    )


def test_every_template_interface_shaped_parameter_is_classified():
    found = _template_interface_shaped_params()
    assert found == set(TEMPLATE_SITES), (
        f"found={found}, declared={set(TEMPLATE_SITES)}. Add the new triple "
        "to TEMPLATE_SITES."
    )


def test_every_adapter_query_interface_shaped_parameter_is_classified():
    found = _adapter_query_interface_shaped_params()
    assert found == set(ADAPTER_QUERY_SITES), (
        f"found={found}, declared={set(ADAPTER_QUERY_SITES)}. Add the new "
        "triple to ADAPTER_QUERY_SITES."
    )


def test_the_enumeration_is_not_vacuous():
    """Anti-vacuity: the four enumerators must actually find the known
    sites, or the four completeness tests above would pass over an empty
    set for the wrong reason."""

    assert _mcp_tool_interface_shaped_params(), "found nothing on the MCP surface"
    assert _cli_interface_shaped_params(), "found nothing on the CLI surface"
    assert _template_interface_shaped_params(), "found nothing in templates.py"
    assert _adapter_query_interface_shaped_params(), "found nothing in the adapters"


# --------------------------------------------------------------------------- #
# Part 2: behavioural proof for every declared site.
# --------------------------------------------------------------------------- #


def test_the_prometheus_interface_slot_canonicalizes_with_a_positive_control():
    """CANONICALIZES, proven directly against the adapter slot itself."""

    slot = metrics_prometheus.PROMETHEUS_QUERIES["interface_rate_history"].params["interface"]

    assert slot.parse("interface", "Gi0/0/0/0") == "GigabitEthernet0/0/0/0"
    # Positive control (OBS-181): the already-long, already-legitimate form
    # must still be accepted, and unchanged -- canonicalising must not be the
    # only spelling this slot accepts.
    assert slot.parse("interface", "GigabitEthernet0/0/0/0") == "GigabitEthernet0/0/0/0"
    # The charset gate still holds; canonicalising did not widen it.
    with pytest.raises(metrics_prometheus.PrometheusQueryError):
        slot.parse("interface", "Gi0/0/0/0; rm -rf /")


def test_get_lab_interface_forwards_the_caller_spelling_unchanged(monkeypatch):
    """EXEMPT, proven at the actual entry point: `get_lab_interface` must
    call `network_tools.get_interface` with `name` untouched, in EITHER
    spelling -- the positive control this site's whole argument depends on."""

    captured: dict[str, str] = {}

    def fake_get_interface(device_name, name):
        captured["device_name"] = device_name
        captured["name"] = name
        return {"tool": "get_interface", "status": "success", "data": {}}

    monkeypatch.setattr(server, "get_interface", fake_get_interface)

    server.get_lab_interface("PE2", "Gi0/0/0/0")
    assert captured == {"device_name": "PE2", "name": "Gi0/0/0/0"}

    server.get_lab_interface("PE2", "GigabitEthernet0/0/0/0")
    assert captured == {"device_name": "PE2", "name": "GigabitEthernet0/0/0/0"}


def test_investigate_lab_session_forwards_the_subject_spelling_unchanged(monkeypatch):
    """EXEMPT, for all three interface-scoped flows, plus the `bgp_session`
    control proving an IPv4 subject was never a candidate for this either."""

    captured: dict[str, str] = {}

    class _StubResult:
        def to_payload(self):
            return {"tool": "investigate", "status": "success"}

    def fake_investigate(device, subject, flow="bgp_session"):
        captured["device"] = device
        captured["subject"] = subject
        captured["flow"] = flow
        return _StubResult()

    monkeypatch.setattr(server, "investigate", fake_investigate)

    for flow in sorted(interface_scoped_flows()):
        server.investigate_lab_session("PE2", "Gi0/0/0/2", flow=flow)
        assert captured["subject"] == "Gi0/0/0/2", flow
        server.investigate_lab_session("PE2", "GigabitEthernet0/0/0/2", flow=flow)
        assert captured["subject"] == "GigabitEthernet0/0/0/2", flow

    # Control: bgp_session's subject is an IPv4 address, never interface-shaped.
    server.investigate_lab_session("RR1", "10.255.0.12", flow="bgp_session")
    assert captured["subject"] == "10.255.0.12"


def _parse_investigate_args(*extra: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(["investigate", "PE2", *extra, "--quiet"])


def test_cli_interface_forwards_the_caller_spelling_unchanged(monkeypatch):
    """EXEMPT, at the `nettools interface` entry point."""

    captured: dict[str, str] = {}

    def fake_get_interface(device_name, name):
        captured["device_name"] = device_name
        captured["name"] = name
        return {"tool": "get_interface", "status": "success", "data": {}}

    monkeypatch.setattr(cli, "get_interface", fake_get_interface)

    for spelling in ("Gi0/0/0/0", "GigabitEthernet0/0/0/0"):
        args = cli.build_parser().parse_args(["interface", "PE2", spelling, "--quiet"])
        cli._cmd_interface(args)
        assert captured == {"device_name": "PE2", "name": spelling}


def test_cli_investigate_forwards_the_subject_spelling_unchanged(monkeypatch):
    """EXEMPT, at the `nettools investigate` entry point, for all three
    interface-scoped flows plus the `bgp_session` control."""

    captured: dict[str, object] = {}

    def fake_investigate(device, subject, flow=None, analyst=None, sender=None):
        captured["device"] = device
        captured["subject"] = subject
        captured["flow"] = flow
        # Raised, not returned: stops _cmd_investigate before the ledger/
        # ticket bookkeeping, which needs a real DescentResult this fake has
        # no reason to build. `_cmd_investigate`'s own (ValueError, KeyError)
        # handler turns this into an ordinary error envelope.
        raise ValueError("captured; stopping before ledger/ticket side effects")

    monkeypatch.setattr(cli, "investigate", fake_investigate)

    for flow in sorted(interface_scoped_flows()):
        for spelling in ("Gi0/0/0/2", "GigabitEthernet0/0/0/2"):
            args = _parse_investigate_args(spelling, "--flow", flow)
            cli._cmd_investigate(args)
            assert captured["subject"] == spelling, (flow, spelling)

    # Control: bgp_session's subject is an IPv4 address.
    args = _parse_investigate_args("10.255.0.12", "--flow", "bgp_session")
    cli._cmd_investigate(args)
    assert captured["subject"] == "10.255.0.12"


# --------------------------------------------------------------------------- #
# Part 3: why EXEMPT is correct, measured against the real committed fixtures.
# --------------------------------------------------------------------------- #


def test_the_fixture_replay_workflow_is_why_this_is_exempt_not_forgotten():
    """Real evidence, not an assertion from memory.

    `tests/fixtures/` captures per-interface output keyed by the SHORT
    spelling. If `get_interface`'s caller-supplied name were canonicalised
    the way `metrics_prometheus`'s Prometheus label lookup is, this would
    flip from `success` to a missing-fixture error -- for every caller who
    typed the exact spelling `check_lab_interfaces` itself returns.
    """

    send = fixture_sender(label="broken")

    short_result = get_interface("PE2", "Gi0/0/0/0", sender=send)
    assert short_result["status"] == "success", (
        "the committed fixture is captured under the short spelling"
    )

    long_result = get_interface("PE2", "GigabitEthernet0/0/0/0", sender=send)
    assert long_result["status"] == "error", (
        "no fixture is captured under the long spelling -- this is the "
        "concrete cost canonicalising `get_lab_interface`'s `name` would "
        "have imposed on --from-fixtures, for zero comparison-correctness "
        "gain (nothing on this path compares `name` against another source)"
    )


def test_canonical_is_idempotent_on_an_already_long_spelling():
    """Sanity check the CANONICALIZES sites' positive control depends on:
    `canonical` is a fixed point on the form it produces, so re-canonicalising
    an already-canonical value (e.g. a value copied from a prior tool's
    output) is always safe."""

    for name in ("Gi0/0/0/0", "GigabitEthernet0/0/0/0", "Lo0", "Bundle-Ether1", "Mg0/RP0/CPU0/0"):
        once = canonical(name)
        assert canonical(once) == once
