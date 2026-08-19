"""Tests for `metrics_prometheus.py`: the read-only Prometheus metrics
adapter, Stage-2 M5b -- the temporal evidence axis's second source.

No network anywhere -- every test drives `run_named_query` through its
`fetcher=` seam (the same injection idiom `logs_loki.run_named_query`'s
`fetcher=` and `network_tools`'s `sender=` use), never the real
`_http_fetcher`. Structured around the same four things the build brief for
`logs_loki.py` asked to be mutation-tested and reported, plus the one axis
that is genuinely new here:

1. A caller-supplied raw PromQL string is refused (there is no parameter
   that accepts one; a device/interface value shaped like a PromQL
   injection attempt is refused by validation, not by content filtering).
2. A metric label crosses to a model only via a fixed, named field extract
   -- this module's answer to "wrap in the projector's delimiters" is
   "never extract it at all"; see section 4 for the proof.
3. **Absence is not zero** -- the module's own reason to exist. All four
   cases: samples present; series known but window empty/partial; series
   never observed; query failed outright.
4. The envelope shape matches `network_tools._base_result`'s contract.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from agent_nettools import metrics_prometheus as prom
from agent_nettools import model_egress
from agent_nettools.parsers import PARSE_FAILED, PARSE_OK
from mcp_server import boundary

# --------------------------------------------------------------------------- #
# Canned Prometheus responses
# --------------------------------------------------------------------------- #

_INPUT_DROPS_METRIC = prom._COUNTER_METRICS["input_drops"]
_ISIS_METRIC = prom._ISIS_UPTIME_METRIC

_PE1_INTERFACE_LABELS = {
    "__name__": _INPUT_DROPS_METRIC,
    "host": "d964ff1e8737",
    "instance": "gnmic:7890",
    "interface_name": "GigabitEthernet0/0/0/0",
    "job": "xrd-telemetry",
    "source": "PE1",
    "subscription": "SUB-SOTA",
}


def _matrix_ok(series: list[dict]) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": series}}


def _series_ok(label_sets: list[dict]) -> dict:
    return {"status": "success", "data": label_sets}


def _dispatch_fetcher(by_path: dict, *, calls: list | None = None):
    """One fetcher, dispatching on `path` -- `by_path["/api/v1/query_range"]`
    and/or `by_path["/api/v1/series"]`, each a dict response or an Exception
    instance to raise."""

    def fetcher(base_url: str, path: str, params: dict) -> dict:
        if calls is not None:
            calls.append((base_url, path, dict(params)))
        entry = by_path.get(path)
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            raise AssertionError(f"fetcher was called for unexpected path {path!r}")
        return entry

    return fetcher


def _only_range(response: dict, *, calls: list | None = None):
    """A fetcher answering `/api/v1/query_range` with `response` and
    `/api/v1/series` with an empty (but successful) series list -- most
    tests below only care about the primary call's shape/result, and an
    empty matrix legitimately triggers the existence check (see section 3),
    so this default keeps that call harmless rather than unconfigured."""

    return _dispatch_fetcher(
        {"/api/v1/query_range": response, "/api/v1/series": _series_ok([])}, calls=calls
    )


# --------------------------------------------------------------------------- #
# 1. The named-query table: exact match, structurally no raw-PromQL path
# --------------------------------------------------------------------------- #


def test_exactly_four_queries_are_registered():
    """2 -> 4 (B-530 TSDB survey): ldp_session_history/device_uptime_history
    joined interface_rate_history/isis_adjacency_history. BGP metrics are
    deliberately still not here -- see the module docstring's "Why BGP is
    still not queried" section."""

    assert prom.known_prometheus_queries() == (
        "device_uptime_history",
        "interface_rate_history",
        "isis_adjacency_history",
        "ldp_session_history",
    )


