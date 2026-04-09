# CLI Commands Reference

This document describes all built-in commands available in the mocode CLI.

## Built-in Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/` | `/help`, `/h`, `/?` | Show commands or interactive menu |
| `/provider` | `/p` | Switch provider and model |
| `/mode` | | Manage operation modes (normal, yolo) |
| `/session` | `/s` | Manage conversation sessions |
| `/clear` | `/c` | Clear conversation history |
| `/skills` | | List and activate skills |
| `/plugin` | | Manage plugins |
| `/rtk` | | Manage RTK (Token Killer) |
| `/exit` | `/q`, `/quit` | Exit application |

## Command Details

### `/` — Show Commands

```bash
/              # Interactive menu
/help          # Show command list
/h             # Alias
/?             # Alias
```

Without arguments, displays an interactive menu to select and execute commands.

### `/provider` — Switch Provider and Model

```bash
/provider          # Interactive selection
/p                 # Alias
/provider deepseek # Direct selection by key
/provider 2        # Selection by number
```

After switching provider, you will be prompted for model selection. Use the "Manage" option in the menu to add, edit, or delete providers.

### `/mode` — Manage Operation Modes

```bash
/mode          # Show current mode
/mode list     # List all available modes
/mode yolo     # Switch to yolo mode
/mode normal   # Switch back to normal mode
```

Modes control permission behavior:
- **normal**: Standard permission checks (ask/allow/deny based on config)
- **yolo**: Auto-approves all tools except dangerous commands

### `/session` — Manage Sessions

```bash
/session            # Interactive session selection
/s                  # Alias
/session restore N  # Restore session by number
```

Sessions are stored per working directory in `~/.mocode/sessions/`. When you clear history with `/clear`, the conversation is automatically saved as a session.

### `/clear` — Clear History

```bash
/clear   # Clear history (auto-saves session)
/c       # Alias
```

Automatically saves the current conversation before clearing.

### `/skills` — List Skills

```bash
/skills        # Interactive selection
/skills 2      # Select by number
/skills name   # Direct activation
```

Lists available skills from `~/.mocode/skills/` and allows activation.

### `/plugin` — Manage Plugins

```bash
/plugin                    # Toggle plugins interactively
/plugin <name>             # Toggle plugin by name
/plugin info <name>        # Show plugin information
/plugin install <url>      # Install from GitHub
/plugin uninstall <name>   # Uninstall a plugin
/plugin update <name>      # Update a plugin
```

Plugins are discovered from `~/.mocode/plugins/` and `<project>/.mocode/plugins/`.

### `/rtk` — Manage RTK

RTK (Rust Token Killer) compresses command output to save tokens.

```bash
/rtk         # Interactive menu
/rtk status  # Show installation and status
/rtk install # Install RTK
/rtk gain    # Show token savings statistics
```

Use `/plugin` to enable or disable the RTK plugin.

### `/exit` — Exit Application

```bash
/exit   # Exit mocode
/q      # Alias
/quit   # Alias
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| ESC | Interrupt current operation |

Press ESC during AI response or tool execution to cancel.

## Configuration

Configuration is stored at `~/.mocode/config.json`:

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-..."
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "read": "allow"
  }
}
```

For advanced configuration, see [Provider Configuration](provider.md), [Permission System](permission.md), and [Plugin System](plugins.md).

## Interactive Menus

Most commands support interactive selection when called without arguments:

1. Use arrow keys to navigate
2. Press Enter to select
3. Press ESC to cancel

For example, `/provider` shows a list of available providers with the current one highlighted.

## Permission Prompts

When a tool requires permission, an interactive menu appears:

```
? Permission required for bash: rm -rf /temp

  > Allow (execute the tool)
    Deny (cancel the operation)
    Type something (provide custom response)
```

Options:
- **Allow** — Execute the tool
- **Deny** — Cancel and return denial message
- **Type something** — Provide custom input as tool result

See [Permission System](permission.md) for configuration details.
