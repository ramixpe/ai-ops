"""The model-ingress manifest builder: the counterpart to `model_egress.py`.

`model_egress.py` governs what a model may **see**. This module governs what
a model may **supply**.

The measured problem (B-459, `docs/build/MCP-EXPERIMENT.md` S6.3)
-------------------------------------------------------------------
"Every containment mechanism in this build operates on what a tool
**returns**. Nothing constrains what a model **supplies**." A model called
`investigate_lab_session("PE2", "10.255.0.99")` -- a session that does not
exist on this fabric -- and got back a **fully grounded, correctly cited,
deterministically derived investigation of nothing**. `render_command`
accepted the address (well-formed syntax, correctly canonicalised). The
descent walked. Rung 1 read `show bgp summary`, found no such peer, and
answered honestly. Every citation resolved. Grounding passed. **Nothing
malfunctioned anywhere** -- every component did its job correctly on the
input it was given, and that is exactly the failure: silent-failure shape 6
moved upstream of the evidence entirely, to the *question*.

B-459 is marked DONE, but only for `investigate`: `flows.py` attaches
`subject_present` to all four flows and `investigation.py` runs it before the
walk. Nothing else on the staged surface validates -- `get_bgp_neighbor`/
`get_route`/`get_interface` (reached here through `lookup_lab`) and
`ping_device`/`traceroute_device` (through `probe_lab`) take a
syntactically-valid string and send it. §6.3's own words: "a prefix, an
interface name and a ping target are the same class" as the peer address
that started this.

The approach: make fabrication unrepresentable, not refused
--------------------------------------------------------------
An event already supplies a **validated** device and subject --
`event_routing._validated_device` refuses a device absent from the
inventory ("a stated refusal, not a guess"), and `event_routing.
_validated_subject` validates by reconstruction (added after a P0 where a
`subject` label of `"10.0.0.1; touch /tmp/x #"` reached `suggested_command`).
`PinnedContext` below is the same shape those two functions already produce
-- a device, an optional subject, and (for `investigate_lab`) a flow read
from `event_routing.MNEMONIC_FLOW_TABLE`, never chosen by a model.

So the model never supplies an identifier at all. `build_offers` takes the
staged surface's own tool manifest -- name, description, and the real JSON
Schema each tool's parameters produce -- and returns a manifest with every
pinnable parameter's property **deleted from `input_schema`**, not merely
defaulted or filtered later. A model reading the offered schema cannot name
`10.255.0.99` because no field in it accepts a peer address: the field does
not exist on the wire.

This is `reasoning_gate.py`'s own mechanism -- its docstring: "the wire
schema `parse_decision` reads has no field for a target's identity at all"
-- moved from candidate selection to tool arguments. **The structure is
borrowed, the module is not imported.** `reasoning_gate.py` gaining a live,
non-test caller used to be tripwired by `docs/diagrams/d9.py`'s
self-invalidating diagram guard (`facts.reasoning_gate_consumers()`, which
scanned `src/`+`mcp_server/` imports); that generated-diagram system was
retired 2026-08-23 and nothing currently re-derives this automatically, but
the invariant it guarded is unchanged: this module must never be the import
that makes `reasoning_gate` live, so nothing here does
`from .reasoning_gate import ...` or `from . import reasoning_gate`. What is
reused is the *idea* -- delete-the-field beats validate-the-value --
reimplemented independently against a different wire shape (a tool's
`input_schema`, not a gate's decision payload).

Also borrowed, independently: `mcp_server` depends on `agent_nettools`,
never the reverse (`model_egress.py`'s own rule, restated here because it
binds this module too). `build_offers` therefore never imports
`mcp_server.staged_surface` -- it takes that module's tool manifest as a
plain, duck-typed argument (`{"name", "description", "input_schema"}`
mappings) supplied by whatever caller sits on the `mcp_server` side of the
boundary, and the pin table below is a **declared copy** of what
`mcp_server/staged_surface.py`'s six functions actually accept, read
directly from that file and from `templates.py`'s `ParamType` registrations
-- the same "declared, not detected" discipline `model_egress.
FREE_TEXT_FIELDS` and `event_routing.MNEMONIC_FLOW_TABLE` already use.

Three properties that make this hold -- all three are the point
--------------------------------------------------------------------
1. **Removal from the schema, not filtering at dispatch.** A pinned
   parameter's property is deleted from the offered `input_schema` by
   `_build_one_offer`, before any call is dispatched. There is no code path
   in this module that reads a pinned key back out of a model's own JSON --
   `resolve_arguments` never even looks at `model_supplied[key]` for a key
   in `offer.pinned`, except to check whether it is *present* (see 2).

2. **A collision is an `ArgumentRefusal`, never a silent overwrite.** If the
   model supplies a pinned key anyway -- possible for a non-validating
   client, or a model that ignores the schema it was given -- `
   resolve_arguments` refuses the whole call rather than discarding the
   model's value quietly. A silent overwrite would hide the single most
   valuable measurement this design can produce: *how often does an
   unattended model try to fabricate an argument?* S6.3's own honest limit
   on its finding -- "the model asked rather than inventing -- that is one
   model on one occasion, not a property" -- is exactly the gap a counted
   refusal closes.

3. **Never repairs.** Same rule as `reasoning_gate.parse_decision`'s index
   check: an enumerated value outside the offered choices is refused in
   full, never resolved to the nearest real one (no "did you mean 'bgp'"
   for a mistyped `intent`).

A tool with a parameter that is neither pinnable nor enumerable is not
offered at all -- refused at manifest-build time, not at call time. See
`STRUCTURALLY_EXCLUDED_TOOLS`: `lookup_lab` is the one staged tool this
applies to, and the reason is recorded there rather than merely omitted.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "ArgumentRefusal",
    "PIN_TABLE",
    "STRUCTURALLY_EXCLUDED_TOOLS",
    "ParamRole",
    "ParamSpec",
    "PinnedContext",
    "ToolOffer",
    "build_offers",
    "resolve_arguments",
]


# --------------------------------------------------------------------------- #
# PinnedContext -- the validated shape event_routing already produces.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PinnedContext:
    """One event's validated identity, ready to pin into a tool call.

    ``device`` -- inventory-checked upstream by
    `event_routing._validated_device`. Never empty; enforced below rather
    than merely typed, the same "the type is the enforcement" discipline
    `reasoning_gate.NarrowRequest.__post_init__` uses.

    ``subject`` -- reconstructed upstream by
    `event_routing._validated_subject` (a valid IPv4 host address or a valid
    interface name, never a string carrying shell metacharacters -- that
    function exists because of a P0 where an unvalidated `subject` label
    reached `suggested_command`). ``None`` means the event carried no
    subject (e.g. an `explore_lab`/`check_lab`-shaped question); it is not
    interchangeable with an empty string, which is refused below (absence
    is never zero).

    ``flow`` -- the flow name `event_routing.MNEMONIC_FLOW_TABLE` (or a
    future Alertmanager rule) already decided, **not a model's choice**.
    `investigate_lab` accepts all four implemented flows. Event routing
    currently produces only `bgp_session` and `interface`; IS-IS and LDP are
    available through explicit guided MCP calls -- see `PIN_TABLE`.
    ``None`` means no flow was determined (or the context is not
    event-shaped at all), which excludes `investigate_lab` from the offered
    manifest rather than letting the model pick one -- see
    `_is_known_investigate_flow`.
    """

    device: str
    subject: str | None
    flow: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError(
                "PinnedContext.device must be a non-empty string "
                "(inventory-checked upstream by event_routing._validated_device)"
            )
        # Absence is never zero: subject=None ("this event carried no
        # subject") must be distinguishable from subject="" (a validator
        # upstream that produced an empty string by mistake). Refusing the
        # empty string here means a caller cannot silently collapse the two.
        if self.subject is not None and (not isinstance(self.subject, str) or self.subject == ""):
            raise ValueError(
                "PinnedContext.subject must be None (no subject) or a "
                "non-empty string -- an empty string is refused, not treated "
                "as 'no subject'"
            )
        if self.flow is not None and (not isinstance(self.flow, str) or self.flow == ""):
            raise ValueError(
                "PinnedContext.flow must be None (no flow determined) or a "
                "non-empty string"
            )


# --------------------------------------------------------------------------- #
# The pin table -- declared from staged_surface.py's real parameters.
# --------------------------------------------------------------------------- #


class ParamRole(Enum):
    """What a staged tool's parameter is allowed to be, and nothing else."""

    #: Sourced from `PinnedContext`, deleted from the offered schema.
    PINNED = "pinned"
    #: A closed vocabulary (a `Literal[...]` in the real function signature)
    #: the model may genuinely choose from -- kept in the offered schema.
    ENUMERATED = "enumerated"


