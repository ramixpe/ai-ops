"""The one IOS-XR syslog wire-format parser used by all event inputs."""

from __future__ import annotations

import re

__all__ = ["IOSXR_SYSLOG_LINE", "match_iosxr_syslog_line"]


# The device buffer has no leading host token; syslog-ng adds exactly one.
# Keeping that framing optional here lets every consumer share field parsing
# while each retains its own source-specific admission policy.
IOSXR_SYSLOG_LINE = re.compile(
    r"^(?:(?P<host>\S+)\s+)?(?P<node>RP/0/RP0/CPU0):"
    r"(?P<timestamp>\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\.\d+\s+\w+): "
    r"(?P<process>[A-Za-z0-9_]+)\[(?P<pid>\d+)\]: "
    r"%(?P<mnemonic>[A-Za-z0-9_-]+) : (?P<text>.*)$"
)


def match_iosxr_syslog_line(line: str) -> re.Match[str] | None:
    """Parse one buffer or syslog-ng-framed IOS-XR event line."""

    return IOSXR_SYSLOG_LINE.match(line)