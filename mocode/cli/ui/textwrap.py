"""Text wrapping utilities with ANSI code preservation and CJK support."""

import re

ANSI_ESCAPE = re.compile(r'\033\[[0-9;]*m')


def display_width(text: str) -> int:
    """Calculate display width (CJK chars take 2 width, ANSI codes take 0)."""
    width = 0
    in_ansi = False
    for char in text:
        if char == '\033':
            in_ansi = True
        elif in_ansi:
            if char == 'm':
                in_ansi = False
        elif char == '\n':
            pass
        elif (
            "\u4e00" <= char <= "\u9fff"
            or "\u3000" <= char <= "\u303f"
            or "\uff00" <= char <= "\uffef"
            or "\u3040" <= char <= "\u309f"
            or "\u30a0" <= char <= "\u30ff"
        ):
            width += 2
        else:
            width += 1
    return width


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_ESCAPE.sub('', text)


def wrap_text(text: str, width: int, initial_indent: str = "", subsequent_indent: str = "") -> list[str]:
    """Wrap text to specified width, preserving ANSI codes.

    Returns list of wrapped lines.
    """
    # Split on newlines first
    paragraphs = text.split('\n')
    result = []

    for para in paragraphs:
        if not para.strip():
            result.append('')
            continue

        lines = _wrap_paragraph(para, width, initial_indent, subsequent_indent)
        result.extend(lines)

    return result


def _wrap_paragraph(text: str, width: int, initial_indent: str, subsequent_indent: str) -> list[str]:
    """Wrap a single paragraph."""
    lines = []
    current = ""
    current_width = display_width(initial_indent)

    # Split into words while preserving ANSI codes
    words = _split_words(text)

    for word, word_w in words:
        test_width = current_width + (1 if current else 0) + word_w

        if current and test_width > width:
            # Wrap to new line
            indent = subsequent_indent if lines else initial_indent
            lines.append(indent + current)
            current = word
            current_width = display_width(subsequent_indent) + word_w
        else:
            if current:
                current += " " + word
                current_width += 1 + word_w
            else:
                current = word
                current_width = display_width(initial_indent) + word_w

    if current:
        indent = subsequent_indent if lines else initial_indent
        lines.append(indent + current)

    return lines


def _split_words(text: str) -> list[tuple[str, int]]:
    """Split text into (word, display_width) tuples, preserving ANSI codes."""
    words = []
    current = ""
    in_ansi = False

    for char in text:
        if char == '\033':
            in_ansi = True
            current += char
        elif in_ansi:
            current += char
            if char == 'm':
                in_ansi = False
        elif char in ' \t':
            if current:
                words.append((current, display_width(current)))
                current = ""
        else:
            current += char

    if current:
        words.append((current, display_width(current)))

    return words
