"""Tests for `logs_loki.py`: the read-only Loki log adapter, Stage-2 M5.

No network anywhere -- every test drives `run_named_query` through its
`fetcher=` seam (the same injection idiom `network_tools`'s `sender=` uses),
never the real `_http_fetcher`. Structured around the four things the build
brief asked to be mutation-tested and reported:

1. A caller-supplied raw LogQL string is refused (there is no parameter that
   accepts one; a device value shaped like a LogQL fragment is refused by
   inventory lookup; even a `logql=` kwarg is refused as an unexpected
   parameter).
2. A log line crosses to a model only inside the projector's untrusted-text
   delimiters (and the trap this module's own docstring names -- an envelope
   that never sets `data["intent"]` cannot be protected at all -- is
   demonstrated directly, then shown closed on this module's real output).
3. Absence is distinguishable from failure (an empty-but-successful window
   vs. a failed query; `coverage_from_loki` refuses an absence claim over
   either, for different, honestly-stated reasons).
4. The envelope shape matches `network_tools._base_result`'s contract.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from agent_nettools import grounding, log_window, model_egress
from agent_nettools import logs_loki as loki
from agent_nettools.parsers import PARSE_FAILED, PARSE_OK
from mcp_server import boundary

# --------------------------------------------------------------------------- #
# Canned Loki responses
# --------------------------------------------------------------------------- #

_PE1_IP = "172.20.250.21"  # inventory/lab.yaml: PE1 -> mgmt_ip 172.20.250.21


def _stream(labels: dict, values: list[tuple[str, str]]) -> dict:
    return {"stream": labels, "values": [[ts, line] for ts, line in values]}


def _loki_ok(streams: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "streams", "result": streams}}


def _canned_fetcher(response: dict, *, calls: list | None = None):
    def fetcher(base_url: str, params: dict) -> dict:
        if calls is not None:
            calls.append((base_url, dict(params)))
        return response

    return fetcher


def _raising_fetcher(exc: Exception, *, calls: list | None = None):
    def fetcher(base_url: str, params: dict) -> dict:
        if calls is not None:
            calls.append((base_url, dict(params)))
        raise exc

    return fetcher


_PE1_LABELS = {
    "host": "PE1.sota-xrd",
    "job": "sota-routers",
    "severity": "err",
    "source_ip": _PE1_IP,
}

_LINE_A = (
    "PE1.sota-xrd RP/0/RP0/CPU0:Aug 18 12:29:15.443 UTC: ssh_syslog_proxy[1191]: "
    "%SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL : sshd[363565]: Read error from "
    "remote host 172.20.250.1 port 50086: Connection reset by peer "
)
_LINE_B = (
    "PE1.sota-xrd RP/0/RP0/CPU0:Aug 18 04:14:16.696 UTC: isis[1003]: "
    "%ROUTING-ISIS-3-FLEX_ALGO_DEF_CHANGED : ISIS (CORE): Flex-Algo 128, "
    "Level-2 definition changed, priority 128, source System-ID 0000.0000.0031 "
)


# --------------------------------------------------------------------------- #
# 1. The named-query table: exact match, structurally no raw-LogQL path
# --------------------------------------------------------------------------- #


def test_only_declared_queries_are_registered():
    assert loki.known_loki_queries() == ("logs_for_device", "logs_for_device_mnemonic")


def test_unknown_query_name_is_refused_and_the_fetcher_is_never_called():
    calls: list = []
    result = loki.run_named_query(
        "delete_everything", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    assert result["status"] == "error"
    assert "unknown loki query" in result["errors"][0]
    assert calls == []


def test_run_named_query_has_no_parameter_shaped_like_a_raw_query_string():
    """Structural pin: nothing in the public signature could ever carry a
    LogQL string -- `query_name` selects a table entry by exact name, and
    every other value arrives through `**params`, which is validated against
    the *selected* query's own declared slots (see the next test)."""

    sig = inspect.signature(loki.run_named_query)
    names = set(sig.parameters)
    assert names == {"query_name", "fetcher", "params"}


def test_a_logql_kwarg_is_refused_as_an_unexpected_parameter():
    """The concrete version of the structural pin above: even a caller who
    tries to smuggle a `logql=` value in gets refused before anything is
    built, because `logs_for_device` declares no such slot."""

    calls: list = []
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        logql='{source_ip=".*"}',
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    assert result["status"] == "error"
    assert "parameter mismatch" in result["errors"][0]
    assert "logql" in result["errors"][0]
    assert calls == []


