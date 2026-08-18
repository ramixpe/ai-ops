"""The MCP boundary: nothing leaves here carrying unparsed device text.

Invariant 4 — *no unparsed device text ever reaches a model* — was enforced
structurally in `prompt_library`, which takes a `DescentResult` and therefore
**cannot** receive raw text (OBS-061). That protects one path to a model.

`mcp_server/server.py` is a second path, and it never got the same treatment.
Audited 2026-08-17: **14 of 20 tools returned raw device output** under
`data.commands`, up to 37,962 characters for one `get_lab_logging` call — the
entire unshaped device log buffer, which is precisely what `log_window.py`
exists to filter and `coverage.py` exists to bound.

The invariant was true of the code and false of the surface. When those tools
were written the only consumer was our own code, which reads `data.parsed` and
ignores `data.commands`, so nothing was wrong until a new kind of consumer
arrived that hands the *whole* return to a model.

Why this is one function and not an argument or a wrapper
-----------------------------------------------------------
Both alternatives make the guarantee depend on the caller remembering, and the
caller here is a model. :func:`sanitize` is applied by the **registration
decorator**, so a tool is sanitised by the act of being registered:

* a tool author cannot forget it — there is no code path that registers a tool
  without it;
* a tool added later inherits it by construction, with no diff to this file.

Same move as deleting the write imports from the server module (OBS-106): a
boundary that cannot emit raw text cannot be made to.

Withheld, not deleted
----------------------
A stripped key is replaced by a record that text existed and how much. Silently
dropping it would make a command that ran and produced 6 kB indistinguishable
from one that produced nothing — absence-as-health, in the one place where the
reader is a model that cannot check. So the model is told *what it is not being
shown*, which is the same discipline as `coverage.gaps()` and
`unaccounted_lines` themselves.
"""

from __future__ import annotations

from typing import Any

# mcp_server depends on agent_nettools (never the reverse), so the projector's
# free-text table and quoter are imported rather than duplicated. The MCP
# client IS a model consumer, so the same B-467 treatment the model-egress
# paths got must apply here -- a gap the 2026-08-18 holistic review found: raw
# `commands` were withheld, but device-authored free text under `parsed`
# (a syslog line's `text`, a BGP `last_reset_reason`, an interface
# `description`) reached the client completely unmarked.
from agent_nettools.model_egress import (
    FREE_TEXT_FIELDS as _PROJECTOR_FREE_TEXT_FIELDS,
)
from agent_nettools.model_egress import (
    quote_device_text,
)

#: Free-text field NAMES, drawn from the projector's (context, field) table.
#: The MCP boundary walks a generic payload and does not know the intent
#: context, so it matches on field name alone -- the same fallback the
#: projector documents as acceptable, and here it is the only option.
_FREE_TEXT_FIELD_NAMES = frozenset(field for _context, field in _PROJECTOR_FREE_TEXT_FIELDS)

__all__ = [
    "ERROR_KINDS",
    "MAX_ERROR_CHARS",
    "RAW_TEXT_KEYS",
    "sanitize",
]

#: Keys whose values are, or can contain, **unparsed device text**. Declared as
#: a table rather than detected, the same discipline as
#: `template_parsers.IgnoreRule`, `log_window.NoiseRule` and `interface_kind`.
#:
#: ``commands``           ``{rendered command: raw output}`` — every intent and
#:                        template envelope carries one. The main vector.
#: ``unaccounted_lines``  §0.10's remainder: lines no template group and no
#:                        declared `IgnoreRule` matched. **Raw device lines by
#:                        definition.** Empty across the whole committed corpus
#:                        and nested under ``data.parsed.meta`` rather than at
#:                        the top of ``parsed``, which is exactly why the first
#:                        pass of the audit measured zero for it. It is a
#:                        property of the *parser contract*, not of these
#:                        fixtures, and another platform will fill it.
#:
#: ``unparsed_rows`` is **deliberately not here**. It looks like a sibling of
#: ``unaccounted_lines`` and reads like one in the parser docs, but it is an
#: ``int`` -- a count of rows a parser recognised and could not read, never
#: their text. Stripping it would destroy a diagnostic for no safety gain, and
#: the resemblance is exactly the sort of thing that gets swept up by a rule
#: written from a name rather than from a measured type.
RAW_TEXT_KEYS = frozenset({"commands", "unaccounted_lines"})

