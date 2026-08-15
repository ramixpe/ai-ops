"""Tests for fixture capture, scrubbing, and offline replay.

The committed fixtures under ``tests/fixtures`` are real IOS-XR output captured
from the lab as two snapshots ~90s apart (labels ``t0`` and ``t1``) on an
otherwise quiet fabric. They are the ground truth for parser, diff, and
health-rule work, none of which needs lab access to develop against.
"""

from __future__ import annotations

import copy

import pytest
from helpers import (
    FIXTURE_DIR,
    LAB_PLATFORM,
    install_fake_netmiko,
    platform_commands,
    set_device_environment,
)

from agent_nettools import parsers
from agent_nettools.fixtures import (
    QUIET_PAIR_LABELS,
    capture_device,
    command_slug,
    load_fixture_evidence,
    scrub_output,
)
from agent_nettools.lab import DEVICES
from agent_nettools.network_tools import diff_evidence
from agent_nettools.platforms import commands_for, intents_for

ALL_COMMANDS = platform_commands(LAB_PLATFORM)
LAB_INTENTS = intents_for(LAB_PLATFORM)


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
            path = FIXTURE_DIR / LAB_PLATFORM / device_name / label / f"{command_slug(command)}.txt"
            assert path.is_file(), f"missing fixture: {path}"
            assert path.read_text(encoding="utf-8").strip(), f"empty fixture: {path}"


def test_replay_reconstructs_a_full_evidence_bundle(monkeypatch):
    """Replay goes through the sender= seam, so it exercises the real envelope
    construction and section slicing rather than a parallel implementation."""

    set_device_environment(monkeypatch)

    evidence = load_fixture_evidence("PE1", label="t0", base_dir=str(FIXTURE_DIR))

    assert evidence["device"] == "PE1"
    assert evidence["platform"] == LAB_PLATFORM
    for intent in LAB_INTENTS:
        assert evidence[intent]["status"] == "success"
        assert list(evidence[intent]["data"]["commands"]) == list(
            commands_for(LAB_PLATFORM, intent)
        )
    # Real captured output, not a synthetic echo.
    assert "Cisco IOS XR Software" in evidence["facts"]["data"]["commands"]["show version"]


def test_missing_fixture_surfaces_as_a_structured_error(monkeypatch):
    set_device_environment(monkeypatch)

    evidence = load_fixture_evidence("PE1", label="does-not-exist", base_dir=str(FIXTURE_DIR))

    for intent in LAB_INTENTS:
        assert evidence[intent]["status"] == "error"


@pytest.mark.parametrize("device_name", sorted(DEVICES))
def test_quiet_fabric_pair_reports_no_change(monkeypatch, device_name):
    """Proves the Phase 2 fix against real data: a quiet fabric now diffs clean.

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

    Diffing at the intent level fixes this two ways: the parsers (``parsers.py``)
    exclude each intent's ``VOLATILE_FIELDS`` from record/meta comparison, and the
    normalized-text fallback (``normalize.py``) strips the timestamp preamble and
    masks the same moving fields in place. Either path used alone on this fixture
    pair reports zero real change -- this test is the proof: every intent this
    platform's parsers can read lands in ``unchanged``, none in ``changed``, and
    nothing appeared, vanished, or failed.
    """

    set_device_environment(monkeypatch)
    old, new = load_pair(device_name)

    diff = diff_evidence(old, new)

    assert diff["changed"] == []
    assert sorted(diff["unchanged"]) == sorted(LAB_INTENTS)
    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["failed"] == []
    assert diff["recovered"] == []
    assert diff["unsupported"] == []


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


def load_evidence(device_name, label):
    """Full evidence (parsed, per Task 2 wiring) from a committed fixture."""

    return load_fixture_evidence(device_name, label=label, base_dir=str(FIXTURE_DIR))


def test_parse_xr_facts_from_real_fixture(monkeypatch):
    set_device_environment(monkeypatch)
    evidence = load_evidence("PE1", "t0")

    assert evidence["facts"]["data"]["parse_status"] == parsers.PARSE_OK
    meta = evidence["facts"]["data"]["parsed"]["meta"]
    assert meta["hostname"] == "PE1"
    assert meta["version"] == "7.11.2"


