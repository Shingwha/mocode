"""Tests for spinner truncation algorithm."""

from __future__ import annotations

from mocode.app.cli.spinner import (
    Priority,
    Segment,
    Truncate,
    _truncate_segs,
)


class TestTruncateSegs:
    """测试统一截断算法。"""

    def test_low_priority_truncated_first(self):
        """低优先级 TAIL/MIDDLE 应先被截断。"""
        segs = [
            Segment("node", "step_1", Priority.HIGH, Truncate.TAIL),
            Segment("detail", "read(src/main.py), bash×2", Priority.LOW, Truncate.MIDDLE),
            Segment("thinking", "Thinking", Priority.NORMAL, Truncate.TAIL),
        ]
        # 空间只够显示高优先级和正常优先级
        result = _truncate_segs(segs, avail=30)

        # detail (LOW, MIDDLE) 应该被截断但保留
        assert any(s.id == "detail" for s in result)
        detail_seg = next(s for s in result if s.id == "detail")
        assert "..." in detail_seg.text

    def test_middle_truncated_preserves_head_tail(self):
        """MIDDLE 策略应保留首尾。"""
        segs = [
            Segment("detail", "read(src/very/long/path/to/some/deeply/nested/file.py)", Priority.LOW, Truncate.MIDDLE),
        ]
        result = _truncate_segs(segs, avail=20)
        detail_seg = next(s for s in result if s.id == "detail")
        assert "..." in detail_seg.text
        # Should preserve start and end
        assert detail_seg.text.startswith("read(")
        assert detail_seg.text.endswith(")")

    def test_truncates_high_priority_last(self):
        """高优先级只在最后才截断。"""
        segs = [
            Segment("node", "very_long_node_name_here", Priority.HIGH, Truncate.TAIL),
            Segment("detail", "info", Priority.LOW, Truncate.MIDDLE),
        ]
        # 空间非常有限
        result = _truncate_segs(segs, avail=15)

        # detail (LOW) 应该先被处理（截断到最小宽度后可能被移除）
        # node 应该被截断但保留有意义的内容
        node_seg = next(s for s in result if s.id == "node")
        assert "..." in node_seg.text
        assert len(node_seg.text) >= 8  # 最小宽度

    def test_no_truncation_when_enough_space(self):
        """空间充足时不做任何截断。"""
        segs = [
            Segment("a", "Hello", Priority.LOW, Truncate.TAIL),
            Segment("b", "World", Priority.NORMAL, Truncate.TAIL),
        ]
        result = _truncate_segs(segs, avail=100)

        assert len(result) == 2
        assert result[0].text == "Hello"
        assert result[1].text == "World"

    def test_none_truncate_never_removed(self):
        """NONE 策略的 segment 不应被移除或截断。"""
        segs = [
            Segment("fixed", "●○○", Priority.LOW, Truncate.NONE),
            Segment("flexible", "detail", Priority.LOW, Truncate.TAIL),
        ]
        result = _truncate_segs(segs, avail=5)

        # fixed 应该保留（NONE 不移除也不截断）
        assert any(s.id == "fixed" for s in result)
        fixed_seg = next(s for s in result if s.id == "fixed")
        assert fixed_seg.text == "●○○"

    def test_tail_removed_when_at_min_width(self):
        """TAIL segment 截断到最小宽度仍超出时应被移除。"""
        segs = [
            Segment("a", "very_long_text_here", Priority.LOW, Truncate.TAIL),
            Segment("b", "keep", Priority.HIGH, Truncate.TAIL),
        ]
        # 空间极端有限，a 会被截断到 min(8)，然后如果还超出就被移除
        result = _truncate_segs(segs, avail=5)

        # a (LOW) 应该被移除，b (HIGH) 保留
        assert not any(s.id == "a" for s in result)
        assert any(s.id == "b" for s in result)

    def test_middle_removed_when_at_min_width(self):
        """MIDDLE segment 截断到最小宽度仍超出时应被移除。"""
        segs = [
            Segment("a", "read(src/main.py), bash×2", Priority.LOW, Truncate.MIDDLE),
            Segment("b", "keep", Priority.HIGH, Truncate.TAIL),
        ]
        # 空间极端有限，a 的 min_w=12，如果 avail 小于总宽度且 a 截到12仍超出
        result = _truncate_segs(segs, avail=10)

        # a (LOW, MIDDLE) 被截到 min 12，总宽度仍超出，应被移除
        assert not any(s.id == "a" for s in result)
        assert any(s.id == "b" for s in result)

    def test_none_survives_extreme_pressure(self):
        """即使空间极端不足，NONE segment 也不会被截断或移除。"""
        segs = [
            Segment("spinner", "∘○∘", Priority.LOW, Truncate.NONE),
            Segment("tag", "Running tools", Priority.NORMAL, Truncate.TAIL),
            Segment("detail", "read(src/very/long/path/file.py)", Priority.LOW, Truncate.MIDDLE),
        ]
        result = _truncate_segs(segs, avail=3)

        # spinner (NONE) 始终保留
        assert any(s.id == "spinner" for s in result)
        spinner_seg = next(s for s in result if s.id == "spinner")
        assert spinner_seg.text == "∘○∘"
