"""Guard against documentation drifting from the code.

These tests fail if the README's approved-command list or the MCP server
README's tool list stops matching what the code actually exposes.
"""

import re
from pathlib import Path

import mcp_server.server as server
from agent_nettools.network_tools import APPROVED_COMMANDS

REPO_ROOT = Path(__file__).resolve().parents[1]


def _backticked_between(text: str, start_marker: str, end_marker: str) -> set[str]:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    section = text[start:end]
    return set(re.findall(r"`([^`]+)`", section))


def test_readme_lists_exactly_the_approved_commands():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    documented = _backticked_between(
        readme,
        "Approved read-only commands:",
        "There is no configuration mode",
    )
    assert documented == APPROVED_COMMANDS


def test_mcp_readme_lists_exactly_the_exposed_tools():
    exposed = {
        name
        for name, obj in vars(server).items()
        if callable(obj)
        and name.startswith(("list_lab", "get_lab", "check_lab", "collect_lab"))
    }
    mcp_readme = (REPO_ROOT / "mcp_server" / "README.md").read_text(encoding="utf-8")
    documented = _backticked_between(mcp_readme, "## Exposed Tools", "There is no shell")
    assert documented == exposed
