"""Tool system tests — v0.2 (instance-scoped ToolRegistry)"""

import pytest

from mocode.tool import Tool, ToolError, ToolRegistry


class TestToolRegistry:
    def test_register_and_get(self, registry):
        t = Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"])
        registry.register(t)
        assert registry.get("echo") is t

    def test_unregister(self, registry):
        t = Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"])
        registry.register(t)
        assert registry.unregister("echo")
        assert registry.get("echo") is None

    def test_unregister_nonexistent(self, registry):
        assert not registry.unregister("nope")

    def test_run(self, registry):
        registry.register(Tool("add", "Add", {"a": "number", "b": "number"}, lambda a: str(a["a"] + a["b"])))
        assert registry.run("add", {"a": 1, "b": 2}) == "3"

    def test_run_unknown(self, registry):
        result = registry.run("unknown", {})
        assert "error" in result
        assert "unknown" in result

    def test_all_schemas(self, registry):
        registry.register(Tool("t1", "Tool 1", {"x": "string"}, lambda a: ""))
        registry.register(Tool("t2", "Tool 2", {"y": "number?"}, lambda a: ""))
        schemas = registry.all_schemas()
        assert len(schemas) == 2
        names = {s["function"]["name"] for s in schemas}
        assert names == {"t1", "t2"}

    def test_instances_are_independent(self):
        r1 = ToolRegistry()
        r2 = ToolRegistry()
        r1.register(Tool("x", "X", {}, lambda a: "r1"))
        assert r1.get("x") is not None
        assert r2.get("x") is None


class TestTool:
    def test_run_basic(self):
        t = Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"])
        assert t.run({"msg": "hello"}) == "hello"

    def test_run_error_handling(self):
        def bad_func(args):
            raise ToolError("something broke", "broken")

        t = Tool("bad", "Bad", {}, bad_func)
        result = t.run({})
        assert "error" in result
        assert "broken" in result

    def test_run_generic_exception(self):
        t = Tool("boom", "Boom", {}, lambda a: (_ for _ in ()).throw(ValueError("oops")))
        result = t.run({})
        assert "error" in result

    def test_validate_optional_args(self):
        t = Tool("opt", "Optional", {"name": "string", "nick?": "string"}, lambda a: a.get("nick", "none"))
        assert t.run({"name": "test"}) == "none"
        assert t.run({"name": "test", "nick": "n"}) == "n"

    def test_validate_default_args(self):
        t = Tool("def", "Default", {
            "name": "string",
            "count": {"type": "number", "default": 5}
        }, lambda a: str(a["count"]))
        assert t.run({"name": "test"}) == "5"
        assert t.run({"name": "test", "count": 10}) == "10"

    def test_schema_format(self):
        t = Tool("my_tool", "Does stuff", {"x": "string", "y?": "number"}, lambda a: "")
        schema = t.to_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "my_tool"
        assert "x" in schema["function"]["parameters"]["required"]
        assert "y" not in schema["function"]["parameters"]["required"]


class TestFileTools:
    @pytest.fixture(autouse=True)
    def register_tools(self, registry, config):
        from mocode.tools.file import register_file_tools
        register_file_tools(registry, config)

    def test_file_read(self, registry, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3", encoding="utf-8")
        result = registry.run("read", {"path": str(f)})
        assert "line1" in result

    def test_file_read_not_found(self, registry, tmp_path):
        result = registry.run("read", {"path": str(tmp_path / "nope.txt")})
        assert "error" in result

    def test_file_write(self, registry, tmp_path):
        f = tmp_path / "out.txt"
        result = registry.run("write", {"path": str(f), "content": "hello"})
        assert result == "ok"
        assert f.read_text(encoding="utf-8") == "hello"

    def test_file_append(self, registry, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("first\n", encoding="utf-8")
        registry.run("append", {"path": str(f), "content": "second"})
        content = f.read_text(encoding="utf-8")
        assert "first" in content
        assert "second" in content

    def test_file_edit(self, registry, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world", encoding="utf-8")
        result = registry.run("edit", {"path": str(f), "old": "world", "new": "mocode"})
        assert result == "ok"
        assert f.read_text(encoding="utf-8") == "hello mocode"

    def test_file_edit_not_unique(self, registry, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text("aaa bbb aaa", encoding="utf-8")
        result = registry.run("edit", {"path": str(f), "old": "aaa", "new": "ccc"})
        assert "not_unique" in result

    def test_file_edit_all_flag(self, registry, tmp_path):
        f = tmp_path / "all.txt"
        f.write_text("aaa bbb aaa", encoding="utf-8")
        result = registry.run("edit", {"path": str(f), "old": "aaa", "new": "ccc", "all": True})
        assert result == "ok"
        assert f.read_text(encoding="utf-8") == "ccc bbb ccc"


class TestSearchTools:
    @pytest.fixture(autouse=True)
    def register_tools(self, registry):
        from mocode.tools.search import register_search_tools
        register_search_tools(registry)

    def test_glob(self, registry, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.py").write_text("", encoding="utf-8")
        (tmp_path / "c.txt").write_text("", encoding="utf-8")
        result = registry.run("glob", {"pat": "*.py", "path": str(tmp_path)})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_grep(self, registry, tmp_path):
        (tmp_path / "hello.py").write_text("print('hello world')\n", encoding="utf-8")
        (tmp_path / "bye.py").write_text("print('goodbye')\n", encoding="utf-8")
        result = registry.run("grep", {"pat": "hello", "path": str(tmp_path)})
        assert "hello world" in result
        assert "goodbye" not in result

    def test_grep_no_match(self, registry, tmp_path):
        (tmp_path / "empty.py").write_text("nothing here\n", encoding="utf-8")
        result = registry.run("grep", {"pat": "nonexistent_pattern_xyz", "path": str(tmp_path)})
        assert result == "none"
