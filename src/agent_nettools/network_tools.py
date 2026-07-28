"""Read-only network inspection tools for Cisco IOS-XR.

These functions are the only network tools that should be exposed to the agent.
They intentionally avoid generic command execution.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .inventory import InventoryError, get_device, load_inventory

# Approved read-only IOS-XR show commands. Nothing here changes device state.
APPROVED_COMMANDS = {
    "show running-config hostname",
    "show version",
    "show interfaces brief",
    "show bgp summary",
    "show lldp neighbors",
    "show isis neighbors",
    "show segment-routing traffic-eng policy",
}

# Evidence section -> the approved commands that fill it. collect_evidence runs
# every one of these over a single SSH session.
EVIDENCE_COMMANDS = {
    "facts": ["show running-config hostname", "show version"],
    "interfaces": ["show interfaces brief"],
    "bgp": ["show bgp summary"],
    "lldp": ["show lldp neighbors"],
    "isis": ["show isis neighbors"],
    "sr_policies": ["show segment-routing traffic-eng policy"],
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_result(tool: str, device_name: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "device": device_name,
        "status": "success",
        "timestamp": _timestamp(),
        "data": {},
        "errors": [],
    }


def _safe_error(tool: str, device_name: str, message: str) -> dict[str, Any]:
    result = _base_result(tool, device_name)
    result["status"] = "error"
    result["errors"].append(message)
    return result


def _audit_log(entry: dict[str, Any]) -> None:
    """Append one JSONL audit record when NETTOOLS_LOG is set.

    Cheap, best-effort observability: every command run records device, command,
    duration, and bytes returned. Logging failures never break a check.
    """

    log_path = os.getenv("NETTOOLS_LOG")
    if not log_path:
        return
    try:
        record = {"timestamp": _timestamp(), **entry}
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _netmiko_send_commands(
    device: dict[str, Any],
    commands: list[str],
) -> tuple[dict[str, str], list[str]]:
    """Open one SSH session and run every approved command over it.

    One login per device check, not one login per command: IOS-XR rate-limits
    repeated logins, and a full evidence collection is seven commands.

    Returns ``(outputs, errors)``. ``outputs`` maps command -> text for the
    commands that ran; ``errors`` holds one message per failure.
    """

    from netmiko import ConnectHandler  # Imported lazily so unit tests do not need live SSH.

    platform = device.get("platform", "")
    device_type = platform or "cisco_xr"

    connection_params: dict[str, Any] = {
        "device_type": device_type,
        "host": device["hostname"],
        "username": device["username"],
        "port": device.get("port", 22),
    }
    # Password and/or SSH key: a key file is used when provided, otherwise the
    # shared password. Netmiko accepts both together for key + passphrase setups.
    if device.get("key_file"):
        connection_params["use_keys"] = True
        connection_params["key_file"] = device["key_file"]
    if device.get("password"):
        connection_params["password"] = device["password"]

    outputs: dict[str, str] = {}
    errors: list[str] = []

    try:
        with ConnectHandler(**connection_params) as connection:
            for command in commands:
                started = time.monotonic()
                try:
                    output = connection.send_command(command)
                    outputs[command] = output
                    _audit_log(
                        {
                            "device": device["name"],
                            "command": command,
                            "duration_ms": round((time.monotonic() - started) * 1000, 1),
                            "bytes": len(output),
                            "status": "success",
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - beginner-friendly structured errors.
                    errors.append(f"{command}: {exc}")
                    _audit_log(
                        {
                            "device": device["name"],
                            "command": command,
                            "duration_ms": round((time.monotonic() - started) * 1000, 1),
                            "status": "error",
                            "error": str(exc),
                        }
                    )
    except Exception as exc:  # noqa: BLE001 - the session itself failed; no command ran.
        errors.append(f"connection to {device['hostname']} failed: {exc}")
        _audit_log({"device": device["name"], "status": "connection_error", "error": str(exc)})

    return outputs, errors


def _run_approved_commands(
    device_name: str,
    commands: list[str],
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Run a set of approved read-only commands against one device."""

    unsafe_commands = [command for command in commands if command not in APPROVED_COMMANDS]
    if unsafe_commands:
        return _safe_error(
            "run_approved_commands",
            device_name,
            f"Refusing unapproved commands: {', '.join(unsafe_commands)}",
        )

    try:
        device = get_device(device_name)
    except InventoryError as exc:
        return _safe_error("run_approved_commands", device_name, str(exc))

    result = _base_result("run_approved_commands", device_name)
    result["data"] = {"commands": {}}

    if sender is not None:
        # Test/injection path: the caller supplies output per command.
        for command in commands:
            try:
                result["data"]["commands"][command] = sender(device, command)
            except Exception as exc:  # noqa: BLE001 - beginner-friendly structured errors.
                result["status"] = "error"
                result["errors"].append(f"{command}: {exc}")
        return result

    outputs, errors = _netmiko_send_commands(device, commands)
    result["data"]["commands"] = outputs
    if errors:
        result["status"] = "error"
        result["errors"].extend(errors)

    return result


