"""Tests for settings.py (B-476/P2-02): the declared env var table and its
report-only validation.

The source-scan test (``test_every_source_env_var_is_declared``) is the
load-bearing one -- see settings.py's module docstring. It rediscovers every
``os.getenv``-reachable string literal in ``src/`` and ``mcp_server/``
independently of ``SETTINGS`` and asserts the two sides match exactly, modulo
``settings.EXTERNAL_ONLY_KEYS`` (the three device-credential names that are
never a Python literal anywhere -- they come from ``inventory/lab.yaml`` at
runtime). A new env var added anywhere in the source tree, with no matching
entry in ``SETTINGS``, fails this test.
"""

from __future__ import annotations

import re
from pathlib import Path

from agent_nettools import settings

# --------------------------------------------------------------------------- #
# validate_environment(): malformed values, and the clean-environment case.
# --------------------------------------------------------------------------- #


def _clear_all_settings(monkeypatch):
    """Remove every declared setting from the environment.

    Ambient shell/CI environment is not under this test's control, so a
    "clean environment" test has to force it, not assume it -- there is no
    project-wide autouse fixture clearing env vars between tests.
    """

    for setting in settings.SETTINGS:
        monkeypatch.delenv(setting.name, raising=False)


def test_malformed_int_produces_exactly_one_problem_naming_variable_and_shape(monkeypatch):
    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "twice")

    problems = settings.validate_environment()

    assert len(problems) == 1
    assert "NETTOOLS_COMMAND_RETRIES" in problems[0]
    assert "twice" in problems[0]
    assert "integer" in problems[0]


def test_malformed_float_letter_o_typo_is_caught(monkeypatch):
    """The exact P2-02 example: a letter O instead of a zero."""

    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_READ_TIMEOUT_SECONDS", "1O")

    problems = settings.validate_environment()

    assert len(problems) == 1
    assert "NETTOOLS_READ_TIMEOUT_SECONDS" in problems[0]
    assert "1O" in problems[0]


def test_clean_environment_produces_zero_problems(monkeypatch):
    _clear_all_settings(monkeypatch)

    assert settings.validate_environment() == []


def test_unset_variables_are_never_a_problem(monkeypatch):
    """Unset means 'use the default' -- that is not malformed."""

    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_ACTOR", "")  # blank counts as unset too

    assert settings.validate_environment() == []


def test_bool_typo_is_flagged_even_though_the_code_would_accept_it(monkeypatch):
    """NETTOOLS_ALLOW_ACTIVE_PROBES='fasle' -- the consuming code treats any
    unrecognized value as truthy (see network_tools._active_probes_allowed),
    so this typo silently *enables* active probing instead of disabling it.
    That is exactly the P2-02 shape this validator exists to surface."""

    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_ALLOW_ACTIVE_PROBES", "fasle")

    problems = settings.validate_environment()

    assert len(problems) == 1
    assert "NETTOOLS_ALLOW_ACTIVE_PROBES" in problems[0]


def test_bool_typo_on_a_fail_closed_setting_says_the_typo_disables_it(monkeypatch):
    """B-493: NETTOOLS_MCP_ALLOW_ACTIVE_PROBES/NETTOOLS_ENABLE_AGENT declare
    unknown_bool_disables=True -- the opposite runtime behavior from
    NETTOOLS_ALLOW_ACTIVE_PROBES, so the message must say the opposite thing
    too. A message claiming "this typo turns it on" for a setting that
    actually stays off on a typo would be actively misleading."""

    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_MCP_ALLOW_ACTIVE_PROBES", "fasle")

    [problem] = settings.validate_environment()

    assert "NETTOOLS_MCP_ALLOW_ACTIVE_PROBES" in problem
    assert "disabled" in problem and "fails closed" in problem
    assert "turns the setting *on*" not in problem


