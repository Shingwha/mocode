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
        params: dict[str, dict],
        func: Callable[[dict], str],
    ):
        self.name = name
        self.description = description
        self._required = []
        normalized = {}
        for k, v in params.items():
            if not isinstance(v, dict):
                raise TypeError(
                    f"Tool '{name}': param '{k}' must be a dict with 'type' and 'description', "
                    f"got {type(v).__name__}: {v!r}"
                )
            normalized[k] = v
            if "default" not in v and not v.get("optional"):
                self._required.append(k)
        self.params = normalized
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
            if param_name not in result:
                if "default" in param_spec:
                    result[param_name] = param_spec["default"]
                elif param_name in self._required:
                    raise ToolError(
                        f"Missing required parameter '{param_name}'",
                        "missing_param",
                    )
        return result

    def to_schema(self) -> dict:
        properties = {}

        for param_name, param_spec in self.params.items():
            prop = {"type": param_spec.get("type", "string")}
            if "description" in param_spec:
                prop["description"] = param_spec["description"]
            if "enum" in param_spec:
                prop["enum"] = param_spec["enum"]
            if "default" in param_spec:
                prop["default"] = param_spec["default"]
            properties[param_name] = prop

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": self._required,
                },
            },
        }


class ToolRegistry:
    """Instance-scoped tool registry — storage and schema generation."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._schema_cache: list[dict] | None = None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
        self._schema_cache = None

    def unregister(self, name: str) -> Tool | None:
        result = self._tools.pop(name, None)
        if result:
            self._schema_cache = None
        return result

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def all_schemas(self) -> list[dict]:
        if self._schema_cache is None:
            self._schema_cache = [t.to_schema() for t in self._tools.values()]
        return self._schema_cache

    def derived(self, exclude: set[str] | None = None) -> ToolRegistry:
        new = ToolRegistry()
        if exclude:
            new._tools = {k: v for k, v in self._tools.items() if k not in exclude}
        else:
            new._tools = dict(self._tools)
        return new