def test_unknown_query_name_is_refused_and_the_fetcher_is_never_called():
    calls: list = []
    result = prom.run_named_query(
        "drop_table_devices",
        device="PE1", interface="GigabitEthernet0/0/0/0", counter="input_drops",
        since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "unknown prometheus query" in result["errors"][0]
    assert calls == []


def test_run_named_query_has_no_parameter_shaped_like_a_raw_query_string():
    """Structural pin: nothing in the public signature could ever carry a
    PromQL string -- `query_name` selects a table entry by exact name, and
    every other value arrives through `**params`, validated against the
    *selected* query's own declared slots."""

    sig = inspect.signature(prom.run_named_query)
    assert set(sig.parameters) == {"query_name", "fetcher", "params"}


def test_a_promql_kwarg_is_refused_as_an_unexpected_parameter():
    calls: list = []
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=300, step_seconds=15,
        promql='up{job=".*"}',
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "parameter mismatch" in result["errors"][0]
    assert "promql" in result["errors"][0]
    assert calls == []


def test_a_promql_shaped_device_value_is_refused_by_inventory_lookup_not_by_content():
    """MUTATION TEST (guard 1: raw PromQL is refused). A `device` value
    crafted to look like a selector-injection attempt is not content
    filtered -- it simply is not a device name, so it fails the same
    inventory lookup any nonsense string would. The fetcher is never
    called, proving the value never reaches query construction."""

    malicious = '"} or up{job="x'
    calls: list = []
    result = prom.run_named_query(
        "isis_adjacency_history", device=malicious, since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "is not in the lab inventory" in result["errors"][0]
    assert calls == []
    assert result["data"]["parsed"]["records"] == []
    assert "promql" not in result["data"]


def test_a_promql_shaped_interface_value_is_refused_by_regex_not_by_content():
    malicious = 'Gi0/0/0/0"} or up{job="x'
    calls: list = []
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface=malicious, counter="input_drops",
        since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "not a valid interface name" in result["errors"][0]
    assert calls == []


def test_an_unallowlisted_counter_is_refused():
    calls: list = []
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter='input_drops"} or up{job="x', since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "is not an allowlisted counter" in result["errors"][0]
    assert calls == []


def test_the_classified_error_drops_the_offending_value_even_though_the_raw_one_does_not():
    malicious = '"} or up{job="x'
    result = prom.run_named_query(
        "isis_adjacency_history", device=malicious, since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([])),
    )
    assert malicious in result["errors"][0]  # raw envelope: present (a receipt)

    projected = model_egress.project_envelope(result)
    assert malicious not in str(projected["errors"])
    assert "the device is not in the inventory" in projected["errors"][0]

    sanitized = boundary.sanitize(result)
    assert malicious not in str(sanitized["errors"])


def test_unknown_device_name_is_refused():
    calls: list = []
    result = prom.run_named_query(
        "isis_adjacency_history", device="NOT-A-REAL-DEVICE", since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "is not in the lab inventory" in result["errors"][0]
    assert calls == []


@pytest.mark.parametrize("bad_device", [123, None, "", ["PE1"]])
def test_device_slot_type_and_emptiness_checks(bad_device):
    result = prom.run_named_query(
        "isis_adjacency_history", device=bad_device, since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([])),
    )
    assert result["status"] == "error"
    assert ("expected a string" in result["errors"][0]) or ("must not be empty" in result["errors"][0])


@pytest.mark.parametrize("bad_value", ["300", 3.5, True, None])
def test_since_seconds_rejects_non_int_types(bad_value):
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=bad_value, step_seconds=15,
        fetcher=_only_range(_matrix_ok([])),
    )
    assert result["status"] == "error"
    assert "expected an integer" in result["errors"][0]


@pytest.mark.parametrize("bad_value", [0, 59, 7 * 24 * 3600 + 1])
def test_since_seconds_out_of_bounds_is_refused(bad_value):
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=bad_value, step_seconds=15,
        fetcher=_only_range(_matrix_ok([])),
    )
    assert result["status"] == "error"
    assert "must be between" in result["errors"][0]


@pytest.mark.parametrize("bad_value", [0, 14, 3601])
def test_step_seconds_out_of_bounds_is_refused(bad_value):
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=300, step_seconds=bad_value,
        fetcher=_only_range(_matrix_ok([])),
    )
    assert result["status"] == "error"
    assert "must be between" in result["errors"][0]


def test_missing_parameter_is_refused():
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([])),
    )
    assert result["status"] == "error"
    assert "parameter mismatch" in result["errors"][0]
    assert "counter" in result["errors"][0]


def test_a_sample_budget_that_would_be_exceeded_is_refused_before_any_http_call():
    calls: list = []
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=7 * 24 * 3600, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "would return approximately" in result["errors"][0]
    assert calls == []


def test_mutation_a_malicious_build_selector_is_caught_by_the_post_build_shape_check(monkeypatch):
    """MUTATION TEST (defense in depth for guard 1). Simulates the only way
    a non-canonical string could ever reach selector-building -- a bug in
    `PROMETHEUS_QUERIES` itself -- by monkeypatching `build_selector` to
    return an injection-shaped string, and proves the independent
    post-build shape check refuses it before any HTTP call is made."""

    calls: list = []
    original = prom.PROMETHEUS_QUERIES["isis_adjacency_history"]
    mutated = dataclasses.replace(
        original,
        build_selector=lambda resolved: f'{prom._ISIS_UPTIME_METRIC}{{source="1.2.3.4"}} or up',
    )
    monkeypatch.setitem(prom.PROMETHEUS_QUERIES, "isis_adjacency_history", mutated)

    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error"
    assert "post-build shape check" in result["errors"][0]
    assert calls == []


def test_interface_rate_selector_shape_rejects_anything_but_the_exact_form():
    good = (
        'rate(Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_'
        'latest_generic_counters_input_drops{source="PE1",interface_name='
        '"GigabitEthernet0/0/0/0"}[15s])'
    )
    assert prom._INTERFACE_RATE_SHAPE.fullmatch(good)
    for bad in (
        good + " or up",
        good.replace("[15s]", "[15s:5s]"),
        good.replace('"PE1"', '".*"'),
        "'; DROP TABLE devices; --",
    ):
        assert not prom._INTERFACE_RATE_SHAPE.fullmatch(bad)


