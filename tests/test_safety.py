from agent_nettools.network_tools import APPROVED_COMMANDS

# IOS-XR state-changing verbs that must never appear in the approved allowlist.
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
]


def test_approved_commands_are_read_only():
    joined = "\n".join(APPROVED_COMMANDS).lower()
    for snippet in BANNED_SNIPPETS:
        assert snippet not in joined


def test_no_generic_run_command_is_exposed():
    # The tooling intentionally exposes narrow tools instead of arbitrary CLI execution.
    import mcp_server.server as server

    exposed_names = [name for name in dir(server) if not name.startswith("_")]
    assert "run_command" not in exposed_names
    assert "configure_device" not in exposed_names
