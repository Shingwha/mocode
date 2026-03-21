"""Tool 基类和注册机制"""

from typing import Callable


class ToolError(Exception):
    """工具执行错误"""

    def __init__(self, message: str, code: str = "execution_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class Tool:
    """工具类"""

    def __init__(
        self,
        name: str,
        description: str,
        params: dict,
        func: Callable[[dict], str],
    ):
        self.name = name
        self.description = description
        self.params = params  # 支持两种格式：简写 "type?" 或完整 {"type": "...", "default": ...}
        self.func = func

    def run(self, args: dict) -> str:
        """执行工具"""
        try:
            validated = self._validate_args(args)
            return self.func(validated)
        except ToolError as e:
            return f"error [{e.code}]: {e.message}"
        except Exception as err:
            return f"error: {err}"

    def _validate_args(self, args: dict) -> dict:
        """验证并填充参数默认值"""
        result = dict(args)
        for param_name, param_spec in self.params.items():
            # 完整格式：{"type": "...", "default": ...}
            if isinstance(param_spec, dict):
                if param_name not in result and "default" in param_spec:
                    result[param_name] = param_spec["default"]
            # 简写格式：处理可选参数
            elif isinstance(param_spec, str) and param_spec.endswith("?"):
                pass  # 可选参数，无需验证
        return result

    def to_schema(self) -> dict:
        """转换为 OpenAI API 格式的工具定义"""
        properties = {}
        required = []

        for param_name, param_spec in self.params.items():
            # 完整格式
            if isinstance(param_spec, dict):
                prop = {"type": param_spec.get("type", "string")}
                if "description" in param_spec:
                    prop["description"] = param_spec["description"]
                if "enum" in param_spec:
                    prop["enum"] = param_spec["enum"]
                properties[param_name] = prop

                # 有默认值则为可选
                if "default" not in param_spec:
                    required.append(param_name)

            # 简写格式（向后兼容）
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
    """工具注册表"""

    _tools: dict[str, Tool] = {}

    @classmethod
    def register(cls, tool: Tool):
        """注册工具"""
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> Tool | None:
        """获取工具"""
        return cls._tools.get(name)

    @classmethod
    def all(cls) -> list[Tool]:
        """获取所有工具"""
        return list(cls._tools.values())

    @classmethod
    def all_schemas(cls) -> list[dict]:
        """获取所有工具的 schema"""
        return [t.to_schema() for t in cls._tools.values()]

    @classmethod
    def run(cls, name: str, args: dict) -> str:
        """运行工具"""
        tool = cls.get(name)
        if not tool:
            return f"error: unknown tool '{name}'"
        return tool.run(args)


def tool(name: str, description: str, params: dict):
    """工具注册装饰器

    使用示例：
    @tool("read", "Read file", {"path": "string", "offset": "number?"})
    def read_file(args):
        ...
    """

    def decorator(func: Callable[[dict], str]) -> Callable[[dict], str]:
        ToolRegistry.register(Tool(name, description, params, func))
        return func

    return decorator