@dataclass(frozen=True)
class ParamSpec:
    """One staged-tool parameter's declared treatment.

    ``context_attr``/``shape_check`` apply to ``PINNED`` only: the context
    attribute supplying the value, and an optional extra check the value
    must satisfy beyond "is not None". A `PINNED` parameter whose context
    value is `None`, or that fails its `shape_check`, is never partially
    offered -- the whole *tool* is excluded from that call's manifest (see
    `_build_one_offer`). There is no middle ground where the field is
    silently dropped and the underlying function's own default is trusted
    instead; that would be exactly the kind of implicit fallback this
    module exists to refuse.

    ``choices``/``required`` apply to ``ENUMERATED`` only: the values the
    schema already declares (copied from the real `Literal[...]`, not
    reinvented) and whether the underlying function has no default for it
    (`probe_lab`'s `kind`, e.g. -- there is no way to call it without one).
    """

    name: str
    role: ParamRole
    context_attr: str | None = None
    shape_check: Callable[[str], bool] | None = None
    choices: tuple[str, ...] = field(default_factory=tuple)
    required: bool = False


def _is_ipv4_host(value: str) -> bool:
    """Whether ``value`` parses as a bare IPv4 host address.

    `templates.py`'s `ping`/`traceroute` templates both declare
    `IPv4AddressParam` (`PLATFORM_TEMPLATES["cisco_xr"]["ping"|"traceroute"]`)
    -- a single host address, not a prefix. `event_routing._validated_subject`
    can also hand back an interface name (the `interface`-flow shape), which
    is not a legal probe target; pinning `probe_lab`'s `address` to a
    subject of that shape would smuggle a type mismatch into the rendered
    command. Reconstruction, the same canonicalise-by-reconstruction rule
    every other IPv4 check in this codebase uses -- never a regex over the
    string's shape.
    """

    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


