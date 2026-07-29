"""Adversarial tests for the parameterized command templates (Phase 5).

This is the heart of the phase: every template and every one of its
parameters must reject every string in ``ADVERSARIAL_STRINGS`` below, no
command may ever be rendered for a rejected input, and a rendered command
built from valid input must never contain a forbidden character. See
``agent_nettools.templates``'s module docstring for the security model these
tests police ("canonicalize by reconstruction").
"""

from __future__ import annotations

import pytest

from agent_nettools.platforms import (
    PLATFORM_TEMPLATES,
    BoundedIntParam,
    InterfaceNameParam,
    IPv4AddressParam,
    IPv4PrefixParam,
    ParamType,
    TemplateValidationError,
    is_safe_rendered_command,
    known_platforms,
    render_command,
    supports_template,
)
from agent_nettools.templates import FORBIDDEN_CHARACTERS

# Every string here must be rejected for every template/parameter combination,
# regardless of the parameter's declared type -- an IPv4 address parser, a
# prefix parser, an interface-name regex, and a bounded-int parser must all
# independently refuse all of these before any command is ever assembled.
ADVERSARIAL_STRINGS: tuple[str, ...] = (
    "10.0.0.1 | reload",
    "10.0.0.1 | file disk0:/x",
    "10.0.0.1; configure",
    "10.0.0.1 && reload",
    "10.0.0.1\nconfigure terminal",
    "10.0.0.1\rconfigure",
    "10.0.0.1 detail",
    "10.0.0.1`id`",
    "10.0.0.1$(id)",
    "${IFS}",
    "10.0.0.1\x00",
    "01.1.1.1",
    "1.1.1.1.1",
    "999.1.1.1",
    "",
    "A" * 10_000,
    "１０.0.0.1",  # fullwidth "10" digits
    "10.0.0.а",  # Cyrillic "а" (U+0430) homoglyph for Latin "a"
    "../../etc/passwd",
)

# Specific to the "count" (bounded integer) parameter.
COUNT_ADVERSARIAL: tuple[str, ...] = ("0", "501", "-1", "1.5", "1e3", "0x10")

_VALID_BY_TYPE: dict[type, tuple[str, ...]] = {
    IPv4AddressParam: ("10.255.0.31", "0.0.0.0", "255.255.255.255", "192.168.1.1"),
    IPv4PrefixParam: ("10.0.0.0/24", "0.0.0.0/0", "255.255.255.255/32", "172.20.250.0/24"),
    InterfaceNameParam: (
        "Gi0/0/0/2.300",
        "Mg0/RP0/CPU0/0",
        "Bundle-Ether23",
        "Loopback0",
        "srte_c_10_ep",
        "Nu0",
        "BV200",
    ),
    BoundedIntParam: ("1", "20", "250", "500"),
}


def _valid_default(param_type: ParamType) -> str:
    """One always-valid value for a parameter type, used to fill in every
    parameter *other* than the one under test in a multi-parameter template."""

    return _VALID_BY_TYPE[type(param_type)][0]


def _all_template_params() -> list[tuple[str, str, str]]:
    """Every (platform, template_name, param_name) triple across the registry."""

    return [
        (platform, template_name, param_name)
        for platform, templates in PLATFORM_TEMPLATES.items()
        for template_name, template in templates.items()
        for param_name in template.params
    ]


def _bounded_int_params() -> list[tuple[str, str, str]]:
    return [
        (platform, template_name, param_name)
        for platform, templates in PLATFORM_TEMPLATES.items()
        for template_name, template in templates.items()
        for param_name, param_type in template.params.items()
        if isinstance(param_type, BoundedIntParam)
    ]


ALL_TEMPLATE_PARAMS = _all_template_params()
BOUNDED_INT_PARAMS = _bounded_int_params()


@pytest.mark.parametrize("platform,template_name,param_name", ALL_TEMPLATE_PARAMS)
@pytest.mark.parametrize("bad_value", ADVERSARIAL_STRINGS)
def test_every_parameter_rejects_every_adversarial_string(
    platform, template_name, param_name, bad_value
):
    template = PLATFORM_TEMPLATES[platform][template_name]
    kwargs = {name: _valid_default(p_type) for name, p_type in template.params.items()}
    kwargs[param_name] = bad_value

    with pytest.raises(TemplateValidationError):
        render_command(platform, template_name, **kwargs)


@pytest.mark.parametrize("platform,template_name,param_name", BOUNDED_INT_PARAMS)
@pytest.mark.parametrize("bad_count", COUNT_ADVERSARIAL)
def test_bounded_int_parameters_reject_the_count_adversarial_table(
    platform, template_name, param_name, bad_count
):
    with pytest.raises(TemplateValidationError):
        render_command(platform, template_name, **{param_name: bad_count})


def test_logging_count_rejects_every_count_adversarial_value():
    """The concrete case named in the spec: the "logging" template's "count"
    parameter, on every platform that declares it."""

    for platform in known_platforms():
        if not supports_template(platform, "logging"):
            continue
        for bad_count in COUNT_ADVERSARIAL:
            with pytest.raises(TemplateValidationError):
                render_command(platform, "logging", count=bad_count)


def test_rejected_input_never_produces_a_rendered_command():
    """No partial assembly: for every rejected input, render_command must
    raise and never return a string at all -- there is nothing, partial or
    otherwise, left over to inspect or accidentally send."""

    sentinel = object()
    sample_bad_values = ("10.0.0.1 | reload", "", "A" * 10_000, "../../etc/passwd")

    for platform, template_name, param_name in ALL_TEMPLATE_PARAMS:
        template = PLATFORM_TEMPLATES[platform][template_name]
        for bad_value in sample_bad_values:
            kwargs = {name: _valid_default(p_type) for name, p_type in template.params.items()}
            kwargs[param_name] = bad_value

            result = sentinel
            try:
                result = render_command(platform, template_name, **kwargs)
            except TemplateValidationError:
                pass

            assert result is sentinel, (
                f"{platform}.{template_name}.{param_name}={bad_value!r} rendered {result!r} "
                "instead of raising -- nothing should ever be returned for rejected input"
            )


def test_rendered_commands_never_contain_a_forbidden_character_across_many_valid_inputs():
    """Property-style check: across a table of many legitimately valid inputs
    per parameter type, a successfully rendered command must never contain a
    forbidden character and must always pass the post-render safety gate."""

    for platform, templates in PLATFORM_TEMPLATES.items():
        for template_name, template in templates.items():
            for param_name, param_type in template.params.items():
                other_kwargs = {
                    name: _valid_default(p_type)
                    for name, p_type in template.params.items()
                    if name != param_name
                }
                for value in _VALID_BY_TYPE[type(param_type)]:
                    command = render_command(
                        platform, template_name, **{**other_kwargs, param_name: value}
                    )
                    assert is_safe_rendered_command(command)
                    for character in FORBIDDEN_CHARACTERS:
                        assert character not in command, (
                            f"{platform}.{template_name} rendered {command!r} containing "
                            f"{character!r}"
                        )
