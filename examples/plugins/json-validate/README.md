# json-validate — a plugin with its own environment

A `validate_json` tool that checks a JSON document against a JSON Schema
file. The point of the example is not the tool — it is the dependency:
`jsonschema` is not a package MoCode ships, so this plugin declares it in a
`pyproject.toml` at its root and runs against an environment of its own,
materialised by uv.

## Install

From a checkout of this repository:

```bash
mocode plugin install ./examples/plugins/json-validate
```

That one command fetches the plugin, places it under `~/.mocode/plugins/`,
runs `uv sync` inside it (creating its `.venv`), and tells you to restart.
After the restart the model has `validate_json`. `mocode plugin list` shows
the plugin as `[own environment]`; `mocode plugin remove json-validate`
takes it away again, environment included.

## Try it

Give the project a schema and a document that breaks it:

```json
// deploy.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": { "replicas": { "type": "integer", "minimum": 1 } },
  "required": ["replicas"]
}
```

```json
// deploy.json
{ "replicas": 0 }
```

Then ask: *“validate deploy.json against deploy.schema.json”* — the tool
answers `replicas: 0 is less than the minimum of 1`, the schema's own words,
not the model's guess.

## What the environment is — and is not

The plugin's `.venv` is private to it on disk, but in the process it is
**addition, not isolation**: MoCode appends its `site-packages` to the end
of `sys.path`, so a package resolves there only when MoCode's own
environment does not have it. See [Dependencies](../../../docs/plugins.md)
for the full contract and the alternatives.

After editing the plugin's `dependencies`, re-materialise with:

```bash
mocode plugin sync json-validate
```

## Layout

```
json-validate/
├── plugin.json            the manifest (standard)
├── pyproject.toml         the dependency declaration — uv sync reads this
└── mocode/
    └── plugin.py          the tool; imports jsonschema like any module
```
