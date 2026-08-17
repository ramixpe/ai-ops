import json
import threading

import pytest
from helpers import (
    LAB_PLATFORM,
    install_fake_netmiko,
    platform_commands,
    set_device_environment,
)

from agent_nettools import lab, network_tools
from agent_nettools.network_tools import (
    NETTOOLS_ALLOW_ACTIVE_PROBES_ENV,
    _run_approved_commands,
    check_fabric,
    check_isis_neighbors,
    check_lldp_neighbors,
    check_sr_policies,
    collect_evidence,
    diff_evidence,
    get_bgp_neighbor,
    get_interface,
    get_logging,
    get_route,
    iter_fabric,
    list_devices,
    ping_device,
    run_intent,
    run_template,
    traceroute_device,
)
from agent_nettools.platforms import (
    APPROVED_COMMANDS,
    TemplateValidationError,
    all_intents,
    commands_for,
    intents_for,
    render_command,
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


# --------------------------------------------------------------------------- #
# run_template: validated, parameterized commands (Phase 5).
# --------------------------------------------------------------------------- #


def test_run_template_refuses_a_bad_parameter_before_loading_credentials():
    """The Phase 5 equivalent of
    test_refuses_unapproved_commands_before_loading_credentials: no
    DEVICE_USERNAME/DEVICE_PASSWORD is set here on purpose. If parameter
    validation ran after credential loading, this would raise InventoryError
    instead of a clean structured refusal -- that ordering is the invariant
    Phase 5 must not break."""

    result = run_template("PE1", "bgp_neighbor", address="10.0.0.1; configure")

    assert result["status"] == "error"
    assert "bgp_neighbor" in result["errors"][0] or "address" in result["errors"][0]


def test_run_template_renders_and_sends_the_expected_command(monkeypatch):
    set_device_environment(monkeypatch)
    seen = []

    def recording_sender(device, command):
        seen.append(command)
        return f"output for {command}"

    result = run_template("PE1", "route", prefix="10.255.0.31", sender=recording_sender)

    assert result["status"] == "success"
    assert seen == ["show route 10.255.0.31/32"]
    assert result["data"]["command"] == "show route 10.255.0.31/32"
    # Same "commands" shape run_intent produces, so generic consumers that walk
    # data.commands do not silently skip template results.
    assert result["data"]["commands"] == {
        "show route 10.255.0.31/32": "output for show route 10.255.0.31/32"
    }


def test_run_template_resolves_to_platform_specific_syntax(monkeypatch):
    """Same template name, different vendor syntax -- the parameterized
    equivalent of test_intent_resolves_to_platform_specific_syntax."""

    set_device_environment(monkeypatch)
    seen = []

    def recording_sender(device, command):
        seen.append(command)
        return "output"

    run_template("PE1", "bgp_neighbor", address="10.255.0.31", sender=recording_sender)
    assert seen == ["show bgp neighbor 10.255.0.31"]

    seen.clear()
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "cisco_iosxe")
    run_template("PE1", "bgp_neighbor", address="10.255.0.31", sender=recording_sender)
    assert seen == ["show ip bgp neighbors 10.255.0.31"]


def test_run_template_unknown_template_is_unsupported_not_an_error(monkeypatch):
    set_device_environment(monkeypatch)
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "juniper_junos")

    result = run_template("PE1", "route", prefix="10.0.0.0/24")

    assert result["status"] == "unsupported"
    assert result["errors"] == []
    assert result["data"]["template"] == "route"
    assert result["data"]["platform"] == "juniper_junos"


def test_run_template_unknown_platform_fails_closed(monkeypatch):
    set_device_environment(monkeypatch)
    monkeypatch.setitem(lab.PLATFORMS, "PE1", "nonexistent_os")

    result = run_template("PE1", "route", prefix="10.0.0.0/24")

    assert result["status"] == "error"
    assert "No command definitions for platform" in result["errors"][0]


def test_run_template_rejects_a_bad_parameter_with_a_sender_configured(monkeypatch):
    """A sender being available must not bypass parameter validation."""

    set_device_environment(monkeypatch)

    def failing_sender(device, command):
        raise AssertionError("sender must never be called for a rejected parameter")

    result = run_template("PE1", "bgp_neighbor", address="not-an-ip", sender=failing_sender)

    assert result["status"] == "error"


def test_run_template_raising_from_render_command_is_caught(monkeypatch):
    """render_command's TemplateValidationError must be translated to a
    structured error, not propagate as a raw exception."""

    set_device_environment(monkeypatch)
    with pytest.raises(TemplateValidationError):
        # Direct call to prove the underlying function does raise ...
        render_command("cisco_xr", "bgp_neighbor", address="not-an-ip")

    # ... but run_template itself must never leak that exception to the caller.
    result = run_template("PE1", "bgp_neighbor", address="not-an-ip", sender=lambda d, c: "x")
    assert result["status"] == "error"
    assert isinstance(result["errors"][0], str)


