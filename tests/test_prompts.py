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
    pressure, and the invented one is usually a confident guess. `undetermined`
    has to be an available answer, spelled out.
    """

    text = prompt.read_text(encoding="utf-8").lower()
    assert "undetermined" in text, (
        f"{prompt.name} does not name a refusal path; GRACE's C slot requires "
        f'"if the evidence does not support a conclusion, return undetermined"'
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


def test_the_library_is_still_a_scaffold_and_the_rule_tests_are_skipping():
    """T-026 ships the rules; T-027 and T-028 ship the prompts.

    Until then the two parametrized rule tests above have nothing to run
    against and pytest reports them as skipped -- which is honest, but silent.
    A rule that is skipping is a rule that is not enforced, and this build has
    already shipped one vacuously-passing guardrail (OBS-054) before catching
    it.

    So this test states the scaffold state out loud. **It fails the moment the
    first prompt lands**, which is the signal to confirm the parametrized tests
    are actually running rather than still skipping -- exactly how
    `test_registry_is_empty_until_the_parsers_land` handed over at T-012.
    """

    assert _prompt_files() == [], (
        "a prompt now exists -- check that the parametrized rule tests above "
        "are running rather than skipping, then update this test to assert the "
        "real expectation"
    )
