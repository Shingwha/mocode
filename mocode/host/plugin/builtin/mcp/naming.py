"""MCP names and exposure — how servers and tools are named, and who sees them.

Names are predictable and collision-free (decision D7): every character
outside ``[A-Za-z0-9_]`` folds to ``_``, a tool's full name is
``mcp__<server>__<tool>``, and two raw tool names of one server that fold to
the same full name are disambiguated by a stable suffix — the colliding raws
sort, the first keeps the plain name and the rest get ``_<sha1(raw)[:6]>``.
A global counter is deliberately not used: the result must not depend on the
order a server happened to list its tools.

Exposure decides how a tool reaches the model (decision D2): the server
default is ``auto`` — ``codemode`` when the codemode plugin is enabled,
otherwise ``direct`` — a server's ``exposure`` overrides it, and
``toolExposure`` overrides per tool: exact names beat patterns, ``*`` is the
only wildcard and matches any characters, and the first matching pattern
wins. ``codemode-deferred`` is accepted as an alias of ``codemode``. Unknown
exposure values are reported and ignored, never fatal.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

from ...loader import report

if TYPE_CHECKING:
    from .config import McpServerConfig

#: Every character outside this set folds to '_' in mocode names.
_TOOL_NAME_PREFIX = "mcp__"

#: The exposure vocabulary (decision D2).
EXPOSURES = frozenset({"direct", "codemode", "deferred", "hidden"})
#: Accepted aliases — canonical exposure by alias.
_EXPOSURE_ALIASES = {"codemode-deferred": "codemode"}
#: exposure -> (Tool.availability, disabled). "hidden" registers the tool and
#: then disables it: invisible to both audiences and refused on a run.
EXPOSURE_AVAILABILITY: dict[str, tuple[str, bool]] = {
    "direct": ("both", False),
    "codemode": ("program", False),
    "deferred": ("program", False),
    "hidden": ("both", True),
}


def normalize(name: str) -> str:
    """Fold every character that is not alphanumeric or ``_`` to ``_``."""
    return "".join(c if c.isalnum() or c == "_" else "_" for c in name)


def fold_server_name(name: str) -> str:
    """The comparison key for server names.

    Names that differ only in ``-`` and ``_`` count as the same server — the
    configuration merge drops the weaker duplicate on this key.
    """
    return normalize(name)


def tool_full_name(server: str, tool: str) -> str:
    """The mocode tool name for an MCP tool: ``mcp__<server>__<tool>``."""
    return f"{_TOOL_NAME_PREFIX}{normalize(server)}__{normalize(tool)}"


def assign_tool_names(server: str, raw_names: list[str]) -> dict[str, str]:
    """Map each raw tool name to its full mocode name.

    Raw names whose normalized form collides are sorted; the first keeps the
    plain full name, the rest get ``_<sha1(raw)[:6]>`` appended. The hash is
    of the raw name alone and the sort is total, so the assignment is stable
    no matter which order the server listed its tools in.
    """
    by_norm: dict[str, list[str]] = {}
    for raw in raw_names:
        by_norm.setdefault(normalize(raw), []).append(raw)
    out: dict[str, str] = {}
    for norm, raws in by_norm.items():
        ordered = sorted(raws)
        for index, raw in enumerate(ordered):
            if index == 0:
                out[raw] = f"{_TOOL_NAME_PREFIX}{normalize(server)}__{norm}"
            else:
                digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
                out[raw] = f"{_TOOL_NAME_PREFIX}{normalize(server)}__{norm}_{digest}"
    return out


def canon_exposure(exposure: str) -> str:
    """Resolve an alias to its canonical exposure (unknown values pass through)."""
    return _EXPOSURE_ALIASES.get(exposure, exposure)


def default_exposure(mcp_config: object, *, codemode_enabled: bool) -> str:
    """The exposure a server gets unless its own configuration says otherwise.

    ``auto`` — the default — follows the codemode plugin: ``codemode`` when
    it is enabled, ``direct`` otherwise. Any other configured value is used
    as-is when it is a known exposure; a value outside the vocabulary is
    reported and treated as ``auto``.
    """
    raw: object = "auto"
    if isinstance(mcp_config, dict):
        raw = mcp_config.get("default_exposure", "auto")
    if not isinstance(raw, str):
        report("config.json: plugins.mcp.default_exposure must be a string — 'auto' is used")
        raw = "auto"
    if raw == "auto":
        return "codemode" if codemode_enabled else "direct"
    if canon_exposure(raw) in EXPOSURES:
        return raw
    report(
        f"config.json: unknown plugins.mcp.default_exposure {raw!r} — "
        "'auto' is used"
    )
    return "codemode" if codemode_enabled else "direct"


def _pattern_re(pattern: str) -> "re.Pattern[str]":
    """A pattern matcher where ``*`` is the only wildcard (no regex)."""
    return re.compile(re.escape(pattern).replace(r"\*", ".*") + r"\Z", re.DOTALL)


def resolve_exposure(
    cfg: "McpServerConfig", raw_tool: str, default: str, *, source: str = ""
) -> str:
    """The effective exposure of one tool.

    Precedence: an exact ``toolExposure`` name, then the first matching
    ``toolExposure`` pattern (``*`` matches any characters), then the
    server's own ``exposure``, then *default*. Unknown values report and
    fall through; the final fallback for a still-unknown default is
    ``direct`` so a tool always lands somewhere defined.
    """
    source = source or cfg.source or "mcp"

    server_exp: str | None = None
    if cfg.exposure is not None:
        server_exp = canon_exposure(cfg.exposure)
        if server_exp not in EXPOSURES:
            report(
                f"{source}: server {cfg.name!r}: unknown exposure "
                f"{cfg.exposure!r} — ignored"
            )
            server_exp = None

    tool_exp: str | None = None
    table = cfg.tool_exposure
    if table:
        candidate: str | None = None
        if raw_tool in table:
            candidate = table[raw_tool]
        else:
            for pattern, value in table.items():
                if "*" in pattern and _pattern_re(pattern).match(raw_tool):
                    candidate = value
                    break
        if candidate is not None:
            candidate = canon_exposure(candidate)
            if candidate in EXPOSURES:
                tool_exp = candidate
            else:
                report(
                    f"{source}: server {cfg.name!r}: unknown toolExposure "
                    f"value {candidate!r} for {raw_tool!r} — ignored"
                )

    effective = tool_exp or server_exp or canon_exposure(default)
    if effective not in EXPOSURES:
        report(
            f"{source}: unknown default exposure {default!r} for server "
            f"{cfg.name!r} — 'direct' is used"
        )
        effective = "direct"
    return effective


def availability_for(exposure: str) -> tuple[str, bool]:
    """(Tool.availability, disabled) for a resolved exposure — see
    :data:`EXPOSURE_AVAILABILITY`."""
    return EXPOSURE_AVAILABILITY[exposure]
