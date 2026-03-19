# Plugin System

mocode provides a powerful plugin system that allows you to extend and customize its behavior through hooks. Plugins can intercept and modify various stages of the conversation lifecycle, from user input to tool execution.

## Overview

The plugin system consists of three main components:

| Component | Description |
|-----------|-------------|
| **HookPoint** | Defines specific points in the execution flow where plugins can intercept |
| **Hook** | A function or class that executes at a specific HookPoint |
| **Plugin** | A collection of hooks, tools, and commands with lifecycle management |

## Hook Points

mocode provides the following hook points for interception:

### Plugin Lifecycle

| HookPoint | Description |
|-----------|-------------|
| `PLUGIN_LOAD` | Called when a plugin is loaded into memory |
| `PLUGIN_ENABLE` | Called when a plugin is enabled |
| `PLUGIN_DISABLE` | Called when a plugin is disabled |
| `PLUGIN_UNLOAD` | Called when a plugin is unloaded from memory |

### Agent Lifecycle

| HookPoint | Description |
|-----------|-------------|
| `AGENT_CHAT_START` | Called at the start of a chat, can modify user input |
| `AGENT_CHAT_END` | Called at the end of a chat, can access response and messages |

### Tool Lifecycle

| HookPoint | Description |
|-----------|-------------|
| `TOOL_BEFORE_RUN` | Called before a tool executes, can modify arguments |
| `TOOL_AFTER_RUN` | Called after a tool executes, can modify result |

### Message Lifecycle

| HookPoint | Description |
|-----------|-------------|
| `MESSAGE_BEFORE_SEND` | Called before a message is sent to the LLM |
| `MESSAGE_AFTER_RECEIVE` | Called after a message is received from the LLM |

### Prompt Lifecycle

| HookPoint | Description |
|-----------|-------------|
| `PROMPT_BUILD_START` | Called when prompt building starts |
| `PROMPT_BUILD_END` | Called when prompt building ends, can modify final prompt |

## Plugin Directory Structure

Plugins are discovered from the following directories:

```
~/.mocode/plugins/           # Global plugins
<project>/.mocode/plugins/   # Project-level plugins
```

### Plugin Structure

```
~/.mocode/plugins/my-plugin/
├── plugin.py       # Main plugin file (required)
├── plugin.yaml     # Metadata (optional)
└── config.json     # Plugin configuration (optional)
```

### plugin.yaml

```yaml
name: my-plugin
version: 1.0.0
description: A sample plugin
author: Developer
dependencies:
  - other-plugin
permissions:
  - tools.bash
```

## Creating a Plugin

### Method 1: Decorator-based Hooks

```python
# ~/.mocode/plugins/my-plugin/plugin.py

from mocode.plugins import Plugin, PluginMetadata, Hook, HookContext, HookPoint, hook

# Define hooks using the @hook decorator
@hook(HookPoint.AGENT_CHAT_START, name="log-chat", priority=20)
def log_chat_start(ctx: HookContext) -> HookContext:
    print(f"Chat started: {ctx.data.get('input', '')[:50]}...")
    return ctx

@hook(HookPoint.TOOL_AFTER_RUN, name="log-tool", priority=25)
def log_tool_execution(ctx: HookContext) -> HookContext:
    tool_name = ctx.data.get("name", "")
    print(f"Tool executed: {tool_name}")
    return ctx

# Plugin class
class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="my-plugin",
            version="1.0.0",
            description="A sample plugin",
        )

    def on_load(self) -> None:
        print("Plugin loaded")

    def on_enable(self) -> None:
        print("Plugin enabled")

    def on_disable(self) -> None:
        print("Plugin disabled")

    def on_unload(self) -> None:
        print("Plugin unloaded")

    def get_hooks(self) -> list[Hook]:
        # Return all decorator-based hooks
        return [log_chat_start, log_tool_execution]

# Entry point for plugin discovery
plugin_class = MyPlugin
```

### Method 2: Class-based Hooks