def test_a_logql_shaped_device_value_is_refused_by_inventory_lookup_not_by_content():
    """MUTATION TEST (guard 1: raw LogQL is refused). A `device` value
    crafted to look like a LogQL injection attempt -- closing the stream
    selector's quote/brace and appending a pipeline stage -- is not
    special-cased or content-filtered; it is simply not a device name, so it
    fails the same inventory lookup any nonsense string would. The fetcher
    is never called, proving the value never reaches query construction --
    there is no `logql`/`data.parsed.records` anywhere in the result that
    could carry it, and it is not the built selector because none was built.

    The raw envelope's `errors[0]`/top-level `device` DO still echo the
    caller's invalid value verbatim -- see the two tests below for why that
    is the existing, accepted convention (`network_tools._safe_error` does
    exactly the same for an unknown device) rather than a new gap, and why
    the value is gone once `_classify_errors` rebuilds `errors` for a model."""

    malicious = '"} | line_format "{{__line__}}'
    calls: list = []
    result = loki.run_named_query(
        "logs_for_device", device=malicious, since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    assert result["status"] == "error"
    assert "is not in the lab inventory" in result["errors"][0]
    assert calls == []
    assert result["data"]["parsed"]["records"] == []
    assert "logql" not in result["data"]


def test_the_classified_error_drops_the_offending_value_even_though_the_raw_one_does_not():
    """The layer that actually matters for a model: `_classify_errors`
    (both copies -- `model_egress.py`'s and `mcp_server/boundary.py`'s)
    rebuilds every error from a command placeholder and a matched KIND
    phrase, discarding the caller-supplied detail entirely -- the same
    "the offending value never does [cross]" rule `model_egress.ERROR_KINDS`
    already documents for `templates.py`'s validation refusals. This is
    what actually closes the injection-shaped-device-value case for any
    model-facing consumer, not the raw envelope."""

    malicious = '"} | line_format "{{__line__}}'
    result = loki.run_named_query(
        "logs_for_device", device=malicious, since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )

    assert malicious in result["errors"][0]  # raw envelope: present (a receipt, like network_tools')

    projected = model_egress.project_envelope(result)
    assert malicious not in str(projected["errors"])
    assert "the device is not in the inventory" in projected["errors"][0]

    sanitized = boundary.sanitize(result)
    assert malicious not in str(sanitized["errors"])


def test_the_top_level_device_echo_on_a_refusal_matches_existing_network_tools_convention():
    """Not a new gap: `network_tools._run_approved_commands` does the exact
    same thing for an unknown device (`_safe_error("run_approved_commands",
    device_name, str(exc))` -- `device_name` is the caller's raw,
    unvalidated string, echoed at the top level regardless of whether it
    named a real device). The top-level `device` field is treated as a
    short identifier/receipt across this whole codebase, never as
    device-authored free text -- `FREE_TEXT_FIELDS` protects fields *inside*
    `data.parsed`, not envelope identity fields, and that line is drawn the
    same way here as everywhere else `_base_result`-shaped envelopes exist."""

    malicious = '"} | line_format "{{__line__}}'
    result = loki.run_named_query(
        "logs_for_device", device=malicious, since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    assert result["device"] == malicious


def test_unknown_device_name_is_refused():
    calls: list = []
    result = loki.run_named_query(
        "logs_for_device", device="NOT-A-REAL-DEVICE", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    assert result["status"] == "error"
    assert "is not in the lab inventory" in result["errors"][0]
    assert calls == []


@pytest.mark.parametrize("bad_device", [123, None, "", ["PE1"]])
def test_device_slot_type_and_emptiness_checks(bad_device):
    result = loki.run_named_query(
        "logs_for_device", device=bad_device, since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    assert result["status"] == "error"
    assert ("expected a string" in result["errors"][0]) or ("must not be empty" in result["errors"][0])


@pytest.mark.parametrize("bad_value", ["3600", 3.5, True, None])
def test_since_seconds_rejects_non_int_types(bad_value):
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=bad_value, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    assert result["status"] == "error"
    assert "expected an integer" in result["errors"][0]


@pytest.mark.parametrize("bad_value", [0, -1, 7 * 24 * 3600 + 1])
def test_since_seconds_out_of_bounds_is_refused(bad_value):
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=bad_value, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    assert result["status"] == "error"
    assert "must be between" in result["errors"][0]


@pytest.mark.parametrize("bad_value", [0, 1001])
def test_limit_out_of_bounds_is_refused(bad_value):
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=bad_value,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    assert result["status"] == "error"
    assert "must be between" in result["errors"][0]


def test_missing_parameter_is_refused():
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    assert result["status"] == "error"
    assert "parameter mismatch" in result["errors"][0]
    assert "limit" in result["errors"][0]


def test_mutation_a_malicious_build_selector_is_caught_by_the_post_build_shape_check(monkeypatch):
    """MUTATION TEST (defense in depth for guard 1). Simulates the only way
    a non-canonical string could ever reach selector-building -- a bug in
    `LOKI_QUERIES` itself, since every caller-supplied slot is validated
    first -- by monkeypatching `build_selector` to return an injection-shaped
    string, and proves the independent post-build shape check (mirroring
    `templates._validate_rendered_command`'s layer 4) refuses it before any
    HTTP call is made."""

    calls: list = []
    original = loki.LOKI_QUERIES["logs_for_device"]
    mutated = dataclasses.replace(
        original,
        build_selector=lambda resolved: '{source_ip="1.2.3.4"} | line_format "{{__line__}}"',
    )
    monkeypatch.setitem(loki.LOKI_QUERIES, "logs_for_device", mutated)

    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    assert result["status"] == "error"
    assert "post-build shape check" in result["errors"][0]
    assert calls == []


def test_selector_shape_regex_rejects_anything_but_the_exact_ipv4_selector():
    """Unit-tests the guard `test_mutation_a_malicious_build_selector...`
    relies on, directly."""

    assert loki._SELECTOR_SHAPE.fullmatch('{source_ip="172.20.250.21"}')
    for bad in (
        '{source_ip="172.20.250.21"} | line_format "x"',
        '{source_ip=".*"}',
        '{source_ip="172.20.250.21", extra="1"}',
        '{source_ip="172.20.250.21"',
        "'; DROP TABLE devices; --",
    ):
        assert not loki._SELECTOR_SHAPE.fullmatch(bad)


# --------------------------------------------------------------------------- #
# 2. Successful query: envelope shape, selector correctness, dedup, parsing
# --------------------------------------------------------------------------- #


def test_selector_is_built_from_the_devices_resolved_mgmt_ip():
    calls: list = []
    loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=3600, limit=50,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )
    assert len(calls) == 1
    _base_url, params = calls[0]
    assert params["query"] == f'{{source_ip="{_PE1_IP}"}}'
    assert params["limit"] == "50"
    assert params["direction"] == "backward"
    assert int(params["end"]) - int(params["start"]) == 3600 * 1_000_000_000


def test_declared_mnemonic_query_uses_a_closed_filter_across_the_requested_window():
    calls: list = []
    loki.run_named_query(
        "logs_for_device_mnemonic",
        device="PE1",
        mnemonic="ROUTING-BGP-5-ADJCHANGE",
        since_seconds=7 * 24 * 3600,
        limit=1000,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    [(_base_url, params)] = calls
    assert params["query"] == f'{{source_ip="{_PE1_IP}"}} |= "%ROUTING-BGP-5-ADJCHANGE :"'
    assert int(params["end"]) - int(params["start"]) == 7 * 24 * 3600 * 1_000_000_000


def test_undeclared_mnemonic_filter_is_refused_before_loki_is_called():
    calls: list = []
    result = loki.run_named_query(
        "logs_for_device_mnemonic",
        device="PE1",
        mnemonic='.*" | line_format "{{__line__}}',
        since_seconds=60,
        limit=10,
        fetcher=_canned_fetcher(_loki_ok([]), calls=calls),
    )

    assert result["status"] == "error"
    assert "declared mnemonic filter set" in result["errors"][0]
    assert calls == []


def test_envelope_matches_the_base_result_contract_on_success():
    response = _loki_ok([_stream(_PE1_LABELS, [("1787056163000000000", _LINE_A)])])
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(response),
    )

    assert set(result) == {"tool", "device", "status", "timestamp", "source", "data", "errors"}
    assert result["status"] == "success"
    assert result["device"] == "PE1"
    assert result["source"] == "loki"
    assert result["errors"] == []
    assert isinstance(result["timestamp"], str) and result["timestamp"]
    assert result["data"]["intent"] == "logs_for_device"
    assert result["data"]["parse_status"] == PARSE_OK
    assert "records" in result["data"]["parsed"]
    assert "meta" in result["data"]["parsed"]


def test_a_recognised_line_is_parsed_into_the_logging_template_shape():
    response = _loki_ok([_stream(_PE1_LABELS, [("1787056163000000000", _LINE_A)])])
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(response),
    )

    [record] = result["data"]["parsed"]["records"]
    assert record["mnemonic"] == "SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL"
    assert record["facility"] == "SECURITY-SSHD_SYSLOG_PRX"
    assert record["severity"] == "3"
    assert record["code"] == "ERR_GENERAL"
    assert record["process"] == "ssh_syslog_proxy"
    assert record["pid"] == "1191"
    assert record["timestamp"] == "Aug 18 12:29:15.443 UTC"
    assert "Read error from remote host" in record["text"]
    assert record["host"] == "PE1.sota-xrd"
    assert record["source_ip"] == _PE1_IP
    assert record["loki_severity_label"] == "err"
    assert record["ingest_timestamp_ns"] == "1787056163000000000"
    assert record["raw_event"] == _LINE_A


def test_an_unrecognised_line_is_kept_not_dropped_and_counted():
    response = _loki_ok([_stream(_PE1_LABELS, [("1", "not a syslog line at all")])])
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(response),
    )

    [record] = result["data"]["parsed"]["records"]
    assert record["mnemonic"] == ""
    assert record["text"] == "not a syslog line at all"
    assert result["data"]["parsed"]["meta"]["unparsed_lines"] == 1


def test_duplicates_are_removed_and_counted():
    """B-206b: the measured duplication problem. Three identical copies
    (same device timestamp, mnemonic, text) collapse to one, and the removed
    count is reported rather than hidden."""

    response = _loki_ok(
        [
            _stream(
                _PE1_LABELS,
                [
                    ("3", _LINE_A),
                    ("2", _LINE_A),
                    ("1", _LINE_A),
                    ("4", _LINE_B),
                ],
            )
        ]
    )
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(response),
    )

    meta = result["data"]["parsed"]["meta"]
    assert meta["records_before_dedup"] == 4
    assert meta["duplicates_removed"] == 2
    assert len(result["data"]["parsed"]["records"]) == 2
    assert meta["lines"] == "2"


