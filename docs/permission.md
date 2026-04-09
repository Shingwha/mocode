# Permission System

mocode provides configurable permission control for tool execution.

## Actions

| Action | Description |
|--------|-------------|
| `allow` | Execute without prompting |
| `ask` | Prompt user for confirmation |
| `deny` | Block execution |

## Quick Configuration

```json
{
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": "allow"
  }
}
```

## Configuration Formats

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

### Nested Format (Bash Command Control)

For the `bash` tool, specify permissions for individual commands:

```json
{
  "permission": {
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "pwd": "allow",
      "git *": "allow",
      "rm *": "deny"
    }
  }
}
```

### Matching Priority

Rules are matched by priority: **specific tool rule > wildcard `*`**.

For bash commands with nested rules:
1. Exact match: `"pwd": "allow"` matches `pwd`
2. Prefix match: `"ls *": "allow"` matches `ls`, `ls -la`, `ls /home`
3. Wildcard `*`: Default fallback

## Recommendations

### Secure (Read-Only)

```json
{
  "permission": {
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "pwd": "allow",
      "find *": "allow",
      "grep *": "allow"
    },
    "read": "allow",
    "glob": "allow",
    "grep": "allow"
  }
}
```

### Convenient (Development)

```json
{
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": "allow"
  }
}
```

### Strict

```json
{
  "permission": {
    "*": "deny",
    "read": "allow"
  }
}
```

## Mode System

Modes provide quick switching between safety and convenience:

- **normal** (default): Respects permission rules
- **yolo**: Auto-approves non-dangerous tools

Switch modes:

```bash
/mode          # Show current mode
/mode yolo     # Switch to yolo
/mode normal   # Switch back
```

In yolo mode, dangerous bash commands (rm, mv, dd, etc.) are still blocked.
