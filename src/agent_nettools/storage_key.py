"""One shared storage-key policy: is this string safe to become a single
filesystem/database path component?

EER-001 (v1.0.1 hotfix) fixed a real path-traversal defect and, in the
process, left a note at ``fixtures.py``'s ``_FIXTURE_PART_RE`` that three
call sites carried duplicate copies of the identical charset regex:
``evidence_store._DEVICE_NAME_RE`` (``evidence_store.py:117``),
``fixtures._FIXTURE_PART_RE`` (``fixtures.py:94``, before this module
existed), and ``session_memory._SESSION_ID_RE`` (``session_memory.py:261``).
Consolidating them was deliberately deferred out of that security fix --
"a real consolidation ... tracked as its own item rather than smuggled into
a security fix" -- and EER-009 is that item.

The policy itself, unchanged from all three prior copies: a storage key is
one to ``max_length`` characters drawn from ``[A-Za-z0-9_.-]``, and is never
``"."`` or ``".."`` even though both are legal runs of the charset's ``.``
character under a bare length/charset check alone -- joined onto a path,
``".."`` is a traversal and ``"."`` is a confusing no-op alias, so both are
refused explicitly rather than left for the regex to accept by accident.

This module holds the policy only, not a fixed error message: every call
site still raises its own ``ValueError`` with whatever wording fits its own
context (a device name, a fixture label, a session id, an inventory device
name, ...), the same way the three original copies always did. Consolidating
the wording too would have coupled unrelated call sites to a shared phrase
for no safety benefit.

Migrated to this module so far: ``fixtures.py`` and
``inventory_model.py`` (both owned by this lane). ``evidence_store.py`` and
``session_memory.py`` still carry their own copies -- neither is owned by
this lane; migrating them is follow-up work for whichever lane owns each
file (reported alongside this item).
"""

from __future__ import annotations

import re

#: The charset a single storage path/key component may use. Alphanumerics,
#: underscore, dot, hyphen -- covers every real value this project has ever
#: produced: inventory device names (`P1`..`RR1`), fixture platforms/labels
#: (`cisco_xr`, `t0`/`t1`/`broken`/`healthy`/`isis-broken`/`unit`), and
#: session-memory ids (PID-derived, by default).
STORAGE_KEY_CHARSET = r"[A-Za-z0-9_.-]"

#: The length ceiling `evidence_store._DEVICE_NAME_RE` and (previously)
#: `fixtures._FIXTURE_PART_RE` both used. `session_memory._SESSION_ID_RE`
#: uses 128 instead -- pass `max_length=128` for that shape.
DEFAULT_MAX_LENGTH = 64

#: The two strings the charset alone would wrongly accept -- both are legal
#: runs of `.`, and both are storage-path-meaningful (`".."` is a traversal
#: segment, `"."` is a same-directory alias) rather than an ordinary key.
_RESERVED = (".", "..")


def storage_key_pattern(max_length: int = DEFAULT_MAX_LENGTH) -> re.Pattern[str]:
    """Compile the fullmatch pattern for one storage key at a given length
    ceiling. Exposed for a caller that wants to reuse the compiled pattern
    (e.g. across many calls in a hot path) rather than recompiling it
    through :func:`is_valid_storage_key` every time."""

    return re.compile(rf"{STORAGE_KEY_CHARSET}{{1,{max_length}}}")


def is_valid_storage_key(value: object, *, max_length: int = DEFAULT_MAX_LENGTH) -> bool:
    """Return whether ``value`` is safe to use as one storage path/key
    component: a non-empty string of only the allowed charset, at most
    ``max_length`` characters, and never ``"."`` or ``".."``.

    A predicate, not a raiser -- every caller so far raises its own
    ``ValueError`` with wording fitted to its own context, so this stays a
    plain boolean callers can drop straight into their existing ``if`` (the
    same shape every prior copy already had).
    """

    return (
        isinstance(value, str)
        and value not in _RESERVED
        and bool(storage_key_pattern(max_length).fullmatch(value))
    )
