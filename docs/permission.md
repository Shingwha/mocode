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
