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

## SDK 使用

```python
from mocode import MocodeClient, EventType

async def main():
    client = MocodeClient(config={
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {"openai": {"api_key": "sk-...", "base_url": "https://api.openai.com/v1"}}
    })

    client.on_event(EventType.TEXT_COMPLETE, lambda e: print(e.data))
    await client.chat("你好！")

asyncio.run(main())
```

## 命令

| 命令 | 别名 | 说明 |
|------|------|------|
| `/help` | `/h`, `/?` | 显示命令 |
| `/model` | `/m` | 切换模型 |
| `/provider` | `/p` | 切换供应商 |
| `/clear` | `/c` | 清空历史 |
| `/skills` | | 列出技能 |
| `/rtk` | | 管理 RTK |
| `/exit` | `/q`, `quit` | 退出 |

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
├── sdk.py              # MocodeClient
├── main.py             # 入口
├── core/               # 核心逻辑
│   ├── agent.py        # AsyncAgent
│   ├── config.py       # 多供应商配置
│   ├── events.py       # EventBus
│   └── permission.py   # 权限管理
├── gateway/            # 多渠道机器人
├── providers/          # LLM 供应商
├── tools/              # 工具实现
├── skills/             # 技能系统
└── cli/                # 终端界面
```

## 系统要求

- Python >= 3.10
- OpenAI 兼容 API

## 许可证

MIT
