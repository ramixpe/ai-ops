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
``known_platforms()``) and ``storage_key.py`` (for the shared storage-key
charset policy, EER-009 -- a leaf module with no imports of its own, so this
adds no cycle risk); ``lab.py`` and ``inventory.py`` depend on this module,
never the other way, so there is no import cycle.
"""

from __future__ import annotations

import ipaddress
import os
import re
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .platforms import known_platforms
from .storage_key import is_valid_storage_key

Role = Literal["core", "edge", "route-reflector"]


class InventoryError(ValueError):
    """Raised when the declarative inventory is missing, malformed, or invalid.

    Re-exported from ``inventory.py`` for backward compatibility -- existing
    code and tests import it from there.
    """


#: The shell-portable environment-variable-name grammar (POSIX "Environment
#: Variable Name" in XBD 8.1, minus a leading digit): an uppercase letter,
#: then any run of uppercase letters, digits, or underscores. A *grammar*
#: check only -- this module never asks whether the named variable is
#: actually set (EER-009's hard constraint: this module may read the
#: environment only to *locate* the YAML file, in `resolve_inventory_path`
#: below; checking presence here would leak "is this credential configured"
#: into the credential-free layer, which is exactly what
#: `test_refuses_unapproved_commands_before_loading_credentials` pins against
#: and what CLAUDE.md's "safety boundary" section names explicitly).
_ENV_VAR_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*")


def _validate_env_var_name(value: str) -> str:
    if not _ENV_VAR_NAME_RE.fullmatch(value):
        raise ValueError(
            f"not a valid environment variable name (expected [A-Z][A-Z0-9_]*): {value!r}"
        )
    return value


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

    @field_validator("username_env", "password_env")
    @classmethod
    def _validate_required_env_names(cls, value: str) -> str:
        return _validate_env_var_name(value)

    @field_validator("ssh_keyfile_env")
    @classmethod
    def _validate_optional_env_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_env_var_name(value)


class Defaults(BaseModel):
    """Fallback values applied to any device that omits the field."""

    model_config = ConfigDict(extra="forbid")

    platform: str
    credential_group: str
    #: TCP port range (EER-009) -- a port outside 1-65535 cannot be dialled,
    #: so it is a load-time typo, not a value worth carrying through to a
    #: connect-time failure.
    port: int = Field(default=22, ge=1, le=65535)


class Expected(BaseModel):
    """Derived (never invented) per-device topology counts.

    Populated by ``nettools learn-topology`` (see ``topology.py``) from parsed
    evidence -- ``isis_adjacencies`` is a count of IS-IS neighbor records,
    ``bgp_peers`` a count of BGP neighbor records. Absent, not zero, means "no
    evidence was available to derive this" (e.g. a device with no BGP process
    at all has no ``bgp_peers`` key, ever).
    """

    model_config = ConfigDict(extra="forbid")

    #: Non-negative (EER-009) -- both are counts of observed records, and a
    #: negative count cannot come from `nettools learn-topology`'s own
    #: derivation; one in a hand-edited file is a typo, not a real value.
    isis_adjacencies: int | None = Field(default=None, ge=0)
    bgp_peers: int | None = Field(default=None, ge=0)


class Intended(BaseModel):
    """Version-controlled operator intent, distinct from observed baselines."""

    model_config = ConfigDict(extra="forbid")

    #: Interfaces that are required to participate in LDP on this device.
    #: An empty list is meaningful: the operator explicitly intends no local
    #: LDP interfaces. Absent means no intent has been declared.
    ldp_interfaces: list[str] | None = None

    @field_validator("ldp_interfaces")
    @classmethod
    def _validate_ldp_interfaces(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not name.strip() for name in value):
            raise ValueError("ldp_interfaces entries must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("ldp_interfaces must not contain duplicates")
        return value


class Note(BaseModel):
    """One operator-authored fact about this estate (B-402).

    **Human-written, never model-written.** D-something the whole architecture
    refuses: a model writing memory reads its own hallucinations back as
    evidence and compounds them. A note is a claim by a person, and it carries
    who made it and when so a reader can weigh it.

    What a note is *for*: stopping the agent rediscovering known local truth on
    every run. This fabric has several, all of them proven during the build and
    all of them things a fresh investigation would otherwise re-derive or, worse,
    report as findings.

    `applies_to` is deliberately free text rather than an enum. The things an
    operator needs to say something about -- an interface, a peer, a protocol, a
    whole device -- do not share a vocabulary, and forcing one would either
    exclude the note or distort it.
    """

    model_config = ConfigDict(extra="forbid")

    #: What the note is about. Free text: `"Gi0/0/0/2.300"`, `"bgp"`, `"lldp"`,
    #: or omitted for a device-wide fact.
    applies_to: str | None = None
    #: The fact itself, in a sentence an engineer would recognise.
    note: str
    #: Who wrote it. Provenance, not authorisation -- the same distinction
    #: `_resolve_actor` makes in the audit log.
    author: str | None = None
    #: ISO date. A note about a lab that has since been rebuilt is worth less
    #: than a fresh one, and a reader cannot tell without this.
    recorded: str | None = None
    #: What would make this note wrong. **Optional, and the most valuable field
    #: here when present** -- an operator fact with no expiry condition becomes
    #: folklore, and folklore outlives the thing it described.
    revisit_when: str | None = None

    @field_validator("note")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a note must say something")
        return value


class Device(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: EER-009: validated below with the shared storage-key policy
    #: (`storage_key.is_valid_storage_key`) -- this value feeds
    #: `evidence_store`/`fixtures` storage paths (device names are the one
    #: identity this module's own `_index_cache` and every downstream storage
    #: boundary key off), so `""` or `"../.."` reaching those boundaries
    #: unvalidated is a path-safety issue, not just a schema nicety.
    name: str
    mgmt_ip: str
    role: Role
    #: Free text (a location label, not a storage key or command argument
    #: anywhere downstream -- confirmed by grep, nothing else in this package
    #: reads `Device.site`), so the only thing worth enforcing is EER-009's
    #: "must say something" -- see `_non_empty_site` below.
    site: str
    platform: str | None = None
    # Not in the Task 1 schema sketch, but a natural per-device escape hatch
    # from ``defaults.credential_group`` -- kept optional so the common case
    # (every device sharing one credential group) needs no per-device entry.
    credential_group: str | None = None
    #: IPv4 when present (EER-009, mirrors `mgmt_ip`'s `_validate_ipv4`) --
    #: `investigation.py`'s `_route_back_prefix` interpolates this straight
    #: into `f"{router_id}/32"` for a `route` template lookup, so a
    #: non-IPv4 value here would only surface as a confusing downstream
    #: failure far from where the bad value was written. Uniqueness is
    #: checked at the document level, in `InventoryFile._validate_cross_
    #: references` below -- a single field cannot see its siblings.
    router_id: str | None = None
    #: 1-4294967295 when present (EER-009): the full 2-byte/4-byte BGP ASN
    #: range (RFC 6793); 0 is reserved and never a real device's ASN.
    local_as: int | None = Field(default=None, ge=1, le=4294967295)
    tags: list[str] = Field(default_factory=list)
    expected: Expected | None = None
    intended: Intended | None = None
    #: Operator-authored facts about this device (B-402). Empty by default;
    #: an estate with nothing worth saying about a device says nothing.
    notes: list[Note] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _validate_name_is_a_safe_storage_key(cls, value: str) -> str:
        if not is_valid_storage_key(value):
            raise ValueError(
                f"device name is not a safe storage key (expected 1-64 characters "
                f"of letters, digits, '_', '.', '-', and never '.' or '..'): {value!r}"
            )
        return value

    @field_validator("site")
    @classmethod
    def _non_empty_site(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("site must say something")
        return value

    @field_validator("mgmt_ip")
    @classmethod
    def _validate_ipv4(cls, value: str) -> str:
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"mgmt_ip is not a valid IPv4 address: {value!r}") from exc
        return value

    @field_validator("router_id")
    @classmethod
    def _validate_router_id_ipv4(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ipaddress.IPv4Address(value)
        except ValueError as exc:
            raise ValueError(f"router_id is not a valid IPv4 address: {value!r}") from exc
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
        seen_mgmt_ips: set[str] = set()
        seen_router_ids: set[str] = set()
        for device in self.devices:
            if device.name in seen_names:
                raise ValueError(f"duplicate device name: {device.name!r}")
            seen_names.add(device.name)

            # EER-009: mgmt_ip is required and unconditionally checked; a
            # duplicate router_id can only be checked when the field is
            # present at all -- absent (e.g. PE4, which has none) is not a
            # collision with anything.
            if device.mgmt_ip in seen_mgmt_ips:
                raise ValueError(
                    f"device {device.name!r} duplicates mgmt_ip {device.mgmt_ip!r} "
                    f"already used by another device"
                )
            seen_mgmt_ips.add(device.mgmt_ip)

            if device.router_id is not None:
                if device.router_id in seen_router_ids:
                    raise ValueError(
                        f"device {device.name!r} duplicates router_id {device.router_id!r} "
                        f"already used by another device"
                    )
                seen_router_ids.add(device.router_id)

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
    """A byte-identical copy of ``inventory/lab.yaml``, shipped as package data.

    R2: the canonical file lives at the repo root, outside ``src/``, so a
    non-editable install (a real ``pip install`` of a built wheel, with no
    source checkout anywhere nearby) cannot see it -- ``resolve_inventory_path``
    would previously fall through to nothing findable. The fix is a real
    packaging step: ``src/agent_nettools/data/lab.yaml`` is a checked-in copy
    of the same file, declared in ``pyproject.toml``'s
    ``[tool.setuptools.package-data]`` (the existing ``agent_nettools =
    ["data/*.yaml"]`` glob already covers it -- no new entry needed), so it
    ships inside the wheel and ``importlib.resources`` can find it regardless
    of whether this is an editable or an installed copy.

    This is only ever reached when there is no explicit path, no
    ``NETTOOLS_INVENTORY``, and no ``./inventory/lab.yaml`` in the cwd --  a
    live checkout run from its own repo root always wins via the cwd check in
    ``resolve_inventory_path`` and never reaches this function, so the two
    files being distinct copies (rather than one) never surfaces as a stale
    read for the common case. Kept honest by
    ``tests/test_packaging_inventory.py``, which fails loudly if the two
    copies ever diverge.
    """

    return importlib_resources.files("agent_nettools") / "data" / "lab.yaml"


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
