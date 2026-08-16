"""The prompt library's rules are enforced, not just documented (T-026).

`prompts/README.md` states four rules. A rule nobody checks is a preference,
so each one has a test here. These are cheap and they run in the same suite as
everything else, which is the point: a prompt is an input to a system whose
output someone acts on, and it deserves the same treatment as the allowlist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
CASES_DIR = PROMPTS_DIR / "tests" / "cases"

_VERSIONED = re.compile(r"^[a-z][a-z0-9_]*\.v\d+\.txt$")


def _prompt_files() -> list[Path]:
    return sorted(p for p in PROMPTS_DIR.glob("*.txt"))


def test_the_library_exists_with_its_readme_and_case_directory():
    assert PROMPTS_DIR.is_dir()
    assert (PROMPTS_DIR / "README.md").is_file()
    assert CASES_DIR.is_dir()


def test_every_prompt_file_is_versioned():
    """Rule 1. `report.txt` would be a prompt nobody can pin a report to."""

    for path in _prompt_files():
        assert _VERSIONED.match(path.name), (
            f"{path.name} is not versioned; prompts are `name.vN.txt` so a report "
            f"can name the version that produced it"
        )


def test_no_prompt_version_is_duplicated():
    """Two files claiming the same version would make "which one ran?"
    unanswerable after the fact."""

    seen = [p.name for p in _prompt_files()]
    assert len(seen) == len(set(seen))


@pytest.mark.parametrize("prompt", _prompt_files(), ids=lambda p: p.name)
def test_every_prompt_names_its_refusal_path(prompt):
    """GRACE's Constraints slot requires it explicitly.

    A prompt with no named refusal path leaves the model to invent one under
    pressure, and the invented one is usually a confident guess.

    The marker is read from the prompt's own case file rather than hardcoded,
    because **the refusals differ in kind**: a report that cannot determine a
    cause returns `undetermined`, while a correlation with nothing to correlate
    returns `found: false` and "no correlating events in window". The first
    version of this test assumed one vocabulary fitted both and failed on
    `correlate.v1.txt` -- the rule was right, the check was written from a
    single example.
    """

    import json

    stem = prompt.name.split(".")[0]
    case_file = CASES_DIR / f"{stem}.cases.json"
    assert case_file.is_file(), f"{prompt.name} has no case file declaring its refusal marker"
    declared = json.loads(case_file.read_text(encoding="utf-8"))
    # Keyed by *filename*, not by prompt family. A superseded version carries
    # the wording of its own era -- `correlate.v3` says "in the available
    # coverage" where v1 and v2 said "in window" -- and checking every version
    # against the current wording would fail the historical record for being
    # historical. Second time this rule has been too narrow; see OBS-065.
    markers = declared.get("refusal_markers") or {}
    marker = markers.get(prompt.name) or declared.get("refusal_marker")
    assert marker, (
        f"{case_file.name} declares no refusal marker for {prompt.name}; every "
        f"version needs one, including superseded ones"
    )

    text = " ".join(prompt.read_text(encoding="utf-8").split()).lower()
    assert marker.lower() in text, (
        f"{prompt.name} does not name its declared refusal path {marker!r}; GRACE's "
        f"C slot requires the model be told what to return when the evidence does "
        f"not support a conclusion"
    )


@pytest.mark.parametrize("prompt", _prompt_files(), ids=lambda p: p.name)
def test_every_prompt_has_at_least_one_golden_case(prompt):
    """Rule 3. A prompt with no case is untested text in a file."""

    stem = prompt.name.split(".")[0]
    cases = list(CASES_DIR.glob(f"{stem}.*"))
    assert cases, f"{prompt.name} has no golden case in {CASES_DIR}"


def test_no_prompt_asks_the_model_to_mark_its_own_work():
    """The deliberate omission of GRACE's Evaluation slot, enforced.

    `llm_analysis.py`'s existing TROUBLESHOOTING_PROMPT ends with "verify that
    every claim is supported by the provided data" -- a clause that reads as
    reassurance and provides none. Evaluation here is `grounding.py` and the
    schema validator, both of which run every time without anyone's attention.
    A prompt clause cannot have that property, and including one would imply
    the model's self-assessment is part of the guarantee.
    """

    banned = ("verify that every claim", "check your own", "evaluate your response")
    for path in _prompt_files():
        text = path.read_text(encoding="utf-8").lower()
        for phrase in banned:
            assert phrase not in text, (
                f"{path.name} contains {phrase!r} -- evaluation is code "
                f"(grounding.py, the schema validator), not a prompt clause"
            )


def test_the_readme_records_the_causal_chain_requirement():
    """The report prompt's product is the chain, not the finding.

    Pinned as a test rather than left in prose because it is a requirement on
    T-027, and a README requirement with no check is a suggestion.
    """

    readme = (PROMPTS_DIR / "README.md").read_text(encoding="utf-8")
    assert "CAUSAL CHAIN" in readme
    assert "observation" in readme and "interpretation" in readme


def test_the_rule_tests_are_actually_running_now_that_prompts_exist():
    """Was `test_the_library_is_still_a_scaffold...` (T-026).

    That test asserted the library was empty, so it failed the moment
    `report.v1.txt` landed -- which was the point. Its job was to make me check
    that the two parametrised rule tests above had switched from skipping to
    running, rather than silently continuing to skip. They had: pytest now
    reports them as `[report.v1.txt]`.

    Replaced with the real expectation, per §0.12: a guardrail that can pass by
    measuring nothing needs a companion that fails when the empty set ends.
    """

    prompts = _prompt_files()
    assert prompts, "the rule tests above are skipping again -- they enforce nothing"
    assert any(p.name.startswith("report.") for p in prompts)
