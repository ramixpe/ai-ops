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

__all__ = [
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
#: that sees a truncated one — but bounded, because a transport exception can
#: embed accumulated device output in its message. This is the **known residual
#: vector** and it is bounded rather than claimed clean (B-458).
MAX_ERROR_CHARS = 400


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


def _truncate_errors(errors: list) -> list:
    out = []
    for error in errors:
        text = str(error)
        if len(text) > MAX_ERROR_CHARS:
            text = (
                text[:MAX_ERROR_CHARS]
                + f" […{len(text) - MAX_ERROR_CHARS} more characters withheld: a "
                f"transport error can embed device output]"
            )
        out.append(text)
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
                clean[key] = _truncate_errors(value)
            else:
                clean[key] = sanitize(value)
        return clean

    if isinstance(payload, list):
        return [sanitize(item) for item in payload]

    return payload
