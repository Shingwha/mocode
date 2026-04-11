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
| `/plugin` | | Manage plugins |
| `/rtk` | | Token optimizer |
| `/exit` | `/q`, `/quit` | Exit |

## Gateway

Connect to WeChat via ClawBot:

```bash
mocode gateway
```

See [Gateway Documentation](docs/gateway.md) for details.

## Documentation

- [CLI Commands](docs/cli.md)
- [Provider Configuration](docs/provider.md)
- [Permission System](docs/permission.md)
- [Plugin System](docs/plugins.md)
- [Skills](docs/skills.md)
- [Gateway](docs/gateway.md)

## License

MIT
