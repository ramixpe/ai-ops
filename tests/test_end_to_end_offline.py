"""The whole pipeline, offline, on both labels (T-032).

    resolve scope -> descend -> correlate -> report -> ground -> emit -> exit code

Everything real except the model, which is a mock that **reads its own prompt**
rather than returning a canned string. That matters: a mock returning a
hardcoded report would pass on a pipeline that rendered the wrong descent into
the prompt, because the report would be right anyway. This one derives its
answer from what it was handed, so a prompt carrying the wrong descent produces
a report that fails grounding.

**Both labels, as T-025 does.** The same peer -- `RR1 -> 10.255.0.12` -- must
give opposite answers on `healthy` and `broken`, all the way through to a
grounded report and an exit code. One label proves the pipeline runs. Two prove
it discriminates, and only the second is worth having: a pipeline hardwired to
return `interface_line_down` passes every single-label test.

No network, and that is enforced rather than assumed: `netmiko` is replaced with
a module that raises on contact, so any transport attempt fails the test loudly
instead of quietly reaching a lab that may happen to be up.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from agent_nettools import cli, log_window, template_parsers
from agent_nettools.fixtures import fixture_sender
from agent_nettools.grounding import observation_labels
from agent_nettools.investigation import EMITTED, NOT_ATTEMPTED, investigate

FIXTURES = __import__("pathlib").Path(__file__).resolve().parent / "fixtures" / "cisco_xr"
_OWNER = {"10.255.0.12": "PE2", "10.255.0.31": "RR1"}


# --------------------------------------------------------------------------- #
# The offline guarantees, enforced
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Replace netmiko with a module that refuses to be used.

    `netmiko` is imported lazily inside `_netmiko_send_commands`, so this is the
    single choke point every real device connection passes through. If any part
    of the pipeline reaches for transport, the test fails here rather than
    silently succeeding against a lab that happens to be reachable -- which is
    exactly how an "offline" test stops being one.
    """

    def refuse(*args, **kwargs):
        raise AssertionError(
            "the offline pipeline attempted a real SSH connection; "
            "every read must come from committed fixtures"
        )

    fake = types.ModuleType("netmiko")
    fake.ConnectHandler = refuse
    fake.NetmikoAuthenticationException = type("NetmikoAuthenticationException", (Exception,), {})
    fake.NetmikoTimeoutException = type("NetmikoTimeoutException", (Exception,), {})
    monkeypatch.setitem(sys.modules, "netmiko", fake)


