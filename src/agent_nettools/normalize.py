"""Strip self-changing text from command output so it can be compared.

This is the fallback comparison path, used when no parser exists for a
platform/intent (see ``parsers.py``). It handles the problem that made
``nettools diff`` useless: **every IOS-XR show command prefixes its output with
the current time**, so a byte-for-byte comparison reports every command as
changed on every run.

Measured against the committed t0/t1 fixture pair, the preamble alone accounted
for all 63 of 63 spurious "changed" reports across the nine lab devices; three of
the seven commands differed by *nothing else*.

Two mechanisms, kept separate because they have different scopes:

- ``PREAMBLE_PATTERNS`` -- per *platform*. Whole lines dropped from the top of
  every command's output. This is a property of the vendor's CLI, not of any one
  command.
- ``VOLATILE_TEXT_PATTERNS`` -- per *platform and intent*. Substitutions that mask
  a moving value in place, keeping the surrounding structure so the text stays
  readable and the rest of the line is still compared.

Masking rather than deleting is deliberate: a line whose counter was masked still
diffs if anything *else* on it changed.
"""

from __future__ import annotations

import re

VOLATILE_MARKER = "[VOLATILE]"

# Lines to drop from the top of any command's output, per platform. IOS-XR emits
# a timestamp such as "Wed Jul 29 11:59:09.555 UTC" as the first non-blank line of
# every show command.
PREAMBLE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "cisco_xr": (
        re.compile(
            r"^[A-Z][a-z]{2} [A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2}\.\d{1,6} [A-Z]{2,5}$"
        ),
    ),
    # Junos prefixes output when a timestamp is configured; harmless if absent.
    "juniper_junos": (
        re.compile(r"^[A-Z][a-z]{2} [A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2} [A-Z]{2,5}$"),
    ),
    "cisco_iosxe": (),
}

# In-place masks for values that move on their own, per platform and intent.
# Derived from the measured t0/t1 fixture diff, not from assumption.
VOLATILE_TEXT_PATTERNS: dict[tuple[str, str], tuple[tuple[re.Pattern[str], str], ...]] = {
    ("cisco_xr", "facts"): (
        # "PE1 uptime is 15 hours, 36 minutes"
        (re.compile(r"^(?P<host>\S+) uptime is .*$", re.MULTILINE), r"\g<host> uptime is " + VOLATILE_MARKER),
    ),
    ("cisco_xr", "bgp"): (
        # Neighbour row: MsgRcvd, MsgSent, and the Up/Down timer all move on
        # keepalives alone. Anchored on the row shape so the state column, which
        # is the signal, is left intact and still compared.
        (
            re.compile(
                r"^(?P<head>\S+\s+\d+\s+\d+)\s+\d+\s+\d+"
                r"(?P<mid>\s+\S+\s+\S+\s+\S+)\s+\S+"
                r"(?P<tail>\s+\S+)\s*$",
                re.MULTILINE,
            ),
            r"\g<head> " + VOLATILE_MARKER + r" " + VOLATILE_MARKER + r"\g<mid> " + VOLATILE_MARKER + r"\g<tail>",
        ),
    ),
    ("cisco_xr", "isis"): (
        # "P2  Gi0/0/0/1  *PtoP*  Up  21  L2  Capable" -- Holdtime counts down.
        (
            re.compile(
                r"^(?P<head>\S+\s+\S+\s+\S+\s+\S+)\s+\d+(?P<tail>\s+\S+\s+\S+\s*)$",
                re.MULTILINE,
            ),
            r"\g<head> " + VOLATILE_MARKER + r"\g<tail>",
        ),
    ),
    ("cisco_xr", "sr"): (
        # "Operational: up for 15:32:34 (since Jul 28 20:25:23.937)" -- the
        # duration moves, the absolute "since" does not and is the better signal.
        (
            re.compile(r"(?P<state>Operational: \S+) for \S+", re.MULTILINE),
            r"\g<state> for " + VOLATILE_MARKER,
        ),
    ),
}


def strip_preamble(text: str, *, platform: str) -> str:
    """Drop the vendor's per-command output preamble.

    Only leading blank lines and matching preamble lines are removed, so a
    timestamp-shaped line appearing legitimately later in the output survives.
    """

    patterns = PREAMBLE_PATTERNS.get(platform, ())
    if not patterns:
        return text

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if any(pattern.match(stripped) for pattern in patterns):
            index += 1
            continue
        break
    return "\n".join(lines[index:])


def mask_volatile(text: str, *, platform: str, intent: str) -> str:
    """Replace self-changing values in place, keeping the surrounding structure."""

    for pattern, replacement in VOLATILE_TEXT_PATTERNS.get((platform, intent), ()):
        text = pattern.sub(replacement, text)
    return text


def normalize_output(text: str, *, platform: str, intent: str) -> str:
    """Return output stripped of its preamble and with volatile values masked.

    Trailing whitespace is also normalized: IOS-XR pads some table rows, and a
    change in padding is not a change in state.
    """

    text = strip_preamble(text, platform=platform)
    text = mask_volatile(text, platform=platform, intent=intent)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()