def test_interface_bare_selector_shape_rejects_a_function_wrapped_expression():
    """The existence-check selector must never be the `rate(...)` form --
    `/api/v1/series` refuses it outright (measured live, 400 Bad Request)."""

    good = f'{_INPUT_DROPS_METRIC}{{source="PE1",interface_name="GigabitEthernet0/0/0/0"}}'
    assert prom._INTERFACE_BARE_SHAPE.fullmatch(good)
    for bad in (
        f"rate({good}[60s])",
        good + " or up",
        good.replace('"PE1"', '".*"'),
    ):
        assert not prom._INTERFACE_BARE_SHAPE.fullmatch(bad)


def test_isis_adjacency_selector_shape_rejects_anything_but_the_exact_form():
    good = f'{_ISIS_METRIC}{{source="PE1"}}'
    assert prom._ISIS_ADJACENCY_SHAPE.fullmatch(good)
    for bad in (good + " or up", good.replace('"PE1"', '".*"'), "up{job=\"x\"}"):
        assert not prom._ISIS_ADJACENCY_SHAPE.fullmatch(bad)


def test_a_corrupted_inventory_name_is_refused_not_forwarded(monkeypatch):
    """Defence-in-depth reconstruction, mirroring `logs_loki`'s own
    adversarial-hunt test for its corrupted-`mgmt_ip` case: supplies an
    inventory record whose `.name` has been corrupted downstream of
    pydantic, and asserts it is refused rather than interpolated into a
    selector. Names WHICH layer refused (the reconstruction, not the
    selector-shape regex) so the test cannot pass merely because some
    guard fired."""

    class _Corrupted:
        name = 'PE2"} or up{job="x'

    monkeypatch.setattr(prom, "find_device", lambda name: _Corrupted())

    calls: list = []
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE2", since_seconds=300, step_seconds=15,
        fetcher=_only_range(_matrix_ok([]), calls=calls),
    )
    assert result["status"] == "error", "a corrupted device name must not reach a query"
    assert calls == [], "the fetcher was reached with an unvalidated device name"
    assert "forbidden character" in result["errors"][0], (
        f"expected the reconstruction check to refuse this, got: {result['errors'][0]!r} "
        "-- if the selector-shape check caught it instead, the reconstruction is untested"
    )


# --------------------------------------------------------------------------- #
# 2. Successful query: envelope shape, selector correctness, shaping
# --------------------------------------------------------------------------- #


def test_interface_rate_selector_is_built_from_validated_slots():
    calls: list = []
    values = [[1000 + i * 15, "0"] for i in range(21)]
    response = _matrix_ok([{"metric": dict(_PE1_INTERFACE_LABELS), "values": values}])
    prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=_only_range(response, calls=calls),
    )
    # A non-empty result never triggers the existence check (section 3) --
    # exactly one HTTP call for the common case.
    assert len(calls) == 1
    base_url, path, params = calls[0]
    assert path == "/api/v1/query_range"
    assert params["query"] == (
        f'rate({_INPUT_DROPS_METRIC}{{source="PE1",interface_name="GigabitEthernet0/0/0/0"}}'
        f'[{prom._RATE_WINDOW_SECONDS}s])'
    )
    assert params["step"] == "15"
    assert int(params["end"]) - int(params["start"]) == 300


def test_envelope_matches_the_base_result_contract_on_success():
    values = [[1000 + i * 15, "3.5"] for i in range(21)]
    response = _matrix_ok([{"metric": dict(_PE1_INTERFACE_LABELS), "values": values}])
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=_only_range(response),
    )

    assert set(result) == {"tool", "device", "status", "timestamp", "source", "data", "errors"}
    assert result["status"] == "success"
    assert result["device"] == "PE1"
    assert result["source"] == "prometheus"
    assert result["errors"] == []
    assert isinstance(result["timestamp"], str) and result["timestamp"]
    assert result["data"]["intent"] == "interface_rate_history"
    assert result["data"]["parse_status"] == PARSE_OK
    assert "records" in result["data"]["parsed"]
    assert "meta" in result["data"]["parsed"]


def test_interface_rate_records_are_timestamp_value_pairs():
    values = [[1000, "0"], [1015, "0.5"], [1030, "1.25"]]
    response = _matrix_ok([{"metric": dict(_PE1_INTERFACE_LABELS), "values": values}])
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    records = result["data"]["parsed"]["records"]
    assert [r["rate_per_second"] for r in records] == [0, 0.5, 1.25]
    assert all("timestamp" in r for r in records)
    meta = result["data"]["parsed"]["meta"]
    assert meta["counter"] == "input_drops"
    assert meta["metric_name"] == _INPUT_DROPS_METRIC
    assert meta["interface"] == "GigabitEthernet0/0/0/0"


