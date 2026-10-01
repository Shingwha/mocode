# multi-file — a plugin as a package

A prompt section and a `/motd` command, deliberately split across three
files. The point of the example is not the contributions — it is the
layout: past a few hundred lines, a single `mocode/plugin.py` stops being
something you enjoy editing, and this is the shape to reach for instead.

```
multi-file/
├── plugin.json                  the manifest (standard)
└── mocode/
    └── plugin/                  the package — the directory is the entry
        ├── __init__.py          assembles the submodules, exposes `plugin`
        ├── sections.py          a prompt section
        └── commands.py          the /motd command (imports from sections.py)
```

## When to use which

Stay with `mocode/plugin.py` (or `mocode.cli/plugin.py`) while the code
reads comfortably top to bottom — a few hundred lines, one concern. Move to
a package when the file starts holding more than one idea: a tool and its
helpers, commands and their shared table, anything you would already have
split in ordinary Python. The same judgment applies to both namespaces, and
the loader decides identically for each:

1. `<ns>/plugin.py` — the single file, when present it wins;
2. `<ns>/plugin/__init__.py` — the package form, submodules beside it.

Both present is not an error; the single file is the entry and the package
is ignored. A `plugin/` directory *without* `__init__.py`, or stray `.py`
files beside no entry at all, is reported with the shape to fix instead of
the plugin quietly loading as skills-only.

## What the package buys, and what it does not

- Submodules load through **relative imports only** (`from .commands import
  motd`). Each plugin's package gets a name derived from the plugin's own,
  so two plugins can both ship a `helpers.py` and neither ever sees the
  other's — which is exactly why you must not add the plugin directory to
  `sys.path`: same-named modules would overwrite each other process-wide, a
  cross-plugin pollution nothing would report.
- The loader resolves the plugin from the entry module: a module-level
  `plugin = MyPlugin()` instance wins. In package form this is more than a
  style point — only the names `__init__.py` imported are visible on the
  module, so a plugin class living in a submodule must be re-exported (or
  instantiated) in `__init__.py` for the loader to find it. This example
  does the sturdiest thing: the class lives in `__init__.py`, the pieces it
  wires together live in submodules.
- Third-party dependencies are unchanged by any of this: they still belong
  in a `pyproject.toml` at the plugin root and a `PluginVenv` of its own
  (see [`json-validate`](../json-validate) for the worked version). The
  package form organises *your* files; it is not an environment.

## Install

From a checkout of this repository:

```bash
mocode plugin install ./examples/plugins/multi-file
```

Restart MoCode, and `/motd` answers in any frontend, while the model sees
the `motd` prompt section.
