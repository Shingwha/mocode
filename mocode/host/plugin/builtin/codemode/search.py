"""Tool ranking for codemode discovery — a deterministic BM25-lite.

The algorithm is frozen (see the spec) so tests can assert exact order:
tokenize on ``[a-z0-9_]``, a query token hitting a tool's name scores 2 and
hitting its description scores 1 (both may apply to the same token), a tool
matching every query token gets a +1 bonus, unscored tools drop out, and the
result sorts by ``(-score, name)``.

The module is deliberately dependency-free — stdlib only — so it can be
reasoned about and tested in isolation.
"""

from __future__ import annotations

import re

__all__ = ["normalize", "preview", "rank"]

#: Catalogue entries preview their description at this many characters —
#: a description of exactly this length stays untouched, an empty one gets
#: no ellipsis, and ranking always reads the full text (the trim happens
#: at the catalogue's return boundary only).
_DESCRIPTION_PREVIEW_CHARS = 80

_TOKEN = re.compile(r"[a-z0-9_]+")


def normalize(name: str) -> str:
    """The folded form of a name: every character outside
    ``[A-Za-z0-9_]`` becomes ``_`` (pi's rule). Registered names arrive
    folded, so this is how another spelling is compared against one — the
    toolbox's did-you-mean candidates, and :func:`rank`'s namespace
    filter."""
    return re.sub(r"[^0-9A-Za-z_]", "_", name)


def preview(entry: dict) -> dict:
    """A catalogue entry with its description trimmed for the catalogue:
    past ``_DESCRIPTION_PREVIEW_CHARS`` the text is cut and an ellipsis
    marks the cut. The full description stays one ``describe_tool(name)``
    away."""
    description = entry["description"]
    if len(description) > _DESCRIPTION_PREVIEW_CHARS:
        description = description[:_DESCRIPTION_PREVIEW_CHARS] + "…"
    return {"name": entry["name"], "description": description}


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def rank(
    query: str,
    tools: list[dict],
    limit: int = 8,
    namespace: str | None = None,
) -> list[dict]:
    """Rank *tools* (``{"name", "description"}`` entries) against *query*.

    *namespace*, when given, keeps only tools of that MCP-style namespace —
    names starting with ``mcp__<normalized ns>__``. An empty query returns
    the first *limit* entries in registration order. The input list is not
    mutated; returned entries are the same dicts.
    """
    entries = list(tools)
    if namespace:
        prefix = f"mcp__{normalize(namespace)}__"
        entries = [t for t in entries if t["name"].startswith(prefix)]
    if not query or not query.strip():
        return entries[:limit]
    query_tokens = _tokens(query)
    scored: list[tuple[int, dict]] = []
    for entry in entries:
        name_tokens = set(_tokens(entry["name"]))
        desc_tokens = set(_tokens(entry["description"]))
        score = 0
        matched = 0
        for token in query_tokens:
            hit = False
            if token in name_tokens:
                score += 2
                hit = True
            if token in desc_tokens:
                score += 1
                hit = True
            matched += hit
        if matched == len(query_tokens):
            score += 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], item[1]["name"]))
    return [entry for _, entry in scored[:limit]]
