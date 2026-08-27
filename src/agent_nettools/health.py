"""Deterministic health verdicts over collected evidence.

Why this module exists
-----------------------
``status: "success"`` in every other tool in this package reflects only
*transport* success: the SSH session opened and the commands ran. A device
whose every BGP peer is down still reports ``success`` -- there is nothing
today that answers "is this device healthy?". At fleet scale that question
must be answered by cheap, deterministic rules *before* any LLM ever looks at
the evidence, so the reasoning layer only ever sees the anomalies, not all
nine (or nine thousand) devices' worth of noise.

Two independent rule classes -- read this before adding a rule
----------------------------------------------------------------
``inventory/lab.yaml``'s ``expected:`` blocks are *derived* from this fabric's
own current state (see ``topology.py``), and this fabric is partly broken:
the file literally records ``PE2: isis_adjacencies: 0`` and
``PE4: isis_adjacencies: 0``. A rule that only compares observed counts
against that baseline would therefore call both devices healthy -- the
baseline blesses the very brokenness it was measured from. So health rules
come in two kinds, kept in separate tables below, and a change should know
which one it is:

1. **Role invariants** (``ROLE_INVARIANT_RULES``) -- what must be true of a
   router in this fabric *given its role*, independent of anything recorded
   in the inventory. These are what catch baked-in brokenness that the
   baseline would otherwise bless: "every router must have at least one IS-IS
   adjacency" does not care that the baseline says zero is expected.
2. **Baseline rules** (``BASELINE_RULES``) -- compare an observed count
   against the inventory's recorded ``expected:`` value. These catch *drift*
   from the last known-derived state, which role invariants cannot see (a
   device dropping from 4 BGP peers to 3 is not a role violation, but it is a
   change worth flagging).

A third, single ``META_RULES`` entry (``suspicious_baseline``) makes the tool
honest about its own baseline: when the recorded expectation is itself a
value a role invariant would call unhealthy (``isis_adjacencies == 0``), that
is flagged directly, so nobody mistakes "matches the baseline" for "is
healthy" on a device whose baseline was learned from a broken fabric.

Unevaluated, not silently "ok"
-------------------------------
A rule must never read a missing or failed intent as healthy. If an intent's
parse did not succeed (``parsers.PARSE_OK``) -- because the platform command
errored, is ``unsupported`` on this platform, or the parser itself failed --
every rule reading that intent is skipped for that device and the intent name
is listed in the verdict's ``unevaluated`` list instead. Silence from a failed
collection must never look like health.

**Merged into `checks.py` at B-403.** This module is now a re-export shim for
the rule engine so the nine importers below keep working; the rules
themselves live beside the descent's predicates. See the divider comment
there for what the merge cost.

Maintenance windows / silences (B-483)
=======================================
Nothing above this line knows an event can be *expected*. During a planned
change every rule above still fires exactly as designed -- correctly,
because the fault it reports is real -- and a NOC running this in anger pages
itself on work it scheduled. That is the gap this section closes, and it is
scoped narrowly on purpose: **a silence changes what a verdict does with a
finding it already produced. It never changes whether a rule fires.**

What a silence covers
----------------------
Three independent fields, each optional and each an AND when present:
``device`` (an exact device name), ``rule`` (an exact rule name from
``ALL_RULES`` -- e.g. ``"bgp_session_down"`` -- or, for a caller silencing an
``investigate``/descent-side finding, whatever identifier that caller uses
for the same purpose, since :func:`find_silence` does not care which table a
name came from), and ``subject`` (an exact subject string -- a peer address,
an interface name -- matching :class:`checks.CheckResult`'s own ``subject``
convention). Omitting a field means "any" for that field, so a silence can be
as broad as "every rule on PE2 today" (``device="PE2"``) or as narrow as
"just the BGP session to 10.255.0.12, only while it flaps during the
migration" (``device=..., rule="bgp_session_down", subject="10.255.0.12"``).

How long
--------
Every silence carries a mandatory ``expires_at`` -- there is deliberately no
way to construct one that never ends. An optional ``starts_at`` lets one be
filed ahead of a scheduled window; omitted, it is active from the moment it
is loaded. A silence past its ``expires_at`` simply stops matching -- see
:meth:`Silence.matches` -- so an operator who forgets to delete an old entry
gets a table that is merely untidy, never one that is silently still
suppressing something.

What a silenced finding does instead of vanishing -- the part that matters
-----------------------------------------------------------------------------
This build has hit "an absence read as health" three times before this
change existed (OBS-006, OBS-043, OBS-044, all cited in ``checks.py``'s own
docstring) and the fabric-scale version of it twice more, by name
(OBS-188, OBS-202): a report that quietly drops a fact reads identically to a
report with nothing to say. A silence that made a finding disappear from
:func:`evaluate_device`'s output would be that defect in a fourth costume --
worse, this time deliberately built in, in service of the exact goal
("silence the noise") that makes it dangerous.

So a silenced finding is **never removed**. :func:`apply_silences` keeps it
in ``findings``, tags it ``"silenced": True``, and attaches the matched
silence's ``id``/``reason``/``created_by``/``expires_at`` right beside it, so
a human reading the report -- not just a machine deciding whether to page --
sees the fault, sees that it was expected, and sees who said so and until
when. What changes is narrower and specific to what a silence is *for*:

* The finding stops contributing to the verdict's ``severity`` and its
  ``counts`` bucket -- so ``nettools health``'s exit code (via
  :func:`exit_code_for_severity`) and a cron job gating on it are not failed
  by a fault the operator already knows about and is already fixing.
* ``raw_severity`` on the returned verdict preserves what severity would have
  been with **no** silence in effect. "Silenced, and would have been
  critical" is therefore never rendered the same as "ok" -- the two are
  different facts and stay different fields, which is the direct fix for the
  OBS-188/OBS-202 shape: a silenced-but-critical device is never
  indistinguishable from a healthy one, because ``raw_severity`` says so even
  when ``severity`` (the paging-facing field) does not.
* ``counts["silenced"]`` is its own bucket, separate from
  ``critical``/``warning``/``info``, for the same reason: a glance at counts
  must show "3 critical, 1 silenced" rather than either merging the silenced
  one into "critical" (which would still page) or dropping it out of the
  count entirely (which is the vanish this section exists to prevent).

``--notify`` is a narrower case of the same rule, and lives in
``notifier.py`` rather than here: see :class:`notifier.SilenceNotice` and
:func:`notifier.notify`'s own docstring for why paging (a single channel,
unlike a report) is allowed to actually skip delivery when silenced, while
the *report* --  this module's concern -- never does.

``NETTOOLS_SILENCE_FILE`` -- verdict production, not just annotation
----------------------------------------------------------------------
:func:`load_silences` itself still takes an explicit ``path`` and reads no
environment variable -- it stays a pure function, the same discipline
``ledger.py``'s own module docstring gives for the identical shape. The
environment variable lives one layer up, on the two functions that actually
*produce* a verdict for a caller: :func:`evaluate_device_with_silences` and
:func:`evaluate_fabric_with_silences`. Each runs ``evaluate_device``/
``evaluate_fabric`` and then :func:`apply_silences`/
:func:`apply_silences_to_fabric` in one call, resolving which silences apply
in this order: an explicit ``silences=`` sequence wins outright; otherwise an
explicit ``silence_path=`` is loaded; otherwise ``NETTOOLS_SILENCE_FILE`` is
read and loaded. Declared in ``settings.py`` like every other ``NETTOOLS_*``
variable, so it shows up in ``nettools config show|check``. Unset (the
default) resolves to ``load_silences(None)`` -- ``()``, never an error -- so
a verdict produced through these two functions with no silence file
configured is byte-for-byte what :func:`evaluate_device`/:func:`evaluate_fabric`
already returned, plus the always-present ``silenced: False`` tag
:func:`apply_silences` adds to every finding. **Nothing above this line about
"never removed" changes**: these two functions exist so a caller gets that
guarantee automatically, by calling one function instead of remembering to
thread three together; they do not change what silencing means.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

from .checks import (  # noqa: F401 - re-exported for the pre-merge importers
    ALL_RULES,
    BASELINE_RULES,
    META_RULES,
    ROLE_INVARIANT_RULES,
    SEVERITY_ORDER,
    Rule,
    RuleContext,
    evaluate_device,
    evaluate_fabric,
    exit_code_for_severity,
    severity_rank,
)
from .inventory_model import Device

#: Read only by `evaluate_device_with_silences`/`evaluate_fabric_with_silences`
#: below -- `load_silences` itself stays a pure function. Declared in
#: settings.py so `nettools config show|check` lists it.
NETTOOLS_SILENCE_FILE_ENV = "NETTOOLS_SILENCE_FILE"

__all__ = [
    "ALL_RULES",
    "BASELINE_RULES",
    "META_RULES",
    "NETTOOLS_SILENCE_FILE_ENV",
    "ROLE_INVARIANT_RULES",
    "SEVERITY_ORDER",
    "Rule",
    "RuleContext",
    "Silence",
    "SilenceError",
    "apply_silences",
    "apply_silences_to_fabric",
    "evaluate_device",
    "evaluate_device_with_silences",
    "evaluate_fabric",
    "evaluate_fabric_with_silences",
    "exit_code_for_severity",
    "find_silence",
    "load_silences",
    "parse_silences",
    "severity_rank",
]


class SilenceError(Exception):
    """A silence file (or an in-memory definition) is malformed.

    Raised by :func:`parse_silences`/:func:`load_silences` -- never by
    :func:`find_silence` or :func:`apply_silences`, which only ever consume
    already-validated :class:`Silence` objects. The same "a typo must not
    silently disable the thing it configures" rule
    ``notifier.get_notifier`` applies to an unknown provider name applies
    here to a malformed silence: a maintenance window that silently failed to
    load would page right through the outage it was filed to cover, which is
    the opposite of what filing it was for.
    """


def _parse_timestamp(value: Any, *, field: str, context: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SilenceError(
                f"{context}: {field!r} is not a valid ISO-8601 timestamp: {value!r}"
            ) from exc
    else:
        raise SilenceError(f"{context}: {field!r} must be a string, got {value!r}")

    if parsed.tzinfo is None:
        raise SilenceError(
            f"{context}: {field!r} ({value!r}) has no timezone -- a naive timestamp "
            "compared against another timezone's wall clock is exactly the ambiguity "
            "this module refuses to guess about; write it with an explicit offset "
            "(e.g. '2026-08-20T18:00:00+00:00' or the 'Z' suffix)"
        )
    return parsed


@dataclass(frozen=True)
class Silence:
    """One declared maintenance window. See the module docstring for the shape.

    ``device``/``rule``/``subject`` are each ``None`` (matches anything) or an
    exact string -- no globbing, no regex. A silence table is meant to be read
    by a human under time pressure; a table where every row's meaning is
    "what it says, literally" is the one that stays readable at 2 a.m.
    """

    id: str
    reason: str
    created_by: str
    expires_at: datetime
    device: str | None = None
    rule: str | None = None
    subject: str | None = None
    starts_at: datetime | None = None

    def matches(
        self, *, device: str, rule: str | None, subject: str | None, now: datetime
    ) -> bool:
        """Is this silence active right now, and does it cover this finding?"""

        if now >= self.expires_at:
            return False
        if self.starts_at is not None and now < self.starts_at:
            return False
        if self.device is not None and self.device != device:
            return False
        if self.rule is not None and self.rule != rule:
            return False
        if self.subject is not None and self.subject != subject:
            return False
        return True

    def as_dict(self) -> dict[str, Any]:
        """The compact record a caller attaches to a silenced finding."""

        return {
            "id": self.id,
            "reason": self.reason,
            "created_by": self.created_by,
            "expires_at": self.expires_at.isoformat(),
        }


def parse_silences(data: Any, *, source: str = "<data>") -> tuple[Silence, ...]:
    """Turn already-loaded YAML/JSON data into validated :class:`Silence` objects.

    ``data`` is a list of mappings (the shape ``yaml.safe_load`` returns for a
    YAML file whose top level is a list). Separated from :func:`load_silences`
    so a caller with in-memory data (a test, a future CLI subcommand that
    builds one from flags) never needs a file on disk to get validation.
    """

    if not isinstance(data, list):
        raise SilenceError(f"{source}: a silence file must be a list at the top level")

    silences: list[Silence] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(data):
        context = f"{source}[{index}]"
        if not isinstance(entry, dict):
            raise SilenceError(f"{context}: each silence must be a mapping, got {entry!r}")

        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise SilenceError(
                f"{context}: 'reason' is required and must be non-empty -- an "
                "unexplained silence is only marginally better than an unexplained "
                "page, and it is the operator reading the report later who pays for it"
            )
        created_by = entry.get("created_by")
        if not isinstance(created_by, str) or not created_by.strip():
            raise SilenceError(f"{context}: 'created_by' is required and must be non-empty")
        if "expires_at" not in entry:
            raise SilenceError(
                f"{context}: 'expires_at' is required -- every silence ends; there is no "
                "such thing as a permanent one here (see the module docstring)"
            )
        expires_at = _parse_timestamp(entry["expires_at"], field="expires_at", context=context)
        starts_at = (
            _parse_timestamp(entry["starts_at"], field="starts_at", context=context)
            if entry.get("starts_at") is not None
            else None
        )
        if starts_at is not None and starts_at >= expires_at:
            raise SilenceError(f"{context}: 'starts_at' must be before 'expires_at'")

        for field_name in ("device", "rule", "subject"):
            value = entry.get(field_name)
            if value is not None and not isinstance(value, str):
                raise SilenceError(f"{context}: {field_name!r} must be a string or omitted")

        silence_id = entry.get("id")
        if silence_id is None:
            silence_id = (
                f"silence-{index}-"
                f"{entry.get('device') or 'any'}-{entry.get('rule') or 'any'}"
            )
        silence_id = str(silence_id)
        if silence_id in seen_ids:
            raise SilenceError(f"{context}: id {silence_id!r} is declared twice")
        seen_ids.add(silence_id)

        silences.append(
            Silence(
                id=silence_id,
                reason=reason.strip(),
                created_by=created_by.strip(),
                expires_at=expires_at,
                device=entry.get("device"),
                rule=entry.get("rule"),
                subject=entry.get("subject"),
                starts_at=starts_at,
            )
        )
    return tuple(silences)


def load_silences(path: str | Path | None) -> tuple[Silence, ...]:
    """The declarative silence table, or ``()`` if none is configured.

    ``path=None`` (or a path that does not exist) means "no maintenance
    windows declared" -- returns ``()``, never raises. That is deliberately
    the same "absent config is a valid, quiet default" shape
    ``notifier.get_notifier``'s ``NETTOOLS_NOTIFIER=none`` gives ``--notify``:
    a tool that only just gained silence support must not require every
    caller to create an empty file first.

    A file that **exists but is malformed** does raise
    (:class:`SilenceError`) -- see the module docstring's "a typo must not
    silently disable the thing it configures" rule.
    """

    if path is None:
        return ()
    resolved = Path(path)
    if not resolved.exists():
        return ()
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SilenceError(f"cannot read silence file {resolved}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise SilenceError(f"invalid YAML in silence file {resolved}: {exc}") from exc
    if data is None:
        return ()
    return parse_silences(data, source=str(resolved))


def find_silence(
    silences: Sequence[Silence],
    *,
    device: str,
    rule: str | None = None,
    subject: str | None = None,
    now: datetime | None = None,
) -> Silence | None:
    """The most specific active silence covering this finding, or ``None``.

    "Most specific" ranks by how many of ``device``/``rule``/``subject`` a
    candidate pins rather than wildcards, so a narrow silence filed for
    exactly this finding is never shadowed by a broad one filed earlier for
    "everything on this device" -- table order must not matter for which
    silence wins, only for nothing else.
    """

    now = now or datetime.now(timezone.utc)
    candidates = [
        s for s in silences if s.matches(device=device, rule=rule, subject=subject, now=now)
    ]
    if not candidates:
        return None

    def _specificity(s: Silence) -> int:
        return sum(1 for field in (s.device, s.rule, s.subject) if field is not None)

    return max(candidates, key=_specificity)


def _max_severity(a: str, b: str) -> str:
    # Re-declared rather than imported from `checks.py`'s private
    # `_max_severity` -- three lines, and the same "neither should have to
    # import the other's private helper" reasoning `checks._is_numeric`'s own
    # docstring already gives for this exact situation.
    return a if severity_rank(a) >= severity_rank(b) else b


def apply_silences(
    verdict: dict[str, Any],
    silences: Sequence[Silence],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Annotate one device's :func:`evaluate_device` verdict with active silences.

    Returns a new dict; ``verdict`` is not mutated. Every finding stays in
    ``findings`` -- see the module docstring's "what a silenced finding does
    instead of vanishing". The returned verdict adds:

    * ``findings[i]["silenced"]`` (bool) and, when true,
      ``findings[i]["silence"]`` (:meth:`Silence.as_dict`).
    * ``severity`` / ``counts`` -- recomputed from the **unsilenced** findings
      only, so paging and exit-code gating (:func:`exit_code_for_severity`)
      ignore what the operator already knows about. ``counts["silenced"]`` is
      the count of findings that were tagged.
    * ``raw_severity`` -- what ``severity`` would have been with no silence
      applied at all (the original verdict's own ``severity``), preserved so
      "silenced but would be critical" is never indistinguishable from "ok".
    * ``silences_applied`` -- ``True`` iff at least one finding was silenced,
      so a caller can decide whether to say anything about it without
      re-deriving the fact from ``counts``.
    """

    now = now or datetime.now(timezone.utc)
    device = str(verdict.get("device", ""))

    new_findings: list[dict[str, Any]] = []
    counts = {
        "critical": 0, "unreachable": 0, "warning": 0, "info": 0, "silenced": 0,
    }
    severity = "ok"

    for finding in verdict.get("findings", []):
        finding = dict(finding)
        silence = find_silence(
            silences,
            device=device,
            rule=finding.get("rule"),
            subject=finding.get("subject"),
            now=now,
        )
        if silence is not None:
            finding["silenced"] = True
            finding["silence"] = silence.as_dict()
            counts["silenced"] += 1
        else:
            finding["silenced"] = False
            fseverity = finding.get("severity", "ok")
            severity = _max_severity(severity, fseverity)
            if fseverity in counts:
                counts[fseverity] += 1
        new_findings.append(finding)

    result = dict(verdict)
    result["findings"] = new_findings
    result["raw_severity"] = verdict.get("severity", "ok")
    result["severity"] = severity
    result["counts"] = counts
    result["silences_applied"] = counts["silenced"] > 0
    return result


