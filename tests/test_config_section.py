"""B-104 -- the config axis (D16). Tests for `config_section.py`.

Three concerns, mirroring the module's own docstring:

1. The two parsers round-trip real captured output cleanly (section 0.10).
2. The safety boundary specific to this axis: no unqualified
   `show running-config` is reachable, on any platform, through either
   allowlist -- with a positive control proving the check is not vacuous
   (OBS-181).
3. Nothing captured here can carry a secret past this module, and the
   free-text field this axis DOES add (`config_interface`'s `description`)
   is quoted and budgeted exactly like every other device-authored string
   already reaching a model (invariant 4).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import FIXTURE_DIR

from agent_nettools import config_section as cs
from agent_nettools import model_egress
from agent_nettools import template_parsers as tp
from agent_nettools.fixtures import scrub_output
from agent_nettools.platforms import (
    PLATFORM_INTENTS,
    PLATFORM_TEMPLATES,
    TemplateValidationError,
    known_platforms,
    render_command,
)

# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def test_registry_has_all_config_parsers():
    assert tp.has_template_parser("cisco_xr", "config_isis")
    assert tp.has_template_parser("cisco_xr", "config_interface")
    assert tp.has_template_parser("cisco_xr", "config_ldp")


def test_record_keys_and_volatile_fields_are_registered():
    assert tp.template_record_key("cisco_xr", "config_isis") == "interface"
    # Meta-only shape: nothing to key a records table by.
    assert tp.template_record_key("cisco_xr", "config_interface") is None
    assert tp.template_record_key("cisco_xr", "config_ldp") == "interface"
    # Configuration does not drift between two captures of an unchanged
    # device the way operational counters do.
    assert tp.template_volatile_fields("cisco_xr", "config_isis") == frozenset()
    assert tp.template_volatile_fields("cisco_xr", "config_interface") == frozenset()
    assert tp.template_volatile_fields("cisco_xr", "config_ldp") == frozenset()


def test_config_templates_are_declared_cisco_xr_only():
    """Unlike most Phase-5 templates, deliberately not mirrored onto
    cisco_iosxe/juniper_junos -- there is no live device to verify
    `show running-config`'s section syntax against on either platform, and
    guessing configuration-mode syntax is a worse failure mode than an
    unverified status command (see `platforms.py`'s own verification-status
    note)."""

    assert "config_isis" not in PLATFORM_TEMPLATES.get("cisco_iosxe", {})
    assert "config_isis" not in PLATFORM_TEMPLATES.get("juniper_junos", {})
    assert "config_interface" not in PLATFORM_TEMPLATES.get("cisco_iosxe", {})
    assert "config_interface" not in PLATFORM_TEMPLATES.get("juniper_junos", {})
    assert "config_ldp" not in PLATFORM_TEMPLATES.get("cisco_iosxe", {})
    assert "config_ldp" not in PLATFORM_TEMPLATES.get("juniper_junos", {})


def test_config_ldp_parses_only_configured_interfaces():
    raw = (
        "mpls ldp\n"
        " address-family ipv4\n"
        "  interface GigabitEthernet0/0/0/0\n"
        "  interface GigabitEthernet0/0/0/1\n"
        " !\n"
        "!\n"
    )

    parsed, status = tp.parse_template_output("cisco_xr", "config_ldp", raw)

    assert status is tp.PARSE_OK
    assert parsed["records"] == [
        {"interface": "GigabitEthernet0/0/0/0"},
        {"interface": "GigabitEthernet0/0/0/1"},
    ]
    assert parsed["meta"]["unaccounted_lines"] == []


# --------------------------------------------------------------------------- #
# Fixture round-trips (section 0.10), discovered from disk
# --------------------------------------------------------------------------- #

_CONFIG_ISIS_FIXTURES = sorted(FIXTURE_DIR.glob("cisco_xr/*/*/show-running-config-router-isis.txt"))
_CONFIG_INTERFACE_FIXTURES = sorted(
    FIXTURE_DIR.glob("cisco_xr/*/*/show-running-config-interface-*.txt")
)


def test_at_least_one_fixture_of_each_shape_is_on_disk():
    """A sanity check on the parametrization source below -- the same
    companion `test_template_parsers.py` pairs with its own round-trip
    tests, so a typo'd glob fails loudly rather than silently parametrizing
    over nothing."""

    assert len(_CONFIG_ISIS_FIXTURES) >= 3
    assert len(_CONFIG_INTERFACE_FIXTURES) >= 3


@pytest.mark.parametrize(
    "fixture_path", _CONFIG_ISIS_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR))
)
def test_every_committed_config_isis_fixture_round_trips_clean(fixture_path: Path):
    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "config_isis", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["records"], f"{fixture_path}: no interface records parsed"


@pytest.mark.parametrize(
    "fixture_path", _CONFIG_INTERFACE_FIXTURES, ids=lambda p: str(p.relative_to(FIXTURE_DIR))
)
def test_every_committed_config_interface_fixture_round_trips_clean(fixture_path: Path):
    raw = fixture_path.read_text()
    parsed, status = tp.parse_template_output("cisco_xr", "config_interface", raw)
    assert status is tp.PARSE_OK, f"{fixture_path}: {status}"
    assert parsed["meta"]["unaccounted_lines"] == [], f"{fixture_path}: {parsed['meta']['unaccounted_lines']}"
    assert parsed["meta"]["unparsed_rows"] == 0
    assert parsed["meta"]["interface"]


def _load_fixture(*parts: str) -> str:
    return FIXTURE_DIR.joinpath(*parts).read_text()


# --------------------------------------------------------------------------- #
# The worked example: B-496's isis-broken pair (PE3 <-> P2), the reason
# B-104 exists at all. This is the load-bearing semantic test in this file:
# it proves the config axis actually explains what the descent could not.
# --------------------------------------------------------------------------- #


def test_pe3_gi0_0_0_0_is_isis_enabled_but_has_no_ipv4_address():
    """The isis-broken fixture's own root cause, captured live during this
    task: IS-IS is configured to run on PE3's Gi0/0/0/0 (it has a record,
    it is point-to-point, ipv4 AF is listed) -- but the interface itself
    carries no `ipv4 address` at all, unlike its neighbour (see the next
    test). This is exactly the class of fact `flows.CAUSE_NOT_LOCALISED`
    could not see: the interface rung reads healthy (line protocol up) and
    the isis rung reads broken, and nothing before B-104 could say why."""

    isis_raw = _load_fixture("cisco_xr", "PE3", "isis-broken", "show-running-config-router-isis.txt")
    isis_parsed, isis_status = tp.parse_template_output("cisco_xr", "config_isis", isis_raw)
    assert isis_status is tp.PARSE_OK

    records_by_interface = {r["interface"]: r for r in isis_parsed["records"]}
    assert "GigabitEthernet0/0/0/0" in records_by_interface, (
        "IS-IS is enabled on this interface in config"
    )
    record = records_by_interface["GigabitEthernet0/0/0/0"]
    assert record["point_to_point"] is True
    assert "ipv4" in record["address_families"]

    iface_raw = _load_fixture(
        "cisco_xr", "PE3", "isis-broken", "show-running-config-interface-gi0-0-0-0.txt"
    )
    iface_parsed, iface_status = tp.parse_template_output("cisco_xr", "config_interface", iface_raw)
    assert iface_status is tp.PARSE_OK
    assert iface_parsed["meta"]["ipv4_address"] is None, (
        "PE3's Gi0/0/0/0 has no IPv4 address configured, despite being "
        "enabled for IS-IS -- the observed-vs-intended fact this axis exists "
        "to surface"
    )


def test_p2_gi0_0_0_4_the_far_end_has_an_ipv4_address():
    """The contrast case, same pair, other end: P2's Gi0/0/0/4 (the far
    side of the same broken link) DOES carry an IPv4 address. Without this
    the previous test would not be evidence of anything -- it would be
    equally consistent with a parser that always reports `ipv4_address:
    None` (OBS-181's "a refusal test that has never seen an acceptance
    proves nothing", applied to a semantic finding rather than a safety
    refusal)."""

    raw = _load_fixture("cisco_xr", "P2", "isis-broken", "show-running-config-interface-gi0-0-0-4.txt")
    parsed, status = tp.parse_template_output("cisco_xr", "config_interface", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["ipv4_address"] == "10.0.1.9"
    assert parsed["meta"]["ipv4_netmask"] == "255.255.255.254"


def test_the_healthy_rr1_pair_shows_no_asymmetry():
    """Same two templates, a healthy device (RR1), both physical links --
    every interface IS-IS is enabled on also carries an IPv4 address. The
    companion to the two tests above: proves the parser reports the SAME
    (healthy) shape it would need to for a device with nothing wrong, not
    just the specific broken shape it was written against."""

    isis_raw = _load_fixture("cisco_xr", "RR1", "healthy", "show-running-config-router-isis.txt")
    isis_parsed, _status = tp.parse_template_output("cisco_xr", "config_isis", isis_raw)
    isis_interfaces = {r["interface"] for r in isis_parsed["records"] if r["point_to_point"]}

    for name, slug in (
        ("GigabitEthernet0/0/0/0", "gi0-0-0-0"),
        ("GigabitEthernet0/0/0/1", "gi0-0-0-1"),
    ):
        assert name in isis_interfaces
        raw = _load_fixture("cisco_xr", "RR1", "healthy", f"show-running-config-interface-{slug}.txt")
        parsed, status = tp.parse_template_output("cisco_xr", "config_interface", raw)
        assert status is tp.PARSE_OK
        assert parsed["meta"]["ipv4_address"] is not None, f"{name}: expected an IPv4 address"


# --------------------------------------------------------------------------- #
# A synthetic case the live corpus cannot supply: `shutdown`.
# Same pattern `test_garbage_input_raises_parse_error_and_reports_parse_failed`
# already uses in `tests/test_template_parsers.py` for a case its own fixture
# corpus cannot produce.
# --------------------------------------------------------------------------- #


def test_a_hand_authored_shutdown_line_is_recognised():
    """No interface in this lab is currently administratively shut, so
    `shutdown` is declared from well-known IOS-XR grammar rather than
    measured (see `config_section.py`'s module docstring). Covered here
    with a hand-typed block instead of a captured fixture."""

    raw = (
        "\nWed Aug 19 12:00:00.000 UTC\n"
        "interface GigabitEthernet0/0/0/9\n"
        " description SPARE-NOT-IN-SERVICE\n"
        " shutdown\n"
        "!\n"
    )
    parsed, status = tp.parse_template_output("cisco_xr", "config_interface", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["shutdown"] is True
    assert parsed["meta"]["unaccounted_lines"] == []


def test_garbage_input_raises_parse_error_for_both_config_parsers():
    garbage = "lorem ipsum dolor sit amet\nconsectetur adipiscing"

    with pytest.raises(cs.ParseError):
        cs.parse_xr_config_isis(garbage)
    with pytest.raises(cs.ParseError):
        cs.parse_xr_config_interface(garbage)

    for template in ("config_isis", "config_interface"):
        parsed, status = tp.parse_template_output("cisco_xr", template, garbage)
        assert parsed is None
        assert status is tp.PARSE_FAILED


# --------------------------------------------------------------------------- #
# Never a secret in a structured field.
# --------------------------------------------------------------------------- #


def test_config_isis_never_stores_what_follows_authentication():
    """Global- and interface-scoped `authentication ...` lines both set a
    boolean and nothing else -- the secret-shaped tail of the line must
    never appear anywhere in the parsed result, structured field or
    otherwise."""

    canary = "SECRET-KEYCHAIN-DO-NOT-LEAK"
    raw = (
        "\nWed Aug 19 12:00:00.000 UTC\n"
        "router isis CORE\n"
        " is-type level-2-only\n"
        " net 49.0001.0000.0000.0099.00\n"
        f" authentication key-chain {canary}\n"
        " interface GigabitEthernet0/0/0/0\n"
        "  point-to-point\n"
        f"  authentication key-chain {canary}\n"
        "  address-family ipv4 unicast\n"
        "  !\n"
        " !\n"
        "!\n"
    )
    parsed, status = tp.parse_template_output("cisco_xr", "config_isis", raw)
    assert status is tp.PARSE_OK
    assert parsed["meta"]["authentication_configured"] is True
    assert parsed["records"][0]["authentication_configured"] is True

    import json

    serialised = json.dumps(parsed)
    assert canary not in serialised


def test_scrub_output_redacts_a_synthetic_isis_authentication_line():
    """No live device in this lab configures ISIS or interface
    authentication -- confirmed by inspecting all nine devices' `config_isis`
    output (none contains an `authentication` line at all) -- so
    `scrub_output`'s coverage for this axis, like the base `show` output
    CLAUDE.md already documents, is currently proven only against a
    synthetic line, not a real capture. This is that proof, using shapes
    `scrub_output`'s existing SCRUB_PATTERNS are documented to catch
    (measured directly against `agent_nettools.fixtures.SCRUB_PATTERNS`
    during this task, not assumed): a `password`/`authentication-key`/
    `pre-shared-key`-shaped line, wherever it appears in a `router isis` or
    `interface` block."""

    raw = (
        "router isis CORE\n"
        " interface GigabitEthernet0/0/0/0\n"
        "  authentication-key 7 070C285F4D06\n"
        " !\n"
        "!\n"
        "interface GigabitEthernet0/0/0/1\n"
        " password 7 070C285F4D06\n"
        "!\n"
    )
    scrubbed = scrub_output(raw)

    assert "070C285F4D06" not in scrubbed
    assert "[SCRUBBED]" in scrubbed
    # Structure survives -- scrubbing masks the secret in place, it does not
    # delete the surrounding line (same discipline `fixtures.py`'s module
    # docstring states for the existing patterns).
    assert "authentication-key [SCRUBBED]" in scrubbed
    assert "password [SCRUBBED]" in scrubbed


def test_scrub_output_does_not_touch_a_key_chain_reference():
    """Documented gap, not a defect in scope here: IOS-XR's ISIS
    authentication is a REFERENCE to a `key chain <name>` -- the name is not
    a secret, and the secret material (`key-string ...`) lives in that
    entirely separate top-level config section, which neither
    `config_isis` nor `config_interface` can ever retrieve (a different
    `show running-config` subtree). This test pins that `scrub_output`
    leaves the reference alone -- correct, since there is nothing to scrub
    here -- and exists so a future reader does not mistake the previous
    test's coverage for "every authentication-shaped line is scrubbed"."""

    raw = " authentication key-chain ISIS-KEYS\n"
    assert scrub_output(raw) == raw


# --------------------------------------------------------------------------- #
# Invariant 4: `config_interface`'s `description` is the one genuinely
# free-text field this axis adds.
# --------------------------------------------------------------------------- #


def _config_interface_envelope(description: str) -> dict:
    return {
        "tool": "run_template",
        "device": "PE3",
        "status": "success",
        "timestamp": "2026-08-19T00:00:00Z",
        "data": {
            "template": "config_interface",
            "platform": "cisco_xr",
            "commands": {"show running-config interface Gi0/0/0/0": "raw"},
            "parsed": {
                "meta": {
                    "interface": "GigabitEthernet0/0/0/0",
                    "description": description,
                    "shutdown": False,
                    "vrf": None,
                    "ipv4_address": None,
                    "ipv4_netmask": None,
                    "ipv6_addresses": [],
                    "unaccounted_lines": [],
                    "unparsed_rows": 0,
                },
                "records": [],
            },
            "parse_status": "ok",
        },
        "errors": [],
    }


def test_config_interface_description_is_quoted_by_the_projector():
    canary = "CONFIG-INTERFACE-DESCRIPTION-CANARY"
    projected = model_egress.project_envelope(_config_interface_envelope(canary))
    description = projected["data"]["parsed"]["meta"]["description"]

    assert canary in description
    assert description.startswith(model_egress.DEVICE_TEXT_OPEN)
    assert description.endswith(model_egress.DEVICE_TEXT_CLOSE)


def test_config_interface_other_fields_pass_through_unquoted():
    """Companion (S0.12): the previous test must not be passing because the
    projector quotes *everything* in a `config_interface` envelope, only
    because it correctly targets `description`."""

    projected = model_egress.project_envelope(_config_interface_envelope("ordinary text"))
    meta = projected["data"]["parsed"]["meta"]

    assert meta["interface"] == "GigabitEthernet0/0/0/0"
    assert meta["shutdown"] is False
    assert meta["ipv4_address"] is None
    # commands is withheld to a count, same as every other envelope shape.
    assert "commands_withheld" in projected["data"]
    assert "commands" not in projected["data"]


def test_free_text_fields_table_names_config_interface_description():
    assert ("config_interface", "description") in model_egress.FREE_TEXT_FIELDS


def test_mcp_boundary_inherits_the_same_entry_by_import_not_a_second_table():
    """`mcp_server.boundary` derives its free-text field-name set from
    `model_egress.FREE_TEXT_FIELDS` directly (see that module's own
    docstring) -- so the entry added for this axis needs no matching edit
    on the MCP side. Pinned here so a future refactor that breaks the
    import cannot silently reopen the gap for this specific field."""

    import mcp_server.boundary as boundary

    assert "description" in boundary._FREE_TEXT_FIELD_NAMES


# --------------------------------------------------------------------------- #
# The safety boundary specific to this axis: never `show running-config`
# unqualified, on any platform, through either allowlist.
# --------------------------------------------------------------------------- #


def _is_unqualified_running_config(command: str) -> bool:
    """The one string this axis must never produce, on any platform: bare
    `show running-config`, with no section qualifier at all -- the single
    most dangerous read command available (the entire device configuration,
    secrets included). Reused by both the refusal test below and its
    positive control, so the two are provably checking the same thing."""

    return command.strip() == "show running-config"


def test_no_unqualified_running_config_command_exists_in_any_platform_intents():
    for platform, intents in PLATFORM_INTENTS.items():
        for intent, commands in intents.items():
            for command in commands:
                assert not _is_unqualified_running_config(command), (
                    f"{platform}.{intent} approves bare 'show running-config': {command!r}"
                )


def test_no_unqualified_running_config_template_exists_on_any_platform():
    for platform, templates in PLATFORM_TEMPLATES.items():
        for name, template in templates.items():
            assert not _is_unqualified_running_config(template.format_string), (
                f"{platform}.{name}'s format string is bare 'show running-config': "
                f"{template.format_string!r}"
            )


def test_positive_control_the_unqualified_check_actually_catches_a_bad_entry():
    """OBS-181: a refusal test that has never seen an acceptance proves
    nothing. The two tests above would also pass if `_is_unqualified_
    running_config` were `lambda command: False` -- this proves it is not.
    Runs the exact same helper against synthetic tables shaped like
    `PLATFORM_INTENTS`/`PLATFORM_TEMPLATES` but carrying one deliberately
    bad entry, and asserts it is caught."""

    from agent_nettools.templates import Template

    bad_intents = {"cisco_xr": {"config_all": ("show running-config",)}}
    caught = [
        command
        for intents in bad_intents.values()
        for commands in intents.values()
        for command in commands
        if _is_unqualified_running_config(command)
    ]
    assert caught == ["show running-config"]

    bad_template = Template(name="config_all", format_string="show running-config")
    assert _is_unqualified_running_config(bad_template.format_string)


def test_config_interface_cannot_be_coerced_into_the_bare_command():
    """The parameterised half of the same guarantee: no value of `interface`
    can make `render_command` produce anything other than
    `show running-config interface <name>` -- the literal text around the
    placeholder is never a caller's to change (see templates.py's
    "canonicalize by reconstruction"). Every adversarial string is already
    covered generically by `tests/test_template_security.py` (FROZEN); this
    is the specific claim that matters for this axis."""

    for interface in ("Gi0/0/0/0", "Loopback0", "Bundle-Ether23", "Mg0/RP0/CPU0/0"):
        command = render_command("cisco_xr", "config_interface", interface=interface)
        assert command != "show running-config"
        assert command.startswith("show running-config interface ")

    with pytest.raises(TemplateValidationError):
        render_command("cisco_xr", "config_interface", interface="")


def test_config_isis_takes_no_parameters_at_all():
    """No parameter means no argument surface to coerce in the first
    place -- the strongest form of "cannot be widened" available."""

    assert PLATFORM_TEMPLATES["cisco_xr"]["config_isis"].params == {}
    assert render_command("cisco_xr", "config_isis") == "show running-config router isis"


@pytest.mark.parametrize("platform", known_platforms())
def test_every_config_template_format_string_contains_a_bounded_config_section(platform):
    """Belt-and-braces alongside the two exact-match refusal tests above:
    every `config_*` template name, on every platform that ever declares
    one, must be scoped to a named section -- `router <protocol>`,
    `interface <name>`, or `mpls ldp` -- not just "not exactly the bare
    string"."""

    for name, template in PLATFORM_TEMPLATES.get(platform, {}).items():
        if not name.startswith("config_"):
            continue
        scoped = f" {template.format_string} "
        assert (
            " router " in scoped
            or " interface " in scoped
            or " mpls ldp" in scoped
        ), (
            f"{platform}.{name}: {template.format_string!r} is not scoped to a named section"
        )
