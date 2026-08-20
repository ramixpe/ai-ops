"""Capture real device output into a fixtures tree, and replay it offline.

Fixtures exist so parser, diff, and health-rule work is developable and testable
without lab access. Layout::

    tests/fixtures/<platform>/<device>/<label>/<command-slug>.txt

The replay path deliberately goes through the ``sender=`` seam that
``network_tools`` already exposes, so a fixture-backed evidence collection reuses
the real envelope construction, section slicing, and error attribution rather
than reimplementing them. A missing fixture file therefore surfaces as the same
structured error a failed command would.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from . import parsers
from .interface_kind import is_physical_member
from .inventory import get_device
from .network_tools import collect_evidence, run_templates

# Fixtures land here, relative to the working directory unless overridden.
DEFAULT_FIXTURE_DIR = "tests/fixtures"

# Labels for a capture pair taken minutes apart on a quiet fabric. Diffing t0
# against t1 must report no meaningful change; anything it does report is
# volatile-field noise.
QUIET_PAIR_LABELS = ("t0", "t1")

# Patterns scrubbed before a fixture is written. Fixtures are committed to git
# and are permanent once pushed, so anything credential-shaped is replaced even
# though the approved commands are not expected to return it. Management IPs and
# hostnames are deliberately *not* scrubbed: they are lab-private and already
# published in README.md and docs/devices.md.
SCRUB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Config secrets, in any of the IOS-XR / IOS / JunOS spellings.
    (
        re.compile(
            r"(?im)^(?P<prefix>\s*(?:\S+\s+)*?"
            r"(?:secret|password|passwd|pre-shared-key|authentication-key|md5)\s+)\S+.*$"
        ),
        r"\g<prefix>[SCRUBBED]",
    ),
    # Chassis / module serial numbers.
    (
        re.compile(r"(?im)^(?P<prefix>.*\bserial\s*(?:number)?\b\s*[:=]?\s*)\S+"),
        r"\g<prefix>[SCRUBBED]",
    ),
    # Private key blocks, should a config-adjacent command ever return one.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[SCRUBBED PRIVATE KEY]",
    ),
)


def command_slug(command: str) -> str:
    """Return a filesystem-safe file stem for an approved command.

    ``show running-config hostname`` becomes ``show-running-config-hostname``.
    The mapping is not reversed by parsing filenames; callers slug the command
    they are looking for and read that file.
    """

    return re.sub(r"[^a-z0-9]+", "-", command.lower()).strip("-")


def scrub_output(text: str) -> str:
    """Replace credential- and serial-shaped material in captured output."""

    for pattern, replacement in SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _fixture_dir(base_dir: str | None) -> Path:
    return Path(base_dir or os.getenv("NETTOOLS_FIXTURE_DIR") or DEFAULT_FIXTURE_DIR)


def fixture_path(
    device: dict[str, Any],
    command: str,
    *,
    label: str,
    base_dir: str | None = None,
) -> Path:
    """Return the fixture file for one command on one device capture."""

    return (
        _fixture_dir(base_dir)
        / str(device["platform"])
        / str(device["name"])
        / label
        / f"{command_slug(command)}.txt"
    )


# --------------------------------------------------------------------------- #
# Template capture (T-011)
# --------------------------------------------------------------------------- #

# The loopbacks that are BGP subjects in this fabric. `show bgp neighbor <ip>`
# and `show route <ip>/32` are captured for every one of these *except* the
# capturing device's own, which is never its own peer.
BGP_SUBJECT_LOOPBACKS: tuple[str, ...] = (
    "10.255.0.11",  # PE1
    "10.255.0.12",  # PE2
    "10.255.0.13",  # PE3
    "10.255.0.14",  # PE4
    "10.255.0.31",  # RR1
)

# `show logging last N`. 200 is enough to carry a fault's own log lines without
# turning every fixture into a megabyte of the SSH churn that dominates this
# fabric's syslog (see docs/build/discovery-loki.md section 6).
LOGGING_LINES = 200

# The reachability target for ping/traceroute. RR1 is the natural choice --
# every PE peers with it -- so RR1 itself probes PE1 instead.
PROBE_TARGET = "10.255.0.31"
PROBE_TARGET_FALLBACK = "10.255.0.11"


def _capturable_interfaces(evidence: dict[str, Any]) -> list[str]:
    """Interface names worth a per-interface capture, from the device's own brief.

    Derived from what the device actually reports rather than hardcoded, so the
    manifest cannot drift from the fabric. Narrowed to physical Gigabit
    interfaces plus ``Lo0``: those are what the descent's interface rung reads
    (line state, error counters, carrier transitions). Other loopbacks carry no
    counters worth diffing, and the ``srte_*`` tunnels are dynamic -- capturing
    them would make the fixture set churn for reasons unrelated to any fault.
    """

    section = evidence.get("interfaces")
    if not isinstance(section, dict):
        return []
    parsed = section.get("data", {}).get("parsed")
    if not parsed or section.get("data", {}).get("parse_status") != parsers.PARSE_OK:
        return []

    # Deliberately *not* the descent's member set, and expressed against the
    # same predicate so the difference is visible rather than accidental. A
    # capture manifest wants the physical ports **and** `Lo0`, because a
    # loopback's address is what a route resolves to; the descent's interface
    # rung wants ports only. Three copies of a near-identical rule (B-431) is
    # how "near-identical" stops being true without anyone noticing.
    names: list[str] = []
    for record in parsed.get("records", []):
        name = record.get("interface", "")
        if is_physical_member(name) or name == "Lo0":
            names.append(name)
    return names


def template_manifest_for(
    *,
    interfaces: list[str],
    router_id: str | None = None,
) -> list[tuple[str, dict[str, str]]]:
    """Build one device's template capture manifest.

    Implements docs/build/capture-manifest.md section 5b. Returned as
    ``(template_name, params)`` pairs for ``run_templates``, which validates
    every one of them by reconstruction before any credential is loaded.

    Takes no device identity of its own -- ``router_id`` (to skip a device's
    own loopback and pick its ping/traceroute target) and ``interfaces`` (the
    per-interface entries) are the only per-device facts this manifest is
    built from. A ``device_name`` parameter was here through B-511-era
    callers and never read in this body (unused-params sweep, release-1.0
    cleanup); all four callers -- the one production call site and three
    direct test calls -- passed a string whose value never once reached an
    assertion or a manifest entry.
    """

    manifest: list[tuple[str, dict[str, str]]] = []

    for loopback in BGP_SUBJECT_LOOPBACKS:
        if loopback == router_id:
            continue  # a device is never its own BGP peer
        manifest.append(("bgp_neighbor", {"address": loopback}))
        manifest.append(("route", {"prefix": f"{loopback}/32"}))

    for interface in interfaces:
        manifest.append(("interface", {"interface": interface}))
        # B-104: the config axis (D16), captured over the same interface set
        # as the status-side "interface" template just above -- one
        # observed/intended pair per interface is what B-105/B-106 need to
        # diff later, and it is the same physical-members-plus-Lo0 scope
        # `_capturable_interfaces` already narrows this manifest to.
        manifest.append(("config_interface", {"interface": interface}))

    # One per device, not per interface: `show running-config router isis`
    # is the whole process, and IOS-XR nests every interface's ISIS
    # participation inside it -- see config_section.py's module docstring.
    manifest.append(("config_isis", {}))

    manifest.append(("logging", {"count": str(LOGGING_LINES)}))

    target = PROBE_TARGET_FALLBACK if router_id == PROBE_TARGET else PROBE_TARGET
    manifest.append(("ping", {"address": target}))
    manifest.append(("traceroute", {"address": target}))

    return manifest


def capture_device_templates(
    device_name: str,
    manifest: list[tuple[str, dict[str, str]]],
    *,
    label: str = "t0",
    base_dir: str | None = None,
    scrub: bool = True,
) -> dict[str, Any]:
    """Capture rendered template output to the fixtures tree, in one session.

    Uses ``run_templates`` rather than a loop over ``run_template``: the loop
    opens one login per command, and IOS-XR rate-limits repeated logins. See
    that function's docstring, and OBS-027.

    Writes through the same ``fixture_path``/``command_slug``/``scrub_output``
    pipeline ``capture_device`` uses, so template fixtures are addressed by
    their rendered command and replay through the existing ``sender=`` seam
    with no change to the read path.
    """

    device = get_device(device_name)
    result = run_templates(device_name, manifest)

    written: list[str] = []
    for command, output in result.get("data", {}).get("commands", {}).items():
        text = scrub_output(output) if scrub else output
        path = fixture_path(device, command, label=label, base_dir=base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        written.append(str(path))

    return {
        "device": device_name,
        "platform": device["platform"],
        "label": label,
        "scrubbed": scrub,
        "written": written,
        "requested": len(manifest),
        # Reported, never swallowed: a partial capture must not be mistaken for
        # a complete one, which is the same contract capture_device holds.
        "errors": list(dict.fromkeys(result.get("errors", []))),
    }


def capture_device(
    device_name: str,
    *,
    label: str = "t0",
    base_dir: str | None = None,
    scrub: bool = True,
    templates: bool = False,
) -> dict[str, Any]:
    """Capture one device's full evidence bundle to the fixtures tree.

    Uses ``collect_evidence``, so this is a single SSH session for all approved
    commands. Only commands that actually returned output are written; failures
    are reported so a partial capture is never mistaken for a complete one.
    """

    device = get_device(device_name)
    evidence = collect_evidence(device_name)

    written: list[str] = []
    errors: list[str] = []
    for section_result in evidence.values():
        if not isinstance(section_result, dict):
            continue  # "device", "platform", and "timestamp" are plain strings.
        errors.extend(section_result.get("errors", []))
        # An unsupported intent has no commands and no errors, so it contributes
        # nothing here -- correct, there is no output to capture.
        for command, output in section_result.get("data", {}).get("commands", {}).items():
            text = scrub_output(output) if scrub else output
            path = fixture_path(device, command, label=label, base_dir=base_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
            written.append(str(path))

    if templates:
        # Two sessions per device, not one: collect_evidence owns its own
        # session for the intents, and the templates get a second. Merging them
        # into one would mean reworking collect_evidence, which is not worth the
        # regression risk against a one-shot capture window -- and two logins
        # per device is already 15x better than the per-command loop OBS-027
        # measured.
        #
        # The interface list is derived from the evidence just collected, so the
        # manifest always matches what this device actually has.
        from .inventory_model import find_device

        entry = find_device(device_name)
        manifest = template_manifest_for(
            interfaces=_capturable_interfaces(evidence),
            router_id=getattr(entry, "router_id", None),
        )
        template_result = capture_device_templates(
            device_name, manifest, label=label, base_dir=base_dir, scrub=scrub
        )
        written.extend(template_result["written"])
        errors.extend(template_result["errors"])

    return {
        "device": device_name,
        "platform": device["platform"],
        "label": label,
        "scrubbed": scrub,
        "written": written,
        # A connection-level failure is appended to every section; report it once.
        "errors": list(dict.fromkeys(errors)),
    }


def fixture_sender(
    *,
    label: str = "t0",
    base_dir: str | None = None,
) -> Callable[[dict[str, Any], str], str]:
    """Return a ``sender`` that serves captured fixtures instead of live SSH.

    Pass the result to any check or to ``collect_evidence``. Platform and device
    name are read from the device record handed to the sender, so the same sender
    works across a whole fabric replay.
    """

    def sender(device: dict[str, Any], command: str) -> str:
        return fixture_path(device, command, label=label, base_dir=base_dir).read_text(
            encoding="utf-8"
        )

    return sender


def load_fixture_evidence(
    device_name: str,
    *,
    label: str = "t0",
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Rebuild a full evidence collection from captured fixtures."""

    return collect_evidence(
        device_name, sender=fixture_sender(label=label, base_dir=base_dir)
    )
