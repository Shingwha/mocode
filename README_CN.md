# mocode

基于 LLM 的 CLI 编程助手，支持工具调用。

[English](README.md)

## 功能特性

- 交互式 CLI，实时流式输出
- 内置文件操作、搜索、Shell 执行工具
- 多供应商支持（OpenAI、Claude、DeepSeek 等）
- SDK 模式，可嵌入应用
- Gateway 模式，多渠道机器人（Telegram）
- 可扩展技能系统
- 细粒度权限控制
- Session 管理 - 保存和恢复对话历史
- RTK 集成 - 减少 60-90% token 消耗
- 模块化 Prompt 系统，支持缓存
- 交互式命令选择，支持键盘导航

## 文档

- [供应商配置](docs/provider.md)
- [权限系统](docs/permission.md)
- [Gateway 模式](docs/gateway.md)
- [CLI 命令](docs/cli.md)
- [插件系统](docs/plugins.md)

## 前置条件

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) 包管理器

## 安装

### 方式一：直接安装（推荐普通用户）

无需克隆，直接从 Git 安装：

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

### 方式二：本地开发

克隆并安装用于开发：

```bash
git clone https://github.com/Shingwha/mocode.git
cd mocode
uv tool install -e .
```

详细安装说明请参阅 [安装指南](docs/installation.md)。

## 快速开始

```bash
mocode           # CLI 模式
mocode gateway   # Gateway 模式（Telegram 机器人）
```

## 配置

配置文件位于 `~/.mocode/config.json`：

```json
{
  "current": { "provider": "openai", "model": "gpt-4o" },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "permission": { "*": "ask", "bash": "allow", "read": "allow" }
}
```

## 命令

| 命令 | 别名 | 说明 |
|------|------|------|
| `/help` | `/h`, `/?` | 显示命令（交互式菜单） |
| `/model` | `/m` | 切换或管理模型 |
| `/provider` | `/p` | 切换或管理供应商 |
| `/session` | `/s` | 管理对话会话 |
| `/clear` | `/c` | 清空历史（自动保存 session） |
| `/skills` | | 列出技能 |
| `/plugin` | | 管理插件 |
| `/rtk` | | 管理 RTK（token 优化器） |
| `/exit` | `/q`, `quit` | 退出 |

### 交互式菜单

`/model`、`/provider`、`/session` 等命令支持键盘导航的交互式选择：
- 方向键导航
- 回车选择
- ESC 取消

## Session 管理

清空历史时自动保存 session，可随时恢复：

```bash
/session          # 交互式选择 session
/session list     # 列出所有 session
/session restore <id>  # 恢复指定 session
```

Session 按工作目录隔离存储在 `~/.mocode/sessions/`。

## RTK 集成

