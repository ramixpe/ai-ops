"""Free-text flow selection: a pure function, deliberately not a model call. B-112.

D5 indexes flows by *object type* — seven for this fabric, two implemented —
because the selection problem is small once it is framed that way: "is this an
object, or a symptom of one?" `event_routing.py` (B-480/B-202) already answers
the machine-input half of that question with a declared table, a device that
never comes from message text, and a subject validated by reconstruction. This
module is the human-sentence half, and it **inherits those properties rather
than reinventing them** — the device/subject validators below are imported
from `event_routing`, not copied, so there is exactly one definition of "a
known device" and "a well-formed subject" in this repository.

Why no model
------------
B-112's brief is explicit that this must not become "ask a model to parse the
sentence". The architecture's central claim — repeated throughout
the project — is that a model *selects from a menu*, it never *authors* the
selection. A free-text parser is exactly the kind of
component where that boundary is easiest to blur, because natural language
looks like the model's home turf. It is not the model's job here: the menu is
two flows, matching against it is a handful of declared regular expressions,
and a sentence that does not match is a stated, listed refusal — not a
prompt.

Unmatched is an answer, not an error
-------------------------------------
Same discipline as `event_routing.RoutingDecision`: every `SelectionDecision`
carries a `reason`, and an unmatched sentence carries `candidates` — what the
caller could say instead — rather than an exception a script has to catch.
**Never guess.** A sentence that could plausibly mean two different flows, or
that names two devices, or two peer addresses, refuses rather than picking
one; guessing wrong here means investigating the wrong object silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .event_routing import _known_devices, _validated_device, _validated_subject

__all__ = [
    "SENTENCE_FLOW_TABLE",
    "SelectionDecision",
    "looks_like_sentence",
    "select_flow",
]


@dataclass(frozen=True)
class SelectionDecision:
    """One sentence's flow-selection verdict. ``reason`` is always populated."""

    matched: bool
    flow: str | None = None
    device: str | None = None
    subject: str | None = None
    reason: str = ""
    #: Names of the declared `SENTENCE_FLOW_TABLE` rows that fired, for a
    #: caller's log -- the free-text analogue of `RoutingDecision.matched`,
    #: plural here because more than one declared phrase can name one flow.
    matched_patterns: tuple[str, ...] = ()
    #: Populated only when `matched` is False: concrete alternatives, not a
    #: bare "could not parse". A refusal a caller cannot act on is not
    #: meaningfully different from an exception.
    candidates: tuple[str, ...] = ()

    def suggested_command(self) -> list[str] | None:
        """The exact ``nettools`` argv this decision suggests, or ``None``.

        Same shape and same reasoning as `RoutingDecision.suggested_command`:
        a **list**, never a shell string, so a caller that joins it itself
        cannot reopen a quoting-injection surface. Every element is either a
        literal or a value `_validated_device`/`_validated_subject` already
        reconstructed -- this suggestion carries no authority of its own.
        """

        if not self.matched:
            return None
        return ["nettools", "investigate", str(self.device), str(self.subject),
                "--flow", str(self.flow)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched, "flow": self.flow, "device": self.device,
            "subject": self.subject, "reason": self.reason,
            "matched_patterns": list(self.matched_patterns),
            "candidates": list(self.candidates),
            "suggested_command": self.suggested_command(),
        }


def looks_like_sentence(subject: str) -> bool:
    """True if ``subject`` cannot be a literal token ``investigate`` already accepts.

    `investigate DEVICE SUBJECT` takes exactly one token today -- an IPv4
    address or an interface name -- and neither one ever contains whitespace.
    Whitespace is therefore a cheap, complete signal that the caller handed
    over prose: it cannot misfire against any subject the command already
    accepts, so gating the free-text path on it changes nothing for an
    existing invocation (the whole point of B-112's wiring requirement).
    """

    return isinstance(subject, str) and any(ch.isspace() for ch in subject)


# --------------------------------------------------------------------------- #
# The table. Declared, reviewed, ordered -- same house style as
# `event_routing.MNEMONIC_FLOW_TABLE`, `audit.AUDIT_RULES`,
# `model_egress.ERROR_KINDS`: a flat tuple of named rows a reviewer reads top
# to bottom, not a model prompt and not a learned classifier.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _SentenceRule:
    """One declared way a human names a flow. Matched with ``.search`` against
    the lowercased sentence -- case is a typing habit, not a signal."""

    name: str
    pattern: re.Pattern[str]
    flow: str