def test_dedupe_reuses_log_windows_own_function():
    """Not a re-implementation -- `logs_loki` calls `log_window.dedupe`
    directly, the function that module's own docstring says is "load-bearing
    for B-206, dead-looking until then"."""

    records = [
        {"timestamp": "t", "mnemonic": "M", "text": "x"},
        {"timestamp": "t", "mnemonic": "M", "text": "x"},
    ]
    assert log_window.dedupe(records) == [records[0]]


# --------------------------------------------------------------------------- #
# 3. Absence vs. failure
# --------------------------------------------------------------------------- #


def test_an_empty_window_is_a_successful_query_with_zero_records():
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )

    assert result["status"] == "success"
    assert result["data"]["parsed"]["records"] == []
    assert result["data"]["parsed"]["meta"]["query_complete"] is True


def test_a_transport_failure_is_status_error_with_query_incomplete():
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_raising_fetcher(loki.LokiTransportError("loki request failed: Connection refused")),
    )

    assert result["status"] == "error"
    assert result["data"]["parse_status"] == PARSE_FAILED
    assert result["data"]["parsed"]["meta"]["query_complete"] is False
    assert result["data"]["parsed"]["records"] == []
    assert "loki request failed" in result["errors"][0]


def test_a_non_success_loki_body_is_a_transport_failure():
    def fetcher(base_url, params):
        return {"status": "error", "errorType": "bad_data", "error": "parse error"}

    # run_named_query calls the fetcher directly; a fetcher that wants to
    # simulate "loki answered but rejected the query" raises, matching what
    # _http_fetcher itself does for this exact case.
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_raising_fetcher(loki.LokiTransportError("loki query did not return a success status")),
    )
    assert result["status"] == "error"
    assert "loki query did not return a success status" in result["errors"][0]


