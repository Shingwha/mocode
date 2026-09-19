# MoCode plugin SDK — example plugin

A complete plugin showing every surface, in the layout the Agent Plugins
standard defines:

```
git-status/
├── plugin.json              the manifest: name, version, description
├── skills/commit/SKILL.md   portable skill — any compatible client can read it
├── mocode/plugin.py         contributions to the agent: tool, command, prompt section
└── mocode.cli/plugin.py     contributions to the terminal: a command with a picker
```

## Install

Copy this directory into one of the plugin directories:

```
./.mocode/plugins/git-status/     project-local (wins on name conflicts)
~/.mocode/plugins/git-status/     user-global
```

Restart MoCode. The `git_status` tool, the `/branch` command, the `/status`
terminal command, the `commit` skill and the extra prompt guidance are live — no
source changes.

## What goes where

| path | who reads it | what belongs there |
|---|---|---|
| `plugin.json` | every client | identity: `name` (required), `version`, `description`, `author`, `license`… |
| `skills/<name>/SKILL.md` | every client | instructions the agent loads on demand |
| `mcp.json` | every client | MCP servers (standard; MoCode does not serve them yet) |
| `mocode/plugin.py` | MoCode | contributions to the agent — works in every frontend |
| `mocode.cli/plugin.py` | the terminal | terminal chrome — a picker, a keybinding, a full-screen view |
| `mocode.web/` | a web frontend, later | its own chrome |

The root of the directory is the standard's; everything client-specific lives
under a directory named for the namespace that defines it. Another client
reading this directory picks up `skills/`, ignores `mocode/` and `mocode.cli/`
without validating them, and vice versa.

A single `git-status.py` file next to the plugin directories works too, for a
plugin with no portable parts.

## Enabling / disabling

The manifest has no `enabled` field — the switch is config:

```json
{
  "plugins": {
    "git-status": { "enabled": false },
    "shell": { "enabled": false }
  }
}
```

`~/.mocode/config.json` governs every plugin, built-in ones included
(`filesystem`, `shell`, `skills`).

Plugins are **trusted code**: importing `mocode/plugin.py` runs it.