#: The four flow names `investigate_lab`'s own Literal accepts -- read directly
#: from `mcp_server/staged_surface.py`. Event routing currently produces only
#: `bgp_session` and `interface`; explicit MCP calls may select the other two.
#: This is not imported from either module (this module must not
#: import `mcp_server`, and importing `event_routing` only to re-derive two
#: literal strings would add a dependency for no new information) -- kept as
#: a small literal tuple, the same "copied, not imported" choice
#: `model_egress.ERROR_KINDS` documents for the identical reason.
_INVESTIGATE_LAB_FLOWS: tuple[str, ...] = (
    "bgp_session", "interface", "isis_adjacency", "ldp_session"
)


def _is_known_investigate_flow(value: str) -> bool:
    """Refuse a `flow` this staged tool's own schema cannot represent.

    `PinnedContext.flow` is typed `str | None`, not a closed enum, because
    it is meant to carry whatever `event_routing.MNEMONIC_FLOW_TABLE` (or a
    future flow) produces. The staged surface accepts all currently
    implemented flows; a future unknown flow remains excluded rather than
    pinning a value the tool's own schema would reject -- the same "never
    repairs" rule applied to context data instead of model input.
    """

    return value in _INVESTIGATE_LAB_FLOWS


#: Declared from `mcp_server/staged_surface.py`'s six function signatures,
#: read directly rather than introspected at runtime -- the same discipline
#: `event_routing.MNEMONIC_FLOW_TABLE` and `model_egress.FREE_TEXT_FIELDS`
#: already use ("Declared as a table, not detected"). `lookup_lab` has no
#: entry here; see `STRUCTURALLY_EXCLUDED_TOOLS`.
#:
#: explore_lab(device_name: str | None = None)
#:   `device_name` pins to `context.device` unconditionally -- explore_lab
#:   accepts no device at all (lists the whole inventory) only because a
#:   human caller might want that; a pinned context always names exactly one
#:   device, so this tool is always offered scoped to it.
#:
#: check_lab(scope: str, intent: Literal["facts","interfaces","bgp","lldp",
#:           "isis","sr"] | None = None)
#:   `scope` is a device name OR the literal `"fabric"` in the real
#:   function -- pinning it to `context.device` closes the fabrication
#:   vector (a model can no longer name an arbitrary device, in or out of
#:   the inventory) at the cost of the fabric-wide branch never being
#:   reachable through a pinned offer. See the module report / docstring
#:   note below `PIN_TABLE` for why this is a documented consequence, not a
#:   bug.  `intent` is a genuine closed vocabulary -- enumerated.
#:
#: investigate_lab(device_name: str, subject: str, flow: Literal[
#:                  "bgp_session","interface","isis_adjacency","ldp_session"] = "bgp_session")
#:   All three parameters pin. `device_name`/`subject` from the identically-
#:   named context fields; `flow` is explicitly NOT enumerated even though
#:   its vocabulary is small and closed -- B-459/S6.3's own framing is that
#:   the flow choice already happened upstream (mnemonic -> flow, by table,
#:   not by model judgement -- `event_routing.MNEMONIC_FLOW_TABLE`), so
#:   offering it to the model here would silently reopen a decision this
#:   codebase already made deterministically. `subject`/`flow` both being
#:   PINNED with no fallback means this tool is entirely absent from the
#:   offer whenever `context.subject` or `context.flow` is `None`, or
#:   `context.flow` names a flow this tool's own schema does not accept.
#:
#: history_lab(device_name: str, mode: Literal["latest_diff","golden_diff",
#:             "flaps"] = "latest_diff")
#:   `device_name` pins; `mode` is a genuine closed vocabulary --
#:   enumerated.
#:
#: probe_lab(device_name: str, kind: Literal["ping","traceroute"],
#:           address: str)
#:   `device_name` pins. `kind` is a genuine closed vocabulary (which active
#:   probe, not an identity) -- enumerated, and required (no default).
#:   `address` pins to `context.subject`, guarded by `_is_ipv4_host`: §6.3's
#:   own words are "a prefix, an interface name and a ping target are the
#:   same class" as the fabricated peer address that motivated this module,
#:   so a probe target is exactly the vector this table exists to close --
#:   but only when the subject is address-shaped; an interface-flow
#:   subject excludes `probe_lab` from the offer rather than probing
#:   something that was never an address.
PIN_TABLE: Mapping[str, tuple[ParamSpec, ...]] = {
    "explore_lab": (ParamSpec("device_name", ParamRole.PINNED, context_attr="device"),),
    "check_lab": (
        ParamSpec("scope", ParamRole.PINNED, context_attr="device"),
        ParamSpec(
            "intent",
            ParamRole.ENUMERATED,
            choices=("facts", "interfaces", "bgp", "lldp", "isis", "sr"),
        ),
    ),
    "investigate_lab": (
        ParamSpec("device_name", ParamRole.PINNED, context_attr="device"),
        ParamSpec("subject", ParamRole.PINNED, context_attr="subject"),
        ParamSpec(
            "flow", ParamRole.PINNED, context_attr="flow", shape_check=_is_known_investigate_flow
        ),
    ),
    "history_lab": (
        ParamSpec("device_name", ParamRole.PINNED, context_attr="device"),
        ParamSpec(
            "mode", ParamRole.ENUMERATED, choices=("latest_diff", "golden_diff", "flaps")
        ),
    ),
    "probe_lab": (
        ParamSpec("device_name", ParamRole.PINNED, context_attr="device"),
        ParamSpec("kind", ParamRole.ENUMERATED, choices=("ping", "traceroute"), required=True),
        ParamSpec("address", ParamRole.PINNED, context_attr="subject", shape_check=_is_ipv4_host),
    ),
}


