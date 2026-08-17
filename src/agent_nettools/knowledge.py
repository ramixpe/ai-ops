"""The repo's own knowledge, exposed: search, mnemonics, operator notes. B-478.

No vectors, no embeddings, deliberately
-----------------------------------------
The corpus is ~1.6 MB of markdown across ~43 files. A retrieval stack for a
corpus grep answers in milliseconds would add the one thing this architecture
has none of — an unreviewable, non-deterministic layer between a question and
its source — to save nothing. Results cite ``path:line``, which an embedding
cannot give and an engineer can open. If the corpus ever grows two orders of
magnitude, revisit; not before (`OPS-WAVE-PLAN.md` §1).

Knowledge as declared items
-----------------------------
``data/mnemonics.yaml`` is a curated table — reviewed like code, seeded only
from mnemonics *measured* in this fabric's fixtures and the T-004 corpus. That
is the house pattern (`IgnoreRule`, `NoiseRule`, `ERROR_KINDS`,
`MNEMONIC_FLOW_TABLE`): knowledge an engineer wrote down and another reviewed,
never text a pipeline scraped.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .inventory_model import load_inventory_file

__all__ = [
    "MAX_QUERY_LENGTH",
    "explain_mnemonic",
    "load_mnemonic_table",
    "search_knowledge",
]

MAX_QUERY_LENGTH = 200

#: What search covers, relative to the repo root. Declared, not discovered —
#: a glob that silently widened would one day index something nobody meant to
#: expose through an MCP tool.
_SEARCH_ROOTS = ("docs", "README.md", "CONTRIBUTING.md", "SECURITY.md")


def _repo_root() -> Path | None:
    """Walk up from this file to the directory holding ``pyproject.toml``.

    ``None`` for an installed-without-source deployment — search then answers
    with a structured "docs not present", never a crash: absence of the corpus
    is a fact to report, not an error to raise.
    """

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "docs").is_dir():
            return parent
    return None


def _searchable_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for entry in _SEARCH_ROOTS:
        path = root / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.md")))
    return files


def search_knowledge(query: str, *, max_results: int = 8) -> dict[str, Any]:
    """Search the repo's documents and the inventory's operator notes.

    Plain case-insensitive term matching; the query is caller text, so it is
    length-capped and regex-escaped — a search tool must not be a regex-DoS
    surface. Heading lines score double: a term in a title is a better lead
    than the same term mid-paragraph. Ordering is deterministic
    (score, then path, then line).
    """

    query = (query or "").strip()[:MAX_QUERY_LENGTH]
    if not query:
        return {"query": query, "results": [], "note": "empty query"}

    terms = [re.escape(t) for t in query.lower().split() if t]
    root = _repo_root()

    results: list[dict[str, Any]] = []

    if root is not None:
        for path in _searchable_files(root):
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            rel = str(path.relative_to(root))
            for number, line in enumerate(lines, start=1):
                lowered = line.lower()
                hits = sum(1 for t in terms if re.search(t, lowered))
                if not hits:
                    continue
                score = hits * (2 if line.lstrip().startswith("#") else 1)
                start = max(0, number - 2)
                snippet = "\n".join(lines[start:number + 1]).strip()
                results.append({"source": f"{rel}:{number}", "score": score,
                                "snippet": snippet[:400]})

    # Inventory notes: the operator-authored facts that twice held answers
    # nobody read (OBS-139, the bgp_no_prefixes note). First-class citizens.
    try:
        for device in load_inventory_file().devices:
            for note in getattr(device, "notes", []) or []:
                text = note.note or ""
                lowered = text.lower()
                hits = sum(1 for t in terms if re.search(t, lowered))
                if hits:
                    results.append({
                        "source": f"inventory:{device.name}"
                                  + (f" (applies_to: {note.applies_to})" if note.applies_to else ""),
                        "score": hits * 2,  # a note about THIS fabric outranks prose about the project
                        "snippet": text[:400],
                    })
    except Exception:  # noqa: BLE001 -- no inventory readable: docs results still stand.
        pass

    results.sort(key=lambda r: (-r["score"], r["source"]))
    out: dict[str, Any] = {"query": query, "results": results[:max_results]}
    if root is None:
        out["note"] = ("the documentation corpus is not present in this "
                       "installation; only inventory notes were searched")
    return out


# --------------------------------------------------------------------------- #
# Mnemonics
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def load_mnemonic_table() -> tuple[dict[str, Any], ...]:
    path = Path(__file__).parent / "data" / "mnemonics.yaml"
    with open(path, encoding="utf-8") as handle:
        entries = yaml.safe_load(handle)
    return tuple(entries or ())


def explain_mnemonic(mnemonic: str) -> dict[str, Any]:
    """One mnemonic's curated record, or a structured "not in the table".

    The unknown case still parses the FACILITY-SEVERITY-CODE shape (the same
    right-anchored two-hyphen split ``template_parsers`` documents), so a
    caller learns the severity digit and facility even for a mnemonic nobody
    has written up — parts are cheap, meaning is curated.
    """

    cleaned = (mnemonic or "").strip().lstrip("%").upper()
    if not cleaned:
        return {"mnemonic": mnemonic, "known": False, "reason": "empty"}

    for entry in load_mnemonic_table():
        if entry.get("mnemonic", "").upper() == cleaned:
            return {"known": True, **entry}

    parts: dict[str, Any] = {}
    pieces = cleaned.rsplit("-", 2)
    if len(pieces) == 3 and pieces[1].isdigit():
        parts = {"facility": pieces[0], "severity": int(pieces[1]), "code": pieces[2]}
    return {
        "mnemonic": cleaned, "known": False, **parts,
        "reason": "not in the curated table — additions are reviewed like code "
                  "(src/agent_nettools/data/mnemonics.yaml)",
    }
