"""checks.py and health.py must never contradict each other (T-021).

The two modules answer different questions over the same evidence.
``health.py`` is per-*device* and produces severities; ``checks.py`` is
per-*object* and produces rung verdicts. The LLD keeps them separate on
purpose — ``health.py``'s rules are pinned by a large existing suite, and
refactoring one into the other would put that at risk for no immediate gain.

**This file is the guardrail that makes coexistence safe.** The rule it
enforces, from `PROCESS.md` T-021:

* Neither may say ``healthy`` where the other says ``broken``.
* One may be ``unevaluated`` where the other is not — that is allowed, and is
  exactly the asymmetry the "absence is unevaluated" rule creates.
* **A genuine disagreement is a design finding, not a test to loosen.**
  §0.11 makes a failure here an absolute HALT.

Comparisons run over **every committed label** — ``t0``, ``t1``, ``healthy``
and ``broken`` — because the labels disagree with each other about the fabric
by construction, and a pairing that agrees on only one of them has not been
tested.
"""

from __future__ import annotations

import pytest
from helpers import set_device_environment

from agent_nettools import checks, health
from agent_nettools.fixtures import load_fixture_evidence


@pytest.fixture(autouse=True)
def _credentials(monkeypatch):
    """`load_fixture_evidence` replays through `collect_evidence`, which resolves
    a device before the injected sender runs -- so it needs credentials present
    even though no socket is ever opened.

    Without this every section errors, every check returns `unevaluated`, and
    every agreement assertion below passes vacuously. That is precisely what
    `test_the_agreement_comparison_is_not_vacuous` exists to catch, and it did.
    """

    set_device_environment(monkeypatch)


DEVICES = ("P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1")
LABELS = ("t0", "t1", "healthy", "broken")

_CASES = [(label, device) for label in LABELS for device in DEVICES]
_IDS = [f"{label}-{device}" for label, device in _CASES]


def _verdict(device, label):
    evidence = load_fixture_evidence(device, label=label)
    return evidence, health.evaluate_fabric({device: evidence})["devices"][device]


def _fired(health_verdict) -> set[str]:
    return {finding["rule"] for finding in health_verdict.get("findings", [])}


# --------------------------------------------------------------------------- #
# IS-IS: checks.isis_adjacency  vs  health's isis_isolated
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("label", "device"), _CASES, ids=_IDS)
def test_isis_check_never_contradicts_the_isis_health_rule(label, device):
    evidence, verdict = _verdict(device, label)
    check = checks.isis_adjacency(evidence)

    isolated = "isis_isolated" in _fired(verdict)
    health_could_not_tell = "isis" in verdict.get("unevaluated", [])

    if check.status == checks.HEALTHY:
        assert not isolated, (
            f"{device}/{label}: checks says the IS-IS adjacencies are healthy while "
            f"health.py fires isis_isolated. This is a real disagreement, not a test "
            f"to loosen -- see PROCESS.md §0.11."
        )
    if check.status == checks.BROKEN:
        assert isolated or health_could_not_tell, (
            f"{device}/{label}: checks says IS-IS is broken while health.py neither "
            f"fires isis_isolated nor reports the intent unevaluated."
        )


# --------------------------------------------------------------------------- #
# BGP: checks.bgp_session_state (per peer)  vs  health's bgp_session_down
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("label", "device"), _CASES, ids=_IDS)
def test_bgp_check_never_contradicts_the_bgp_health_rule(label, device):
    evidence, verdict = _verdict(device, label)

    parsed = evidence.get("bgp", {}).get("data", {}).get("parsed") or {}
    peers = [r["neighbor"] for r in parsed.get("records", []) if r.get("neighbor")]
    if not peers:
        pytest.skip(f"{device} runs no BGP process in {label}")

    statuses = {peer: checks.bgp_session_state(evidence, peer).status for peer in peers}
    any_broken = any(s == checks.BROKEN for s in statuses.values())
    all_healthy = all(s == checks.HEALTHY for s in statuses.values())

    session_down = "bgp_session_down" in _fired(verdict)
    health_could_not_tell = "bgp" in verdict.get("unevaluated", [])

    if all_healthy:
        assert not session_down, (
            f"{device}/{label}: every peer checks healthy ({statuses}) while health.py "
            f"fires bgp_session_down. A real disagreement -- HALT, do not loosen."
        )
    if any_broken:
        assert session_down or health_could_not_tell, (
            f"{device}/{label}: checks reports a broken session ({statuses}) while "
            f"health.py neither fires bgp_session_down nor reports bgp unevaluated."
        )


