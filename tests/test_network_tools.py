from helpers import (
    LAB_PLATFORM,
    install_fake_netmiko,
    platform_commands,
    set_device_environment,
)

from agent_nettools import lab
from agent_nettools.network_tools import (
    _run_approved_commands,
    check_fabric,
    check_isis_neighbors,
    check_lldp_neighbors,
    check_sr_policies,
    collect_evidence,
    diff_evidence,
    list_devices,
    run_intent,
)
from agent_nettools.platforms import (
    APPROVED_COMMANDS,
    all_intents,
    commands_for,
    intents_for,
)

LAB_INTENTS = intents_for(LAB_PLATFORM)
LAB_COMMANDS = platform_commands(LAB_PLATFORM)


def test_list_devices_does_not_expose_credentials(monkeypatch):
    set_device_environment(monkeypatch)

    result = list_devices()

    assert result["status"] == "success"
    assert result["data"]["devices"][0]["name"] == "P1"
    assert "username" not in result["data"]["devices"][0]
    assert "password" not in result["data"]["devices"][0]


def test_refuses_unapproved_commands_before_loading_credentials():
    """No DEVICE_USERNAME/DEVICE_PASSWORD is set here on purpose: if the allowlist
    check ran after credential loading, this would raise InventoryError instead of
    refusing. That ordering is the safety invariant."""

    result = _run_approved_commands("PE1", ["configure"])

    assert result["status"] == "error"
    assert "Refusing unapproved commands" in result["errors"][0]


def test_refuses_another_platforms_command_without_credentials():
    """`show ip bgp summary` is approved -- for IOS-XE, not for this IOS-XR device.
    The refusal must still happen before any credential access."""

    result = _run_approved_commands("PE1", ["show ip bgp summary"])

    assert result["status"] == "error"
    assert "Refusing unapproved commands for cisco_xr" in result["errors"][0]
    assert "show ip bgp summary" in result["errors"][0]


def test_unsupported_intent_is_not_an_error(monkeypatch):
    """A platform with no commands for an intent reports "unsupported", which is
    a property of the fabric rather than a failure."""

    set_device_environment(monkeypatch)
    # Junos has no "sr" intent in the platform table.
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "juniper_junos")

    result = run_intent("PE1", "sr")

    assert result["status"] == "unsupported"
    assert result["errors"] == []
    assert result["data"]["commands"] == {}
    assert result["data"]["platform"] == "juniper_junos"


def test_unknown_platform_fails_closed(monkeypatch):
    set_device_environment(monkeypatch)
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "nonexistent_os")

    result = run_intent("PE1", "bgp")

    assert result["status"] == "error"
    assert "No command definitions for platform" in result["errors"][0]


def test_intent_resolves_to_platform_specific_syntax(monkeypatch):
    """The same intent must produce different commands on different vendors --
    otherwise the abstraction is decorative."""

    set_device_environment(monkeypatch)
    seen = []

    def recording_sender(device, command):
        seen.append(command)
        return "output"

    run_intent("PE1", "bgp", sender=recording_sender)
    assert seen == ["show bgp summary"]

    seen.clear()
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "cisco_iosxe")
    run_intent("PE1", "bgp", sender=recording_sender)
    assert seen == ["show ip bgp summary"]

    seen.clear()
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "juniper_junos")
    run_intent("PE1", "isis", sender=recording_sender)
    assert seen == ["show isis adjacency"]


def test_fabric_stays_green_when_a_vendor_cannot_answer(monkeypatch):
    set_device_environment(monkeypatch)
    monkeypatch.setitem(lab.PLATFORMS, "PE2", "juniper_junos")

    result = check_fabric("sr", sender=lambda device, command: "output")

    assert result["status"] == "success"
    assert result["data"]["unsupported"] == ["PE2"]
    assert result["data"]["devices"]["PE2"]["status"] == "unsupported"
    assert result["data"]["devices"]["PE1"]["status"] == "success"


def test_collect_evidence_marks_unsupported_intents(monkeypatch):
    """Evidence keeps one section per known intent regardless of platform, so the
    shape is identical across a mixed fabric."""

    set_device_environment(monkeypatch)
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "juniper_junos")

    evidence = collect_evidence("PE1", sender=lambda device, command: f"out: {command}")

    assert evidence["platform"] == "juniper_junos"
    assert evidence["sr"]["status"] == "unsupported"
    assert evidence["isis"]["status"] == "success"
    assert list(evidence["isis"]["data"]["commands"]) == ["show isis adjacency"]
    # Same key set as an IOS-XR collection.
    assert set(all_intents()) <= set(evidence)


def test_injected_sender_path(monkeypatch):
    set_device_environment(monkeypatch)

    def test_sender(device, command):
        return f"output from {device['name']} for {command}"

    result = _run_approved_commands("PE1", ["show version"], sender=test_sender)

    assert result["status"] == "success"
    assert "show version" in result["data"]["commands"]


def test_new_commands_are_approved():
    assert "show lldp neighbors" in APPROVED_COMMANDS[LAB_PLATFORM]
    assert "show isis neighbors" in APPROVED_COMMANDS[LAB_PLATFORM]
    assert "show segment-routing traffic-eng policy" in APPROVED_COMMANDS[LAB_PLATFORM]


def test_lldp_isis_sr_tools_use_approved_commands(monkeypatch):
    set_device_environment(monkeypatch)

    def echo_sender(device, command):
        return f"{command} on {device['name']}"

    # Each tool accepts the injected sender directly, so its exact command is
    # exercised end to end without live SSH.
    for tool, command in (
        (check_lldp_neighbors, "show lldp neighbors"),
        (check_isis_neighbors, "show isis neighbors"),
        (check_sr_policies, "show segment-routing traffic-eng policy"),
    ):
        result = tool("PE1", sender=echo_sender)
        assert result["status"] == "success"
        assert command in result["data"]["commands"]