def test_unknown_bool_disables_settings_actually_fail_closed(monkeypatch):
    """The declared table's claim is not decorative: for every setting that
    says unknown_bool_disables=True, the module that actually reads it must
    treat an unrecognized spelling as OFF. Pins the current instances against
    their real parsing functions, not just the declared flag.

    NETTOOLS_NETBOX_WRITE_ENABLED joined this set with the netbox.py
    collector (Stage-2 M3b) -- the same reasoning as the other two: a gate
    that exists to keep a real write off by default must not reopen on a
    typo."""

    from agent_nettools import cli, netbox
    from mcp_server import server as mcp_server_module

    unrecognized = "definitely-not-a-recognized-spelling"

    fail_closed_settings = {
        s.name for s in settings.SETTINGS if s.kind == "bool" and s.unknown_bool_disables
    }
    assert fail_closed_settings == {
        "NETTOOLS_MCP_ALLOW_ACTIVE_PROBES",
        "NETTOOLS_ENABLE_AGENT",
        "NETTOOLS_NETBOX_WRITE_ENABLED",
    }

    monkeypatch.setenv("NETTOOLS_MCP_ALLOW_ACTIVE_PROBES", unrecognized)
    assert mcp_server_module._mcp_active_probes_allowed() is False

    monkeypatch.setenv("NETTOOLS_ENABLE_AGENT", unrecognized)
    assert cli._agent_enabled() is False

    monkeypatch.setenv("NETTOOLS_NETBOX_WRITE_ENABLED", unrecognized)
    assert netbox.write_enabled() is False


def test_enum_typo_is_flagged(monkeypatch):
    """NETTOOLS_EVIDENCE_BACKEND='sqlit' -- get_store() silently falls back to
    the file backend for anything other than exactly 'sqlite'."""

    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_EVIDENCE_BACKEND", "sqlit")

    problems = settings.validate_environment()

    assert len(problems) == 1
    assert "NETTOOLS_EVIDENCE_BACKEND" in problems[0]
    assert "files" in problems[0] and "sqlite" in problems[0]


def test_valid_values_of_every_kind_produce_no_problems(monkeypatch):
    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "3")
    monkeypatch.setenv("NETTOOLS_READ_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("NETTOOLS_ALLOW_ACTIVE_PROBES", "0")
    monkeypatch.setenv("NETTOOLS_CREDENTIAL_PROVIDER", "FILE")  # case-insensitive
    monkeypatch.setenv("NETTOOLS_ACTOR", "someone")

    assert settings.validate_environment() == []


def test_out_of_range_numeric_value_is_flagged(monkeypatch):
    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_TELEGRAM_MAX_CHARS", "999999")  # over Telegram's 4096

    problems = settings.validate_environment()

    assert len(problems) == 1
    assert "NETTOOLS_TELEGRAM_MAX_CHARS" in problems[0]


# --------------------------------------------------------------------------- #
# effective_config(): secrets never leak, and value/source/problem are right.
# --------------------------------------------------------------------------- #


def test_secret_value_never_appears_in_effective_config(monkeypatch):
    _clear_all_settings(monkeypatch)
    fake_token = "FAKE-TELEGRAM-TOKEN-should-never-appear-9f3a2b"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", fake_token)

    config = settings.effective_config()

    assert config["TELEGRAM_BOT_TOKEN"]["value"] == "[SET]"
    assert config["TELEGRAM_BOT_TOKEN"]["source"] == "env"

    rendered = repr(config)
    assert fake_token not in rendered


def test_secret_unset_renders_as_unset_marker(monkeypatch):
    _clear_all_settings(monkeypatch)

    config = settings.effective_config()

    assert config["TELEGRAM_BOT_TOKEN"]["value"] == "[UNSET]"
    assert config["TELEGRAM_BOT_TOKEN"]["source"] == "default"
    assert config["ANTHROPIC_API_KEY"]["value"] == "[UNSET]"


def test_effective_config_reports_default_source_and_value_when_unset(monkeypatch):
    _clear_all_settings(monkeypatch)

    config = settings.effective_config()

    assert config["NETTOOLS_COMMAND_RETRIES"] == {
        "value": 2,
        "source": "default",
        "problem": None,
    }


def test_effective_config_reports_env_source_and_raw_value_when_set(monkeypatch):
    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "5")

    config = settings.effective_config()

    assert config["NETTOOLS_COMMAND_RETRIES"] == {
        "value": "5",
        "source": "env",
        "problem": None,
    }


