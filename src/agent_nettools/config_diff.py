"""B-106 -- the intent-vs-observed diff (D16's payoff), and B-105's finding.

**"The real answer to the context problem: the model reads the
reconciliation, not the documents."** `config_section.py` gives this build
configured intent (`config_isis`, `config_interface`); the nine `show`
intents already give it observed operational state. Nothing before this
module compared the two in code -- which is exactly why a real fault
(B-496's `isis-broken` pair, PE3 <-> P2) regularly bottomed out at
`flows.CAUSE_NOT_LOCALISED`: the interface rung read healthy, the isis rung
read broken, and the only honest thing left to say was that nothing further
down explained it. It could not say "PE3's Gi0/0/0/0 is enabled under
`router isis CORE` but has no `ipv4 address`" -- that sentence needs both
axes read at once, compared field by field, in code. This module is that
comparison.

**No model is involved anywhere in this module.** Same discipline as
`descent.py`, for the same reason: reconciling a parsed config record against
a parsed status record is parse-and-compare, not judgement.

Three outcomes, closed
-----------------------
`AGREES`, `DISAGREES`, `CANNOT_COMPARE` -- D16's own vocabulary, kept as its
own enum rather than reusing `checks.HEALTHY`/`BROKEN`/`UNEVALUATED`. A field
diff is not a health verdict (nothing here is read by `descent.py`, and
nothing here is wired into a `Flow` -- see "Not a rung" below), and closing
over a distinct set of three states keeps that true structurally rather than
by convention.

`CANNOT_COMPARE` is not a weaker `DISAGREES`, and it is not a silent
`AGREES` either. It means exactly one thing: intent or observation (or both)
was missing, so nothing was actually compared. Collapsing it into one of the
other two is this build's own recurring failure -- an absence reported as a
value (OBS-188, OBS-202) -- and `FieldDiff.__post_init__` refuses to
construct a `CANNOT_COMPARE` result with no stated reason, for the same
reason `checks.CheckResult` refuses a `healthy`/`broken` verdict with no
evidence key.

B-105 -- inheritance resolution, investigated and not built
--------------------------------------------------------------
B-105's brief was `resolve_inheritance(parsed_config) -> dict`: expand
IOS-XR's `neighbor-group` / `session-group` / `af-group` and record, per
resolved field, whether it came from a group or was set directly -- because
an unresolved `use neighbor-group RR-CLIENT` forces a model to guess the
expansion from its majority-dialect prior (D16, `docs/design/
lld-investigation-layer.md` S5.5).

**Checked first, per B-105's own instruction, and the answer is no.** Three
independent sources agree, none of them this session inventing a live SSH
probe it had no credentials for in this worktree:

1. Every `config_isis`/`config_interface` fixture committed to this repo (11
   devices, both the ``healthy`` and ``isis-broken`` labels) was grepped for
   ``neighbor-group``, ``session-group``, ``af-group`` and a bare ``use ``
   line. Zero matches. IS-IS's own IOS-XR grammar has no equivalent
   indirection to begin with -- every interface's `point-to-point`, `bfd`,
   and `address-family` lines are written directly in every captured stanza,
   never through a `use` reference (see the fixtures under
   `tests/fixtures/cisco_xr/*/isis-broken/show-running-config-router-isis.txt`
   and the `healthy` label's equivalents).
2. `docs/build/discovery-l3vpn.md` S5 (T-006, live read-only SSH to PE1-PE4,
   2026-08-15) recorded PE1's `router bgp 65000` neighbour toward RR1 with
   its attributes named directly (`update-source Loopback0`, `route-policy
   PASS in/out`, BFD) -- the shape group-based inheritance would replace.
3. `docs/build/ROUND-8.md` S0 (B-463, live read of `show running-config
   router bgp 65000` on PE2, 2026-08-17) states it explicitly: **"`remote-as`
   is direct on the neighbour, no `use neighbor-group`."**

`neighbor-group`/`session-group`/`af-group` are BGP-specific constructs, and
`config_section.py`'s own docstring already explains why `config_bgp`/
`config_bgp_neighbor` were deliberately not added to `templates.py` (B-104:
no captured fixture yet demonstrates BGP config as a real blocker, unlike
IS-IS). So there is currently no parsed config shape in this build a group
reference could even appear inside.

**Conclusion: build the resolver against the case that exists, per the
task's own instruction, and the case that exists has no inheritance in it.**
Writing `resolve_inheritance` today would mean writing an expansion engine
exercised by nothing but a hand-authored synthetic test forever -- OBS-121's
"capability added and never exercised", the same shape B-503 refused for
OSPF/RSVP-TE/CDP after measuring them absent from this fabric too. What *is*
real and worth stating in code is the corollary: because nothing here is
inherited, every field this module reads from `config_isis`/
`config_interface` already **is** the resolved value -- its provenance is
"direct" by construction, not by a resolution step that had to run. That is
why `FieldDiff` below carries `intent`/`observed` values straight from the
parsed record with no intermediate "resolved config" object: there is
nothing between the parse and the field for this fabric, today. If a future
capture ever shows a `use neighbor-group`/`session-group`/`af-group` line
(which would require `config_bgp`/`config_bgp_neighbor` to exist first), it
will not silently misparse -- `config_section.py`'s own accounting
(`unaccounted_lines`, S0.10) already refuses to drop a line it does not
recognise, so an inheritance reference in a future capture surfaces loudly,
as a documented limit, exactly as BUILD-PLAN S0.13 requires. That is the
right amount of code for a mechanism that has never been observed: enough to
not misread it if it appears, not an engine built ahead of any evidence it
is needed.

Not a rung
------------
This module is deliberately not wired into `flows.py`/`checks.py`.
Reconciliation reads `show running-config`, and deciding that a flow's
descent should read configuration at all is a design decision nobody has
made yet -- adding a rung here would make that decision by accident, buried
inside an axis that was only supposed to build the comparison. Every
function below takes an already-assembled evidence dict and returns a typed
result; nothing here calls into `descent.py`, `flows.py`, or `checks.py`'s
rung machinery, and nothing here decides what a walk does with a
disagreement.

Field coverage
-----------------
Two fields pair a config-axis record against an observed one, matching the
two rungs `bgp_session`, `isis_adjacency` and `ldp_session` all bottom out
at:

* ``isis_adjacency`` -- intent: does `config_isis` have an interface stanza
  for this subject at all (`records` keyed by `interface`)? observed: does
  `isis` (`show isis neighbors`) show an ``Up`` adjacency on it? This is
  B-496's worked example exactly: PE3's Gi0/0/0/0 answers "yes" and "no",
  which is `DISAGREES`, stated in code instead of guessed by a model handed
  two raw sections.
* ``interface_admin_state`` -- intent: `config_interface`'s `shutdown`
  boolean. observed: `interfaces`' (`show interfaces brief`) `admin_state`
  column. Answers "was it put there on purpose" versus "is it actually
  reachable", the distinction `config_interface`'s own docstring names as
  D16's reason for existing.

A third field, ``ipv4_address``, is included and is **always**
`CANNOT_COMPARE` -- not a bug, and not the same reason as a missing section.
None of the nine `show` intents this build collects (`facts`, `interfaces`,
`bgp`, `bgp_vpnv4`, `lldp`, `isis`, `ldp`, `ldp_discovery`, `sr`) reports a
per-interface IPv4 address (`parsers.parse_xr_interfaces` carries admin/line
state, encapsulation, MTU and bandwidth only). So this field has no
observed half to compare against, ever, on this fabric, as currently built --
a structurally different flavour of "cannot compare" than "the section
failed to parse this time", and D16/B-106 both require the two are never
rendered the same way. It is kept as a field, not silently dropped, precisely
so that fact is visible rather than absent.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import checks
from .checks import parsed_records, require_parsed
from .interface_kind import same_interface
from .network_tools import collect_evidence_and_templates

__all__ = [
    "AGREES",
    "CANNOT_COMPARE",
    "DISAGREES",
    "OUTCOMES",
    "FieldDiff",
    "ReconciliationResult",
    "diff_interface_admin_state",
    "diff_interface_ipv4_address",
    "diff_isis_adjacency",
    "gather_reconciliation_evidence",
    "reconcile_interface",
]

# The three outcomes. Deliberately not `checks.STATUSES` -- see the module
# docstring's "Three outcomes, closed" section.
AGREES = "agrees"
DISAGREES = "disagrees"
CANNOT_COMPARE = "cannot_compare"

OUTCOMES: frozenset[str] = frozenset({AGREES, DISAGREES, CANNOT_COMPARE})


@dataclass(frozen=True)
class FieldDiff:
    """One field's reconciliation between configured intent and observed
    operational state, for one subject.

    Frozen for the same reason `checks.CheckResult` is: a verdict is a record
    of what was compared, and a caller that could mutate one could turn a
    `cannot_compare` into an `agrees` several frames away from the evidence
    with nothing downstream able to notice.
    """

    field: str
    outcome: str
    intent: Any = None
    observed: Any = None
    reason: str | None = None
    intent_evidence_key: str | None = None
    observed_evidence_key: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(
                f"FieldDiff.outcome must be one of {sorted(OUTCOMES)}, got {self.outcome!r}"
            )
        # The guard B-106 exists to enforce. An absence must never render as
        # an unexplained value -- collapsing "cannot compare" into a bare
        # `disagrees` or a silently-empty `agrees` is this build's recurring
        # failure (OBS-188, OBS-202: "an absence reported as a value"), and
        # D16's own text names the same risk for this exact axis.
        if self.outcome == CANNOT_COMPARE and not self.reason:
            raise ValueError(
                "a cannot_compare FieldDiff must carry a reason -- an absence with "
                "no explanation is indistinguishable from a value nobody checked"
            )


@dataclass(frozen=True)
class ReconciliationResult:
    """One subject's reconciliation, bundling every field this axis compares.

    Mirrors `descent.DescentResult`'s shape deliberately -- a tuple of typed
    per-field outcomes plus convenience properties -- so a caller never has
    to filter `fields` by hand to ask "did anything disagree?" or "was
    everything actually comparable?".
    """

    device: str
    subject: str
    fields: tuple[FieldDiff, ...] = field(default_factory=tuple)

    @property
    def disagreements(self) -> tuple[FieldDiff, ...]:
        return tuple(f for f in self.fields if f.outcome == DISAGREES)

    @property
    def incomparable(self) -> tuple[FieldDiff, ...]:
        return tuple(f for f in self.fields if f.outcome == CANNOT_COMPARE)

    @property
    def has_disagreement(self) -> bool:
        return bool(self.disagreements)


def _find_by_interface(records: list[dict[str, Any]], interface: str) -> dict[str, Any] | None:
    """The record whose `interface` field denotes `interface`, or `None`.

    Matched through `interface_kind.same_interface`, not exact string
    equality: `config_isis`'s records come from `show running-config router
    isis`, which IOS-XR always echoes in the *long* form
    (`GigabitEthernet0/0/0/0`), while `interface`/`isis`'s own subject
    convention is the *short* form a device's other `show` output uses
    (`Gi0/0/0/0`, per `flows.ISIS_ADJACENCY_FLOW`'s `subject_schema`). This is
    the exact cross-spelling problem OBS-117 names -- same discipline
    `checks.isis_neighbor_up` already uses for its own LLDP corroboration.
    """

    return next(
        (r for r in records if same_interface(r.get("interface", ""), interface)), None
    )


def _meta(section: dict[str, Any]) -> dict[str, Any]:
    parsed = (section.get("data") or {}).get("parsed") or {}
    meta = parsed.get("meta")
    return meta if isinstance(meta, dict) else {}


def diff_isis_adjacency(evidence: dict[str, Any], device: str, interface: str) -> FieldDiff:
    """Intent: is IS-IS configured on `interface` (`config_isis` has a
    stanza for it)? Observed: is an IS-IS adjacency `Up` on it (`isis`)?

    B-496's own worked example: PE3's Gi0/0/0/0 answers "yes, configured" and
    "no adjacency" -- `DISAGREES`, with a reason naming exactly that. This is
    the fact `descent.py`'s `cause_not_localised` could not state, because
    nothing before this module read the config half of the question at all.
    """

    field_name = "isis_adjacency"
    intent_key = checks.evidence_key(device, "config_isis")
    observed_key = checks.evidence_key(device, "isis", interface)

    config_section, config_bail = require_parsed(evidence, "config_isis", subject=interface)
    isis_section, isis_bail = require_parsed(evidence, "isis", subject=interface)

    missing = []
    if config_bail is not None:
        missing.append(f"intent ({intent_key}): {config_bail.reason}")
    if isis_bail is not None:
        missing.append(f"observed ({observed_key}): {isis_bail.reason}")
    if missing:
        return FieldDiff(
            field=field_name,
            outcome=CANNOT_COMPARE,
            reason="; ".join(missing),
            intent_evidence_key=intent_key,
            observed_evidence_key=observed_key,
        )

    config_record = _find_by_interface(parsed_records(config_section), interface)
    intent_enabled = config_record is not None

    isis_record = _find_by_interface(parsed_records(isis_section), interface)
    observed_up = isis_record is not None and isis_record.get("state") == "Up"

    if intent_enabled == observed_up:
        return FieldDiff(
            field=field_name,
            outcome=AGREES,
            intent=intent_enabled,
            observed=observed_up,
            intent_evidence_key=intent_key,
            observed_evidence_key=observed_key,
        )

    if intent_enabled and not observed_up:
        reason = (
            f"{device}:{interface} is enabled under the configured IS-IS process "
            f"('router isis' carries an interface stanza for it) but shows no Up "
            f"adjacency in 'show isis neighbors'"
        )
    else:
        reason = (
            f"{device}:{interface} shows an Up IS-IS adjacency in 'show isis "
            f"neighbors' but has no interface stanza under the configured IS-IS "
            f"process"
        )

    return FieldDiff(
        field=field_name,
        outcome=DISAGREES,
        intent=intent_enabled,
        observed=observed_up,
        reason=reason,
        intent_evidence_key=intent_key,
        observed_evidence_key=observed_key,
    )


def diff_interface_admin_state(evidence: dict[str, Any], device: str, interface: str) -> FieldDiff:
    """Intent: was `interface` put here on purpose (`config_interface`'s
    `shutdown` boolean)? Observed: is it administratively up (`interfaces`'
    `admin_state` column)?

    The bottom rung `bgp_session`, `isis_adjacency` and `ldp_session` all
    share (`interface_line_down`) can say an interface is down; only this
    comparison says whether that was deliberate.
    """

    field_name = "interface_admin_state"
    intent_key = checks.evidence_key(device, "config_interface", interface)
    observed_key = checks.evidence_key(device, "interfaces", interface)

    config_key = f"config_interface:{interface}"
    config_section, config_bail = require_parsed(evidence, config_key, subject=interface)
    observed_section, observed_bail = require_parsed(evidence, "interfaces", subject=interface)

    missing = []
    if config_bail is not None:
        missing.append(f"intent ({intent_key}): {config_bail.reason}")
    if observed_bail is not None:
        missing.append(f"observed ({observed_key}): {observed_bail.reason}")
    if missing:
        return FieldDiff(
            field=field_name,
            outcome=CANNOT_COMPARE,
            reason="; ".join(missing),
            intent_evidence_key=intent_key,
            observed_evidence_key=observed_key,
        )

    intent_shutdown = bool(_meta(config_section).get("shutdown"))

    observed_record = _find_by_interface(parsed_records(observed_section), interface)
    if observed_record is None:
        return FieldDiff(
            field=field_name,
            outcome=CANNOT_COMPARE,
            intent=intent_shutdown,
            reason=(
                f"observed ({observed_key}): 'interfaces' parsed, but has no record "
                f"for {interface!r} -- present in config but never reported by "
                f"'show interfaces brief'"
            ),
            intent_evidence_key=intent_key,
            observed_evidence_key=observed_key,
        )

    observed_admin_state = observed_record.get("admin_state")
    observed_up = observed_admin_state == "up"
    expected_up = not intent_shutdown

    if expected_up == observed_up:
        return FieldDiff(
            field=field_name,
            outcome=AGREES,
            intent=intent_shutdown,
            observed=observed_admin_state,
            intent_evidence_key=intent_key,
            observed_evidence_key=observed_key,
        )

    if intent_shutdown:
        reason = (
            f"{device}:{interface} carries a 'shutdown' line in its configuration "
            f"but 'show interfaces brief' reports admin_state={observed_admin_state!r}, "
            f"not administratively down"
        )
    else:
        reason = (
            f"{device}:{interface} carries no 'shutdown' line in its configuration "
            f"but 'show interfaces brief' reports admin_state={observed_admin_state!r}, "
            f"not up"
        )

    return FieldDiff(
        field=field_name,
        outcome=DISAGREES,
        intent=intent_shutdown,
        observed=observed_admin_state,
        reason=reason,
        intent_evidence_key=intent_key,
        observed_evidence_key=observed_key,
    )


def diff_interface_ipv4_address(evidence: dict[str, Any], device: str, interface: str) -> FieldDiff:
    """Intent: what IPv4 address (if any) does `config_interface` carry for
    `interface`? Always `CANNOT_COMPARE` -- see the module docstring's "Field
    coverage" section for why this is a real, permanent fact about this
    build's observed axis rather than a placeholder for unfinished work.
    """

    field_name = "ipv4_address"
    intent_key = checks.evidence_key(device, "config_interface", interface)

    config_key = f"config_interface:{interface}"
    config_section, config_bail = require_parsed(evidence, config_key, subject=interface)
    if config_bail is not None:
        return FieldDiff(
            field=field_name,
            outcome=CANNOT_COMPARE,
            reason=f"intent ({intent_key}): {config_bail.reason}",
            intent_evidence_key=intent_key,
        )

    intent_address = _meta(config_section).get("ipv4_address")

    return FieldDiff(
        field=field_name,
        outcome=CANNOT_COMPARE,
        intent=intent_address,
        reason=(
            "no observed intent in this build reports a per-interface IPv4 "
            "address -- 'interfaces' ('show interfaces brief') carries "
            "admin/line state, encapsulation, MTU and bandwidth only "
            "(parsers.parse_xr_interfaces); there is nothing on the "
            "operational axis to compare this field against"
        ),
        intent_evidence_key=intent_key,
    )


def reconcile_interface(evidence: dict[str, Any], device: str, interface: str) -> ReconciliationResult:
    """Every field this axis currently knows how to compare, for one
    (device, interface) subject. No field is ever silently dropped if it
    could not be evaluated -- a `CANNOT_COMPARE` `FieldDiff` is still
    returned, never omitted, so `len(result.fields)` is constant regardless
    of what could actually be read.
    """

    return ReconciliationResult(
        device=device,
        subject=interface,
        fields=(
            diff_isis_adjacency(evidence, device, interface),
            diff_interface_admin_state(evidence, device, interface),
            diff_interface_ipv4_address(evidence, device, interface),
        ),
    )


def gather_reconciliation_evidence(
    device: str,
    interfaces: Sequence[str],
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """One device's observed evidence, plus this axis's config-side sections
    for each of `interfaces` -- everything `reconcile_interface` needs, in
    one batched session.

    Built on `network_tools.collect_evidence_and_templates` rather than a
    `collect_evidence` call followed by one `run_template` per interface:
    B-455 measured that session count, not command count, is what costs time
    on this fabric (~8s per extra login after the first), so this axis reuses
    the same batching `fixtures.capture_device_templates` already relies on
    instead of reintroducing the per-call cost.

    `interfaces` is a required, explicit list rather than something this
    function derives on its own from a first `collect_evidence` call: the
    natural caller already knows which interface(s) it is reconciling (the
    same way `reconcile_interface`'s own caller does), and deriving the list
    here would cost a second SSH session for a case this function does not
    need to solve. `fixtures.capture_device`'s own `templates=True` path is
    the one place in this codebase that already accepted a second-session
    cost, deliberately, for a genuinely different job -- writing the whole
    fixture corpus to disk in advance, where no caller yet knows the
    interface list.
    """

    manifest: list[tuple[str, dict[str, str]]] = [("config_isis", {})]
    manifest.extend(("config_interface", {"interface": name}) for name in interfaces)

    evidence, template_envelopes = collect_evidence_and_templates(device, manifest, sender=sender)

    evidence["config_isis"] = template_envelopes[0]
    for name, envelope in zip(interfaces, template_envelopes[1:], strict=True):
        evidence[f"config_interface:{name}"] = envelope

    return evidence
