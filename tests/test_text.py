"""The terminal's text metrics — measured, cut, and flattened to one row.

No terminal involved: these assert measurements and cuts directly. The
flattening is here because of what it prevents — ``wcswidth`` reports -1 for a
control character, so a newline-bearing string that is not flattened first
slips past every width-based bound downstream.
"""

from __future__ import annotations

from mocode.cli.text import one_row


class TestOneRow:
    """A row is one row: a newline becomes a marker, not a cursor move."""

    def test_line_endings_become_the_two_character_marker(self):
        assert one_row("a\nb") == "a\\nb"
        assert one_row("a\r\nb") == "a\\nb"
        assert one_row("a\rb") == "a\\nb"

    def test_a_tab_becomes_a_space(self):
        assert one_row("if x:\n\ty()") == "if x:\\n y()"

    def test_plain_text_passes_through_untouched(self):
        assert one_row("read  a.py · 3 lines") == "read  a.py · 3 lines"

    def test_escape_sequences_survive(self):
        """Escape codes carry none of the three characters, so the marker
        replacement cannot split one."""
        styled = "\033[2mcodemode\033[0m"
        assert one_row(styled) == styled