def test_isis_adjacency_records_carry_only_the_named_fields():
    values_up = [[1000, "60"], [1015, "75"], [1030, "90"]]
    series = {
        "metric": {
            "interface_name": "GigabitEthernet0/0/0/0",
            "system_id": "0000.0000.0001",
            "neighbor_state": "isis-adj-up-state",
            "neighbor_circuit_type": "isis-levels-2",
            "source": "PE1",
        },
        "values": values_up,
    }
    response = _matrix_ok([series])
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    [record] = result["data"]["parsed"]["records"]
    assert record["interface_name"] == "GigabitEthernet0/0/0/0"
    assert record["neighbor_system_id"] == "0000.0000.0001"
    assert record["neighbor_state"] == "isis-adj-up-state"
    assert record["neighbor_circuit_type"] == "isis-levels-2"
    assert [s["uptime_seconds"] for s in record["samples"]] == [60, 75, 90]
    assert record["reset_count"] == 0
    assert record["reset_timestamps"] == []


def test_isis_flap_is_detected_as_an_uptime_reset():
    """A `neighbor_uptime` value that DROPS between consecutive samples
    means the adjacency reset -- the "sudden drop" §2.4a asks history to
    surface, distinct from a rung verdict."""

    values = [[1000, "500"], [1015, "515"], [1030, "10"], [1045, "25"]]
    series = {"metric": {"interface_name": "Gi0/0/0/1"}, "values": values}
    response = _matrix_ok([series])
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    [record] = result["data"]["parsed"]["records"]
    assert record["reset_count"] == 1
    assert len(record["reset_timestamps"]) == 1


def test_isis_adjacency_count_meta_reflects_multiple_series():
    response = _matrix_ok(
        [
            {"metric": {"interface_name": "Gi0/0/0/0"}, "values": [[1000, "60"]]},
            {"metric": {"interface_name": "Gi0/0/0/1"}, "values": [[1000, "120"]]},
        ]
    )
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    meta = result["data"]["parsed"]["meta"]
    assert meta["adjacency_count"] == 2
    assert meta["records_returned"] == 2  # one sample each
    assert meta["records_available"] is None  # unknown series count ahead of the call


# --------------------------------------------------------------------------- #
# 2b. ldp_session_history / device_uptime_history (B-530 TSDB survey)
# --------------------------------------------------------------------------- #

_LDP_METRIC = prom._LDP_UPTIME_METRIC
_DEVICE_UPTIME_METRIC = prom._DEVICE_UPTIME_METRIC


