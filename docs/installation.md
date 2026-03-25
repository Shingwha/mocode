# Installation Guide

This guide covers different ways to install MoCode on your system.

## Prerequisites

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) package manager

If you don't have `uv` installed, you can install it with:

```bash
# On macOS and Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# On Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## Installation Methods

### Method 1: Install from Git Repository (Recommended)

Install directly from the Git repository without cloning:

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

### Method 2: Local Installation

Clone the repository and install locally:

```bash
# Clone the repository
git clone https://github.com/Shingwha/mocode.git
cd mocode

# Install as a tool (editable mode)
uv tool install -e .

# Or install in non-editable mode
uv tool install .
```

### Method 3: Run with uv run

If you prefer not to install globally, you can run directly from the source:

```bash
# Clone the repository
git clone https://github.com/Shingwha/mocode.git
cd mocode

# Run directly
uv run mocode
```

## Verify Installation

After installation, verify that MoCode is working:

```bash
# Check if mocode is available
mocode --help

# Or if using uv run
uv run mocode --help
```

## Configuration

After installation, you need to configure your LLM provider. Create a configuration file at `~/.mocode/config.json`:

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
      "api_key": "sk-your-api-key",
      "models": ["gpt-4o", "gpt-4o-mini"]
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "your-deepseek-api-key",
      "models": ["deepseek-chat", "deepseek-reasoner"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "read": "allow"
  },
  "modes": {
    "normal": { "auto_approve": false },
    "yolo": {
      "auto_approve": true,
      "dangerous_patterns": [
        "rm ", "rmdir ", "dd ", "mv ", "del ",
        "chmod ", "chown ", "sudo ", "format ", "mkfs "
      ]
    }
  },
  "current_mode": "normal"
}
```

For more details on configuration, see the [Provider Configuration](provider.md), [Permission System](permission.md), and [Mode System](cli.md#mode) documentation.

## Plugin Dependencies

Plugins may require additional Python dependencies. mocode uses `uv` to manage plugin dependencies in isolated virtual environments. When you enable a plugin with dependencies, mocode automatically:

1. Creates a virtual environment in `~/.mocode/plugins/<plugin-name>/.venv/`
2. Installs all dependencies listed in `plugin.yaml` or `requirements.txt`
3. Loads the plugin in the isolated environment

Make sure `uv` is installed and available in your PATH. If you don't have `uv` installed, see the [Prerequisites](#prerequisites) section.

## Updating

### Update from Git Repository

```bash
uv tool install --upgrade git+https://github.com/Shingwha/mocode.git
```

### Update Local Installation

```bash
cd mocode
git pull
uv tool install -e .
```

## Uninstallation

To uninstall MoCode:

```bash
uv tool uninstall mocode
```

## Troubleshooting

### Command not found

If `mocode` command is not found after installation, ensure that `uv` tool directory is in your PATH:

```bash
# Check where uv installs tools
uv tool dir

# Add to PATH (on macOS/Linux, add to your shell config)
export PATH="$HOME/.local/bin:$PATH"
```

### Permission errors

If you encounter permission errors, make sure you have write access to the uv tool directory:

```bash
# Check permissions
ls -la $(uv tool dir)
```

## Next Steps

- [CLI Commands Reference](cli.md)
- [Provider Configuration](provider.md)
- [Permission System](permission.md)
- [Plugin System](plugins.md)
