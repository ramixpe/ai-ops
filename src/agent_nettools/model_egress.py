"""The model-egress projector: the one function every model path must call.

DEEP-REVIEW-2026-08-17 §2.1 (which subsumes the expert peer review's P0-02
and P2-06) named the gap this module closes: **parsing bounds structure, not
content.** `mcp_server/boundary.py` withholds `data.commands` -- the *raw*
device buffer -- and calls the parsed structure it leaves behind safe, "on
the stated ground that structured records are what a model should reason
over." That ground is only half true. A parsed `logging` record still
carries a `text` field holding the syslog line's own prose, verbatim --
**17,916 characters of it, measured through one sanitised `get_lab_logging`
return** -- and a syslog line is something an unauthenticated attacker can
write from the network (a failed SSH login embeds the attacker-chosen
username; crafted traffic produces crafted log lines). Parsing tells you the
record has a `text` field of type `str`. It says nothing about what is
*in* that field. Three internal paths handed the whole parsed structure, raw
text included, to a model with no typing at all:
`llm_analysis.build_analysis_prompt`/`_anthropic_user_content` (the full
evidence dict, `data.commands` and all), `evidence_budget._section_text`
(a raw-command fallback after a failed parse, by prior test-pinned design),
and `prompt_library.build_correlate_prompt` (every shaped log record's
`text`, unmarked, on the *trusted* deterministic-descent path). Filed as
**B-467** (the free-text typing) and **B-470** (routing every path through
one projector rather than three ad hoc fixes); this module is both.

The fix is not to strip free text -- correlation is impossible without the
event text, and `prompt_library`'s whole reason to exist is to give a model
what a deterministic walk cannot: *when* something happened and whether it
coincided with anything else. The fix is to **type** it: a free-text device
field is wrapped in an explicit untrusted-content quote block at the point it
leaves this package, so nothing downstream can mistake device prose for the
tool's own instructions, and every projection counts and caps how much of it
it is willing to spend.

Two independent guarantees, not one
------------------------------------
`RAW_TEXT_KEYS` handles what `boundary.py` already handles: keys whose
*entire* value is unparsed device text (`commands`, `unaccounted_lines`).
Those are withheld outright, exactly as `boundary.sanitize` withholds them --
copied here, not imported, because `src/agent_nettools` must not depend on
`mcp_server` (the dependency runs the other way: `mcp_server` is a consumer
of this package). `errors` gets the same treatment: classified against a
copy of `boundary.ERROR_KINDS`, never passed through raw, because a
transport exception can embed device output (measured in netmiko 4.7's
`base_connection`: one `ReadException` interpolates `output={repr(output)}`
directly).

`FREE_TEXT_FIELDS` handles what `boundary.py` does not: specific fields
*within* an otherwise-structured parsed record that are themselves
device-authored prose. These are not withheld -- a model that cannot see
`text` cannot correlate a finding against a timeline, which is the one job
`prompt_library.build_correlate_prompt` exists to do -- they are quoted, so a
model reading them is told, structurally, that this span is data and not
instruction, and budgeted, so one verbose device does not consume the whole
prompt in device-authored prose nobody asked for.

**The quoting is a mitigation for prompt injection, not a proof against it.**
An instruction-following model reading text an attacker chose is a risk no
delimiter fully closes -- the delimiters make the untrusted span
*identifiable* and give every prompt built from this projector one line
telling the model not to follow instructions found inside it, which is what
today's model safety training and system-prompt instructions can actually
enforce. What contains the damage if that mitigation is defeated is what
`grounding.py` already does on the trusted path: `ground_correlation`
requires every timeline entry to cite a real timestamp and matching
mnemonic, and the paraphrase/correlation is non-authoritative and withheld on
failure. An injected log line can steer non-authoritative prose or burn
tokens; it cannot fabricate a cited finding. This module does not change
that containment -- it exists because *reaching* the model with unmarked
device prose at all was the gap, independent of what happens downstream.

What "record-context" means here, and why it stops at the envelope
----------------------------------------------------------------------
`FREE_TEXT_FIELDS` is a table of `(context, field)` pairs, not field names
alone -- a bare `"text"` would either over-match (withholding a field that
happens to share a name elsewhere) or under-match nothing new, since the
project's field vocabulary is small enough that name collisions are the real
risk. `context` is the *envelope's* intent/template name -- `data.intent`
for a base intent (`facts`, `interfaces`, `bgp`, ...), `data.template` for a
Phase 5 parameterized template (`logging`, `bgp_neighbor`, `interface`) --
read once per envelope in `_envelope_context` and threaded through the
recursive walk. It is deliberately **not** finer-grained than that: within
one intent/template's `parsed` structure there is no field-name collision
between `meta` and `records[i]` (`text` only ever appears inside a
`logging` record, `description` only ever inside an `interface` template's
`meta`), so a context parameter that could distinguish *where inside* one
envelope a field sits would buy nothing this table does not already prevent,
at the cost of threading a path argument through every recursive call.
Matching on field name alone, scoped to the one context this projector
already has in hand, is the documented choice B-470 allows when finer
context is impractical.

Two corrections against DEEP-REVIEW-2026-08-17 §2.1's illustrative table,
made by checking `template_parsers.py` directly rather than trusting the
review's field names verbatim -- see `FREE_TEXT_FIELDS`'s own comment for
the detail. Copying the spec's spelling unchecked would have shipped a table
that silently protected nothing for two of its four entries.

Non-mutating, like `boundary.sanitize`
-----------------------------------------
Neither `project_envelope` nor `project_evidence` mutates its input --
every dict and list is rebuilt, never modified in place, the same
discipline `boundary.sanitize` uses and for the same reason: a CLI caller
that shares the same evidence object with a human-facing renderer must keep
its raw text, which is that renderer's receipt.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DEFAULT_FREE_TEXT_BUDGET",
    "DEVICE_TEXT_CLOSE",
    "DEVICE_TEXT_OPEN",
    "ERROR_KINDS",
    "FREE_TEXT_FIELDS",
    "RAW_TEXT_KEYS",
    "project_envelope",
    "project_evidence",
    "quote_device_text",
]

#: Keys whose *entire* value is unparsed device text. Identical to
#: `mcp_server.boundary.RAW_TEXT_KEYS` -- see that module's docstring for
#: the full rationale (``commands`` is the main vector; ``unaccounted_lines``
#: is §0.10's remainder, nested under ``data.parsed.meta``). Not imported
#: from there: `mcp_server` depends on `agent_nettools`, never the reverse,
#: so the table is copied. Kept as a frozenset literal, not a re-derivation,
#: so a diff to one is a diff a reviewer can compare to the other by eye.
RAW_TEXT_KEYS = frozenset({"commands", "unaccounted_lines"})

#: (record-context, field) pairs that legitimately carry device-authored free
#: text a model NEEDS (correlation is impossible without event text).
#: Declared as a table, not detected -- the same discipline as
#: `template_parsers.IgnoreRule`, `log_window.NoiseRule`, and
#: `RAW_TEXT_KEYS`/`ERROR_KINDS` below.
#:
#: "record-context" is the *envelope's* intent/template name (`data.intent`
#: or `data.template` -- see `_envelope_context`), not the name of whatever
#: sub-record a field sits inside within that envelope's `parsed` structure.
#: See the module docstring's "What record-context means" section for why
#: stopping at envelope granularity is a documented choice, not a shortcut.
#:
#: Two corrections against DEEP-REVIEW-2026-08-17 §2.1's illustrative table:
#: * BGP's free-text field is **`last_reset_reason`** (the prose after
#:   "due to" -- `_LAST_RESET` in `template_parsers.py`), not `last_reset`,
#:   which does not exist as a field anywhere in this codebase. The sibling
#:   field `last_reset_ago` is a duration, not prose, and is deliberately
#:   left out of this table.
#: * The per-interface template is named **`interface`** (singular) in
#:   `TEMPLATE_PARSERS`/`data.template` (`run_template(device, "interface",
#:   ...)`), not `interfaces`. `interfaces` (plural) is the unrelated base
#:   intent (`show interfaces brief`, parsed by `parsers.py`, not
#:   `template_parsers.py`) and carries no free text at all -- a table
#:   using the review's literal spelling would key on a context that never
#:   occurs and protect nothing.
#: `("logging", "code")` is included even though `code` is a short token
#: (the mnemonic's trailing segment, e.g. `ADJCHANGE`) rather than prose --
#: it is still device-chosen text from the same untrusted line `text` came
#: from, and the budget cost of quoting it is negligible.
FREE_TEXT_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        ("logging", "text"),
        ("logging", "code"),
        ("bgp_neighbor", "last_reset_reason"),
        ("interface", "description"),
    }
)

#: Delimiters wrapped around one free-text value. Deliberately unlikely to
#: occur in ordinary device output (mixed case, punctuation no IOS-XR
#: mnemonic or syslog line uses) while still being plain ASCII a model reads
#: as a literal marker rather than markup it might interpret. See
#: `quote_device_text` for what happens when a device line contains these
#: strings itself.
DEVICE_TEXT_OPEN = "<<<DEVICE-TEXT untrusted>>>"
DEVICE_TEXT_CLOSE = "<<<END-DEVICE-TEXT>>>"

#: Characters of device free text one projection (one `project_envelope`
#: call) will quote before withholding the rest. Deliberately generous: a
#: handful of log lines or one BGP reset reason should never be touched, and
#: this exists to bound the pathological case (a verbose description or a
#: device that logged the same line thousands of times), not the normal one.
DEFAULT_FREE_TEXT_BUDGET = 8000

#: Copied from `mcp_server.boundary.ERROR_KINDS`, not imported -- see
#: `RAW_TEXT_KEYS` above for why this package cannot depend on `mcp_server`.
#: **Both tables must change together.** `tests/test_model_egress.py`
#: asserts this tuple equals `mcp_server.boundary.ERROR_KINDS` exactly, so a
#: change to one without the other fails a test instead of silently
#: reopening, on one of the two egress surfaces, the hole the other already
#: closed.
ERROR_KINDS: tuple[tuple[str, str], ...] = (
    ("authentication", "authentication failed"),
    ("connection refused", "the device refused the connection"),
    ("timed out", "the read timed out"),
    ("read timeout", "the read timed out"),
    ("pattern not detected", "the device's prompt was not recognised before the timeout"),
    ("unable to successfully split output", "the response could not be split on the prompt"),
    ("no route to host", "the device was unreachable"),
    ("name or service not known", "the device's name did not resolve"),
    ("refusing unapproved", "the command was refused by the allowlist"),
    ("refusing unsafe rendered", "the rendered command was refused"),
    ("active probes are disabled", "active probes are disabled by configuration"),
    ("no such template", "no such template for this platform"),
    ("required environment variable", "a credential is not configured"),
    ("is not in the lab inventory", "the device is not in the inventory"),
    # --- Connection-establishment failures (2026-08-18 MCP re-test). -------
    #
    # netmiko's most common real failure is `NetmikoTimeoutException("TCP
    # connection to device failed.")`, and NOTHING in this table matched it --
    # so the single most likely thing to go wrong in production produced the
    # least useful message the system can emit. The operator hit it three
    # times in one session and learned only "an unclassified error".
    #
    # These are safe to name for a reason stronger than "the phrase looks
    # tool-authored": a connection-ESTABLISHMENT failure happens before any
    # session exists, so there is no device output yet that could be embedded
    # in it. The withholding rationale in this module's docstring -- that a
    # transport exception can interpolate `output=...` -- applies to reads on
    # an open channel, not to a refused or unanswered connect.
    ("tcp connection to device failed", "the device did not answer a TCP connection (unreachable, filtered, or wrong port)"),
    ("ssh negotiation", "the SSH negotiation failed before login"),
    ("connection reset", "the device reset the connection"),
    ("unable to connect", "the connection could not be opened"),
    ("host key", "the device's SSH host key was rejected"),
    # --- Parameter-validation refusals (added after wave 2-B). ------------
    #
    # These phrases originate in the FROZEN validators (`templates.py`) --
    # tool-authored, stable, and containing no device text. Before these
    # entries a validation refusal classified as "unclassified" and the model
    # lost the one thing the agent loop's own prompt tells it to act on: WHY
    # the call was refused. "Say so plainly rather than retrying the same
    # call unchanged" is only possible if the reason survives the boundary.
    # The KIND phrase is what crosses; the offending value never does.
    ("must not be empty", "the parameter was refused: it must not be empty"),
    ("exceeds the maximum length", "the parameter was refused: too long"),
    ("non-ascii characters are not allowed", "the parameter was refused: non-ASCII characters"),
    ("whitespace is not allowed", "the parameter was refused: whitespace is not allowed"),
    ("contains a control character", "the parameter was refused: control characters"),
    ("contains a forbidden character", "the parameter was refused: a forbidden character"),
    ("not a valid ipv4 address", "the parameter was refused: not a valid IPv4 address"),
    ("not a valid ipv4 prefix", "the parameter was refused: not a valid IPv4 prefix"),
    ("not a valid interface name", "the parameter was refused: not a valid interface name"),
    ("must be between", "the parameter was refused: out of range"),
    ("expected a string", "the parameter was refused: wrong type"),
    # --- Locally-generated selection errors (operator walkthrough 2026-08-18,
    # stumble 8). These messages are OUR text built from fixed registries --
    # "unknown intent 'bogus'; one of [...]" -- yet classified to the generic
    # withheld phrase, so neither a human nor a small navigator model learned
    # which values were valid. The kind strings below carry the valid sets as
    # STATIC text (the registries are fixed), so the classify-rebuild pattern
    # holds: nothing dynamic crosses, and the caller learns what to try next.
    ("unknown intent", "the intent was not recognised; valid: facts, interfaces, bgp, lldp, isis, sr"),
    ("unknown kind", "the kind was not recognised; valid: route, bgp_neighbor, interface, logging (probe_lab: ping, traceroute)"),
    ("unknown mode", "the mode was not recognised; valid: latest_diff, golden_diff, flaps"),
    ("unknown object type", "the flow was not recognised; implemented: bgp_session, interface"),
    ("unknown check", "the check was not recognised; valid: facts, interfaces, bgp, lldp, isis, sr"),
)


def _withheld_commands(commands: dict) -> dict:
    """What a model is told instead of raw command output.

    Same shape as `boundary._withheld_commands`: a record that text existed
    and how much, never the text itself. Withheld, not deleted -- absence
    would make a command that produced 6 kB indistinguishable from one that
    produced nothing, in the one place the reader cannot check.
    """

    return {
        command: {
            "withheld": "raw device text is not sent to a model (invariant 4, B-467/B-470)",
            "chars": len(output) if isinstance(output, str) else 0,
            "lines": len(output.splitlines()) if isinstance(output, str) else 0,
        }
        for command, output in commands.items()
    }


def _classify_errors(errors: list) -> list:
    """Rebuild each error from its command and a declared kind.

    Identical logic to `boundary._classify_errors` -- see that function's
    docstring for the full argument. Rebuilt, not filtered: the output is
    assembled only from a command this package rendered and a phrase from
    `ERROR_KINDS`, so no text from the device (or from a transport exception
    that embedded device output) can reach the result.
    """

    out = []
    for error in errors:
        text = str(error)
        command, _, detail = text.partition(": ")
        if not detail:
            command, detail = "", text

        # See mcp_server/boundary.py: match the whole text, not the post-":"
        # detail, so a message with an earlier colon still classifies.
        lowered = text.lower()
        kind = next((phrase for token, phrase in ERROR_KINDS if token in lowered), None)

        if kind is None:
            kind = (
                "an unclassified error; its detail is withheld because a transport "
                "exception can embed device output"
            )
        out.append(f"{command}: {kind}" if command else kind)
    return out


def quote_device_text(text: str) -> str:
    """Wrap one device-authored string in the untrusted-content delimiters.

    Any occurrence of either delimiter string is stripped from ``text``
    first -- a log line that happened to contain
    ``"<<<END-DEVICE-TEXT>>>"`` must not be able to close the block early
    and have whatever follows read as if it came from outside the quote.
    That is the one property this function exists to guarantee; see the
    module docstring for why the guarantee stops there (a mitigation for
    prompt steering, not a proof against it).
    """

    stripped = text.replace(DEVICE_TEXT_OPEN, "").replace(DEVICE_TEXT_CLOSE, "")
    return f"{DEVICE_TEXT_OPEN}\n{stripped}\n{DEVICE_TEXT_CLOSE}"


class _FreeTextBudget:
    """Mutable per-projection counter -- not part of the public API.

    A plain ``int`` local would work equally well; this exists only so
    `_project`'s recursive calls can share and decrement one counter without
    every call site returning and re-threading an updated integer.
    """

    __slots__ = ("remaining",)

    def __init__(self, budget: int) -> None:
        self.remaining = budget


def _quote_or_withhold(text: str, budget: _FreeTextBudget) -> Any:
    """Quote ``text`` if the projection's free-text budget has room, else
    withhold it -- never silently drop it.

    Checked *before* spending: once ``remaining`` reaches zero or below, every
    further free-text value in this projection is withheld whole, including
    ones that would themselves have fit. A single field is never truncated
    mid-string -- withholding a whole log line is honest about what the model
    is not seeing; a half a syslog message is not. Spending is charged against
    the *device text's* own length, not the wrapped/quoted length, since the
    budget's unit is "characters of device free text", not prompt bytes.
    """

    if budget.remaining <= 0:
        return {"withheld": "free-text budget exhausted", "chars": len(text)}
    budget.remaining -= len(text)
    return quote_device_text(text)


def _envelope_context(envelope: Any) -> str | None:
    """The intent/template name that scopes `FREE_TEXT_FIELDS` lookups.

    Every envelope `network_tools.py` produces carries its own name at
    `data.intent` (base intents: `run_intent`/`_attach_parsed`) or
    `data.template` (Phase 5 templates: `run_template`/
    `_attach_parsed_template`) -- never both. Anything else (a malformed or
    hand-built envelope in a test) yields `None`, which makes every
    `FREE_TEXT_FIELDS` lookup miss rather than raise: an envelope this
    projector cannot identify gets no free-text wrapping, only the
    unconditional `RAW_TEXT_KEYS`/`errors` handling below.
    """

    if not isinstance(envelope, dict):
        return None
    data = envelope.get("data")
    if not isinstance(data, dict):
        return None
    context = data.get("intent")
    if context is None:
        context = data.get("template")
    return context if isinstance(context, str) else None


def _project(value: Any, *, context: str | None, budget: _FreeTextBudget) -> Any:
    """The recursive walk both public functions are built from.

    Same structure as `boundary.sanitize`: total over dicts and lists to any
    depth, so a raw-text key or a free-text field is caught wherever it
    appears in the envelope, not just at a hardcoded depth.
    """

    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, val in value.items():
            if key == "commands" and isinstance(val, dict):
                clean["commands_withheld"] = _withheld_commands(val)
            elif key in RAW_TEXT_KEYS:
                # `unaccounted_lines`: the count is the signal a reader
                # needs; the lines themselves are raw device text.
                clean[f"{key}_withheld"] = len(val) if isinstance(val, (list, tuple)) else 1
            elif key == "errors" and isinstance(val, list):
                clean[key] = _classify_errors(val)
            elif isinstance(val, str) and context is not None and (context, key) in FREE_TEXT_FIELDS:
                clean[key] = _quote_or_withhold(val, budget)
            else:
                clean[key] = _project(val, context=context, budget=budget)
        return clean

    if isinstance(value, list):
        return [_project(item, context=context, budget=budget) for item in value]

    return value


def project_envelope(envelope: dict, *, free_text_budget: int | None = None) -> dict:
    """One tool/intent envelope -> model-safe projection.

    ``envelope`` is the shape every `network_tools.py` function returns:
    ``{"tool", "device", "status", "timestamp", "data": {...}, "errors": [...]}``,
    with ``data`` carrying ``commands``, ``parsed``, ``parse_status``, and
    ``intent`` or ``template``. ``commands``/``unaccounted_lines`` are
    withheld (see ``RAW_TEXT_KEYS``), ``errors`` is classified, and every
    ``parsed`` field matching ``FREE_TEXT_FIELDS`` for this envelope's own
    intent/template is quoted and counted against ``free_text_budget``
    (``DEFAULT_FREE_TEXT_BUDGET`` chars when not given). Everything else in
    ``parsed`` -- the vast majority of it -- passes through unchanged, because
    structured fields are exactly what a model should reason over; only the
    free-text minority needs marking.
    """

    budget = _FreeTextBudget(DEFAULT_FREE_TEXT_BUDGET if free_text_budget is None else free_text_budget)
    context = _envelope_context(envelope)
    return _project(envelope, context=context, budget=budget)


def project_evidence(evidence: dict, *, free_text_budget: int | None = None) -> dict:
    """A full `collect_evidence()` dict -> model-safe projection.

    Applies `project_envelope` to every intent section (`bgp`, `interfaces`,
    `logging`, ...); plain scalar fields at the top level (`device`,
    `platform`, `timestamp`) pass through untouched, the same "not every
    value is a section" check `evidence_budget.budget_device_evidence` and
    `mcp_server.boundary.sanitize` both already make.

    ``free_text_budget`` applies **per section** -- each intent's envelope is
    its own projection with its own fresh budget, not one budget shared
    across the whole fabric-wide evidence dict. `DEFAULT_FREE_TEXT_BUDGET` is
    sized for "how much free text is reasonable from one intent on one
    device", and a shared budget would let one verbose intent starve every
    other section's `logging` window of the budget it is entitled to on its
    own.
    """

    return {
        key: project_envelope(section, free_text_budget=free_text_budget)
        if isinstance(section, dict)
        else section
        for key, section in evidence.items()
    }