```python
# ~/.mocode/plugins/my-plugin/plugin.py

from mocode.plugins import Plugin, PluginMetadata, Hook, HookContext, HookPoint

class LogToolHook(Hook):
    """A hook that logs tool executions"""

    @property
    def name(self) -> str:
        return "log-tool"

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.TOOL_AFTER_RUN

    @property
    def priority(self) -> int:
        return 50

    def should_execute(self, context: HookContext) -> bool:
        # Only log bash commands
        return context.data.get("name") == "bash"

    def execute(self, context: HookContext) -> HookContext:
        tool_args = context.data.get("args", {})
        print(f"Bash executed: {tool_args.get('command', 'N/A')}")
        return context

class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(name="my-plugin", version="1.0.0")

    def on_load(self) -> None:
        pass

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass

    def on_unload(self) -> None:
        pass

    def get_hooks(self) -> list[Hook]:
        return [LogToolHook()]

plugin_class = MyPlugin
```

## HookContext

The `HookContext` class provides access to hook data and control flow:

```python
@dataclass
class HookContext:
    hook_point: HookPoint       # The hook point that triggered this hook
    data: dict[str, Any]        # Context data (input, args, result, etc.)
    result: Any                 # The result (can be modified)
    modified: bool              # Whether the result was modified
```

### Context Data by HookPoint

| HookPoint | Data Fields |
|-----------|-------------|
| `AGENT_CHAT_START` | `input` - User input string |
| `AGENT_CHAT_END` | `response` - Final response, `messages` - Message list |
| `TOOL_BEFORE_RUN` | `name` - Tool name, `args` - Tool arguments |
| `TOOL_AFTER_RUN` | `name` - Tool name, `result` - Tool result, `args` - Tool arguments |
| `MESSAGE_BEFORE_SEND` | `message` - Message dict |
| `MESSAGE_AFTER_RECEIVE` | `message` - Message dict |
| `PROMPT_BUILD_START` | (empty) |
| `PROMPT_BUILD_END` | (result contains the prompt) |

### Modifying Results

```python
@hook(HookPoint.AGENT_CHAT_START, priority=10)
def modify_input(ctx: HookContext) -> HookContext:
    user_input = ctx.data.get("input", "")

    # Modify the input
    if "[IMPORTANT]" in user_input:
        modified = user_input.replace("[IMPORTANT]", "[PRIORITY]")
        ctx.set_result(modified)  # Set modified result

    return ctx
```

### Stopping Propagation

```python
@hook(HookPoint.TOOL_BEFORE_RUN, priority=1)
def block_dangerous_commands(ctx: HookContext) -> HookContext:
    tool_name = ctx.data.get("name", "")
    args = ctx.data.get("args", {})

    if tool_name == "bash" and "rm -rf /" in str(args.get("command", "")):
        ctx.set_result("Blocked: dangerous command detected")
        ctx.stop_propagation()  # Stop further hooks from running

    return ctx
```

### Conditional Execution

```python
class ConditionalHook(Hook):
    @property
    def name(self) -> str:
        return "conditional-hook"

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.TOOL_AFTER_RUN

    @property
    def priority(self) -> int:
        return 50

    def should_execute(self, context: HookContext) -> bool:
        # Only execute for specific tools
        return context.data.get("name") in ["bash", "edit", "write"]

    def execute(self, context: HookContext) -> HookContext:
        print(f"Tool executed: {context.data.get('name')}")
        return context
```

## Priority System

Hooks are executed in order of priority (lower number = earlier execution):

```
Priority 1-10:   Pre-processing hooks (input modification, validation)
Priority 11-50:  Standard hooks (logging, monitoring)
Priority 51-90:  Post-processing hooks (result modification)
Priority 91-100: Final hooks (cleanup, reporting)
```

## Plugin Lifecycle

```
DISCOVERED → LOADED → ENABLED → DISABLED → DISCOVERED
                ↓         ↓
              ERROR     ERROR
```

| State | Description |
|-------|-------------|
| `DISCOVERED` | Plugin found but not loaded |
| `LOADED` | Plugin loaded into memory |
| `ENABLED` | Plugin active, hooks registered |
| `DISABLED` | Plugin loaded but inactive |
| `ERROR` | Plugin failed to load/enable |

## CLI Commands

### List Plugins

```bash
/plugin list
```

Output:
```
Discovered plugins:
--------------------------------------------------
  test-plugin v1.0.0 [enabled] - A test plugin
  my-plugin v1.0.0 [disabled]
--------------------------------------------------
Total: 2 plugin(s)
```

### Enable Plugin

```bash
/plugin enable my-plugin
```

### Disable Plugin

```bash
/plugin disable my-plugin
```

### Show Plugin Info

```bash
/plugin info my-plugin
```

