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

from . import parsers
from .inventory import InventoryError, get_device, load_inventory
from .lab import platform_for
from .normalize import normalize_output
from .platforms import (
    DEFAULT_PLATFORM,
    TemplateValidationError,
    all_intents,
    commands_for,
    intents_for,
    is_approved,
    is_safe_rendered_command,
    known_platforms,
    render_command,
    supports,
    supports_template,
    template_for,
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
    *,
    read_timeout: float | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Open one SSH session and run every approved command over it.

    One login per device check, not one login per command: IOS-XR rate-limits
    repeated logins, and a full evidence collection is seven commands.

    ``read_timeout`` overrides netmiko's default per-command timeout for
    every command in this batch. Templates pass their own suggested timeout
    (ping/traceroute run much longer than a ``show`` command); evidence
    collection and static allowlist checks pass ``None`` and get netmiko's
    default, unchanged from before this parameter existed.

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
    send_kwargs = {"read_timeout": read_timeout} if read_timeout is not None else {}

    try:
        with ConnectHandler(**connection_params) as connection:
            for command in commands:
                started = time.monotonic()
                try:
                    output = connection.send_command(command, **send_kwargs)
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


def _attach_parsed(section: dict[str, Any], platform: str, intent: str) -> None:
    """Attach parsed data (or its absence) to one intent's section result.

    Parsing is independent of transport status: an "error" section still gets a
    parse attempt over whatever commands did return output (usually none, which
    ``parse_intent`` reports as ``PARSE_FAILED``), while an "unsupported" section
    always gets ``PARSE_UNAVAILABLE`` with no parse attempt at all -- there is no
    output to look at. A parser exception never reaches here: ``parse_intent``
    already guards every parser call.
    """

    if section.get("status") == STATUS_UNSUPPORTED:
        section["data"]["parsed"] = None
        section["data"]["parse_status"] = parsers.PARSE_UNAVAILABLE
        return

    outputs = section["data"].get("commands", {})
    parsed, parse_status = parsers.parse_intent(platform, intent, outputs)
    section["data"]["parsed"] = parsed
    section["data"]["parse_status"] = parse_status


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
        result = _safe_error(
            "run_approved_commands",
            device_name,
            f"No command definitions for platform: {platform}.",
        )
    elif not supports(platform, intent):
        result = _unsupported_result("run_approved_commands", device_name, intent, platform)
    else:
        result = _run_approved_commands(
            device_name,
            list(commands_for(platform, intent)),
            platform=platform,
            sender=sender,
        )

    _attach_parsed(result, platform, intent)
    return result


# --------------------------------------------------------------------------- #
# Templates: validated, parameterized commands (Phase 5).
# --------------------------------------------------------------------------- #

# Whether ping/traceroute templates may run at all. Default enabled: they are
# read-only in the sense that they change no device configuration, but unlike
# every "show" command they *do* generate traffic (ICMP echoes, UDP/ICMP probes)
# -- and they are table stakes for troubleshooting, which is why the default
# favors availability. Set to "0"/"false"/"no"/"off" to disable them entirely,
# e.g. for a stricter deployment that wants zero device-generated traffic ever;
# every other truthy-looking value (including unset) leaves them enabled.
NETTOOLS_ALLOW_ACTIVE_PROBES_ENV = "NETTOOLS_ALLOW_ACTIVE_PROBES"
_FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off"})


def _active_probes_allowed() -> bool:
    value = os.getenv(NETTOOLS_ALLOW_ACTIVE_PROBES_ENV, "1").strip().lower()
    return value not in _FALSY_ENV_VALUES


def _unsupported_template_result(
    tool: str, device_name: str, template_name: str, platform: str
) -> dict[str, Any]:
    """Return an envelope for a template this platform has no definition for.

    Mirrors ``_unsupported_result``'s "not a failure" shape for intents: a
    platform simply not having a given template is a fact about the fabric,
    not a caller error.
    """

    result = _base_result(tool, device_name)
    result["status"] = STATUS_UNSUPPORTED
    result["data"] = {"template": template_name, "platform": platform}
    return result


def _run_rendered_command(
    device_name: str,
    command: str,
    *,
    template_name: str,
    platform: str,
    read_timeout: float | None = None,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Run one already-rendered, already-validated template command.

    Mirrors ``_run_approved_commands``'s ordering invariant, but checks
    ``is_safe_rendered_command`` instead of the static ``is_approved``
    allowlist: a rendered template command (it carries a caller-supplied
    parameter) is never a member of the flat per-platform allowlist, so its
    authorization comes from ``render_command``'s own layered validation --
    re-checked here, defense in depth, immediately before credentials are
    loaded, exactly as ``is_approved`` is re-checked in
    ``_run_approved_commands`` regardless of what the caller supposedly
    already filtered.
    """

    if not is_safe_rendered_command(command):
        return _safe_error(
            "run_template", device_name, f"Refusing unsafe rendered command: {command!r}"
        )

    try:
        device = get_device(device_name)
    except InventoryError as exc:
        return _safe_error("run_template", device_name, str(exc))

    result = _base_result("run_template", device_name)
    # Output goes under "commands", keyed by the rendered command, matching what
    # run_intent produces. A second shape for the same concept would make every
    # generic consumer -- the MCP client, the LLM prompt builder, anything walking
    # data.commands -- silently skip template results. The rendered command is
    # also echoed as "command" for readability, but the output is stored once.
    result["data"] = {"template": template_name, "platform": platform, "command": command}

    if sender is not None:
        # Test/injection path: the caller supplies output for this command.
        try:
            result["data"]["commands"] = {command: sender(device, command)}
        except Exception as exc:  # noqa: BLE001 - beginner-friendly structured errors.
            result["status"] = STATUS_ERROR
            result["errors"].append(f"{command}: {exc}")
            result["data"]["commands"] = {}
        return result

    outputs, errors = _netmiko_send_commands(device, [command], read_timeout=read_timeout)
    result["data"]["commands"] = dict(outputs)
    if errors:
        result["status"] = STATUS_ERROR
        result["errors"].extend(errors)

    return result


def run_template(
    device_name: str,
    template_name: str,
    *,
    platform: str | None = None,
    sender: Callable[[dict[str, Any], str], str] | None = None,
    **params: str,
) -> dict[str, Any]:
    """Render and run one validated, parameterized command template.

    Mirrors ``run_intent``'s ordering invariant exactly: platform resolves
    from static lab data (``platform_for``, no credentials touched), the
    template is looked up and every parameter is validated by reconstruction
    -- never passed through -- *before* ``get_device``/credential access/a
    socket. A bad parameter is therefore refused with no credentials loaded
    at all, same as an unapproved static command.

    An active-probe template (``ping``/``traceroute``) is refused before
    rendering, if ``NETTOOLS_ALLOW_ACTIVE_PROBES`` says no, since that check
    needs no device access either.

    Unknown template for this platform returns ``status: "unsupported"``,
    matching ``run_intent`` -- a platform not having a template is a fact
    about the fabric, not a caller error.
    """

    platform = platform or platform_for(device_name)

    if platform not in known_platforms():
        # A typo'd or not-yet-defined platform is a real error: it approves
        # nothing, so failing closed here gives a clearer message than an
        # empty template set would.
        result = _safe_error(
            "run_template", device_name, f"No command definitions for platform: {platform}."
        )
        result["data"] = {"template": template_name, "platform": platform}
        return result

    if not supports_template(platform, template_name):
        return _unsupported_template_result("run_template", device_name, template_name, platform)

    template = template_for(platform, template_name)

    if template.active_probe and not _active_probes_allowed():
        result = _safe_error(
            "run_template",
            device_name,
            "Active probes (ping/traceroute) are disabled: "
            f"{NETTOOLS_ALLOW_ACTIVE_PROBES_ENV} is set to a falsy value. Unset it or set it to "
            "1/true to allow ping/traceroute templates.",
        )
        result["data"] = {"template": template_name, "platform": platform}
        return result

    try:
        command = render_command(platform, template_name, **params)
    except TemplateValidationError as exc:
        return _safe_error("run_template", device_name, str(exc))

    return _run_rendered_command(
        device_name,
        command,
        template_name=template_name,
        platform=platform,
        read_timeout=template.read_timeout,
        sender=sender,
    )


# Named single-parameter template tools, one per registered template, kept
# alongside CHECK_TOOLS's per-intent functions so the CLI and MCP server call
# the same thing: a thin, typed wrapper over run_template(). The parameter is
# still validated by reconstruction inside render_command() -- these wrappers
# add nothing to the safety story, only a friendlier call shape.


def get_route(
    device_name: str,
    prefix: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Look up a specific route by IPv4 address or prefix, e.g. "10.0.0.0/24"."""

    return run_template(device_name, "route", prefix=prefix, sender=sender)


def get_bgp_neighbor(
    device_name: str,
    address: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Look up a specific BGP neighbor by IPv4 address."""

    return run_template(device_name, "bgp_neighbor", address=address, sender=sender)


def get_interface(
    device_name: str,
    interface: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Look up a specific interface's status by name, e.g. "GigabitEthernet0/0/0/1"."""

    return run_template(device_name, "interface", interface=interface, sender=sender)


def get_logging(
    device_name: str,
    count: int | str = 20,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Show the last N log lines (1-500, default 20)."""

    return run_template(device_name, "logging", count=str(count), sender=sender)


def ping_device(
    device_name: str,
    address: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Ping a specific IPv4 address from the device.

    An active probe: it generates ICMP traffic (unlike every other tool in
    this module) even though it changes no device state. Gated by
    ``NETTOOLS_ALLOW_ACTIVE_PROBES``; see the module docstring section above.
    """

    return run_template(device_name, "ping", address=address, sender=sender)


def traceroute_device(
    device_name: str,
    address: str,
    *,
    sender: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    """Traceroute to a specific IPv4 address from the device.

    An active probe, like ``ping_device``: generates traffic, changes no
    device state, gated by ``NETTOOLS_ALLOW_ACTIVE_PROBES``.
    """

    return run_template(device_name, "traceroute", address=address, sender=sender)


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
        message = f"No command definitions for platform: {platform}."
        evidence: dict[str, Any] = {
            "device": device_name,
            "platform": platform,
            "timestamp": _timestamp(),
        }
        for intent in all_intents():
            # A fresh envelope per intent -- not a shared copy -- so attaching
            # parsed data to one intent's "data" dict can never leak into another.
            section = _safe_error("run_approved_commands", device_name, message)
            _attach_parsed(section, platform, intent)
            evidence[intent] = section
        return evidence

    supported = intents_for(platform)
    commands = [command for intent in supported for command in commands_for(platform, intent)]
    combined = _run_approved_commands(device_name, commands, platform=platform, sender=sender)

    evidence = {"device": device_name, "platform": platform, "timestamp": _timestamp()}
    for intent in all_intents():
        if intent in supported:
            section = _section_from_combined(
                device_name, list(commands_for(platform, intent)), combined
            )
        else:
            section = _unsupported_result(
                "run_approved_commands", device_name, intent, platform
            )
        _attach_parsed(section, platform, intent)
        evidence[intent] = section

    return evidence


# Snapshots land here, relative to the working directory unless overridden.
DEFAULT_SNAPSHOT_DIR = "evidence"

# The golden (pinned) snapshot's filename. Deliberately not timestamp-shaped so
# it can never be confused with -- or accidentally picked up by -- the
# lexicographic "latest timestamped snapshot" glob below; every timestamped
# snapshot lookup explicitly excludes this exact name instead of relying on
# sort order to separate the two.
GOLDEN_SNAPSHOT_FILENAME = "golden.json"


def _snapshot_dir(base_dir: str | None) -> Path:
    return Path(base_dir or os.getenv("NETTOOLS_EVIDENCE_DIR") or DEFAULT_SNAPSHOT_DIR)


def _timestamped_snapshot_paths(directory: Path) -> list[Path]:
    """Return one device's timestamped snapshots, oldest first, golden excluded."""

    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.name != GOLDEN_SNAPSHOT_FILENAME)


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
    """Return the most recent saved *timestamped* snapshot for a device, or None.

    Never returns the golden snapshot -- it lives under a fixed filename that
    this listing explicitly excludes, so pinning a baseline can never silently
    change what "latest" means.
    """

    directory = _snapshot_dir(base_dir) / device_name
    snapshots = _timestamped_snapshot_paths(directory)
    if not snapshots:
        return None
    return json.loads(snapshots[-1].read_text(encoding="utf-8"))


def save_golden_snapshot(evidence: dict[str, Any], *, base_dir: str | None = None) -> str:
    """Pin one evidence collection as the device's golden (known-good) baseline.

    Overwrites any previously pinned golden snapshot for this device -- there
    is exactly one golden snapshot per device, unlike the unbounded history of
    timestamped snapshots. Returns the path written.
    """

    device = str(evidence.get("device", "unknown"))
    directory = _snapshot_dir(base_dir) / device
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / GOLDEN_SNAPSHOT_FILENAME
    path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return str(path)


def load_golden_snapshot(
    device_name: str,
    *,
    base_dir: str | None = None,
) -> dict[str, Any] | None:
    """Return a device's pinned golden snapshot, or None if never pinned."""

    path = _snapshot_dir(base_dir) / device_name / GOLDEN_SNAPSHOT_FILENAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# Sentinel distinguishing "field absent" from "field present with value None"
# when diffing meta/record dicts -- JSON has no tombstone value of its own.
_MISSING = object()


def _intent_state(section: Any) -> str:
    """Classify one snapshot's section for an intent.

    One of ``"absent"`` (the intent key is not in the evidence dict at all --
    the shape hand-built tests use), ``"unsupported"``, ``"error"``, or
    ``"available"``. A section with no ``status`` key at all (again, common in
    hand-built test evidence) is treated as available: only an explicit
    ``STATUS_ERROR``/``STATUS_UNSUPPORTED`` is special-cased, matching the
    pre-Phase-2 diff, which never inspected ``status`` for command listing.
    """

    if not isinstance(section, dict):
        return "absent"
    status = section.get("status")
    if status == STATUS_UNSUPPORTED:
        return "unsupported"
    if status == STATUS_ERROR:
        return "error"
    return "available"


def _diff_fields(
    old_fields: dict[str, Any], new_fields: dict[str, Any], exclude: frozenset[str]
) -> dict[str, dict[str, Any]]:
    """Return ``{field: {"old":.., "new":..}}`` for every differing field.

    A field present on only one side compares against the ``_MISSING``
    sentinel, so an added or dropped field is reported too, not just a changed
    value; it surfaces in the result as ``None`` since the envelope is JSON.
    """

    changes: dict[str, dict[str, Any]] = {}
    for field in (old_fields.keys() | new_fields.keys()) - exclude:
        old_value = old_fields.get(field, _MISSING)
        new_value = new_fields.get(field, _MISSING)
        if old_value != new_value:
            changes[field] = {
                "old": None if old_value is _MISSING else old_value,
                "new": None if new_value is _MISSING else new_value,
            }
    return changes


def _compare_parsed(
    intent: str, old_parsed: dict[str, Any], new_parsed: dict[str, Any], *, platform: str
) -> dict[str, Any]:
    """Compare two ``ParseResult``-shaped dicts, matching rows by record key."""

    volatile = parsers.volatile_fields(platform, intent)
    key_field = parsers.record_key(platform, intent)

    changed_meta = _diff_fields(old_parsed.get("meta") or {}, new_parsed.get("meta") or {}, volatile)

    added_records: list[Any] = []
    removed_records: list[Any] = []
    changed_records: list[dict[str, Any]] = []

    if key_field is not None:
        old_records = {r[key_field]: r for r in (old_parsed.get("records") or []) if key_field in r}
        new_records = {r[key_field]: r for r in (new_parsed.get("records") or []) if key_field in r}

        added_records = [new_records[key] for key in sorted(new_records.keys() - old_records.keys())]
        removed_records = [old_records[key] for key in sorted(old_records.keys() - new_records.keys())]
        for key in sorted(new_records.keys() & old_records.keys()):
            row_changes = _diff_fields(old_records[key], new_records[key], volatile)
            if row_changes:
                changed_records.append({"key": key, "changes": row_changes})

    return {
        "added_records": added_records,
        "removed_records": removed_records,
        "changed_records": changed_records,
        "changed_meta": changed_meta,
        "compared_via": "parsed",
    }


def _normalized_intent_text(intent: str, data: dict[str, Any], *, platform: str) -> str:
    """Join one intent's command outputs after preamble-stripping and masking.

    Each command is normalized on its own before joining -- normalization only
    strips a preamble from the very top of a string, so joining raw texts first
    would leave a second command's timestamp line stranded mid-document.
    """

    commands = data.get("commands") or {}
    return "\n".join(
        normalize_output(text, platform=platform, intent=intent) for _, text in sorted(commands.items())
    )


def _compare_intent(
    intent: str,
    old_section: dict[str, Any],
    new_section: dict[str, Any],
    *,
    old_platform: str,
    new_platform: str,
) -> tuple[dict[str, Any], bool]:
    """Compare one intent present (successfully or not) on both sides.

    Returns ``(details_entry, changed)``. Parsed comparison is preferred when
    both sides parsed cleanly; otherwise falls back to normalized text, in
    which case the details lists stay empty but ``compared_via`` still records
    which path was used.
    """

    old_data = old_section.get("data", {}) or {}
    new_data = new_section.get("data", {}) or {}

    if old_data.get("parse_status") == parsers.PARSE_OK and new_data.get("parse_status") == parsers.PARSE_OK:
        entry = _compare_parsed(
            intent, old_data.get("parsed") or {}, new_data.get("parsed") or {}, platform=new_platform
        )
        changed = bool(
            entry["added_records"]
            or entry["removed_records"]
            or entry["changed_records"]
            or entry["changed_meta"]
        )
        return entry, changed

    old_text = _normalized_intent_text(intent, old_data, platform=old_platform)
    new_text = _normalized_intent_text(intent, new_data, platform=new_platform)
    entry = {
        "added_records": [],
        "removed_records": [],
        "changed_records": [],
        "changed_meta": {},
        "compared_via": "normalized_text",
    }
    return entry, old_text != new_text


def diff_evidence(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compare two evidence collections at the intent level.

    Command-level comparison is a dead end: every IOS-XR ``show`` command
    prefixes its output with the current timestamp, so byte comparison reports
    every command changed on every run (see ``normalize.py``). Comparing at the
    intent level instead lets each intent choose its best available comparison:
    parsed records when both sides parsed cleanly (matched by
    ``parsers.record_key``, excluding ``parsers.volatile_fields``), falling back
    to normalized text otherwise.

    An intent missing because its section *errored* lands in ``failed``/
    ``recovered``, never in ``added``/``removed`` -- a transient failure must
    never masquerade as a removal. An ``unsupported`` intent (this platform has
    no command for it) is its own bucket, excluded from every other one.
    Platform is resolved per snapshot from ``evidence["platform"]``, defaulting
    to ``DEFAULT_PLATFORM`` for older snapshots that predate it.
    """

    old_platform = old.get("platform")
    new_platform = new.get("platform")
    old_resolved_platform = str(old_platform or DEFAULT_PLATFORM)
    new_resolved_platform = str(new_platform or DEFAULT_PLATFORM)

    intents = sorted(
        {key for key, value in old.items() if isinstance(value, dict)}
        | {key for key, value in new.items() if isinstance(value, dict)}
    )

    changed: list[str] = []
    unchanged: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    failed: list[str] = []
    recovered: list[str] = []
    unsupported: list[str] = []
    details: dict[str, Any] = {}

    for intent in intents:
        old_section = old.get(intent)
        new_section = new.get(intent)
        old_state = _intent_state(old_section)
        new_state = _intent_state(new_section)

        if new_state == "unsupported":
            unsupported.append(intent)
        elif new_state == "error":
            failed.append(intent)
        elif new_state == "absent":
            if old_state == "available":
                removed.append(intent)
        elif old_state == "error":
            recovered.append(intent)
        elif old_state in ("absent", "unsupported"):
            added.append(intent)
        else:
            entry, is_changed = _compare_intent(
                intent,
                old_section,
                new_section,
                old_platform=old_resolved_platform,
                new_platform=new_resolved_platform,
            )
            details[intent] = entry
            (changed if is_changed else unchanged).append(intent)

    return {
        "device": new.get("device", old.get("device")),
        "platform": new_platform or old_platform,
        # A device that changed vendor between snapshots explains every intent
        # appearing and disappearing at once, so surface it rather than leaving
        # the reader to infer it from a wholesale added/removed churn.
        "platform_changed": bool(old_platform and new_platform and old_platform != new_platform),
        "old_timestamp": old.get("timestamp"),
        "new_timestamp": new.get("timestamp"),
        "changed": sorted(changed),
        "unchanged": sorted(unchanged),
        "added": sorted(added),
        "removed": sorted(removed),
        "failed": sorted(failed),
        "recovered": sorted(recovered),
        "unsupported": sorted(unsupported),
        "details": details,
    }


# --------------------------------------------------------------------------- #
# Flap detection: history a single before/after diff cannot see.
# --------------------------------------------------------------------------- #


def _flap_sequences(
    snapshots: list[dict[str, Any]],
) -> dict[tuple[str, str | None, str], list[Any]]:
    """Build ``{(intent, subject, field): [value, value, ...]}`` across snapshots.

    ``subject`` is ``None`` for a meta-level field (device- or protocol-wide,
    e.g. a BGP process's ``active`` flag) and the record key's value
    (``parsers.record_key``) for a per-row field -- the same identity diffing
    already uses, so a peer that bounces is tracked as one continuous history
    across snapshots rather than compared pairwise. Volatile fields
    (``parsers.volatile_fields``) are excluded: they move every collection and
    would swamp real oscillation with noise. Only intents that parsed cleanly
    contribute; a snapshot with a failed or unsupported intent simply has
    nothing to add for it in that round, rather than breaking the sequence.
    """

    sequences: dict[tuple[str, str | None, str], list[Any]] = {}

    for snapshot in snapshots:
        platform = str(snapshot.get("platform") or DEFAULT_PLATFORM)
        for intent, section in snapshot.items():
            if not isinstance(section, dict):
                continue  # "device", "platform", "timestamp" are plain strings.
            data = section.get("data", {}) or {}
            if data.get("parse_status") != parsers.PARSE_OK:
                continue
            parsed = data.get("parsed") or {}
            volatile = parsers.volatile_fields(platform, intent)
            key_field = parsers.record_key(platform, intent)

            for field, value in (parsed.get("meta") or {}).items():
                if field in volatile:
                    continue
                sequences.setdefault((intent, None, field), []).append(value)

            if key_field is None:
                continue
            for record in parsed.get("records") or []:
                subject = record.get(key_field)
                if subject is None:
                    continue
                for field, value in record.items():
                    if field == key_field or field in volatile:
                        continue
                    sequences.setdefault((intent, subject, field), []).append(value)

    return sequences


def detect_flaps(
    device_name: str,
    *,
    base_dir: str | None = None,
    min_transitions: int = 3,
) -> dict[str, Any]:
    """Report fields that oscillate across a device's saved snapshot history.

    A peer that bounced up/down/up between collections can look clean in
    every single pairwise ``diff_evidence`` call -- each one only ever shows
    one change, never the pattern of repeated change. Reading the *whole*
    history instead surfaces it. History is read from every timestamped
    snapshot under this device's evidence directory (oldest first; the golden
    snapshot is excluded, same as ``load_latest_snapshot``), grouped into
    per-(intent, subject, field) value sequences, and a sequence is reported
    once it has accumulated at least ``min_transitions`` changes in value.
    """

    directory = _snapshot_dir(base_dir) / device_name
    paths = _timestamped_snapshot_paths(directory)
    snapshots = [json.loads(path.read_text(encoding="utf-8")) for path in paths]

    flapping: list[dict[str, Any]] = []
    for (intent, subject, field), values in _flap_sequences(snapshots).items():
        transitions = sum(1 for old, new in zip(values, values[1:], strict=False) if old != new)
        if transitions >= min_transitions:
            flapping.append(
                {
                    "intent": intent,
                    "subject": subject,
                    "field": field,
                    "transitions": transitions,
                    "values": values,
                }
            )

    flapping.sort(key=lambda item: (-item["transitions"], item["intent"], str(item["subject"]), item["field"]))
    return {"device": device_name, "snapshots_examined": len(snapshots), "flapping": flapping}
