"""Safety-boundary tests.

The allowlist is the project's central guarantee, so these tests police it
structurally rather than by example: they iterate *every* platform, so adding a
vendor cannot smuggle in a state-changing command, and they assert on the MCP
module's public surface, so a generic executor cannot appear unnoticed.
"""

import pytest

from agent_nettools.network_tools import CHECK_TOOLS
from agent_nettools.platforms import (
    ALL_APPROVED_COMMANDS,
    APPROVED_COMMANDS,
    PLATFORM_INTENTS,
    all_intents,
    is_approved,
    known_platforms,
)

# State-changing verbs that must never appear in any platform's allowlist.
# Note: "configure" (not bare "config") is used on purpose so it does not match
# the substring inside "show running-config hostname".
BANNED_SNIPPETS = [
    "configure",
    "commit",
    "rollback",
    "reload",
    "no shutdown",
    "shutdown",
    "delete",
    "clear",
    "erase",
    "write",
    "copy",
    "request",  # Junos: "request system reboot"
    "restart",
    "set ",  # Junos config mode
]

# Characters that would let one approved string carry a second command, an output
# redirect, or a pipe modifier. None of the table's commands take arguments, so
# this is belt-and-braces against a careless future addition.
FORBIDDEN_CHARACTERS = ["|", ";", "&", ">", "<", "\n", "\r", "`", "$", "{", "}"]


@pytest.mark.parametrize("platform", known_platforms())
def test_approved_commands_are_read_only(platform):
    joined = "\n".join(sorted(APPROVED_COMMANDS[platform])).lower()
    for snippet in BANNED_SNIPPETS:
        assert snippet not in joined, f"{platform} allowlist contains {snippet!r}"


@pytest.mark.parametrize("platform", known_platforms())
def test_approved_commands_are_plain_single_commands(platform):
    for command in APPROVED_COMMANDS[platform]:
        for character in FORBIDDEN_CHARACTERS:
            assert character not in command, f"{command!r} contains {character!r}"
        # No format placeholders: nothing in this table is parameterized, so a
        # stray "{}" would mean an un-substituted value reaching a device.
        assert "{" not in command and "}" not in command
        assert command == command.strip()


@pytest.mark.parametrize("platform", known_platforms())
def test_every_approved_command_is_a_show_command(platform):
    """Pins that the table contains only inspection verbs.

    Phase 5 introduces validated ``ping``/``traceroute`` templates and will widen
    this to an explicit read-only verb set; until then, "show" is the whole
    surface and asserting it is the cheapest possible guard.
    """

    for command in APPROVED_COMMANDS[platform]:
        assert command.startswith("show "), f"{platform}: {command!r} is not a show command"


def test_unknown_platform_approves_nothing():
    """A device with a typo'd platform must fail closed, not open."""

    assert not is_approved("cisco_xe_typo", "show version")
    assert not is_approved("", "show version")
    # Even a command that is approved somewhere else.
    for command in ALL_APPROVED_COMMANDS:
        assert not is_approved("not-a-platform", command)


def test_commands_do_not_leak_across_platforms():
    """An IOS-XE command must not be approved for IOS-XR merely by existing.

    The allowlist is per platform precisely so that a mixed fabric cannot send
    one vendor's syntax to another's device.
    """

    xr_only = APPROVED_COMMANDS["cisco_xr"] - APPROVED_COMMANDS["cisco_iosxe"]
    iosxe_only = APPROVED_COMMANDS["cisco_iosxe"] - APPROVED_COMMANDS["cisco_xr"]
    assert "show interfaces brief" in xr_only
    assert "show ip bgp summary" in iosxe_only

    for command in iosxe_only:
        assert not is_approved("cisco_xr", command)
    for command in xr_only:
        assert not is_approved("cisco_iosxe", command)


def test_check_tool_intents_exist_in_the_platform_table():
    """A CLI subcommand with no platform definition anywhere would always be
    unsupported, which is a wiring bug rather than a fabric property."""

    assert set(CHECK_TOOLS) <= set(all_intents())
    for intent in CHECK_TOOLS:
        assert any(intent in intents for intents in PLATFORM_INTENTS.values())


def test_no_generic_run_command_is_exposed():
    # The tooling intentionally exposes narrow tools instead of arbitrary CLI execution.
    import mcp_server.server as server

    exposed_names = [name for name in dir(server) if not name.startswith("_")]
    assert "run_command" not in exposed_names
    assert "configure_device" not in exposed_names
    assert "send_command" not in exposed_names
    assert "exec_command" not in exposed_names
