"""model_ingress.py -- what a model may SUPPLY, the counterpart to
test_model_egress.py. See model_ingress.py's own module docstring for the
full argument: B-459 / `docs/build/MCP-EXPERIMENT.md` S6.3 is the measured
problem, `reasoning_gate.py`'s "no field for a target's identity at all" is
the borrowed (not imported) mechanism.

Every refusal test here has a positive control alongside it, through the
identical path -- OBS-181: a refusal that is never shown to let a legitimate
case through proves nothing.

The tool manifests below are hand-typed from `mcp_server/staged_surface.py`'s
own function signatures and `templates.py`'s `ParamType` registrations (read,
not imported -- `src/agent_nettools` must not depend on `mcp_server`), the
same "declared, not detected" discipline `PIN_TABLE` itself documents.
"""

from __future__ import annotations

import pytest

from agent_nettools.model_ingress import (
    PIN_TABLE,
    STRUCTURALLY_EXCLUDED_TOOLS,
    ArgumentRefusal,
    ParamRole,
    PinnedContext,
    ToolOffer,
    build_offers,
    resolve_arguments,
)

STAGED_TOOL_NAMES = (
    "explore_lab",
    "check_lab",
    "lookup_lab",
    "investigate_lab",
    "history_lab",
    "probe_lab",
)


# --------------------------------------------------------------------------- #
# The manifest fixture -- one entry per staged_surface.py function, built
# from the real signatures rather than from PIN_TABLE, so a test comparing
# the two is not comparing a table to itself.
# --------------------------------------------------------------------------- #


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _manifest() -> list[dict]:
    return [
        {
            "name": "explore_lab",
            "description": "Answers: what is here, and is it broadly OK?",
            "input_schema": _schema({"device_name": {"type": ["string", "null"]}}, []),
        },
        {
            "name": "check_lab",
            "description": "Answers: what is the state of X right now?",
            "input_schema": _schema(
                {
                    "scope": {"type": "string"},
                    "intent": {
                        "type": ["string", "null"],
                        "enum": ["facts", "interfaces", "bgp", "lldp", "isis", "sr", None],
                    },
                },
                ["scope"],
            ),
        },
        {
            "name": "lookup_lab",
            "description": "Answers: what does this device say about this specific object?",
            "input_schema": _schema(
                {
                    "device_name": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["route", "bgp_neighbor", "interface", "logging"],
                    },
                    "value": {"type": "string"},
                },
                ["device_name", "kind", "value"],
            ),
        },
        {
            "name": "investigate_lab",
            "description": "Answers: why is this broken?",
            "input_schema": _schema(
                {
                    "device_name": {"type": "string"},
                    "subject": {"type": "string"},
                    "flow": {"type": "string", "enum": ["bgp_session", "interface"]},
                },
                ["device_name", "subject"],
            ),
        },
        {
            "name": "history_lab",
            "description": "Answers: what changed on this device?",
            "input_schema": _schema(
                {
                    "device_name": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["latest_diff", "golden_diff", "flaps"],
                    },
                },
                ["device_name"],
            ),
        },
        {
            "name": "probe_lab",
            "description": "ACTIVE PROBE: sends ICMP/UDP traffic to the target.",
            "input_schema": _schema(
                {
                    "device_name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["ping", "traceroute"]},
                    "address": {"type": "string"},
                },
                ["device_name", "kind", "address"],
            ),
        },
    ]


def _bgp_context(subject="10.255.0.12", flow="bgp_session") -> PinnedContext:
    return PinnedContext(device="PE1", subject=subject, flow=flow)


def _offer_named(offers: tuple[ToolOffer, ...], name: str) -> ToolOffer:
    (match,) = [o for o in offers if o.name == name]
    return match


# --------------------------------------------------------------------------- #
# PinnedContext -- absence is never zero, and the type is the enforcement.
# --------------------------------------------------------------------------- #


