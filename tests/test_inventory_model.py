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


# --------------------------------------------------------------------------- #
# B-402 -- operator knowledge notes
# --------------------------------------------------------------------------- #


def test_the_lab_carries_the_facts_this_build_had_to_rediscover():
    """The point of B-402: stop the agent re-deriving known local truth.

    Every seeded note is something this build proved and then had to keep
    re-explaining -- a baseline learned from a broken fabric, self-contradictory
    LLDP, and a lab where zero prefixes is normal.
    """

    from agent_nettools.inventory_model import load_inventory_file

    devices = {d.name: d for d in load_inventory_file().devices}

    assert all(d.notes for d in devices.values()), "every device says something"

    pe2 = " ".join(n.note for n in devices["PE2"].notes)
    assert "learned by" in pe2 and "isolated" in pe2
    assert "suspicious_baseline" in pe2

    p1 = " ".join(n.note for n in devices["P1"].notes)
    # Corrected at OBS-103: LLDP does NOT contradict itself here. The apparent
    # disagreement was between LLDP's device-reported names and the inventory's
    # labels -- P1 was configured as LEAF05_DHCP_SERVER in the t0/t1 captures.
    assert "does NOT contradict itself" in p1
    assert "LEAF05_DHCP_SERVER" in p1
    assert "configured hostname" in p1, "the actual rule a reader needs"

    assert all(
        any("0 prefixes" in n.note for n in d.notes) for d in devices.values()
    ), "bgp_no_prefixes is normal here, and every device should say so"


def test_pe4_bgp_process_claim_is_not_contradicted_by_derived_evidence(monkeypatch):
    """B-504 regression: a comment above PE4's entry once read "No BGP process
    configured at all", which was false -- PE4 runs a separate MP-BGP VPNv4
    process with an Established session to RR1, confirmed from both ends.

    The false claim was never a structured :class:`Note` -- it was a bare
    ``#`` comment, which ``load_inventory_file()`` cannot see at all (PyYAML
    discards comments), which is *how* it survived unnoticed. So this checks
    two things: the DERIVED fact, read from the same real captured evidence
    ``netbox.build_records`` would (not a hardcoded belief about PE4), and
    the raw committed file's text -- both the structured notes and any prose
    around PE4's entry.
    """

    import re

    from helpers import set_device_environment

    from agent_nettools.fixtures import load_fixture_evidence
    from agent_nettools.inventory_model import load_inventory_file, resolve_inventory_path
    from agent_nettools.netbox import build_records

    # 1. The derived fact, from real fixture-captured evidence -- not assumed.
    set_device_environment(monkeypatch)
    pe4_evidence = load_fixture_evidence("PE4", label="t0")
    pe4_record = build_records({"PE4": pe4_evidence}).devices[0]
    assert pe4_record.bgp_vpnv4_peers is not None and pe4_record.bgp_vpnv4_peers > 0, (
        "this regression test only means something if PE4's own captured evidence "
        "shows an active BGP process -- if this fails, the fixture changed, not the claim"
    )

    no_bgp_pattern = re.compile(r"no\s+bgp\s+process", re.IGNORECASE)

    # 2. Structured notes -- what load_inventory_file() actually parses.
    pe4 = next(d for d in load_inventory_file().devices if d.name == "PE4")
    for note in pe4.notes:
        assert not no_bgp_pattern.search(note.note), (
            f"PE4 note (applies_to={note.applies_to!r}) claims no BGP process, "
            "contradicting the device's own captured evidence"
        )

    # 3. Raw text -- comments are invisible to (2) above, which is exactly how
    # the original false claim went unread. Scoped to PE4's own block: any
    # comment lines directly above "  - name: PE4" (its own preceding
    # comment, never some earlier device's), plus PE4's own YAML up to the
    # next top-level device entry -- so this cannot be satisfied by some
    # OTHER device legitimately saying it has no BGP process (the P-routers
    # genuinely do not, on either address family).
    raw = resolve_inventory_path().read_text()
    comment_match = re.search(r"(?:^  #.*\n)+(?=  - name: PE4\n)", raw, re.MULTILINE)
    comment_block = comment_match.group(0) if comment_match else ""
    start = raw.index("  - name: PE4")
    next_device = raw.find("\n  - name:", start)
    device_block = raw[start:] if next_device == -1 else raw[start:next_device]
    pe4_block = comment_block + device_block
    assert not no_bgp_pattern.search(pe4_block), (
        "inventory/lab.yaml's PE4 entry (or a comment above it) claims no BGP process, "
        "which PE4's own captured evidence contradicts (B-504)"
    )