def test_parse_xr_bgp_from_real_fixture(monkeypatch):
    set_device_environment(monkeypatch)
    evidence = load_evidence("PE1", "t0")

    parsed = evidence["bgp"]["data"]["parsed"]
    assert parsed["meta"]["router_id"] == "10.255.0.11"
    assert parsed["meta"]["local_as"] == "65000"
    assert any(record["neighbor"] == "10.255.0.31" for record in parsed["records"])


def test_parse_xr_interfaces_from_real_fixture(monkeypatch):
    set_device_environment(monkeypatch)
    evidence = load_evidence("PE1", "t0")

    by_name = {r["interface"]: r for r in evidence["interfaces"]["data"]["parsed"]["records"]}
    assert by_name["Gi0/0/0/2.300"]["line_protocol"] == "down"


def test_parse_xr_sr_from_real_fixture(monkeypatch):
    set_device_environment(monkeypatch)
    evidence = load_evidence("PE1", "t0")

    records = evidence["sr"]["data"]["parsed"]["records"]
    assert any(record["operational_state"] == "down" for record in records)


@pytest.mark.parametrize("platform_intent", sorted(parsers.PARSERS))
def test_every_parser_fails_on_garbage_input_without_raising(platform_intent):
    """Pins the strictness contract from every parser's own docstring: a parser
    that returns nothing from non-empty, unrecognized input must report
    PARSE_FAILED rather than a silent empty success (the ntc-templates failure
    mode ``parsers.py`` exists to avoid)."""

    platform, intent = platform_intent
    garbage = {"cmd": "this is not a recognized command output\njust some words\n"}

    parsed, status = parsers.parse_intent(platform, intent, garbage)

    assert status == parsers.PARSE_FAILED
    assert parsed is None


def test_parser_exception_is_caught_not_propagated(monkeypatch):
    """A parser bug must never break collection -- parse_intent's broad except
    turns any exception into PARSE_FAILED."""

    def exploding_parser(outputs):
        raise RuntimeError("parser bug")

    monkeypatch.setitem(parsers.PARSERS, ("cisco_xr", "bgp"), exploding_parser)

    parsed, status = parsers.parse_intent("cisco_xr", "bgp", {"show bgp summary": "some output"})

    assert status == parsers.PARSE_FAILED
    assert parsed is None


def test_diff_detects_a_real_non_volatile_change(monkeypatch):
    """A change to a field outside VOLATILE_FIELDS must be reported as real."""

    set_device_environment(monkeypatch)
    old = load_evidence("PE1", "t0")
    new = copy.deepcopy(old)

    neighbor = new["bgp"]["data"]["parsed"]["records"][0]
    assert neighbor["neighbor"] == "10.255.0.31"
    neighbor["state_pfx_rcd"] = "12"  # was "0" -- a real prefix-count change.

    diff = diff_evidence(old, new)

    assert "bgp" in diff["changed"]
    entry = diff["details"]["bgp"]
    assert entry["compared_via"] == "parsed"
    assert entry["changed_records"] == [
        {"key": "10.255.0.31", "changes": {"state_pfx_rcd": {"old": "0", "new": "12"}}}
    ]


def test_diff_ignores_a_volatile_only_change(monkeypatch):
    """MsgRcvd/Up-Down move on keepalives alone and must not be reported."""

    set_device_environment(monkeypatch)
    old = load_evidence("PE1", "t0")
    new = copy.deepcopy(old)

    neighbor = new["bgp"]["data"]["parsed"]["records"][0]
    neighbor["msg_rcvd"] = "99999"
    neighbor["up_down"] = "00:00:01"

    diff = diff_evidence(old, new)

    assert "bgp" not in diff["changed"]
    assert "bgp" in diff["unchanged"]
    assert diff["details"]["bgp"]["changed_records"] == []


def test_diff_falls_back_to_normalized_text_when_parsing_unavailable(monkeypatch):
    """Forcing both sides to PARSE_FAILED must not break the quiet-fabric pair:
    the normalized-text fallback masks the same volatile fields the parser
    excludes, so it independently agrees there is no real change."""

    set_device_environment(monkeypatch)
    old, new = load_pair("PE1")

    for evidence in (old, new):
        evidence["bgp"]["data"]["parsed"] = None
        evidence["bgp"]["data"]["parse_status"] = parsers.PARSE_FAILED

    diff = diff_evidence(old, new)

    assert diff["details"]["bgp"]["compared_via"] == "normalized_text"
    assert "bgp" not in diff["changed"]
    assert "bgp" in diff["unchanged"]


