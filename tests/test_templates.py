"""Unit tests for validated, parameterized command templates (Phase 5).

Adversarial/attack-string coverage lives in ``test_template_security.py``;
this module covers the happy path, the parameter-set contract, and the
lookup/registry API.
"""

import pytest

from agent_nettools.platforms import (
    PLATFORM_TEMPLATES,
    InterfaceNameParam,
    IPv4AddressParam,
    IPv4PrefixParam,
    Template,
    TemplateValidationError,
    UnknownTemplateError,
    is_safe_rendered_command,
    known_platform_templates,
    render_command,
    supports_template,
    template_for,
)


def test_cisco_xr_templates_render_the_expected_commands():
    assert render_command("cisco_xr", "route", prefix="10.255.0.31") == "show route 10.255.0.31/32"
    assert render_command("cisco_xr", "route", prefix="10.0.0.0/24") == "show route 10.0.0.0/24"
    assert (
        render_command("cisco_xr", "bgp_neighbor", address="10.255.0.31")
        == "show bgp neighbor 10.255.0.31"
    )
    assert (
        render_command("cisco_xr", "interface", interface="Gi0/0/0/2.300")
        == "show interfaces Gi0/0/0/2.300"
    )
    assert render_command("cisco_xr", "logging", count="20") == "show logging last 20"
    assert render_command("cisco_xr", "ping", address="10.255.0.31") == "ping 10.255.0.31"
    assert (
        render_command("cisco_xr", "traceroute", address="10.255.0.31")
        == "traceroute 10.255.0.31"
    )


def test_cisco_iosxe_templates_use_different_syntax_for_the_same_template_name():
    """Same template name, different vendor syntax -- proves the abstraction is
    real, exactly like PLATFORM_INTENTS."""

    assert render_command("cisco_iosxe", "route", prefix="10.0.0.0/24") == "show ip route 10.0.0.0/24"
    assert (
        render_command("cisco_iosxe", "bgp_neighbor", address="10.255.0.31")
        == "show ip bgp neighbors 10.255.0.31"
    )


@pytest.mark.parametrize(
    "interface",
    [
        "Gi0/0/0/2.300",
        "Mg0/RP0/CPU0/0",
        "Bundle-Ether23",
        "Loopback0",
        "srte_c_10_ep",
        "Nu0",
        "BV200",
    ],
)
def test_interface_regex_accepts_every_fixture_interface_name(interface):
    assert render_command("cisco_xr", "interface", interface=interface) == f"show interfaces {interface}"


def test_ipv4_prefix_canonicalizes_host_bits_to_network_address():
    """`strict=False` accepts a host address with the "wrong" bits set for its
    mask, but the *rendered* command always carries the network address, not
    the caller's original text -- reconstruction, not pass-through."""

    assert render_command("cisco_xr", "route", prefix="10.0.0.5/24") == "show route 10.0.0.0/24"


def test_ipv4_address_leading_zeros_are_rejected_not_normalized():
    with pytest.raises(TemplateValidationError):
        render_command("cisco_xr", "bgp_neighbor", address="010.0.0.1")


def test_render_command_rejects_missing_parameter():
    with pytest.raises(TemplateValidationError, match="missing"):
        render_command("cisco_xr", "route")


def test_render_command_rejects_unexpected_parameter():
    with pytest.raises(TemplateValidationError, match="unexpected"):
        render_command("cisco_xr", "route", prefix="10.0.0.0/24", extra="oops")


def test_render_command_rejects_a_non_string_parameter():
    with pytest.raises(TemplateValidationError):
        render_command("cisco_xr", "logging", count=20)  # an int, not "20"


def test_unknown_template_raises_for_a_known_platform():
    assert not supports_template("cisco_xr", "no_such_template")
    with pytest.raises(UnknownTemplateError):
        template_for("cisco_xr", "no_such_template")
    with pytest.raises(UnknownTemplateError):
        render_command("cisco_xr", "no_such_template", address="10.0.0.1")


def test_unknown_platform_has_no_templates():
    assert known_platform_templates("not-a-platform") == ()
    assert not supports_template("not-a-platform", "route")


def test_junos_has_no_templates_declared_yet():
    """Junos has no PLATFORM_TEMPLATES entries at all -- proves an
    undeclared platform fails closed the same way an undeclared intent does."""

    assert known_platform_templates("juniper_junos") == ()
    assert not supports_template("juniper_junos", "route")