Output:
```
Plugin: my-plugin
Status: enabled
Path: /home/user/.mocode/plugins/my-plugin
Version: 1.0.0
Description: A sample plugin
Author: Developer
```

## Configuration

Enable plugins automatically in `~/.mocode/config.json`:

```json
{
  "plugins": {
    "enabled": ["my-plugin", "another-plugin"],
    "disabled": ["old-plugin"],
    "settings": {
      "my-plugin": {
        "option1": "value1"
      }
    }
  }
}
```

## SDK Usage

```python
from mocode import MocodeClient, HookPoint, hook, HookContext

# Create client with plugin support
client = MocodeClient(config={...})

# List plugins
plugins = client.list_plugins()

# Enable plugin
client.enable_plugin("my-plugin")

# Disable plugin
client.disable_plugin("my-plugin")

# Access hook registry directly
@hook(HookPoint.AGENT_CHAT_START, priority=10)
def my_hook(ctx: HookContext) -> HookContext:
    print(f"Input: {ctx.data.get('input', '')}")
    return ctx

client.hook_registry.register(my_hook)
```

## Best Practices

### 1. Use Appropriate Priorities

```python
# Input validation should run early
@hook(HookPoint.AGENT_CHAT_START, priority=5)
def validate_input(ctx: HookContext) -> HookContext:
    # Validation logic
    return ctx

# Logging should run late
@hook(HookPoint.AGENT_CHAT_END, priority=90)
def log_response(ctx: HookContext) -> HookContext:
    # Logging logic
    return ctx
```

### 2. Check Data Existence

```python
@hook(HookPoint.TOOL_AFTER_RUN, priority=50)
def safe_hook(ctx: HookContext) -> HookContext:
    # Always use .get() with defaults
    tool_name = ctx.data.get("name", "unknown")
    result = ctx.data.get("result", "")
    return ctx
```

### 3. Handle Errors Gracefully

```python
@hook(HookPoint.TOOL_AFTER_RUN, priority=50)
def robust_hook(ctx: HookContext) -> HookContext:
    try:
        # Hook logic
        pass
    except Exception as e:
        # Log error but don't crash
        print(f"Hook error: {e}")
    return ctx
```

### 4. Use Conditional Hooks for Performance

```python
class SelectiveHook(Hook):
    def should_execute(self, context: HookContext) -> bool:
        # Skip expensive operations when not needed
        return context.data.get("name") == "bash"
```

## Example: Security Plugin

A plugin that blocks dangerous commands and logs all tool usage:

```python
from mocode.plugins import Plugin, PluginMetadata, Hook, HookContext, HookPoint

class SecurityHook(Hook):
    DANGEROUS_PATTERNS = ["rm -rf", "sudo rm", "mkfs", "dd if="]

    @property
    def name(self) -> str:
        return "security-check"

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.TOOL_BEFORE_RUN

    @property
    def priority(self) -> int:
        return 1  # Run first

    def should_execute(self, context: HookContext) -> bool:
        return context.data.get("name") == "bash"

    def execute(self, context: HookContext) -> HookContext:
        args = context.data.get("args", {})
        command = str(args.get("command", ""))

        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in command:
                context.set_result(f"Blocked by security plugin: '{pattern}' detected")
                context.stop_propagation()
                print(f"[Security] Blocked dangerous command: {command}")
                return context

        return context

class LoggingHook(Hook):
    @property
    def name(self) -> str:
        return "tool-logger"

    @property
    def hook_point(self) -> HookPoint:
        return HookPoint.TOOL_AFTER_RUN

    @property
    def priority(self) -> int:
        return 100

    def execute(self, context: HookContext) -> HookContext:
        tool_name = context.data.get("name", "")
        args = context.data.get("args", {})
        print(f"[Log] Tool={tool_name}, Args={args}")
        return context

class SecurityPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="security-plugin",
            version="1.0.0",
            description="Security and logging plugin",
            author="mocode",
        )

    def on_load(self) -> None:
        print("[SecurityPlugin] Loaded")

    def on_enable(self) -> None:
        print("[SecurityPlugin] Enabled - protecting against dangerous commands")

    def on_disable(self) -> None:
        print("[SecurityPlugin] Disabled")

    def on_unload(self) -> None:
        print("[SecurityPlugin] Unloaded")

    def get_hooks(self) -> list[Hook]:
        return [SecurityHook(), LoggingHook()]

plugin_class = SecurityPlugin
```
