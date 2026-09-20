# MoCode

A lean AI agent framework — a small, plugin-extensible core you can build custom agents on.

MoCode gives you an interactive REPL (or one-shot CLI) where an LLM can read and write files, run shell commands, and load reusable skills. Underneath it is a runtime an application embeds: one process can hold many conversations at once, each in its own project, on its own model. Everything beyond the kernel — and that includes the built-in tools — is a plugin you can replace, disable, or write yourself.

---

## Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) package manager

## Installation

```bash
# Direct install
uv tool install git+https://github.com/Shingwha/mocode.git

# Developer install
git clone https://github.com/Shingwha/mocode.git
cd mocode
uv tool install -e .
```

---

## Quick Start

```bash
# 1. Describe your provider and model in ~/.mocode/config.json (see Configuration)
#    Leave api_key out and export INTERN_API_KEY instead if you prefer.

# 2. Start chatting
mocode
> What does this project do?
```

One-shot mode:

```bash
mocode -p "Explain what mocode/core/agent.py does"

# with piped input as context
cat error.log | mocode -p "Summarize these errors"
```

---

## Configuration

MoCode reads `~/.mocode/config.json`. The file is organised by who owns each value: connection details per provider, model facts per model, and host policy on its own.

```jsonc
{
  "active_provider": "intern",
  "active_model": "Atria-Dawn-Preview",

  "agent": {                       // host policy — the same whatever model is loaded
    "tool_timeout": 240,           // seconds before a tool call is abandoned
    "max_iterations": 0            // 0 = no limit on tool-calling rounds
  },

  "providers": {
    "intern": {
      "name": "Intern Discovery",
      "base_url": "https://discovery-api.intern-ai.org.cn/v1",
      "api_key": "sk-...",         // optional — see below
      "models": {                  // keyed by model name
        "Atria-Dawn-Preview": {
          "context_window": 200000,  // optional; for plugins that budget context
          "max_output": 32768,       // optional; no cap is sent when absent
          "extra_body": {}           // optional; provider-specific request fields
        }
      }
    }
  },

  "plugins": {
    "shell": { "enabled": false }
  }
}
```

### API keys

Leave `api_key` out and MoCode looks for `INTERN_API_KEY` — the provider key, upper-cased, with non-alphanumerics as underscores (`my-gateway` → `MY_GATEWAY_API_KEY`). Nothing to declare:

```bash
export INTERN_API_KEY=sk-...
```

An explicit `api_key` in the file wins over the environment.

### Output caps

MoCode sends **no** `max_tokens` unless you set `max_output` on the model. Guessing low silently truncates answers, and guessing high can be rejected outright, so the server's own default applies until you say otherwise.

### Model facts

`context_window` and `max_output` are optional and stay absent until you fill them in — MoCode never invents a model's limits. They are what plugins read (a compaction hook, for example, needs the window size) and what the request carries.

Any OpenAI-compatible API works — just set `base_url`. Per-model `extra_body` passes provider-specific fields straight through (DeepSeek's `thinking`, llama.cpp's samplers, and so on).

`> /model` switches provider and model and writes the choice back to the file.

Keys MoCode does not recognise are preserved untouched when it saves.

---

## Built-in Tools

Shipped as the `filesystem` and `shell` plugins:

| Tool | Plugin | Description |
|------|--------|-------------|
| `read` | `filesystem` | Read a file with line numbers (supports offset/limit, lists directories) |
| `write` | `filesystem` | Write or append to a file |
| `edit` | `filesystem` | Find-and-replace in a file |
| `bash` | `shell` | Run commands in a persistent bash session (cwd and env survive) |
| `skill` | `skills` | Load a skill's instructions by name |

Need `glob`, `grep`, web fetch, sub-agents or context compaction? Those are plugins. See [docs/plugins.md](docs/plugins.md) — a sub-agent tool is about 40 lines on top of the kernel's `derive()` primitive.

---

## Skills

A skill is a directory with a `SKILL.md` (YAML frontmatter + instructions) and optional reference files the agent can read.