def test_is_safe_rendered_command_accepts_real_rendered_output():
    for platform, templates in PLATFORM_TEMPLATES.items():
        for name in templates:
            template = templates[name]
            sample_params = {
                p_name: {
                    IPv4AddressParam: "10.255.0.31",
                    IPv4PrefixParam: "10.0.0.0/24",
                    InterfaceNameParam: "Loopback0",
                }.get(type(p_type), "20")
                for p_name, p_type in template.params.items()
            }
            command = render_command(platform, name, **sample_params)
            assert is_safe_rendered_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "",
        "show route 10.0.0.1 | reload",
        "configure terminal",
        "reload",
        "show route 10.0.0.1; configure",
        "show route 10.0.0.1\nreload",
        "show route 10.0.0.1\x00",
        "１０ show version",  # non-ascii
    ],
)
def test_is_safe_rendered_command_rejects_unsafe_or_non_show_strings(command):
    assert not is_safe_rendered_command(command)


def test_multi_parameter_rejection_does_not_leak_a_partially_rendered_command(monkeypatch):
    """Guards the *pattern* for future multi-parameter templates: every
    parameter must be validated before ``str.format`` is ever called, so one
    bad parameter can never yield a command with the *other*, valid
    parameter already substituted in. Today's real templates all take
    exactly one parameter, so this is exercised against a synthetic
    two-parameter template registered only for the duration of this test.
    """

    synthetic = Template(
        name="_test_two_param",
        format_string="show route {prefix} vrf {vrf_name}",
        params={"prefix": IPv4PrefixParam(), "vrf_name": InterfaceNameParam()},
    )
    patched = dict(PLATFORM_TEMPLATES["cisco_xr"])
    patched["_test_two_param"] = synthetic
    monkeypatch.setitem(PLATFORM_TEMPLATES, "cisco_xr", patched)

    with pytest.raises(TemplateValidationError) as excinfo:
        render_command(
            "cisco_xr", "_test_two_param", prefix="10.0.0.0/24", vrf_name="bad name; reload"
        )

    # The valid "prefix" value must never appear pre-substituted anywhere --
    # there is no partially rendered command to leak, not even in the error.
    assert "show route 10.0.0.0/24" not in str(excinfo.value)


def test_render_command_attempts_every_parameter_before_ever_assembling_a_command(monkeypatch):
    """A more direct version of the above: spy on every ``ParamType.parse``
    call to prove each parameter is attempted, and that the parameter-level
    exception is what propagates -- there is no separate "assemble, then
    validate" step in between where a partial string could exist even
    transiently."""

    synthetic = Template(
        name="_test_two_param_2",
        format_string="show route {prefix} vrf {vrf_name}",
        params={"prefix": IPv4PrefixParam(), "vrf_name": InterfaceNameParam()},
    )
    patched = dict(PLATFORM_TEMPLATES["cisco_xr"])
    patched["_test_two_param_2"] = synthetic
    monkeypatch.setitem(PLATFORM_TEMPLATES, "cisco_xr", patched)

    parsed_names: list[str] = []
    original_prefix_parse = IPv4PrefixParam.parse
    original_interface_parse = InterfaceNameParam.parse

    def spy_prefix_parse(self, name, value):
        parsed_names.append(name)
        return original_prefix_parse(self, name, value)

    def spy_interface_parse(self, name, value):
        parsed_names.append(name)
        return original_interface_parse(self, name, value)

    monkeypatch.setattr(IPv4PrefixParam, "parse", spy_prefix_parse)
    monkeypatch.setattr(InterfaceNameParam, "parse", spy_interface_parse)

    with pytest.raises(TemplateValidationError):
        render_command(
            "cisco_xr", "_test_two_param_2", prefix="10.0.0.0/24", vrf_name="bad name; reload"
        )

    # Both parameters were attempted (order is dict-iteration order, which
    # matches insertion order of the kwargs actually supplied to
    # render_command -- not documented API, just what proves nothing was
    # skipped), and the failure came from the bad one, not a rendering step
    # downstream of a supposedly-already-assembled command.
    assert set(parsed_names) == {"prefix", "vrf_name"}
