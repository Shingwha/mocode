# CLI Commands Reference

This document describes all built-in commands available in mocode CLI.

## Installation

```bash
# Install as a tool
uv tool install -e .

# Run the CLI
mocode

# Or use uv run
uv run mocode
```

## Built-in Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/` | `/help`, `/h`, `/?` | Show commands or interactive menu |
| `/model` | `/m` | Switch model |
| `/provider` | `/p` | Switch provider |
| `/session` | `/s` | Manage conversation sessions |
| `/clear` | `c` | Clear conversation history |
| `/skills` | | List and activate skills |
| `/plugin` | | Manage plugins |
| `/rtk` | | Manage RTK (Token Killer) |
| `/exit` | `q`, `quit` | Exit application |

## Command Details

### `/` - Show Commands

```bash
/              # Show interactive menu to select command
/help          # Show command list
/h             # Alias
/?             # Alias
```

Without arguments, displays an interactive menu to select and execute commands. With arguments, shows the command list.

### `/model` - Switch Model

```bash
/model         # Interactive selection
/m             # Alias
/model gpt-4o  # Direct selection by name
/model 2       # Selection by number
```

Shows available models for the current provider and allows switching. Use the "Manage" option in the menu to add or delete models.

### `/provider` - Switch Provider

```bash
/provider      # Interactive selection (then model selection)
/p             # Alias
/provider deepseek  # Direct selection by key
/provider 2    # Selection by number
```

After switching provider, automatically prompts for model selection. Use the "Manage" option in the menu to add, edit, or delete providers.

### `/session` - Manage Sessions

```bash
/session          # Interactive session selection
/s                # Alias
/session list     # List all sessions
/session restore <id>  # Restore specific session
```

Sessions are stored per working directory in `~/.mocode/sessions/`. When you clear history with `/clear`, the conversation is automatically saved as a session.

### `/clear` - Clear History

```bash
/clear         # Clear conversation history (auto-saves session first)
c              # Alias
```

Automatically saves the current conversation as a session before clearing.

### `/skills` - List Skills

```bash
/skills        # Interactive selection
/skills 2      # Selection by number
/skills my-skill  # Direct activation
```

Lists available skills from `~/.mocode/skills/` directory and allows activation.

### `/plugin` - Manage Plugins

```bash
/plugin              # Interactive plugin selection (toggle enable/disable)
/plugin list         # List all discovered plugins
/plugin info <name>  # Show plugin information
/plugin <name>       # Toggle plugin enable/disable by name
/plugin <n>          # Toggle plugin by number
/plugin help         # Show help message
```

Plugins are discovered from `~/.mocode/plugins/` and `<project>/.mocode/plugins/`. When selected interactively, plugins can be toggled on/off.

Output example for `/plugin list`:
```
Discovered plugins:
--------------------------------------------------
  rtk v1.0.0 [enabled] - Compress command output to save tokens
  my-plugin v1.0.0 [disabled] - A sample plugin
--------------------------------------------------
Total: 2 plugin(s)
```

### `/rtk` - Manage RTK

RTK (Rust Token Killer) compresses command output to save tokens. RTK is a built-in plugin.

```bash
/rtk           # Interactive menu
/rtk status    # Show installation and plugin status
/rtk install   # Install RTK (auto on Windows)
/rtk gain      # Show token savings statistics
```

Use `/plugin` to enable or disable the RTK plugin.

#### RTK Subcommands

| Subcommand | Description |
|------------|-------------|
| `status` | Show RTK installation and plugin status |
| `install` | Install RTK (auto-install on Windows) |
| `gain` | Show token savings statistics |

#### Supported Commands

RTK compresses output from:
- File listing: `ls`, `tree`, `find`
- File content: `cat`, `head`, `tail`
- Search: `grep`, `rg`
- Git: `git status`, `git log`, `git diff`, `git show`
- Build/test: `cargo test`, `cargo build`, `npm test`, `pytest`

### `/exit` - Exit Application

```bash
/exit          # Exit mocode
q              # Alias
quit           # Alias
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| ESC | Interrupt current operation |

Press ESC during AI response or tool execution to cancel the operation.

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
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "read": "allow"
  },
  "max_tokens": 8192,
  "plugins": {
    "enabled": ["rtk"],
    "disabled": []
  }
}
```

### Plugin Configuration

The `plugins` section controls plugin behavior:

| Field | Description |
|-------|-------------|
| `enabled` | List of plugins to enable automatically |
| `disabled` | List of plugins to disable |

## Interactive Menus

Most commands support interactive selection when called without arguments:

1. Arrow keys to navigate
2. Enter to select
3. ESC to cancel

For example, `/model` shows a list of available models with the current one highlighted.

## Permission System

When tools require permission, an interactive menu appears:

```
? Permission required for bash
  ls -la

  > Allow (execute the tool)
    Deny (cancel the operation)
    Type something (provide custom response)
```

Options:
- **Allow** - Execute the tool
- **Deny** - Cancel the operation
- **Type something** - Provide custom input as tool result

See [Permission System](permission.md) for configuration details.
