"""PromptBuilder tests"""

import pytest

from mocode.prompt import PromptBuilder, Section, xml_tag


class TestXmlTag:
    def test_basic(self):
        result = xml_tag("tag", "content")
        assert result == "<tag>\ncontent\n</tag>"

    def test_with_attrs(self):
        result = xml_tag("tag", "x", file="a.md")
        assert result == '<tag file="a.md">\nx\n</tag>'

    def test_multiple_attrs(self):
        result = xml_tag("tag", "x", a="1", b="2")
        assert 'a="1"' in result
        assert 'b="2"' in result
        assert result.startswith("<tag ")
        assert result.endswith("</tag>")

    def test_empty_content(self):
        result = xml_tag("tag")
        assert result == "<tag></tag>"

    def test_empty_content_with_attrs(self):
        result = xml_tag("env", key="val")
        assert result == '<env key="val"></env>'


class TestPromptBuilder:
    def test_add_and_build(self):
        builder = PromptBuilder()
        builder.add(Section(name="intro", priority=10, render=lambda ctx: "Hello"))
        result = builder.build()
        assert "Hello" in result
        assert "<system-prompt>" not in result

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
        result = builder.build()
        assert "X" not in result

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
        assert "Here" in result
        assert "<system-prompt>" not in result

    def test_section_lookup(self):
        builder = PromptBuilder()
        builder.add(Section(name="findme", priority=10, render=lambda ctx: "x"))
        assert builder.section("findme") is not None
        assert builder.section("nope") is None

    def test_fluent_api(self):
        builder = PromptBuilder()
        result = (
            builder
            .add(Section(name="a", priority=10, render=lambda ctx: "A"))
            .add(Section(name="b", priority=20, render=lambda ctx: "B"))
            .context(key="value")
        )
        assert result is builder

    def test_build_text_format(self):
        builder = PromptBuilder()
        builder.add(Section(name="a", priority=10, render=lambda ctx: "Hello"))
        result = builder.build(format="text")
        assert result == "Hello"

    def test_build_xml_format(self):
        builder = PromptBuilder()
        builder.add(Section(name="greeting", priority=10, render=lambda ctx: "Hello"))
        result = builder.build(format="xml")
        assert "<system-prompt>" in result
        assert "</system-prompt>" in result
        assert "<greeting>" in result
        assert "</greeting>" in result

    def test_build_xml_custom_wrap(self):
        builder = PromptBuilder()
        builder.add(Section(name="msg", priority=10, render=lambda ctx: "Hi"))
        result = builder.build(format="xml", wrap="custom-prompt")
        assert "<custom-prompt>" in result
        assert "</custom-prompt>" in result
        assert "<system-prompt>" not in result

    def test_section_attrs_in_xml_mode(self):
        builder = PromptBuilder()
        builder.add(Section(
            name="soul", priority=10,
            render=lambda ctx: "content",
            attrs={"file": "/path/to/soul.md"},
        ))
        result = builder.build(format="xml")
        assert 'file="/path/to/soul.md"' in result
        assert "<soul" in result

    def test_section_attrs_not_in_text_mode(self):
        builder = PromptBuilder()
        builder.add(Section(
            name="soul", priority=10,
            render=lambda ctx: "content",
            attrs={"file": "/path/to/soul.md"},
        ))
        result = builder.build(format="text")
        assert "file=" not in result
        assert result == "content"

    def test_multiple_sections_xml(self):
        builder = PromptBuilder()
        builder.add(Section(name="a", priority=10, render=lambda ctx: "AAA"))
        builder.add(Section(name="b", priority=20, render=lambda ctx: "BBB"))
        result = builder.build(format="xml")
        assert result.index("<a>") < result.index("<b>")
        assert "<system-prompt>" in result