[RTK (Rust Token Killer)](https://github.com/rtk-ai/rtk) 通过智能过滤、分组、截断、去重等策略减少 60-90% token 消耗。

```bash
/rtk         # 查看 RTK 状态和统计
/rtk on      # 启用 RTK 包装
/rtk off     # 禁用 RTK 包装
/rtk install # 自动安装（Windows）
```

## SDK 使用

```python
from mocode import MocodeClient, EventType, PromptBuilder, StaticSection

# 基本用法
async def main():
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {"openai": {"api_key": "sk-...", "base_url": "https://api.openai.com/v1"}}
    })

    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(e.data))
    await client.chat("你好！")

    # 中断当前操作
    client.interrupt()

    # Session 管理
    client.save_session()
    sessions = client.list_sessions()
    client.load_session(sessions[0].id)

    # 清空历史并自动保存
    client.clear_history_with_save()

    # 插件管理
    plugins = client.list_plugins()
    client.enable_plugin("my-plugin")
    client.disable_plugin("my-plugin")
    info = client.get_plugin_info("my-plugin")

asyncio.run(main())
```

### 自定义 Prompt 构建

```python
# 构建自定义系统提示
builder = PromptBuilder()
builder.add(StaticSection("custom", 100, "你的自定义指令"))
client = MocodeClient(prompt_builder=builder)
```

## 技能

技能存放在 `~/.mocode/skills/` 目录：

```markdown
---
name: my-skill
description: 技能描述
---

# 指令
详细的 LLM 指令内容...
```

## 架构

mocode 采用分层架构，核心独立于 CLI，可作为库使用。

```
mocode/
├── sdk.py              # MocodeClient - MocodeCore 的薄外观层
├── main.py             # 入口（CLI 或 gateway 模式）
├── paths.py            # 集中式路径配置
├── core/               # 核心逻辑（独立于 UI）
│   ├── orchestrator.py      # MocodeCore - 核心协调器
│   ├── agent_facade.py      # AgentFacade - 高级 agent 操作
│   ├── session_coordinator.py # SessionCoordinator
│   ├── plugin_coordinator.py  # PluginCoordinator
│   ├── agent.py             # AsyncAgent - LLM 对话循环
│   ├── config.py            # 多供应商配置
│   ├── events.py            # EventBus - 实例化事件总线
│   ├── interrupt.py         # InterruptToken - 中断响应
│   ├── permission.py        # PermissionMatcher, PermissionHandler
│   ├── session.py           # SessionManager - 持久化
│   └── prompt/              # 模块化 prompt 系统
├── plugins/            # 插件/hook 系统
│   ├── base.py         # Plugin, Hook, HookPoint, PluginState
│   ├── manager.py      # PluginManager - 生命周期
│   ├── registry.py     # HookRegistry
│   ├── loader.py       # PluginLoader - 发现
│   └── builtin/rtk/    # RTK 插件（token 优化器）
├── gateway/            # 多渠道机器人支持
│   ├── base.py         # BaseChannel 抽象类
│   ├── config.py       # GatewayConfig
│   ├── manager.py      # GatewayManager
│   └── telegram.py     # TelegramChannel
├── providers/          # LLM 供应商
│   └── openai.py       # AsyncOpenAIProvider
├── tools/              # 工具实现
│   ├── base.py         # Tool 类和 ToolRegistry
│   ├── file_tools.py   # read, write, edit
│   ├── search_tools.py # glob, grep
│   └── bash.py         # BashSession, bash tool
├── skills/             # 技能系统
│   ├── manager.py      # SkillManager
│   ├── schema.py       # Skill 数据类
│   └── tool.py         # skill 工具实现
└── cli/                # 终端界面
    ├── app.py          # AsyncApp 主入口
    ├── commands/       # 斜杠命令
    │   ├── base.py     # Command 基类
    │   ├── builtin.py  # /help, /clear, /exit
    │   ├── model.py    # /model
    │   ├── provider.py # /provider
    │   ├── session.py  # /session
    │   ├── plugin.py   # /plugin
    │   └── skills.py   # /skills
    └── ui/             # 布局、颜色、组件
        ├── colors.py   # ANSI 颜色码
        ├── layout.py   # 终端布局
        ├── prompt.py   # SelectMenu, ask, Wizard
        ├── menu.py     # MenuItem, MenuAction
        └── permission.py # CLIPermissionHandler
```

### 核心设计模式

1. **分层架构**: `MocodeClient` (SDK) -> `MocodeCore` (协调器) -> Facades/Coordinators -> `AsyncAgent`。SDK 是薄外观层，`MocodeCore` 协调所有组件。

2. **事件系统**: `EventBus` 解耦 `AsyncAgent` 与 UI。关键事件：`TEXT_STREAMING`、`TEXT_DELTA`、`TEXT_COMPLETE`、`TOOL_START`、`TOOL_COMPLETE`、`PERMISSION_ASK`、`INTERRUPTED`。

3. **中断机制**: `InterruptToken` 提供线程安全的中断支持。CLI 使用 ESC 键，Gateway 使用 `/cancel` 命令，SDK 使用 `interrupt()` 方法。

4. **工具注册**: 通过 `@tool(name, description, params)` 装饰器注册工具。可选参数使用 `"type?"` 语法。

5. **权限系统**: `PermissionMatcher` 检查权限（allow/ask/deny）。`PermissionHandler` 抽象用户交互 - CLI 使用 `CLIPermissionHandler`，Gateway 自动批准。

6. **插件系统**: `PluginManager` 管理插件，hooks 在 `HookPoint`（`TOOL_BEFORE_RUN`、`TOOL_AFTER_RUN` 等）处拦截。RTK 是内置插件。插件从 `~/.mocode/plugins/` 和 `<project>/.mocode/plugins/` 发现。

7. **命令模式**: 斜杠命令通过 `@command` 装饰器和 `CommandRegistry`。命令：`/help`、`/model`、`/provider`、`/session`、`/plugin`、`/skills`、`/rtk`、`/clear`、`/exit`。

8. **技能系统**: 技能来自 `~/.mocode/skills/`。每个技能有 `SKILL.md` 和 YAML frontmatter。列在系统提示中，通过 `skill` 工具按需加载。

## 系统要求

- Python >= 3.10
- OpenAI 兼容 API

## 许可证

MIT