def test_ldp_session_records_carry_only_the_named_fields_and_the_slash_spelled_interface():
    """`interface_name` must come from the SLASH-spelled label
    (`..._data_interface`, "GigabitEthernet0/0/0/0"), never the
    underscore-spelled sibling gNMI also exports for the same field
    (`..._data_interface_name`, "GigabitEthernet0_0_0_0") -- a fifth
    interface-spelling variant would be exactly what B-519's audit exists
    to catch. Both are present on the fixture on purpose, with different
    values, so picking the wrong one is not silently indistinguishable."""

    values_up = [[1000, "3128"], [1015, "3248"], [1030, "3368"]]
    series = {
        "metric": {
            "lsr_id": "10.255.0.1",
            "ldp_nbr_ipv4_adj_info_adjacency_group_link_hello_data_interface": "GigabitEthernet0/0/0/0",
            "ldp_nbr_ipv4_adj_info_adjacency_group_link_hello_data_interface_name": "GigabitEthernet0_0_0_0",
            "detailed_information_peer_state": "Estab",
            "source": "PE1",
        },
        "values": values_up,
    }
    response = _matrix_ok([series])
    result = prom.run_named_query(
        "ldp_session_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    [record] = result["data"]["parsed"]["records"]
    assert record["lsr_id"] == "10.255.0.1"
    assert record["interface_name"] == "GigabitEthernet0/0/0/0"
    assert record["peer_state"] == "Estab"
    assert [s["uptime_seconds"] for s in record["samples"]] == [3128, 3248, 3368]
    assert record["reset_count"] == 0


def test_ldp_session_flap_is_detected_as_an_uptime_reset():
    values = [[1000, "500"], [1015, "515"], [1030, "10"], [1045, "25"]]
    series = {"metric": {"lsr_id": "10.255.0.1"}, "values": values}
    response = _matrix_ok([series])
    result = prom.run_named_query(
        "ldp_session_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    [record] = result["data"]["parsed"]["records"]
    assert record["reset_count"] == 1
    assert len(record["reset_timestamps"]) == 1


def test_ldp_session_count_meta_reflects_multiple_series():
    response = _matrix_ok(
        [
            {"metric": {"lsr_id": "10.255.0.1"}, "values": [[1000, "60"]]},
            {"metric": {"lsr_id": "10.255.0.2"}, "values": [[1000, "120"]]},
        ]
    )
    result = prom.run_named_query(
        "ldp_session_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    meta = result["data"]["parsed"]["meta"]
    assert meta["session_count"] == 2
    assert meta["records_available"] is None  # unknown series count ahead of the call


def test_an_unnamed_hostile_label_never_reaches_the_ldp_shaped_record():
    """The LDP counterpart to the ISIS canary above -- and the specific,
    real, measured field this module's docstring says was considered and
    excluded: `capabilities_received_description`/`_sent_description`
    (e.g. "MP: Multi-Topology (MT)")."""

    hostile_metric = {
        "lsr_id": "10.255.0.1",
        "ldp_nbr_ipv4_adj_info_adjacency_group_link_hello_data_interface": "Gi0/0/0/0",
        "detailed_information_peer_state": "Estab",
        "detailed_information_capabilities_received_description": f"MP: Multi-Topology (MT) -- {_CANARY}",
        "some_future_yang_leaf": _CANARY,
    }
    response = _matrix_ok([{"metric": hostile_metric, "values": [[1000, "60"]]}])
    result = prom.run_named_query(
        "ldp_session_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    assert _CANARY not in str(result)

    projected = model_egress.project_envelope(result)
    assert _CANARY not in str(projected)
    sanitized = boundary.sanitize(result)
    assert _CANARY not in str(sanitized)

    # Non-vacuous companion: structured fields still survive.
    [record] = result["data"]["parsed"]["records"]
    assert record["lsr_id"] == "10.255.0.1"
    assert record["peer_state"] == "Estab"


def test_ldp_session_selector_shape_rejects_anything_but_the_exact_form():
    query = prom.PROMETHEUS_QUERIES["ldp_session_history"]
    assert query.selector_shape.fullmatch(f'{_LDP_METRIC}{{source="PE1"}}')
    assert not query.selector_shape.fullmatch(f'{_LDP_METRIC}{{source="PE1", lsr_id="10.255.0.1"}}')
    assert not query.selector_shape.fullmatch(f'{_LDP_METRIC}[60s]{{source="PE1"}}')


def test_device_uptime_history_returns_a_flat_single_series_sample_list():
    """Unlike isis/ldp's per-adjacency records, this is `interface_rate_
    history`'s single-series shape: one device, one series, records are the
    flat sample list directly."""

    values = [[1000 + i * 60, str(1_334_000 + i * 60)] for i in range(5)]
    response = _matrix_ok([{"metric": {"source": "PE1"}, "values": values}])
    result = prom.run_named_query(
        "device_uptime_history", device="PE1", since_seconds=240, step_seconds=60,
        fetcher=_only_range(response),
    )
    records = result["data"]["parsed"]["records"]
    assert [r["uptime_seconds"] for r in records] == [
        1_334_000 + i * 60 for i in range(5)
    ]
    meta = result["data"]["parsed"]["meta"]
    assert meta["records_available"] == 5  # exact-match, computed like interface_rate_history
    assert meta["reboot_count"] == 0


def test_device_uptime_history_detects_a_reboot():
    values = [[1000, "500000"], [1060, "500060"], [1120, "30"], [1180, "90"]]
    response = _matrix_ok([{"metric": {"source": "PE1"}, "values": values}])
    result = prom.run_named_query(
        "device_uptime_history", device="PE1", since_seconds=180, step_seconds=60,
        fetcher=_only_range(response),
    )
    meta = result["data"]["parsed"]["meta"]
    assert meta["reboot_count"] == 1
    assert len(meta["reboot_timestamps"]) == 1


def test_device_uptime_history_selector_shape_rejects_anything_but_the_exact_form():
    query = prom.PROMETHEUS_QUERIES["device_uptime_history"]
    assert query.selector_shape.fullmatch(f'{_DEVICE_UPTIME_METRIC}{{source="PE1"}}')
    assert not query.selector_shape.fullmatch(f'{_DEVICE_UPTIME_METRIC}{{source="PE1; reload"}}')


# --------------------------------------------------------------------------- #
# 3. Absence vs. failure -- the four cases the build brief asks to be tested
# --------------------------------------------------------------------------- #


def test_case_1_samples_present_no_gap_coverage_is_complete():
    values = [[1000 + i * 15, "0"] for i in range(21)]  # exactly the expected count
    response = _matrix_ok([{"metric": dict(_PE1_INTERFACE_LABELS), "values": values}])
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=_only_range(response),
    )
    meta = result["data"]["parsed"]["meta"]
    assert meta["records_returned"] == 21 == meta["records_available"]
    assert meta["series_known"] is None  # never checked -- records were already present

    coverage = prom.coverage_from_prometheus_history(result["data"]["parsed"], "PE1")
    assert coverage.query_complete is True
    assert coverage.complete is True
    assert coverage.gaps() == ()


def test_case_2_series_known_but_window_empty_is_a_real_gap_not_a_zero():
    """The load-bearing case: zero points, but the existence check (a
    SEPARATE HTTP call, over a wider lookback) finds the series has been
    observed before. Absence here means "stopped being scraped", and
    Coverage must say so -- not report a clean zero."""

    calls: list = []
    fetcher = _dispatch_fetcher(
        {
            "/api/v1/query_range": _matrix_ok([]),
            "/api/v1/series": _series_ok([dict(_PE1_INTERFACE_LABELS)]),
        },
        calls=calls,
    )
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=fetcher,
    )
    assert result["status"] == "success"  # the primary read succeeded; it just found nothing
    meta = result["data"]["parsed"]["meta"]
    assert meta["records_returned"] == 0
    assert meta["records_available"] == 21
    assert meta["series_known"] is True

    # The existence check is a second call, made only because the first was empty.
    paths = [path for _base, path, _params in calls]
    assert paths == ["/api/v1/query_range", "/api/v1/series"]

    # The existence check's match[] is the BARE selector -- no rate(...)
    # wrapper. /api/v1/series rejects a function-wrapped expression outright
    # (measured live 2026-08-19, 400 Bad Request); this is the regression
    # pin for that fix.
    _base_url, _path, series_params = calls[1]
    assert series_params["match[]"] == (
        f'{_INPUT_DROPS_METRIC}{{source="PE1",interface_name="GigabitEthernet0/0/0/0"}}'
    )
    assert "rate(" not in series_params["match[]"]

    coverage = prom.coverage_from_prometheus_history(result["data"]["parsed"], "PE1")
    assert coverage.query_complete is True
    assert coverage.complete is False
    gaps = " ".join(coverage.gaps())
    assert "collection gap, not a measured zero" in gaps
    assert "absence of a sample is never a value of zero" in gaps


def test_case_3_series_never_observed_is_distinct_from_a_collection_gap():
    """Zero points AND the existence check (over the wide lookback) finds
    NOTHING either -- a different, and differently-worded, gap: likely a
    wrong device/interface/metric combination, not a scrape interruption."""

    fetcher = _dispatch_fetcher(
        {
            "/api/v1/query_range": _matrix_ok([]),
            "/api/v1/series": _series_ok([]),
        }
    )
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=fetcher,
    )
    meta = result["data"]["parsed"]["meta"]
    assert meta["series_known"] is False

    coverage = prom.coverage_from_prometheus_history(result["data"]["parsed"], "PE1")
    assert coverage.complete is False
    gaps = " ".join(coverage.gaps())
    assert "has been observed" in gaps and "not a scrape gap" in gaps
    # The wording must differ from case 2's -- a reader must not conflate
    # "never existed" with "existed, then stopped".
    assert "not a measured zero" not in gaps


def test_case_4_a_transport_failure_on_the_primary_call_is_status_error():
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=300, step_seconds=15,
        fetcher=_dispatch_fetcher(
            {"/api/v1/query_range": prom.PrometheusTransportError("prometheus request failed: Connection refused")}
        ),
    )
    assert result["status"] == "error"
    assert result["data"]["parse_status"] == PARSE_FAILED
    assert result["data"]["parsed"]["meta"]["query_complete"] is False
    assert result["data"]["parsed"]["records"] == []
    assert "prometheus request failed" in result["errors"][0]

    coverage = prom.coverage_from_prometheus_history(result["data"]["parsed"], "PE1")
    assert coverage.query_complete is False
    assert "the query did not complete" in coverage.gaps()


