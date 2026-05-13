"""Tool + ToolRegistry — instance-scoped, storage and schema only."""

from __future__ import annotations

import inspect
from typing import Callable


class ToolError(Exception):
    """Tool execution error — callers catch this."""

    def __init__(self, message: str, code: str = "execution_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class Tool:
    """Tool — supports sync and async functions.

    run() / run_async() propagate ToolError and Exception upward.
    The caller (AgentLoop) decides how to handle errors.
    """

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
        validated = self._validate_args(args)
        return self.func(validated)

    async def run_async(self, args: dict) -> str:
        validated = self._validate_args(args)
        if self.is_async:
            return await self.func(validated)
        return self.func(validated)

    def _validate_args(self, args: dict) -> dict:
        result = dict(args)
        for param_name, param_spec in self.params.items():
            if isinstance(param_spec, dict):
                if param_name not in result and "default" in param_spec:
                    result[param_name] = param_spec["default"]
        return result

    def to_schema(self) -> dict:
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
                    "number": "number",
                    "integer": "integer",
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
    """Instance-scoped tool registry — storage and schema generation."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> Tool | None:
        return self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def all_schemas(self) -> list[dict]:
        return [t.to_schema() for t in self._tools.values()]

    def derived(self, exclude: set[str] | None = None) -> "ToolRegistry":
        new = ToolRegistry()
        if exclude:
            new._tools = {k: v for k, v in self._tools.items() if k not in exclude}
        else:
            new._tools = self._tools
        return new
