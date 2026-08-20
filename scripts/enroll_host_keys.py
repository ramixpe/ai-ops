#!/usr/bin/env python3
"""Operator tool: enroll each inventory device's current SSH host key into
nettools' own known-hosts trust store (EER-002).

*** THIS IS THE ONE PLACE IN THIS CODEBASE THAT ACCEPTS AN SSH HOST KEY
WITHOUT VERIFYING IT FIRST. ***

Every other code path -- network_tools.py's real transport, which is what
every check/investigation/agent command in this project ultimately uses --
verifies a device's host key against exactly what this script recorded here,
and refuses to talk to a device whose key does not match
(``ssh_strict=True``, ``system_host_keys=False``, ``alt_host_keys=True`` +
``alt_key_file``; see network_tools.py's "EER-002: SSH host-key
verification" section for the enforcement side). That refusal is only ever
as trustworthy as the key recorded here, so:

  - Run this ONCE per device, the first time nettools will ever talk to it,
    on a network you trust (ideally on the lab's own management network, not
    over an untrusted hop) -- this is the trust-on-first-use (TOFU) moment.
    There is no cryptographic way to verify a key you have never seen before
    without an out-of-band channel (reading it off the device's own console,
    a vendor-published fingerprint, etc.) -- this script does not attempt
    that; it only records whatever the device presents and shows you its
    fingerprint so YOU can decide whether to trust it.
  - Run it again, DELIBERATELY, only when a device's key has legitimately
    changed (reimage, RMA, controller failover to a box with a fresh host
    key) -- never as a reflex "fix the SSH host key error" response. That
    reflex is exactly the window a machine-in-the-middle attack needs: an
    attacker who can intercept the connection can also make the *real* key
    look "rejected" so an operator re-enrolls the ATTACKER's key instead.
    If a key changes unexpectedly, verify out-of-band before re-running this.
  - Everything that happens through network_tools.py AFTER this script runs
    is verified against what it recorded. Nothing before it was.

Connects with SSH host-key verification INTENTIONALLY DISABLED -- a bare
transport handshake, no ``ConnectHandler``, no netmiko, no authentication at
all -- to read each device's presented host key, prints its fingerprint for
the operator to look at, and records it into the known-hosts file
network_tools.py itself reads for verification
(``NETTOOLS_SSH_KNOWN_HOSTS``, or ``DEFAULT_SSH_KNOWN_HOSTS`` if that is
unset). Nothing is written until the operator explicitly confirms (or passes
``--yes``).

Usage
-----
    python scripts/enroll_host_keys.py [DEVICE ...] [--known-hosts PATH] [--yes]

With no DEVICE arguments, enrolls every device in ``inventory/lab.yaml``.
Device names, management IPs, and ports come from that credential-free
inventory file only -- this script never reads DEVICE_USERNAME/
DEVICE_PASSWORD, because recording a host key needs nothing past the
transport handshake, no authentication.

NOT run by CI, NOT run by any test in this repository, and NOT something an
agent should ever invoke on its own initiative: it needs a live, reachable
lab and a human trust decision. The orchestrator runs this by hand against
the live lab after this change merges.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

import paramiko  # noqa: E402

from agent_nettools import inventory_model  # noqa: E402
from agent_nettools.network_tools import (  # noqa: E402
    DEFAULT_SSH_KNOWN_HOSTS,
    NETTOOLS_SSH_KNOWN_HOSTS_ENV,
    _ssh_known_hosts_path,
)


def _host_key_entry_name(hostname: str, port: int) -> str:
    """OpenSSH known_hosts naming: a bare hostname for port 22,
    ``"[host]:port"`` otherwise -- matches the convention paramiko's own
    ``SSHClient.connect`` uses internally (``client.py``'s
    ``server_hostkey_name``) so a later ``ssh_strict=True`` lookup for the
    same host/port actually finds the entry this script wrote."""

    return hostname if port == 22 else f"[{hostname}]:{port}"


def fetch_host_key(hostname: str, port: int, *, timeout: float = 10.0) -> paramiko.PKey:
    """Open a bare SSH transport -- no authentication, no host-key check --
    and return the key the device presents during the handshake.

    This is the one function in this whole codebase that talks to a device
    without any host-key verification at all. It exists ONLY so this
    script's caller can look at the key and decide whether to trust it; nothing
    downstream of this function inherits that lack of verification.
    """

    sock = socket.create_connection((hostname, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        if key is None:
            raise RuntimeError(f"{hostname}:{port} presented no host key")
        return key
    finally:
        transport.close()


def enroll(devices: list[dict], known_hosts_path: str, *, timeout: float = 10.0) -> list[dict]:
    """Fetch and record one host key per device.

    Returns one result dict per device -- never raises for a single device's
    connection failure, so one unreachable device does not abort the rest of
    the batch. A device whose newly-fetched key differs from one already on
    file is reported as ``"changed"`` (with both fingerprints) rather than
    silently overwritten without comment -- see this module's docstring on
    why an unexpected change deserves suspicion, not a reflex re-enrollment.
    """

    path = Path(known_hosts_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    host_keys = paramiko.HostKeys()
    if path.is_file():
        host_keys.load(str(path))

    results: list[dict] = []
    for device in devices:
        entry_name = _host_key_entry_name(device["hostname"], device["port"])
        try:
            key = fetch_host_key(device["hostname"], device["port"], timeout=timeout)
        except (OSError, paramiko.SSHException) as exc:
            results.append(
                {
                    "device": device["name"],
                    "hostname": device["hostname"],
                    "port": device["port"],
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue

        fingerprint = key.get_fingerprint().hex(":")
        previous_fingerprint = None
        existing = host_keys.lookup(entry_name)
        if existing is not None:
            existing_key = existing.get(key.get_name())
            if existing_key is not None:
                previous_fingerprint = existing_key.get_fingerprint().hex(":")

        host_keys.add(entry_name, key.get_name(), key)
        results.append(
            {
                "device": device["name"],
                "hostname": device["hostname"],
                "port": device["port"],
                "status": "changed"
                if previous_fingerprint not in (None, fingerprint)
                else "recorded",
                "key_type": key.get_name(),
                "fingerprint": fingerprint,
                "previous_fingerprint": previous_fingerprint,
            }
        )

    host_keys.save(str(path))
    return results


def _load_devices(names: list[str] | None) -> list[dict]:
    """Device name + management IP + port, straight from the credential-free
    inventory file -- see this module's docstring for why credentials are
    never resolved here at all."""

    inventory = inventory_model.load_inventory_file()
    default_port = inventory.defaults.port
    devices = [
        {"name": device.name, "hostname": device.mgmt_ip, "port": default_port}
        for device in inventory.devices
    ]
    if names:
        wanted = set(names)
        known = {device["name"] for device in devices}
        missing = wanted - known
        if missing:
            raise SystemExit(f"Unknown device(s): {', '.join(sorted(missing))}")
        devices = [device for device in devices if device["name"] in wanted]
    return devices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "device", nargs="*", help="Device name(s) to enroll (default: every inventory device)"
    )
    parser.add_argument(
        "--known-hosts",
        default=None,
        help=f"Override the known-hosts file (default: {NETTOOLS_SSH_KNOWN_HOSTS_ENV} "
        f"or {DEFAULT_SSH_KNOWN_HOSTS})",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-device connect timeout (seconds)")
    parser.add_argument(
        "--yes", action="store_true", help="Skip the interactive confirmation prompt"
    )
    args = parser.parse_args(argv)

    devices = _load_devices(args.device or None)
    known_hosts_path = args.known_hosts or _ssh_known_hosts_path()

    print("*** UNVERIFIED CONNECTION -- READ BEFORE PROCEEDING ***")
    print(
        "This will connect to each device below with NO host-key verification "
        "and record whatever key it presents as trusted, in:"
    )
    print(f"    {known_hosts_path}")
    print(
        "This is a trust-on-first-use decision. Only do this on a network you "
        "trust, and only for a device you have not already enrolled -- see "
        "this script's own module docstring (`pydoc scripts/enroll_host_keys.py`) "
        "before re-running it against a device whose key just changed "
        "unexpectedly.\n"
    )
    for device in devices:
        print(f"  {device['name']:<12} {device['hostname']}:{device['port']}")
    print()

    if not devices:
        print("No devices to enroll.")
        return 0

    if not args.yes:
        answer = input("Type 'yes' to fetch and record these devices' host keys: ").strip().lower()
        if answer != "yes":
            print("Aborted -- nothing was recorded.")
            return 1

    results = enroll(devices, known_hosts_path, timeout=args.timeout)

    print()
    exit_code = 0
    for result in results:
        label = f"{result['device']:<12} {result['hostname']}:{result['port']}"
        if result["status"] == "error":
            exit_code = 1
            print(f"  FAILED   {label}  {result['error']}")
        elif result["status"] == "changed":
            print(
                f"  CHANGED  {label}  {result['key_type']} {result['fingerprint']}  "
                f"(was {result['previous_fingerprint']} -- confirm this change was "
                "expected before trusting it)"
            )
        else:
            print(f"  RECORDED {label}  {result['key_type']} {result['fingerprint']}")

    recorded = sum(1 for result in results if result["status"] != "error")
    print(f"\n{known_hosts_path} now holds {recorded} of {len(results)} requested device(s).")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