def test_a_non_success_prometheus_body_is_a_transport_failure():
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=300, step_seconds=15,
        fetcher=_dispatch_fetcher(
            {"/api/v1/query_range": prom.PrometheusTransportError("prometheus query did not return a success status")}
        ),
    )
    assert result["status"] == "error"
    assert "prometheus query did not return a success status" in result["errors"][0]


def test_malformed_matrix_shape_raises_transport_error_not_a_crash():
    with pytest.raises(prom.PrometheusTransportError):
        prom._extract_matrix({"status": "success", "data": {"result": "not-a-list"}})


def test_malformed_series_shape_raises_transport_error_not_a_crash():
    with pytest.raises(prom.PrometheusTransportError):
        prom._extract_series_list({"status": "success", "data": {"not": "a-list"}})


def test_case_5_a_partial_window_is_still_a_gap_even_though_some_samples_returned():
    """A real, common case: 12 of an expected 21 samples returned -- a
    mid-window scrape interruption, distinct from total absence (case 2)
    but caught by the identical mechanism (records_returned <
    records_available)."""

    values = [[1000 + i * 15, "0"] for i in range(12)]
    response = _matrix_ok([{"metric": dict(_PE1_INTERFACE_LABELS), "values": values}])
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=_only_range(response),
    )
    meta = result["data"]["parsed"]["meta"]
    assert meta["records_returned"] == 12
    assert meta["records_available"] == 21
    assert meta["series_known"] is None  # non-empty result -> existence check never needed

    coverage = prom.coverage_from_prometheus_history(result["data"]["parsed"], "PE1")
    assert coverage.truncated is True
    assert coverage.complete is False


