"""F2 (OBS-510..519/B-810..819): the one shared `_float_env`/`_int_env`.

Direct unit tests against `agent_nettools._env`, the module every one of the
six/four pre-existing copies (`notifier.py`, `netbox.py`, `admission.py`,
`network_tools.py`, `logs_loki.py`, `metrics_prometheus.py`) now delegates
to. `tests/test_admission.py`/`test_network_tools.py`/etc. cover that each
module still resolves its own env vars end to end; this file covers the
shared parsing/validation rules once, at the source.
"""

from __future__ import annotations

import math

from agent_nettools import _env as E

ENV_VAR = "NETTOOLS_TEST_ENV_VAR_DOES_NOT_EXIST_OTHERWISE"


# --------------------------------------------------------------------------- #
# _float_env: absence is never coerced to a number
# --------------------------------------------------------------------------- #


def test_float_env_unset_returns_the_default(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert E._float_env(ENV_VAR, 5.0) == 5.0


def test_float_env_blank_returns_the_default(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "   ")
    assert E._float_env(ENV_VAR, 5.0) == 5.0


def test_float_env_malformed_returns_the_default(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "1O")  # letter O, not a zero
    assert E._float_env(ENV_VAR, 5.0) == 5.0


def test_float_env_a_normal_value_is_accepted(monkeypatch):
    """Positive control for the inf/nan refusal tests below: a well-formed
    finite float reaches the caller unchanged through the same call path."""

    monkeypatch.setenv(ENV_VAR, "3.5")
    assert E._float_env(ENV_VAR, 5.0) == 3.5


# --------------------------------------------------------------------------- #
# F2 commit 2: inf/nan refused exactly like a malformed string
# --------------------------------------------------------------------------- #


def test_float_env_infinity_is_refused(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "inf")
    assert E._float_env(ENV_VAR, 5.0) == 5.0


def test_float_env_negative_infinity_is_refused(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "-inf")
    assert E._float_env(ENV_VAR, 5.0) == 5.0


def test_float_env_nan_is_refused(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "nan")
    assert E._float_env(ENV_VAR, 5.0) == 5.0


def test_float_env_zero_and_negative_still_pass_through(monkeypatch):
    """Not this commit's concern (see the module docstring's sign axis) --
    pinned here so a future change to this file notices if it starts
    silently narrowing this axis too."""

    monkeypatch.setenv(ENV_VAR, "-2.5")
    assert E._float_env(ENV_VAR, 5.0) == -2.5
    monkeypatch.setenv(ENV_VAR, "0")
    assert E._float_env(ENV_VAR, 5.0) == 0.0


# --------------------------------------------------------------------------- #
# _int_env
# --------------------------------------------------------------------------- #


def test_int_env_unset_returns_the_default(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert E._int_env(ENV_VAR, 7) == 7


def test_int_env_malformed_returns_the_default(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "not-a-number")
    assert E._int_env(ENV_VAR, 7) == 7


def test_int_env_a_normal_value_is_accepted(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "42")
    assert E._int_env(ENV_VAR, 7) == 42


def test_int_env_inf_shaped_text_is_malformed_not_a_number(monkeypatch):
    """`int("inf")` itself raises `ValueError` -- there is no separate
    inf/nan axis to guard for `_int_env` the way there is for `_float_env`."""

    monkeypatch.setenv(ENV_VAR, "inf")
    assert E._int_env(ENV_VAR, 7) == 7


def test_int_env_zero_and_negative_still_pass_through(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "-3")
    assert E._int_env(ENV_VAR, 7) == -3
    monkeypatch.setenv(ENV_VAR, "0")
    assert E._int_env(ENV_VAR, 7) == 0


# --------------------------------------------------------------------------- #
# math sanity: nan/-inf really are the values these tests exercise
# --------------------------------------------------------------------------- #


def test_the_refused_float_strings_really_do_parse_to_non_finite_values():
    assert math.isinf(float("inf"))
    assert math.isinf(float("-inf"))
    assert math.isnan(float("nan"))


# --------------------------------------------------------------------------- #
# EER-015: _BOOL_TRUE/_BOOL_FALSE -- one literal each, not four
# --------------------------------------------------------------------------- #


def test_bool_true_and_false_are_the_recognized_spellings():
    assert E._BOOL_TRUE == {"1", "true", "yes", "on"}
    assert E._BOOL_FALSE == {"0", "false", "no", "off"}
    assert E._BOOL_TRUE.isdisjoint(E._BOOL_FALSE)


def test_settings_and_mcp_server_share_one_bool_true_not_four_copies():
    """`settings.py` declared its own `_BOOL_TRUE`/`_BOOL_FALSE`, and
    `mcp_server/server.py` declared the truthy half twice more, once per
    MCP-only gate (`_MCP_ACTIVE_PROBES_TRUTHY`, `_MCP_EXTERNAL_SOURCES_
    TRUTHY`) -- four definitions of two literal tuples, collapsed here.

    Identity, not just equality: value equality would also pass if a future
    edit reintroduced a separate literal that still happened to spell the
    same four strings today, which is exactly the silent-drift shape this
    collapse exists to close off -- the same reasoning
    `tests/test_persist.py` uses for the EER-015 persistence helpers.
    """

    from agent_nettools import settings
    from mcp_server import server as mcp_server_module

    assert settings._BOOL_TRUE is E._BOOL_TRUE
    assert settings._BOOL_FALSE is E._BOOL_FALSE
    assert mcp_server_module._MCP_ACTIVE_PROBES_TRUTHY is E._BOOL_TRUE
    assert mcp_server_module._MCP_EXTERNAL_SOURCES_TRUTHY is E._BOOL_TRUE
