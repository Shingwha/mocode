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

### Gateway Mode

Gateway lets MoCode work as a chatbot on messaging platforms. Two channels are supported:

#### WeChat (Weixin)

```bash
mocode gateway --type weixin
```

On first run, a QR code is displayed — scan it with WeChat to connect.

#### Feishu (Lark)

```bash
mocode gateway --type feishu
```

Uses WebSocket long connection — no public IP or webhook required.

Setup on [Feishu Open Platform](https://open.feishu.cn/):
1. Create an enterprise app
2. Enable Bot capability
3. Event subscription → select "Use WebSocket to receive events"
4. Add event: `im.message.receive_v1`
5. Copy App ID and App Secret to config

#### Multi-Channel Auto-Discovery

```bash
# Auto-start all enabled channels from config
mocode gateway

# Explicit single channel
mocode gateway --type feishu
```

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
    }
  },
  "gateway": {
    "channels": {
      "weixin": {
        "enabled": false
      },
      "feishu": {
        "enabled": true,
        "app_id": "cli_xxxxxxxxxxxx",
        "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxx",
        "encrypt_key": "",
        "verification_token": "",
        "allow_from": ["*"],
        "group_policy": "mention",
        "reply_to_message": false
      }
    }
  }
}
```

Gateway config fields (feishu):

| Field | Description | Default |
|-------|-------------|---------|
| `enabled` | Enable this channel | `false` |
| `app_id` | Feishu Open Platform App ID | required |
| `app_secret` | Feishu Open Platform App Secret | required |
| `encrypt_key` | Event encryption key | optional |
| `verification_token` | Event verification token | optional |
| `allow_from` | Allowed user IDs, `["*"]` = all | `["*"]` |
| `group_policy` | Group message policy: `open` or `mention` | `mention` |
| `reply_to_message` | Quote original message in reply | `false` |

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
  gateway/        # Gateway (WeChat, Feishu/Lark)
```

## License

MIT
