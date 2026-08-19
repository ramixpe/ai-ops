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
    [
        t
        for t in flows.OBJECT_TYPES
        if t not in ("bgp_session", "interface", "isis_adjacency", "ldp_session")
    ],
)
def test_an_unimplemented_flow_raises_rather_than_returning_none(object_type):
    """`None` would let a caller read "no flow" as "nothing wrong"."""

    with pytest.raises(NotImplementedError, match=object_type):
        flows.flow_for(object_type)


def test_an_unknown_object_type_is_a_key_error_not_a_not_implemented():
    """A typo and a deliberate stub are different mistakes."""

    with pytest.raises(KeyError):
        flows.flow_for("bgp_sesion")


def test_device_health_is_refused_not_merely_unbuilt():
    """B-108. `device_health` never gets a `FLOWS` entry -- it is an
    aggregation over independent per-protocol rules, not a dependency descent,
    and forcing rung order onto independent signals would fabricate a causal
    claim the evidence does not support (see the block comment above `FLOWS`
    in `flows.py`).

    All three refused object types raise `NotImplementedError`, so a caller
    cannot tell "refused" from "not yet built" by exception type alone -- but
    the message must, so a human (or a retrying model) reading it does not
    wait for something that will never land. See
    `test_l3vpn_service_is_refused_not_merely_unbuilt` and
    `test_topology_is_refused_not_merely_unbuilt` for the other two, and
    `test_every_declared_object_type_is_built_or_refused` for the registry-wide
    invariant this trio establishes.
    """

    with pytest.raises(NotImplementedError) as excinfo:
        flows.flow_for("device_health")
    message = str(excinfo.value)
    assert "device_health" in message
    assert "nettools health" in message
    assert "assess_lab_device_health" in message

    # Positive control (OBS-181): the refusal is not the module's default
    # behaviour. A flow that is genuinely built still resolves normally and
    # carries none of device_health's own replacement pointers.
    resolved = flows.flow_for("bgp_session")
    assert resolved.object_type == "bgp_session"
    assert "assess_lab_device_health" not in str(resolved)

    # Distinctiveness: the three refusals must not be one canned message
    # wearing three names -- each names its own reason and replacement.
    assert "vrf" not in message.lower()
    assert "nettools audit" not in message


def test_l3vpn_service_is_refused_not_merely_unbuilt():
    """B-110. Q-004/OBS-035 settled the naming (`<pe>:<vrf>`) and
    `docs/build/discovery-l3vpn.md` confirms this fabric runs a real L3VPN
    service (three VRFs, real RT-leak policy, MP-BGP VPNv4 Established with
    non-zero prefixes everywhere, per OBS-183). The flow is refused anyway, on
    a different axis than device_health's: this build has no VRF-scoped
    collection surface at all -- verified directly against
    `platforms.PLATFORM_INTENTS` and `templates.PLATFORM_TEMPLATES` rather
    than assumed, see the `l3vpn_service (B-110)` comment block above `FLOWS`
    in `flows.py` -- so neither `subject_present` nor any rung below it could
    tell one VRF's evidence from another's, or from no VRF at all.
    """

    with pytest.raises(NotImplementedError) as excinfo:
        flows.flow_for("l3vpn_service")
    message = str(excinfo.value)
    assert "l3vpn_service" in message
    assert "vrf" in message.lower()
    assert "discovery-l3vpn.md" in message

    # Positive control: this is not "every flow is refused" -- an implemented
    # flow still resolves, and carries none of l3vpn_service's own wording.
    resolved = flows.flow_for("interface")
    assert resolved.object_type == "interface"
    assert "vrf" not in str(resolved).lower()

    # Distinctiveness against the other two refusals.
    assert "nettools health" not in message
    assert "nettools audit" not in message


def test_topology_is_refused_not_merely_unbuilt():
    """B-111. The backlog row's stated blocker ("LLDP data is
    self-contradictory") was corrected by OBS-103/B-435 -- the disagreement
    was a hostname-resolution artefact, not a real one -- so that is not why
    this is refused. It is refused because no framing tried survives D5's
    "object, or symptom of one?" test: a per-link framing duplicates
    `isis_adjacency`'s existing ladder (LLDP is already read there as
    corroboration and was itself refused as a standalone rung by B-107); a
    fabric-wide framing is an aggregation already served by `nettools
    audit`/`nettools learn-topology`, the B-108 shape again; and a
    device-pair framing re-derives rungs `bgp_session` already owns
    (`route_present`, `isis_adjacency`, `interface_state`) without a new
    top-layer object. See the `topology (B-111)` comment block above `FLOWS`
    in `flows.py` for the full reasoning.
    """

    with pytest.raises(NotImplementedError) as excinfo:
        flows.flow_for("topology")
    message = str(excinfo.value)
    assert "topology" in message
    assert "nettools audit" in message
    assert "nettools learn-topology" in message

    # Positive control: an implemented flow still resolves and carries none
    # of topology's own wording.
    resolved = flows.flow_for("isis_adjacency")
    assert resolved.object_type == "isis_adjacency"
    assert "nettools audit" not in str(resolved)

    # Distinctiveness against the other two refusals.
    assert "nettools health" not in message
    assert "vrf" not in message.lower()