#: Seeded for the two flows MVP-0 implements (`flows.FLOWS`) -- the same
#: restriction `event_routing.MNEMONIC_FLOW_TABLE` states for the same
#: reason: routing to a declared-but-unimplemented flow would trade "not
#: understood" (an honest refusal) for a `NotImplementedError` three frames
#: deeper (a confusing one). Extending this to a third flow (D5's growth
#: path) means adding rows here *and* a `SUBJECT_EXTRACTORS` entry -- both
#: covered by `test_every_selectable_flow_is_actually_implemented` below.
#:
#: Multiple rows may name the same flow (interface has two, same as
#: `MNEMONIC_FLOW_TABLE`'s two interface mnemonics) -- each is one phrase a
#: reviewer can independently confirm belongs to that object type.
SENTENCE_FLOW_TABLE: tuple[_SentenceRule, ...] = (
    _SentenceRule("bgp_keyword", re.compile(r"\bbgp\b"), "bgp_session"),
    _SentenceRule(
        "session_or_peer_language",
        re.compile(r"\b(?:session|peer|neighbou?r)\b"),
        "bgp_session",
    ),
    _SentenceRule("establish_language", re.compile(r"\bestablish\w*\b"), "bgp_session"),
    # Deliberately not `\b`-anchored on the leading edge: "unreachable" must
    # match, and "un" + "reachable" share no word boundary for `\breach` to
    # land on. `reach` is not a plausible substring of an unrelated word in a
    # network-troubleshooting sentence, so the looser match costs nothing.
    _SentenceRule("reachability_language", re.compile(r"reach"), "bgp_session"),
    _SentenceRule("interface_keyword", re.compile(r"\binterfaces?\b"), "interface"),
    _SentenceRule("link_or_port_language", re.compile(r"\b(?:link|port)\b"), "interface"),
    _SentenceRule("line_protocol_language", re.compile(r"\bline\s*protocol\b"), "interface"),
    _SentenceRule(
        "admin_down_language",
        re.compile(r"\b(?:shut(?:down)?|admin(?:istratively)?[\s-]*down)\b"),
        "interface",
    ),
)


# --------------------------------------------------------------------------- #
# Subject extractors: sentence -> (subject, problem). One per implemented
# flow, same `Callable` shape `MNEMONIC_FLOW_TABLE` uses for its third column.
# --------------------------------------------------------------------------- #

_IPV4_TOKEN = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

#: Declared vocabulary of IOS-XR interface-name families this fabric uses --
#: reviewed, not learned, same posture as `SENTENCE_FLOW_TABLE`. A token is
#: only treated as an interface candidate if it starts with one of these;
#: without that anchor, any bare word matching the interface charset would
#: qualify, and a device name does (`_INTERFACE_NAME_RE` in `templates.py`
#: would `fullmatch("RR1")` cleanly -- letter, then digits, all in the
#: charset). The anchor is what keeps a device mention from masquerading as
#: an interface.
_INTERFACE_PREFIXES = (
    "GigabitEthernet", "TenGigE", "HundredGigE", "FortyGigE",
    "Loopback", "Bundle-Ether", "MgmtEth", "Null",
)
_INTERFACE_TOKEN = re.compile(
    r"\b(?:" + "|".join(_INTERFACE_PREFIXES) + r")[A-Za-z0-9_./-]*",
    re.IGNORECASE,
)


def _extract_bgp_subject(text: str) -> tuple[str | None, str]:
    """The one IPv4 peer address named in free text.

    Every candidate is re-validated through `_validated_subject` (imported,
    not reimplemented) rather than `ipaddress` directly here -- the same
    canonicalise-by-reconstruction rule as `event_routing`'s own extractors,
    from the same code, so the two paths cannot silently diverge on what
    counts as a valid IPv4 subject.
    """

    found: list[str] = []
    for token in _IPV4_TOKEN.findall(text):
        validated = _validated_subject(token)
        if validated is not None and validated not in found:
            found.append(validated)
    if not found:
        return None, "no valid IPv4 peer address found in the sentence"
    if len(found) > 1:
        return None, (
            f"more than one address mentioned ({', '.join(found)}); "
            "refusing to guess which one is the subject"
        )
    return found[0], ""


def _extract_interface_subject(text: str) -> tuple[str | None, str]:
    """The one interface name named in free text, anchored on a known family."""

    found: list[str] = []
    for match in _INTERFACE_TOKEN.finditer(text):
        validated = _validated_subject(match.group(0))
        if validated is not None and validated not in found:
            found.append(validated)
    if not found:
        return None, (
            "no valid interface name found in the sentence (must be a full "
            "name like GigabitEthernet0/0/0/1, not an abbreviation)"
        )
    if len(found) > 1:
        return None, (
            f"more than one interface mentioned ({', '.join(found)}); "
            "refusing to guess which one is the subject"
        )
    return found[0], ""


#: Keys must be exactly the flows named in `SENTENCE_FLOW_TABLE` -- pinned by
#: `test_every_selectable_flow_has_a_subject_extractor`.
SUBJECT_EXTRACTORS: dict[str, Callable[[str], tuple[str | None, str]]] = {
    "bgp_session": _extract_bgp_subject,
    "interface": _extract_interface_subject,
}


# --------------------------------------------------------------------------- #
# Device extraction. Candidates come from the sentence; the *decision* about
# whether one names a real device never does -- `_validated_device` (imported
# from `event_routing`) is the sole authority, exact-match against the live
# inventory, same as every other caller in this repository.
# --------------------------------------------------------------------------- #

