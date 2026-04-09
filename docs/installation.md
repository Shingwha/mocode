# Installation Guide

Install mocode using uv, the Python package manager.

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/)

Install uv:

```bash
# macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Install

```bash
uv tool install mocode
```

Or from Git:

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

## Verify

```bash
mocode --help
```

## Configure

Create `~/.mocode/config.json` with your LLM provider:

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
  }
}
```

See [Provider Configuration](provider.md) for multi-provider setup.

## Update

```bash
uv tool install --upgrade mocode
```

## Uninstall

```bash
uv tool uninstall mocode
```
