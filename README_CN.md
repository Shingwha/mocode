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

## 安装

```bash
uv tool install -e .
```

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

## 项目结构

```
mocode/
├── sdk.py              # MocodeClient - SDK 入口
├── main.py             # 入口（CLI 或 gateway 模式）
├── paths.py            # 集中式路径配置
├── core/               # 核心逻辑（独立于 UI）
│   ├── agent.py        # AsyncAgent - LLM 对话循环
│   ├── config.py       # 多供应商配置
│   ├── events.py       # EventBus - 事件驱动通信
│   ├── interrupt.py    # InterruptToken - 中断支持
│   ├── permission.py   # PermissionMatcher, PermissionHandler
│   ├── session.py      # SessionManager - 对话持久化
│   └── prompt/         # 模块化 prompt 系统
│       ├── builder.py  # PromptBuilder（带缓存）
│       ├── sections.py # 内置 prompt 片段
│       └── templates.py
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
│   ├── shell_tools.py  # bash
│   ├── bash_session.py # SimpleBashSession
│   ├── rtk_wrapper.py  # RTK 集成
│   └── context.py      # ContextVar 工具配置
├── skills/             # 技能系统（可插拔扩展）
│   ├── manager.py      # SkillManager
│   ├── schema.py       # Skill 数据类
│   └── tool.py         # skill 工具实现
└── cli/                # 终端界面
    ├── app.py          # AsyncApp 主入口
    ├── commands/       # 斜杠命令系统
    │   ├── builtin.py  # /help, /clear, /exit
    │   ├── model.py    # /model 命令
    │   ├── provider.py # /provider 命令
    │   ├── session.py  # /session 命令
    │   ├── rtk.py      # /rtk 命令
    │   └── skills_command.py
    └── ui/             # 布局、颜色、组件
        ├── colors.py   # ANSI 颜色码
        ├── components.py
        ├── interactive.py  # Wizard, ask() 提示
        ├── keyboard.py     # getch, ESC 监控
        ├── layout.py       # 终端布局
        ├── permission_handler.py
        └── widgets.py      # SelectMenu
```

### 核心设计模式

1. **事件系统**: `EventBus` 解耦 `AsyncAgent` 与 UI。关键事件：`TEXT_STREAMING`、`TEXT_DELTA`、`TEXT_COMPLETE`、`TOOL_START`、`TOOL_COMPLETE`、`PERMISSION_ASK`、`INTERRUPTED`。

2. **中断机制**: `InterruptToken` 提供线程安全的中断支持。CLI 使用 ESC 键，Gateway 使用 `/cancel` 命令，SDK 使用 `interrupt()` 方法。

3. **工具注册**: 通过 `@tool()` 装饰器注册工具。可选参数使用 `"type?"` 语法。

4. **权限系统**: `PermissionMatcher` 检查工具权限（allow/ask/deny）。`PermissionHandler` 抽象用户交互。

5. **Session 管理**: `SessionManager` 按工作目录存储对话，清空历史时自动保存。

6. **Prompt 构建器**: 模块化 prompt 构建，支持 `StaticSection` 和 `DynamicSection`，带缓存和条件渲染。

## 系统要求

- Python >= 3.10
- OpenAI 兼容 API

## 许可证

MIT