def test_ping_and_traceroute_are_active_probes_gated_by_environment(monkeypatch):
    set_device_environment(monkeypatch)

    def recording_sender(device, command):
        return f"output for {command}"

    # Enabled by default (unset).
    result = ping_device("PE1", "10.255.0.31", sender=recording_sender)
    assert result["status"] == "success"
    assert result["data"]["command"] == "ping 10.255.0.31"

    result = traceroute_device("PE1", "10.255.0.31", sender=recording_sender)
    assert result["status"] == "success"
    assert result["data"]["command"] == "traceroute 10.255.0.31"

    # Explicitly disabled.
    monkeypatch.setenv(NETTOOLS_ALLOW_ACTIVE_PROBES_ENV, "0")
    result = ping_device("PE1", "10.255.0.31", sender=recording_sender)
    assert result["status"] == "error"
    assert "Active probes" in result["errors"][0]

    result = traceroute_device("PE1", "10.255.0.31", sender=recording_sender)
    assert result["status"] == "error"
    assert "Active probes" in result["errors"][0]

    # A non-active-probe template must be unaffected by the switch.
    result = get_route("PE1", "10.0.0.0/24", sender=recording_sender)
    assert result["status"] == "success"


def test_active_probe_gate_accepts_common_falsy_spellings(monkeypatch):
    set_device_environment(monkeypatch)

    def recording_sender(device, command):
        return "output"

    for falsy in ("0", "false", "False", "no", "NO", "off"):
        monkeypatch.setenv(NETTOOLS_ALLOW_ACTIVE_PROBES_ENV, falsy)
        result = ping_device("PE1", "10.255.0.31", sender=recording_sender)
        assert result["status"] == "error", f"{falsy!r} should disable active probes"

    for truthy in ("1", "true", "yes", "anything-else"):
        monkeypatch.setenv(NETTOOLS_ALLOW_ACTIVE_PROBES_ENV, truthy)
        result = ping_device("PE1", "10.255.0.31", sender=recording_sender)
        assert result["status"] == "success", f"{truthy!r} should leave active probes enabled"


def test_get_route_bgp_neighbor_interface_logging_use_the_expected_commands(monkeypatch):
    set_device_environment(monkeypatch)

    def recording_sender(device, command):
        return f"output for {command}"

    assert get_route("PE1", "10.255.0.31", sender=recording_sender)["data"]["command"] == (
        "show route 10.255.0.31/32"
    )
    assert get_bgp_neighbor("PE1", "10.255.0.31", sender=recording_sender)["data"][
        "command"
    ] == "show bgp neighbor 10.255.0.31"
    assert get_interface("PE1", "GigabitEthernet0/0/0/1", sender=recording_sender)["data"][
        "command"
    ] == "show interfaces GigabitEthernet0/0/0/1"
    assert get_logging("PE1", 20, sender=recording_sender)["data"]["command"] == (
        "show logging last 20"
    )


def test_run_template_uses_one_command_per_call_not_a_batched_session(monkeypatch):
    """Unlike collect_evidence, a template call is a single command -- it must
    not be batched with anything else."""

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    result = run_template("PE1", "bgp_neighbor", address="10.255.0.31")

    assert result["status"] == "success"
    assert len(sessions) == 1


def test_diff_evidence_reports_changed_intents():
    """No parser is wired for these hand-built sections (no "parse_status"), so
    the comparison falls back to normalized text -- exercised on its own merits
    by the fixture-backed tests below."""

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

    assert diff["changed"] == ["bgp"]
    assert diff["unchanged"] == ["isis"]
    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["failed"] == []
    assert diff["recovered"] == []
    assert diff["unsupported"] == []
    assert diff["details"]["bgp"]["compared_via"] == "normalized_text"
    assert diff["details"]["bgp"]["changed_records"] == []
    assert diff["details"]["isis"]["compared_via"] == "normalized_text"


def test_diff_evidence_separates_failures_from_removals():
    healthy_bgp = {"status": "success", "data": {"commands": {"show bgp summary": "Established"}}}
    failed_bgp = {"status": "error", "data": {"commands": {}}, "errors": ["show bgp summary: boom"]}

    # BGP failed in the new run: not "removed", reported as "failed".
    diff = diff_evidence(
        {"device": "PE1", "bgp": healthy_bgp},
        {"device": "PE1", "bgp": failed_bgp},
    )
    assert diff["failed"] == ["bgp"]
    assert diff["removed"] == []

    # BGP failed in the old run and is back: not "added", reported as "recovered".
    diff = diff_evidence(
        {"device": "PE1", "bgp": failed_bgp},
        {"device": "PE1", "bgp": healthy_bgp},
    )
    assert diff["recovered"] == ["bgp"]
    assert diff["added"] == []


def test_diff_evidence_removed_and_added_intents():
    """An intent present-and-healthy in only one snapshot (not because it
    errored) is "removed" or "added" -- distinct from the failed/recovered
    pair above, which is specifically about transient errors."""

    healthy_bgp = {"status": "success", "data": {"commands": {"show bgp summary": "Established"}}}

    diff = diff_evidence({"device": "PE1", "bgp": healthy_bgp}, {"device": "PE1"})
    assert diff["removed"] == ["bgp"]
    assert diff["failed"] == []

    diff = diff_evidence({"device": "PE1"}, {"device": "PE1", "bgp": healthy_bgp})
    assert diff["added"] == ["bgp"]
    assert diff["recovered"] == []


