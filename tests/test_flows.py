"""Flow registry contract and safety tests (T-022, T-023)."""

from __future__ import annotations

import pytest

from agent_nettools import checks, flows
from agent_nettools.platforms import PLATFORM_TEMPLATES, all_intents

LAB_PLATFORM = "cisco_xr"


# --------------------------------------------------------------------------- #
# The safety test T-022 requires
# --------------------------------------------------------------------------- #


def test_every_collect_step_names_a_real_intent_or_template():
    """Mirrors `test_check_tool_intents_exist_in_the_platform_table`.

    A rung whose collect step names something the platform table does not have
    would fail at collection time, in the middle of a descent, against a real
    device -- and the failure would look like a device problem rather than a
    typo in a ladder. Catching it here makes it a static property of the
    registry instead.
    """

    intents = set(all_intents())
    templates = set(PLATFORM_TEMPLATES[LAB_PLATFORM])

    for object_type, flow in flows.FLOWS.items():
        for rung in flow.descent:
            for step in rung.collect:
                if step.is_template:
                    assert step.name in templates, (
                        f"{object_type}/{rung.name}: collect step {step.name!r} is "
                        f"not a template on {LAB_PLATFORM}"
                    )
                else:
                    assert step.name in intents, (
                        f"{object_type}/{rung.name}: collect step {step.name!r} is "
                        f"not a known intent"
                    )


def test_every_template_collect_step_names_a_real_parameter():
    """A template step must name the parameter the subject fills, and that
    parameter must exist on the template -- otherwise `render_command` refuses
    at run time for a reason that reads like bad input rather than a bad ladder.
    """

    for object_type, flow in flows.FLOWS.items():
        for rung in flow.descent:
            for step in rung.collect:
                if not step.is_template:
                    assert step.parameter is None, (
                        f"{object_type}/{rung.name}: intent {step.name!r} takes no parameter"
                    )
                    continue
                template = PLATFORM_TEMPLATES[LAB_PLATFORM][step.name]
                assert step.parameter in template.params, (
                    f"{object_type}/{rung.name}: {step.name!r} has no parameter "
                    f"{step.parameter!r}; it takes {sorted(template.params)}"
                )


# --------------------------------------------------------------------------- #
# Registry shape
# --------------------------------------------------------------------------- #


def test_seven_object_types_are_declared():
    assert len(flows.OBJECT_TYPES) == 7
    assert set(flows.FLOWS) <= set(flows.OBJECT_TYPES)


@pytest.mark.parametrize(
    "object_type",
    [t for t in flows.OBJECT_TYPES if t not in ("bgp_session", "interface")],
)
def test_an_unimplemented_flow_raises_rather_than_returning_none(object_type):
    """`None` would let a caller read "no flow" as "nothing wrong"."""

    with pytest.raises(NotImplementedError, match=object_type):
        flows.flow_for(object_type)


def test_an_unknown_object_type_is_a_key_error_not_a_not_implemented():
    """A typo and a deliberate stub are different mistakes."""

    with pytest.raises(KeyError):
        flows.flow_for("bgp_sesion")


@pytest.mark.parametrize("object_type", ["bgp_session", "interface"])
def test_the_implemented_flows_resolve(object_type):
    flow = flows.flow_for(object_type)
    assert flow.object_type == object_type
    assert flow.descent
    assert flow.subject_schema


def test_every_rung_finding_is_declared_on_its_flow():
    """Enforced in Flow.__post_init__; asserted here so the intent is visible."""

    for flow in flows.FLOWS.values():
        for rung in flow.descent:
            assert rung.finding in flow.findings


def test_every_flow_declares_the_universal_findings():
    """`all_layers_healthy`, `undetermined` and `cause_not_localised` can be
    produced by the walker for any flow, so every flow must declare them."""

    for object_type, flow in flows.FLOWS.items():
        assert flows.UNIVERSAL_FINDINGS <= flow.findings, object_type


# --------------------------------------------------------------------------- #
# Device scope -- Q-013
# --------------------------------------------------------------------------- #


def test_the_bgp_ladder_switches_to_the_subject_device_below_the_route_rung():
    """The measurement that settled Q-013, pinned as a test.

    On the `broken` label, RR1's own IS-IS was healthy while PE2 -- the
    subject's device -- had none. A ladder that checked the IGP and interface
    rungs locally would find nothing below `route_to_peer` and could never
    reach the cause. See OBS-055.
    """

    ladder = {rung.name: rung.device_scope for rung in flows.flow_for("bgp_session").descent}

    assert ladder["bgp_session"] is flows.DeviceScope.LOCAL
    assert ladder["transport"] is flows.DeviceScope.LOCAL
    assert ladder["route_to_peer"] is flows.DeviceScope.LOCAL
    assert ladder["igp_adjacency"] is flows.DeviceScope.SUBJECT
    assert ladder["interface"] is flows.DeviceScope.SUBJECT


def test_a_path_scoped_rung_must_declare_an_aggregation():
    """A PATH scope resolves to a set, and a set needs a combining rule.

    Left implicit, the walker would silently pick one -- and the right rule
    differs per rung: ECMP routes are any-suffices, path hops are all-must.
    """

    with pytest.raises(ValueError, match="must declare an aggregation"):
        flows.Rung(
            name="probe",
            collect=(flows.CollectStep("isis"),),
            check=checks.isis_adjacency,
            finding="igp_isolated",
            device_scope=flows.DeviceScope.PATH,
        )


def test_a_single_device_rung_may_not_declare_an_aggregation():
    """The other direction: an aggregation on a scope that resolves to one
    device is meaningless, and silently ignoring it would hide a mistake."""

    with pytest.raises(ValueError, match="declares an aggregation"):
        flows.Rung(
            name="probe",
            collect=(flows.CollectStep("isis"),),
            check=checks.isis_adjacency,
            finding="igp_isolated",
            device_scope=flows.DeviceScope.LOCAL,
            aggregation=flows.Aggregation.ALL_HEALTHY,
        )


def test_both_aggregations_exist_and_are_distinct():
    assert flows.Aggregation.ALL_HEALTHY is not flows.Aggregation.ANY_HEALTHY


# --------------------------------------------------------------------------- #
# The ladder itself (T-023)
# --------------------------------------------------------------------------- #


def test_the_bgp_ladder_is_the_protocol_stack_top_down():
    """Order is the contract: the walk descends dependencies, so a reordering
    would change which rung is 'lowest' and therefore the reported cause."""

    assert [r.name for r in flows.flow_for("bgp_session").descent] == [
        "bgp_session",
        "transport",
        "route_to_peer",
        "igp_adjacency",
        "interface",
    ]


def test_all_five_declared_findings_are_reachable_from_a_rung():
    """Q-017's test in registry form.

    Under the original walk rule four of these five were unreachable, because
    the descent stopped at the first broken rung. Every rung-level finding must
    correspond to a rung that can actually produce it.
    """

    flow = flows.flow_for("bgp_session")
    rung_findings = {rung.finding for rung in flow.descent}

    assert rung_findings == {
        "peer_not_established",
        "transport_blocked",
        "peer_unreachable_no_route",
        "igp_isolated",
        "interface_line_down",
    }
    assert rung_findings | flows.UNIVERSAL_FINDINGS == flow.findings


def test_flows_module_calls_no_model_and_touches_no_device():
    import ast
    import inspect
    from pathlib import Path

    source = Path(inspect.getfile(flows)).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"network_tools", "inventory", "llm_analysis", "anthropic", "openai", "netmiko"}
    assert not (imported & forbidden), sorted(imported & forbidden)
