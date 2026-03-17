# 权限系统

mocode CLI 提供可配置的权限系统，在工具执行前根据配置规则决定是否允许、询问或拒绝操作。

## 配置格式

权限配置位于 `~/.mocode/config.json` 的 `permission` 字段：

```json
{
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
    "read": "allow",
    "glob": "allow",
    "grep": "allow"
  }
}
```

## 权限动作

| 动作 | 说明 |
|------|------|
| `allow` | 无需审批直接执行 |
| `ask` | 弹出选择菜单让用户决定 |
| `deny` | 阻止操作，返回拒绝消息 |

## 支持的工具

| 工具 | 说明 |
|------|------|
| `bash` | 执行 shell 命令 |
| `edit` | 编辑文件 |
| `write` | 写入文件 |
| `read` | 读取文件 |
| `glob` | 搜索文件 |
| `grep` | 搜索内容 |

## 匹配优先级

规则按优先级匹配：**具体工具规则 > 通配符 `*`**

```json
{
  "permission": {
    "*": "ask",      // 默认：询问用户
    "bash": "allow", // bash 工具：直接允许
    "edit": "deny"   // edit 工具：直接拒绝
  }
}
```

## 用户交互

当权限动作为 `ask` 时，会显示选择菜单：

```
? Permission required for bash
  ls -la

  > Allow (execute the tool)
    Deny (cancel the operation)
    Type something (provide custom response)
```

### 选项说明

| 选项 | 说明 |
|------|------|
| **Allow** | 允许执行该工具 |
| **Deny** | 拒绝执行，返回拒绝消息 |
| **Type something** | 输入自定义内容作为工具结果 |

## 默认行为

如果未配置权限规则，默认行为为 `ask`（询问用户）。

## 完整配置示例

```json
{
  "$schema": "https://mocode.ai/config.json",
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "permission": {
    "*": "ask",
    "bash": "ask",
    "edit": "ask",
    "write": "ask",
    "read": "allow",
    "glob": "allow",
    "grep": "allow"
  },
  "max_tokens": 8192
}
```

## 安全建议

1. **谨慎配置 `allow`**：建议对 `bash`、`edit`、`write` 使用 `ask`
2. **读取操作可开放**：`read`、`glob`、`grep` 可以设为 `allow`
3. **使用 `deny` 保护敏感操作**：如需禁止某些工具，可设为 `deny`