def test_diff_evidence_unsupported_intent_is_its_own_bucket():
    """An intent unsupported on the current platform is neither failed nor
    removed nor changed -- it is a fact about the fabric, not a diff outcome."""

    healthy_bgp = {"status": "success", "data": {"commands": {"show bgp summary": "Established"}}}
    unsupported_bgp = {"status": "unsupported", "data": {"intent": "bgp", "commands": {}}}

    diff = diff_evidence(
        {"device": "PE1", "bgp": healthy_bgp},
        {"device": "PE1", "bgp": unsupported_bgp},
    )
    assert diff["unsupported"] == ["bgp"]
    assert diff["failed"] == []
    assert diff["removed"] == []
    assert diff["changed"] == []
    assert "bgp" not in diff["details"]


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


def test_golden_snapshot_is_pinned_separately_from_timestamped_history(monkeypatch, tmp_path):
    from agent_nettools.network_tools import (
        load_golden_snapshot,
        load_latest_snapshot,
        save_golden_snapshot,
        save_snapshot,
    )

    monkeypatch.setenv("NETTOOLS_EVIDENCE_DIR", str(tmp_path))

    first = {"device": "PE1", "timestamp": "t0"}
    save_snapshot(first)
    golden = {"device": "PE1", "timestamp": "golden-capture"}
    golden_path = save_golden_snapshot(golden)
    second = {"device": "PE1", "timestamp": "t1"}
    save_snapshot(second)

    # The golden file sorts after every ISO timestamp lexicographically ("g" >
    # any digit); if it were not excluded from the timestamped glob it would
    # wrongly become "latest".
    assert load_latest_snapshot("PE1") == second
    assert load_golden_snapshot("PE1") == golden
    assert golden_path.endswith("golden.json")


def test_load_golden_snapshot_returns_none_when_never_pinned(tmp_path):
    from agent_nettools.network_tools import load_golden_snapshot

    assert load_golden_snapshot("PE1", base_dir=str(tmp_path)) is None


def test_pinning_golden_twice_overwrites_the_previous_pin(tmp_path):
    from agent_nettools.network_tools import load_golden_snapshot, save_golden_snapshot

    save_golden_snapshot({"device": "PE1", "timestamp": "first"}, base_dir=str(tmp_path))
    save_golden_snapshot({"device": "PE1", "timestamp": "second"}, base_dir=str(tmp_path))

    assert load_golden_snapshot("PE1", base_dir=str(tmp_path)) == {
        "device": "PE1",
        "timestamp": "second",
    }


def _bgp_snapshot(state: str, *, up_down: str = "00:00:01") -> dict:
    """One synthetic snapshot with a single BGP peer at the given St/PfxRcd state.

    ``up_down`` is a volatile field (see ``parsers.VOLATILE_FIELDS``) included
    here specifically so a test can assert it is excluded from flap detection.
    """

    return {
        "device": "PE9",
        "platform": "cisco_xr",
        "timestamp": "irrelevant",
        "bgp": {
            "status": "success",
            "data": {
                "parse_status": "ok",
                "parsed": {
                    "meta": {"router_id": "10.0.0.9", "neighbor_count": 1},
                    "records": [
                        {"neighbor": "10.0.0.1", "state_pfx_rcd": state, "up_down": up_down}
                    ],
                },
            },
        },
    }


def _write_snapshot_at(tmp_path, device: str, index: int, evidence: dict) -> None:
    """Write one snapshot file with an explicit, order-preserving timestamp name.

    Bypasses ``save_snapshot``'s wall-clock timestamp so a tight test loop
    cannot flakily collide on filename -- history order only needs to be
    lexicographic, which a zero-padded index guarantees deterministically.
    """

    import json as _json

    directory = tmp_path / device
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"2026-01-01T00-00-{index:02d}.json").write_text(
        _json.dumps(evidence), encoding="utf-8"
    )


def test_detect_flaps_reports_an_oscillating_field(tmp_path):
    from agent_nettools.network_tools import detect_flaps

    # Idle/Established/Idle/Established/Idle: 4 transitions, well past the
    # default min_transitions=3 threshold.
    states = ["Idle", "5", "Idle", "5", "Idle"]
    for index, state in enumerate(states):
        _write_snapshot_at(tmp_path, "PE9", index, _bgp_snapshot(state, up_down=f"00:0{index}:00"))

    result = detect_flaps("PE9", base_dir=str(tmp_path))

    assert result["snapshots_examined"] == 5
    assert len(result["flapping"]) == 1
    entry = result["flapping"][0]
    assert entry["intent"] == "bgp"
    assert entry["subject"] == "10.0.0.1"
    assert entry["field"] == "state_pfx_rcd"
    assert entry["transitions"] == 4
    assert entry["values"] == states


def test_detect_flaps_ignores_volatile_fields(tmp_path):
    """up_down changes on every single snapshot in a healthy fabric -- it must
    never be reported as flapping."""

    from agent_nettools.network_tools import detect_flaps

    for index in range(5):
        _write_snapshot_at(tmp_path, "PE9", index, _bgp_snapshot("5", up_down=f"00:0{index}:00"))

    result = detect_flaps("PE9", base_dir=str(tmp_path))

    assert result["flapping"] == []