def test_effective_config_surfaces_the_problem_alongside_the_bad_value(monkeypatch):
    _clear_all_settings(monkeypatch)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "twice")

    config = settings.effective_config()

    entry = config["NETTOOLS_COMMAND_RETRIES"]
    assert entry["value"] == "twice"
    assert entry["source"] == "env"
    assert entry["problem"] is not None
    assert "NETTOOLS_COMMAND_RETRIES" in entry["problem"]


def test_effective_config_covers_every_declared_setting(monkeypatch):
    _clear_all_settings(monkeypatch)

    config = settings.effective_config()

    assert set(config) == {setting.name for setting in settings.SETTINGS}


# --------------------------------------------------------------------------- #
# Source-scan cross-check. The load-bearing test.
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Direct, literal os.getenv("NAME", ...) / os.getenv("NAME") calls.
_RE_GETENV = re.compile(r'os\.getenv\(\s*["\']([A-Z][A-Z0-9_]*)["\']')

# This codebase names every constant/parameter/dict-entry that holds an env
# var *name* with an "_env"/"_ENV" suffix (e.g. ``TOKEN_ENV = "TELEGRAM_BOT_TOKEN"``,
# ``model_env: str = "OPENAI_MODEL"``) and later reads it via a variable
# rather than a literal, e.g. ``os.getenv(model_env, model_default)``. This
# picks up exactly those declarations, so a var read only through such an
# indirection is still discovered.
_RE_ENV_ASSIGN = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*_[Ee][Nn][Vv]\b\s*"
    r"(?::\s*[A-Za-z_][A-Za-z0-9_\[\] |]*)?\s*=\s*"
    r'["\']([A-Z][A-Z0-9_]*)["\']'
)
_RE_ENV_DICT = re.compile(
    r'["\'][A-Za-z_][A-Za-z0-9_]*_env["\']\s*:\s*["\']([A-Z][A-Z0-9_]*)["\']'
)


def _scan_source_for_env_var_names() -> set[str]:
    """Rediscover every env var name reachable from src/ + mcp_server/.

    Independent of SETTINGS by construction -- this walks the .py files
    directly rather than importing anything from settings.py, so it cannot
    be fooled by SETTINGS simply agreeing with itself.
    """

    found: set[str] = set()
    for directory in ("src", "mcp_server"):
        for path in (_REPO_ROOT / directory).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for pattern in (_RE_GETENV, _RE_ENV_ASSIGN, _RE_ENV_DICT):
                found.update(pattern.findall(text))
    return found


def test_every_source_env_var_is_declared():
    """A new env var added anywhere in src/ or mcp_server/ must be declared
    in settings.SETTINGS (or added to EXTERNAL_ONLY_KEYS with a reason), or
    this test fails. This is the guard against the table going stale."""

    discovered = _scan_source_for_env_var_names()
    declared = {setting.name for setting in settings.SETTINGS}

    undeclared = discovered - declared
    assert not undeclared, (
        f"Found env var(s) read in src/ or mcp_server/ that are not declared "
        f"in settings.SETTINGS: {sorted(undeclared)}. Add a Setting() entry "
        f"for each in src/agent_nettools/settings.py."
    )


def test_external_only_keys_are_declared_and_really_undiscoverable():
    """EXTERNAL_ONLY_KEYS is a documented, narrow exemption (device
    credential names configured in inventory/lab.yaml, not Python source) --
    not a place to quietly stash a var the scan simply failed to find. Pin
    both halves: every exempted name is declared, and none of them is
    actually discoverable by the scan (if one becomes discoverable, e.g. a
    future refactor hardcodes the name, it should be dropped from the
    exemption instead of staying here as dead cover)."""

    declared = {setting.name for setting in settings.SETTINGS}
    assert settings.EXTERNAL_ONLY_KEYS <= declared

    discovered = _scan_source_for_env_var_names()
    assert settings.EXTERNAL_ONLY_KEYS.isdisjoint(discovered)


def test_declared_settings_have_no_stale_entries():
    """The reverse direction: every declared, non-exempt setting must still
    be found by the scan -- otherwise SETTINGS documents a variable nothing
    reads any more."""

    discovered = _scan_source_for_env_var_names()
    declared = {setting.name for setting in settings.SETTINGS}

    stale = declared - discovered - settings.EXTERNAL_ONLY_KEYS
    assert not stale, f"SETTINGS declares var(s) no longer read anywhere: {sorted(stale)}"