@pytest.fixture(autouse=True)
def _no_environment(monkeypatch):
    """No credentials, no API keys. Offline means offline."""

    for name in ("DEVICE_USERNAME", "DEVICE_PASSWORD", "DEVICE_SSH_KEYFILE",
                 "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(cli, "find_dotenv", lambda *a, **k: "")


def test_the_guard_actually_fires():
    """§0.12 for the guard itself.

    Every assertion in this file rests on "no network was used". If the fake
    netmiko were never reached -- wrong module name, wrong attribute -- the
    guard would be decorative and every test would still pass.
    """

    import netmiko

    with pytest.raises(AssertionError, match="attempted a real SSH connection"):
        netmiko.ConnectHandler(host="1.2.3.4")


# --------------------------------------------------------------------------- #
# A model that reads its prompt
# --------------------------------------------------------------------------- #


def _embedded_object(prompt: str, key: str) -> dict:
    """Pull the JSON object containing ``key`` out of a rendered prompt.

    The mock model parses its own input, exactly as a real one would. Scanning
    for a balanced object rather than slicing at a marker, so a prompt rewrap
    cannot break it.
    """

    for start, char in enumerate(prompt):
        if char != "{":
            continue
        depth = 0
        for end in range(start, len(prompt)):
            depth += prompt[end] == "{"
            depth -= prompt[end] == "}"
            if depth == 0:
                try:
                    candidate = json.loads(prompt[start:end + 1])
                except ValueError:
                    break
                if isinstance(candidate, dict) and key in candidate:
                    return candidate
                break
    raise AssertionError(f"no JSON object with {key!r} in the rendered prompt")


class ReadsItsPrompt:
    """A mock analyst that answers from the prompt it was given.

    Deliberately not a canned string. A hardcoded report is right whatever the
    pipeline renders, so it cannot detect the pipeline handing the model the
    wrong descent -- which is the single most likely wiring bug in a chain this
    long, and the one an end-to-end test exists to catch.
    """

    def __init__(self):
        self.saw_descents: list[dict] = []
        self.saw_windows: list[dict] = []
        #: The rendered prompts verbatim. Kept separately from the parsed
        #: payloads above because invariant 4 is a property of the *text* the
        #: model receives, and asserting it against the parsed object would
        #: check a value that cannot contain raw output by construction.
        self.prompts: list[str] = []

    def __call__(self, prompt) -> str:
        # B-421: `analyst` now receives a `prompt_library.RenderedPrompt`
        # (system/user pre-split, so `complete_prompt` can cache the static
        # half), not one fully-rendered string. `system + user`, in that
        # order, is exactly the text a single string used to be -- this mock
        # (and invariant 4 below, which scans `self.prompts` for raw device
        # text) reassembles it rather than changing what either checks.
        prompt = f"{prompt.system}\n\n{prompt.user}"
        self.prompts.append(prompt)
        if "LOG WINDOW" in prompt:
            window = _embedded_object(prompt, "entries")
            self.saw_windows.append(window)
            entries = window["entries"]
            if not entries:
                return json.dumps({"timeline": [], "correlation": {
                    "found": False,
                    "summary": "no correlating events in the available coverage",
                    "followed_a_commit": False, "recurrence": "none"}})
            return json.dumps({
                "timeline": [{"at": e["at"], "event": e["text"][:40], "mnemonic": e["mnemonic"]}
                             for e in entries[:3]],
                "correlation": {"found": True, "summary": "events precede the finding",
                                "followed_a_commit": True, "recurrence": "once"},
            })

        descent = _embedded_object(prompt, "rungs")
        self.saw_descents.append(descent)
        observations = [
            {"claim": f"{r['rung']} on {r['device']} is {r['status']}",
             "evidence_key": r["evidence_keys"][0]}
            for r in descent["rungs"] if r["evidence_keys"]
        ]
        labels = observation_labels(len(observations))
        return json.dumps({
            "observations": observations,
            "interpretations": [{
                "claim": f"The descent found {descent['finding']}.",
                "based_on": list(labels),
            }],
            "recommendation": {"next_check": "confirm with the operator",
                               "requires_human": True},
        })


def _window(device: str, label: str):
    raw = (FIXTURES / device / label / "show-logging-last-200.txt").read_text()
    parsed, status = template_parsers.parse_template_output("cisco_xr", "logging", raw)
    assert status is template_parsers.PARSE_OK
    return log_window.shape_window(
        parsed["records"], coverage=log_window.coverage_from_logging(parsed, device)
    )


def _pipeline(label: str, analyst):
    return investigate(
        "RR1", "10.255.0.12", flow="bgp_session", analyst=analyst,
        resolver=lambda s: _OWNER[s], sender=fixture_sender(label=label),
        window=lambda device: _window(device, label),
    )


# --------------------------------------------------------------------------- #
# Both labels, end to end
# --------------------------------------------------------------------------- #


def test_the_broken_label_runs_the_whole_pipeline_and_grounds():
    analyst = ReadsItsPrompt()
    result = _pipeline("broken", analyst)

    assert result.finding == "interface_line_down"
    assert result.descent.cause.device == "PE2"
    assert len(result.descent.causal_chain) == 4

    assert result.report_status == EMITTED
    assert result.report_grounding.ok, result.report_grounding.summary()
    assert result.report_grounding.rungs_covered == 5
    assert len(result.report["observations"]) == 5
    assert result.report["recommendation"]["requires_human"] is True

    assert result.correlation_status == EMITTED
    assert result.correlation["correlation"]["found"] is True
    assert result.coverage is not None and result.coverage.device == "PE2"


def test_the_healthy_label_runs_the_whole_pipeline_and_grounds():
    analyst = ReadsItsPrompt()
    result = _pipeline("healthy", analyst)

    assert result.finding == "all_layers_healthy"
    assert result.descent.cause is None

    assert result.report_status == EMITTED
    assert result.report_grounding.ok, result.report_grounding.summary()
    assert result.report_grounding.rungs_required == 5, "every rung was read and healthy"

    assert result.correlation_status == NOT_ATTEMPTED, "no cause, nothing to place in time"


def test_the_same_peer_gives_opposite_answers_on_the_two_labels():
    """The assertion that makes this suite worth running.

    Identical device, identical subject, identical flow, identical code. Only
    the captured state differs, and every layer of the pipeline has to carry
    that difference through -- parser, check, descent, prompt, model, grounding.
    A pipeline hardwired to return `interface_line_down` passes every
    single-label test above and fails here.
    """

    broken = _pipeline("broken", ReadsItsPrompt())
    healthy = _pipeline("healthy", ReadsItsPrompt())

    assert broken.device == healthy.device == "RR1"
    assert broken.subject == healthy.subject == "10.255.0.12"

    assert broken.finding != healthy.finding
    assert {o.status for o in broken.descent.outcomes} == {"broken"}
    assert {o.status for o in healthy.descent.outcomes} == {"healthy"}

    assert broken.report_grounding.ok and healthy.report_grounding.ok
    assert "interface_line_down" in json.dumps(broken.report)
    assert "interface_line_down" not in json.dumps(healthy.report)


def test_the_model_was_handed_the_right_descent_on_each_label():
    """What the prompt-reading mock buys.

    A pipeline that rendered the `broken` descent into the prompt on a `healthy`
    run would still produce a groundable report -- the report would just be
    about the wrong thing, and every assertion above would pass.
    """

    for label, expected in (("broken", "interface_line_down"),
                            ("healthy", "all_layers_healthy")):
        analyst = ReadsItsPrompt()
        _pipeline(label, analyst)

        assert len(analyst.saw_descents) == 1
        assert analyst.saw_descents[0]["finding"] == expected
        assert analyst.saw_descents[0]["subject"] == "10.255.0.12"


def test_no_unparsed_device_text_reaches_the_model():
    """Invariant 4, asserted at the point it could actually be violated.

    `prompt_library` cannot violate it structurally, but the *runner* assembles
    the inputs. This checks the rendered prompts themselves against raw text
    that is definitely in the fixtures and must never be in a prompt.
    """

    analyst = ReadsItsPrompt()
    result = _pipeline("broken", analyst)
    assert result.report_status == EMITTED
    assert len(analyst.prompts) == 2

    # Strings that are definitely in the fixtures this run read, and that no
    # prompt may contain. Asserted against the verbatim rendered prompts, not
    # against the parsed payloads -- a parsed payload cannot hold raw output by
    # construction, so checking it would be checking nothing.
    raw = (FIXTURES / "RR1" / "broken" / "show-version.txt").read_text()
    assert "Cisco IOS XR Software" in raw, "the corpus must contain what we forbid"

    for rendered in analyst.prompts:
        for forbidden in ("Cisco IOS XR Software", "Build Information",
                          "BGP router identifier"):
            assert forbidden not in rendered, f"raw device text reached the model: {forbidden}"

    # The correlate prompt legitimately carries device *log lines*, which are
    # parsed records re-serialised, not raw command output. Distinguishing the
    # two is the whole content of invariant 4 here, so it is stated rather than
    # left as an apparent inconsistency.
    correlate = next(p for p in analyst.prompts if "LOG WINDOW" in p)
    assert "Aug 16 07:41:54.688 UTC" in correlate
    assert "Log Buffer (4194303 bytes)" not in correlate, "the raw header must not appear"


# --------------------------------------------------------------------------- #
# Through the CLI, including the exit code
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("label", "code", "finding"),
    [("healthy", 0, "all_layers_healthy"),
     ("broken", 1, "interface_line_down")],
)
def test_the_cli_carries_both_labels_to_the_right_exit_code(
    monkeypatch, capsys, label, code, finding
):
    monkeypatch.setattr(
        sys, "argv",
        ["nettools", "investigate", "RR1", "10.255.0.12",
         "--from-fixtures", "--label", label, "--format", "table"],
    )
    actual = cli.main()
    out = capsys.readouterr().out

    assert actual == code
    assert finding in out
    assert "bgp_session" in out and "interface" in out, "the whole ladder is rendered"


def test_the_cli_exit_codes_differ_between_the_labels(monkeypatch):
    """The discriminating assertion again, at the exit-code layer -- the only
    part of the output a cron job ever reads."""

    codes = []
    for label in ("healthy", "broken"):
        monkeypatch.setattr(
            sys, "argv",
            ["nettools", "investigate", "RR1", "10.255.0.12",
             "--from-fixtures", "--label", label, "--quiet"],
        )
        codes.append(cli.main())

    assert codes == [0, 1]


def test_nothing_in_this_file_needed_a_lab_or_a_key():
    """Stated as a test so it is checked rather than believed.

    Both autouse fixtures are already active. If either were removed, the
    earlier tests would begin passing for a different reason -- reaching a real
    lab, or reading a real `.env` -- and nothing would say so.
    """

    import os

    for name in ("DEVICE_USERNAME", "DEVICE_PASSWORD", "ANTHROPIC_API_KEY",
                 "OPENAI_API_KEY", "MINIMAX_API_KEY"):
        assert os.getenv(name) is None, f"{name} leaked into an offline test"

    with pytest.raises(AssertionError, match="attempted a real SSH connection"):
        sys.modules["netmiko"].ConnectHandler(host="1.2.3.4")
