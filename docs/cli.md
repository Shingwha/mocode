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
| `/help` | `/h`, `/?`, `/` | Show commands or interactive menu |
| `/model` | `/m` | Switch model |
| `/provider` | `/p` | Switch provider |
| `/clear` | `/c` | Clear conversation history |
| `/skills` | | List and activate skills |
| `/rtk` | | Manage RTK (Token Killer) |
| `/exit` | `/q`, `quit` | Exit application |

## Command Details

### `/help` - Show Commands

```bash
/help          # Show command list
/h             # Alias
/?             # Alias
/              # Alias - shows interactive menu
```

Without arguments, displays an interactive menu to select and execute commands. With arguments, shows the command list.

### `/model` - Switch Model

```bash
/model         # Interactive selection
/m             # Alias
/model gpt-4o  # Direct selection by name
/model 2       # Selection by number
```

Shows available models for the current provider and allows switching.

### `/provider` - Switch Provider

```bash
/provider      # Interactive selection (then model selection)
/p             # Alias
/provider deepseek  # Direct selection by key
/provider 2    # Selection by number
```

After switching provider, automatically prompts for model selection.

### `/clear` - Clear History

```bash
/clear         # Clear conversation history
/c             # Alias
```

### `/skills` - List Skills

```bash
/skills        # Interactive selection
/skills 2      # Selection by number
/skills my-skill  # Direct activation
```

Lists available skills from `~/.mocode/skills/` directory and allows activation.

### `/rtk` - Manage RTK

RTK (Rust Token Killer) compresses command output to save tokens.

```bash
/rtk           # Interactive menu
/rtk status    # Show installation and feature status
/rtk install   # Install RTK (auto on Windows)
/rtk gain      # Show token savings statistics
/rtk enable    # Enable RTK feature
/rtk disable   # Disable RTK feature
```

#### RTK Subcommands

| Subcommand | Description |
|------------|-------------|
| `status` | Show RTK installation and feature status |
| `install` | Install RTK (auto-install on Windows) |
| `gain` | Show token savings statistics |
| `enable` | Enable RTK feature |
| `disable` | Disable RTK feature |

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
/q             # Alias
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
  "rtk": {
    "enabled": true
  }
}
```

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