def test_detect_flaps_respects_min_transitions(tmp_path):
    from agent_nettools.network_tools import detect_flaps

    # Only 2 transitions: below the raised threshold.
    for index, state in enumerate(("Idle", "5", "Idle")):
        _write_snapshot_at(tmp_path, "PE9", index, _bgp_snapshot(state))

    assert detect_flaps("PE9", base_dir=str(tmp_path), min_transitions=3)["flapping"] == []
    assert len(detect_flaps("PE9", base_dir=str(tmp_path), min_transitions=2)["flapping"]) == 1


def test_detect_flaps_with_no_history_reports_nothing(tmp_path):
    from agent_nettools.network_tools import detect_flaps

    result = detect_flaps("NEVER_SEEN", base_dir=str(tmp_path))

    assert result == {"device": "NEVER_SEEN", "snapshots_examined": 0, "flapping": []}


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


# --------------------------------------------------------------------------- #
# Phase 7, Task 1: fabric checks resolve each device record once.
# --------------------------------------------------------------------------- #


def test_check_fabric_never_re_resolves_a_device_by_name(monkeypatch):
    """check_fabric resolves every device once via load_inventory() and threads
    the record down; get_device() (a by-name lookup) must never be called again
    for any of the per-device checks that follow."""

    set_device_environment(monkeypatch)

    def _must_not_be_called(_name):
        raise AssertionError("get_device() was called again after check_fabric's single resolve")

    monkeypatch.setattr(network_tools, "get_device", _must_not_be_called)

    result = check_fabric("facts", sender=lambda device, command: "output")

    assert result["status"] == "success"
    assert set(result["data"]["devices"]) == {
        "P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1",
    }


def test_run_intent_accepts_a_pre_resolved_device_and_skips_get_device(monkeypatch):
    """The same threading seam CHECK_TOOLS/check_fabric use, exercised directly:
    passing an already-resolved device record must bypass get_device() entirely,
    and the sender must receive that exact record."""

    def _must_not_be_called(_name):
        raise AssertionError("get_device() should not be called when device= is supplied")

    monkeypatch.setattr(network_tools, "get_device", _must_not_be_called)

    prebuilt = {
        "name": "PE1",
        "hostname": "172.20.250.21",
        "platform": "cisco_xr",
        "username": "preloaded-user",
        "password": "preloaded-password",
        "key_file": None,
        "port": 22,
    }
    seen = []

    def sender(device, command):
        seen.append(device)
        return "output"

    result = run_intent("PE1", "facts", sender=sender, device=prebuilt)

    assert result["status"] == "success"
    assert all(device is prebuilt for device in seen)


def test_refuses_unapproved_commands_before_using_a_pre_resolved_device():
    """The ordering invariant holds even when a caller supplies device=: the
    allowlist check still runs, and rejection never touches the passed-in
    record. No credentials are set in the environment on purpose."""

    result = _run_approved_commands(
        "PE1", ["configure"], device={"name": "PE1", "hostname": "unused"}
    )

    assert result["status"] == "error"
    assert "Refusing unapproved commands" in result["errors"][0]


# --------------------------------------------------------------------------- #
# Phase 7, Task 2: iter_fabric streams results instead of materializing them.
# --------------------------------------------------------------------------- #


def test_iter_fabric_yields_incrementally_not_after_every_device_finishes(monkeypatch):
    """A blocked PE1 must not hold up every other device's result: the very
    first item out of the iterator must be some device other than PE1, proven
    by blocking PE1's command with an Event this test controls -- not by
    fragile timing."""

    set_device_environment(monkeypatch)
    blocker = threading.Event()

    def sender(device, command):
        if device["name"] == "PE1":
            assert blocker.wait(timeout=5), "test setup: PE1 was never unblocked"
        return "output"

    gen = iter_fabric("facts", sender=sender, max_workers=8)
    try:
        first_name, first_result = next(gen)
        assert first_name != "PE1"
        assert first_result["status"] == "success"
    finally:
        blocker.set()

    remaining = dict(gen)
    assert "PE1" in remaining
    assert remaining["PE1"]["status"] == "success"


def test_iter_fabric_rejects_unknown_check():
    with pytest.raises(ValueError, match="Unknown check"):
        list(iter_fabric("reload"))


def test_check_fabric_matches_iter_fabric_collected(monkeypatch):
    """check_fabric is documented as "iter_fabric, collected and reordered" --
    pin that the two agree on every device's result."""

    set_device_environment(monkeypatch)

    def sender(device, command):
        return f"{command} on {device['name']}"

    def strip_timestamps(devices):
        return {
            name: {key: value for key, value in result.items() if key != "timestamp"}
            for name, result in devices.items()
        }

    from_iter = strip_timestamps(dict(iter_fabric("lldp", sender=sender)))
    from_check = strip_timestamps(check_fabric("lldp", sender=sender)["data"]["devices"])

    assert from_iter == from_check


# --------------------------------------------------------------------------- #
# Phase 7, Task 3: connection timeouts and bounded retries.
# --------------------------------------------------------------------------- #


