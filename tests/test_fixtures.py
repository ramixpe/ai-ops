"""Tests for fixture capture, scrubbing, and offline replay.

The committed fixtures under ``tests/fixtures`` are real IOS-XR output captured
from the lab as two snapshots ~90s apart (labels ``t0`` and ``t1``) on an
otherwise quiet fabric. They are the ground truth for parser, diff, and
health-rule work, none of which needs lab access to develop against.
"""

from __future__ import annotations

import pytest
from helpers import FIXTURE_DIR, install_fake_netmiko, set_device_environment

from agent_nettools.fixtures import (
    QUIET_PAIR_LABELS,
    capture_device,
    command_slug,
    load_fixture_evidence,
    scrub_output,
)
from agent_nettools.lab import DEVICES
from agent_nettools.network_tools import EVIDENCE_COMMANDS, diff_evidence

ALL_COMMANDS = [command for group in EVIDENCE_COMMANDS.values() for command in group]


def load_pair(device_name):
    """Return the (t0, t1) evidence pair for one device from committed fixtures."""

    return tuple(
        load_fixture_evidence(device_name, label=label, base_dir=str(FIXTURE_DIR))
        for label in QUIET_PAIR_LABELS
    )


def test_command_slug_is_filesystem_safe():
    assert command_slug("show running-config hostname") == "show-running-config-hostname"
    assert command_slug("show segment-routing traffic-eng policy") == (
        "show-segment-routing-traffic-eng-policy"
    )
    # Distinct commands must never collide on one filename.
    assert len({command_slug(command) for command in ALL_COMMANDS}) == len(ALL_COMMANDS)


def test_scrub_output_redacts_credential_material():
    """The committed XRd fixtures contain nothing sensitive, so the scrubber has
    no live coverage from them. Pin its behaviour explicitly instead."""

    scrubbed = scrub_output(
        "username admin password lab-pass-123\n"
        " secret 5 $1$abc$xyzhash\n"
        "Chassis Serial Number : FOC1234ABCD\n"
    )

    assert "lab-pass-123" not in scrubbed
    assert "$1$abc$xyzhash" not in scrubbed
    assert "FOC1234ABCD" not in scrubbed
    # The surrounding structure survives, so a scrubbed fixture is still parseable.
    assert "username admin password [SCRUBBED]" in scrubbed


def test_scrub_output_leaves_operational_data_alone():
    """Over-scrubbing would silently destroy the signal these fixtures exist for."""

    original = (
        "Neighbor        Spk    AS MsgRcvd MsgSent   TblVer  InQ OutQ  Up/Down  St/PfxRcd\n"
        "10.255.0.31       0 65000     959     946        6    0    0 15:35:29          0\n"
        "P2             Gi0/0/0/1        *PtoP*         Up    21       L2   Capable\n"
    )

    assert scrub_output(original) == original


@pytest.mark.parametrize("label", QUIET_PAIR_LABELS)
def test_fixtures_are_complete_for_every_device(label):
    """Guard against a half-finished recapture landing in the repo."""

    for device_name in DEVICES:
        for command in ALL_COMMANDS:
            path = FIXTURE_DIR / "cisco_xr" / device_name / label / f"{command_slug(command)}.txt"
            assert path.is_file(), f"missing fixture: {path}"
            assert path.read_text(encoding="utf-8").strip(), f"empty fixture: {path}"


def test_replay_reconstructs_a_full_evidence_bundle(monkeypatch):
    """Replay goes through the sender= seam, so it exercises the real envelope
    construction and section slicing rather than a parallel implementation."""

    set_device_environment(monkeypatch)

    evidence = load_fixture_evidence("PE1", label="t0", base_dir=str(FIXTURE_DIR))

    assert evidence["device"] == "PE1"
    for section, commands in EVIDENCE_COMMANDS.items():
        assert evidence[section]["status"] == "success"
        assert list(evidence[section]["data"]["commands"]) == commands
    # Real captured output, not a synthetic echo.
    assert "Cisco IOS XR Software" in evidence["facts"]["data"]["commands"]["show version"]


def test_missing_fixture_surfaces_as_a_structured_error(monkeypatch):
    set_device_environment(monkeypatch)

    evidence = load_fixture_evidence("PE1", label="does-not-exist", base_dir=str(FIXTURE_DIR))

    for section in EVIDENCE_COMMANDS:
        assert evidence[section]["status"] == "error"


@pytest.mark.parametrize("device_name", sorted(DEVICES))
def test_quiet_fabric_pair_diffs_every_command_today(monkeypatch, device_name):
    """Pins the current diff defect against real data.

    Every IOS-XR show command prefixes its output with the current timestamp
    (``Wed Jul 29 11:59:09.555 UTC``), and several add their own moving fields:

    ==========================================  ====================================
    command                                     volatile beyond the timestamp
    ==========================================  ====================================
    show version                                ``<host> uptime is ...``
    show bgp summary                            ``MsgRcvd``/``MsgSent``, ``Up/Down``
    show isis neighbors                         ``Holdtime`` countdown
    show segment-routing traffic-eng policy     ``up for``/``down for`` durations
    show running-config hostname                (timestamp only)
    show interfaces brief                       (timestamp only)
    show lldp neighbors                         (timestamp only -- ``Hold-time``
                                                is the advertised TTL and is stable)
    ==========================================  ====================================

    Because ``diff_evidence`` compares whole command strings, all 7 commands are
    reported as changed on a fabric where nothing actually happened: a 100%
    false-positive rate with no true negatives.

    **Phase 2 inverts this test** -- ``changed`` becomes empty and ``unchanged``
    becomes all 7 commands. It is written to fail loudly when that lands.
    """

    set_device_environment(monkeypatch)
    old, new = load_pair(device_name)

    diff = diff_evidence(old, new)

    assert sorted(diff["changed"]) == sorted(ALL_COMMANDS)
    assert diff["unchanged"] == []
    # The noise is purely volatile fields -- nothing appeared, vanished, or failed.
    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["failed"] == []
    assert diff["recovered"] == []


def test_capture_reports_a_failed_command_instead_of_writing_it(monkeypatch, tmp_path):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch, fail_commands={"show bgp summary"})

    result = capture_device("PE1", label="t0", base_dir=str(tmp_path))

    assert result["errors"], "a partial capture must not look clean"
    assert any("show bgp summary" in error for error in result["errors"])
    written = {path.rsplit("/", 1)[-1] for path in result["written"]}
    assert "show-bgp-summary.txt" not in written
    assert "show-version.txt" in written


def test_capture_deduplicates_a_connection_level_failure(monkeypatch, tmp_path):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch, fail_connect=True)

    result = capture_device("PE1", label="t0", base_dir=str(tmp_path))

    # The same connection error is appended to all six sections; report it once.
    assert len(result["errors"]) == 1
    assert "connection to 172.20.250.21 failed" in result["errors"][0]
    assert result["written"] == []
