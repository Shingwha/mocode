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

One-shot mode — run one prompt and exit, with piped stdin as context:

```bash
mocode -p "Explain what mocode/core/agent.py does"

# with piped input as context
cat error.log | mocode -p "Summarize these errors"

# the prompt itself from stdin ("-" is the marker)
cat task.txt | mocode -p -
```

Other entry points: `mocode plugin install <git-url | local-path>` (and
`list` / `sync` / `remove`) manages plugins without starting a conversation;
the library entry is [Embedding](#embedding) below.

---

## Configuration

MoCode reads `~/.mocode/config.json`. The file is organised by who owns each value: connection details per provider, model facts per model, and host policy on its own.

```jsonc
{
  "provider": "commandcode",               // the default for new conversations
  "model": "deepseek/deepseek-v4.1-flash",

  "agent": {                       // host policy — the same whatever model is loaded
    "tool_timeout": 240,           // seconds before a tool call is abandoned
    "max_iterations": 0            // 0 = no limit on tool-calling rounds
  },

  "providers": {
    "commandcode": {
      "name": "Command Code",
      "base_url": "https://api.commandcode.ai/provider/v1",
      "api_key": "sk-...",         // optional — see below
      "models": [                  // ordered array; "id" is the unique key
        {
          "id": "deepseek/deepseek-v4.1-flash",
          "name": "DeepSeek V4.1 Flash",  // optional display name
          "context_window": 1000000,      // optional; for plugins that budget context
          "max_tokens": 65536,            // optional; no cap is sent when absent
          "efforts": ["low", "high", "max"],  // optional reasoning-level table
          "effort": "high",               // optional level sent with the request
          "retry": { "max_attempts": 3 }  // optional; per-model retry override
        },
        {
          "id": "xiaomi/mimo-v2.6-pro",
          "efforts": ["high", "xhigh", "max"],  // any custom levels are allowed
          "effort": "xhigh"
        }
      ]
    }
  },

  "plugins": {
    "shell": { "enabled": false },
    "mcp": { "default_exposure": "auto" },
    "codemode": { "enabled": true }
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

MoCode sends **no** `max_tokens` unless you set `max_tokens` on the model. Guessing low silently truncates answers, and guessing high can be rejected outright, so the server's own default applies until you say otherwise.

### Model facts

`context_window` and `max_tokens` are optional and stay absent until you fill them in — MoCode never invents a model's limits. They are what plugins read (a compaction hook, for example, needs the window size) and what the request carries.

Reasoning effort is a model fact too. A model entry may declare its own ordered level table with `efforts` — absent, the default triple `low` / `medium` / `high` stands, and any custom names (`["high", "xhigh", "max"]`, say) are allowed. The chosen level is sent on the wire verbatim as the API's standard `reasoning_effort` field; `effort` on the entry is the level new conversations start from, and when it is absent the request carries no such parameter and the server decides entirely on its own. `/effort` (no argument) reports the level and the table; `/effort high` switches the level for the current conversation at runtime, without touching the file — a host built-in command every frontend gets.

Endpoints that need provider-specific request fields (DeepSeek's `thinking`, llama.cpp's samplers, and so on) take a custom provider type registered in code — see [docs/providers.md](docs/providers.md).

`> /model` switches provider and model for the current conversation *and* remembers the choice as the default for new ones, which is the only thing a running MoCode writes back to the file.

Keys MoCode does not recognise are preserved untouched when it saves.

---

## Built-ins

The plugins MoCode ships are ordinary plugins: disable any of them with
`"plugins": { "<name>": { "enabled": false } }` and contribute your own instead.

| Plugin | Contributes |
|---|---|
| `filesystem` | `read` (with line numbers, offset/limit, directory listings), `write`, `edit` — paths resolve into the conversation's project |
| `shell` | `bash`, `bash_output`, `kill_shell` — one persistent bash session per conversation; cwd, env and background jobs survive between calls |
| `skills` | the `skill` tool, a `/skill:<name>` command per skill, the prompt's skills section |
| `mcp` | connects to MCP servers from `mcp.json` / config and registers their tools as `mcp__<server>__<tool>`; `mcp_status` answers program calls with the server table |
| `codemode` | the `codemode` tool — the model writes a Python script that calls other tools in parallel; only the script's output comes back |
| `default-prompts` | the four sections a fresh prompt starts with: `guidelines`, `agents`, `environment`, `time` |
| `session` | `/export` (JSON to resume, Markdown to read), `/clear` |
| `help` | `/help` — lists every registered command, plugin ones included |
| `effort` | `/effort` — shows or sets the reasoning level for this conversation |
| `cache-protect` | pins the request prefix, so a session keeps its provider-side cache; drift arrives as `[context update]` notices (a hook, no tools) |

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
├── mcp.json                 MCP servers (standard; served by the `mcp` builtin)
├── mocode/plugin.py         contributions to the agent: tools, commands, hooks, prompt
└── mocode.cli/plugin.py     contributions to the terminal: chrome only it can honour
```

Restart MoCode and a dropped-in plugin is live — or install one without
leaving the shell with `mocode plugin install <git-url | local-path>` (add
`--project` to land in `./.mocode/plugins/`). A single `<name>.py` file next
to the plugin directories works too, for a plugin with no portable parts.

The layout, the manifest rules, hooks, tools, the environment a plugin may
declare for itself and a complete example live in
[docs/plugins.md](docs/plugins.md) and [examples/plugins/git-status](examples/plugins/git-status).
Plugins are trusted code — installing one runs it.

---

## Embedding

MoCode is a library before it is a CLI. One runtime opens as many conversations as you like, in as many projects, on as many models, at the same time:

```python
from mocode import MoCode

mc = MoCode()                                    # config + home
conv = mc.new_conversation(cwd="/srv/proj-a")     # one conversation
async for event in conv.stream("list the tests"):
    ...                                          # text, tool calls, usage

answer = await conv.chat("list the tests")        # just the final text
conv.state.to_dict()                              # status endpoint
conv.save()                                       # → ~/.mocode/sessions/…
```

A conversation owns its project, its model, its history and its event stream; `conv.subscribe(since=seq)` is how a reader catches up, including one that reconnects. See [docs/embedding.md](docs/embedding.md).

---

## Interactive Commands

Two registries, one way to type them: slash-commands a *shared* command
registry answers for every frontend, and the commands that need a picker or
the clipboard — which only this frontend has.

| Command | From | Description |
|---|---|---|
| `/help` | host (`help`) | Show all commands |
| `/export [json\|md]` | host (`session`) | Export the current session |
| `/clear` | host (`session`) | Save and clear the conversation |
| `/effort [level]` | host (`effort`) | Show or set the reasoning effort for this conversation |
| `/skill:<name>` | host (`skills`) | Inject a skill into the conversation |
| `/model` | terminal | Switch provider/model (and remember it as the default) |
| `/resume [file.json]` | terminal | Browse and resume sessions, or load an exported file |
| `/copy` | terminal | Copy the last response to the clipboard |
| `/quit` | terminal | Exit |

---

## AGENTS.md

Two files are merged into the system prompt, global first:

- `~/.mocode/AGENTS.md` — instructions for all your projects
- `./AGENTS.md` — instructions for this project

The prompt sections, `/export`, `/clear`, `/help` and `/effort` are
contributions from MoCode's built-in plugins (`default-prompts`, `session`,
`help`, `effort`) — ordinary plugins you can disable with
`"plugins": { "default-prompts": { "enabled": false } }`, the same way as any
other.

A session's prompt and tool interface stay frozen while it runs, so the provider's prefix cache survives turn after turn. An AGENTS.md you edit mid-session is not written into that frozen prompt — the model is told what moved as a `[context update]` diff, just before your next message.

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

## Documentation

| Doc | For |
|---|---|
| [docs/plugins.md](docs/plugins.md) | writing a plugin |
| [docs/embedding.md](docs/embedding.md) | embedding MoCode in an application |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | the module map and how the layers reason |
| [docs/providers.md](docs/providers.md) | the Provider protocol, writing a provider |
| [docs/testing.md](docs/testing.md) | testing a plugin against a scripted model |
| [docs/api.md](docs/api.md) | the public surface, layer by layer |
| [examples/core/](examples/core) | agents built from `core/` alone, runnable |

Contributors work under [AGENTS.md](AGENTS.md).

---

## License

MIT
