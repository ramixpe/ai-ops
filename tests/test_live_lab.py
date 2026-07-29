"""Live-lab integration tests (Phase 8): exercise `nettools` against the real
9-device IOS-XR lab this project's fixtures were captured from.

**Skipped by default.** These need a reachable lab plus real
``DEVICE_USERNAME``/``DEVICE_PASSWORD`` credentials, which CI and most
contributors' machines do not have -- unlike every other test in this suite
(``sender=``, fixture replay, or a fake ``netmiko`` module; see CLAUDE.md,
"Testing seams"), these open a real SSH session. Opt in with:

    NETTOOLS_LIVE_LAB=1 pytest -m live_lab

The ``live_lab`` marker (registered in ``pyproject.toml``) is a *selection*
mechanism only -- it is what lets ``pytest -m live_lab`` target just this
file, and what keeps a bare ``pytest``/``pytest -m live_lab`` from warning
about an unregistered marker. It does not, by itself, skip anything. The
``NETTOOLS_LIVE_LAB`` environment-variable check in every test below is what
actually prevents these from running (and failing loudly) in CI or on a
laptop with no lab reachable: unset, they self-skip with a clear reason
instead of erroring out.
"""

from __future__ import annotations

import os

import pytest

from agent_nettools.health import evaluate_fabric
from agent_nettools.network_tools import check_fabric, collect_evidence, list_devices

pytestmark = pytest.mark.live_lab


def _require_live_lab() -> None:
    flag = os.getenv("NETTOOLS_LIVE_LAB", "").strip().lower()
    if flag not in {"1", "true", "yes", "on"}:
        pytest.skip(
            "Live-lab tests need NETTOOLS_LIVE_LAB=1 plus a reachable lab and real "
            "DEVICE_USERNAME/DEVICE_PASSWORD; skipped by default. Run with "
            "`NETTOOLS_LIVE_LAB=1 pytest -m live_lab`."
        )


def test_list_devices_returns_the_full_nine_device_inventory():
    _require_live_lab()

    result = list_devices()

    assert result["status"] == "success"
    names = {device["name"] for device in result["data"]["devices"]}
    assert names == {"P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"}


def test_collect_evidence_from_pe1_reaches_the_real_device():
    _require_live_lab()

    evidence = collect_evidence("PE1")

    assert evidence["device"] == "PE1"
    assert evidence["platform"] == "cisco_xr"
    assert evidence["facts"]["status"] == "success"


def test_check_fabric_bgp_reaches_every_device():
    _require_live_lab()

    result = check_fabric("bgp")

    assert set(result["data"]["devices"]) == {
        "P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1",
    }


def test_health_evaluate_fabric_against_a_live_collection():
    """The health verdicts documented in CLAUDE.md/README (PE2/PE4 isolated at
    the IS-IS layer, RR1 seeing their sessions Idle) should still describe
    this lab if it has not been repaired since those fixtures were captured --
    this is an honest smoke test, not a strict pin, since a real lab can
    legitimately change."""

    _require_live_lab()

    listed = list_devices()
    names = [device["name"] for device in listed["data"]["devices"]]
    evidence_by_device = {name: collect_evidence(name) for name in names}

    result = evaluate_fabric(evidence_by_device)

    assert result["severity"] in ("ok", "info", "warning", "critical")
    assert set(result["devices"]) == set(names)
