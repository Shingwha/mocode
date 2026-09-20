"""The plugin's host contributions — the `mocode/` namespace.

This plugin exists to demonstrate the one thing the other examples don't: a
dependency. ``jsonschema`` is not a package MoCode ships, so the plugin
declares it in its ``pyproject.toml`` and runs in an environment of its own
(see the README — ``mocode plugin install`` materialises it with uv). The
code below is an ordinary plugin that simply imports what it needs.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from mocode.plugins import Plugin, Tool, ToolResult


class ValidateJsonTool(Tool):
    """Check a JSON document against a JSON Schema — both project files."""

    def __init__(self, cwd: Path) -> None:
        super().__init__(
            name="validate_json",
            description=(
                "Validate a JSON document against a JSON Schema file in this "
                "project. Use it whenever a config, manifest or payload has a "
                "schema — before trusting it or shipping it."
            ),
            params={
                "schema_path": {
                    "type": "string",
                    "description": "Path to the JSON Schema file, relative to the project",
                },
                "document_path": {
                    "type": "string",
                    "description": "Path to the JSON document to check",
                },
            },
            func=self._run,
            result_key="error_count",
        )
        self._cwd = cwd

    def _run(self, args: dict) -> ToolResult:
        # A conversation works in its own project; both paths resolve there.
        schema = self._load(args.get("schema_path", ""))
        document = self._load(args.get("document_path", ""))
        if isinstance(schema, ToolResult):
            return schema
        if isinstance(document, ToolResult):
            return document

        errors = [
            {"path": self._path_of(e), "message": e.message}
            for e in sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(document),
                key=self._path_of,
            )
        ]

        # Content is what the model reads: short and complete. Details are the
        # facts a UI wants — same data, both channels, no parsing in between.
        if not errors:
            return ToolResult(
                content=f"valid: {args.get('document_path')} matches the schema",
                details={"valid": True, "error_count": 0, "errors": []},
            )
        lines = [f"{e['path']}: {e['message']}" for e in errors]
        return ToolResult(
            content="invalid (" + str(len(errors)) + "):\n" + "\n".join(lines),
            details={
                "valid": False,
                "error_count": len(errors),
                "errors": errors,
            },
        )

    def _load(self, relative: str) -> ToolResult | dict:
        """One file from the project, or the ToolResult that says why not."""
        path = self._cwd / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            return ToolResult(
                content=f"error: cannot read {relative!r}: {e}",
                details={"valid": False, "error_count": -1, "errors": []},
            )

    @staticmethod
    def _path_of(error: jsonschema.ValidationError) -> str:
        where = ".".join(str(p) for p in error.absolute_path)
        return where or "(root)"


class JsonValidatePlugin(Plugin):
    name = "json-validate"
    description = "A validate_json tool, backed by its own environment"

    def build(self, ctx) -> None:
        ctx.tools.register(ValidateJsonTool(ctx.cwd))


plugin = JsonValidatePlugin()
