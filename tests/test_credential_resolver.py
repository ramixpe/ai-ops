"""Pluggable credential resolution (Phase 8): env (default) and file providers.

Also extends the project's "credentials never leak" guarantee (see
``test_network_tools.py::test_list_devices_does_not_expose_credentials``) to
the file provider and to the resolver's own return value, so a new provider
cannot quietly introduce a leak path.
"""

from __future__ import annotations

import json

import pytest

from agent_nettools.credential_resolver import (
    DEFAULT_CREDENTIAL_PROVIDER,
    EnvCredentialResolver,
    FileCredentialResolver,
    get_resolver,
    known_credential_providers,
)
from agent_nettools.inventory import InventoryError, get_device, load_inventory
from agent_nettools.inventory_model import CredentialGroup
from agent_nettools.network_tools import _run_approved_commands


def _group(**overrides) -> CredentialGroup:
    defaults = {"username_env": "DEVICE_USERNAME", "password_env": "DEVICE_PASSWORD"}
    defaults.update(overrides)
    return CredentialGroup(**defaults)


def test_default_provider_is_env():
    assert DEFAULT_CREDENTIAL_PROVIDER == "env"
    assert isinstance(get_resolver(), EnvCredentialResolver)


def test_known_credential_providers_lists_both():
    assert known_credential_providers() == ("env", "file")


# --------------------------------------------------------------------------- #
# EnvCredentialResolver: behavior-identical to the pre-Phase-8 inline code.
# --------------------------------------------------------------------------- #


def test_env_resolver_reads_values_directly(monkeypatch):
    monkeypatch.setenv("DEVICE_USERNAME", "alice")
    monkeypatch.setenv("DEVICE_PASSWORD", "s3cret")

    resolved = EnvCredentialResolver().resolve(_group())

    assert resolved == {"username": "alice", "password": "s3cret", "key_file": None}


def test_env_resolver_requires_username_and_password(monkeypatch):
    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    with pytest.raises(InventoryError, match="DEVICE_USERNAME"):
        EnvCredentialResolver().resolve(_group())


def test_env_resolver_keyfile_makes_password_optional(monkeypatch, tmp_path):
    keyfile = tmp_path / "id_lab"
    keyfile.write_text("not a real key", encoding="utf-8")

    monkeypatch.setenv("DEVICE_USERNAME", "alice")
    monkeypatch.setenv("DEVICE_SSH_KEYFILE", str(keyfile))
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    resolved = EnvCredentialResolver().resolve(
        _group(ssh_keyfile_env="DEVICE_SSH_KEYFILE")
    )

    assert resolved == {"username": "alice", "password": "", "key_file": str(keyfile)}


# --------------------------------------------------------------------------- #
# FileCredentialResolver: Docker/Kubernetes secrets convention.
# --------------------------------------------------------------------------- #


def test_file_resolver_reads_secret_content_from_the_named_path(monkeypatch, tmp_path):
    username_file = tmp_path / "username"
    password_file = tmp_path / "password"
    username_file.write_text("alice\n", encoding="utf-8")
    password_file.write_text("s3cret\n", encoding="utf-8")

    monkeypatch.setenv("DEVICE_USERNAME", str(username_file))
    monkeypatch.setenv("DEVICE_PASSWORD", str(password_file))

    resolved = FileCredentialResolver().resolve(_group())

    # Trailing newline stripped -- the file holds exactly what a Docker/K8s
    # secret mount would (one value, often newline-terminated by the tool that
    # wrote it).
    assert resolved == {"username": "alice", "password": "s3cret", "key_file": None}


def test_file_resolver_missing_file_raises_inventory_error(monkeypatch, tmp_path):
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("DEVICE_USERNAME", str(missing))
    monkeypatch.setenv("DEVICE_PASSWORD", "irrelevant, username fails first")

    with pytest.raises(InventoryError, match="DEVICE_USERNAME"):
        FileCredentialResolver().resolve(_group())


def test_file_resolver_missing_env_var_raises_before_touching_the_filesystem(monkeypatch):
    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    with pytest.raises(InventoryError, match="DEVICE_USERNAME"):
        FileCredentialResolver().resolve(_group())


