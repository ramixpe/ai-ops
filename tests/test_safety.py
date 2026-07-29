"""Safety-boundary tests.

The allowlist is the project's central guarantee, so these tests police it
structurally rather than by example: they iterate *every* platform, so adding a
vendor cannot smuggle in a state-changing command, and they assert on the MCP
module's public surface, so a generic executor cannot appear unnoticed.
"""

import re

import pytest

from agent_nettools.network_tools import CHECK_TOOLS
from agent_nettools.platforms import (
    ALL_APPROVED_COMMANDS,
    APPROVED_COMMANDS,
    PLATFORM_INTENTS,
    PLATFORM_TEMPLATES,
    VERB_ALLOWLIST,
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
def test_every_approved_command_starts_with_a_read_only_verb(platform):
    """Pins that the static table contains only read-only-verb commands.

    Widened for Phase 5: ``VERB_ALLOWLIST`` is the explicit set
    (``show``/``ping``/``traceroute``) that both the static allowlist and
    every template's format string must draw their first word from. Before
    Phase 5 this only ever needed to check "show"; ping/traceroute now live in
    ``PLATFORM_TEMPLATES``, not here, but the same verb set covers both so
    there is exactly one place a reviewer needs to check for "what verbs can
    ever be sent to a device".
    """

    for command in APPROVED_COMMANDS[platform]:
        verb = command.split(" ", 1)[0]
        assert verb in VERB_ALLOWLIST, f"{platform}: {command!r} does not start with a read-only verb"


@pytest.mark.parametrize("platform", known_platforms())
def test_every_template_format_string_starts_with_a_read_only_verb(platform):
    for template_name, template in PLATFORM_TEMPLATES.get(platform, {}).items():
        verb = template.format_string.split(" ", 1)[0]
        assert verb in VERB_ALLOWLIST, (
            f"{platform}.{template_name}: {template.format_string!r} does not start with a "
            "read-only verb"
        )


@pytest.mark.parametrize("platform", known_platforms())
def test_every_template_format_string_is_free_of_banned_snippets(platform):
    for template_name, template in PLATFORM_TEMPLATES.get(platform, {}).items():
        lowered = template.format_string.lower()
        for snippet in BANNED_SNIPPETS:
            assert snippet not in lowered, (
                f"{platform}.{template_name} format string contains {snippet!r}"
            )


@pytest.mark.parametrize("platform", known_platforms())
def test_every_template_format_string_has_only_safe_characters(platform):
    """A template's *literal* text must never itself carry a forbidden
    character -- this is independent of whatever a caller later supplies,
    since the literal parts of the format string are never validated at
    render time (only the substituted parameter values are)."""

    for template_name, template in PLATFORM_TEMPLATES.get(platform, {}).items():
        # "{" and "}" are expected around a placeholder name -- that is the
        # one legitimate use of either character in a template. Strip
        # placeholders out first so this test polices the *literal* text
        # around them, not the substitution syntax itself.
        literal_text = re.sub(r"\{\w+\}", "", template.format_string)
        for character in FORBIDDEN_CHARACTERS:
            assert character not in literal_text, (
                f"{platform}.{template_name}: {template.format_string!r} contains {character!r} "
                "outside of a placeholder"
            )


@pytest.mark.parametrize("platform", known_platforms())
def test_every_template_placeholder_matches_its_declared_parameters(platform):
    """Every ``{name}`` in a format string must have a declared parameter of
    that name, and every declared parameter must actually appear in the
    format string -- an un-declared placeholder would let unsubstituted text
    reach ``.format()`` uncontrolled, and an unused declared parameter would
    mean a validated value that never actually lands in the rendered
    command (a silent no-op at best, a sign of a copy-paste bug at worst)."""

    for template_name, template in PLATFORM_TEMPLATES.get(platform, {}).items():
        placeholders = set(re.findall(r"\{(\w+)\}", template.format_string))
        declared = set(template.params)
        assert placeholders == declared, (
            f"{platform}.{template_name}: placeholders {placeholders} != declared params {declared}"
        )


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
