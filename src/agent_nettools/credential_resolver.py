"""Pluggable credential resolution: where a device's username/password/key actually come from.

Before this module, ``inventory.py`` read every credential straight off
``os.environ`` inline. That is fine for a single-user lab, but it hardcodes one
assumption -- "the value is already the secret" -- into the one place secrets
are ever touched. This module pulls that assumption out into a small strategy
interface (``CredentialResolver``) with two real, testable implementations:

- ``EnvCredentialResolver`` (the default, and the only behavior this project
  had through Phase 7): the credential group's ``username_env``/
  ``password_env``/``ssh_keyfile_env`` name environment variables whose
  *value* is the secret itself.
- ``FileCredentialResolver``: the Docker/Kubernetes secrets convention. The
  same named environment variables are still read, but their value is a
  *file path*, and the secret is that file's content -- matching how a
  container orchestrator mounts one secret per file (e.g. under
  ``/run/secrets/<name>``) and an app is told where via an env var. The SSH
  key file itself is unaffected by either provider: it was already a path
  netmiko reads directly, not a value this module resolves.

Selected by ``NETTOOLS_CREDENTIAL_PROVIDER`` (``env``, the default, or
``file``), read once per ``get_resolver()`` call so tests can monkeypatch it
freely.

**Extension point for a real secret manager (Vault, AWS Secrets Manager, ...).**
This project deliberately does not ship one: there is no such service
available in this lab to test against, and an untested credential path is
worse than an honest gap (see the Phase 8 notes in ``CLAUDE.md``/``README.md``).
A real backend would:

1. Subclass ``CredentialResolver`` and implement ``_read_required``/
   ``_read_optional`` -- each takes the *name* of a credential-group field
   (``group.username_env``, etc., which under this backend would name a
   secret path or key in the external store, not an OS environment variable)
   and returns the resolved string, raising ``InventoryError`` (never a raw
   SDK exception) on any failure.
2. Authenticate to the backend itself however that backend requires (a
   token, an instance role, a config file) -- entirely inside the resolver;
   nothing upstream of it should need to know.
3. Never log, print, or return the raw secret through any channel other than
   the ``{"username", "password", "key_file"}`` dict ``resolve()`` already
   returns -- the same rule ``EnvCredentialResolver``/``FileCredentialResolver``
   already follow, and what ``tests/test_credential_resolver.py`` pins.
4. Register itself in ``_RESOLVERS`` under a new provider name so
   ``NETTOOLS_CREDENTIAL_PROVIDER`` can select it.

**Safety invariant, unchanged.** Nothing in this module is ever imported by
``lab.py``/``inventory_model.py``/``platforms.py`` (the credential-free
layers): ``inventory.load_inventory()``/``get_device()`` are still the only
callers, at the same point in the call chain as before this module existed.
Platform resolution and the allowlist check still need zero credential
access, regardless of which provider is configured -- see CLAUDE.md, "The
safety boundary".
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from .inventory_model import CredentialGroup, InventoryError

NETTOOLS_CREDENTIAL_PROVIDER_ENV = "NETTOOLS_CREDENTIAL_PROVIDER"
DEFAULT_CREDENTIAL_PROVIDER = "env"


class CredentialResolver(ABC):
    """Resolves one credential group into a device's username/password/key file.

    ``resolve()`` implements the shared shape both providers follow --
    username is always required; the key file, if the named environment
    variable is set, makes the password merely an *optional* passphrase
    instead of a required password -- and defers only "how does a named field
    become a string" to the two abstract methods, which is the only thing
    that differs between reading a value directly and reading it from a file.
    """

    def resolve(self, group: CredentialGroup) -> dict[str, str | None]:
        """Return ``{"username": str, "password": str, "key_file": str | None}``.

        The SSH key file path itself is read the same way regardless of
        provider -- it is already a filesystem path in both, never a value
        this module fetches from somewhere else.
        """

        username = self._read_required(group.username_env)
        key_file = os.getenv(group.ssh_keyfile_env, "").strip() if group.ssh_keyfile_env else ""
        password = (
            self._read_optional(group.password_env)
            if key_file
            else self._read_required(group.password_env)
        )
        return {"username": username, "password": password, "key_file": key_file or None}

    @abstractmethod
    def _read_required(self, env_name: str) -> str:
        """Return the resolved secret for a required field, or raise ``InventoryError``."""

    @abstractmethod
    def _read_optional(self, env_name: str) -> str:
        """Return the resolved secret for an optional field, or ``""`` if unset."""


def _required_env(env_name: str) -> str:
    value = os.getenv(env_name, "").strip()
    if not value:
        raise InventoryError(f"Required environment variable is not set: {env_name}")
    return value


class EnvCredentialResolver(CredentialResolver):
    """The default, pre-Phase-8 behavior: the named env var's value IS the secret."""

    def _read_required(self, env_name: str) -> str:
        return _required_env(env_name)

    def _read_optional(self, env_name: str) -> str:
        return os.getenv(env_name, "").strip()


def _read_secret_file(path: str, env_name: str) -> str:
    """Read one secret file's content, stripped of surrounding whitespace/newline.

    Errors name the environment variable and the path it pointed at, never
    file content -- there is nothing to leak in a "file not found" message.
    """

    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InventoryError(
            f"Could not read secret file for {env_name} (path {path!r}): {exc}"
        ) from exc


class FileCredentialResolver(CredentialResolver):
    """Docker/Kubernetes secrets convention: the named env var's value is a file path.

    Same ``CredentialGroup`` schema as ``EnvCredentialResolver`` -- no
    inventory YAML change needed to switch providers -- but here
    ``os.getenv(group.username_env)`` etc. is expected to hold a *path*
    (e.g. ``/run/secrets/device_username``), and the secret itself is that
    file's content.
    """

    def _read_required(self, env_name: str) -> str:
        path = _required_env(env_name)
        return _read_secret_file(path, env_name)

    def _read_optional(self, env_name: str) -> str:
        path = os.getenv(env_name, "").strip()
        if not path:
            return ""
        return _read_secret_file(path, env_name)


_RESOLVERS: dict[str, type[CredentialResolver]] = {
    "env": EnvCredentialResolver,
    "file": FileCredentialResolver,
}


def known_credential_providers() -> tuple[str, ...]:
    """Return every registered provider name, for error messages and docs."""

    return tuple(sorted(_RESOLVERS))


def get_resolver(provider: str | None = None) -> CredentialResolver:
    """Return the configured resolver.

    ``provider`` overrides ``NETTOOLS_CREDENTIAL_PROVIDER`` (env-then-default
    ``"env"``, same resolution order as every other env-then-default setting
    in this project). Raises ``InventoryError`` -- not a bare ``KeyError`` --
    for an unrecognized provider name, so a typo surfaces the same way any
    other inventory misconfiguration does.
    """

    name = (provider or os.getenv(NETTOOLS_CREDENTIAL_PROVIDER_ENV, DEFAULT_CREDENTIAL_PROVIDER))
    name = name.strip().lower()
    try:
        resolver_cls = _RESOLVERS[name]
    except KeyError as exc:
        raise InventoryError(
            f"Unknown {NETTOOLS_CREDENTIAL_PROVIDER_ENV}: {name!r}. "
            f"Known providers: {', '.join(known_credential_providers())}."
        ) from exc
    return resolver_cls()