```
~/.mocode/skills/my-skill/SKILL.md       your own, every project
./.mocode/skills/my-skill/SKILL.md       your own, this project
<plugin>/skills/my-skill/SKILL.md        shipped by a plugin
```

```markdown
---
name: my-skill
description: What this skill does, and when to use it.
---

Instructions the agent follows once this skill is loaded.
```

Two ways to use one: `> /skill:my-skill` injects it into the conversation, or the agent calls the `skill` tool on its own when the description matches the request.

---

## Plugins

A plugin is a directory laid out the way the [Agent Plugins](https://agent-plugins.org) standard defines: the root holds what any compatible client understands, and client-specific code lives under a namespace directory.

```
./.mocode/plugins/git-helper/     project-local (wins on name conflicts)
~/.mocode/plugins/git-helper/     user-global
├── plugin.json              the manifest — name, version, description
├── skills/<name>/SKILL.md   portable skills (standard)
├── mcp.json                 MCP servers (standard; not served yet)
├── mocode/plugin.py         contributions to the agent: tools, commands, hooks, prompt
└── mocode.cli/plugin.py     contributions to the terminal: chrome only it can honour
```

```python
# mocode/plugin.py — works in every frontend
from mocode.plugins import Plugin, Tool

class GitStatusTool(Tool):
    def __init__(self, cwd):
        super().__init__(
            name="git_status",
            description="Show the working tree status of the project.",
            params={},
            func=lambda args: "clean",
        )

class GitHelperPlugin(Plugin):
    name = "git-helper"

    def build(self, ctx):
        ctx.tools.register(GitStatusTool(ctx.cwd))
        # also: ctx.register(Command(...)), ctx.hooks.append(...),
        #       ctx.prompt_sections.append(Section(...))
```

Restart MoCode and it is live. A complete example with both surfaces lives in [examples/plugins/git-status](examples/plugins/git-status). Plugins are trusted code — installing one runs it.

A single `<name>.py` file next to the plugin directories works too, for a plugin with no portable parts.

---

## Embedding

MoCode is a library before it is a CLI. One runtime opens as many conversations as you like, in as many projects, on as many models, at the same time:

```python
from mocode import MoCode

mc = MoCode()                                        # config + home
conv = mc.new_conversation(cwd="/srv/proj-a")         # one conversation
async for event in conv.chat("list the tests"):
    ...                                              # text, tool calls, usage

conv.state.to_dict()                                 # status endpoint
conv.save()                                          # → ~/.mocode/sessions/…
```

A conversation owns its project, its model, its history and its event stream; `conv.subscribe(since=seq)` is how a reader catches up, including one that reconnects. See [docs/embedding.md](docs/embedding.md).

---

## Interactive Commands

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/model` | Switch provider/model |
| `/resume [file.json]` | Browse and resume sessions |
| `/export [json\|md]` | Export the current session |
| `/clear` | Save and clear the conversation |
| `/copy` | Copy the last response to the clipboard |
| `/skill:<name>` | Inject a skill into the conversation |
| `/quit` | Exit |

---

## AGENTS.md

Two files are merged into the system prompt, global first:

- `~/.mocode/AGENTS.md` — instructions for all your projects
- `./AGENTS.md` — instructions for this project

The prompt sections, `/export`, `/clear` and `/help` are contributions from
MoCode's built-in plugins (`default-prompts`, `session`, `help`) — ordinary
plugins you can disable with `"plugins": { "default-prompts": { "enabled":
false } }`, the same way as any other.

A session's prompt and tool interface stay frozen while it runs, so the
provider's prefix cache survives turn after turn. An AGENTS.md you edit
mid-session is not written into that frozen prompt — the model is told what
moved as a `[context update]` diff, just before your next message.

---

## Project Layout

```
~/.mocode/
├── config.json      # Providers, models, plugin switches
├── AGENTS.md        # Global instructions
├── sessions/        # Saved conversations
├── skills/          # Your skills
└── plugins/         # Your plugins
```

---

## License

MIT
