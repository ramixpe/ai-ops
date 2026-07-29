import pytest
import yaml

from agent_nettools.inventory import (
    InventoryError,
    get_default_device_name,
    get_device,
    load_inventory,
)
from agent_nettools.inventory_model import reset_inventory_cache
from agent_nettools.lab import DEVICES, all_devices, platform_for


def test_all_devices_returns_every_lab_device():
    devices = all_devices()

    assert len(devices) == 9
    assert devices["PE1"] == "172.20.250.21"
    assert devices["RR1"] == "172.20.250.31"


def test_all_devices_returns_a_copy():
    devices = all_devices()
    devices["PE1"] = "0.0.0.0"

    assert all_devices()["PE1"] == "172.20.250.21"
    assert DEVICES["PE1"] == "172.20.250.21"


def set_device_environment(monkeypatch):
    """Set safe test-only values for the required device environment."""

    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_PASSWORD", "test-password")


def test_load_inventory_returns_all_ios_xr_devices(monkeypatch):
    set_device_environment(monkeypatch)

    devices = load_inventory()
    names = [device["name"] for device in devices]

    assert names == ["P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1"]
    assert all(device["platform"] == "cisco_xr" for device in devices)


def test_load_inventory_attaches_credentials(monkeypatch):
    set_device_environment(monkeypatch)

    devices = load_inventory()
    pe1 = next(device for device in devices if device["name"] == "PE1")

    assert pe1["hostname"] == "172.20.250.21"
    assert pe1["username"] == "test-user"
    assert pe1["password"] == "test-password"


def test_get_device_rejects_unknown_device(monkeypatch):
    set_device_environment(monkeypatch)

    with pytest.raises(InventoryError, match="not in the lab inventory"):
        get_device("does-not-exist")


def test_default_device_is_pe1(monkeypatch):
    set_device_environment(monkeypatch)

    assert get_default_device_name() == "PE1"


@pytest.mark.parametrize("missing_name", ["DEVICE_USERNAME", "DEVICE_PASSWORD"])
def test_load_inventory_requires_environment(monkeypatch, missing_name):
    set_device_environment(monkeypatch)
    monkeypatch.delenv(missing_name)

    with pytest.raises(InventoryError, match=missing_name):
        load_inventory()


def test_platform_for_requires_no_credentials(monkeypatch):
    """The whole point of keeping platform data credential-free: this must work
    with nothing set in the environment at all."""

    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)
    monkeypatch.delenv("DEVICE_SSH_KEYFILE", raising=False)

    assert platform_for("PE1") == "cisco_xr"
    assert platform_for("RR1") == "cisco_xr"
    # An unknown device name still resolves to a platform rather than raising;
    # the device itself is rejected later by get_device().
    assert platform_for("does-not-exist") == "cisco_xr"


def test_ssh_keyfile_makes_password_an_optional_passphrase(monkeypatch, tmp_path):
    keyfile = tmp_path / "id_lab"
    keyfile.write_text("not a real key", encoding="utf-8")

    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_SSH_KEYFILE", str(keyfile))
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    devices = load_inventory()
    pe1 = next(device for device in devices if device["name"] == "PE1")

    assert pe1["key_file"] == str(keyfile)
    assert pe1["password"] == ""


def test_ssh_keyfile_with_passphrase_set(monkeypatch, tmp_path):
    keyfile = tmp_path / "id_lab"
    keyfile.write_text("not a real key", encoding="utf-8")

    monkeypatch.setenv("DEVICE_USERNAME", "test-user")
    monkeypatch.setenv("DEVICE_SSH_KEYFILE", str(keyfile))
    monkeypatch.setenv("DEVICE_PASSWORD", "key-passphrase")

    pe1 = get_device("PE1")

    assert pe1["key_file"] == str(keyfile)
    assert pe1["password"] == "key-passphrase"


def test_ssh_keyfile_still_requires_username(monkeypatch, tmp_path):
    keyfile = tmp_path / "id_lab"
    keyfile.write_text("not a real key", encoding="utf-8")

    monkeypatch.delenv("DEVICE_USERNAME", raising=False)
    monkeypatch.setenv("DEVICE_SSH_KEYFILE", str(keyfile))
    monkeypatch.delenv("DEVICE_PASSWORD", raising=False)

    with pytest.raises(InventoryError, match="DEVICE_USERNAME"):
        load_inventory()


def _minimal_inventory_document(*devices: dict) -> dict:
    return {
        "version": 1,
        "defaults": {"platform": "cisco_xr", "credential_group": "lab", "port": 22},
        "credential_groups": {
            "lab": {"username_env": "DEVICE_USERNAME", "password_env": "DEVICE_PASSWORD"}
        },
        "devices": list(devices),
    }


def test_nettools_inventory_env_overrides_the_default_file(monkeypatch, tmp_path):
    document = _minimal_inventory_document(
        {"name": "LAB-ONLY-DEVICE", "mgmt_ip": "10.0.0.1", "role": "core", "site": "alt-lab"}
    )
    alt_path = tmp_path / "alt-lab.yaml"
    alt_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    monkeypatch.setenv("NETTOOLS_INVENTORY", str(alt_path))
    reset_inventory_cache()
    try:
        set_device_environment(monkeypatch)

        assert all_devices() == {"LAB-ONLY-DEVICE": "10.0.0.1"}
        assert platform_for("LAB-ONLY-DEVICE") == "cisco_xr"

        devices = load_inventory()
        assert [device["name"] for device in devices] == ["LAB-ONLY-DEVICE"]
    finally:
        reset_inventory_cache()
