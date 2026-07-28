import pytest

from agent_nettools.inventory import (
    InventoryError,
    get_default_device_name,
    get_device,
    load_inventory,
)
from agent_nettools.lab import DEVICES, all_devices


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