#: `lookup_lab(device_name: str, kind: Literal["route","bgp_neighbor",
#: "interface","logging"], value: str)` -- excluded whole, for every
#: context, not merely narrowed. Recorded here (not just left absent from
#: `PIN_TABLE`) so the exclusion is a documented decision a reviewer can
#: find, the same "refused, not merely unbuilt" distinction
#: `flows.REFUSED_OBJECT_TYPES` draws for `device_health`/`l3vpn_service`/
#: `topology`.
#:
#: `value`'s legitimate content is conditional on the sibling `kind` field,
#: and the four branches do not share one shape:
#:
#: * `kind="route"`   -> an IPv4 prefix (`templates.IPv4PrefixParam`, which
#:   also accepts a bare host address via `strict=False`) -- matches
#:   `context.subject` when it is address-shaped.
#: * `kind="bgp_neighbor"` -> an IPv4 host address (`IPv4AddressParam`) --
#:   also matches `context.subject` when address-shaped.
#: * `kind="interface"` -> an interface name (`InterfaceNameParam`) --
#:   matches `context.subject` when it is NOT address-shaped.
#: * `kind="logging"` -> `templates.BoundedIntParam(minimum=1, maximum=500)`
#:   -- a **line count**, not a network object identity at all (see
#:   `network_tools.get_logging`'s own signature, `count: int | str = 20`).
#:   Nothing in `PinnedContext` represents "how many log lines", and a
#:   1-500 integer range is not a small closed vocabulary the way
#:   `intent`/`kind`/`mode` are -- it cannot be `ENUMERATED` either.
#:
#: `ParamSpec`/`ToolOffer` describe ONE static role per parameter, for the
#: whole tool, not a role conditioned on another field's runtime value --
#: and there is no context field that is correct for three of `value`'s
#: four meanings and wrong for the fourth. Pinning `value` to
#: `context.subject` universally would silently break the `logging` branch
#: (a subject is never a valid line count) or, worse, coerce a device's own
#: address/interface text into a count field a device would then receive
#: literally. There is no safe static schema for this tool, so it is
#: refused whole rather than partially, silently offered.
STRUCTURALLY_EXCLUDED_TOOLS: Mapping[str, str] = {
    "expand_lab_evidence": (
        "expand_lab_evidence requires an opaque, short-lived investigation context "
        "created by a prior human-guided call. It has no stable event-model binding."
    ),
    "lookup_lab": (
        "lookup_lab's `value` parameter has no single legitimate binding: "
        "for kind in {route, bgp_neighbor, interface} it must be a network "
        "object identity (the exact class of value B-459/MCP-EXPERIMENT.md "
        "S6.3 names as fabricable), but for kind='logging' it is a bounded "
        "integer LINE COUNT (1-500), not an identity at all. No "
        "PinnedContext field represents 'how many log lines' to pin "
        "against, and a bounded-int range is not a closed enumerable "
        "vocabulary the way intent/kind/mode are. Because `value`'s "
        "legitimate domain depends on a sibling field the model itself "
        "chooses (`kind`), no single static schema is correct for every "
        "kind -- so the whole tool is refused at manifest-build time "
        "rather than partially, silently offered."
    ),
}


