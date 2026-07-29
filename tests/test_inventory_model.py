"""Schema validation for the declarative inventory YAML file.

Every failure mode here must raise ``InventoryError`` with a message naming the
file and the offending device/field -- a vague "invalid inventory" is useless
once there are thousands of devices to scan for a typo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_nettools.inventory import InventoryError
from agent_nettools.inventory_model import (
    load_inventory_file,
    parse_inventory,
    reset_inventory_cache,
    resolve_inventory_path,
)


def _base_document() -> dict[str, Any]:
    return {
        "version": 1,
        "defaults": {"platform": "cisco_xr", "credential_group": "lab", "port": 22},
        "credential_groups": {
            "lab": {"username_env": "DEVICE_USERNAME", "password_env": "DEVICE_PASSWORD"}
        },
        "devices": [
            {"name": "P1", "mgmt_ip": "172.20.250.11", "role": "core", "site": "lab"},
            {"name": "PE1", "mgmt_ip": "172.20.250.21", "role": "edge", "site": "lab"},
        ],
    }


def _write(tmp_path: Path, document: dict[str, Any], name: str = "lab.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def test_valid_document_parses(tmp_path):
    path = _write(tmp_path, _base_document())

    inventory = parse_inventory(path)

    assert [d.name for d in inventory.devices] == ["P1", "PE1"]
    assert inventory.defaults.platform == "cisco_xr"


def test_duplicate_device_name_raises(tmp_path):
    document = _base_document()
    document["devices"].append(
        {"name": "P1", "mgmt_ip": "172.20.250.99", "role": "core", "site": "lab"}
    )
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="duplicate device name") as exc:
        parse_inventory(path)
    assert str(path) in str(exc.value)
    assert "P1" in str(exc.value)


def test_invalid_ipv4_raises(tmp_path):
    document = _base_document()
    document["devices"][1]["mgmt_ip"] = "not-an-ip"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="not a valid IPv4 address") as exc:
        parse_inventory(path)
    message = str(exc.value)
    assert str(path) in message
    # The device that failed is named in the message, not just its index.
    assert "PE1" in message


def test_unknown_platform_raises(tmp_path):
    document = _base_document()
    document["devices"][0]["platform"] = "does_not_exist_os"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="unknown platform") as exc:
        parse_inventory(path)
    message = str(exc.value)
    assert str(path) in message
    assert "P1" in message
    assert "does_not_exist_os" in message


def test_unknown_defaults_platform_raises(tmp_path):
    document = _base_document()
    document["defaults"]["platform"] = "bogus_vendor"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="unknown platform"):
        parse_inventory(path)


def test_unknown_credential_group_raises(tmp_path):
    document = _base_document()
    document["devices"][0]["credential_group"] = "production"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="unknown credential_group") as exc:
        parse_inventory(path)
    message = str(exc.value)
    assert "P1" in message
    assert "production" in message


def test_unknown_defaults_credential_group_raises(tmp_path):
    document = _base_document()
    document["defaults"]["credential_group"] = "missing-group"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="unknown credential_group"):
        parse_inventory(path)


def test_unknown_top_level_key_rejected(tmp_path):
    document = _base_document()
    document["credential_group"] = "typo-of-credential_groups"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="[Ee]xtra"):
        parse_inventory(path)


def test_unknown_device_key_rejected(tmp_path):
    document = _base_document()
    document["devices"][1]["managment_ip"] = "172.20.250.99"  # typo of mgmt_ip
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError) as exc:
        parse_inventory(path)
    message = str(exc.value)
    assert "extra" in message.lower()
    # Field-level errors are formatted with the offending device's own name.
    assert "PE1" in message


def test_invalid_role_rejected(tmp_path):
    document = _base_document()
    document["devices"][0]["role"] = "not-a-real-role"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError):
        parse_inventory(path)


def test_missing_file_raises(tmp_path):
    path = tmp_path / "does-not-exist.yaml"

    with pytest.raises(InventoryError, match="Cannot read inventory file"):
        parse_inventory(path)


def test_malformed_yaml_raises(tmp_path):
    path = tmp_path / "lab.yaml"
    path.write_text("devices: [this is not: valid: yaml", encoding="utf-8")

    with pytest.raises(InventoryError, match="Invalid YAML"):
        parse_inventory(path)


def test_non_mapping_document_raises(tmp_path):
    path = tmp_path / "lab.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")

    with pytest.raises(InventoryError, match="must contain a mapping"):
        parse_inventory(path)


def test_committed_lab_inventory_parses_and_has_nine_devices():
    """Sanity check on the real, committed inventory/lab.yaml."""

    reset_inventory_cache()
    inventory = load_inventory_file()

    assert len(inventory.devices) == 9
    names = {d.name for d in inventory.devices}
    assert names == {"P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"}


def test_resolve_inventory_path_prefers_explicit_argument(tmp_path):
    explicit = tmp_path / "explicit.yaml"

    assert resolve_inventory_path(str(explicit)) == explicit


def test_resolve_inventory_path_falls_back_to_packaged_default(monkeypatch, tmp_path):
    """With no env var and no ./inventory/lab.yaml in cwd, it falls back to the repo's copy."""

    monkeypatch.delenv("NETTOOLS_INVENTORY", raising=False)
    monkeypatch.chdir(tmp_path)  # tmp_path has no inventory/lab.yaml of its own

    resolved = resolve_inventory_path()

    assert resolved.name == "lab.yaml"
    assert resolved.is_file()
