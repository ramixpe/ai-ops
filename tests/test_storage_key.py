"""EER-009 -- the shared storage-key policy consolidated out of three
duplicate copies (`evidence_store._DEVICE_NAME_RE`, the former
`fixtures._FIXTURE_PART_RE`, `session_memory._SESSION_ID_RE`).

This module is the policy alone: is a given string safe to become one
filesystem/database path component. It is a predicate, not a raiser, so
these tests are about the boolean it returns, not about any particular
caller's error message (`fixtures.py` and `inventory_model.py` each pin
their own wording in their own test modules).
"""

from __future__ import annotations

import pytest

from agent_nettools.storage_key import (
    DEFAULT_MAX_LENGTH,
    STORAGE_KEY_CHARSET,
    is_valid_storage_key,
    storage_key_pattern,
)


@pytest.mark.parametrize(
    "value",
    [
        "P1", "P2", "P3", "P4", "PE1", "PE2", "PE3", "PE4", "RR1",  # real device names
        "cisco_xr",  # real platform
        "t0", "t1", "broken", "healthy", "isis-broken", "unit",  # real fixture labels
        "a", "A", "0", "_", "-",
        "x" * DEFAULT_MAX_LENGTH,  # exactly at the length ceiling
    ],
)
def test_real_and_boundary_values_are_valid(value):
    """Positive controls (OBS-181): the refusal tests below would pass just
    as well against a function that refuses everything, so every one of them
    is paired with a real or boundary value that must still be accepted."""

    assert is_valid_storage_key(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "", ".", "..",
        "a/b", "../..", "a/../../outside", "/etc",
        "a b",  # space
        "a\x00b",  # NUL
        "x" * (DEFAULT_MAX_LENGTH + 1),  # one past the length ceiling
        None, 123, [],  # not a string at all
    ],
)
def test_unsafe_or_ambiguous_values_are_rejected(value):
    assert is_valid_storage_key(value) is False


def test_dot_and_dotdot_are_rejected_even_though_the_charset_alone_would_accept_them():
    """The specific reason `"."`/`".."` need an explicit exclusion rather
    than relying on the regex: both are legal runs of the charset's `.`
    character, so a bare length/charset fullmatch would accept them."""

    assert storage_key_pattern().fullmatch(".") is not None
    assert storage_key_pattern().fullmatch("..") is not None
    # ... and yet the actual policy still refuses both.
    assert is_valid_storage_key(".") is False
    assert is_valid_storage_key("..") is False


def test_max_length_is_configurable_per_caller():
    """`session_memory._SESSION_ID_RE` uses 128 where `evidence_store`/
    `fixtures` use the 64-character default -- both must be expressible
    through the same shared function."""

    value_65_chars = "x" * 65

    assert is_valid_storage_key(value_65_chars) is False
    assert is_valid_storage_key(value_65_chars, max_length=128) is True


def test_storage_key_charset_matches_the_documented_policy():
    """Pins the actual charset, so a change here is a visible diff rather
    than a silent widening or narrowing of what every migrated call site
    accepts."""

    assert STORAGE_KEY_CHARSET == r"[A-Za-z0-9_.-]"


def test_storage_key_pattern_is_a_fullmatch_not_a_search():
    """A `search`-only pattern would accept `"a/../b"` because *part* of it
    matches the charset -- the policy needs the WHOLE string to match."""

    pattern = storage_key_pattern()

    assert pattern.fullmatch("a/../b") is None
    assert pattern.search("a/../b") is not None  # the substring "a" does match