def test_case_6_the_existence_check_itself_failing_never_flips_a_successful_primary_read():
    """The primary read succeeded (Prometheus answered; it just found
    nothing). If the AUXILIARY existence check then fails, the envelope
    must still read `status="success"` -- the caller has a real answer
    about the requested window -- and `series_known` becomes `None`
    (unknown), never a fabricated True/False."""

    fetcher = _dispatch_fetcher(
        {
            "/api/v1/query_range": _matrix_ok([]),
            "/api/v1/series": prom.PrometheusTransportError("prometheus returned http status 503"),
        }
    )
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=300, step_seconds=15,
        fetcher=fetcher,
    )
    assert result["status"] == "success"
    meta = result["data"]["parsed"]["meta"]
    assert meta["series_known"] is None
    assert "existence check itself failed" in meta["series_existence_check_note"]

    coverage = prom.coverage_from_prometheus_history(result["data"]["parsed"], "PE1")
    assert "existence check itself failed" in " ".join(coverage.gaps())


# --------------------------------------------------------------------------- #
# 4. No free text is ever extracted -- the structural answer to the
#    projector requirement
# --------------------------------------------------------------------------- #

_CANARY = "CANARY-PROM-9f3e1c"


def test_an_unnamed_hostile_label_never_reaches_the_isis_shaped_record():
    """The canary: a label under a key NEITHER shaper reads (mirroring the
    real, measured `peer_reset_reason`/`reset_reason` free-text leaves on
    the BGP series this module does not query) must not appear anywhere in
    the output -- not wrapped, not present at all, because it was never
    extracted from the raw label dict in the first place."""

    hostile_metric = {
        "interface_name": "Gi0/0/0/0",
        "system_id": "0000.0000.0001",
        "neighbor_state": "isis-adj-up-state",
        "neighbor_circuit_type": "isis-levels-2",
        "peer_reset_reason": f"administratively cleared -- {_CANARY}",
        "some_future_yang_leaf": _CANARY,
    }
    response = _matrix_ok([{"metric": hostile_metric, "values": [[1000, "60"]]}])
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    assert _CANARY not in str(result)

    projected = model_egress.project_envelope(result)
    assert _CANARY not in str(projected)
    sanitized = boundary.sanitize(result)
    assert _CANARY not in str(sanitized)


def test_structured_fields_survive_alongside_the_excluded_canary():
    """Non-vacuous companion: the canary test above must not be passing
    because record-shaping stripped everything."""

    hostile_metric = {
        "interface_name": "Gi0/0/0/0",
        "system_id": "0000.0000.0001",
        "neighbor_state": "isis-adj-up-state",
        "peer_reset_reason": _CANARY,
    }
    response = _matrix_ok([{"metric": hostile_metric, "values": [[1000, "60"]]}])
    result = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    [record] = result["data"]["parsed"]["records"]
    assert record["interface_name"] == "Gi0/0/0/0"
    assert record["neighbor_system_id"] == "0000.0000.0001"
    assert record["neighbor_state"] == "isis-adj-up-state"


def test_the_interface_rate_shaper_never_even_reads_the_metric_label_dict():
    """Stronger than "extracts only named fields": `interface_rate_history`
    doesn't consult the label dict at all -- values only. A hostile
    `metric` dict, even one with no recognisable keys whatsoever, changes
    nothing about the output."""

    values = [[1000, "1.0"]]
    hostile = {"metric": {"anything_at_all": _CANARY, "another": "x" * 5000}, "values": values}
    response = _matrix_ok([hostile])
    result = prom.run_named_query(
        "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
        counter="input_drops", since_seconds=60, step_seconds=15,
        fetcher=_only_range(response),
    )
    assert _CANARY not in str(result)
    [record] = result["data"]["parsed"]["records"]
    assert set(record) == {"timestamp", "rate_per_second"}


def test_every_return_path_sets_the_intent_context():
    """The same trap `logs_loki._base_envelope`'s docstring names: an
    envelope that never sets `data["intent"]` cannot be matched by
    `model_egress._envelope_context` at all. Pinned across every branch."""

    make_envelopes = [
        lambda: prom.run_named_query(
            "interface_rate_history", device="PE1", interface="GigabitEthernet0/0/0/0",
            counter="input_drops", since_seconds=60, step_seconds=15,
            fetcher=_only_range(_matrix_ok([])),
        ),
        lambda: prom.run_named_query(
            "bogus_query", device="PE1", since_seconds=60, step_seconds=15,
            fetcher=_only_range(_matrix_ok([])),
        ),
        lambda: prom.run_named_query(
            "isis_adjacency_history", device="NOPE", since_seconds=60, step_seconds=15,
            fetcher=_only_range(_matrix_ok([])),
        ),
        lambda: prom.run_named_query(
            "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
            fetcher=_dispatch_fetcher({"/api/v1/query_range": prom.PrometheusTransportError("boom")}),
        ),
    ]
    for make in make_envelopes:
        envelope = make()
        assert envelope["data"].get("intent") == envelope["data"].get("query_name")
        assert isinstance(envelope["data"]["intent"], str) and envelope["data"]["intent"]