def test_every_note_says_who_wrote_it_and_when():
    """Provenance, not authorisation. A note about a lab that has since been
    rebuilt is worth less than a fresh one, and a reader cannot tell without
    the date."""

    from agent_nettools.inventory_model import load_inventory_file

    for device in load_inventory_file().devices:
        for note in device.notes:
            assert note.author, f"{device.name}: a note with no author"
            assert note.recorded, f"{device.name}: a note with no date"


def test_every_seeded_note_states_what_would_make_it_wrong():
    """The most valuable field when present.

    An operator fact with no expiry condition becomes folklore, and folklore
    outlives the thing it described. Not enforced by the schema -- a note may
    legitimately be timeless -- but every note seeded here has one, because
    every one of them describes a condition that will change.
    """

    from agent_nettools.inventory_model import load_inventory_file

    for device in load_inventory_file().devices:
        for note in device.notes:
            assert note.revisit_when, f"{device.name}/{note.applies_to}: no revisit condition"


def test_notes_are_optional_and_a_note_must_say_something():
    import pytest as _pytest
    from pydantic import ValidationError

    from agent_nettools.inventory_model import Note

    assert Note(note="x").applies_to is None, "a device-wide note needs no subject"
    with _pytest.raises(ValidationError):
        Note(note="   ")


def test_a_typo_in_a_note_is_a_load_failure_not_a_silent_no_op():
    """`extra="forbid"` everywhere, including here. A misspelled key in a note
    would otherwise be dropped and the operator would believe they had recorded
    something."""

    import pytest as _pytest
    from pydantic import ValidationError

    from agent_nettools.inventory_model import Note

    with _pytest.raises(ValidationError):
        Note(note="x", applies__to="bgp")


# --------------------------------------------------------------------------- #
# EER-009 -- ambiguous and path-unsafe identities
# --------------------------------------------------------------------------- #


def test_the_committed_lab_inventory_still_validates_under_every_new_constraint():
    """Positive control (OBS-181), first: every constraint this item adds
    must still accept the real, committed 9-device fabric -- device names,
    router ids, local_as, port, and every credential env var name. If this
    ever fails, either the new validator is wrong or `inventory/lab.yaml`
    needs a real fix, not a loosened check."""

    reset_inventory_cache()
    inventory = load_inventory_file()

    assert len(inventory.devices) == 9
    for device in inventory.devices:
        assert device.name
        if device.router_id is not None:
            assert device.router_id.count(".") == 3


@pytest.mark.parametrize("bad_name", ["", "../..", "..", ".", "a/b", "a b", "x" * 65])
def test_device_name_rejects_unsafe_or_ambiguous_values(tmp_path, bad_name):
    document = _base_document()
    document["devices"][0]["name"] = bad_name
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="storage key"):
        parse_inventory(path)


@pytest.mark.parametrize("good_name", ["P1", "PE4", "RR1", "LAB-ONLY-DEVICE", "a", "x" * 64])
def test_device_name_accepts_the_real_charset(tmp_path, good_name):
    """Positive control for the refusal test above."""

    document = _base_document()
    document["devices"][0]["name"] = good_name
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.devices[0].name == good_name


@pytest.mark.parametrize("bad_site", ["", "   "])
def test_site_must_say_something(tmp_path, bad_site):
    document = _base_document()
    document["devices"][0]["site"] = bad_site
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="site must say something"):
        parse_inventory(path)


def test_router_id_must_be_ipv4_when_present(tmp_path):
    document = _base_document()
    document["devices"][1]["router_id"] = "not-an-ip"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="router_id is not a valid IPv4 address"):
        parse_inventory(path)


def test_router_id_is_optional_and_a_real_one_still_validates(tmp_path):
    """Positive control: `router_id` stays optional (PE4 in the real lab has
    none), and a real IPv4 value parses cleanly."""

    document = _base_document()
    document["devices"][1]["router_id"] = "10.255.0.11"
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.devices[1].router_id == "10.255.0.11"
    assert inventory.devices[0].router_id is None


def test_duplicate_router_id_raises(tmp_path):
    document = _base_document()
    document["devices"][0]["router_id"] = "10.255.0.11"
    document["devices"][1]["router_id"] = "10.255.0.11"
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="duplicates router_id"):
        parse_inventory(path)


def test_two_devices_with_no_router_id_do_not_collide(tmp_path):
    """Positive control: absence is never a duplicate of another absence --
    the real lab has devices with no router_id at all (P1-P4)."""

    document = _base_document()
    document["devices"][0]["router_id"] = None
    document["devices"][1]["router_id"] = None
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.devices[0].router_id is None
    assert inventory.devices[1].router_id is None