def test_malformed_streams_shape_raises_transport_error_not_a_crash():
    with pytest.raises(loki.LokiTransportError):
        loki._extract_records({"status": "success", "data": {"result": "not-a-list"}})


def test_empty_window_absence_claim_is_still_refused_on_severity_grounds():
    """The `coverage.py` case this module is built to satisfy, using this
    module's own coverage builder rather than a hand-built Coverage."""

    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(_loki_ok([])),
    )
    coverage = loki.coverage_from_loki(result["data"]["parsed"], "PE1")

    assert coverage.query_complete is True
    assert coverage.records_returned == 0
    # B-696: 2 and 5 now ARE carried, so the gap set narrowed to 0,1,6,7.
    # The property under test is unchanged and is the point -- an empty
    # window still cannot support an absence claim, because the source
    # never carried every severity. A narrower gap set makes the refusal
    # *more* precise, not weaker.
    assert not coverage.complete  # severities 0,1,6,7 are not carried
    # Asserted against the exact rendered gap rather than substring-hunting
    # for digits: "2" appears in "severities 0,1,6,7" only by accident of
    # formatting, and a test that can be satisfied by coincidence is the
    # OBS-691 shape. This pins the whole sentence, so the carried severities
    # are excluded by construction rather than by a second loop.
    assert coverage.gaps() == ("severities 0,1,6,7 are not carried by this source",)

    refusal = {"correlation": {"found": False, "summary": "no correlating events in window"}}
    ground = grounding.ground_correlation(refusal, coverage)
    assert not ground.ok
    assert ground.failures[0].kind == "absence_claim_exceeds_coverage"


