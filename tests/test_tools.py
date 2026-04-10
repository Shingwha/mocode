"""Tool system tests"""

import pytest

from mocode.tools.base import Tool, ToolRegistry, ToolError, tool


class TestToolRegistry:
    def test_register_and_get(self):
        t = Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"])
        ToolRegistry.register(t)
        assert ToolRegistry.get("echo") is t

    def test_unregister(self):
        t = Tool("echo", "Echo", {"msg": "string"}, lambda a: a["msg"])
        ToolRegistry.register(t)
        assert ToolRegistry.unregister("echo")
        assert ToolRegistry.get("echo") is None

    def test_unregister_nonexistent(self):
        assert not ToolRegistry.unregister("nope")

    def test_run(self):
        ToolRegistry.register(Tool("add", "Add", {"a": "number", "b": "number"}, lambda a: str(a["a"] + a["b"])))
        assert ToolRegistry.run("add", {"a": 1, "b": 2}) == "3"

    def test_run_unknown(self):
        result = ToolRegistry.run("unknown", {})
        assert "error" in result
        assert "unknown" in result

    def test_all_schemas(self):
        ToolRegistry.register(Tool("t1", "Tool 1", {"x": "string"}, lambda a: ""))
        ToolRegistry.register(Tool("t2", "Tool 2", {"y": "number?"}, lambda a: ""))
        schemas = ToolRegistry.all_schemas()
        assert len(schemas) == 2
        names = {s["function"]["name"] for s in schemas}
        assert names == {"t1", "t2"}


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

    def test_schema_with_full_format(self):
        t = Tool("t", "T", {
            "mode": {"type": "string", "description": "Mode", "enum": ["a", "b"]},
            "count": {"type": "number", "default": 1},
        }, lambda a: "")
        schema = t.to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert props["mode"]["enum"] == ["a", "b"]
        assert "count" not in schema["function"]["parameters"]["required"]
        props = schema["function"]["parameters"]["properties"]
        assert props["mode"]["enum"] == ["a", "b"]
        assert "count" not in schema["function"]["parameters"]["required"]


class TestToolDecorator:
    def test_decorator_registers(self):
        @tool("greet", "Greet someone", {"name": "string"})
        def greet(args):
            return f"Hello {args['name']}"

        assert ToolRegistry.get("greet") is not None
        assert ToolRegistry.run("greet", {"name": "World"}) == "Hello World"


class TestFileTools:
    @pytest.fixture(autouse=True)
    def register_tools(self):
        from mocode.tools.file_tools import register_file_tools
        register_file_tools()

    def test_file_read(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3", encoding="utf-8")
        result = ToolRegistry.run("read", {"path": str(f)})
        assert "line1" in result
        assert "line2" in result

    def test_file_read_not_found(self, tmp_path):
        result = ToolRegistry.run("read", {"path": str(tmp_path / "nope.txt")})
        assert "error" in result

    def test_file_read_offset_limit(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
        result = ToolRegistry.run("read", {"path": str(f), "offset": 2, "limit": 3})
        assert "line2" in result
        assert "line4" in result
        assert "line5" not in result

    def test_file_write(self, tmp_path):
        f = tmp_path / "out.txt"
        result = ToolRegistry.run("write", {"path": str(f), "content": "hello"})
        assert result == "ok"
        assert f.read_text(encoding="utf-8") == "hello"

    def test_file_append(self, tmp_path):
        f = tmp_path / "log.txt"
        f.write_text("first\n", encoding="utf-8")
        result = ToolRegistry.run("append", {"path": str(f), "content": "second"})
        assert result == "ok"
        content = f.read_text(encoding="utf-8")
        assert "first" in content
        assert "second" in content

    def test_file_edit(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello world", encoding="utf-8")
        result = ToolRegistry.run("edit", {"path": str(f), "old": "world", "new": "mocode"})
        assert result == "ok"
        assert f.read_text(encoding="utf-8") == "hello mocode"

    def test_file_edit_not_unique(self, tmp_path):
        f = tmp_path / "dup.txt"
        f.write_text("aaa bbb aaa", encoding="utf-8")
        result = ToolRegistry.run("edit", {"path": str(f), "old": "aaa", "new": "ccc"})
        assert "not_unique" in result

    def test_file_edit_all_flag(self, tmp_path):
        f = tmp_path / "all.txt"
        f.write_text("aaa bbb aaa", encoding="utf-8")
        result = ToolRegistry.run("edit", {"path": str(f), "old": "aaa", "new": "ccc", "all": True})
        assert result == "ok"
        assert f.read_text(encoding="utf-8") == "ccc bbb ccc"

    def test_file_edit_not_found(self, tmp_path):
        f = tmp_path / "edit.txt"
        f.write_text("hello", encoding="utf-8")
        result = ToolRegistry.run("edit", {"path": str(f), "old": "missing", "new": "x"})
        assert "not_found" in result


class TestSearchTools:
    @pytest.fixture(autouse=True)
    def register_tools(self):
        from mocode.tools.search_tools import register_search_tools
        register_search_tools()

    def test_glob(self, tmp_path):
        (tmp_path / "a.py").write_text("", encoding="utf-8")
        (tmp_path / "b.py").write_text("", encoding="utf-8")
        (tmp_path / "c.txt").write_text("", encoding="utf-8")
        result = ToolRegistry.run("glob", {"pat": "*.py", "path": str(tmp_path)})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_grep(self, tmp_path):
        (tmp_path / "hello.py").write_text("print('hello world')\n", encoding="utf-8")
        (tmp_path / "bye.py").write_text("print('goodbye')\n", encoding="utf-8")
        result = ToolRegistry.run("grep", {"pat": "hello", "path": str(tmp_path)})
        assert "hello world" in result
        assert "goodbye" not in result

    def test_grep_no_match(self, tmp_path):
        (tmp_path / "empty.py").write_text("nothing here\n", encoding="utf-8")
        result = ToolRegistry.run("grep", {"pat": "nonexistent_pattern_xyz", "path": str(tmp_path)})
        assert result == "none"