# --------------------------------------------------------------------------- #
# Interfaces: the one place the two modules deliberately diverge
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("label", "device"), _CASES, ids=_IDS)
def test_interface_check_diverges_from_health_only_on_admin_down(label, device):
    """The known, reasoned divergence — encoded as an allowance, not a loosening.

    `health.py`'s `_interface_admin_up_line_down` deliberately does not fire on
    an admin-down interface: "an admin-down interface is intentional and must
    never fire this rule". `checks.interface_state` calls it `broken`.

    Both are right for the question they answer, which is the per-device
    versus per-object split the LLD draws. "Is this device unhealthy?" and
    "why is this path down?" are different questions: an interface someone
    deliberately shut is not evidence of ill health, and *is* the complete
    answer to why a path is broken.

    So this test allows exactly that divergence and nothing else. Any interface
    that checks calls broken while health stays silent must be admin-down; a
    line-down-but-admin-up interface disagreeing would be a genuine conflict
    and fails here.
    """

    evidence, verdict = _verdict(device, label)

    section = evidence.get("interfaces", {})
    if section.get("data", {}).get("parse_status") != "ok":
        pytest.skip(f"{device}/{label}: interfaces did not parse")

    parsed = section.get("data", {}).get("parsed") or {}
    # health.py names the object in `subject`, not `interface`. Reading the
    # wrong key silently yields {None}, nothing ever matches, and every
    # interface looks like a disagreement -- which is exactly what happened on
    # the first run of this test.
    health_flagged = {
        finding.get("subject")
        for finding in verdict.get("findings", [])
        if finding["rule"] == "interface_admin_up_line_down"
    }

    for record in parsed.get("records", []):
        name = record.get("interface")
        if not name:
            continue
        check = checks.interface_state(evidence, name)
        if check.status != checks.BROKEN or name in health_flagged:
            continue
        # checks says broken, health stayed silent -- only admin-down may do that.
        assert record.get("admin_state") != "up", (
            f"{device}/{label}: checks calls {name} broken (admin_state="
            f"{record.get('admin_state')!r}, line_protocol="
            f"{record.get('line_protocol')!r}) while health.py stays silent, and it "
            f"is not admin-down. That is a genuine disagreement -- HALT."
        )


# --------------------------------------------------------------------------- #
# The comparison must not pass by comparing nothing
# --------------------------------------------------------------------------- #


def test_the_agreement_comparison_is_not_vacuous():
    """A guardrail that iterates zero rows, or only ever sees one verdict,
    passes for the wrong reason. This pins that the corpus actually exercises
    both outcomes, so the tests above are comparing something real."""

    isis = {checks.isis_adjacency(load_fixture_evidence(d, label=lab)).status
            for lab in LABELS for d in DEVICES}
    assert checks.HEALTHY in isis and checks.BROKEN in isis, isis

    bgp_statuses = set()
    for label in LABELS:
        for device in DEVICES:
            evidence = load_fixture_evidence(device, label=label)
            parsed = evidence.get("bgp", {}).get("data", {}).get("parsed") or {}
            for record in parsed.get("records", []):
                peer = record.get("neighbor")
                if peer:
                    bgp_statuses.add(checks.bgp_session_state(evidence, peer).status)
    assert checks.HEALTHY in bgp_statuses and checks.BROKEN in bgp_statuses, bgp_statuses


def test_both_labels_disagree_about_the_fabric_as_intended():
    """`healthy` and `broken` must describe different fabrics.

    If a future recapture made them identical, every agreement test above
    would still pass while testing half as much. This is the tripwire.
    """

    healthy = checks.isis_adjacency(load_fixture_evidence("PE2", label="healthy"))
    broken = checks.isis_adjacency(load_fixture_evidence("PE2", label="broken"))

    assert healthy.status == checks.HEALTHY
    assert broken.status == checks.BROKEN
