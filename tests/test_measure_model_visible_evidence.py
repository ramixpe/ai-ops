from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "tools" / "measure_model_visible_evidence.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("measure_model_visible_evidence", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_audit_measures_withholding_accounting_and_budget_without_model_call():
    result = _load_module().report("RR1", "broken", per_intent_chars=200)

    assert result["model_call_made"] is False
    assert result["totals"]["raw_command_chars"] > 0
    assert result["totals"]["raw_command_chars"] == result["totals"]["withheld_command_chars"]
    assert result["totals"]["budget_truncated_chars"] > 0
    for section in result["sections"].values():
        accounting = section["evidence_accounting"]
        if section["parse_status"] == "ok":
            assert accounting is not None
            assert accounting["deferred_lines"] == accounting["unknown_lines"] == 0
        else:
            assert accounting is None