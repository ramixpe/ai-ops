"""The B-114 closure receipt is fixture-only and requires no model or lab."""

import importlib.util
from pathlib import Path

_PATH = Path(__file__).parents[1] / "scripts" / "b114_shadow_corpus.py"
_SPEC = importlib.util.spec_from_file_location("b114_shadow_corpus", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_b114_fixture_receipt_has_no_residual_unexplained_case():
    receipt = _MODULE.measure()

    assert receipt == {
        "measurement": "b114_shadow_residual_demand",
        "source": "fixture:isis-broken",
        "investigations": 1,
        "cause_not_localised": 1,
        "config_reconciled": 1,
        "residual_unexplained": 0,
        "narrowing_mode": "shadow_only",
    }