def test_netmiko_connection_uses_configured_timeouts(monkeypatch):
    set_device_environment(monkeypatch)
    monkeypatch.setenv("NETTOOLS_CONNECT_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("NETTOOLS_BANNER_TIMEOUT_SECONDS", "7")
    sessions = install_fake_netmiko(monkeypatch)

    result = run_intent("PE1", "facts")

    assert result["status"] == "success"
    assert sessions[0]["conn_timeout"] == 3.0
    assert sessions[0]["banner_timeout"] == 7.0


def test_retries_a_transient_command_failure_and_reports_it(monkeypatch):
    """A command that fails twice then succeeds must eventually report success,
    with the retry made obvious in the result rather than silently absorbed."""

    set_device_environment(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "3")
    monkeypatch.setenv("NETTOOLS_RETRY_BACKOFF_SECONDS", "0")

    attempts = {"show version": 0}

    class FlakyConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def send_command(self, command, **kwargs):
            if command != "show version":
                return f"output for {command}"
            attempts["show version"] += 1
            if attempts["show version"] < 3:
                raise OSError("timed out")
            return "output for show version"

    import sys
    import types

    fake_netmiko = types.ModuleType("netmiko")
    fake_netmiko.ConnectHandler = lambda **params: FlakyConnection()
    monkeypatch.setitem(sys.modules, "netmiko", fake_netmiko)

    result = run_intent("PE1", "facts")

    assert result["status"] == "success"
    assert attempts["show version"] == 3
    assert result["data"]["retries"]["show version"] == 2


def test_never_retries_more_than_the_configured_attempts(monkeypatch):
    set_device_environment(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "2")
    monkeypatch.setenv("NETTOOLS_RETRY_BACKOFF_SECONDS", "0")

    attempts = {"count": 0}

    class AlwaysFailsConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def send_command(self, command, **kwargs):
            attempts["count"] += 1
            raise OSError("timed out")

    import sys
    import types

    fake_netmiko = types.ModuleType("netmiko")
    fake_netmiko.ConnectHandler = lambda **params: AlwaysFailsConnection()
    monkeypatch.setitem(sys.modules, "netmiko", fake_netmiko)

    result = run_intent("PE1", "facts")

    assert result["status"] == "error"
    # 2 configured attempts per command, times however many commands "facts" sends.
    assert attempts["count"] == 2 * len(commands_for(LAB_PLATFORM, "facts"))


def test_unapproved_commands_are_never_retried_or_even_sent(monkeypatch):
    """The allowlist refusal happens before the transport layer is ever
    reached, so there is nothing to retry -- confirmed here by a fake netmiko
    that would fail the test outright if it were ever invoked."""

    def _must_not_connect(**params):
        raise AssertionError("transport must never be reached for a refused command")

    import sys
    import types

    fake_netmiko = types.ModuleType("netmiko")
    fake_netmiko.ConnectHandler = _must_not_connect
    monkeypatch.setitem(sys.modules, "netmiko", fake_netmiko)

    result = _run_approved_commands("PE1", ["configure"])

    assert result["status"] == "error"
    assert "Refusing unapproved commands" in result["errors"][0]


def test_auth_failure_is_never_retried(monkeypatch):
    """A real netmiko authentication exception is never transient: retrying
    with the same (wrong) credentials cannot succeed."""

    set_device_environment(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "5")
    monkeypatch.setenv("NETTOOLS_RETRY_BACKOFF_SECONDS", "0")

    from netmiko.exceptions import NetmikoAuthenticationException

    attempts = {"count": 0}

    class AuthFailsConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def send_command(self, command, **kwargs):
            attempts["count"] += 1
            raise NetmikoAuthenticationException("bad credentials")

    import sys
    import types

    fake_netmiko = types.ModuleType("netmiko")
    fake_netmiko.exceptions = types.ModuleType("netmiko.exceptions")
    fake_netmiko.exceptions.NetmikoAuthenticationException = NetmikoAuthenticationException
    fake_netmiko.ConnectHandler = lambda **params: AuthFailsConnection()
    monkeypatch.setitem(sys.modules, "netmiko", fake_netmiko)
    monkeypatch.setitem(sys.modules, "netmiko.exceptions", fake_netmiko.exceptions)

    result = run_intent("PE1", "facts")

    assert result["status"] == "error"
    # Exactly one attempt per command -- an auth failure must burn none of the
    # retry budget, since it can never succeed on a later attempt.
    assert attempts["count"] == len(commands_for(LAB_PLATFORM, "facts"))


# --------------------------------------------------------------------------- #
# Phase 7, Task 5: audit log rotation, and that logging failures stay inert.
# --------------------------------------------------------------------------- #


def test_audit_log_rotates_once_it_exceeds_the_configured_size(monkeypatch, tmp_path):
    """The audit log only records real transport activity (_netmiko_send_commands),
    never the sender-injection test seam -- so a fake netmiko transport, not
    sender=, is what exercises it here."""

    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("NETTOOLS_LOG", str(log_path))
    monkeypatch.setenv("NETTOOLS_LOG_MAX_BYTES", "200")
    monkeypatch.setenv("NETTOOLS_LOG_BACKUP_COUNT", "2")

    for _ in range(10):
        result = collect_evidence("PE1")
        assert result["device"] == "PE1"

    assert log_path.is_file()
    # Bounded: never more than backup_count rotated files plus the live one.
    rotated = sorted(tmp_path.glob("audit.jsonl.*"))
    assert len(rotated) <= 2
    assert not (tmp_path / "audit.jsonl.3").exists()


def test_audit_log_failure_never_breaks_a_check(monkeypatch, tmp_path):
    """A bad NETTOOLS_LOG path (a directory sitting where the log file should
    be) must not turn an otherwise successful check into a reported failure --
    the audit log is best-effort observability, never load-bearing."""

    set_device_environment(monkeypatch)
    log_path = tmp_path / "not-a-file"
    log_path.mkdir()  # Any attempt to open this path for writing raises OSError.
    monkeypatch.setenv("NETTOOLS_LOG", str(log_path))

    result = run_intent("PE1", "facts", sender=lambda device, command: "output")

    assert result["status"] == "success"


# --------------------------------------------------------------------------- #
# Phase 8: audit actor -- provenance, never authorization (see the module
# docstring on _resolve_actor).
# --------------------------------------------------------------------------- #


def test_audit_log_records_actor_from_env_var(monkeypatch, tmp_path):
    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("NETTOOLS_LOG", str(log_path))
    monkeypatch.setenv("NETTOOLS_ACTOR", "alice@lab")

    collect_evidence("PE1")

    records = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert records
    assert all(record["actor"] == "alice@lab" for record in records)


def test_audit_log_falls_back_to_os_user_when_actor_env_unset(monkeypatch, tmp_path):
    import getpass

    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("NETTOOLS_LOG", str(log_path))
    monkeypatch.delenv("NETTOOLS_ACTOR", raising=False)

    collect_evidence("PE1")

    records = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert records
    assert all(record["actor"] == getpass.getuser() for record in records)


def test_audit_actor_is_never_treated_as_an_authorization_check(monkeypatch):
    """A bogus/empty actor must not affect the safety boundary in any way: the
    allowlist check still runs, and still refuses, with no credentials set."""

    monkeypatch.setenv("NETTOOLS_ACTOR", "")  # explicitly blank

    result = _run_approved_commands("PE1", ["configure"])

    assert result["status"] == "error"
    assert "Refusing unapproved commands" in result["errors"][0]


# --------------------------------------------------------------------------- #
# run_templates -- batched capture over one session (T-011)
# --------------------------------------------------------------------------- #


def test_run_templates_opens_one_session_for_the_whole_batch(monkeypatch):
    """The reason this function exists.

    run_template opens a session per call; fourteen templates on one device
    opened fourteen logins. IOS-XR rate-limits repeated logins, and every
    abrupt close is logged by the device as %SECURITY-SSHD_SYSLOG_PRX -- so a
    per-command loop manufactures the very log noise a capture is trying to
    record cleanly.
    """

    from agent_nettools.network_tools import run_templates

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    manifest = [
        ("bgp_neighbor", {"address": "10.255.0.12"}),
        ("bgp_neighbor", {"address": "10.255.0.31"}),
        ("route", {"prefix": "10.255.0.12/32"}),
        ("interface", {"interface": "GigabitEthernet0/0/0/0"}),
        ("logging", {"count": "200"}),
    ]
    result = run_templates("PE1", manifest)

    assert result["status"] == "success"
    assert len(sessions) == 1, f"expected one login for the batch, got {len(sessions)}"
    assert len(result["data"]["commands"]) == len(manifest)


def test_run_templates_refuses_every_bad_parameter_before_loading_credentials(monkeypatch):
    """The ordering invariant, mirroring
    test_run_template_refuses_a_bad_parameter_before_loading_credentials.

    The credentials are deleted *explicitly* rather than assumed absent. That
    matters: tests/test_mcp_server.py imports mcp_server.server, which calls
    load_dotenv() at import time, so this repository's real .env populates
    DEVICE_USERNAME for the rest of the process. A test that merely assumes
    the variable is unset passes for the wrong reason in a full-suite run --
    with credentials present, an inverted ordering would load them and *then*
    refuse, producing the same structured error and the same green tick.
    Deleting them is what makes this fail if the ordering is ever inverted.
    """

    from agent_nettools.network_tools import run_templates

    for name in ("DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE"):
        monkeypatch.delenv(name, raising=False)

    manifest = [
        ("bgp_neighbor", {"address": "10.0.0.1 | reload"}),
        ("route", {"prefix": "01.1.1.1/32"}),
        ("interface", {"interface": "Gi0/0/0/0; shutdown"}),
        ("logging", {"count": "9999"}),
        ("bgp_neighbor", {"address": "１０.0.0.1"}),
    ]
    result = run_templates("PE1", manifest)

    assert result["status"] == "error"
    assert len(result["errors"]) == len(manifest)
    assert result["data"]["commands"] == {}
    assert not any("DEVICE_USERNAME" in e for e in result["errors"])


def test_run_templates_never_opens_a_socket_for_an_all_invalid_manifest(monkeypatch):
    """Stronger than the refusal test: asserts on the transport itself."""

    from agent_nettools.network_tools import run_templates

    set_device_environment(monkeypatch)
    sessions = install_fake_netmiko(monkeypatch)

    result = run_templates("PE1", [("bgp_neighbor", {"address": "10.0.0.1 | reload"})])

    assert result["status"] == "error"
    assert sessions == [], "a rejected manifest must never reach the transport"


def test_run_templates_drops_one_bad_entry_and_still_collects_the_rest(monkeypatch):
    """A one-shot capture window must not lose the whole set to one typo."""

    from agent_nettools.network_tools import run_templates

    set_device_environment(monkeypatch)
    install_fake_netmiko(monkeypatch)

    result = run_templates(
        "PE1",
        [
            ("bgp_neighbor", {"address": "10.255.0.12"}),
            ("bgp_neighbor", {"address": "not-an-ip"}),
            ("route", {"prefix": "10.255.0.31/32"}),
        ],
    )

    assert result["data"]["commands"].keys() == {
        "show bgp neighbor 10.255.0.12",
        "show route 10.255.0.31/32",
    }
    assert any("not-an-ip" in e for e in result["errors"])


def test_run_templates_reports_validation_errors_even_when_credentials_are_missing(monkeypatch):
    """Two independent problems must both be reported.

    _safe_error would have built a fresh envelope and discarded the per-entry
    validation errors, sending the operator round the loop twice.
    """

    from agent_nettools.network_tools import run_templates

    for name in ("DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE"):
        monkeypatch.delenv(name, raising=False)

    result = run_templates(
        "PE1",
        [
            ("bgp_neighbor", {"address": "10.255.0.12"}),
            ("bgp_neighbor", {"address": "10.0.0.1 | reload"}),
        ],
    )

    assert result["status"] == "error"
    assert any("reload" in e or "whitespace" in e for e in result["errors"])
    assert any("DEVICE_USERNAME" in e for e in result["errors"])


def test_run_templates_honours_the_active_probe_gate(monkeypatch):
    from agent_nettools.network_tools import run_templates

    set_device_environment(monkeypatch)
    monkeypatch.setenv("NETTOOLS_ALLOW_ACTIVE_PROBES", "0")
    install_fake_netmiko(monkeypatch)

    result = run_templates(
        "PE1",
        [("ping", {"address": "10.255.0.31"}), ("route", {"prefix": "10.255.0.31/32"})],
    )

    assert "show route 10.255.0.31/32" in result["data"]["commands"]
    assert not any(c.startswith("ping") for c in result["data"]["commands"])
    assert any("active probes are disabled" in e for e in result["errors"])


def test_run_templates_uses_the_longest_read_timeout_in_the_batch(monkeypatch):
    """read_timeout applies per command in one batch, so it must be the max.

    It is a ceiling, not a delay: batching a 60s traceroute beside a 10s show
    costs nothing when nothing hangs.
    """

    from agent_nettools import network_tools as nt

    set_device_environment(monkeypatch)
    captured = {}

    def fake_send(device, commands, **kwargs):
        captured.update(kwargs)
        return {c: "out" for c in commands}, [], {}

    monkeypatch.setattr(nt, "_netmiko_send_commands", fake_send)
    nt.run_templates(
        "PE1",
        [("route", {"prefix": "10.255.0.31/32"}), ("traceroute", {"address": "10.255.0.31"})],
    )

    assert captured["read_timeout"] == 60.0


# --------------------------------------------------------------------------- #
# T-018: run_template attaches parsed data -- the LLD's blocking gap
# --------------------------------------------------------------------------- #


def _fixture_template_result(template, **params):
    from agent_nettools.fixtures import fixture_sender
    from agent_nettools.network_tools import run_template

    device = params.pop("_device", "RR1")
    return run_template(device, template, sender=fixture_sender(label="healthy"), **params)


@pytest.mark.parametrize(
    ("template", "params", "device"),
    [
        ("bgp_neighbor", {"address": "10.255.0.11"}, "RR1"),
        ("route", {"prefix": "10.255.0.11/32"}, "RR1"),
        ("interface", {"interface": "Gi0/0/0/0"}, "RR1"),
        ("logging", {"count": "200"}, "RR1"),
        ("ping", {"address": "10.255.0.31"}, "PE1"),
        ("traceroute", {"address": "10.255.0.31"}, "PE1"),
    ],
)
def test_run_template_attaches_parsed_data_for_every_template(
    monkeypatch, template, params, device
):
    """The LLD calls this the blocking gap.

    Every rung of the dependency descent below the top one reads template
    output. Until this landed, run_template returned raw text only -- which
    the fourth invariant forbids handing to a model.
    """

    set_device_environment(monkeypatch)
    result = _fixture_template_result(template, _device=device, **params)

    assert result["data"]["parse_status"] == "ok"
    assert result["data"]["parsed"] is not None
    assert "meta" in result["data"]["parsed"]


def test_run_template_parse_failure_does_not_become_a_transport_error(monkeypatch):
    """Parsing is independent of transport, exactly as run_intent treats it.

    The device answered; we could not read the answer. Those are different
    failures and collapsing them would lose the distinction.
    """

    from agent_nettools.network_tools import run_template

    set_device_environment(monkeypatch)
    result = run_template(
        "RR1", "route", prefix="10.255.0.11/32", sender=lambda _d, _c: "not route output at all"
    )

    assert result["status"] == "success"
    assert result["data"]["parse_status"] == "failed"
    assert result["data"]["parsed"] is None


def test_unsupported_template_is_parse_unavailable_not_parse_failed(monkeypatch):
    """No parser was even attempted, which is not the same as one failing."""

    from agent_nettools.network_tools import run_template

    set_device_environment(monkeypatch)
    result = run_template(
        "RR1", "bgp_neighbor", platform="juniper_junos", address="10.255.0.11"
    )

    assert result["status"] == "unsupported"
    assert result["data"]["parse_status"] == "unavailable"
    assert result["data"]["parsed"] is None


def test_run_template_envelope_shape_is_unchanged_for_existing_callers(monkeypatch):
    """Additive only: the keys every existing consumer reads must still be there."""

    set_device_environment(monkeypatch)
    result = _fixture_template_result("route", prefix="10.255.0.11/32")

    for key in ("tool", "device", "status", "timestamp", "data", "errors"):
        assert key in result
    data = result["data"]
    for key in ("template", "platform", "command", "commands"):
        assert key in data, f"{key} disappeared from the template envelope"


def test_parsed_template_output_is_usable_by_a_descent_rung(monkeypatch):
    """The point of the whole exercise, expressed as the descent will use it.

    A rung asks "is this peer's session established" and gets a typed answer
    out of parsed records -- never by reading device text.
    """

    set_device_environment(monkeypatch)
    result = _fixture_template_result("bgp_neighbor", address="10.255.0.11")

    meta = result["data"]["parsed"]["meta"]
    assert meta["state"] == "Established"
    assert meta["connection_state"] == "Established"
    assert isinstance(result["data"]["parsed"]["records"], list)


# --------------------------------------------------------------------------- #
# B-455 -- the combined runner keeps both authorization rules
# --------------------------------------------------------------------------- #


def test_the_combined_runner_refuses_an_unapproved_intent_command_with_no_credentials(
    monkeypatch,
):
    """The operator's required test for B-455, and the reason it is required.

    `collect_evidence_and_templates` runs intents and rendered templates over
    **one** SSH session, which means it authorizes two kinds of command by two
    different rules -- `is_approved` against the exact-match frozenset for
    intents, `render_command` + `is_safe_rendered_command` for templates. The
    risk a combined runner creates is that one rule quietly becomes the other's,
    so this pins the ordering invariant on the new path exactly as
    `test_refuses_unapproved_commands_before_loading_credentials` pins it on the
    old one.

    **No DEVICE_USERNAME/DEVICE_PASSWORD is set.** If the allowlist check ran
    after credential loading this would raise `InventoryError` rather than
    refusing, and the refusal is what proves platform resolved from static
    inventory and the check happened before any socket.
    """

    from agent_nettools import network_tools

    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)
    monkeypatch.delenv("DEVICE_SSH_KEYFILE", raising=False)

    # An intent whose command is not approved for this platform. Patching the
    # intent table rather than the allowlist: `platforms.py` takes additions
    # only and is frozen, and widening the allowlist to test a refusal would
    # test the opposite of the thing.
    monkeypatch.setattr(
        network_tools, "intents_for", lambda _platform: ("bgp",)
    )
    monkeypatch.setattr(
        network_tools, "commands_for", lambda _platform, _intent: ("configure terminal",)
    )

    evidence, envelopes = network_tools.collect_evidence_and_templates("PE1", [])

    assert evidence["bgp"]["status"] == "error"
    assert "Refusing unapproved commands" in evidence["bgp"]["errors"][0]
    assert "configure terminal" in evidence["bgp"]["errors"][0]
    assert envelopes == []


