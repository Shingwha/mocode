# Permission System

mocode provides configurable permission control for tool execution. Before each tool runs, the system checks the permission rules to allow, ask, or deny the operation.

## Actions

| Action | Description |
|--------|-------------|
| `allow` | Execute without prompting |
| `ask` | Prompt user for confirmation |
| `deny` | Block execution and return denied message |

## Configuration

Permissions are configured in `~/.mocode/config.json`:

```json
{
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
    "read": "allow",
    "glob": "allow",
    "grep": "allow"
  }
}
```

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

If no permission rules are configured, the default behavior is `ask`.

## CLI Interaction

When a tool requires permission (`ask`), an interactive menu appears:

```
? Permission required for bash
  ls -la

  > Allow (execute the tool)
    Deny (cancel the operation)
    Type something (provide custom response)
```

Options:
- **Allow** - Execute the tool
- **Deny** - Cancel and return denial message
- **Type something** - Provide custom input as tool result

## Recommendations

- Use `ask` for destructive operations: `bash`, `edit`, `write`
- Use `allow` for read-only operations: `read`, `glob`, `grep`
- Use `deny` to completely block sensitive tools