def test_failed_query_absence_claim_is_refused_for_a_different_reason():
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_raising_fetcher(loki.LokiTransportError("loki request failed: timed out")),
    )
    coverage = loki.coverage_from_loki(result["data"]["parsed"], "PE1")

    assert coverage.query_complete is False
    assert "the query did not complete" in coverage.gaps()

    refusal = {"correlation": {"found": False, "summary": "no correlating events in window"}}
    ground = grounding.ground_correlation(refusal, coverage)
    assert not ground.ok


def test_a_hit_limit_is_flagged_as_possible_truncation():
    response = _loki_ok(
        [_stream(_PE1_LABELS, [(str(i), _LINE_A.replace("363565", str(i))) for i in range(5)])]
    )
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=5,
        fetcher=_canned_fetcher(response),
    )
    coverage = loki.coverage_from_loki(result["data"]["parsed"], "PE1")

    assert any("requested limit" in note for note in coverage.gaps())


def test_a_partial_window_below_the_limit_is_not_flagged_as_truncated():
    response = _loki_ok([_stream(_PE1_LABELS, [("1", _LINE_A)])])
    result = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=1000,
        fetcher=_canned_fetcher(response),
    )
    coverage = loki.coverage_from_loki(result["data"]["parsed"], "PE1")

    assert not any("requested limit" in note for note in coverage.gaps())


# --------------------------------------------------------------------------- #
# 4. The projector: a log line crosses to a model only inside the delimiters
# --------------------------------------------------------------------------- #

_CANARY = "CANARY-LOKI-9f3e1c"


def _assert_wrapped_exactly_once(payload: str, canary: str) -> None:
    assert canary in payload, f"{canary} did not reach the payload at all"
    idx = payload.index(canary)
    open_idx = payload.rfind(model_egress.DEVICE_TEXT_OPEN, 0, idx)
    close_idx = payload.find(model_egress.DEVICE_TEXT_CLOSE, idx)
    assert open_idx != -1, f"{canary} has no preceding {model_egress.DEVICE_TEXT_OPEN!r}"
    assert close_idx != -1, f"{canary} has no following {model_egress.DEVICE_TEXT_CLOSE!r}"
    assert open_idx < idx < close_idx


def _envelope_with_canary_line() -> dict:
    line = _LINE_A.replace(
        "Read error from remote host 172.20.250.1 port 50086: Connection reset by peer",
        f"Read error -- {_CANARY}",
    )
    response = _loki_ok([_stream(_PE1_LABELS, [("1", line)])])
    return loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_canned_fetcher(response),
    )


