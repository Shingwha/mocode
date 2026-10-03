"""The host's small I/O and text helpers.

``host.io`` keeps every read best-effort and every write atomic-ish;
``host.text`` turns bytes a shell handed us into a string and collapses
a heading to one line. Both are load-bearing for every host surface,
and both are small enough to test exhaustively.
"""

from __future__ import annotations

import json

import pytest

from mocode.host.io import read_json, write_json
from mocode.host.text import decode_bytes, one_line


class TestJsonRoundTrip:
    """write_json / read_json — a round trip keeps what was written."""

    def test_a_round_trip_keeps_the_data(self, tmp_path):
        p = tmp_path / "nested" / "cfg.json"
        write_json(p, {"a": 1, "nested": {"b": [1, 2, 3]}, "s": "中文"})
        assert read_json(p) == {"a": 1, "nested": {"b": [1, 2, 3]}, "s": "中文"}

    def test_the_written_file_is_indented_and_unicode_is_kept(self, tmp_path):
        p = tmp_path / "a.json"
        write_json(p, {"x": "值"})
        text = p.read_text(encoding="utf-8")
        assert '"值"' in text  # ensure_ascii=False — human-editable
        assert text.startswith("{\n")  # indent=2


class TestBrokenReads:
    """A read never raises — a bad file reads as nothing, not as a crash."""

    def test_a_missing_file_is_none(self, tmp_path):
        assert read_json(tmp_path / "nope.json") is None

    def test_a_broken_file_is_none(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert read_json(p) is None
        assert read_json(tmp_path) is None  # a directory, not an error



class TestDecodeBytes:
    """decode_bytes — utf-8 first, the Chinese shell codecs as the fallback."""

    def test_utf_8_is_decoded_and_empty_is_nothing(self):
        assert decode_bytes("中文 ok".encode("utf-8")) == "中文 ok"
        assert decode_bytes(b"") == ""

    def test_a_cp936_sequence_falls_back_past_utf_8(self):
        data = "任务完成".encode("gbk")  # invalid as utf-8, valid as cp936
        assert decode_bytes(data) == "任务完成"

    def test_undecodable_bytes_come_back_replaced_not_crashed(self):
        text = decode_bytes(b"\xff\xfe\xfa")
        assert isinstance(text, str)
        assert "\ufffd" in text


class TestOneLine:
    """one_line — the cut never exceeds the limit, the ellipsis marks it."""

    def test_a_text_at_or_under_the_limit_is_left_alone(self):
        assert one_line("short", 10) == "short"
        assert one_line("12345", 5) == "12345"

    def test_a_cut_keeps_the_limit_exactly(self):
        assert one_line("abcdefghij", 5) == "abcd…"
        assert len(one_line("abcdefghij", 5)) == 5
        assert one_line("abcdef", 4) == "abc…"
