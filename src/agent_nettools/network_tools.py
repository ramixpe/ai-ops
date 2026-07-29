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
from .lab import platform_for
from .platforms import (
    DEFAULT_PLATFORM,
    all_intents,
    commands_for,
    intents_for,
    is_approved,
    known_platforms,
    supports,
)

# Statuses a result envelope may carry. "unsupported" means the device's platform
# has no command for the requested intent -- a Junos box has no SR-TE policy
# output. That is a property of the fabric, not a failure, so it is neither an
# error nor a success and must not make a fabric check go red.
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_UNSUPPORTED = "unsupported"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_result(tool: str, device_name: str) -> dict[str, Any]:
    return {
        "tool": tool,
        "device": device_name,
        "status": STATUS_SUCCESS,
        "timestamp": _timestamp(),
        "data": {},
        "errors": [],
    }


def _safe_error(tool: str, device_name: str, message: str) -> dict[str, Any]:
    result = _base_result(tool, device_name)
    result["status"] = STATUS_ERROR
    result["errors"].append(message)
    return result


def _unsupported_result(tool: str, device_name: str, intent: str, platform: str) -> dict[str, Any]:
    """Return an envelope for an intent this platform cannot answer.

    ``errors`` stays empty -- this is not a failure. ``data.commands`` is present
    and empty so every caller that walks commands keeps working unchanged.
    """

    result = _base_result(tool, device_name)
    result["status"] = STATUS_UNSUPPORTED
    result["data"] = {"intent": intent, "platform": platform, "commands": {}}
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
    platform: str | None = None,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Run a set of approved read-only commands against one device.

    The allowlist is checked against the *device's own platform*, so an IOS-XE
    command can never reach an IOS-XR device even though both are approved
    somewhere. The platform is resolved from static lab data, which needs no
    credentials -- so this check still happens before any credential access or
    socket, exactly as before.
    """

    platform = platform or platform_for(device_name)

    unsafe_commands = [command for command in commands if not is_approved(platform, command)]
    if unsafe_commands:
        return _safe_error(
            "run_approved_commands",
            device_name,
            f"Refusing unapproved commands for {platform}: {', '.join(unsafe_commands)}",
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
                result["status"] = STATUS_ERROR
                result["errors"].append(f"{command}: {exc}")
        return result

    outputs, errors = _netmiko_send_commands(device, commands)
    result["data"]["commands"] = outputs
    if errors:
        result["status"] = STATUS_ERROR
        result["errors"].extend(errors)

    return result


def run_intent(
    device_name: str,
    intent: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Run one vendor-neutral intent against one device.

    Resolves the intent to this device's platform syntax. A platform that cannot
    answer the intent yields ``status: "unsupported"`` rather than an error.
    """

    platform = platform_for(device_name)

    if platform not in known_platforms():
        # A typo'd or not-yet-defined platform is a real error: it approves
        # nothing, so failing closed here gives a clearer message than an empty
        # allowlist would.
        return _safe_error(
            "run_approved_commands",
            device_name,
            f"No command definitions for platform: {platform}.",
        )

    if not supports(platform, intent):
        return _unsupported_result("run_approved_commands", device_name, intent, platform)

    return _run_approved_commands(
        device_name,
        list(commands_for(platform, intent)),
        platform=platform,
        sender=sender,
    )


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
    """Collect basic read-only device facts (hostname and/or version)."""

    return run_intent(device_name, "facts", sender=sender)


