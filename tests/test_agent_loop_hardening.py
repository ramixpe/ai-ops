"""Wave 2-B hardening of the agent loop: B-470's last leg, B-471, B-472, B-475.

Companion to ``tests/test_agent_loop.py`` -- kept in a separate file per the
build spec rather than folded into it, but deliberately self-contained (its
own copy of the fake-``anthropic`` helpers, same pattern) so it can be read
and run on its own:

- **B-470/P0-02** (the last leg of B-470/B-467): every ``tool_result``
  content sent to the model is now routed through ``model_egress``, not
  ``json.dumps(result)`` on the raw envelope. Proven here with a literal
  canary string placed in a fake envelope's ``data.commands`` -- including
  one nested two levels deep under ``check_lab_fabric``'s ``data.devices``,
  which is the recursion-coverage question the build spec asked to confirm
  rather than assume.
- **B-471/P0-03**: ``trust_class`` is always ``"exploratory"``; an
  unrecognized ``stop_reason`` is reported honestly as
  ``f"unknown_stop:{reason}"`` (``complete: False``) instead of silently
  masquerading as ``"end_turn"``.
- **B-472/P1-01**: the wall-clock deadline is checked before every
  individual tool dispatch, not just between turns -- a scripted multi-tool
  response with an exhausted fake clock proves the later blocks in the same
  batch are turned away, not executed.
- **B-475/P1-08**: independent ``tool_use`` blocks in one turn run through a
  bounded ``ThreadPoolExecutor``, proven with the same barrier technique
  ``tests/test_mcp_server.py`` uses for the identical fix there
  (``assess_lab_fabric_health``); ``agent_loop._assess_health``'s fabric
  branch got the same fix and the same proof technique.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest
from helpers import set_device_environment

from agent_nettools import cli
from agent_nettools.agent_loop import _assess_health, _skipped_tool_result, run_agent_loop

# --------------------------------------------------------------------------- #
# The fake-anthropic pattern, duplicated from tests/test_agent_loop.py rather
# than imported cross-module (no precedent for that in this suite; shared
# helpers live in tests/helpers.py, not in another test_*.py file).
# --------------------------------------------------------------------------- #


class FakeStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return self._message


def install_scripted_anthropic(monkeypatch, responses, *, captured=None, client_kwargs=None):
    """Install a fake anthropic module that returns ``responses`` in order.

    Each entry is a callable ``(**kwargs) -> message``; once the list is
    exhausted, the last entry is reused for any further call. If ``captured``
    is a list, every ``.stream()`` call's kwargs are appended to it. If
    ``client_kwargs`` is a list, every ``anthropic.Anthropic(**kwargs)``
    construction's kwargs are appended to it -- this is how the B-472
    timeout-ceiling test below observes what was actually passed at client
    construction.
    """

    module = types.ModuleType("anthropic")
    module.APIStatusError = type("APIStatusError", (Exception,), {})
    module.AuthenticationError = type("AuthenticationError", (module.APIStatusError,), {})
    module.NotFoundError = type("NotFoundError", (module.APIStatusError,), {})
    module.RateLimitError = type("RateLimitError", (module.APIStatusError,), {})
    module.APIConnectionError = type("APIConnectionError", (Exception,), {})

    state = {"n": 0}

    def make_stream(**kwargs):
        if captured is not None:
            captured.append(kwargs)
        idx = min(state["n"], len(responses) - 1)
        state["n"] += 1
        message = responses[idx](**kwargs)
        return FakeStream(message)

    class FakeMessages:
        def stream(self, **kwargs):
            return make_stream(**kwargs)

    class FakeBetaMessages:
        def stream(self, **kwargs):
            return make_stream(**kwargs)

    class FakeBeta:
        def __init__(self):
            self.messages = FakeBetaMessages()

    class FakeAnthropic:
        def __init__(self, **kwargs):
            if client_kwargs is not None:
                client_kwargs.append(kwargs)
            self.messages = FakeMessages()
            self.beta = FakeBeta()

    module.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return module


def _usage(**overrides):
    base = {
        "input_tokens": 5,
        "output_tokens": 5,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _text_message(text, stop_reason="end_turn"):
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
        usage=_usage(),
    )


def _tool_use_message(*tool_uses):
    blocks = [
        types.SimpleNamespace(type="tool_use", id=tu["id"], name=tu["name"], input=tu.get("input", {}))
        for tu in tool_uses
    ]
    return types.SimpleNamespace(content=blocks, stop_reason="tool_use", stop_details=None, usage=_usage())


def _envelope(*, device="PE1", intent="facts", commands, status="success"):
    """A hand-built envelope shaped exactly like network_tools.run_intent's
    return value, with a caller-chosen ``commands`` dict -- used to plant a
    canary in ``data.commands`` without needing real device transport."""

    return {
        "tool": "run_approved_commands",
        "device": device,
        "status": status,
        "timestamp": "2026-01-01T00:00:00+00:00",
        "data": {
            "intent": intent,
            "commands": commands,
            "parsed": {"meta": {}, "records": []},
            "parse_status": "ok",
        },
        "errors": [],
    }


CANARY = "CANARY-do-not-leak-9f3a2c"


@pytest.fixture(autouse=True)
def _anthropic_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


# --------------------------------------------------------------------------- #
# B-470/P0-02: every tool_result is projected -- no raw data.commands leak.
# --------------------------------------------------------------------------- #


def test_canary_in_tool_envelope_commands_does_not_survive_into_tool_result(monkeypatch):
    monkeypatch.setattr(
        "agent_nettools.agent_loop.run_intent",
        lambda *a, **kw: _envelope(commands={"show version": CANARY}),
    )
    captured = []
    responses = [
        lambda **kw: _tool_use_message(
            {"id": "toolu_1", "name": "run_lab_intent", "input": {"device_name": "PE1", "intent": "facts"}}
        ),
        lambda **kw: _text_message("done"),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    run_agent_loop("facts for PE1")

    content = captured[1]["messages"][-1]["content"][0]["content"]
    assert CANARY not in content
    assert "commands_withheld" in content


def test_canary_nested_under_check_fabric_devices_does_not_survive(monkeypatch):
    """The recursion-coverage question the build spec asked to confirm:
    check_lab_fabric's per-device envelopes sit two levels deep under
    data.devices, not at the top level project_envelope's own
    ``_envelope_context`` reads -- so this proves the recursive walk still
    reaches them rather than assuming it from reading the source."""

    fake_fabric_result = {
        "tool": "check_fabric",
        "device": "fabric",
        "status": "success",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "data": {
            "check": "bgp",
            "devices": {"PE1": _envelope(intent="bgp", commands={"show bgp summary": CANARY})},
            "unsupported": [],
        },
        "errors": [],
    }
    monkeypatch.setattr("agent_nettools.agent_loop.check_fabric", lambda *a, **kw: fake_fabric_result)

    captured = []
    responses = [
        lambda **kw: _tool_use_message({"id": "toolu_1", "name": "check_lab_fabric", "input": {"check": "bgp"}}),
        lambda **kw: _text_message("done"),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    run_agent_loop("check bgp fabric-wide")

    content = captured[1]["messages"][-1]["content"][0]["content"]
    assert CANARY not in content
    assert "commands_withheld" in content


def test_collect_lab_evidence_routes_through_project_evidence(monkeypatch):
    """collect_lab_evidence returns a full evidence dict (one envelope per
    intent), not a single envelope -- it must go through project_evidence,
    not project_envelope, or every section but the first would be silently
    mis-shaped (project_envelope would look for `data.intent` at the
    evidence dict's own top level, find none, and skip every FREE_TEXT_FIELDS
    check across the whole bundle)."""

    fake_evidence = {
        "device": "PE1",
        "platform": "cisco_xr",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "facts": _envelope(intent="facts", commands={"show version": CANARY}),
    }
    monkeypatch.setattr("agent_nettools.agent_loop.collect_evidence", lambda *a, **kw: fake_evidence)

    captured = []
    responses = [
        lambda **kw: _tool_use_message(
            {"id": "toolu_1", "name": "collect_lab_evidence", "input": {"device_name": "PE1"}}
        ),
        lambda **kw: _text_message("done"),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    run_agent_loop("collect evidence for PE1")

    content = captured[1]["messages"][-1]["content"][0]["content"]
    assert CANARY not in content
    assert "commands_withheld" in content


# --------------------------------------------------------------------------- #
# B-471/P0-03: trust labelling.
# --------------------------------------------------------------------------- #


def test_trust_class_is_always_exploratory(monkeypatch):
    install_scripted_anthropic(monkeypatch, [lambda **kw: _text_message("ok")])

    result = run_agent_loop("anything")

    assert result["trust_class"] == "exploratory"
    assert result["complete"] is True


def test_unknown_stop_reason_is_reported_honestly_not_as_end_turn(monkeypatch):
    def scripted(**kw):
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="partial thought")],
            stop_reason="server_thing",
            stop_details=None,
            usage=_usage(),
        )

    install_scripted_anthropic(monkeypatch, [scripted])

    result = run_agent_loop("investigate")

    assert result["stopped_because"] == "unknown_stop:server_thing"
    assert result["complete"] is False
    assert result["trust_class"] == "exploratory"
    assert result["answer"] == "partial thought"


def test_cli_agent_exits_warning_on_unknown_stop_and_prints_exploratory_label(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_agent_loop",
        lambda question, **kwargs: {
            "answer": "partial",
            "iterations": 1,
            "tool_calls": [],
            "stopped_because": "unknown_stop:server_thing",
            "complete": False,
            "trust_class": "exploratory",
            "elapsed_s": 0.01,
            "overran_budget": False,
        },
    )
    parser = cli.build_parser()
    args = parser.parse_args(["agent", "why is PE2 down"])

    exit_code = args.func(args)
    out = capsys.readouterr()

    assert exit_code == cli.EXIT_WARNING
    assert "exploratory" in out.err
    assert "not the deterministic investigate path" in out.err
    assert "partial" in out.out
    # stdout stays the answer for pipes -- the label is stderr-only.
    assert "exploratory" not in out.out


def test_cli_agent_end_turn_still_exits_ok(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_agent_loop",
        lambda question, **kwargs: {
            "answer": "PE2 is isolated at IS-IS.",
            "iterations": 2,
            "tool_calls": [],
            "stopped_because": "end_turn",
            "complete": True,
            "trust_class": "exploratory",
            "elapsed_s": 1.2,
            "overran_budget": False,
        },
    )
    parser = cli.build_parser()
    args = parser.parse_args(["agent", "why is PE2 down"])

    assert args.func(args) == cli.EXIT_OK
    assert "PE2 is isolated at IS-IS." in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# B-472/P1-01: the deadline is real.
# --------------------------------------------------------------------------- #


def test_skipped_tool_result_shape():
    """Direct unit check of the (call_entry, result_block) pair the deadline
    check hands back for a tool never started."""

    block = types.SimpleNamespace(id="toolu_z", name="list_lab_devices", input={})

    call_entry, result_block = _skipped_tool_result(block)

    assert call_entry == {
        "tool": "list_lab_devices",
        "input": {},
        "is_error": True,
        "error": "not started: time budget exhausted",
        "skipped": True,
    }
    assert result_block["tool_use_id"] == "toolu_z"
    assert result_block["is_error"] is True
    assert result_block["content"] == "not started: time budget exhausted"


def test_deadline_exhaustion_skips_later_blocks_in_the_same_batch(monkeypatch):
    """The deadline check happens per tool_use block, at submit time, in
    block order -- not just once per turn. A response with three blocks
    where the fake clock crosses the deadline mid-batch must execute the
    earlier block and skip the rest, never silently drop any of them."""

    set_device_environment(monkeypatch)  # list_lab_devices -> list_devices() -> load_inventory() needs it

    def three_tools(**kwargs):
        return _tool_use_message(
            {"id": "toolu_a", "name": "list_lab_devices"},
            {"id": "toolu_b", "name": "list_lab_devices"},
            {"id": "toolu_c", "name": "list_lab_devices"},
        )

    install_scripted_anthropic(monkeypatch, [three_tools])

    # start=0.0 -> deadline=1.0. Top-of-loop check (0.1) and block A's submit
    # check (0.2) are both still under budget; block B's submit check (5.0)
    # is not, and block C's clamps to that same expired value (the fake
    # clock's tail repeats once its schedule is exhausted, same technique
    # tests/test_agent_loop.py's time-budget test already uses).
    schedule = [0.0, 0.1, 0.2, 5.0]
    state = {"i": 0}

    def fake_monotonic():
        idx = min(state["i"], len(schedule) - 1)
        state["i"] += 1
        return schedule[idx]

    monkeypatch.setattr("agent_nettools.agent_loop.time.monotonic", fake_monotonic)

    result = run_agent_loop("list devices three times", max_iterations=100, time_budget_s=1)

    assert result["stopped_because"] == "time_budget"
    assert result["overran_budget"] is True
    assert len(result["tool_calls"]) == 3
    first, second, third = result["tool_calls"]
    assert first["skipped"] is False
    assert first["is_error"] is False
    assert second["skipped"] is True
    assert second["error"] == "not started: time budget exhausted"
    assert second["is_error"] is True
    assert third["skipped"] is True


def test_client_constructed_with_timeout_ceiling_from_time_budget(monkeypatch):
    """The chosen B-472 plumbing: since llm_analysis.py cannot be touched to
    pass a per-turn remaining-time timeout through
    `_stream_anthropic_message`, the ceiling is applied once at Anthropic
    client construction, using the *whole* budget, floored at 5s."""

    client_kwargs = []
    install_scripted_anthropic(
        monkeypatch, [lambda **kw: _text_message("ok")], client_kwargs=client_kwargs
    )

    run_agent_loop("question", time_budget_s=30)
    assert client_kwargs[-1]["timeout"] == 30

    run_agent_loop("question", time_budget_s=1)
    assert client_kwargs[-1]["timeout"] == 5.0


# --------------------------------------------------------------------------- #
# B-475/P1-08: parallel tool execution.
# --------------------------------------------------------------------------- #


def test_tool_use_blocks_execute_concurrently_not_sequentially(monkeypatch):
    """Mirrors tests/test_mcp_server.py's
    test_assess_lab_fabric_health_collects_devices_concurrently: a
    threading.Barrier sized to exactly the block count blocks every fake
    call until *all* of them have started. That can only complete if the
    pool dispatched every block before any of them returned -- a
    reintroduced sequential loop would call the first block, then block
    forever waiting for the other three to also have started (which they
    never would, being unreached), and this test would time out and fail
    loudly rather than pass quietly.

    Four blocks, matching the loop's own `max_workers=min(4, len(blocks))`
    cap exactly, so this stays one barrier cycle regardless of that cap.
    """

    barrier = threading.Barrier(4, timeout=5)
    lock = threading.Lock()
    started: list[str] = []

    def fake_run_intent(device_name, intent, **kwargs):
        with lock:
            started.append(device_name)
        barrier.wait()  # only returns once every block has arrived
        return _envelope(device=device_name, intent=intent, commands={})

    monkeypatch.setattr("agent_nettools.agent_loop.run_intent", fake_run_intent)

    responses = [
        lambda **kw: _tool_use_message(
            {"id": "toolu_1", "name": "run_lab_intent", "input": {"device_name": "D1", "intent": "facts"}},
            {"id": "toolu_2", "name": "run_lab_intent", "input": {"device_name": "D2", "intent": "facts"}},
            {"id": "toolu_3", "name": "run_lab_intent", "input": {"device_name": "D3", "intent": "facts"}},
            {"id": "toolu_4", "name": "run_lab_intent", "input": {"device_name": "D4", "intent": "facts"}},
        ),
        lambda **kw: _text_message("done"),
    ]
    install_scripted_anthropic(monkeypatch, responses)

    result = run_agent_loop("check four devices")

    assert sorted(started) == ["D1", "D2", "D3", "D4"]
    assert len(result["tool_calls"]) == 4
    assert all(call["is_error"] is False for call in result["tool_calls"])


def test_assess_health_fabric_branch_collects_devices_concurrently(monkeypatch):
    """The same fix wave 1-D made for
    mcp_server.server.assess_lab_fabric_health's serial `{name:
    collect_evidence(name) for name in names}`, mirrored here for
    agent_loop._assess_health's identical pattern."""

    fake_names = ["D1", "D2", "D3", "D4"]
    monkeypatch.setattr(
        "agent_nettools.agent_loop.list_devices",
        lambda: {"status": "success", "data": {"devices": [{"name": n} for n in fake_names]}},
    )

    barrier = threading.Barrier(len(fake_names), timeout=5)
    lock = threading.Lock()
    started: list[str] = []

    def fake_collect_evidence(device_name):
        with lock:
            started.append(device_name)
        barrier.wait()
        return {"device": device_name, "status": "success", "data": {}, "errors": []}

    monkeypatch.setattr("agent_nettools.agent_loop.collect_evidence", fake_collect_evidence)
    monkeypatch.setattr(
        "agent_nettools.agent_loop.evaluate_fabric",
        lambda evidence_by_device: {"devices": dict(evidence_by_device)},
    )

    result = _assess_health("fabric")

    assert sorted(started) == sorted(fake_names)
    assert list(result["devices"]) == fake_names


# --------------------------------------------------------------------------- #
# Companions: normal behaviour is unchanged by any of the above.
# --------------------------------------------------------------------------- #


def test_two_tool_batch_still_returns_both_results_in_block_order(monkeypatch):
    set_device_environment(monkeypatch)  # list_lab_devices -> list_devices() -> load_inventory() needs it
    monkeypatch.setattr(
        "agent_nettools.agent_loop.run_intent",
        lambda *a, **kw: _envelope(commands={}),
    )
    captured = []
    responses = [
        lambda **kw: _tool_use_message(
            {"id": "toolu_1", "name": "list_lab_devices"},
            {"id": "toolu_2", "name": "run_lab_intent", "input": {"device_name": "PE1", "intent": "facts"}},
        ),
        lambda **kw: _text_message("Done."),
    ]
    install_scripted_anthropic(monkeypatch, responses, captured=captured)

    result = run_agent_loop("Check PE1")

    assert result["stopped_because"] == "end_turn"
    assert result["complete"] is True
    tool_result_turn = captured[1]["messages"][-1]["content"]
    assert [block["tool_use_id"] for block in tool_result_turn] == ["toolu_1", "toolu_2"]
    assert [call["skipped"] for call in result["tool_calls"]] == [False, False]
    assert [call["is_error"] for call in result["tool_calls"]] == [False, False]
