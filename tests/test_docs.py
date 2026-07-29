"""Guard against documentation drifting from the code.

These tests fail if the README's per-platform allowlist or the MCP server
README's tool list stops matching what the code actually exposes. They match on
literal marker strings, so rewording that prose means updating this file too.
"""

import re
from pathlib import Path

import pytest

import mcp_server.server as server
from agent_nettools.devices_doc import render_devices_doc
from agent_nettools.platforms import APPROVED_COMMANDS, known_platforms

REPO_ROOT = Path(__file__).resolve().parents[1]


def _backticked_between(text: str, start_marker: str, end_marker: str) -> set[str]:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    section = text[start:end]
    return set(re.findall(r"`([^`]+)`", section))


def _readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", known_platforms())
def test_readme_lists_exactly_the_approved_commands_per_platform(platform):
    """Every platform gets its own README block, matching its own allowlist.

    A per-platform assertion is what makes the safety boundary reviewable: the
    reader can see everything that may ever be sent to one vendor in one place.
    """

    readme = _readme()
    heading = f"### {platform}"
    assert heading in readme, f"README has no section for platform {platform}"

    start = readme.index(heading)
    # The block ends at the next platform heading, or at the closing prose.
    candidates = [
        readme.index(f"### {other}", start)
        for other in known_platforms()
        if other != platform and f"### {other}" in readme[start:]
    ]
    candidates.append(readme.index("Only `cisco_xr` is verified", start))
    end = min(candidates)

    documented = set(re.findall(r"`([^`]+)`", readme[start:end]))
    assert documented == set(APPROVED_COMMANDS[platform])


def test_readme_documents_every_known_platform():
    """A platform added to the table without a README block would ship an
    undocumented command surface."""

    readme = _readme()
    for platform in known_platforms():
        assert f"### {platform}" in readme


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


def test_devices_doc_matches_rendered_inventory():
    """docs/devices.md is generated, not hand-maintained -- so it cannot drift
    from the inventory it describes."""

    committed = (REPO_ROOT / "docs" / "devices.md").read_text(encoding="utf-8")
    assert committed == render_devices_doc()