class TestPinnedContextValidation:
    def test_an_empty_device_is_refused(self):
        with pytest.raises(ValueError):
            PinnedContext(device="", subject=None, flow=None)

    def test_a_non_string_device_is_refused(self):
        with pytest.raises(ValueError):
            PinnedContext(device=None, subject=None, flow=None)  # type: ignore[arg-type]

    def test_an_empty_subject_is_refused_not_treated_as_none(self):
        """Absence is never zero: subject='' must not silently mean 'no
        subject' -- only `None` does."""

        with pytest.raises(ValueError):
            PinnedContext(device="PE1", subject="", flow=None)

    def test_an_empty_flow_is_refused_not_treated_as_none(self):
        with pytest.raises(ValueError):
            PinnedContext(device="PE1", subject=None, flow="")

    def test_positive_control_a_fully_populated_context_is_accepted(self):
        context = PinnedContext(device="PE1", subject="10.255.0.12", flow="bgp_session")
        assert context.device == "PE1"
        assert context.subject == "10.255.0.12"
        assert context.flow == "bgp_session"

    def test_positive_control_subject_and_flow_may_genuinely_be_none(self):
        """None is a legitimate value (no subject / no flow determined),
        distinct from being refused."""

        context = PinnedContext(device="PE1", subject=None, flow=None)
        assert context.subject is None
        assert context.flow is None


# --------------------------------------------------------------------------- #
# lookup_lab -- the one staged tool that is structurally excluded.
# --------------------------------------------------------------------------- #


class TestLookupLabIsStructurallyExcluded:
    def test_lookup_lab_has_no_pin_table_entry(self):
        assert "lookup_lab" not in PIN_TABLE

    def test_lookup_lab_is_recorded_with_a_reason(self):
        assert "lookup_lab" in STRUCTURALLY_EXCLUDED_TOOLS
        reason = STRUCTURALLY_EXCLUDED_TOOLS["lookup_lab"]
        assert "kind" in reason and "logging" in reason

    def test_lookup_lab_is_never_offered_regardless_of_context(self):
        """Structural, not contextual: no PinnedContext makes this tool safe
        to offer, because `value`'s meaning depends on the model's own
        choice of `kind`."""

        rich_context = PinnedContext(device="PE1", subject="10.255.0.12", flow="bgp_session")
        empty_context = PinnedContext(device="PE1", subject=None, flow=None)
        for context in (rich_context, empty_context):
            offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
            assert "lookup_lab" not in {o.name for o in offers}

    def test_positive_control_the_other_five_are_offerable_under_a_rich_context(self):
        """The refusal above proves nothing unless everything else in the
        same manifest, under the same call, comes through."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        names = {o.name for o in offers}
        assert names == {"explore_lab", "check_lab", "investigate_lab", "history_lab", "probe_lab"}


# --------------------------------------------------------------------------- #
# Pinned fields are DELETED from the schema, not filtered at dispatch.
# --------------------------------------------------------------------------- #


class TestPinnedFieldsAreRemovedFromTheSchema:
    def test_explore_lab_device_name_is_absent_from_the_offered_schema(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "explore_lab")
        assert "device_name" not in offer.input_schema["properties"]
        assert offer.pinned == {"device_name": "PE1"}

    def test_check_lab_scope_is_absent_the_intent_enum_remains(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        assert "scope" not in offer.input_schema["properties"]
        assert offer.pinned == {"scope": "PE1"}
        assert offer.enumerated["intent"] == (
            "facts", "interfaces", "bgp", "lldp", "isis", "sr",
        )
        assert "intent" in offer.input_schema["properties"]

    def test_investigate_lab_device_subject_and_flow_are_all_absent(self):
        """No field a model could name a peer address, an interface, or a
        flow through -- exactly B-459's fabrication vector, deleted from the
        wire rather than merely defaulted."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "investigate_lab")
        assert offer.input_schema["properties"] == {}
        assert offer.input_schema["required"] == []
        assert offer.pinned == {"device_name": "PE1", "subject": "10.255.0.12", "flow": "bgp_session"}

    def test_probe_lab_address_is_absent_kind_remains(self):
        """The direct analogue of S6.3's own example: 'a prefix, an
        interface name and a ping target are the same class'."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "probe_lab")
        assert "address" not in offer.input_schema["properties"]
        assert offer.pinned == {"device_name": "PE1", "address": "10.255.0.12"}
        assert offer.enumerated["kind"] == ("ping", "traceroute")

    def test_history_lab_device_name_is_absent_mode_remains(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "history_lab")
        assert "device_name" not in offer.input_schema["properties"]
        assert offer.enumerated["mode"] == ("latest_diff", "golden_diff", "flaps")

    def test_positive_control_the_offered_schema_forbids_extra_properties(self):
        """additionalProperties: False on the offer -- a validating client
        refuses a fabricated field before the call even happens."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        assert offer.input_schema["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# Schema drift is refused, never guessed.
# --------------------------------------------------------------------------- #