def test_the_free_text_canary_is_wrapped_in_the_projectors_delimiters():
    envelope = _envelope_with_canary_line()
    projected = model_egress.project_envelope(envelope)

    payload = str(projected)
    _assert_wrapped_exactly_once(payload, _CANARY)


def test_raw_event_is_contained_before_loki_evidence_reaches_a_model():
    envelope = _envelope_with_canary_line()
    projected = model_egress.project_envelope(envelope)

    [record] = projected["data"]["parsed"]["records"]
    assert record["raw_event"].startswith(model_egress.DEVICE_TEXT_OPEN)


def test_structured_fields_survive_the_projection_untouched():
    """Non-vacuous companion (§0.12): the canary test above must not be
    passing because the projector stripped everything."""

    envelope = _envelope_with_canary_line()
    projected = model_egress.project_envelope(envelope)

    [record] = projected["data"]["parsed"]["records"]
    assert record["mnemonic"] == "SECURITY-SSHD_SYSLOG_PRX-3-ERR_GENERAL"
    assert record["host"] == "PE1.sota-xrd"
    assert record["source_ip"] == _PE1_IP


def test_mutation_an_envelope_that_never_sets_intent_cannot_be_protected():
    """MUTATION TEST (guard 2, the trap `_base_envelope`'s docstring names).
    Simulates forgetting to set `data["intent"]` -- the one thing every
    return path in this module actually does -- and shows the free-text
    canary passes through the projector COMPLETELY UNWRAPPED when that
    context is missing. This is not a defect in the projector: it is the
    documented boundary of `_envelope_context`'s lookup, and it is why
    `run_named_query` sets `data["intent"]` on every single return path
    (verified by the next test)."""

    envelope = _envelope_with_canary_line()
    del envelope["data"]["intent"]  # the mutation: forget the context

    projected = model_egress.project_envelope(envelope)
    payload = str(projected)

    assert _CANARY in payload
    assert model_egress.DEVICE_TEXT_OPEN not in payload  # NOT wrapped -- the trap, demonstrated


@pytest.mark.parametrize(
    "make_envelope",
    [
        lambda: loki.run_named_query(
            "logs_for_device", device="PE1", since_seconds=60, limit=10,
            fetcher=_canned_fetcher(_loki_ok([])),
        ),
        lambda: loki.run_named_query(
            "unknown_query", device="PE1", since_seconds=60, limit=10,
            fetcher=_canned_fetcher(_loki_ok([])),
        ),
        lambda: loki.run_named_query(
            "logs_for_device", device="NOPE", since_seconds=60, limit=10,
            fetcher=_canned_fetcher(_loki_ok([])),
        ),
        lambda: loki.run_named_query(
            "logs_for_device", device="PE1", since_seconds=60, limit=10,
            fetcher=_raising_fetcher(loki.LokiTransportError("boom")),
        ),
    ],
    ids=["success-empty", "unknown-query", "unknown-device", "transport-error"],
)
def test_every_return_path_sets_the_intent_context(make_envelope):
    """The guard against the trap above, pinned across every branch
    `run_named_query` can take -- success, an unknown query, a validation
    refusal, and a transport failure all set `data["intent"]`."""

    envelope = make_envelope()
    assert envelope["data"].get("intent") == envelope["data"].get("query_name")
    assert isinstance(envelope["data"]["intent"], str) and envelope["data"]["intent"]


def test_the_free_text_field_name_choice_only_adds_reviewed_raw_event_provenance():
    """`text`/`code` reuse existing boundary names; B-714's literal trigger
    is the one intentional new untrusted name and must stay explicit."""

    names_without_loki = frozenset(
        field
        for context, field in model_egress.FREE_TEXT_FIELDS
        if context != "logs_for_device"
    )
    names_with_loki = frozenset(field for _context, field in model_egress.FREE_TEXT_FIELDS)

    assert names_with_loki - names_without_loki == {"raw_event"}