def test_settings_table_has_no_duplicate_names():
    names = [setting.name for setting in settings.SETTINGS]
    assert len(names) == len(set(names))


def test_every_enum_setting_declares_choices():
    for setting in settings.SETTINGS:
        if setting.kind == "enum":
            assert setting.choices, f"{setting.name} is kind=enum but has no choices"


def test_every_secret_setting_is_marked_secret():
    """kind == 'secret' and the secret=True flag must always agree -- the
    redaction logic in effective_config() only checks the flag."""

    for setting in settings.SETTINGS:
        if setting.kind == "secret":
            assert setting.secret is True, f"{setting.name} is kind=secret but secret=False"


# --------------------------------------------------------------------------- #
# CLI: nettools config show / check.
# --------------------------------------------------------------------------- #


def test_cli_config_check_exits_zero_on_clean_environment(monkeypatch, capsys):
    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["nettools", "config", "check"])

    assert cli.main() == cli.EXIT_OK
    assert "OK" in capsys.readouterr().out


def test_cli_config_check_exits_one_on_a_problem(monkeypatch, capsys):
    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "twice")
    monkeypatch.setattr(sys, "argv", ["nettools", "config", "check"])

    assert cli.main() == cli.EXIT_WARNING
    assert "NETTOOLS_COMMAND_RETRIES" in capsys.readouterr().out


def test_cli_config_check_quiet_suppresses_output_but_keeps_exit_code(monkeypatch, capsys):
    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "twice")
    monkeypatch.setattr(sys, "argv", ["nettools", "config", "check", "--quiet"])

    assert cli.main() == cli.EXIT_WARNING
    assert capsys.readouterr().out == ""


def test_cli_config_show_prints_effective_config_as_json(monkeypatch, capsys):
    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["nettools", "config", "show"])

    assert cli.main() == cli.EXIT_OK
    out = capsys.readouterr().out
    assert '"NETTOOLS_COMMAND_RETRIES"' in out
    assert '"source": "default"' in out


def test_cli_config_show_never_prints_a_secret_value(monkeypatch, capsys):
    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    fake_key = "FAKE-ANTHROPIC-KEY-should-never-appear-7c1d"
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake_key)
    monkeypatch.setattr(sys, "argv", ["nettools", "config", "show"])

    cli.main()

    out = capsys.readouterr().out
    assert fake_key not in out
    assert '"[SET]"' in out


def test_main_prints_config_warning_to_stderr_for_a_malformed_value(monkeypatch, capsys):
    """main() itself validates the environment right after load_dotenv, on
    every subcommand -- not just `config check`."""

    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("NETTOOLS_COMMAND_RETRIES", "twice")
    monkeypatch.setattr(sys, "argv", ["nettools", "version"])

    assert cli.main() == cli.EXIT_OK  # never fails startup over it
    captured = capsys.readouterr()
    assert "# config warning:" in captured.err
    assert "NETTOOLS_COMMAND_RETRIES" in captured.err


def test_main_prints_no_config_warning_on_a_clean_environment(monkeypatch, capsys):
    import sys

    from agent_nettools import cli

    _clear_all_settings(monkeypatch)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["nettools", "version"])

    cli.main()

    assert "config warning" not in capsys.readouterr().err


def test_every_declared_setting_appears_in_env_example():
    """The sync the drift sweep found missing: settings.py declared 38 vars
    and .env.example carried 36. Without this, the template rots var by var
    -- and the template is the only place a new operator learns the surface.

    Secrets are included too: the template documents their NAMES (never
    values), which is exactly what an operator needs.
    """

    from pathlib import Path

    from agent_nettools.settings import SETTINGS

    root = Path(__file__).resolve().parent.parent
    template = (root / ".env.example").read_text()

    missing = [s.name for s in SETTINGS if s.name not in template]
    assert not missing, (
        f".env.example does not mention: {missing} — every declared setting "
        "must appear (commented is fine); the template is the operator's map"
    )
