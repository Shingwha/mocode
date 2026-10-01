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

from mocode.plugins import BuildContext, Plugin, Tool, ToolResult


def _path_of(error: jsonschema.ValidationError) -> str:
    where = ".".join(str(p) for p in error.absolute_path)
    return where or "(root)"


def validate_json_tool(cwd: Path) -> Tool:
    """Check a JSON document against a JSON Schema — both project files."""

    def _load(relative: str) -> ToolResult | dict:
        """One file from the project, or the ToolResult that says why not."""
        path = cwd / relative
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            return ToolResult(
                content=f"error: cannot read {relative!r}: {e}",
                details={"valid": False, "error_count": -1, "errors": []},
            )

    def run(args: dict) -> ToolResult:
        # A conversation works in its own project; both paths resolve there.
        schema = _load(args.get("schema_path", ""))
        document = _load(args.get("document_path", ""))
        if isinstance(schema, ToolResult):
            return schema
        if isinstance(document, ToolResult):
            return document

        errors = [
            {"path": _path_of(e), "message": e.message}
            for e in sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(document),
                key=_path_of,
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

    return Tool(
        name="validate_json",
        description=(
            "Validate a JSON document against a JSON Schema file in this "
            "project. Use it whenever a config, manifest or payload has a "
            "schema — before trusting it or shipping it."
        ),
        schema={
            "type": "object",
            "properties": {
                "schema_path": {
                    "type": "string",
                    "description": "Path to the JSON Schema file, relative to the project",
                },
                "document_path": {
                    "type": "string",
                    "description": "Path to the JSON document to check",
                },
            },
            "required": ["schema_path", "document_path"],
        },
        func=run,
        result_key="error_count",
    )


class JsonValidatePlugin(Plugin):
    name = "json-validate"
    description = "A validate_json tool, backed by its own environment"

    def build(self, ctx: BuildContext) -> None:
        ctx.tools.register(validate_json_tool(ctx.cwd))


plugin = JsonValidatePlugin()