def test_the_combined_runner_refuses_a_bad_template_parameter_with_no_credentials(
    monkeypatch,
):
    """The template half of the same invariant.

    A malformed parameter is rejected by `render_command`'s reconstruction
    before `get_device` is reached, so this returns a structured refusal rather
    than an `InventoryError` -- with no credentials in the environment at all.
    """

    from agent_nettools import network_tools

    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)
    monkeypatch.delenv("DEVICE_SSH_KEYFILE", raising=False)

    _, envelopes = network_tools.collect_evidence_and_templates(
        "PE1", [("route", {"prefix": "10.0.0.1 | reload"})], sender=lambda _d, _c: ""
    )

    assert len(envelopes) == 1
    assert envelopes[0]["status"] == "error"
    assert envelopes[0]["errors"], "the refusal must say why"


def test_the_combined_runner_never_renders_a_refused_batch(monkeypatch):
    """One unapproved intent command refuses the **whole** call.

    Partially collecting after refusing part of a batch would make "the
    allowlist refused something" a condition a caller could miss while holding
    plausible-looking evidence -- which is the failure mode a refusal exists to
    prevent, arriving by a different route.
    """

    from agent_nettools import network_tools

    sent: list[str] = []

    monkeypatch.setattr(network_tools, "intents_for", lambda _p: ("bgp",))
    monkeypatch.setattr(network_tools, "commands_for", lambda _p, _i: ("reload",))

    _, envelopes = network_tools.collect_evidence_and_templates(
        "PE1",
        [("route", {"prefix": "10.255.0.12/32"})],
        sender=lambda _d, c: sent.append(c) or "",
    )

    assert sent == [], "nothing reached the transport at all"
    assert all(e["status"] == "error" for e in envelopes)
