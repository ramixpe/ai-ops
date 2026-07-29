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

from .inventory import get_device
from .network_tools import collect_evidence

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


def capture_device(
    device_name: str,
    *,
    label: str = "t0",
    base_dir: str | None = None,
    scrub: bool = True,
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
            continue  # "device" and "timestamp" are plain strings.
        errors.extend(section_result.get("errors", []))
        for command, output in section_result.get("data", {}).get("commands", {}).items():
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
