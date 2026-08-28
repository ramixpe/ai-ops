from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from agent_nettools.template_parsers import IgnoreKind, IgnoreRule

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "tools" / "measure_parser_coverage.py"


def _load_auditor_module():
    spec = importlib.util.spec_from_file_location("measure_parser_coverage", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_auditor_counts_consumed_lines_by_occurrence_not_value():
    module = _load_auditor_module()
    auditor = module.Auditor()
    auditor.current_name = "test"
    auditor.current_files = 1
    ignored = IgnoreRule(r"^setting value$", "test deferred", IgnoreKind.NOT_NEEDED_YET)

    auditor.finalize(
        lambda **_kwargs: {},
        raw="setting value\nsetting value\n",
        consumed=["setting value"],
        ignores=[ignored],
    )

    counts = auditor.by_parser["test"]
    assert counts.structured_lines == 1
    assert counts.deferred_lines == 1
    assert counts.unaccounted_lines == 0


def test_finalize_discloses_occurrence_aware_model_safe_accounting():
    from agent_nettools.template_parsers import finalize

    parsed = finalize(
        raw="setting value\nsetting value\nunknown device text\n",
        consumed=["setting value"],
        ignores=[IgnoreRule(r"^setting value$", "test deferred", IgnoreKind.NOT_NEEDED_YET)],
        unparsed_rows=2,
    )

    assert parsed["meta"]["unaccounted_lines"] == ["unknown device text"]
    assert parsed["meta"]["evidence_accounting"] == {
        "source_nonblank_lines": 3,
        "structured_lines": 1,
        "deferred_lines": 1,
        "presentation_lines": 0,
        "unknown_lines": 1,
        "malformed_rows": 2,
    }