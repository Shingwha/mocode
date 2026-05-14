# MoCode

An LLM-powered assistant. v0.2 is a rewrite using modular, dependency-injection architecture.

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Direct install
uv tool install git+https://github.com/Shingwha/mocode.git
```

### Developer Install

```bash
git clone https://github.com/Shingwha/mocode.git
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
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com",
      "api_key": "your-api-key",
      "models": ["deepseek-v4-pro", "deepseek-chat"],
      "extra_body": {
        "deepseek-v4-pro": {"thinking": {"type": "enabled"}}
      }
    }
  },
  "image": {
    "enabled": true,
    "base_url": "https://api.openai.com",
    "api_key": "your-api-key",
    "model": "gpt-image-2"
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

Provider config fields:

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Display name | required |
| `base_url` | API endpoint | required |
| `api_key` | API key | required |
| `models` | Available model names | `[]` |
| `extra_body` | Per-model extra request params, `{model_name: {...}}` | `null` |

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

Image config fields:

| Field | Description | Default |
|-------|-------------|---------|
| `enabled` | Enable image generation tool | `false` |
| `base_url` | OpenAI-compatible image API endpoint | `https://api.openai.com` |
| `api_key` | API key | required |
| `model` | Model name | `gpt-image-2` |

Generated images are saved to `~/.mocode/media/images/` by default. Use the `output_dir` parameter to override.

More options see [CLAUDE.md](CLAUDE.md).

## Skills

Skills are plugins that extend MoCode's capabilities. Each skill is a folder containing a `SKILL.md` file with instructions for the LLM. When a user's request matches a skill's description, MoCode automatically loads the skill and follows its instructions.

### How Skills Work

1. At startup, MoCode scans skill directories and reads each `SKILL.md` frontmatter
2. Skill names and descriptions are injected into the system prompt as a menu
3. When the LLM decides a skill is relevant, it calls the `skill` tool to load the full instructions
4. The LLM then follows the skill's Markdown body to complete the task

### Installing Skills

Skills can be placed in two locations:

| Location | Path | Priority |
|----------|------|----------|
| Global | `~/.mocode/skills/<skill-name>/SKILL.md` | Low |
| Project-local | `<project>/.mocode/skills/<skill-name>/SKILL.md` | High (overrides global) |

To install a skill, simply place its folder in the appropriate directory:

```bash
# Global skill (available in all projects)
cp -r my-skill/ ~/.mocode/skills/my-skill/

# Project-local skill (available only in this project)
cp -r my-skill/ .mocode/skills/my-skill/
```

If both locations have a skill with the same name, the project-local one takes precedence.

### Creating a Skill

A skill is just a directory with a `SKILL.md` file. The file uses YAML frontmatter for metadata and Markdown body for instructions:

```
my-skill/
  SKILL.md       # Required: metadata + instructions
  scripts/       # Optional: helper scripts
  references/    # Optional: reference documents
  assets/        # Optional: images, fonts, etc.
```

#### SKILL.md Format

```markdown
---
name: my-skill
description: |
  One-line description of what this skill does.
  This is shown to the LLM as a menu item, so be specific about when to use it.
metadata:
  author: your-name
  version: "1.0.0"
  category: some-category
---

# Skill Instructions

Write your detailed instructions here in Markdown.
This content is loaded when the LLM calls the `skill` tool.

## What you can do
- Step-by-step instructions for the LLM
- Reference patterns, templates, or conventions
- Any rules or constraints to follow

## Example
\`\`\`python
# Code examples if needed
print("Hello from my-skill!")
\`\`\`
```

#### Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Skill identifier, used for lookup. Must be unique across all skill directories. |
| `description` | Yes | Shown in system prompt. Write a clear description of **when** to use this skill. |
| `metadata` | No | Arbitrary key-value pairs (e.g., `author`, `version`, `category`). |
| `license` | No | License identifier (e.g., `MIT`). |

The `description` is the most important field — it determines when the LLM decides to use your skill. Write it as a trigger: "Use this skill when the user wants to..."

#### Minimal Example

Create a file at `~/.mocode/skills/hello/SKILL.md`:

```markdown
---
name: hello
description: |
  Use when the user says hello or asks for a greeting.
  Returns a friendly greeting message.
metadata:
  author: demo
---

When the user greets you, respond with a warm, creative greeting.
Include a fun fact or joke to make their day better.
```

Restart MoCode and the skill will be available. The LLM will see `<hello>Use when the user says hello...</hello>` in its prompt and automatically invoke the skill when appropriate.

### Using Skills

Skills are used automatically — you don't need to call them explicitly. The LLM reads skill descriptions from the system prompt and decides when to load one. For example, if you have a `minimax-docx` skill installed and ask "create a Word document with ...", the LLM will load that skill and follow its instructions.

### Listing Available Skills

Currently there is no CLI command to list skills. You can check the directories directly:

```bash
# Global skills
ls ~/.mocode/skills/

# Project-local skills
ls .mocode/skills/
```

### Tips

- **Keep descriptions specific** — vague descriptions like "helpful tool" won't trigger correctly
- **Put large references in separate files** — the LLM can read auxiliary files from the skill directory
- **Use project-local skills for project-specific workflows** — e.g., a skill that knows your team's PR template
- **Hot reload** — call `SkillManager.refresh()` or restart MoCode to pick up new skills

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