#: A device-shaped token: 1-6 letters then 1-3 digits, e.g. "RR1", "PE2".
#: Generous enough for this fabric's naming (`P1`..`P4`, `PE1`..`PE4`, `RR1`)
#: and, not by accident, too short to swallow an interface-family word --
#: `GigabitEthernet0` has seven consecutive letters before its first digit,
#: which cannot fit the `{1,6}` cap, so the two extractors cannot collide on
#: that token. See `test_an_interface_family_word_is_never_mistaken_for_a_device`.
#:
#: The trailing `(?!/)` excludes an *abbreviated* interface reference like
#: "Gi0/0/0/1" -- "Gi0" alone fits the letters+digits shape and, without this,
#: would be read as a second, unresolvable device candidate every time a
#: sentence used the short form. Real device names in this lab are never
#: followed by "/"; abbreviated interfaces always are. Abbreviated interfaces
#: are not otherwise supported (see `_INTERFACE_PREFIXES`), so this only
#: changes which refusal a caller sees, not whether one happens.
_DEVICE_TOKEN = re.compile(r"\b[A-Za-z]{1,6}\d{1,3}\b(?!/)")


def _extract_device(text: str) -> tuple[str | None, str]:
    """``(device, problem)`` -- resolved against the inventory, never trusted
    from the sentence itself. Mirrors `event_routing._validated_device`'s
    contract exactly, because it *is* that function: the candidate token
    found here carries no authority until that call confirms it."""

    known = _known_devices()
    # Case-insensitive de-duplication, keeping the first spelling seen, so
    # "rr1 ... RR1" in one sentence counts once rather than reading as two
    # different devices.
    seen: dict[str, str] = {}
    for token in _DEVICE_TOKEN.findall(text):
        seen.setdefault(token.upper(), token)

    if not seen:
        return None, (
            "no device named in the sentence (known devices: "
            f"{', '.join(sorted(known)) or 'inventory unreadable'})"
        )
    if len(seen) > 1:
        return None, (
            f"more than one device named ({', '.join(sorted(seen.values()))}); "
            "refusing to guess which one to investigate from"
        )
    (upper, raw), = seen.items()
    by_upper = {name.upper(): name for name in known}
    # Pass the *canonical* spelling through when the token resolves, so a
    # lowercase "rr1" investigates as "RR1" like every other entry point --
    # falling back to the raw token when it does not resolve, so the refusal
    # below names exactly what the caller typed.
    return _validated_device(by_upper.get(upper, raw), source_kind="freetext")


# --------------------------------------------------------------------------- #
# What to say instead, when nothing matched.
# --------------------------------------------------------------------------- #

_CANDIDATES: tuple[str, ...] = (
    "name the flow explicitly: nettools investigate DEVICE SUBJECT --flow bgp_session",
    "name the flow explicitly: nettools investigate DEVICE SUBJECT --flow interface",
    "describe a BGP session: mention 'bgp', 'session', 'peer'/'neighbor', or "
    "'reach'/'unreachable', a device name, and the peer's IPv4 address",
    "describe an interface: mention 'interface', 'link', 'port', 'line protocol', "
    "or 'shutdown'/'admin down', a device name, and the interface's full name "
    "(e.g. GigabitEthernet0/0/0/1)",
)


def select_flow(sentence: str) -> SelectionDecision:
    """Route one free-text sentence to (flow, device, subject), or refuse.

    Pure function, no I/O beyond reading the inventory (through the imported
    validators) to know what a real device is. Never calls a model -- see the
    module docstring for why that is the point, not a limitation to fix.
    """

    if not isinstance(sentence, str) or not sentence.strip():
        return SelectionDecision(matched=False, reason="empty input", candidates=_CANDIDATES)

    lowered = sentence.lower()
    matched_flows = sorted({rule.flow for rule in SENTENCE_FLOW_TABLE if rule.pattern.search(lowered)})

    if not matched_flows:
        return SelectionDecision(
            matched=False,
            reason="no declared BGP-session or interface language recognised in the sentence",
            candidates=_CANDIDATES,
        )
    if len(matched_flows) > 1:
        return SelectionDecision(
            matched=False,
            reason=(
                f"sentence matches language for more than one flow "
                f"({', '.join(matched_flows)}); ambiguous, refusing to guess"
            ),
            candidates=_CANDIDATES,
        )

    flow = matched_flows[0]
    matched_names = tuple(sorted(
        rule.name for rule in SENTENCE_FLOW_TABLE
        if rule.flow == flow and rule.pattern.search(lowered)
    ))

    device, device_problem = _extract_device(sentence)
    if device_problem:
        return SelectionDecision(
            matched=False, flow=flow, reason=device_problem,
            matched_patterns=matched_names, candidates=_CANDIDATES,
        )

    subject, subject_problem = SUBJECT_EXTRACTORS[flow](sentence)
    if subject_problem:
        return SelectionDecision(
            matched=False, flow=flow, device=device, reason=subject_problem,
            matched_patterns=matched_names, candidates=_CANDIDATES,
        )

    return SelectionDecision(
        matched=True, flow=flow, device=device, subject=subject,
        reason=f"matched {flow} language ({', '.join(matched_names)})",
        matched_patterns=matched_names,
    )
