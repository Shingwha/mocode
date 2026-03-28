---
name: mocode-plugin-dev
description: mocode 插件开发指南 - 如何创建 hooks、tools、commands
---

# mocode 插件开发指南

## 相关链接

- **项目地址**: https://github.com/Shingwha/mocode
- **官方插件**: https://github.com/Shingwha/mocode-plugins

## 插件位置

- 用户级: `~/.mocode/plugins/<name>/`
- 项目级: `<project>/.mocode/plugins/<name>/`

## 插件结构

```
my-plugin/
├── plugin.py          # 必需：插件主文件
├── plugin.yaml        # 可选：元数据（名称、版本、依赖等）
└── requirements.txt   # 可选：依赖包
```

## 最小示例

```python
# plugin.py
from mocode.plugins import Plugin, PluginMetadata, Hook, HookContext, HookPoint, hook

@hook(HookPoint.TOOL_AFTER_RUN, name="log-hook", priority=50)
def log_hook(ctx: HookContext) -> HookContext:
    """记录工具执行"""
    tool_name = ctx.data.get("name")
    print(f"[Hook] Tool executed: {tool_name}")
    return ctx

class MyPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="my-plugin",
            version="1.0.0",
            description="A sample plugin",
        )

    async def on_load(self) -> None:
        print("Plugin loaded")

    async def on_enable(self) -> None:
        print("Plugin enabled")

    async def on_disable(self) -> None:
        print("Plugin disabled")

    async def on_unload(self) -> None:
        print("Plugin unloaded")

    def get_hooks(self) -> list[Hook]:
        return [log_hook]

# 必须导出 plugin_class
plugin_class = MyPlugin
```

## Hook 点

Hook 点定义在 `HookPoint` 枚举中：

| Hook 点 | 触发时机 |
|---------|----------|
| `PLUGIN_LOAD` | 插件加载时 |
| `PLUGIN_ENABLE` | 插件启用时 |
| `PLUGIN_DISABLE` | 插件禁用时 |
| `PLUGIN_UNLOAD` | 插件卸载时 |
| `AGENT_CHAT_START` | 对话开始时 |
| `AGENT_CHAT_END` | 对话结束时 |
| `TOOL_BEFORE_RUN` | 工具执行前 |
| `TOOL_AFTER_RUN` | 工具执行后 |
| `MESSAGE_BEFORE_SEND` | 消息发送前 |
| `MESSAGE_AFTER_RECEIVE` | 消息接收后 |
| `PROMPT_BUILD_START` | Prompt 构建开始 |
| `PROMPT_BUILD_END` | Prompt 构建结束 |

## Hook 函数

使用 `@hook` 装饰器创建 hook：

```python
from mocode.plugins import hook, HookPoint, HookContext

@hook(HookPoint.TOOL_BEFORE_RUN, name="my-hook", priority=50)
def my_hook(ctx: HookContext) -> HookContext:
    # ctx.data 包含上下文数据
    # ctx.result 可以修改结果
    # ctx.stop_propagation() 可以停止传播
    return ctx
```

### HookContext

```python
@dataclass
class HookContext:
    hook_point: HookPoint      # 当前 hook 点
    data: dict[str, Any]       # 上下文数据
    result: Any                # 可修改的结果
    modified: bool             # 是否已修改

    def stop_propagation()     # 停止后续 hook 执行
    def set_result(result)     # 设置结果并标记已修改
    def set_error(error)       # 设置错误并停止传播
```

### 优先级

- 数字越小，执行越早
- 默认为 50
- 范围通常为 0-100

## 提供工具

插件可以提供工具：

```python
from mocode.tools.base import Tool, ToolRegistry

class MyPlugin(Plugin):
    def get_tools(self) -> list[Tool]:
        return [
            Tool(
                "my_tool",
                "My custom tool",
                {"param": "string"},
                self._my_tool_impl,
            )
        ]

    def _my_tool_impl(self, args: dict) -> str:
        return f"Result: {args.get('param')}"
```

## 提供命令

插件可以提供斜杠命令：

```python
from mocode.cli.commands.base import Command, CommandContext, command

@command("/my-cmd", description="My command")
class MyCommand(Command):
    def execute(self, ctx: CommandContext) -> bool:
        print("My command executed")
        return True

class MyPlugin(Plugin):
    def get_commands(self) -> list[Command]:
        return [MyCommand()]
```

## 提供 Prompt Section

插件可以向系统 prompt 添加内容：

```python
from mocode.core.prompt.builder import PromptContributions, StaticSection

class MyPlugin(Plugin):
    def get_prompt_sections(self) -> PromptContributions:
        return PromptContributions(
            add=[StaticSection("my-section", 100, "Custom instructions...")],
            disable=[],  # 要禁用的 section ID 列表
            replace={}   # {section_id: StaticSection} 替换指定 section
        )
```

**PromptContributions** 字段：
- `add`: 添加新的 prompt section 列表
- `disable`: 要禁用的 section ID 列表
- `replace`: 替换指定 section 的字典 `{section_id: PromptSection}`

## 插件元数据

```python
@dataclass
class PluginMetadata:
    name: str                    # 插件名称（必需）
    version: str = "1.0.0"       # 版本号
    description: str = ""        # 描述
    author: str = ""             # 作者
    dependencies: list[str]      # 依赖的其他插件
    permissions: list[str]       # 需要的权限
    replaces_tools: list[str]    # 要替换的工具
```

## 插件上下文

启用后可通过 `self.context` 访问：

```python
class MyPlugin(Plugin):
    async def on_enable(self) -> None:
        # 访问事件总线
        event_bus = self.context.event_bus
        # 订阅事件
        self.context.on_event(EventType.TEXT_COMPLETE, my_handler)
        # 注入消息到对话
        await self.context.inject_message("user", "context info")
        # 排队消息（等待 agent 空闲后发送）
        self.context.queue_message("user", "deferred message")
        # 获取当前消息列表
        messages = self.context.get_messages()
        # 当前工作目录
        workdir = self.context.workdir
        # 检查 agent 是否忙碌
        if self.context.is_agent_busy():
            pass
        # 当前会话 ID
        conv_id = self.context.current_conversation_id
```

## 生命周期

```
发现 (DISCOVERED)
    ↓ on_load()
加载 (LOADED)
    ↓ on_enable()
启用 (ENABLED)
    ↓ on_disable()
禁用 (DISABLED)
    ↓ on_unload()
卸载
```

## 完整示例：日志插件

```python
# plugin.py
from datetime import datetime
from mocode.plugins import Plugin, PluginMetadata, hook, HookPoint, HookContext

@hook(HookPoint.TOOL_AFTER_RUN, name="log-tool", priority=99)
def log_tool_execution(ctx: HookContext) -> HookContext:
    """记录所有工具执行"""
    tool_name = ctx.data.get("name", "unknown")
    tool_args = ctx.data.get("args", {})
    result = ctx.result

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "tool": tool_name,
        "args": tool_args,
        "success": not ctx.has_error,
    }

    # 写入日志文件
    with open("mocode-tools.log", "a") as f:
        f.write(f"{log_entry}\n")

    return ctx

class LogPlugin(Plugin):
    def __init__(self):
        self.metadata = PluginMetadata(
            name="log-plugin",
            version="1.0.0",
            description="Log all tool executions",
            author="Developer",
        )

    def get_hooks(self) -> list:
        return [log_tool_execution]

plugin_class = LogPlugin
```

## 调试

启用插件后查看日志：

```bash
mocode
/plugin list
/plugin enable my-plugin
```

如果插件加载失败，状态会显示为 ERROR，可通过 `/plugin info <name>` 查看错误信息。
