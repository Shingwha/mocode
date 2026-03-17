"""Tool 基类和注册机制"""

from typing import Callable


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
        self.params = params  # {"param_name": "type?"} 格式，? 表示可选
        self.func = func

    def run(self, args: dict) -> str:
        """执行工具"""
        try:
            return self.func(args)
        except Exception as err:
            return f"error: {err}"

    def to_schema(self) -> dict:
        """转换为 OpenAI API 格式的工具定义"""
        properties = {}
        required = []

        for param_name, param_type in self.params.items():
            is_optional = param_type.endswith("?")
            base_type = param_type.rstrip("?")

            # 类型映射
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