def test_file_resolver_keyfile_still_a_direct_path_not_a_secret_file(monkeypatch, tmp_path):
    """The SSH key file path is unaffected by the provider -- it was already a
    path netmiko reads directly in both, never a value fetched from elsewhere."""

    username_file = tmp_path / "username"
    username_file.write_text("alice", encoding="utf-8")
    keyfile = tmp_path / "id_lab"
    keyfile.write_text("not a real key", encoding="utf-8")

    monkeypatch.setenv("DEVICE_USERNAME", str(username_file))
    monkeypatch.setenv("DEVICE_SSH_KEYFILE", str(keyfile))
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    resolved = FileCredentialResolver().resolve(
        _group(ssh_keyfile_env="DEVICE_SSH_KEYFILE")
    )

    assert resolved == {"username": "alice", "password": "", "key_file": str(keyfile)}


# --------------------------------------------------------------------------- #
# Provider selection.
# --------------------------------------------------------------------------- #


def test_get_resolver_selects_file_provider_via_environment(monkeypatch):
    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "file")
    assert isinstance(get_resolver(), FileCredentialResolver)


def test_get_resolver_rejects_an_unknown_provider(monkeypatch):
    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "vault")
    with pytest.raises(InventoryError, match="Unknown NETTOOLS_CREDENTIAL_PROVIDER"):
        get_resolver()


def test_inventory_load_inventory_works_end_to_end_with_the_file_provider(monkeypatch, tmp_path):
    """A full load_inventory() call, switched to the file provider -- proves the
    plug point is wired all the way through, not just unit-tested in isolation."""

    username_file = tmp_path / "username"
    password_file = tmp_path / "password"
    username_file.write_text("alice", encoding="utf-8")
    password_file.write_text("s3cret", encoding="utf-8")

    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "file")
    monkeypatch.setenv("DEVICE_USERNAME", str(username_file))
    monkeypatch.setenv("DEVICE_PASSWORD", str(password_file))

    devices = load_inventory()
    pe1 = next(device for device in devices if device["name"] == "PE1")

    assert pe1["username"] == "alice"
    assert pe1["password"] == "s3cret"

    pe1_direct = get_device("PE1")
    assert pe1_direct["username"] == "alice"
    assert pe1_direct["password"] == "s3cret"


# --------------------------------------------------------------------------- #
# The safety invariant: switching providers must never move credential access
# earlier than the allowlist check. Both tests run with an empty environment,
# same as the two pinned tests in test_network_tools.py.
# --------------------------------------------------------------------------- #


def test_file_provider_selected_still_refuses_unapproved_commands_with_no_credentials(
    monkeypatch,
):
    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "file")

    result = _run_approved_commands("PE1", ["configure"])

    assert result["status"] == "error"
    assert "Refusing unapproved commands" in result["errors"][0]


def test_unknown_provider_configured_still_refuses_unapproved_commands(monkeypatch):
    """Even a *misconfigured* resolver (a typo'd provider name) must not move
    credential resolution ahead of the allowlist check: the allowlist check
    never constructs a resolver at all."""

    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "not-a-real-provider")

    result = _run_approved_commands("PE1", ["configure"])

    assert result["status"] == "error"
    assert "Refusing unapproved commands" in result["errors"][0]


# --------------------------------------------------------------------------- #
# Credential-leak coverage, extended to the file provider's secrets and the
# resolver's own return value.
# --------------------------------------------------------------------------- #


def test_file_provider_secrets_do_not_leak_into_list_devices(monkeypatch, tmp_path):
    username_file = tmp_path / "username"
    password_file = tmp_path / "password"
    username_file.write_text("alice", encoding="utf-8")
    password_file.write_text("s3cret-file-payload", encoding="utf-8")

    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "file")
    monkeypatch.setenv("DEVICE_USERNAME", str(username_file))
    monkeypatch.setenv("DEVICE_PASSWORD", str(password_file))

    from agent_nettools.network_tools import list_devices

    result = list_devices()
    serialized = json.dumps(result)

    assert "s3cret-file-payload" not in serialized
    assert "alice" not in serialized


def test_resolver_return_value_carries_no_extra_fields_to_accidentally_serialize(monkeypatch):
    """The resolver's contract is exactly three keys -- pins the shape so a
    future provider cannot smuggle a fourth field (e.g. a raw API token) that
    some caller might serialize without knowing to redact it."""

    monkeypatch.setenv("DEVICE_USERNAME", "alice")
    monkeypatch.setenv("DEVICE_PASSWORD", "s3cret")

    resolved = get_resolver().resolve(_group())

    assert set(resolved) == {"username", "password", "key_file"}
