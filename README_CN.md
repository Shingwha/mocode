# mocode

基于 LLM 的 CLI 编程助手，支持工具调用。

[English](README.md) | [文档索引](docs/)

## 功能特性

- 交互式 CLI，实时流式输出
- 文件操作、搜索、Shell 执行
- 多供应商支持（OpenAI、Claude 等）
- SDK 模式，可嵌入应用
- 插件系统（钩子、工具替换）
- 权限控制与模式系统
- Session 管理
- 微信集成（通过 Gateway）

## 前置条件

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) 包管理器

## 安装

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

## 快速开始

```bash
# 启动 CLI
mocode

# 启动微信网关
mocode gateway --type weixin
```

**首次使用？** 请先创建 `~/.mocode/config.json` 配置文件（详见下方【配置说明】）。

## 配置说明

创建配置文件 `~/.mocode/config.json`：

### 基础配置（单个供应商）

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-..."
    }
  },
  "permission": {
    "*": "ask"
  }
}
```

### 完整配置（多供应商）

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
      "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
    },
    "anthropic": {
      "name": "Anthropic",
      "base_url": "https://api.anthropic.com/v1",
      "api_key": "sk-ant-...",
      "models": ["claude-opus-4", "claude-sonnet-4", "claude-3.5-haiku"]
    },
    "deepseek": {
      "name": "DeepSeek",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "models": ["deepseek-chat", "deepseek-coder"]
    },
    "local": {
      "name": "本地模型 (Ollama)",
      "base_url": "http://localhost:11434/v1",
      "api_key": "dummy",
      "models": ["llama3.2", "codellama", "mistral"]
    }
  },
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "pwd": "allow",
      "git *": "allow"
    }
  },
  "tool_result_limit": 25000
}
```

### 常用模型名称

| 供应商 | 可用模型 |
|--------|----------|
| OpenAI | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`, `gpt-3.5-turbo` |
| Anthropic | `claude-opus-4`, `claude-sonnet-4`, `claude-3.5-haiku` |
| DeepSeek | `deepseek-chat`, `deepseek-coder` |
| 本地 (Ollama) | `llama3.2`, `codellama`, `mistral`, `qwen2.5-coder` |

> **注意**：`model` 字段的值必须与对应供应商配置中的 `models` 数组之一匹配。

### 配置字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `current.provider` | 字符串 | 当前使用的供应商标识 |
| `current.model` | 字符串 | 使用的模型名称 |
| `providers` | 对象 | 供应商配置，键名为供应商标识 |
| `providers[*].base_url` | 字符串 | API 地址（兼容 OpenAI 格式） |
| `providers[*].api_key` | 字符串 | 认证密钥 |
| `providers[*].models` | 数组 | 该供应商支持的模型列表 |
| `permission` | 对象 | 工具执行权限规则 |
| `tool_result_limit` | 数字 | 工具输出大小限制（0=无限制，默认 25000） |
| `gateway` | 对象 | Gateway 相关配置（详见 [Gateway 文档](docs/gateway.md)） |

### 环境变量

你可以在配置中使用环境变量，避免硬编码密钥：

```json
{
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "${OPENAI_API_KEY}"
    }
  }
}
```

如果配置中没有指定 `api_key`，mocode 会自动读取环境变量 `OPENAI_API_KEY` 作为备选。

### 在 CLI 中切换供应商

```bash
/provider          # 交互式选择
/provider openai   # 直接指定
/p                 # 快捷别名
```

### 权限规则

控制工具执行是否需要确认：

```json
{
  "permission": {
    "*": "ask",              # 默认：执行前询问
    "read": "allow",         # 读取类工具：直接允许
    "write": "deny",         # 写入类工具：直接禁止
    "bash": {               # 针对 bash 命令的细粒度规则
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "rm *": "deny"
    }
  }
}
```

权限规则匹配优先级：**具体工具规则 > 通配符 `*`**。

详细规则说明见 [权限系统文档](docs/permission.md)。

### 模式系统

在安全与便捷之间快速切换：

```bash
/mode          # 显示当前模式
/mode list     # 列出所有可用模式
/mode yolo     # 自动批准安全工具
/mode normal   # 恢复默认模式（尊重权限规则）
```

- **normal**（默认）：严格执行权限规则，`ask` 类工具需要确认
- **yolo**：自动批准安全工具，仅阻止危险 bash 命令（rm、mv、dd、format 等）

你还可以在配置文件中自定义模式：

```json
{
  "modes": {
    "safe": {
      "auto_approve": false,
      "dangerous_patterns": ["rm ", "rmdir ", "dd ", "mv "]
    },
    "fast": {
      "auto_approve": true,
      "dangerous_patterns": ["rm ", "format ", "mkfs "]
    }
  }
}
```

详细说明见 [权限系统文档](docs/permission.md)。

## Gateway

将 mocode 作为机器人部署到即时通讯平台：

```bash
# 启动微信网关
mocode gateway --type weixin
```

详见 [Gateway 文档](docs/gateway.md)。

## 常用命令

| 命令 | 别名 | 说明 |
|------|------|------|
| `/help` | `/`, `/h`, `/?` | 显示帮助 |
| `/provider` | `/p` | 切换供应商 |
| `/mode` | | 切换模式 |
| `/session` | `/s` | 管理会话 |
| `/clear` | `/c` | 清空历史 |
| `/skills` | | 管理技能 |
| `/plugin` | | 管理插件 |
| `/rtk` | | Token 优化 |
| `/exit` | `/q`, `/quit` | 退出 |

## SDK 使用

```python
from mocode import MocodeCore
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

    client = MocodeCore(config=config, persistence=False)
    response = await client.chat("你好！")
    print(response)

asyncio.run(main())
```

## 文档

- [安装指南](docs/installation.md)
- [CLI 命令](docs/cli.md)
- [供应商配置](docs/provider.md)
- [权限系统](docs/permission.md)
- [插件系统](docs/plugins.md)
- [技能系统](docs/skills.md)
- [Gateway](docs/gateway.md)

## 许可证

MIT
