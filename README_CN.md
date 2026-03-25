# mocode

基于 LLM 的 CLI 编程助手，支持工具调用。

[English](README.md)

## 功能特性

- 交互式 CLI，实时流式输出
- 文件操作、搜索、Shell 执行
- 多供应商支持（OpenAI、Claude 等）
- SDK 模式，可嵌入应用
- 插件系统（钩子、工具替换）
- 权限控制与模式系统
- Session 管理
- RTK token 优化

## 前置条件

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) 包管理器

## 安装

### 直接安装（推荐）

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

### 开发安装

```bash
git clone https://github.com/Shingwha/mocode.git
cd mocode
uv tool install -e .
```

## 快速开始

```bash
mocode           # 启动 CLI
```

## 配置

配置文件：`~/.mocode/config.json`

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
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow"
  }
}
```

## 常用命令

| 命令 | 别名 | 说明 |
|------|------|------|
| `/help` | `/h`, `/?` | 显示帮助 |
| `/provider` | `/p` | 切换供应商 |
| `/mode` | `/m` | 切换模式 |
| `/session` | `/s` | 管理会话 |
| `/clear` | `/c` | 清空历史 |
| `/plugin` | | 管理插件 |
| `/rtk` | | Token 优化 |
| `/exit` | `/q` | 退出 |

### Session 管理

- `/session` - 交互式选择会话
- `/session list` - 列出所有会话
- `/session restore <id>` - 恢复指定会话

## RTK (Token 优化)

RTK 通过智能过滤减少 token 消耗。

```bash
/rtk status   # 查看状态
/rtk on       # 启用
/rtk off      # 禁用
```

## SDK 使用

```python
from mocode import MocodeClient
import asyncio

async def main():
    config = {
        "current": {"provider": "openai", "model": "gpt-4o"},
        "providers": {
            "openai": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "sk-..."
            }
        },
        "permission": {"*": "ask"}
    }

    client = MocodeClient(config=config, persistence=False)

    response = await client.chat("你好！")
    print(response)

    # 切换模型
    client.set_model("gpt-4o-mini")

    # Session 管理
    client.save_session()
    sessions = client.list_sessions()

asyncio.run(main())
```

## 目录结构

```
mocode/
├── core/          # 核心逻辑
├── plugins/       # 插件系统
├── providers/     # LLM 供应商
├── tools/         # 内置工具
├── skills/        # 技能系统
├── cli/           # 终端界面
└── sdk.py         # SDK 入口
```

## 系统要求

- Python >= 3.10
- OpenAI 兼容 API

## 许可证

MIT
