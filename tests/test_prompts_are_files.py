"""Every prompt sent to a model lives in `prompts/`, not in a Python literal.

The operator's requirement, in their words: *"I want a folder that contains all
prompts, instead of the script having them, as I want always to review them."*

Moving the three stragglers (`TROUBLESHOOTING_PROMPT`, `FABRIC_ANALYSIS_PROMPT`,
`AGENT_SYSTEM_PROMPT`) into `prompts/` on 2026-08-21 satisfied that once. This
file is what keeps it satisfied, because the next prompt will be added in a
hurry by someone who does not know the rule, and a rule with no check is a
preference.

**It is also more than a filing convention, which is the part worth stating.**
`prompts/` is a policy boundary, not a directory: `tests/test_prompts.py`
applies GRACE's rules to every file in it -- each prompt must name its refusal
path, must carry at least one golden case, must not ask the model to mark its
own work. All three migrated prompts *failed those rules on arrival*, not
because the prompts were bad but because living in Python had exempted them
from ever being checked. A prompt in a string literal is not merely harder to
review; it is outside the review system entirely.

Derived by scanning the source, following the precedent of
`tests/test_docs.py` (which reads the MCP registry rather than a frozen list)
and `tests/test_settings.py` (which scans for `os.getenv` rather than trusting
a table). A hardcoded list of known prompts would go stale the first time
someone added one -- which is the exact failure this exists to prevent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SEARCH_ROOTS = (REPO_ROOT / "src" / "agent_nettools", REPO_ROOT / "mcp_server")

#: A module-level string assigned to a name matching one of these, and long
#: enough to be prose rather than a label, is treated as a prompt.
#:
#: Name-based rather than content-based on purpose. "Does this string look
#: like it addresses a model" is a judgement a regex makes badly in both
#: directions, and a check that is wrong in the *quiet* direction is worse
#: than no check. A name-based rule can be defeated by naming a prompt
#: something else -- but that is a deliberate act, where forgetting the
#: convention is an accident, and accidents are what this catches.
_PROMPT_NAME_MARKERS = ("PROMPT", "SYSTEM_MESSAGE", "INSTRUCTIONS")

#: Shorter than this and it is a label, a header, or a format fragment.
#: The smallest real prompt in the tree is ~1,000 characters; the longest
#: non-prompt constant caught by the name rule was well under 200. The gap is
#: wide enough that the exact number is not load-bearing.
_MIN_PROMPT_CHARS = 400

#: Deliberate exceptions, each with the reason it is not a prompt file.
#: Empty is the goal. An entry here is a recorded decision, not an oversight --
#: the same shape `tests/test_interface_canonicalization.py` uses for its
#: `EXEMPT` classifications, and the reason this test asks for a justification
#: rather than just a name.
_EXEMPT: dict[str, str] = {
    # (module, constant): why it is not a model-facing prompt
}


def _module_level_string_constants(path: Path) -> list[tuple[str, str]]:
    """`(name, value)` for every module-level `NAME = "..."` in one file.

    Module level only: a prompt assembled inside a function from parts is a
    different (and more suspicious) thing, and is not what this rule is about.
    """

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - not our files
        return []

    found = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found.append((target.id, node.value.value))
    return found


def _literal_prompts() -> list[tuple[str, str, int]]:
    """Every `(module, constant, length)` that looks like a prompt in Python."""

    hits = []
    for root in SEARCH_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if "data" in path.parts:  # packaged copies, not source
                continue
            module = path.relative_to(REPO_ROOT).as_posix()
            for name, value in _module_level_string_constants(path):
                if not any(marker in name.upper() for marker in _PROMPT_NAME_MARKERS):
                    continue
                if len(value) < _MIN_PROMPT_CHARS:
                    continue
                if (module, name) in _EXEMPT:
                    continue
                hits.append((module, name, len(value)))
    return hits


def test_no_model_facing_prompt_is_a_python_string_literal():
    """The rule itself."""

    offenders = _literal_prompts()
    assert offenders == [], (
        "prompt(s) defined as Python string literals instead of files in "
        f"prompts/: {offenders}. Move the text to `prompts/<name>.v1.txt`, load "
        "it with `prompt_library.load_prompt`, add the packaged copy under "
        "`src/agent_nettools/data/prompts/`, and give it a case file in "
        "`prompts/tests/cases/` -- note that `tests/test_prompts.py` will then "
        "require it to name a refusal path, which is the point. If it is "
        "genuinely not a model-facing prompt, add it to `_EXEMPT` with the "
        "reason."
    )


def test_the_detector_actually_detects(tmp_path):
    """Positive control (OBS-181).

    Without this, a typo in `_PROMPT_NAME_MARKERS`, a `_MIN_PROMPT_CHARS` set
    too high, or an `ast` walk that silently matched nothing would leave the
    test above passing over an empty set and looking green for the wrong
    reason. This is the failure mode OBS-691 hit for real a day earlier: a
    containment test that proved a field safe using an example that had
    nothing unsafe in it.
    """

    offender = tmp_path / "sneaky.py"
    offender.write_text(
        'ANALYSIS_PROMPT = """You are a network troubleshooting assistant.\n'
        + ("Analyze the evidence and recommend a next check.\n" * 20)
        + '"""\n',
        encoding="utf-8",
    )

    found = _module_level_string_constants(offender)
    assert found, "the AST walk found no module-level string constants at all"
    name, value = found[0]
    assert any(m in name.upper() for m in _PROMPT_NAME_MARKERS)
    assert len(value) >= _MIN_PROMPT_CHARS, (
        "the sample prompt is shorter than the threshold, so this control "
        "proves nothing about the threshold"
    )


def test_a_short_constant_with_a_prompt_shaped_name_is_not_flagged(tmp_path):
    """The other direction of the control.

    A detector that flagged every constant whose name contains PROMPT would be
    useless noise, and would push people to name things evasively. Pinning the
    length rule keeps the first control honest -- it proves the threshold does
    something, not just that the name rule fires.
    """

    benign = tmp_path / "benign.py"
    benign.write_text('PROMPT_SIDECAR_DIRNAME = "_prompts"\n', encoding="utf-8")

    name, value = _module_level_string_constants(benign)[0]
    assert any(m in name.upper() for m in _PROMPT_NAME_MARKERS)
    assert len(value) < _MIN_PROMPT_CHARS


@pytest.mark.parametrize("name", ["troubleshooting", "fabric_analysis", "agent_system"])
def test_the_three_migrated_prompts_are_on_disk_and_loadable(name):
    """The migration itself, pinned so it cannot silently regress.

    Checks all three places a prompt has to exist: the reviewable source file,
    the packaged copy the wheel ships (EER-003), and the loader's own current
    version table.
    """

    from agent_nettools.prompt_library import CURRENT_VERSION, load_prompt

    source = REPO_ROOT / "prompts" / f"{name}.v1.txt"
    packaged = REPO_ROOT / "src" / "agent_nettools" / "data" / "prompts" / f"{name}.v1.txt"

    assert source.is_file(), f"{source} is missing -- the reviewable copy"
    assert packaged.is_file(), f"{packaged} is missing -- the wheel would ship without it"
    assert source.read_bytes() == packaged.read_bytes(), (
        f"{name}.v1.txt has drifted between the source tree and the packaged copy"
    )
    assert CURRENT_VERSION[name] == 1
    assert load_prompt(name, 1) == source.read_text(encoding="utf-8")
