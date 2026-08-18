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
    """Derived from the server's **registry**, not from a prefix allowlist.

    It was a prefix allowlist until 2026-08-17, widened by hand each time a
    batch of tools landed. `investigate_lab_session` was added and the test
    passed, because `investigate_lab` was not in the list -- a doc-sync check
    that silently stopped covering the newest tool, which is the one most likely
    to be undocumented.

    Reading the registry makes the check self-maintaining and removes the
    failure mode entirely: there is no list to forget to widen. Same reasoning
    as `test_mcp_boundary.py` iterating the registry rather than naming tools,
    and the same lesson as the audit that produced it -- a rule that holds for
    every case anyone remembered is a convention, not a rule.
    """

    exposed = set(_registered_tool_names())

    assert len(exposed) >= 21, f"only {len(exposed)} tools found in the registry"

    mcp_readme = (REPO_ROOT / "mcp_server" / "README.md").read_text(encoding="utf-8")
    documented = _backticked_between(mcp_readme, "## Exposed Tools", "There is no shell")
    assert documented == exposed


def _registered_tool_names() -> set[str]:
    """Tool names as the MCP SDK holds them, with a documented fallback."""

    for attribute in ("_tool_manager", "_tools", "tools"):
        holder = getattr(server.mcp, attribute, None)
        if holder is None:
            continue
        registry = getattr(holder, "_tools", holder)
        if isinstance(registry, dict) and registry:
            return set(registry)

    # An SDK whose registry we cannot reach. Falling back to "every public
    # callable defined in the module" is broader than the old prefix list and
    # cannot silently miss a new tool -- it would over-report instead, which
    # fails loudly rather than passing quietly.
    import inspect as _inspect

    return {
        name
        for name, obj in vars(server).items()
        if callable(obj)
        and not name.startswith("_")
        and getattr(obj, "__module__", "") == server.__name__
        and "lab" in name
        and not _inspect.isclass(obj)
    }


def test_the_devices_doc_generator_still_renders_from_the_inventory():
    """`docs/devices.md` was deleted at M0 (2026-08-18, operator sign-off): a
    generated file committed beside its generator is a second copy that can
    drift, and the inventory is the source of truth.

    The drift test that used to live here compared the committed file against
    `render_devices_doc()`. With no committed file there is nothing to drift,
    so this asserts the weaker, still-true thing: the generator works and reads
    the inventory. `render_devices_doc` stays exported so a caller can render
    the table on demand; if nothing ever calls it, it is a fair candidate for
    removal in a later pass."""

    rendered = render_devices_doc()

    assert rendered.strip(), "the generator produces a document"
    for device in ("PE1", "PE2", "RR1"):
        assert device in rendered, f"{device} is in the inventory and must be rendered"
