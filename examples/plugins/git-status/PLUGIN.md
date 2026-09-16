# MoCode Plugin SDK — example plugin

A complete, minimal plugin showing all three contribution points: a **tool**, a
**command**, and a **prompt section**.

## Install

Copy this directory into one of the plugin directories:

```
./.mocode/plugins/git-status/     project-local (wins on name conflicts)
~/.mocode/plugins/git-status/     user-global
```

Restart MoCode. The `git_status` tool, the `/branch` command, and the extra
prompt guidance are live — no source changes.

## Options in `PLUGIN.md`

| field | required | meaning |
|---|---|---|
| `name` | yes | Unique plugin id; used for enable/disable and conflict resolution |
| `description` | yes | Shown in listings and errors |
| `version` | no | Display only |
| `enabled` | no | `false` disables the plugin; `config.json` overrides it |
| `entrypoint` | no | Class name to instantiate in `plugin.py` (default: the module-level `plugin`, else the first `Plugin` subclass defined in the file) |

## Enabling / disabling

```json
{
  "plugins": {
    "git-status": { "enabled": false },
    "shell": { "enabled": false }
  }
}
```

`~/.mocode/config.json` wins over `PLUGIN.md`. Built-in plugins (`filesystem`,
`shell`, `skills`, `cli`) use the same switch.

Plugins are **trusted code**: importing `plugin.py` runs it.
