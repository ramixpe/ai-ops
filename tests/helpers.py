"""Shared test helpers.

Not named ``test_*``, so pytest does not collect it. Imported directly by test
modules (``tests/`` is on ``sys.path`` under pytest's default import mode).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from agent_nettools.platforms import commands_for, intents_for

# Captured real-device output, committed under tests/fixtures. Resolved from this
# file rather than the working directory so tests pass from any cwd.
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

# The lab is all IOS-XR, so this is the platform every live-shaped test uses.
LAB_PLATFORM = "cisco_xr"


def platform_commands(platform: str = LAB_PLATFORM) -> list[str]:
    """Every approved command for a platform, in evidence-collection order."""

    return [
        command for intent in intents_for(platform) for command in commands_for(platform, intent)
    ]


def set_device_environment(monkeypatch):
    """Set safe test-only values for the required device environment."""

    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_PASSWORD", "test-password")


def install_fake_netmiko(monkeypatch, *, fail_commands=(), fail_connect=False):
    """Install a fake netmiko module and record every session it opens."""

    sessions = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def send_command(self, command, **kwargs):
            # **kwargs absorbs netmiko options real callers may pass (e.g.
            # ``read_timeout``, used by template commands whose Template
            # declares a per-command timeout) -- this fake only cares about
            # the command string itself.
            if command in fail_commands:
                raise OSError(f"timed out running {command}")
            return f"output for {command}"

    def fake_connect_handler(**params):
        if fail_connect:
            raise OSError("TCP connection to device failed")
        sessions.append(params)
        return FakeConnection()

    fake_netmiko = types.ModuleType("netmiko")
    fake_netmiko.ConnectHandler = fake_connect_handler
    monkeypatch.setitem(sys.modules, "netmiko", fake_netmiko)
    return sessions
