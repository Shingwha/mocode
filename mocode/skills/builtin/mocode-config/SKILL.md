---
name: mocode-config
description: mocode 配置指南 - 如何修改 provider、permission、plugins 等配置
---

# mocode 配置指南

## 相关链接

- **项目地址**: https://github.com/Shingwha/mocode
- **官方插件**: https://github.com/Shingwha/mocode-plugins

## 配置文件位置

配置文件位于 `~/.mocode/config.json`

## 配置结构

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
  },
  "max_tokens": 8192,
  "plugins": {
    "rtk": "enable"
  },
  "tool_result_limit": 25000
}
```

## 字段说明

### current

当前使用的 provider 和 model。

- `provider`: 供应商 key（对应 providers 中的键名）
- `model`: 模型名称

### providers

供应商配置字典，key 为供应商标识符。

每个供应商包含：
- `name`: 显示名称
- `base_url`: API 端点 URL
- `api_key`: API 密钥
- `models`: 支持的模型列表

### permission

工具权限配置，支持两种格式。

**值可以是**：
- `ask`: 每次执行前询问用户
- `allow`: 自动允许执行
- `deny`: 拒绝执行

**扁平格式**（简单场景）：

```json
{
  "permission": {
    "*": "ask",
    "bash": "allow",
    "write": "ask"
  }
}
```

**嵌套格式**（细粒度控制）：

```json
{
  "permission": {
    "*": "ask",
    "bash": {
      "*": "ask",
      "git *": "allow",
      "ls": "allow",
      "rm *": "deny"
    },
    "read": {
      "*": "allow",
      "**/.env": "deny"
    }
  }
}
```

**匹配模式**：

| 模式 | 说明 | 示例 |
|------|------|------|
| `"*"` | 通配符，匹配所有 | 匹配任何工具/命令 |
| `"git *"` | 前缀匹配 | 匹配 `git status`、`git commit` |
| `"rm *"` | 前缀匹配 | 匹配 `rm file.txt`、`rm -rf dir` |
| `"*.txt"` | glob 匹配 | 匹配 `test.txt`、`notes.txt` |
| `"**/node_modules/**"` | 递归匹配 | 匹配任意层级的 node_modules |

**工具参数匹配**：

- `bash` 工具：匹配 `command` 参数
- 文件工具 (`read`/`write`/`edit`/`append`): 匹配 `path` 参数
- 其他工具：组合所有参数值

### max_tokens

模型返回的最大 token 数。

### plugins

插件启用/禁用状态。

```json
{
  "plugins": {
    "rtk": "enable",
    "my-plugin": "disable"
  }
}
```

- `"enable"`: 启用插件
- `"disable"`: 禁用插件

### tool_result_limit

工具返回结果的最大字符数，0 表示无限制。

### modes（运行时，不持久化）

模式配置为运行时状态，**不会保存到配置文件**。通过 `/mode` 命令或 `config.set_mode()` 方法切换。

- `normal`（默认）：标准权限检查
- `yolo`：自动批准非危险操作

```python
# SDK 中切换模式
client.config.set_mode("yolo")
```

```bash
# CLI 中切换模式
/mode yolo
```

## 常用操作

### 添加新的 Provider

```json
{
  "providers": {
    "my-provider": {
      "name": "My Provider",
      "base_url": "https://api.example.com/v1",
      "api_key": "your-api-key",
      "models": ["model-1", "model-2"]
    }
  }
}
```

然后在 `current.provider` 中设置为 `"my-provider"`。

### 修改权限

允许所有 bash 命令：
```json
{
  "permission": {
    "*": "ask",
    "bash": "allow"
  }
}
```

只允许特定 bash 命令：
```json
{
  "permission": {
    "*": "ask",
    "bash": {
      "*": "ask",
      "git *": "allow",
      "ls": "allow",
      "cat": "allow"
    }
  }
}
```

禁止危险命令：
```json
{
  "permission": {
    "*": "ask",
    "bash": {
      "*": "ask",
      "rm *": "deny",
      "sudo *": "deny",
      "chmod *": "deny"
    }
  }
}
```

保护敏感文件：
```json
{
  "permission": {
    "*": "ask",
    "read": {
      "*": "allow",
      "**/.env": "deny",
      "**/secrets/**": "deny"
    },
    "write": {
      "*": "ask",
      "**/.env": "deny"
    }
  }
}
```

拒绝所有文件写入：
```json
{
  "permission": {
    "*": "ask",
    "write": "deny",
    "edit": "deny",
    "append": "deny"
  }
}
```

### 启用/禁用插件

```json
{
  "plugins": {
    "rtk": "enable",
    "my-plugin": "disable"
  }
}
```

## 命令行操作

mocode 提供内置命令管理配置：

```
/provider              # 显示当前配置
/provider openai       # 切换到 openai
/provider --model gpt-4o-mini  # 切换模型
```

## 环境变量

`OPENAI_API_KEY` 环境变量会自动覆盖配置文件中的 openai provider 的 api_key。

## 配置文件示例

最小配置：
```json
{
  "current": {"provider": "openai", "model": "gpt-4o"},
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxx"
    }
  }
}
```

完整配置：
```json
{
  "current": {"provider": "openai", "model": "gpt-4o"},
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-xxx",
      "models": ["gpt-4o", "gpt-4o-mini", "o1"]
    },
    "custom": {
      "name": "Custom API",
      "base_url": "https://api.custom.com/v1",
      "api_key": "xxx",
      "models": ["custom-model"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "allow",
    "write": "ask"
  },
  "max_tokens": 8192,
  "plugins": {
    "rtk": "enable"
  },
  "tool_result_limit": 25000
}
```
