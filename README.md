# mocode

A CLI coding assistant powered by LLM with tool-calling capabilities.

[中文文档](README_CN.md) | [Documentation](docs/)

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

### Developer Install

```bash
git clone https://github.com/Shingwha/mocode.git
cd mocode
uv tool install -e .
```

Update:

```bash
git pull
uv tool install -e .
```

## Quick Start

```bash
mocode
```

**First run?** Create `~/.mocode/config.json` with your API key (see [Configuration](#configuration) below).

## Configuration

Create `~/.mocode/config.json`:

```json
{
  "current": {
    "provider": "zhipu",
    "model": "glm-5"
  },
  "providers": {
    "zhipu": {
      "base_url": "https://open.bigmodel.cn/api/coding/paas/v4/",
      "api_key": "your-api-key",
      "models": ["glm-5"]
    },
    "step": {
      "base_url": "https://api.stepfun.com/step_plan/v1",
      "api_key": "your-api-key",
      "models": ["step-3.5-flash"]
    }
  },
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "git *": "allow",
      "rm *": "deny"
    }
  },
  "tool_result_limit": 25000
}
```

### Permission Rules

| Rule | Description |
|------|-------------|
| `*` | Default for all tools |
| `"allow"` | Always allow |
| `"ask"` | Prompt before running |
| `"deny"` | Always block |
| Nested object | Per-command rules (e.g. bash) |

### Modes

```bash
/mode          # Show current mode
/mode yolo     # Auto-approve safe tools
/mode normal   # Respect permission rules (default)
```

### CLI Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/`, `/h`, `/?` | Show help |
| `/provider` | `/p` | Switch provider |
| `/mode` | | Switch mode |
| `/session` | `/s` | Manage sessions |
| `/clear` | `/c` | Clear history |
| `/skills` | | List skills |
| `/exit` | `/q`, `/quit` | Exit |

### Context Compaction

Long conversations are automatically compressed when token usage exceeds a threshold (default 80% of context window). Old turns are summarized into a compact summary, keeping recent turns intact.

**Config** (in `~/.mocode/config.json`):

```json
{
  "compact": {
    "enabled": true,
    "threshold": 0.80,
    "keep_recent_turns": 4,
    "context_windows": {}
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `enabled` | `true` | Enable/disable auto-compact |
| `threshold` | `0.80` | Trigger ratio (prompt_tokens / context_window) |
| `keep_recent_turns` | `4` | Number of recent turns to preserve |
| `context_windows` | `{}` | Per-model context window size override (default: 128k) |

**Commands**:

| Command | Description |
|---------|-------------|
| `/compact` | Manually trigger compaction |
| `/compact status` | Show token usage and compact config |

**How it works**: When `prompt_tokens > threshold × context_window`, old messages are summarized via LLM into a structured summary (decisions, code state, pending tasks), then replaced with `[Context Summary]` + recent turns. Compaction only runs between conversation turns, never during tool execution.

## Gateway

Connect to WeChat via ClawBot:

```bash
mocode gateway
```

On first run, open the URL shown in the terminal in a browser and scan the QR code to connect to WeChat.

See [Gateway Documentation](docs/gateway.md) for details.

## Documentation

- [CLI Commands](docs/cli.md)
- [Gateway](docs/gateway.md)

## License

MIT
