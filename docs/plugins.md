# Plugin System

Plugins extend mocode through hooks, tools, and custom slash commands.

## Quick Start

Create `~/.mocode/plugins/my-plugin/plugin.py`:

```python
from mocode.plugins import Plugin, PluginMetadata, hook, HookContext, HookPoint

@hook(HookPoint.TOOL_AFTER_RUN, name="log-tool", priority=25)
def log_tool(ctx: HookContext) -> HookContext:
    print(f"Tool: {ctx.data.get('name', '')}")
    return ctx

class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="my-plugin",
            version="1.0.0",
            description="My first plugin"
        )

    def get_hooks(self):
        return [log_tool]

plugin_class = MyPlugin
```

Enable with: `/plugin enable my-plugin`

## Hook Points

| HookPoint | When It Runs |
|-----------|-------------|
| `PLUGIN_LOAD` | Plugin loaded into memory |
| `PLUGIN_ENABLE` | Plugin activated |
| `PLUGIN_DISABLE` | Plugin deactivated |
| `PLUGIN_UNLOAD` | Plugin unloaded |
| `AGENT_CHAT_START` | Start of a chat |
| `AGENT_CHAT_END` | End of a chat |
| `TOOL_BEFORE_RUN` | Before tool execution |
| `TOOL_AFTER_RUN` | After tool execution |
| `PROMPT_BUILD_START/END` | Prompt construction |

## Tool Replacement

Plugins can replace built-in tools. Declare in `plugin.yaml`:

```yaml
name: better-write
replaces_tools:
  - write
```

Or in code:

```python
class BetterWritePlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="better-write",
            replaces_tools=["write"],
        )

    def get_tools(self):
        return [Tool("write", "Enhanced write", {...}, self._write)]

    def _write(self, args):
        # Custom implementation
        pass
```

## Dependencies

List dependencies in `plugin.yaml`:

```yaml
name: my-plugin
dependencies:
  - requests>=2.28.0
  - numpy>=1.24.0
```

Dependencies are installed automatically in an isolated virtual environment when the plugin is enabled.

## Installation

```bash
/plugin install https://github.com/username/plugin-repo.git
/plugin uninstall my-plugin
/plugin update my-plugin
```

## Discovery Locations

- `~/.mocode/plugins/` — Global plugins
- `<project>/.mocode/plugins/` — Project plugins

## Example: Security Plugin

```python
from mocode.plugins import Plugin, PluginMetadata, HookBase, HookContext, HookPoint

class SecurityHook(HookBase):
    DANGEROUS = ["rm -rf", "sudo rm", "mkfs", "dd if="]

    @property
    def name(self): return "security-check"

    @property
    def hook_point(self): return HookPoint.TOOL_BEFORE_RUN

    @property
    def priority(self): return 1  # Run first

    def should_execute(self, ctx: HookContext) -> bool:
        return ctx.data.get("name") == "bash"

    def execute(self, ctx: HookContext) -> HookContext:
        cmd = str(ctx.data.get("args", {}).get("command", ""))
        for pattern in self.DANGEROUS:
            if pattern in cmd:
                ctx.set_result(f"Blocked: {pattern}")
                ctx.stop_propagation()
                return ctx
        return ctx

class SecurityPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="security-plugin",
            version="1.0.0",
            description="Security and logging plugin"
        )

    def get_hooks(self):
        return [SecurityHook()]

plugin_class = SecurityPlugin
```
