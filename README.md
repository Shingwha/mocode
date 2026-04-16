# MoCode

An LLM-powered assistant. v0.2 is a rewrite using modular, dependency-injection architecture.

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Direct install (current version on refactor-v0.2 branch)
uv tool install git+https://github.com/Shingwha/mocode.git@refactor-v0.2
```

### Developer Install

```bash
# Clone specific branch
git clone -b refactor-v0.2 https://github.com/Shingwha/mocode.git
cd mocode

# Install in editable mode
uv tool install -e .
```

Update:

```bash
git pull
uv tool install -e .
```

## Current Feature

### Gateway Mode (WeChat)

```bash
mocode gateway --type weixin
```

On first run, a QR code is displayed — scan it with WeChat to connect.

## Configuration

Before first run, create config file `~/.mocode/config.json`:

```json
{
  "current": {
    "provider": "zhipu",
    "model": "glm-5"
  },
  "providers": {
    "zhipu": {
      "name": "Zhipu",
      "base_url": "https://open.bigmodel.cn/api/coding/paas/v4/",
      "api_key": "your-api-key",
      "models": ["glm-5.1", "glm-5"]
    },
    "step": {
      "name": "Step",
      "base_url": "https://api.stepfun.com/step_plan/v1",
      "api_key": "your-api-key",
      "models": ["step-3.5-flash", "step-3.5-flash-2603"]
    }
  }
}
```

More options see [CLAUDE.md](CLAUDE.md).

## Development

```bash
# Sync dependencies
uv sync
```

## Project Structure

```
mocode/
  app.py          # App entry + DI container
  agent.py        # LLM orchestration & tool execution
  provider.py     # LLM provider protocol
  tool.py         # Tool registry
  tools/          # Built-in tools
  skills/         # Plugin system
  dream/          # Background reflection system
  gateway/        # Gateway (WeChat, etc.)
```

## License

MIT