def test_every_declared_object_type_is_built_or_refused():
    """As of B-110/B-111, the registry no longer has a third state.
    `l3vpn_service` and `topology` used to be "declared but not yet designed"
    stubs alongside `device_health`'s refusal; both were investigated this
    round and refused too, on their own evidence. So every one of the seven
    `OBJECT_TYPES` is now either built (`FLOWS`) or refused
    (`REFUSED_OBJECT_TYPES`), and `flow_for` can no longer tell a caller "wait
    for it" -- only "here" or "here is why not, and here is what to use
    instead".
    """

    assert set(flows.FLOWS) | set(flows.REFUSED_OBJECT_TYPES) == set(flows.OBJECT_TYPES)
    assert set(flows.FLOWS).isdisjoint(flows.REFUSED_OBJECT_TYPES)
    assert set(flows.REFUSED_OBJECT_TYPES) == {"device_health", "l3vpn_service", "topology"}


@pytest.mark.parametrize(
    "object_type", ["bgp_session", "interface", "isis_adjacency", "ldp_session"]
)
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


# --------------------------------------------------------------------------- #
# isis_adjacency (B-107) -- the second flow
# --------------------------------------------------------------------------- #


def test_the_isis_adjacency_ladder_is_two_rungs_deep():
    """Order is the contract, same reasoning as the bgp_session test above.

    Shorter than bgp_session's five and deliberately so: this tool can observe
    exactly one genuine dependency below the adjacency itself (the physical
    interface) -- see the module comment above `ISIS_ADJACENCY_FLOW` for why
    LLDP is not a third rung despite carrying real diagnostic weight here.
    """

    assert [r.name for r in flows.flow_for("isis_adjacency").descent] == [
        "isis_adjacency",
        "interface",
    ]


def test_the_isis_adjacency_ladder_never_leaves_the_local_device():
    """Unlike bgp_session, no rung here needs `DeviceScope.SUBJECT` or a
    resolver: `isis`, `lldp` and `interfaces` all describe the device that was
    asked about, not the neighbour on the other end of the link."""

    for rung in flows.flow_for("isis_adjacency").descent:
        assert rung.device_scope is flows.DeviceScope.LOCAL, rung.name
        assert rung.subject_rule is flows.SubjectRule.AS_IS, rung.name
        assert rung.aggregation is None, rung.name


def test_isis_adjacency_reuses_interface_exists_for_subject_presence():
    """The subject vocabulary is a local interface name (OBS-057's gap,
    resolved) -- the same vocabulary `interface_exists` and the `interface`
    flow already use, so this flow's `subject_present` is that function,
    unmodified. Reusing it rather than writing a new one is itself evidence
    the vocabulary choice fits what the codebase already has."""

    assert flows.flow_for("isis_adjacency").subject_present is checks.interface_exists
    assert flows.flow_for("isis_adjacency").subject_present is (
        flows.flow_for("interface").subject_present
    )


def test_the_isis_adjacency_top_rung_reads_isis_lldp_and_interfaces():
    """The top rung's `collect` must name every section its check reads.

    Not belt-and-braces: `epoch._collect_one_rung` re-collects *exactly* this
    tuple for the coherence re-read, so a check reading a section its own rung
    does not declare silently disagrees with itself on re-read -- measured
    live (see checks.py's `isis_neighbor_up` docstring and the comment on this
    rung in flows.py).
    """

    rung = flows.flow_for("isis_adjacency").descent[0]
    assert rung.name == "isis_adjacency"
    names = {step.name for step in rung.collect}
    assert names == {"isis", "lldp", "interfaces"}
    assert all(not step.is_template for step in rung.collect)


def test_all_isis_adjacency_findings_are_reachable_from_a_rung():
    flow = flows.flow_for("isis_adjacency")
    rung_findings = {rung.finding for rung in flow.descent}

    assert rung_findings == {"adjacency_not_up", "interface_line_down"}
    assert rung_findings | flows.UNIVERSAL_FINDINGS == flow.findings


def test_isis_adjacency_subject_schema_names_a_local_interface():
    """Documents the subject-vocabulary decision (OBS-057) at the point a
    caller would actually read it, and pins the wording against drift."""

    schema = flows.flow_for("isis_adjacency").subject_schema
    assert "interface" in schema
    assert "Gi0/0/0/0" in schema


# --------------------------------------------------------------------------- #
# ldp_session (B-109) -- the third flow
# --------------------------------------------------------------------------- #


def test_the_ldp_session_ladder_is_two_rungs_deep():
    """Same depth and same reasoning as isis_adjacency: LDP discovery (Hello)
    genuinely gates LDP session formation, but it is read as corroboration
    *inside* the top rung's check (see `checks.ldp_session_up`'s docstring),
    not spent as a third rung -- the physical interface is the one genuine
    dependency left below the session itself.
    """

    assert [r.name for r in flows.flow_for("ldp_session").descent] == [
        "ldp_session",
        "interface",
    ]


