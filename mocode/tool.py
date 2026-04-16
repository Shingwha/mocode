"""Tool dataclass + ToolRegistry (实例作用域)

v0.2 关键改进：
- ToolRegistry 不再是类变量全局单例，而是实例作用域
- Tool 支持同步和异步函数（is_async 标志 + run_async 方法）
- ToolRegistry.run_async() 统一异步调度
- ToolRegistry.derived() 共享工具但隔离 state
"""

import inspect
from typing import Callable


class ToolError(Exception):
    """工具执行错误"""

    def __init__(self, message: str, code: str = "execution_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class Tool:
    """工具类 — 支持同步和异步函数"""

    def __init__(
        self,
        name: str,
        description: str,
        params: dict,
        func: Callable[[dict], str],
    ):
        self.name = name
        self.description = description
        self.params = params
        self.func = func
        self.is_async = inspect.iscoroutinefunction(func)

    def run(self, args: dict) -> str:
        """同步执行工具（仅适用于同步函数）"""
        try:
            validated = self._validate_args(args)
            return self.func(validated)
        except ToolError as e:
            return f"error [{e.code}]: {e.message}"
        except Exception as err:
            return f"error: {err}"

    async def run_async(self, args: dict) -> str:
        """异步执行工具 — async 函数直接 await，sync 函数同步调用

        注意：BaseException（如 Interrupted）不在此捕获，向上传播。
        """
        try:
            validated = self._validate_args(args)
            if self.is_async:
                return await self.func(validated)
            else:
                return self.func(validated)
        except ToolError as e:
            return f"error [{e.code}]: {e.message}"
        except Exception as err:
            return f"error: {err}"

    def _validate_args(self, args: dict) -> dict:
        """验证并填充参数默认值"""
        result = dict(args)
        for param_name, param_spec in self.params.items():
            if isinstance(param_spec, dict):
                if param_name not in result and "default" in param_spec:
                    result[param_name] = param_spec["default"]
            elif isinstance(param_spec, str) and param_spec.endswith("?"):
                pass
        return result

    def to_schema(self) -> dict:
        """转换为 OpenAI API 格式的工具定义"""
        properties = {}
        required = []

        for param_name, param_spec in self.params.items():
            if isinstance(param_spec, dict):
                prop = {"type": param_spec.get("type", "string")}
                if "description" in param_spec:
                    prop["description"] = param_spec["description"]
                if "enum" in param_spec:
                    prop["enum"] = param_spec["enum"]
                properties[param_name] = prop
                if "default" not in param_spec:
                    required.append(param_name)
            else:
                is_optional = param_spec.endswith("?")
                base_type = param_spec.rstrip("?")
                type_map = {
                    "string": "string",
                    "number": "integer",
                    "boolean": "boolean",
                    "array": "array",
                    "object": "object",
                }
                properties[param_name] = {"type": type_map.get(base_type, base_type)}
                if not is_optional:
                    required.append(param_name)

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


class ToolRegistry:
    """实例作用域工具注册表 — 每个 App 一个实例"""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self.state: dict = {}  # 共享状态，供工具访问 Agent 的 messages

    def register(self, tool: Tool) -> None:
        """注册工具"""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """注销工具"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def get(self, name: str) -> Tool | None:
        """获取工具"""
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        """获取所有工具"""
        return list(self._tools.values())

    def all_schemas(self) -> list[dict]:
        """获取所有工具的 schema"""
        return [t.to_schema() for t in self._tools.values()]

    def run(self, name: str, args: dict) -> str:
        """同步运行工具"""
        tool = self.get(name)
        if not tool:
            return f"error: unknown tool '{name}'"
        return tool.run(args)

    async def run_async(self, name: str, args: dict) -> str:
        """异步运行工具 — async 工具直接 await，sync 工具同步调用"""
        tool = self.get(name)
        if not tool:
            return f"error: unknown tool '{name}'"
        return await tool.run_async(args)

    def derived(self) -> "ToolRegistry":
        """创建派生注册表 — 共享工具但隔离 state"""
        new = ToolRegistry()
        new._tools = self._tools
        return new