class TestSchemaDriftIsRefused:
    def test_an_unrecognised_live_property_excludes_the_tool(self):
        manifest = _manifest()
        for entry in manifest:
            if entry["name"] == "check_lab":
                entry["input_schema"]["properties"]["mystery_field"] = {"type": "string"}
        context = _bgp_context()
        offers = build_offers(manifest, context, allowlist=STAGED_TOOL_NAMES)
        assert "check_lab" not in {o.name for o in offers}

    def test_a_missing_expected_property_excludes_the_tool(self):
        manifest = _manifest()
        for entry in manifest:
            if entry["name"] == "check_lab":
                del entry["input_schema"]["properties"]["scope"]
        context = _bgp_context()
        offers = build_offers(manifest, context, allowlist=STAGED_TOOL_NAMES)
        assert "check_lab" not in {o.name for o in offers}

    def test_positive_control_an_undrifted_manifest_still_offers_it(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        assert "check_lab" in {o.name for o in offers}


# --------------------------------------------------------------------------- #
# Contextual exclusion -- investigate_lab and probe_lab need a shaped subject.
# --------------------------------------------------------------------------- #


class TestContextualExclusion:
    def test_investigate_lab_absent_when_subject_is_none(self):
        context = PinnedContext(device="PE1", subject=None, flow="bgp_session")
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        assert "investigate_lab" not in {o.name for o in offers}

    def test_investigate_lab_absent_when_flow_is_none(self):
        context = PinnedContext(device="PE1", subject="10.255.0.12", flow=None)
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        assert "investigate_lab" not in {o.name for o in offers}

    def test_investigate_lab_absent_when_flow_is_not_one_this_tool_declares(self):
        """`ldp_session` is a real flow elsewhere (`flows.FLOWS`) but is not
        in investigate_lab's own Literal -- pinning it would build a call
        the tool's own schema cannot represent."""

        context = PinnedContext(device="PE1", subject="Gi0/0/0/0", flow="ldp_session")
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        assert "investigate_lab" not in {o.name for o in offers}

    def test_probe_lab_absent_when_subject_is_interface_shaped(self):
        """A ping/traceroute target must be an IPv4 host address
        (`templates.IPv4AddressParam`); an interface-flow subject is not
        one, so probe_lab is excluded rather than probing the wrong thing."""

        context = PinnedContext(device="PE3", subject="Gi0/0/0/0", flow="interface")
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        assert "probe_lab" not in {o.name for o in offers}

    def test_probe_lab_absent_when_subject_is_none(self):
        context = PinnedContext(device="PE1", subject=None, flow=None)
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        assert "probe_lab" not in {o.name for o in offers}

    def test_positive_control_investigate_lab_and_probe_lab_offered_with_a_valid_bgp_subject(self):
        """The refusals above prove nothing unless the SAME machinery offers
        both tools when the context genuinely supports them."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        names = {o.name for o in offers}
        assert "investigate_lab" in names
        assert "probe_lab" in names

    def test_positive_control_interface_flow_still_offers_investigate_lab(self):
        """An interface-shaped subject is wrong for probe_lab but perfectly
        legitimate for investigate_lab -- the exclusion is per-tool, not a
        blanket rejection of the context."""

        context = PinnedContext(device="PE3", subject="Gi0/0/0/0", flow="interface")
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "investigate_lab")
        assert offer.pinned["subject"] == "Gi0/0/0/0"
        assert offer.pinned["flow"] == "interface"


# --------------------------------------------------------------------------- #
# resolve_arguments -- collisions refuse loud, never repairs, positive
# controls throughout (OBS-181).
# --------------------------------------------------------------------------- #


class TestResolveArgumentsRefusesAPinnedCollision:
    def test_the_model_supplying_a_pinned_key_is_refused(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "probe_lab")
        with pytest.raises(ArgumentRefusal):
            resolve_arguments(offer, {"kind": "ping", "address": "10.255.0.99"})

    def test_the_refusal_fires_even_when_the_guessed_value_matches_the_pin(self):
        """Checked BEFORE comparing values -- a model that happens to guess
        the real value is still a model that tried to hold an opinion about
        an identity this design says it may never occupy."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "probe_lab")
        with pytest.raises(ArgumentRefusal):
            resolve_arguments(offer, {"kind": "ping", "address": "10.255.0.12"})

    def test_positive_control_the_same_call_without_the_pinned_key_resolves(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "probe_lab")
        resolved = resolve_arguments(offer, {"kind": "ping"})
        assert resolved == {"device_name": "PE1", "address": "10.255.0.12", "kind": "ping"}


class TestResolveArgumentsRefusesAnUnknownArgument:
    def test_an_argument_outside_the_offered_schema_is_refused(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        with pytest.raises(ArgumentRefusal):
            resolve_arguments(offer, {"intent": "bgp", "bogus_field": "x"})

    def test_positive_control_the_same_call_without_the_unknown_field_resolves(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        resolved = resolve_arguments(offer, {"intent": "bgp"})
        assert resolved == {"scope": "PE1", "intent": "bgp"}


class TestResolveArgumentsNeverRepairs:
    def test_a_mistyped_enum_value_is_refused_not_corrected(self):
        """No 'did you mean bgp' -- a typo'd intent is refused whole, the
        same rule `reasoning_gate.parse_decision` applies to an
        out-of-range index."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        with pytest.raises(ArgumentRefusal):
            resolve_arguments(offer, {"intent": "bgpp"})

    def test_a_missing_required_enumerated_argument_is_refused(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "probe_lab")
        with pytest.raises(ArgumentRefusal):
            resolve_arguments(offer, {})

    def test_positive_control_a_legitimate_fully_model_chosen_argument_passes_through_untouched(self):
        """OBS-181: this is the case every refusal above must not have
        broken -- a real, in-vocabulary, model-chosen enumerated argument
        (the `kind` a human caller would also send) goes through exactly as
        given."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "probe_lab")
        resolved = resolve_arguments(offer, {"kind": "traceroute"})
        assert resolved["kind"] == "traceroute"
        assert resolved["device_name"] == "PE1"
        assert resolved["address"] == "10.255.0.12"

    def test_positive_control_an_optional_enumerated_argument_may_be_omitted(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "history_lab")
        resolved = resolve_arguments(offer, {})
        assert resolved == {"device_name": "PE1"}


class TestResolveArgumentsRejectsANonMappingPayload:
    def test_a_string_payload_is_refused(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        with pytest.raises(ArgumentRefusal):
            resolve_arguments(offer, "intent=bgp")  # type: ignore[arg-type]

    def test_positive_control_an_empty_mapping_is_a_legitimate_payload(self):
        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "check_lab")
        assert resolve_arguments(offer, {}) == {"scope": "PE1"}


# --------------------------------------------------------------------------- #
# A full offer -> resolve round trip for the fully-pinned case.
# --------------------------------------------------------------------------- #


class TestFullyPinnedToolResolvesWithNoModelInput:
    def test_investigate_lab_resolves_from_an_empty_model_payload(self):
        """Every field this tool needs already came from context -- the
        model's own contribution to this call is nothing at all."""

        context = _bgp_context()
        offers = build_offers(_manifest(), context, allowlist=STAGED_TOOL_NAMES)
        offer = _offer_named(offers, "investigate_lab")
        resolved = resolve_arguments(offer, {})
        assert resolved == {"device_name": "PE1", "subject": "10.255.0.12", "flow": "bgp_session"}


# --------------------------------------------------------------------------- #
# ParamRole / PIN_TABLE shape sanity.
# --------------------------------------------------------------------------- #


class TestPinTableShape:
    def test_every_pin_table_entry_covers_exactly_the_manifests_declared_properties(self):
        """PIN_TABLE must neither under- nor over-declare relative to the
        real staged-surface schemas -- a mismatch here is exactly the
        'schema drift' condition `_build_one_offer` refuses at call time,
        caught instead at table-authoring time."""

        manifest_by_name = {entry["name"]: entry for entry in _manifest()}
        for name, specs in PIN_TABLE.items():
            live_properties = set(manifest_by_name[name]["input_schema"]["properties"])
            declared = {spec.name for spec in specs}
            assert declared == live_properties, name

    def test_only_flow_and_address_carry_a_shape_check(self):
        """Every other PINNED param trusts context.device/context.subject
        outright -- the extra guard exists only where the underlying
        template genuinely narrows the legal shape (an IPv4 host for a
        probe target, a two-member vocabulary for investigate_lab's flow)."""

        guarded = {
            (tool, spec.name)
            for tool, specs in PIN_TABLE.items()
            for spec in specs
            if spec.role is ParamRole.PINNED and spec.shape_check is not None
        }
        assert guarded == {("investigate_lab", "flow"), ("probe_lab", "address")}