def test_the_ldp_session_ladder_never_leaves_the_local_device():
    """`ldp`, `ldp_discovery` and `interfaces` all describe the device that
    was asked about, not the peer on the other end of the session."""

    for rung in flows.flow_for("ldp_session").descent:
        assert rung.device_scope is flows.DeviceScope.LOCAL, rung.name
        assert rung.subject_rule is flows.SubjectRule.AS_IS, rung.name
        assert rung.aggregation is None, rung.name


def test_ldp_session_reuses_interface_exists_for_subject_presence():
    """Same subject vocabulary as isis_adjacency and interface -- a local
    interface name -- so this flow's `subject_present` is that function,
    unmodified."""

    assert flows.flow_for("ldp_session").subject_present is checks.interface_exists
    assert flows.flow_for("ldp_session").subject_present is (
        flows.flow_for("interface").subject_present
    )


def test_the_ldp_session_top_rung_reads_ldp_ldp_discovery_and_interfaces():
    """The top rung's `collect` must name every section its check reads --
    the epoch coherence re-read (`epoch._collect_one_rung`) re-collects
    *exactly* this tuple, same reasoning as isis_adjacency's own top rung
    (OBS-167)."""

    rung = flows.flow_for("ldp_session").descent[0]
    assert rung.name == "ldp_session"
    names = {step.name for step in rung.collect}
    assert names == {"ldp", "ldp_discovery", "interfaces"}
    assert all(not step.is_template for step in rung.collect)


def test_all_ldp_session_findings_are_reachable_from_a_rung():
    flow = flows.flow_for("ldp_session")
    rung_findings = {rung.finding for rung in flow.descent}

    assert rung_findings == {"session_not_up", "interface_line_down"}
    assert rung_findings | flows.UNIVERSAL_FINDINGS == flow.findings


def test_ldp_session_subject_schema_names_a_local_interface():
    schema = flows.flow_for("ldp_session").subject_schema
    assert "interface" in schema
    assert "Gi0/0/0/0" in schema


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


# --------------------------------------------------------------------------- #
# The member set and the aggregation are one decision (OBS-119)
# --------------------------------------------------------------------------- #


def test_each_member_set_carries_its_own_aggregation():
    """**Asserts the pairing, not either half.**

    This is the point of the test and the reason the rule exists. When
    `EACH_PATH_INTERFACE` switched to every physical interface without switching
    `ANY_HEALTHY` with it, a test of "the aggregation is ANY_HEALTHY" passed and
    a test of "the member set is every physical port" passed, and the rung
    reported healthy on a completely isolated device.

    Only a test that reads them together fails in that state.
    """

    from agent_nettools import descent
    from agent_nettools.epoch import collect_epoch
    from agent_nettools.fixtures import fixture_sender

    flow = flows.flow_for("bgp_session")
    rung = next(r for r in flow.descent if r.subject_rule.is_fanout)

    #: (label, has a reverse route, expected pairing)
    EXPECTED = [
        ("healthy", True, flows.Aggregation.ANY_HEALTHY),
        ("broken", False, flows.Aggregation.ALL_HEALTHY),
    ]

    seen = set()
    for label, route_expected, expected_aggregation in EXPECTED:
        built = collect_epoch(
            flow, "RR1", "10.255.0.12", resolver=lambda _s: "PE2",
            sender=fixture_sender(label=label), origin_prefix="10.255.0.31/32",
        )
        evidence = built.for_device("PE2")
        members, _ = descent.path_interfaces(evidence, "10.255.0.31/32")
        assert bool(members) is route_expected, (
            f"{label}: the fixture must exercise the case, or this is vacuous"
        )

        subjects, aggregation = descent._rung_subjects(
            rung, "10.255.0.12", evidence, "10.255.0.31/32"
        )

        assert subjects, f"{label}: a member set is always produced"
        assert aggregation is expected_aggregation, (
            f"{label}: member set and aggregation must move together -- "
            f"got {aggregation} over {subjects}"
        )
        seen.add(aggregation)

    assert len(seen) == 2, "both pairings must actually be exercised"


def test_the_isolated_device_is_not_reported_healthy():
    """The defect itself, as a regression.

    Two of PE2's three ports are admin-down and the third is up. Under the wrong
    pairing the rung answers healthy; under the right one it answers broken and
    the descent localises to `interface_line_down`.
    """

    from agent_nettools.fixtures import fixture_sender
    from agent_nettools.investigation import investigate

    result = investigate(
        "RR1", "10.255.0.12", sender=fixture_sender(label="broken"),
        resolver=lambda _s: "PE2",
    )
    rung5 = result.descent.outcomes[-1]

    assert rung5.status == "broken", "an isolated device is not healthy"
    assert "all required" in (rung5.result.reason or "")
    assert result.finding == "interface_line_down"