def test_boundary_sanitize_also_wraps_the_same_field():
    """Rule 3's second half: the MCP boundary (a second, independent
    consumer of `FREE_TEXT_FIELDS`) agrees with the projector."""

    envelope = _envelope_with_canary_line()
    sanitized = boundary.sanitize(envelope)

    payload = str(sanitized)
    assert _CANARY in payload
    idx = payload.index(_CANARY)
    assert payload.rfind(model_egress.DEVICE_TEXT_OPEN, 0, idx) != -1
    assert payload.find(model_egress.DEVICE_TEXT_CLOSE, idx) != -1


def test_a_transport_error_message_classifies_through_the_mcp_boundary_too():
    envelope = loki.run_named_query(
        "logs_for_device", device="PE1", since_seconds=60, limit=10,
        fetcher=_raising_fetcher(loki.LokiTransportError("loki request failed: Connection refused")),
    )
    sanitized = boundary.sanitize(envelope)
    assert "unclassified" not in sanitized["errors"][0]
    assert "refused the connection" in sanitized["errors"][0]


# --------------------------------------------------------------------------- #
# Small supporting-function unit tests
# --------------------------------------------------------------------------- #


def test_to_ns_is_exact_at_todays_magnitude():
    import datetime as dt

    when = dt.datetime(2026, 8, 18, 12, 0, 0, 123456, tzinfo=dt.timezone.utc)
    ns = loki._to_ns(when)
    assert ns % 1_000_000_000 == 123_456_000
    assert ns == int(when.timestamp()) * 1_000_000_000 + 123_456_000


def test_measured_severity_available_matches_the_grounding_pin():
    """Keeps this module's declared constant in step with the fixed
    expectation `test_grounding.py`'s own Loki case already pins.

    Widened to `(2, 3, 4, 5)` on 2026-08-21 (B-696) after a fresh
    measurement, which is the only way the constant's own docstring permits
    it to move. **This test failing is the mechanism working, not a nuisance
    to silence**: the constant is a declared claim about a pipeline nothing
    can interrogate live, so the pin exists precisely to make a human notice
    when reality and the claim diverge. It did.

    Still deliberately not 6: `show running-config logging` on PE2 reads
    `severity notifications` (5), so severity 6 is not sent at all and
    claiming it would be claiming coverage the fabric is not configured to
    produce.
    """

    assert loki.MEASURED_SEVERITY_AVAILABLE == (2, 3, 4, 5)


def test_a_corrupted_inventory_address_is_refused_not_forwarded(monkeypatch):
    """The defence-in-depth reconstruction, with a test that discriminates.

    `_DeviceSlot.parse` re-parses `device.mgmt_ip` through
    `ipaddress.IPv4Address` "trusting neither the caller nor the inventory's own
    prior pydantic validation". The adversarial hunt (2026-08-19) deleted that
    reconstruction and **all 50 tests still passed** — every fixture device has
    a clean `mgmt_ip`, so the corpus never exercised the one case the guard
    exists for. A textbook vacuous guard: correct, and undemonstrated.

    This supplies the case the corpus lacks — an inventory record whose address
    has been corrupted downstream of pydantic — and asserts it is refused
    rather than interpolated into a selector.
    """

    import ipaddress

    from agent_nettools import logs_loki

    class _Corrupted:
        name = "PE2"
        mgmt_ip = '172.20.250.22"} | line_format "{{__line__}}'

    monkeypatch.setattr(logs_loki, "find_device", lambda name: _Corrupted())

    calls: list = []
    result = logs_loki.run_named_query(
        "logs_for_device", device="PE2", since_seconds=300, limit=10,
        fetcher=lambda *a, **k: calls.append(k) or {"status": "success", "data": {"result": []}},
    )

    assert result["status"] == "error", "a corrupted address must not reach a query"
    assert not calls, "the fetcher was reached with an unvalidated address"

    # Assert WHICH layer refused, not merely that something did. There are two
    # independent defences here -- this reconstruction, and the selector-shape
    # regex downstream -- and the hunt's mutation showed a test asserting only
    # "refused" cannot tell them apart, so it passes with the reconstruction
    # deleted. Naming the layer is what makes this test discriminating.
    assert "mgmt_ip" in result["errors"][0], (
        f"expected the inventory-address reconstruction to refuse this, got: "
        f"{result['errors'][0]!r} -- if the selector-shape check caught it "
        f"instead, the reconstruction is untested again"
    )
    with pytest.raises(ValueError):
        ipaddress.IPv4Address(_Corrupted.mgmt_ip)