# --------------------------------------------------------------------------- #
# ToolOffer -- what a model actually receives.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolOffer:
    """One staged tool, as a model is actually allowed to see and call it.

    ``input_schema`` is the tool's real JSON Schema with every `PINNED`
    parameter's property **deleted** -- built by `_build_one_offer` from the
    schema `build_offers` was handed, never reconstructed from scratch, so a
    reviewer can diff this against the live tool's manifest and see exactly
    what was removed. ``pinned`` carries the values that will be injected at
    call time; ``enumerated`` carries the closed vocabulary each remaining
    parameter accepts, straight from `PIN_TABLE`.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    pinned: Mapping[str, Any]
    enumerated: Mapping[str, tuple[str, ...]]


def _build_one_offer(
    tool: Mapping[str, Any], specs: tuple[ParamSpec, ...], context: PinnedContext
) -> ToolOffer | None:
    """One tool's manifest entry -> a `ToolOffer`, or `None` to exclude it.

    `None` covers two distinct reasons, both handled the same way (silent
    exclusion from the returned tuple, per the module docstring's "refused
    at manifest-build time, not at call time"):

    * **Schema drift.** The live `input_schema` names a property `PIN_TABLE`
      has no rule for, or is missing one `PIN_TABLE` expects. Guessing a
      treatment for an unrecognised parameter is exactly the fabrication
      surface this module exists to close, applied to itself -- refuse
      rather than guess, the same rule `event_routing`'s tables apply to an
      unrecognised mnemonic.
    * **Unsatisfiable pin.** A `PINNED` parameter's `context_attr` is
      `None` on this context, or fails its `shape_check`. There is no
      partial offer where that one field is left for the model to fill in
      instead -- that would be exactly the fabrication vector B-459 names.
    """

    name = tool.get("name")
    description = tool.get("description")
    schema = tool.get("input_schema")
    if not isinstance(name, str) or not isinstance(description, str):
        return None
    if not isinstance(schema, Mapping):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return None

    declared_names = {spec.name for spec in specs}
    if set(properties) - declared_names:
        return None  # a live parameter this table has no rule for

    pinned: dict[str, Any] = {}
    enumerated: dict[str, tuple[str, ...]] = {}
    offered_properties: dict[str, Any] = {}
    offered_required: list[str] = []

    for spec in specs:
        if spec.name not in properties:
            return None  # the table expects a parameter the live schema lacks

        if spec.role is ParamRole.PINNED:
            value = getattr(context, spec.context_attr)
            if value is None:
                return None
            if spec.shape_check is not None and not spec.shape_check(value):
                return None
            pinned[spec.name] = value
        else:
            enumerated[spec.name] = spec.choices
            # Copied from the REAL schema's own property, not reinvented --
            # so the offered schema is a subset of the live one, provably.
            offered_properties[spec.name] = dict(properties[spec.name])
            if spec.required:
                offered_required.append(spec.name)

    input_schema = {
        "type": "object",
        "properties": offered_properties,
        "required": offered_required,
        "additionalProperties": False,
    }
    return ToolOffer(
        name=name,
        description=description,
        input_schema=input_schema,
        pinned=pinned,
        enumerated=enumerated,
    )


def build_offers(
    tools: Iterable[Mapping[str, Any]],
    context: PinnedContext,
    *,
    allowlist: Iterable[str] = (),
) -> tuple[ToolOffer, ...]:
    """The staged surface's live manifest -> the manifest a model may see.

    ``tools`` is the caller's own view of the live tool registry -- each
    entry a mapping with ``"name"``, ``"description"``, ``"input_schema"``
    (a JSON Schema `{"type": "object", "properties": {...}, "required":
    [...]}`, exactly the shape an MCP `list_tools()` response carries per
    tool once decoded). This module never fetches that itself and never
    imports `mcp_server` to build it -- see the module docstring's
    dependency-direction note. ``allowlist`` scopes which tool NAMES are
    even considered (the caller's own `STAGED_TOOL_NAMES`, typically); a
    tool outside it, or with no `PIN_TABLE` entry, or in
    `STRUCTURALLY_EXCLUDED_TOOLS`, contributes nothing to the result --
    silently, the same "not in the table is the correct answer, loudly [to
    a reviewer reading the table, not as an exception at runtime]" rule
    `event_routing.MNEMONIC_FLOW_TABLE`'s docstring states.

    Order is preserved from ``tools`` -- this function does not reorder or
    deduplicate what the caller handed it.
    """

    allowed = set(allowlist)
    offers: list[ToolOffer] = []
    for tool in tools:
        name = tool.get("name") if isinstance(tool, Mapping) else None
        if name not in allowed or name in STRUCTURALLY_EXCLUDED_TOOLS:
            continue
        specs = PIN_TABLE.get(name)
        if specs is None:
            continue
        offer = _build_one_offer(tool, specs, context)
        if offer is not None:
            offers.append(offer)
    return tuple(offers)


# --------------------------------------------------------------------------- #
# resolve_arguments -- the one function that turns a model's JSON into a call.
# --------------------------------------------------------------------------- #


class ArgumentRefusal(ValueError):
    """Raised when a model's supplied arguments cannot become a safe call.

    Never carries the model's own free text beyond what is needed to name
    which key/value was refused -- the same restraint
    `reasoning_gate.GateRefusal`'s docstring describes for its own refusal
    object, applied here because an `ArgumentRefusal` message is itself
    something a caller might log or relay, and it must not become a second,
    unmarked channel for whatever a model wrote.
    """


def resolve_arguments(offer: ToolOffer, model_supplied: Mapping[str, Any]) -> dict[str, Any]:
    """Merge ``offer.pinned`` with a model's own arguments, or refuse whole.

    Three refusals, each total (nothing is partially applied, nothing is
    silently corrected):

    1. **A pinned key was supplied by the model at all.** Never overwritten
       silently -- see the module docstring's point 2. This is checked
       first and independently of whether the supplied value even matches
       the pinned one, because a model that *guessed right* is still a
       model that tried to supply an identity this design says it may
       never hold an opinion about.
    2. **An argument outside ``offer.enumerated`` was supplied.** The only
       keys a model may ever send are the ones `build_offers` left in the
       schema.
    3. **An enumerated value outside its own `choices`, or a required
       enumerated key missing entirely.** Refused in full, never resolved
       to the nearest valid value -- `reasoning_gate.parse_decision`'s own
       rule for an out-of-range index, applied here to an out-of-vocabulary
       string.

    A legitimate, fully-model-chosen argument that clears all three passes
    through completely untouched -- merged into the pinned values and
    returned, never re-validated or re-typed a second time here (that is
    the underlying tool function's own job, unchanged).
    """

    if not isinstance(model_supplied, Mapping):
        raise ArgumentRefusal(
            f"{offer.name}: arguments must be a JSON object, got "
            f"{type(model_supplied).__name__}"
        )

    collisions = sorted(set(model_supplied) & set(offer.pinned))
    if collisions:
        raise ArgumentRefusal(
            f"{offer.name}: the model supplied pinned argument(s) {collisions}; "
            "refusing rather than silently overwriting -- a validated value "
            "already came from context (B-459, MCP-EXPERIMENT.md S6.3), and a "
            "silent overwrite would hide the one measurement this design "
            "exists to produce: how often an unattended model tries to "
            "fabricate an identity argument"
        )

    unknown = sorted(set(model_supplied) - set(offer.enumerated))
    if unknown:
        raise ArgumentRefusal(
            f"{offer.name}: unknown argument(s) {unknown}; only "
            f"{sorted(offer.enumerated)} may be supplied"
        )

    required = offer.input_schema.get("required", ())
    for key in required:
        if key not in model_supplied:
            raise ArgumentRefusal(f"{offer.name}: missing required argument {key!r}")

    for key, value in model_supplied.items():
        choices = offer.enumerated[key]
        if value not in choices:
            raise ArgumentRefusal(
                f"{offer.name}.{key}: {value!r} is not one of {choices}; "
                "refused in full, never resolved to the nearest valid value"
            )

    resolved = dict(offer.pinned)
    resolved.update(model_supplied)
    return resolved
