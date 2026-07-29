"""Declarative lab inventory: schema, validation, and YAML loading.

Why pydantic models instead of hand-rolled dict checks: at the device counts
this tool is meant to grow into, a vague "invalid inventory" error is useless --
finding which of a thousand devices has the typo means a structured error
location (``devices[41] -> mgmt_ip``) doing the work instead of a human staring
at a diff. ``extra="forbid"`` on every model means a typo'd key
(``managment_ip``) is a load failure, not a silently ignored no-op.

This module is credential-free by design: nothing here reads an environment
variable. ``inventory.py`` is the only place credentials are resolved, and only
at ``load_inventory()`` time -- keeping this module free of ``os.environ`` is
what lets ``lab.platform_for()`` resolve a device's platform without touching
credentials, which is the precondition for the safety boundary in
``network_tools.py`` (see ``CLAUDE.md``, "The safety boundary").

Layering: this module depends only on ``platforms.py`` (for
``known_platforms()``); ``lab.py`` and ``inventory.py`` depend on this module,
never the other way, so there is no import cycle.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .platforms import known_platforms

Role = Literal["core", "edge", "route-reflector"]


class InventoryError(ValueError):
    """Raised when the declarative inventory is missing, malformed, or invalid.

    Re-exported from ``inventory.py`` for backward compatibility -- existing
    code and tests import it from there.
    """


class CredentialGroup(BaseModel):
    """Names the environment variables one or more devices draw credentials from.

    Nothing here is a value -- only the *names* of environment variables live
    in this file. The actual username/password/keyfile are read from the
    process environment at ``inventory.load_inventory()`` time, never here.
    """

    model_config = ConfigDict(extra="forbid")

    username_env: str
    password_env: str
    ssh_keyfile_env: str | None = None


class Defaults(BaseModel):
    """Fallback values applied to any device that omits the field."""

    model_config = ConfigDict(extra="forbid")

    platform: str
    credential_group: str
    port: int = 22


class Expected(BaseModel):
    """Derived (never invented) per-device topology counts.

    Populated by ``nettools learn-topology`` (see ``topology.py``) from parsed
    evidence -- ``isis_adjacencies`` is a count of IS-IS neighbor records,
    ``bgp_peers`` a count of BGP neighbor records. Absent, not zero, means "no
    evidence was available to derive this" (e.g. a device with no BGP process
    at all has no ``bgp_peers`` key, ever).
    """

    model_config = ConfigDict(extra="forbid")

    isis_adjacencies: int | None = None
    bgp_peers: int | None = None


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    mgmt_ip: str
    role: Role
    site: str
    platform: str | None = None
    # Not in the Task 1 schema sketch, but a natural per-device escape hatch
    # from ``defaults.credential_group`` -- kept optional so the common case
    # (every device sharing one credential group) needs no per-device entry.
    credential_group: str | None = None
    router_id: str | None = None
    local_as: int | None = None
    tags: list[str] = Field(default_factory=list)
    expected: Expected | None = None

    @field_validator("mgmt_ip")
    @classmethod
    def _validate_ipv4(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"mgmt_ip is not a valid IPv4 address: {value!r}") from exc
        return value


class InventoryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    defaults: Defaults
    credential_groups: dict[str, CredentialGroup]
    devices: list[Device]

    @model_validator(mode="after")
    def _validate_cross_references(self) -> InventoryFile:
        """Checks that need the whole document: uniqueness and cross-references.

        Per-field checks (types, IPv4 shape) are handled by pydantic itself;
        this validator only covers what a single field cannot see on its own.
        Raises on the *first* problem found rather than collecting every one --
        simpler to reason about, and the caller re-runs after each fix anyway.
        """

        seen_names: set[str] = set()
        for device in self.devices:
            if device.name in seen_names:
                raise ValueError(f"duplicate device name: {device.name!r}")
            seen_names.add(device.name)

            platform = device.platform or self.defaults.platform
            if platform not in known_platforms():
                raise ValueError(
                    f"device {device.name!r} has unknown platform {platform!r}; "
                    f"known platforms: {', '.join(known_platforms())}"
                )

            group = device.credential_group or self.defaults.credential_group
            if group not in self.credential_groups:
                raise ValueError(
                    f"device {device.name!r} references unknown credential_group {group!r}; "
                    f"known groups: {', '.join(self.credential_groups)}"
                )

        if self.defaults.platform not in known_platforms():
            raise ValueError(
                f"defaults.platform is an unknown platform: {self.defaults.platform!r}; "
                f"known platforms: {', '.join(known_platforms())}"
            )
        if self.defaults.credential_group not in self.credential_groups:
            raise ValueError(
                "defaults.credential_group references unknown credential_group: "
                f"{self.defaults.credential_group!r}; known groups: "
                f"{', '.join(self.credential_groups)}"
            )
        return self


def _format_validation_error(path: Path, exc: ValidationError, raw: Any) -> str:
    """Turn a pydantic ``ValidationError`` into one message naming file, device, and field.

    Field-level errors carry a ``loc`` like ``("devices", 3, "mgmt_ip")``; when
    the raw (pre-validation) document is available, the device's own ``name``
    is looked up and folded into the message, because "devices[3]" means
    nothing at 1000s of devices but "devices[3] (P3)" does. Cross-reference
    errors raised by ``_validate_cross_references`` already name the device
    inline, so they are passed through as-is.
    """

    devices_raw = raw.get("devices") if isinstance(raw, dict) else None
    lines: list[str] = []
    for error in exc.errors():
        loc = error["loc"]
        message = error["msg"]
        if (
            loc
            and loc[0] == "devices"
            and len(loc) > 1
            and isinstance(loc[1], int)
            and isinstance(devices_raw, list)
        ):
            index = loc[1]
            name = None
            if 0 <= index < len(devices_raw) and isinstance(devices_raw[index], dict):
                name = devices_raw[index].get("name")
            label = f"devices[{index}]" + (f" ({name})" if name else "")
            field = " -> ".join(str(part) for part in loc[2:]) or "<device>"
            lines.append(f"{label}: {field}: {message}")
        else:
            field = " -> ".join(str(part) for part in loc) or "<root>"
            lines.append(f"{field}: {message}")
    return f"Invalid inventory file {path}:\n  " + "\n  ".join(lines)


def parse_inventory(path: Path) -> InventoryFile:
    """Parse and validate one inventory YAML file. Reads nothing from the environment."""

    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InventoryError(f"Cannot read inventory file {path}: {exc}") from exc

    try:
        data: Any = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise InventoryError(f"Invalid YAML in inventory file {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise InventoryError(f"Inventory file {path} must contain a mapping at the top level")

    try:
        return InventoryFile.model_validate(data)
    except ValidationError as exc:
        raise InventoryError(_format_validation_error(path, exc, data)) from exc


# The inventory file location, resolved env-then-default like
# ``network_tools._snapshot_dir`` / ``fixtures._fixture_dir``.
DEFAULT_INVENTORY_PATH = "inventory/lab.yaml"


def _packaged_fallback_path() -> Path:
    """Repo-root ``inventory/lab.yaml``, resolved from this file's own location.

    Makes the inventory findable for an editable install run from outside the
    repo root, without requiring ``NETTOOLS_INVENTORY`` or a matching cwd. A
    distributed (non-editable) package would ship the file as package data
    instead; there is no such packaging step for this single-lab tool.
    """

    return Path(__file__).resolve().parents[2] / "inventory" / "lab.yaml"


def resolve_inventory_path(explicit: str | None = None) -> Path:
    """Resolve the inventory file path: explicit arg, then env, then cwd, then packaged."""

    if explicit:
        return Path(explicit)
    env_path = os.getenv("NETTOOLS_INVENTORY", "").strip()
    if env_path:
        return Path(env_path)
    cwd_path = Path(DEFAULT_INVENTORY_PATH)
    if cwd_path.is_file():
        return cwd_path
    return _packaged_fallback_path()


# Parsing a lab-scale YAML file is cheap, but re-parsing it on every single
# ``platform_for()`` call (once per approved-command check, i.e. constantly)
# is not free either -- and it is static per process, so a plain module-level
# cache keyed by resolved path is enough. ``reset_inventory_cache()`` exists
# purely for tests that swap ``NETTOOLS_INVENTORY`` mid-run.
_cache: dict[str, InventoryFile] = {}

# A second cache, alongside ``_cache`` and invalidated by the same
# ``reset_inventory_cache()``: a name -> Device index built once per resolved
# path. Phase 7's fabric-scale measurement found ``inventory.get_device()``
# rebuilding the whole credentialed device list and then linear-scanning it
# for one name -- O(n) work repeated once per device, O(n^2) for a
# whole-fabric check. This index is credential-free (just the parsed YAML
# `Device` objects), so caching it carries none of the "env changed, cache
# went stale" risk a credentialed cache would: `inventory.get_device()` still
# resolves credentials fresh from the environment on every call, using this
# index only to find *which* device to resolve in O(1) instead of O(n).
_index_cache: dict[str, dict[str, Device]] = {}


def load_inventory_file(explicit_path: str | None = None) -> InventoryFile:
    """Return the parsed, validated inventory, cached by resolved path."""

    path = resolve_inventory_path(explicit_path)
    key = str(path)
    if key not in _cache:
        _cache[key] = parse_inventory(path)
    return _cache[key]


def find_device(device_name: str, explicit_path: str | None = None) -> Device | None:
    """Return one device by name in O(1), or ``None`` if the inventory has none.

    Reads nothing from the environment, same as everything else in this
    module -- only the parsed ``Device`` (no credentials) comes back.
    """

    path = resolve_inventory_path(explicit_path)
    key = str(path)
    if key not in _index_cache:
        inventory = load_inventory_file(explicit_path)
        _index_cache[key] = {device.name: device for device in inventory.devices}
    return _index_cache[key].get(device_name)


def reset_inventory_cache() -> None:
    """Drop the cached inventory. Call after monkeypatching ``NETTOOLS_INVENTORY``."""

    _cache.clear()
    _index_cache.clear()
