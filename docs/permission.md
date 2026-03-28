# Permission System

mocode provides configurable permission control for tool execution. Before each tool runs, the system checks the permission rules to allow, ask, or deny the operation.

## Actions

| Action | Description |
|--------|-------------|
| `allow` | Execute without prompting |
| `ask` | Prompt user for confirmation |
| `deny` | Block execution and return denied message |

## Configuration

Permissions are configured in `~/.mocode/config.json`. Two formats are supported:

### Flat Format (Simple)

```json
{
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
    "read": "allow"
  }
}
```

### Nested Format (Fine-grained Control)

For `bash` tool, you can specify permissions for individual commands:

```json
{
  "permission": {
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "dir *": "allow",
      "tree *": "allow",
      "pwd": "allow",
      "find *": "allow",
      "cat *": "allow",
      "head *": "allow",
      "tail *": "allow",
      "grep *": "allow"
    },
    "read": "allow",
    "edit": "ask",
    "write": "ask"
  }
}
```

This allows read-only commands to execute automatically while requiring confirmation for destructive operations.

### Available Tools

| Tool | Description |
|------|-------------|
| `bash` | Execute shell commands |
| `edit` | Edit files |
| `write` | Write/create files |
| `read` | Read files |
| `glob` | Search files by pattern |
| `grep` | Search file contents |

## Matching Rules

### Tool-level Matching

Rules are matched by priority: **specific tool rule > wildcard `*`**

```json
{
  "permission": {
    "*": "ask",      // Default: ask user
    "bash": "allow", // bash: auto-allow
    "edit": "deny"   // edit: always deny
  }
}
```

### Command-level Matching (bash only)

When `bash` is configured with nested rules, matching follows this priority:

1. **Exact match**: `"pwd": "allow"` matches `pwd` exactly
2. **Prefix match**: `"ls *": "allow"` matches `ls`, `ls -la`, `ls /home`
3. **Wildcard `*`**: Default fallback if no other rules match

```json
{
  "permission": {
    "bash": {
      "*": "ask",           // Default: ask
      "ls *": "allow",      // Allow all ls commands
      "cat *": "allow",     // Allow all cat commands
      "rm *": "deny",       // Deny all rm commands
      "git status": "allow" // Allow only "git status" exactly
    }
  }
}
```

### Default Behavior

If no permission rules are configured, the default behavior is `ask`.

## CLI Interaction

When a tool requires permission (`ask`), an interactive menu appears:

```
? Permission required for bash
  rm -rf /important

  > Allow (execute the tool)
    Deny (cancel the operation)
    Type something (provide custom response)
```

Options:
- **Allow** - Execute the tool
- **Deny** - Cancel and return denial message
- **Type something** - Provide custom input as tool result

## Recommendations

### Recommended Configuration

A secure configuration that allows read-only operations while confirming destructive ones:

```json
{
  "permission": {
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "dir *": "allow",
      "tree *": "allow",
      "pwd": "allow",
      "find *": "allow",
      "cat *": "allow",
      "head *": "allow",
      "tail *": "allow",
      "grep *": "allow"
    },
    "read": "allow",
    "glob": "allow",
    "grep": "allow",
    "edit": "ask",
    "write": "ask"
  }
}
```

### Security Guidelines

- Use `ask` for destructive operations: `bash` (default), `edit`, `write`
- Use `allow` for read-only operations: `read`, `glob`, `grep`
- Use `deny` to completely block sensitive tools
- For `bash`, use nested format to allow safe commands while protecting dangerous ones

## Mode System

mocode supports operational modes that change how permissions are handled. Modes provide a quick way to switch between safety and convenience.

### Available Modes

**normal** (default):
- Respects the `permission` configuration rules
- Prompts for `ask` actions
- Blocks `deny` actions

**yolo**:
- Auto-approves all tools except dangerous commands
- Dangerous commands are detected by pattern matching on `bash` tool commands
- Useful for rapid development with safety guardrails

### Mode Configuration

Modes are configured at runtime and are **not persisted** to the config file. Default modes are initialized automatically.

```json
{
  "modes": {
    "normal": {
      "auto_approve": false
    },
    "yolo": {
      "auto_approve": true,
      "dangerous_patterns": [
        "rm ", "rmdir ", "dd ", "mv ", "del ",
        "chmod ", "chown ", "sudo ", "format ", "mkfs ",
        "fdisk ", "mkfs ", "dd ", " shred "
      ]
    }
  },
  "current_mode": "normal"
}
```

Note: The `modes` and `current_mode` fields shown above are for reference only. They are in-memory runtime values and are NOT persisted to `config.json` when saving.

### Switching Modes

#### CLI

```bash
/mode              # Show current mode
/mode list         # List all available modes
/mode yolo         # Switch to yolo mode
/mode normal       # Switch back to normal mode
```

#### SDK

```python
from mocode import MocodeClient

client = MocodeClient()

# Check current mode
print(client.config.current_mode)  # "normal"

# Switch mode
client.config.set_mode("yolo")
```

### How Yolo Mode Works

When in `yolo` mode:

1. **Non-dangerous tools**: Auto-approved (bypass `ask` permissions)
2. **Dangerous bash commands**: Still denied based on `dangerous_patterns`
3. **Deny permission rules**: Still respected (always blocked)
4. **Ask permission rules**: Auto-approved for non-dangerous commands

**Dangerous Command Detection**:
- Only applies to `bash` tool
- Checks if command starts with any pattern in `dangerous_patterns`
- Uses prefix matching (with trailing space/tab)
- Example: `"rm "` matches `"rm file.txt"` but not `"rmv file.txt"`

### Recommendations

- Use **normal** mode for production or sensitive environments
- Use **yolo** mode for rapid development when you trust the AI's suggestions
- Customize `dangerous_patterns` in `yolo` mode to match your safety requirements
- Keep `deny` permission rules for extra-sensitive operations even in yolo mode
