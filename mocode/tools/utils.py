"""Shared utilities for search tools — grep output formatting."""

from __future__ import annotations


def expand_context_indices(
    match_indices: list[int], total_lines: int, context: int
) -> list[int]:
    """Expand match indices to include surrounding context lines.

    Returns sorted list of all line indices to display (including matches and context).
    """
    if context <= 0:
        return match_indices
    expanded: set[int] = set()
    for idx in match_indices:
        for j in range(
            max(0, idx - context),
            min(total_lines, idx + context + 1),
        ):
            expanded.add(j)
    return sorted(expanded)


def format_grep_content(
    file_lines: list[str],
    match_indices: list[int],
    display_indices: list[int],
    display_path: str,
    pattern_str: str,
    max_results: int,
    hits: list[str],
) -> str:
    """Format grep content-mode output for one file's matches.

    Appends formatted hit lines to *hits* (mutated in-place).
    Returns the final formatted string if max_results is reached, else empty string.
    """
    match_set = set(match_indices)
    for idx in display_indices:
        sep = ":" if idx in match_set else "-"
        hits.append(f"{display_path}{sep}{idx + 1}{sep}{file_lines[idx]}")
        if len(hits) >= max_results:
            return (
                f"[Showing {len(hits)} matches for '{pattern_str}']\n"
                + "\n".join(hits)
            )
    return ""


def format_grep_files(files: list[str], pattern_str: str) -> str:
    """Format grep files-mode output."""
    if not files:
        return f"No files matching '{pattern_str}'"
    header = f"[Found {len(files)} file(s)]"
    return header + "\n" + "\n".join(files)


def format_grep_count(entries: list[str], pattern_str: str) -> str:
    """Format grep count-mode output. *entries* are 'path:count' strings."""
    if not entries:
        return f"No matches for '{pattern_str}'"
    return "\n".join(entries)