#: Error strings are kept — a model that cannot see failures is worse than one
#: that sees none — but they are **classified and rebuilt**, never truncated
#: (B-458).
#:
#: Truncation was the first mitigation and it was a bound, not a fix: it still
#: passed up to 400 characters of whatever the exception carried. And the
#: exception does carry device output — measured in netmiko 4.7's
#: `base_connection`, one `ReadException` message interpolates
#: ``output={repr(output)}`` directly. That is the residual this closes.
#:
#: The safe structure of one of our error strings is ``"<command>: <detail>"``:
#: the command is **ours**, rendered by reconstruction and already validated,
#: and the detail is the part that came from somewhere else. So the command is
#: kept verbatim, the detail is matched against a declared table of kinds, and
#: anything unmatched is dropped rather than trimmed.
MAX_ERROR_CHARS = 400

#: What a transport failure can be, declared rather than pattern-guessed from
#: the text. Same discipline as `template_parsers.IgnoreRule` and
#: `log_window.NoiseRule`: a reviewable table, and anything not in it is
#: **withheld**, not truncated.
#:
#: Ordered — the first match wins, so the specific entries precede the general
#: ones.
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
    """What a model is told instead of the output."""

    return {
        command: {
            "withheld": "raw device text is not sent to a model (invariant 4)",
            "chars": len(output) if isinstance(output, str) else 0,
            "lines": len(output.splitlines()) if isinstance(output, str) else 0,
        }
        for command, output in commands.items()
    }


def _classify_errors(errors: list) -> list:
    """Rebuild each error from its command and a declared kind (B-458).

    **Rebuilt, not filtered.** The output is assembled from two things this
    module already trusts — a command we rendered, and a phrase from
    :data:`ERROR_KINDS` — so there is no path by which text from the device
    reaches the result, whatever the exception contained. That is the same
    argument as `prompt_library` never holding device text: a function that does
    not carry the dangerous value cannot leak it.

    An unmatched detail is **withheld entirely**. A truncated unknown is still
    an unknown, and the reason a model needs is the *kind*, not the prose.
    """

    out = []
    for error in errors:
        text = str(error)
        command, _, detail = text.partition(": ")
        if not detail:
            command, detail = "", text

        # Match ERROR_KINDS against the WHOLE text, not just the post-colon
        # detail. The partition on the first ": " misfired on messages whose
        # own text contains a colon before the kind phrase -- e.g.
        # "Required environment variable is not set: DEVICE_USERNAME" put the
        # kind on the wrong side and fell through to "unclassified" (2026-08-18
        # invariant audit). The kind tokens are distinctive; the command prefix
        # is preserved separately for the message, so matching the full text
        # costs nothing and closes the dead-entry gap.
        lowered = text.lower()
        kind = next((phrase for token, phrase in ERROR_KINDS if token in lowered), None)

        if kind is None:
            kind = (
                "an unclassified error; its detail is withheld because a transport "
                "exception can embed device output"
            )
        out.append(f"{command}: {kind}" if command else kind)
    return out


def sanitize(payload: Any) -> Any:
    """Return ``payload`` with every raw-text key replaced by a withheld record.

    Recursive and total: it walks dicts and lists to any depth, because a
    fabric-wide result nests one envelope per device and a diff nests per
    intent. **It never mutates the input** — the CLI shares these functions and
    keeps its raw text, which is its receipt for a human reader.

    ``data.parsed`` is left intact. That is the whole point: structured records
    are what a model should reason over, and they are what every one of these
    tools already produces beside the text nobody was reading.
    """

    if isinstance(payload, dict):
        clean: dict[str, Any] = {}
        for key, value in payload.items():
            if key == "commands" and isinstance(value, dict):
                clean["commands_withheld"] = _withheld_commands(value)
            elif key in RAW_TEXT_KEYS:
                # `unaccounted_lines`: the count is the signal a reader needs
                # (something went unread); the lines themselves are the text.
                clean[f"{key}_withheld"] = len(value) if isinstance(value, (list, tuple)) else 1
            elif key == "errors" and isinstance(value, list):
                clean[key] = _classify_errors(value)
            elif key in _FREE_TEXT_FIELD_NAMES and isinstance(value, str):
                # Device-authored, attacker-influenceable free text (B-467).
                # Wrapped in the projector's untrusted-content delimiters so an
                # MCP client reads it as data, never instruction -- the same
                # bound the model-egress paths already apply.
                clean[key] = quote_device_text(value)
            else:
                clean[key] = sanitize(value)
        return clean

    if isinstance(payload, list):
        return [sanitize(item) for item in payload]

    return payload