def list_devices() -> dict[str, Any]:
    """Return the available devices from inventory."""

    result = _base_result("list_devices", "inventory")
    try:
        devices = load_inventory()
    except InventoryError as exc:
        return _safe_error("list_devices", "inventory", str(exc))

    result["data"] = {
        "devices": [
            {"name": device["name"], "hostname": device["hostname"], "platform": device["platform"]}
            for device in devices
        ]
    }
    return result


def get_device_facts(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect basic read-only device facts (hostname + version)."""

    return _run_approved_commands(device_name, EVIDENCE_COMMANDS["facts"], sender=sender)


def check_interfaces(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect interface status using a read-only command."""

    return _run_approved_commands(device_name, EVIDENCE_COMMANDS["interfaces"], sender=sender)


def check_bgp_neighbors(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect BGP summary information using a read-only command."""

    return _run_approved_commands(device_name, EVIDENCE_COMMANDS["bgp"], sender=sender)


def check_lldp_neighbors(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect LLDP neighbor information using a read-only command."""

    return _run_approved_commands(device_name, EVIDENCE_COMMANDS["lldp"], sender=sender)


def check_isis_neighbors(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect IS-IS neighbor state using a read-only command."""

    return _run_approved_commands(device_name, EVIDENCE_COMMANDS["isis"], sender=sender)


def check_sr_policies(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect Segment Routing TE policy state using a read-only command."""

    return _run_approved_commands(device_name, EVIDENCE_COMMANDS["sr_policies"], sender=sender)


# Named single-device checks, keyed by their CLI / fabric name. One source of
# truth so the CLI, fabric runner, and MCP server all agree.
CHECK_TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "facts": get_device_facts,
    "interfaces": check_interfaces,
    "bgp": check_bgp_neighbors,
    "lldp": check_lldp_neighbors,
    "isis": check_isis_neighbors,
    "sr": check_sr_policies,
}


def check_fabric(
    check: str = "bgp",
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
    max_workers: int = 8,
) -> dict[str, Any]:
    """Run one named check across every device in the inventory, in parallel.

    Logins to all devices happen concurrently, so a fabric-wide check is roughly
    one device's latency instead of nine sequential logins.
    """

    if check not in CHECK_TOOLS:
        choices = ", ".join(sorted(CHECK_TOOLS))
        return _safe_error("check_fabric", "fabric", f"Unknown check: {check}. Choose from {choices}.")

    try:
        devices = load_inventory()
    except InventoryError as exc:
        return _safe_error("check_fabric", "fabric", str(exc))

    tool = CHECK_TOOLS[check]
    result = _base_result("check_fabric", "fabric")
    result["data"] = {"check": check, "devices": {}}

    def run_one(device: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        name = str(device["name"])
        return name, tool(name, sender=sender)

    workers = max(1, min(max_workers, len(devices)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        collected = dict(pool.map(run_one, devices))

    # Preserve inventory order in the output regardless of completion order.
    for device in devices:
        name = str(device["name"])
        device_result = collected[name]
        result["data"]["devices"][name] = device_result
        if device_result["status"] != "success":
            result["status"] = "error"
            result["errors"].append(f"{name}: {check} check failed")

    return result


def check_fabric_bgp(
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect BGP summary from every device in the inventory (parallel).

    Returns one aggregated result with a per-device ``{status, output, errors}``
    entry so the whole fabric's BGP health can be reviewed at once.
    """

    fabric = check_fabric("bgp", sender=sender)
    result = _base_result("check_fabric_bgp", "fabric")
    result["status"] = fabric["status"]
    result["errors"] = list(fabric["errors"])
    result["data"] = {"devices": {}}

    command = EVIDENCE_COMMANDS["bgp"][0]
    for name, device_result in fabric.get("data", {}).get("devices", {}).items():
        result["data"]["devices"][name] = {
            "status": device_result["status"],
            "output": device_result["data"].get("commands", {}).get(command),
            "errors": device_result["errors"],
        }

    return result


def _section_from_combined(
    device_name: str,
    commands: list[str],
    combined: dict[str, Any],
) -> dict[str, Any]:
    """Slice one evidence section out of a single combined command run."""

    result = _base_result("run_approved_commands", device_name)
    executed = combined["data"].get("commands", {})
    result["data"] = {"commands": {c: executed[c] for c in commands if c in executed}}

    missing = [command for command in commands if command not in executed]
    if missing:
        result["status"] = "error"
        prefixes = tuple(f"{command}: " for command in missing)
        related = [error for error in combined["errors"] if error.startswith(prefixes)]
        # A connection-level failure carries no command prefix; report it as-is.
        result["errors"] = related or list(combined["errors"])

    return result


def collect_evidence(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect all evidence used by the final troubleshooting workflow.

    Every approved command runs over one SSH session, so a full evidence
    collection is a single login instead of one per check.
    """

    commands = [command for group in EVIDENCE_COMMANDS.values() for command in group]
    combined = _run_approved_commands(device_name, commands, sender=sender)

    evidence: dict[str, Any] = {"device": device_name, "timestamp": _timestamp()}
    for section, section_commands in EVIDENCE_COMMANDS.items():
        evidence[section] = _section_from_combined(device_name, section_commands, combined)

    return evidence


DEFAULT_SNAPSHOT_DIR = "evidence"


def _evidence_commands(evidence: dict[str, Any]) -> dict[str, str]:
    """Flatten an evidence dict to ``{command: output}`` for comparison."""

    commands: dict[str, str] = {}
    for section_result in evidence.values():
        if not isinstance(section_result, dict):
            continue
        for command, output in section_result.get("data", {}).get("commands", {}).items():
            commands[command] = output
    return commands


def save_snapshot(evidence: dict[str, Any], *, base_dir: str = DEFAULT_SNAPSHOT_DIR) -> str:
    """Persist one evidence collection as timestamped JSON. Returns the path."""

    device = str(evidence.get("device", "unknown"))
    stamp = _timestamp().replace(":", "-")
    directory = Path(base_dir) / device
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return str(path)


def load_latest_snapshot(
    device_name: str,
    *,
    base_dir: str = DEFAULT_SNAPSHOT_DIR,
) -> dict[str, Any] | None:
    """Return the most recent saved snapshot for a device, or None."""

    directory = Path(base_dir) / device_name
    if not directory.is_dir():
        return None
    snapshots = sorted(directory.glob("*.json"))
    if not snapshots:
        return None
    return json.loads(snapshots[-1].read_text(encoding="utf-8"))


def diff_evidence(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two evidence collections command-by-command.

    Returns ``{device, changed, unchanged, added, removed}`` where ``changed``
    lists commands whose output differs, ``added``/``removed`` cover commands
    present in only one snapshot.
    """

    old_cmds = _evidence_commands(old)
    new_cmds = _evidence_commands(new)

    changed = sorted(c for c in old_cmds.keys() & new_cmds.keys() if old_cmds[c] != new_cmds[c])
    unchanged = sorted(c for c in old_cmds.keys() & new_cmds.keys() if old_cmds[c] == new_cmds[c])

    return {
        "device": new.get("device", old.get("device")),
        "old_timestamp": old.get("timestamp"),
        "new_timestamp": new.get("timestamp"),
        "changed": changed,
        "unchanged": unchanged,
        "added": sorted(new_cmds.keys() - old_cmds.keys()),
        "removed": sorted(old_cmds.keys() - new_cmds.keys()),
    }