def test_duplicate_mgmt_ip_raises(tmp_path):
    document = _base_document()
    document["devices"][1]["mgmt_ip"] = document["devices"][0]["mgmt_ip"]
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="duplicates mgmt_ip"):
        parse_inventory(path)


@pytest.mark.parametrize("bad_as", [0, -1, 4294967296])
def test_local_as_rejects_out_of_range_values(tmp_path, bad_as):
    document = _base_document()
    document["devices"][0]["local_as"] = bad_as
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError):
        parse_inventory(path)


@pytest.mark.parametrize("good_as", [1, 65000, 4294967295])
def test_local_as_accepts_the_full_asn_range(tmp_path, good_as):
    """Positive control: the real lab's ASN (65000) and the full 2-/4-byte
    RFC 6793 range still validate."""

    document = _base_document()
    document["devices"][0]["local_as"] = good_as
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.devices[0].local_as == good_as


@pytest.mark.parametrize("bad_port", [0, -1, 65536])
def test_defaults_port_rejects_out_of_range_values(tmp_path, bad_port):
    document = _base_document()
    document["defaults"]["port"] = bad_port
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError):
        parse_inventory(path)


@pytest.mark.parametrize("good_port", [1, 22, 65535])
def test_defaults_port_accepts_the_real_range(tmp_path, good_port):
    document = _base_document()
    document["defaults"]["port"] = good_port
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.defaults.port == good_port


@pytest.mark.parametrize("field", ["isis_adjacencies", "bgp_peers"])
def test_expected_counts_reject_negative_values(tmp_path, field):
    document = _base_document()
    document["devices"][0]["expected"] = {field: -1}
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError):
        parse_inventory(path)


def test_expected_counts_accept_zero_and_real_values(tmp_path):
    """Positive control: zero is a real, meaningful count (a device with no
    adjacencies is not the same as a device with no `expected:` block at
    all), and it must still validate."""

    document = _base_document()
    document["devices"][0]["expected"] = {"isis_adjacencies": 0, "bgp_peers": 4}
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.devices[0].expected.isis_adjacencies == 0
    assert inventory.devices[0].expected.bgp_peers == 4


@pytest.mark.parametrize(
    "field", ["username_env", "password_env", "ssh_keyfile_env"]
)
@pytest.mark.parametrize("bad_value", ["device_username", "1DEVICE", "DEVICE-NAME", ""])
def test_credential_group_env_names_reject_non_grammar_values(tmp_path, field, bad_value):
    """Grammar only (EER-009's hard constraint): this never asks whether the
    named variable is actually SET in the environment -- only whether the
    string could ever be a legal env var name."""

    document = _base_document()
    document["credential_groups"]["lab"][field] = bad_value
    path = _write(tmp_path, document)

    with pytest.raises(InventoryError, match="environment variable name"):
        parse_inventory(path)


def test_credential_group_env_names_accept_the_real_names(tmp_path):
    """Positive control: the real lab's three env var names
    (DEVICE_USERNAME, DEVICE_PASSWORD, DEVICE_SSH_KEYFILE) all validate, and
    this reads nothing from the actual process environment to do it -- no
    `monkeypatch.setenv` anywhere in this test."""

    document = _base_document()
    document["credential_groups"]["lab"]["ssh_keyfile_env"] = "DEVICE_SSH_KEYFILE"
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    group = inventory.credential_groups["lab"]
    assert group.username_env == "DEVICE_USERNAME"
    assert group.password_env == "DEVICE_PASSWORD"
    assert group.ssh_keyfile_env == "DEVICE_SSH_KEYFILE"


def test_credential_group_validation_reads_nothing_from_the_environment(monkeypatch, tmp_path):
    """The credential-free invariant, pinned directly: a document naming env
    vars that are NOT set in the process environment at all must still
    validate cleanly -- this module may only check the *name*'s grammar,
    never whether it is present (that would leak "is this credential
    configured" into the credential-free layer)."""

    monkeypatch.delenv("TOTALLY_UNSET_VAR_1", raising=False)
    monkeypatch.delenv("TOTALLY_UNSET_VAR_2", raising=False)
    document = _base_document()
    document["credential_groups"]["lab"]["username_env"] = "TOTALLY_UNSET_VAR_1"
    document["credential_groups"]["lab"]["password_env"] = "TOTALLY_UNSET_VAR_2"
    path = _write(tmp_path, document)

    inventory = parse_inventory(path)

    assert inventory.credential_groups["lab"].username_env == "TOTALLY_UNSET_VAR_1"