# --------------------------------------------------------------------------- #
# Template capture (T-011)
# --------------------------------------------------------------------------- #


def test_template_manifest_skips_the_devices_own_loopback():
    """A device is never its own BGP peer."""

    from agent_nettools.fixtures import template_manifest_for

    manifest = template_manifest_for("RR1", interfaces=[], router_id="10.255.0.31")
    addresses = [p.get("address") for _n, p in manifest if _n == "bgp_neighbor"]

    assert "10.255.0.31" not in addresses
    assert len(addresses) == 4


def test_template_manifest_probes_a_different_target_from_rr1():
    """RR1 cannot usefully ping itself, so it probes PE1 instead."""

    from agent_nettools.fixtures import template_manifest_for

    rr1 = dict(template_manifest_for("RR1", interfaces=[], router_id="10.255.0.31"))
    pe1 = dict(template_manifest_for("PE1", interfaces=[], router_id="10.255.0.11"))

    assert rr1["ping"] == {"address": "10.255.0.11"}
    assert pe1["ping"] == {"address": "10.255.0.31"}


def test_capturable_interfaces_ignores_dynamic_tunnels():
    """srte_* tunnels are dynamic; capturing them would churn the fixture set
    for reasons unrelated to any fault."""

    from agent_nettools import parsers
    from agent_nettools.fixtures import _capturable_interfaces

    evidence = {
        "interfaces": {
            "data": {
                "parse_status": parsers.PARSE_OK,
                "parsed": {
                    "records": [
                        {"interface": "Lo0"},
                        {"interface": "Lo100"},
                        {"interface": "Gi0/0/0/0"},
                        {"interface": "Gi0/0/0/1"},
                        {"interface": "Nu0"},
                        {"interface": "srte_c_10_ep"},
                    ]
                },
            }
        }
    }

    assert _capturable_interfaces(evidence) == ["Lo0", "Gi0/0/0/0", "Gi0/0/0/1"]


def test_capturable_interfaces_is_empty_when_the_parse_failed():
    """A failed parse must not silently yield an empty manifest that looks
    like a device with no interfaces."""

    from agent_nettools import parsers
    from agent_nettools.fixtures import _capturable_interfaces

    assert _capturable_interfaces({}) == []
    assert (
        _capturable_interfaces(
            {"interfaces": {"data": {"parse_status": parsers.PARSE_FAILED, "parsed": None}}}
        )
        == []
    )


def test_capture_device_without_templates_is_unchanged(monkeypatch, tmp_path):
    """The default must be exactly the pre-T-011 behaviour."""

    from agent_nettools.fixtures import capture_device

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    result = capture_device("PE1", label="unit", base_dir=str(tmp_path))

    assert len(sessions) == 1
    assert all("show-bgp-neighbor" not in w for w in result["written"])


def test_capture_device_with_templates_opens_two_sessions_not_fifteen(monkeypatch, tmp_path):
    """One session for the intents, one for the whole template batch.

    The number that matters is that it does not scale with the manifest --
    OBS-027 measured fourteen logins for fourteen templates.
    """

    from agent_nettools.fixtures import capture_device

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    result = capture_device("PE1", label="unit", base_dir=str(tmp_path), templates=True)

    assert len(sessions) == 2
    written = [w.rsplit("/", 1)[-1] for w in result["written"]]
    assert "show-bgp-neighbor-10-255-0-12.txt" in written
    assert "show-route-10-255-0-12-32.txt" in written
    assert "show-logging-last-200.txt" in written


def test_captured_template_files_replay_through_the_existing_sender(monkeypatch, tmp_path):
    """The read path needs no change: run_template with a fixture sender finds
    the file the capture wrote, because both key on the rendered command."""

    from agent_nettools.fixtures import capture_device, fixture_sender
    from agent_nettools.network_tools import run_template

    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)
    capture_device("PE1", label="unit", base_dir=str(tmp_path), templates=True)

    replayed = run_template(
        "PE1",
        "bgp_neighbor",
        address="10.255.0.12",
        sender=fixture_sender(label="unit", base_dir=str(tmp_path)),
    )

    assert replayed["status"] == "success"
    assert "show bgp neighbor 10.255.0.12" in replayed["data"]["commands"]