def apply_silences_to_fabric(
    fabric_verdict: dict[str, Any],
    silences: Sequence[Silence],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """:func:`apply_silences`, run per device, then the fabric roll-up redone the
    same way :func:`evaluate_fabric` computes it -- from the (now silence-aware)
    per-device severities, never from the pre-silence roll-up.
    """

    now = now or datetime.now(timezone.utc)
    devices = {
        name: apply_silences(device_verdict, silences, now=now)
        for name, device_verdict in fabric_verdict.get("devices", {}).items()
    }

    fabric_severity = "ok"
    by_severity: dict[str, int] = {
        "critical": 0, "unreachable": 0, "warning": 0, "info": 0, "ok": 0,
    }
    silenced_devices: list[str] = []
    for name, device_verdict in devices.items():
        fabric_severity = _max_severity(fabric_severity, device_verdict["severity"])
        by_severity[device_verdict["severity"]] += 1
        if device_verdict.get("silences_applied"):
            silenced_devices.append(name)

    result = dict(fabric_verdict)
    result["devices"] = devices
    result["severity"] = fabric_severity
    counts = dict(fabric_verdict.get("counts", {}))
    counts["by_severity"] = by_severity
    counts["silenced_devices"] = sorted(silenced_devices)
    result["counts"] = counts
    return result


def _resolve_silences(
    silences: Sequence[Silence] | None, silence_path: str | Path | None
) -> Sequence[Silence]:
    """Shared resolution order for the two verdict-production helpers below.

    An explicit ``silences=`` sequence always wins (a caller -- typically a
    test -- that already has parsed :class:`Silence` objects should never pay
    for a file read it does not need). Otherwise an explicit ``silence_path=``
    is loaded. Otherwise :data:`NETTOOLS_SILENCE_FILE_ENV` is read and loaded.
    :func:`load_silences` already returns ``()`` -- never raises -- for
    ``None`` or a nonexistent path, so "nothing configured" here is quiet by
    construction, not something this helper has to special-case.
    """

    if silences is not None:
        return silences
    path = silence_path if silence_path is not None else os.getenv(NETTOOLS_SILENCE_FILE_ENV)
    return load_silences(path)


def evaluate_device_with_silences(
    evidence: dict[str, Any],
    device: Device,
    *,
    silences: Sequence[Silence] | None = None,
    silence_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """:func:`evaluate_device` followed by :func:`apply_silences`, in one call.

    This is the verdict-production entry point promised by the module
    docstring's "``NETTOOLS_SILENCE_FILE`` -- verdict production, not just
    annotation" section: a silenced subject's verdict is still produced by
    ``evaluate_device`` exactly as before, and this function only ever
    ANNOTATES it -- the finding stays in ``findings``, tagged, never dropped
    (see :func:`apply_silences`'s own docstring for the full guarantee). With
    no silence source configured at all (no ``silences=``/``silence_path=``
    and ``NETTOOLS_SILENCE_FILE`` unset), the returned verdict differs from a
    bare ``evaluate_device`` call only by the always-present
    ``silenced: False`` tag :func:`apply_silences` adds to every finding and
    the ``raw_severity``/``counts["silenced"]``/``silences_applied`` fields it
    always adds -- severity itself is unchanged, since nothing matched.
    """

    verdict = evaluate_device(evidence, device)
    resolved = _resolve_silences(silences, silence_path)
    return apply_silences(verdict, resolved, now=now)


def evaluate_fabric_with_silences(
    evidence_by_device: dict[str, dict[str, Any]],
    devices: list[Device] | None = None,
    *,
    silences: Sequence[Silence] | None = None,
    silence_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """:func:`evaluate_fabric` followed by :func:`apply_silences_to_fabric`.

    The fabric-wide counterpart to :func:`evaluate_device_with_silences` --
    see its docstring for the silence-resolution order and the "never
    removed" guarantee both of these functions ultimately rest on.
    """

    verdict = evaluate_fabric(evidence_by_device, devices)
    resolved = _resolve_silences(silences, silence_path)
    return apply_silences_to_fabric(verdict, resolved, now=now)
