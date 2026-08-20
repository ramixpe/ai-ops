"""Tests for scripts/enroll_host_keys.py (EER-002).

The script itself talks to real devices over a real socket -- it is
deliberately excluded from live-lab-only coverage (the orchestrator runs it
by hand against the live lab after merge; see the script's own module
docstring). What IS testable without a network, and what these tests cover:

- device selection off the real, credential-free inventory file
- the known_hosts entry-naming convention
- the record/compare/write bookkeeping in ``enroll()``, with
  ``fetch_host_key`` monkeypatched to real (locally generated) paramiko key
  objects rather than a socket -- so the "a key changed" detection is
  exercised against real ``PKey``/fingerprint behavior, not a hand-rolled
  stand-in for it
- the confirmation gate: no key fetch happens without an explicit "yes"

Imported via ``importlib`` rather than a normal package import: ``scripts/``
is not an installed package (no ``__init__.py``, not on ``sys.path`` by
pytest's own ``testpaths = ["tests"]``), matching how the script itself is
meant to be run standalone (``python scripts/enroll_host_keys.py``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import paramiko
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "enroll_host_keys.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("enroll_host_keys", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def enroll_host_keys():
    return _load_module()


@pytest.fixture(scope="module")
def two_keys():
    """Two distinct, locally-generated RSA keys -- fast, and never touches
    the network. Standing in for "the device's real key" and "a different
    key" (a legitimate rotation, or an attacker's) across tests."""

    return paramiko.RSAKey.generate(bits=2048), paramiko.RSAKey.generate(bits=2048)


def test_host_key_entry_name_matches_paramikos_own_convention(enroll_host_keys):
    """Port 22 -> bare hostname; anything else -> "[host]:port" -- the exact
    shape paramiko.SSHClient.connect computes internally for
    server_hostkey_name, so a later ssh_strict=True lookup for the same
    host/port actually finds what this script wrote."""

    assert enroll_host_keys._host_key_entry_name("172.20.250.11", 22) == "172.20.250.11"
    assert (
        enroll_host_keys._host_key_entry_name("172.20.250.11", 2222)
        == "[172.20.250.11]:2222"
    )


def test_load_devices_reads_the_real_credential_free_inventory(enroll_host_keys):
    """No DEVICE_USERNAME/DEVICE_PASSWORD in the environment at all -- proves
    device selection needs no credentials, matching the module docstring's
    claim."""

    devices = enroll_host_keys._load_devices(None)

    assert len(devices) >= 1
    assert all({"name", "hostname", "port"} <= device.keys() for device in devices)
    names = {device["name"] for device in devices}
    assert "PE1" in names  # the lab's documented default device


def test_load_devices_filters_to_the_requested_names(enroll_host_keys):
    devices = enroll_host_keys._load_devices(["PE1"])

    assert [device["name"] for device in devices] == ["PE1"]


def test_load_devices_refuses_an_unknown_device_name(enroll_host_keys):
    with pytest.raises(SystemExit, match="NOT_A_REAL_DEVICE"):
        enroll_host_keys._load_devices(["NOT_A_REAL_DEVICE"])


def test_enroll_records_a_new_key_and_a_second_run_agrees(enroll_host_keys, tmp_path, monkeypatch, two_keys):
    key_a, _ = two_keys
    monkeypatch.setattr(enroll_host_keys, "fetch_host_key", lambda hostname, port, timeout=10.0: key_a)

    known_hosts = str(tmp_path / "known_hosts")
    device = {"name": "PE1", "hostname": "172.20.250.21", "port": 22}

    [first] = enroll_host_keys.enroll([device], known_hosts)
    assert first["status"] == "recorded"
    assert first["previous_fingerprint"] is None
    assert first["fingerprint"] == key_a.get_fingerprint().hex(":")

    # A second run against the SAME key must not be reported as a change --
    # only a DIFFERENT key on a later run should ever say "changed".
    [second] = enroll_host_keys.enroll([device], known_hosts)
    assert second["status"] == "recorded"
    assert second["previous_fingerprint"] == first["fingerprint"]

    # The file is real OpenSSH known_hosts, loadable by paramiko/netmiko's
    # own alt_host_keys machinery -- not just something this script itself
    # can parse.
    loaded = paramiko.HostKeys()
    loaded.load(known_hosts)
    entry = loaded.lookup("172.20.250.21")
    assert entry is not None
    assert entry[key_a.get_name()].get_fingerprint() == key_a.get_fingerprint()


def test_enroll_reports_a_changed_key_with_both_fingerprints(enroll_host_keys, tmp_path, monkeypatch, two_keys):
    key_a, key_b = two_keys
    known_hosts = str(tmp_path / "known_hosts")
    device = {"name": "PE1", "hostname": "172.20.250.21", "port": 22}

    monkeypatch.setattr(enroll_host_keys, "fetch_host_key", lambda hostname, port, timeout=10.0: key_a)
    [first] = enroll_host_keys.enroll([device], known_hosts)

    monkeypatch.setattr(enroll_host_keys, "fetch_host_key", lambda hostname, port, timeout=10.0: key_b)
    [second] = enroll_host_keys.enroll([device], known_hosts)

    assert second["status"] == "changed"
    assert second["previous_fingerprint"] == first["fingerprint"]
    assert second["fingerprint"] == key_b.get_fingerprint().hex(":")
    assert second["fingerprint"] != second["previous_fingerprint"]


def test_enroll_does_not_abort_the_batch_on_one_devices_failure(enroll_host_keys, tmp_path, monkeypatch, two_keys):
    key_a, _ = two_keys

    def flaky_fetch(hostname, port, timeout=10.0):
        if hostname == "unreachable":
            raise OSError("Connection refused")
        return key_a

    monkeypatch.setattr(enroll_host_keys, "fetch_host_key", flaky_fetch)
    known_hosts = str(tmp_path / "known_hosts")
    devices = [
        {"name": "BAD", "hostname": "unreachable", "port": 22},
        {"name": "PE1", "hostname": "172.20.250.21", "port": 22},
    ]

    results = enroll_host_keys.enroll(devices, known_hosts)

    by_name = {result["device"]: result for result in results}
    assert by_name["BAD"]["status"] == "error"
    assert "Connection refused" in by_name["BAD"]["error"]
    assert by_name["PE1"]["status"] == "recorded"


def test_main_fetches_nothing_without_explicit_confirmation(enroll_host_keys, tmp_path, monkeypatch):
    """No 'yes' -> fetch_host_key must never even be called."""

    def must_not_be_called(hostname, port, timeout=10.0):
        raise AssertionError("fetch_host_key must not run without confirmation")

    monkeypatch.setattr(enroll_host_keys, "fetch_host_key", must_not_be_called)
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")

    exit_code = enroll_host_keys.main(
        ["PE1", "--known-hosts", str(tmp_path / "known_hosts")]
    )

    assert exit_code == 1
    assert not (tmp_path / "known_hosts").exists()


def test_main_yes_flag_skips_the_prompt_and_records(enroll_host_keys, tmp_path, monkeypatch, two_keys):
    key_a, _ = two_keys
    monkeypatch.setattr(enroll_host_keys, "fetch_host_key", lambda hostname, port, timeout=10.0: key_a)

    def must_not_prompt(prompt=""):
        raise AssertionError("--yes must skip the interactive prompt entirely")

    monkeypatch.setattr("builtins.input", must_not_prompt)
    known_hosts = tmp_path / "known_hosts"

    exit_code = enroll_host_keys.main(["PE1", "--known-hosts", str(known_hosts), "--yes"])

    assert exit_code == 0
    assert known_hosts.is_file()
