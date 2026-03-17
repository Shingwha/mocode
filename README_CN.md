# mocode

基于 LLM 的 CLI 编程助手，支持工具调用。

[English](README.md)

## 功能特性

- **交互式 CLI** - 美观的终端界面，支持实时流式输出
- **工具调用** - 内置文件操作、搜索、Shell 执行工具
- **多供应商支持** - 支持 OpenAI 兼容 API（OpenAI、Claude、DeepSeek 等）
- **SDK 模式** - 可嵌入到其他应用中
- **Gateway 模式** - 作为多渠道机器人运行（支持 Telegram）
- **技能系统** - 通过自定义技能扩展功能
- **权限控制** - 细粒度的工具权限管理
- **中断支持** - 按 ESC 键取消长时间运行的操作

## 安装

```bash
# 使用 uv 安装
uv tool install -e .

# 或安装依赖
uv sync
```

## 快速开始

### CLI 模式

```bash
# 运行交互式 CLI
mocode

# 或使用 uv run
uv run mocode
```

### Gateway 模式

```bash
# 作为 Telegram 机器人运行
mocode gateway
```

## 配置

配置文件位于 `~/.mocode/config.json`：

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "models": ["deepseek-chat", "deepseek-coder"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "read": "allow"
  },
  "max_tokens": 8192,
  "gateway": {
    "channels": {
      "telegram": {
        "enabled": true,
        "token": "bot_token",
        "allowFrom": ["telegram_user_id"]
      }
    }
  }
}
```

### 权限设置

| 值 | 说明 |
|----|------|
| `allow` | 无需询问直接执行 |
| `ask` | 弹出确认提示 |
| `deny` | 阻止执行 |

## SDK 使用

```python
import asyncio
from mocode import MocodeClient, EventType

async def main():
    # 使用内存配置创建客户端
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {
            "openai": {
                "api_key": "sk-...",
                "base_url": "https://api.openai.com/v1",
                "models": ["gpt-4o"]
            }
        }
    })

    # 订阅事件
    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(f"[响应] {e.data}"))
    client.on_event(EventType.INTERRUPTED, lambda e: print("已取消"))

    # 对话
    response = await client.chat("你好！")
    print(response)

    # 中断正在进行的操作（从其他任务/线程）
    client.interrupt()

    # 清空历史
    client.clear_history()

asyncio.run(main())
```

## 技能系统

技能存放在 `~/.mocode/skills/` 目录下，每个技能是一个 `SKILL.md` 文件：

```markdown
---
name: my-skill
description: 技能描述
---

# 指令

详细的 LLM 指令内容...
```

## 内置命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示可用命令 |
| `/model` | 切换模型 |
| `/provider` | 切换供应商 |
| `/clear` | 清空对话历史 |
| `/skills` | 列出可用技能 |
| `/exit` | 退出程序 |

## 项目结构

```
mocode/
├── sdk.py              # SDK 入口 (MocodeClient)
├── main.py             # 主入口 (CLI 或 gateway 模式)
├── core/               # 核心业务逻辑 (与 UI 解耦)
│   ├── agent.py        # AsyncAgent - LLM 对话循环
│   ├── config.py       # 多供应商配置
│   ├── events.py       # EventBus - 事件驱动通信
│   ├── interrupt.py    # InterruptToken - 取消操作
│   └── permission.py   # 权限管理
├── gateway/            # 多渠道机器人支持
│   ├── manager.py      # GatewayManager
│   └── telegram.py     # Telegram 渠道
├── providers/          # LLM 供应商
│   └── openai.py       # OpenAI 兼容供应商
├── tools/              # 工具实现
│   ├── file_tools.py   # read, write, edit
│   ├── search_tools.py # glob, grep
│   └── shell_tools.py  # bash
├── skills/             # 技能系统
│   └── manager.py      # SkillManager
└── cli/                # 终端界面
    ├── app.py          # 主应用
    ├── commands/       # 斜杠命令
    └── ui/             # 布局、颜色、组件
```

## 开发

```bash
# 安装依赖
uv sync

# 运行 CLI
uv run mocode
```

## 系统要求

- Python >= 3.10
- OpenAI 兼容的 API 访问权限

## 许可证

MIT
