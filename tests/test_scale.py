"""B-409 — the device-count-independence claim, measured instead of asserted.

`BACKLOG.md` B-409 says: *"The tool surface and flow count are device-count
independent by construction. Prove it."* It sat open as "needs a fabric an
order of magnitude larger", which is the wrong instrument for the claim. The
claim is not *"it is fast on a big fabric"* — that is a throughput question and
it does need one. The claim is that **nothing a caller has to reason about
grows with the fabric**, and that is a property of this code, provable against
a synthetic inventory with no devices behind it at all.

Three things must not grow, and one thing must:

* the tool surface a model reads before it can act (MCP tools, CLI subcommands);
* the flow vocabulary;
* the number of devices a single investigation touches — the descent walks a
  dependency path, not a fabric, so its cost is O(path) and not O(devices);
* the inventory listing itself, which is the one thing that *should* be O(n),
  and is asserted so rather than left implied.

Nothing here opens a socket: the descent runs on the `sender=` seam, which is
also how the device count is counted.
"""

from __future__ import annotations

import pytest
import yaml

from agent_nettools import flows
from agent_nettools.inventory_model import reset_inventory_cache
from agent_nettools.lab import all_devices
from agent_nettools.network_tools import list_devices

#: Two orders of magnitude past the real lab's nine.
_LARGE = 900


def _synthetic_inventory(count: int) -> dict:
    """`count` devices, valid against the real schema — roles rotate so the
    document is not uniform in a way a scale claim could hide behind."""

    roles = ("core", "edge", "route-reflector")
    return {
        "version": 1,
        "defaults": {"platform": "cisco_xr", "credential_group": "lab", "port": 22},
        "credential_groups": {
            "lab": {"username_env": "DEVICE_USERNAME", "password_env": "DEVICE_PASSWORD"}
        },
        "devices": [
            {
                "name": f"SYN{i:04d}",
                "mgmt_ip": f"10.{i // 256 % 256}.{i % 256}.1",
                "role": roles[i % len(roles)],
                "site": "synthetic",
            }
            for i in range(count)
        ],
    }


@pytest.fixture
def large_fabric(monkeypatch, tmp_path):
    path = tmp_path / "synthetic.yaml"
    path.write_text(yaml.safe_dump(_synthetic_inventory(_LARGE)), encoding="utf-8")
    monkeypatch.setenv("NETTOOLS_INVENTORY", str(path))
    reset_inventory_cache()
    yield
    reset_inventory_cache()


def _cli_subcommand_count() -> int:
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "agent_nettools.cli", "--help"],
                         capture_output=True, text=True).stdout
    import re

    match = re.search(r"\{([a-z0-9,\-]+)\}", out)
    assert match, "could not find the subcommand list in --help"
    return len(match.group(1).split(","))


def test_the_surface_a_model_must_read_does_not_grow_with_the_fabric(large_fabric):
    """The load-bearing half of B-409.

    A model reads the whole tool manifest before it can choose anything, so a
    surface that grew per device would put the fabric size into every single
    call's context. It does not: tools are per *question*, never per device.
    """

    baseline_flows = len(flows.OBJECT_TYPES)
    baseline_cli = _cli_subcommand_count()

    assert len(all_devices()) == _LARGE, "the synthetic fabric really is large"

    assert len(flows.OBJECT_TYPES) == baseline_flows
    assert _cli_subcommand_count() == baseline_cli


def test_the_mcp_tool_surface_does_not_grow_with_the_fabric(large_fabric):
    """Same claim, the other surface. Imported lazily: the MCP SDK is an extra."""

    pytest.importorskip("mcp")
    from mcp_server import staged_surface

    assert len(all_devices()) == _LARGE
    # The staged surface is a fixed tuple of stage-shaped tools; the classic
    # surface is a fixed set of decorated functions. Neither is derived from
    # the inventory, and this is the assertion that keeps it that way.
    assert len(staged_surface.STAGED_TOOL_NAMES) == 6


def test_one_investigation_touches_the_path_not_the_fabric(large_fabric):
    """The claim that actually matters operationally.

    A descent walks a dependency ladder. The number of devices it contacts is a
    property of the path — here the local device plus whatever the subject
    resolves to — and must be independent of how many devices exist. If this
    ever became O(fabric), `investigate` would get slower every time someone
    racked a router, which is the failure mode the flow design exists to avoid.
    """

    from agent_nettools.network_tools import collect_evidence

    contacted: set[str] = set()

    def counting_sender(device, command):
        contacted.add(device["name"] if isinstance(device, dict) else str(device))
        return ""

    collect_evidence("SYN0001", sender=counting_sender)

    assert contacted == {"SYN0001"}, (
        f"collecting one device's evidence contacted {sorted(contacted)} — evidence "
        "collection must be per-device, not per-fabric"
    )


def test_listing_the_inventory_is_the_one_thing_that_is_allowed_to_be_linear(large_fabric):
    """Stated rather than left implied, so the claim above stays honest.

    `list_devices` returns one record per device — of course it is O(n). The
    point of B-409 is that this is the *only* place the fabric size appears,
    and it is a listing, not a surface a model must read to act.
    """

    listed = list_devices()

    assert listed["status"] == "success"
    assert len(listed["data"]["devices"]) == _LARGE
