"""PromptBuilder tests"""

import pytest

from mocode.prompt import PromptBuilder, PromptContributions, Section


class TestPromptBuilder:
    def test_add_and_build(self):
        builder = PromptBuilder()
        builder.add(Section(name="intro", priority=10, render=lambda ctx: "Hello"))
        result = builder.build()
        assert "Hello" in result

    def test_priority_sorting(self):
        builder = PromptBuilder()
        builder.add(Section(name="second", priority=20, render=lambda ctx: "B"))
        builder.add(Section(name="first", priority=10, render=lambda ctx: "A"))
        result = builder.build()
        assert result.index("A") < result.index("B")

    def test_disable_section(self):
        builder = PromptBuilder()
        builder.add(Section(name="visible", priority=10, render=lambda ctx: "Yes"))
        builder.add(Section(name="hidden", priority=20, render=lambda ctx: "No"))
        builder.disable("hidden")
        result = builder.build()
        assert "Yes" in result
        assert "No" not in result

    def test_enable_section(self):
        builder = PromptBuilder()
        builder.add(Section(name="s", priority=10, render=lambda ctx: "Content"))
        builder.disable("s")
        assert "Content" not in builder.build()
        builder.enable("s")
        assert "Content" in builder.build()

    def test_remove_section(self):
        builder = PromptBuilder()
        builder.add(Section(name="remove_me", priority=10, render=lambda ctx: "X"))
        builder.remove("remove_me")
        assert builder.build() == ""

    def test_context_passing(self):
        builder = PromptBuilder()
        builder.add(Section(
            name="ctx_test",
            priority=10,
            render=lambda ctx: f"cwd={ctx.get('cwd', 'none')}",
        ))
        result = builder.context(cwd="/home/user").build()
        assert "cwd=/home/user" in result

    def test_empty_render_skipped(self):
        builder = PromptBuilder()
        builder.add(Section(name="empty", priority=10, render=lambda ctx: ""))
        builder.add(Section(name="real", priority=20, render=lambda ctx: "Here"))
        result = builder.build()
        assert result == "Here"

    def test_get_section(self):
        builder = PromptBuilder()
        builder.add(Section(name="findme", priority=10, render=lambda ctx: "x"))
        assert builder.get_section("findme") is not None
        assert builder.get_section("nope") is None

    def test_fluent_api(self):
        builder = PromptBuilder()
        result = (
            builder
            .add(Section(name="a", priority=10, render=lambda ctx: "A"))
            .add(Section(name="b", priority=20, render=lambda ctx: "B"))
            .context(key="value")
        )
        assert result is builder


class TestPromptContributions:
    def test_default_empty(self):
        pc = PromptContributions()
        assert pc.add == []
        assert pc.disable == []
        assert pc.replace == {}

    def test_with_sections(self):
        section = Section(name="plugin_section", priority=5, render=lambda ctx: "Plugin")
        pc = PromptContributions(add=[section], disable=["soul"], replace={"tools": section})
        assert len(pc.add) == 1
        assert "soul" in pc.disable
        assert "tools" in pc.replace
