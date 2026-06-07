# MoCode

A lean AI agent framework — build custom agents with composable tools, prompts, and providers.

MoCode gives you an interactive REPL (or CLI one-shot) where an LLM can read/write files, run shell commands, search code, delegate sub-tasks, and orchestrate multi-step workflows — all through a simple tool-based architecture you can extend with Python.

---

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

---

## Quick Start

```bash
# 1. Configure a provider (interactive wizard)
mocode
> /connect

# 2. Start chatting
> What does this project do?
```

Or one-shot mode:

```bash
mocode -p "Explain what mocode/core/agent.py does"
```

With piped input:

```bash
cat error.log | mocode -p "Summarize these errors"
```

---

## Configuration

MoCode stores all config in `~/.mocode/config.json`. The file is created when you first add a provider via `/connect`.

### Config Structure

```jsonc
{
  "active_provider": "deepseek",
  "active_model": "deepseek-v4-pro",
  "max_tokens": 8192,
  "tool_result_limit": 25000,
  "tool_timeout": 240,
  "providers": {
    "deepseek": {
      "name": "DeepSeek",
      "api_key": "sk-...",
      "base_url": "https://api.deepseek.com",
      "models": [
        {
          "name": "deepseek-v4-flash",
          "extra_body": {
            "thinking": { "type": "enabled" }
          }
        },
        {
          "name": "deepseek-v4-pro",
          "extra_body": {
            "thinking": { "type": "enabled" }
          }
        }
      ]
    },
    "mimo": {
      "name": "Xiaomi MiMo",
      "api_key": "tp-...",
      "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
      "models": [
        { "name": "mimo-v2.5-pro" },
        { "name": "mimo-v2.5" }
      ]
    }
  }
}
```

### Provider Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | No | Display name shown in `/model` |
| `api_key` | Yes | API key for the provider |
| `base_url` | No | Custom API endpoint (for OpenAI-compatible providers) |
| `models` | Yes | List of model names or `{name, extra_body}` objects |

### Global Settings

| Field | Default | Description |
|-------|---------|-------------|
| `active_provider` | — | Key of the currently active provider |
| `active_model` | — | Model name to use for chat |
| `max_tokens` | `8192` | Max tokens per LLM response |
| `tool_result_limit` | `25000` | Truncate tool results beyond this character count |
| `tool_timeout` | `240` | Seconds before a tool call times out |

### Managing Providers

Use the `/connect` command in the interactive REPL to add, edit, or delete providers. You can also edit `~/.mocode/config.json` directly.

```
> /connect          # opens interactive provider manager
> /model            # switch between providers and models
```

### Using Non-OpenAI Providers

Any OpenAI-compatible API works — just set `base_url`. The main config above shows DeepSeek and Xiaomi MiMo as examples. For local Ollama:

```jsonc
{
  "ollama": {
    "name": "Ollama (local)",
    "api_key": "ollama",
    "base_url": "http://localhost:11434/v1",
    "models": [{ "name": "llama3.1" }]
  }
}
```

### Per-Model `extra_body`

Pass provider-specific parameters on a per-model basis. For example, enable DeepSeek's extended thinking mode:

```jsonc
"models": [
  {
    "name": "deepseek-v4-pro",
    "extra_body": {
      "thinking": { "type": "enabled" }
    }
  }
]
```

---

## Built-in Tools

| Tool | Description |
|------|-------------|
| `read` | Read files with line numbers; supports offset/limit |
| `write` | Write or append content to files |
| `edit` | Find-and-replace text in files |
| `glob` | Find files matching a pattern |
| `grep` | Search file contents with regex |
| `bash` | Run shell commands in a persistent session |
| `fetch` | Fetch a URL and convert to Markdown |
| `skill` | Load a skill by name |
| `sub_agent` | Delegate a task to a sub-agent |

---

## Skills

Skills are directory-based extensions. Each skill has a `SKILL.md` (YAML frontmatter + content) and optional reference files.

**Built-in:** `workflow` — DAG-based multi-step task orchestration.

**Custom skills:** Place skill directories in `~/.mocode/skills/`. Each needs a `SKILL.md`:

```markdown
---
name: my-skill
description: What this skill does.
---

Instructions for the agent when this skill is loaded...
```

Load a skill in chat: `> /skill:my-skill` or use the `skill` tool.

---

## Workflows

Multi-step task orchestration as a YAML DAG. Each node runs an independent LLM session.

```yaml
name: code-review
params:
  - path
  - focus: "general"

nodes:
  - id: scan
    task: Scan {path} for issues, focus on {focus}.

  - id: report
    task: Summarize: {nodes.scan.output}
    depends: [scan]
```

```bash
> /workflow run code-review ./src security
```

Node types: `task` (LLM prompt), `router` (conditional routing), `map` (fan-out over a list).

---

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/connect` | Manage providers |
| `/model` | Switch provider/model |
| `/resume [id]` | Resume a previous session |
| `/export [json\|md]` | Export current session |
| `/clear` | Save and clear conversation |
| `/copy` | Copy last response to clipboard |
| `/compact` | Compress conversation to free context |
| `/init` | Analyze project and create AGENTS.md |
| `/fix <issue>` | Fix a bug with guided process |
| `/workflow` | Workflow management |
| `/quit` | Exit |

---

## AGENTS.md

MoCode reads `AGENTS.md` files to give the agent project-specific context:

- `~/.mocode/AGENTS.md` — global instructions (all projects)
- `./AGENTS.md` — project-level instructions (working directory)

Use `/init` to auto-generate a project AGENTS.md, or write one manually.

---

## Project Structure

```
~/.mocode/
├── config.json          # Provider & model config
├── AGENTS.md            # Global instructions
├── sessions/            # Saved conversation sessions
├── skills/              # Custom skills
└── workflow_runs/       # Workflow run results
```

---

## License

MIT
