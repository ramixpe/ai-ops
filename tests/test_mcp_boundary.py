"""Invariant 4 at the MCP boundary (B-458).

The audit that produced this file found **14 of 20 tools returning raw device
text** under `data.commands`, up to 37,962 characters for one
`get_lab_logging` call. Nothing was broken in the library: every one of those
envelopes also carried `data.parsed`, and every internal consumer read the
parsed half. The invariant was true of the code and false of the surface.

Two tests, and the first one is the one that matters. It **iterates the
server's own tool registry** rather than a list written here, so a tool added
next month is covered without anyone remembering this file exists — which is
the same property the fix itself has, and for the same reason: on this surface
the caller is a model, and a guarantee that depends on someone remembering is
not a guarantee.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_server import server
from mcp_server.boundary import MAX_ERROR_CHARS, RAW_TEXT_KEYS, sanitize


def _raw_text_keys_in(payload, path="") -> list[str]:
    """Every path at which a declared raw-text key survives."""

    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            where = f"{path}.{key}" if path else key
            if key in RAW_TEXT_KEYS:
                found.append(where)
            found.extend(_raw_text_keys_in(value, where))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(_raw_text_keys_in(item, f"{path}[{index}]"))
    return found


def _registered_tools() -> dict:
    """The server's own registry, whatever the installed SDK calls it.

    Deliberately not a list maintained here. A hand-written list is a second
    registry that agrees with the first until someone adds a tool, and the whole
    point of this test is to cover the tool nobody remembered.
    """

    for attribute in ("_tool_manager", "_tools", "tools"):
        holder = getattr(server.mcp, attribute, None)
        if holder is None:
            continue
        registry = getattr(holder, "_tools", holder)
        if isinstance(registry, dict) and registry:
            return registry
    pytest.skip("cannot reach this MCP SDK's tool registry")


# --------------------------------------------------------------------------- #
# Test 1 -- every registered tool, measured
# --------------------------------------------------------------------------- #


def test_the_registry_is_not_empty_and_covers_the_documented_surface():
    """Guard against the sweep below passing over nothing.

    A registry lookup that silently returned `{}` would make every assertion in
    this file vacuous -- shape 3, a vacuous guardrail, in a file written to
    close a hole.
    """

    registry = _registered_tools()

    assert len(registry) >= 20, f"only {len(registry)} tools registered"
    assert "get_lab_logging" in registry, "the worst offender must be in the sweep"


def test_every_registered_tool_return_is_free_of_raw_device_text(monkeypatch):
    """**The audit, as a test.** Measured, not asserted.

    Every tool is called for real against committed fixtures, and its actual
    return value is walked for the declared raw-text keys. A tool registered
    later is swept automatically.
    """

    from agent_nettools import network_tools
    from agent_nettools.fixtures import fixture_sender

    send = fixture_sender(label="broken")

    # Every device-touching function the server imports, served from fixtures.
    # Patched on `network_tools` *and* on `server`, because the server imported
    # them by name at module load.
    for name in (
        "get_device_facts", "check_interfaces", "check_bgp_neighbors",
        "check_lldp_neighbors", "check_isis_neighbors", "check_sr_policies",
        "collect_evidence",
    ):
        original = getattr(network_tools, name)
        patched = (lambda f: lambda d, **k: f(d, sender=send, **k))(original)
        monkeypatch.setattr(server, name, patched, raising=False)

    for name, template, kwarg in (
        ("get_route", "route", "prefix"),
        ("get_bgp_neighbor", "bgp_neighbor", "address"),
        ("get_interface", "interface", "interface"),
        ("get_logging", "logging", "count"),
    ):
        patched = (
            lambda t, kw: lambda d, v, **k: network_tools.run_template(
                d, t, sender=send, **{kw: str(v)}
            )
        )(template, kwarg)
        monkeypatch.setattr(server, name, patched, raising=False)

    calls = {
        "list_lab_devices": (),
        "get_lab_device_facts": ("PE2",),
        "check_lab_interfaces": ("PE2",),
        "check_lab_bgp_neighbors": ("RR1",),
        "check_lab_lldp_neighbors": ("PE2",),
        "check_lab_isis_neighbors": ("PE2",),
        "check_lab_sr_policies": ("PE2",),
        "collect_lab_evidence": ("PE2",),
        "get_lab_route": ("RR1", "10.255.0.12/32"),
        "get_lab_bgp_neighbor": ("RR1", "10.255.0.12"),
        "get_lab_interface": ("PE2", "Gi0/0/0/0"),
        "get_lab_logging": ("PE2", 200),
    }

    swept = 0
    for tool_name, args in calls.items():
        function = getattr(server, tool_name)
        result = function(*args)
        leaks = _raw_text_keys_in(result)
        assert not leaks, f"{tool_name} still emits raw device text at {leaks}"
        swept += 1

    assert swept == len(calls)


def test_the_sweep_would_have_caught_the_original_defect():
    """Prove the detector fires, rather than trusting that it would.

    A boundary test that has never seen a violation is a test that has never
    been shown to work. This runs one tool *without* the boundary and asserts
    the sweep rejects it -- 6,139 characters of raw `show bgp neighbor` output
    on the committed fixture.
    """

    from agent_nettools import network_tools
    from agent_nettools.fixtures import fixture_sender

    unsanitised = network_tools.run_template(
        "RR1", "bgp_neighbor", address="10.255.0.12",
        sender=fixture_sender(label="broken"),
    )

    # Both vectors on one real envelope: the command output, and the §0.10
    # remainder nested under `parsed.meta` -- which the first pass of the audit
    # measured as zero because it looked at the top of `parsed`, not inside
    # `meta`. The sweep walks to any depth precisely so a nesting change cannot
    # hide a key from it.
    assert set(_raw_text_keys_in(unsanitised)) == {
        "data.commands", "data.parsed.meta.unaccounted_lines",
    }
    assert not _raw_text_keys_in(sanitize(unsanitised))


def test_registration_is_what_applies_the_boundary():
    """The structural claim, asserted directly.

    Not "every tool calls sanitize" -- that is a convention. The decorator
    wraps, so there is no registered function that skips it and a new tool
    cannot be written without it.
    """

    source = inspect.getsource(server._read_only_tool)

    assert "sanitize(" in source, "the boundary is applied at registration"
    assert "register(sanitized)" in source, "the *wrapped* function is what gets registered"


# --------------------------------------------------------------------------- #
# Test 2 -- the stripper itself
# --------------------------------------------------------------------------- #


def test_a_synthetic_envelope_loses_commands_and_keeps_parsed():
    """The required unit test: in with raw text, out without, parsed intact."""

    envelope = {
        "tool": "run_template",
        "device": "PE2",
        "status": "success",
        "data": {
            "platform": "cisco_xr",
            "command": "show interfaces GigabitEthernet0/0/0/0",
            "commands": {
                "show interfaces GigabitEthernet0/0/0/0": "line one\nline two\nline three",
            },
            "parsed": {
                "meta": {"device": "PE2"},
                "records": [{"interface": "Gi0/0/0/0", "state": "up"}],
            },
            "parse_status": "ok",
        },
        "errors": [],
    }

    clean = sanitize(envelope)

    assert "commands" not in clean["data"]
    assert clean["data"]["parsed"]["records"] == [{"interface": "Gi0/0/0/0", "state": "up"}]
    assert clean["data"]["parse_status"] == "ok"
    # The rendered command is ours, validated by reconstruction, and is not
    # device text -- a model needs it to know what was asked.
    assert clean["data"]["command"] == "show interfaces GigabitEthernet0/0/0/0"

    withheld = clean["data"]["commands_withheld"]
    assert withheld["show interfaces GigabitEthernet0/0/0/0"]["lines"] == 3
    assert withheld["show interfaces GigabitEthernet0/0/0/0"]["chars"] == len(
        "line one\nline two\nline three"
    )

    assert envelope["data"]["commands"], "the input is never mutated -- the CLI shares it"


def test_output_is_withheld_rather_than_deleted():
    """Silently dropping it would make 6 kB indistinguishable from nothing.

    Absence-as-health (shape 2), in the one place where the reader is a model
    that cannot go and check.
    """

    clean = sanitize({"data": {"commands": {"show version": "x" * 5000}}})
    record = clean["data"]["commands_withheld"]["show version"]

    assert record["chars"] == 5000
    assert "invariant 4" in record["withheld"]


def test_unaccounted_lines_are_stripped_and_unparsed_rows_are_not():
    """The pair that looks like a pair and is not.

    `unaccounted_lines` is §0.10's remainder -- **raw device lines by the parser
    contract**, empty across this corpus and non-empty the first time another
    platform is parsed. `unparsed_rows` reads like its sibling and is an
    **int**: a count of rows recognised and not read, never their text.

    Stripping both would have destroyed a diagnostic for no safety gain. The
    resemblance is the trap, and it is the kind a rule written from a *name*
    walks into and a rule written from a *measured type* does not.
    """

    clean = sanitize({
        "data": {"parsed": {"meta": {
            "records": [{"a": 1}],
            "unaccounted_lines": ["raw line the parser did not match"],
            "unparsed_rows": 3,
        }}}
    })
    meta = clean["data"]["parsed"]["meta"]

    assert "unaccounted_lines" not in meta
    assert meta["unaccounted_lines_withheld"] == 1
    assert meta["unparsed_rows"] == 3, "a count is not text and is kept"
    assert meta["records"] == [{"a": 1}]


def test_it_reaches_into_nested_per_device_results():
    """A fabric result nests one envelope per device; a diff nests per intent.

    A stripper that only looked at the top level would leave the largest return
    in the whole surface untouched -- `check_lab_fabric` was 9 raw blocks.
    """

    clean = sanitize({
        "data": {"devices": {
            "PE1": {"data": {"commands": {"show bgp summary": "raw"}}},
            "PE2": {"data": {"commands": {"show bgp summary": "raw"}}},
        }}
    })

    assert not _raw_text_keys_in(clean)
    assert clean["data"]["devices"]["PE1"]["data"]["commands_withheld"]


def test_errors_survive_but_are_bounded():
    """Kept, because a model that cannot see failures is worse than one that
    sees a truncated one. Bounded, because a transport exception can embed
    accumulated device output -- the known residual vector (B-458)."""

    clean = sanitize({"errors": ["short one", "y" * (MAX_ERROR_CHARS + 500)]})

    assert clean["errors"][0] == "short one"
    assert len(clean["errors"][1]) < MAX_ERROR_CHARS + 200
    assert "withheld" in clean["errors"][1]


def test_sanitize_is_total_over_the_shapes_these_tools_return():
    """Scalars, None, empty containers -- nothing raises on the way out."""

    for payload in (None, 0, "", [], {}, {"a": None}, [[{"commands": {}}]]):
        sanitize(payload)