def check_interfaces(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect interface status using a read-only command."""

    return run_intent(device_name, "interfaces", sender=sender)


def check_bgp_neighbors(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect BGP summary information using a read-only command."""

    return run_intent(device_name, "bgp", sender=sender)


def check_lldp_neighbors(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect LLDP neighbor information using a read-only command."""

    return run_intent(device_name, "lldp", sender=sender)


def check_isis_neighbors(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect IS-IS neighbor state using a read-only command."""

    return run_intent(device_name, "isis", sender=sender)


def check_sr_policies(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect Segment Routing TE policy state using a read-only command."""

    return run_intent(device_name, "sr", sender=sender)


# Named single-device checks, keyed by intent. One source of truth so the CLI,
# fabric runner, and MCP server all agree, and one vocabulary: these keys are the
# same intent names used by platforms.PLATFORM_INTENTS and by the evidence
# sections. Adding an intent here is not enough on its own -- the platform table
# must define commands for it.
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
    result["data"] = {"check": check, "devices": {}, "unsupported": []}

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
        status = device_result["status"]
        if status == STATUS_UNSUPPORTED:
            # A vendor that cannot answer this intent is a fact about the fabric,
            # not a failure. Reported, but the fabric check stays green.
            result["data"]["unsupported"].append(name)
        elif status != STATUS_SUCCESS:
            result["status"] = STATUS_ERROR
            result["errors"].append(f"{name}: {check} check failed")

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
        result["status"] = STATUS_ERROR
        prefixes = tuple(f"{command}: " for command in missing)
        related = [error for error in combined["errors"] if error.startswith(prefixes)]
        # A connection-level failure carries no command prefix; report it as-is.
        result["errors"] = related or list(combined["errors"])

    return result


def evidence_intents(platform: str) -> tuple[str, ...]:
    """Return the intents a full evidence collection covers on one platform."""

    return intents_for(platform)


def collect_evidence(
    device_name: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Collect all evidence used by the final troubleshooting workflow.

    Every approved command runs over one SSH session, so a full evidence
    collection is a single login instead of one per intent.

    The returned dict carries one section per intent *known to any platform*, so
    the shape is identical across a mixed fabric; intents this device's platform
    cannot answer are marked ``unsupported``. ``platform`` is recorded so later
    consumers -- diffing, parsing, health rules -- can resolve sections back to
    the commands that filled them without re-reading the inventory.
    """

    platform = platform_for(device_name)

    if platform not in known_platforms():
        failure = _safe_error(
            "run_approved_commands",
            device_name,
            f"No command definitions for platform: {platform}.",
        )
        evidence: dict[str, Any] = {
            "device": device_name,
            "platform": platform,
            "timestamp": _timestamp(),
        }
        for intent in all_intents():
            evidence[intent] = dict(failure)
        return evidence

    supported = intents_for(platform)
    commands = [command for intent in supported for command in commands_for(platform, intent)]
    combined = _run_approved_commands(device_name, commands, platform=platform, sender=sender)

    evidence = {"device": device_name, "platform": platform, "timestamp": _timestamp()}
    for intent in all_intents():
        if intent in supported:
            evidence[intent] = _section_from_combined(
                device_name, list(commands_for(platform, intent)), combined
            )
        else:
            evidence[intent] = _unsupported_result(
                "run_approved_commands", device_name, intent, platform
            )

    return evidence


# Snapshots land here, relative to the working directory unless overridden.
DEFAULT_SNAPSHOT_DIR = "evidence"


def _snapshot_dir(base_dir: str | None) -> Path:
    return Path(base_dir or os.getenv("NETTOOLS_EVIDENCE_DIR") or DEFAULT_SNAPSHOT_DIR)


def _evidence_commands(evidence: dict[str, Any]) -> dict[str, str]:
    """Flatten an evidence dict to ``{command: output}`` for comparison."""

    commands: dict[str, str] = {}
    for section_result in evidence.values():
        if not isinstance(section_result, dict):
            continue
        for command, output in section_result.get("data", {}).get("commands", {}).items():
            commands[command] = output
    return commands


def _failed_commands(evidence: dict[str, Any]) -> set[str]:
    """Commands whose evidence section errored, so no output exists to compare.

    Intents are resolved through the platform the evidence was collected on, so
    this stays correct for a snapshot taken from any vendor. An ``unsupported``
    section is skipped: its commands were never expected, so they are neither
    failed nor removed.
    """

    platform = str(evidence.get("platform") or DEFAULT_PLATFORM)
    failed: set[str] = set()
    for intent, section_result in evidence.items():
        if not isinstance(section_result, dict):
            continue
        if section_result.get("status") != STATUS_ERROR:
            continue
        present = section_result.get("data", {}).get("commands", {})
        expected = commands_for(platform, intent) if supports(platform, intent) else ()
        failed.update(command for command in expected if command not in present)
    return failed


def save_snapshot(evidence: dict[str, Any], *, base_dir: str | None = None) -> str:
    """Persist one evidence collection as timestamped JSON. Returns the path.

    Snapshots go to ``base_dir``, the ``NETTOOLS_EVIDENCE_DIR`` environment
    variable, or ``evidence/`` in the working directory, in that order.
    """

    device = str(evidence.get("device", "unknown"))
    stamp = _timestamp().replace(":", "-")
    directory = _snapshot_dir(base_dir) / device
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return str(path)


def load_latest_snapshot(
    device_name: str,
    *,
    base_dir: str | None = None,
) -> dict[str, Any] | None:
    """Return the most recent saved snapshot for a device, or None."""

    directory = _snapshot_dir(base_dir) / device_name
    if not directory.is_dir():
        return None
    snapshots = sorted(directory.glob("*.json"))
    if not snapshots:
        return None
    return json.loads(snapshots[-1].read_text(encoding="utf-8"))


def diff_evidence(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two evidence collections command-by-command.

    ``changed`` lists commands whose output differs and ``added``/``removed``
    cover commands genuinely present in only one snapshot. Commands that are
    absent because their section *errored* are reported separately so a
    transient failure never masquerades as a removal: ``failed`` (errored in
    the new collection) and ``recovered`` (errored in the old one, back now).
    """

    old_cmds = _evidence_commands(old)
    new_cmds = _evidence_commands(new)
    old_failed = _failed_commands(old)
    new_failed = _failed_commands(new)

    changed = sorted(c for c in old_cmds.keys() & new_cmds.keys() if old_cmds[c] != new_cmds[c])
    unchanged = sorted(c for c in old_cmds.keys() & new_cmds.keys() if old_cmds[c] == new_cmds[c])

    old_platform = old.get("platform")
    new_platform = new.get("platform")

    return {
        "device": new.get("device", old.get("device")),
        "platform": new_platform or old_platform,
        # A device that changed vendor between snapshots explains every command
        # appearing and disappearing at once, so surface it rather than leaving
        # the reader to infer it from a wholesale added/removed churn.
        "platform_changed": bool(old_platform and new_platform and old_platform != new_platform),
        "old_timestamp": old.get("timestamp"),
        "new_timestamp": new.get("timestamp"),
        "changed": changed,
        "unchanged": unchanged,
        "added": sorted(new_cmds.keys() - old_cmds.keys() - old_failed),
        "removed": sorted(old_cmds.keys() - new_cmds.keys() - new_failed),
        "failed": sorted(new_failed),
        "recovered": sorted(old_failed & new_cmds.keys()),
    }