def test_check_fabric_runs_named_check_across_all_devices(monkeypatch):
    set_device_environment(monkeypatch)

    def echo_sender(device, command):
        return f"{command} on {device['name']}"

    result = check_fabric("lldp", sender=echo_sender)

    assert result["status"] == "success"
    assert result["data"]["check"] == "lldp"
    devices = result["data"]["devices"]
    assert list(devices) == ["P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"]
    assert devices["PE1"]["status"] == "success"


def test_check_fabric_rejects_unknown_check(monkeypatch):
    set_device_environment(monkeypatch)
    result = check_fabric("reload")
    assert result["status"] == "error"
    assert "Unknown check" in result["errors"][0]


def test_diff_evidence_reports_changed_commands():
    old = {
        "device": "PE1",
        "timestamp": "t0",
        "bgp": {"data": {"commands": {"show bgp summary": "Idle"}}},
        "isis": {"data": {"commands": {"show isis neighbors": "same"}}},
    }
    new = {
        "device": "PE1",
        "timestamp": "t1",
        "bgp": {"data": {"commands": {"show bgp summary": "Established"}}},
        "isis": {"data": {"commands": {"show isis neighbors": "same"}}},
    }

    diff = diff_evidence(old, new)

    assert diff["changed"] == ["show bgp summary"]
    assert diff["unchanged"] == ["show isis neighbors"]
    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["failed"] == []
    assert diff["recovered"] == []


def test_diff_evidence_separates_failures_from_removals():
    healthy_bgp = {"status": "success", "data": {"commands": {"show bgp summary": "Established"}}}
    failed_bgp = {"status": "error", "data": {"commands": {}}, "errors": ["show bgp summary: boom"]}

    # BGP failed in the new run: not "removed", reported as "failed".
    diff = diff_evidence(
        {"device": "PE1", "bgp": healthy_bgp},
        {"device": "PE1", "bgp": failed_bgp},
    )
    assert diff["failed"] == ["show bgp summary"]
    assert diff["removed"] == []

    # BGP failed in the old run and is back: not "added", reported as "recovered".
    diff = diff_evidence(
        {"device": "PE1", "bgp": failed_bgp},
        {"device": "PE1", "bgp": healthy_bgp},
    )
    assert diff["recovered"] == ["show bgp summary"]
    assert diff["added"] == []


def test_snapshot_round_trip_honors_evidence_dir(monkeypatch, tmp_path):
    from agent_nettools.network_tools import load_latest_snapshot, save_snapshot

    monkeypatch.setenv("NETTOOLS_EVIDENCE_DIR", str(tmp_path))

    evidence = {"device": "PE1", "timestamp": "t0"}
    path = save_snapshot(evidence)

    assert path.startswith(str(tmp_path))
    assert load_latest_snapshot("PE1") == evidence


def test_collect_evidence_uses_one_ssh_session(monkeypatch):
    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    evidence = collect_evidence("PE1")

    # One login for the whole check, not one per command.
    assert len(sessions) == 1
    assert sessions[0]["host"] == "172.20.250.21"
    assert sessions[0]["device_type"] == "cisco_xr"

    assert set(LAB_INTENTS) <= set(evidence)
    assert evidence["platform"] == LAB_PLATFORM
    for intent in LAB_INTENTS:
        assert evidence[intent]["status"] == "success"
        assert list(evidence[intent]["data"]["commands"]) == list(
            commands_for(LAB_PLATFORM, intent)
        )


def test_collect_evidence_isolates_a_failed_command(monkeypatch):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch, fail_commands={"show bgp summary"})

    evidence = collect_evidence("PE1")

    assert evidence["bgp"]["status"] == "error"
    assert "show bgp summary" in evidence["bgp"]["errors"][0]
    # Sections whose commands ran are unaffected.
    assert evidence["interfaces"]["status"] == "success"
    assert evidence["facts"]["status"] == "success"


def test_collect_evidence_reports_a_connection_failure_everywhere(monkeypatch):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch, fail_connect=True)

    evidence = collect_evidence("PE1")

    for intent in LAB_INTENTS:
        assert evidence[intent]["status"] == "error"
        assert "connection to 172.20.250.21 failed" in evidence[intent]["errors"][0]


def test_collect_evidence_accepts_an_injected_sender(monkeypatch):
    set_device_environment(monkeypatch)

    def echo_sender(device, command):
        return f"{command} on {device['name']}"

    evidence = collect_evidence("PE1", sender=echo_sender)

    assert evidence["device"] == "PE1"
    assert evidence["lldp"]["data"]["commands"]["show lldp neighbors"] == (
        "show lldp neighbors on PE1"
    )


def test_evidence_commands_are_all_approved():
    for command in LAB_COMMANDS:
        assert command in APPROVED_COMMANDS[LAB_PLATFORM]


def test_fabric_default_check_is_bgp(monkeypatch):
    set_device_environment(monkeypatch)

    def bgp_sender(device, command):
        return f"BGP summary for {device['name']}"

    result = check_fabric(sender=bgp_sender)

    assert result["status"] == "success"
    assert result["data"]["check"] == "bgp"
    devices = result["data"]["devices"]
    # Every inventory device should appear with its own BGP output.
    assert set(devices) == {"P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"}
    assert devices["PE1"]["data"]["commands"]["show bgp summary"] == "BGP summary for PE1"