def test_a_transport_error_message_classifies_through_both_egress_surfaces():
    envelope = prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=60, step_seconds=15,
        fetcher=_dispatch_fetcher(
            {"/api/v1/query_range": prom.PrometheusTransportError("prometheus returned http status 503")}
        ),
    )
    projected = model_egress.project_envelope(envelope)
    assert "unclassified" not in projected["errors"][0]
    assert "non-success HTTP status" in projected["errors"][0]

    sanitized = boundary.sanitize(envelope)
    assert "unclassified" not in sanitized["errors"][0]
    assert "non-success HTTP status" in sanitized["errors"][0]


def test_error_kinds_stay_byte_identical_between_the_two_egress_copies():
    """Rule 3's second half, and the guard `tests/test_model_egress.py`
    already pins globally -- restated locally so a change to just this
    module's block is caught by a test that names the module."""

    from mcp_server.boundary import ERROR_KINDS as boundary_error_kinds

    assert model_egress.ERROR_KINDS == boundary_error_kinds


# --------------------------------------------------------------------------- #
# Small supporting-function unit tests
# --------------------------------------------------------------------------- #


def test_counter_metric_table_covers_bytes_packets_and_error_drop_families():
    names = set(prom._COUNTER_METRICS)
    assert {"bytes_received", "bytes_sent", "packets_received", "packets_sent"} <= names
    assert {"input_errors", "output_errors", "input_drops", "output_drops"} <= names
    for metric in prom._COUNTER_METRICS.values():
        assert metric.startswith(
            "Cisco_IOS_XR_infra_statsd_oper:infra_statistics_interfaces_interface_"
            "latest_generic_counters_"
        )


def test_existence_lookback_defaults_to_measured_prometheus_retention():
    assert prom.DEFAULT_EXISTENCE_LOOKBACK_SECONDS == 7 * 24 * 3600


def test_existence_check_is_a_second_http_call_with_a_wider_window(monkeypatch):
    monkeypatch.delenv(prom.EXISTENCE_LOOKBACK_ENV, raising=False)
    calls: list = []
    fetcher = _dispatch_fetcher(
        {
            "/api/v1/query_range": _matrix_ok([]),
            "/api/v1/series": _series_ok([]),
        },
        calls=calls,
    )
    prom.run_named_query(
        "isis_adjacency_history", device="PE1", since_seconds=300, step_seconds=15,
        fetcher=fetcher,
    )
    _base_url, _path, series_params = calls[1]
    lookback = int(series_params["end"]) - int(series_params["start"])
    assert lookback == prom.DEFAULT_EXISTENCE_LOOKBACK_SECONDS
    assert series_params["match[]"] == f'{_ISIS_METRIC}{{source="PE1"}}'


def test_an_abbreviated_interface_name_finds_the_same_series_as_the_long_form():
    """`check_lab_interfaces` hands a model `Gi0/0/0/0`; gNMI stores
    `GigabitEthernet0/0/0/0`. Both must reach the same series.

    Measured 2026-08-19 over 68 consecutive live calls: a model listed a
    device's interfaces and asked for each one's history, and every single call
    returned zero samples with `series_known=False` -- whose note says "no
    series matching this device/interface/metric selector has been observed".
    The series existed (56 of them). The name did not match. The tool was
    confidently wrong in a way the caller had no means to doubt, which is worse
    than an error (OBS-202).
    """

    captured: list[str] = []

    def fetcher(base, path, params):
        captured.append(params.get("query") or params.get("match[]") or "")
        return {"status": "success", "data": {"resultType": "matrix", "result": []}}

    for name in ("Gi0/0/0/0", "GigabitEthernet0/0/0/0"):
        captured.clear()
        prom.run_named_query(
            "interface_rate_history", fetcher=fetcher, device="PE1",
            interface=name, counter="input_errors",
            since_seconds=300, step_seconds=60,
        )
        assert captured, "no query was built"
        assert 'interface_name="GigabitEthernet0/0/0/0"' in captured[0], (
            f"{name!r} did not expand to the stored spelling: {captured[0]}"
        )


def test_the_expansion_does_not_accept_an_invented_interface():
    """Anti-vacuity companion. Expanding an abbreviation must not become
    'accept anything and hope' -- a name that is not a valid interface is still
    refused, and a valid-but-unknown one still reaches the selector verbatim
    rather than being mapped onto something that exists.
    """

    def fetcher(base, path, params):
        return {"status": "success", "data": {"resultType": "matrix", "result": []}}

    bad = prom.run_named_query(
        "interface_rate_history", fetcher=fetcher, device="PE1",
        interface='Gi0/0/0/0" or up{a="', counter="input_errors",
        since_seconds=300, step_seconds=60,
    )
    assert bad["status"] == "error"
