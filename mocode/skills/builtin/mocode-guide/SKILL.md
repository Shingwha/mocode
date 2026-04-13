---
name: mocode-guide
description: mocode 完整指南 - 配置、权限、技能系统
dependencies: []
---

# mocode 指南

mocode 官方指南，涵盖配置、权限等核心概念。

## 快速开始

### 最小配置

```json
{
  "current": {"provider": "openai", "model": "gpt-4o"},
  "providers": {
    "openai": {"base_url": "https://api.openai.com/v1", "api_key": "sk-..."}
  },
  "permission": {"*": "ask"}
}
```

保存为 `~/.mocode/config.json`，然后运行 `mocode`。

### 常用命令

| 命令 | 说明 |
|------|------|
| `/provider` | 切换供应商/模型 |
| `/mode` | 切换模式（normal/yolo） |
| `/skills` | 查看技能 |
| `/clear` | 清空历史 |

## 配置系统

### 完整配置示例

```json
{
  "current": {"provider": "openai", "model": "gpt-4o"},
  "providers": {
    "openai": {
      "base_url": "https://api.openai.com/v1",
      "api_key": "${OPENAI_API_KEY}",
      "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"]
    },
    "deepseek": {
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "${DEEPSEEK_API_KEY}",
      "models": ["deepseek-chat"]
    },
    "local": {
      "base_url": "http://localhost:11434/v1",
      "api_key": "dummy",
      "models": ["llama3.2", "codellama"]
    }
  },
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": {"*": "ask", "ls *": "allow", "cat *": "allow", "git *": "allow", "rm *": "deny"}
  },
  "tool_result_limit": 25000
}
```

### 配置字段

| 字段 | 说明 |
|------|------|
| `current.provider` | 当前供应商 key |
| `current.model` | 当前模型名称 |
| `providers[*].base_url` | API 地址（OpenAI 格式） |
| `providers[*].api_key` | API 密钥（支持 `${ENV_VAR}`） |
| `providers[*].models` | 可用模型列表 |
| `permission` | 工具权限规则（见下文） |
| `tool_result_limit` | 工具输出大小限制（默认 25000） |
| `modes` | 自定义模式（运行时，不持久化） |

### 环境变量

```bash
export OPENAI_API_KEY="sk-..."
export DEEPSEEK_API_KEY="sk-..."
```

配置中可写 `"api_key": "${OPENAI_API_KEY}"`，或完全省略该字段（自动读取 `OPENAI_API_KEY`）。

### 供应商与模型

| 供应商 key | base_url | 常用模型 |
|-----------|----------|---------|
| `openai` | `https://api.openai.com/v1` | `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo` |
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-chat`, `deepseek-coder` |
| `local` | `http://localhost:11434/v1` | `llama3.2`, `codellama`, `qwen2.5` |

任何 OpenAI 兼容的 API 都可添加（只需改 `base_url` 和 `models`）。

---

## 权限系统

### 权限动作

| 动作 | 含义 |
|------|------|
| `allow` | 直接执行 |
| `ask` | 执行前询问 |
| `deny` | 直接拒绝 |

### 权限规则格式

**扁平格式**（简单）：

```json
{"permission": {"*": "ask", "read": "allow", "bash": "deny"}}
```

**嵌套格式**（细粒度，推荐）：

```json
{
  "permission": {
    "*": "ask",
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "pwd": "allow",
      "git *": "allow",
      "rm *": "deny",
      "sudo *": "deny"
    },
    "read": {
      "*": "allow",
      "**/.env": "deny",
      "**/secrets/**": "deny"
    }
  }
}
```

### 匹配优先级

1. 精确匹配：`"pwd": "allow"`
2. 前缀匹配：`"ls *": "allow"` 匹配 `ls -la`、`ls /home`
3. 通配符：`"*": "ask"` 作为默认 fallback

### 文件路径匹配（glob）

```json
{
  "read": {
    "*.txt": "allow",           // 所有 .txt 文件
    "**/node_modules/**": "deny" // 递归排除依赖目录
  }
}
```

### 模式（Modes）

| 模式 | 行为 |
|------|------|
| `normal` | 严格执行权限规则（默认） |
| `yolo` | 自动批准安全工具，仅阻止危险命令（rm/mv/dd/format 等） |

切换：`/mode yolo`、`/mode normal`

自定义模式：

```json
{
  "modes": {
    "safe": {"auto_approve": false, "dangerous_patterns": ["rm ", "dd "]},
    "fast": {"auto_approve": true, "dangerous_patterns": ["rm ", "format "]}
  }
}
```

---

## 技能系统（Skills）

### Skill 结构

```
my-skill/
├── SKILL.md           # 必需：YAML frontmatter + 说明
├── script.py          # 可选：工具实现
└── requirements.txt   # 可选：依赖
```

### SKILL.md 格式

```markdown
---
name: python-expert
description: Python 编程专家技能
---

# Python 专家模式

你是一位经验丰富的 Python 开发者...

## 编码规范

- 使用 type hints
- 遵循 PEP 8
- 编写 docstrings
```

### script.py 示例

```python
from mocode.tools import tool

@tool("lint", "Lint Python code", {"code": "string"})
def lint_code(args: dict) -> str:
    import pylint
    return "Linting done"
```

### 安装位置

- 项目级：`<project>/.mocode/skills/`
- 用户级：`~/.mocode/skills/`

优先级：项目级 > 用户级 > 内置

管理命令：
```bash
/skills              # 列出所有技能
/skills reload       # 重新加载
/skills disable <name>
```

---

## Gateway（网关）

将 mocode 部署到即时通讯平台。

### 微信网关

```bash
mocode gateway --type weixin
```

扫描二维码登录即可使用。

### 架构要点

- **每用户独立实例**：会话完全隔离
- **强制 yolo 模式**：自动批准安全工具
- **LRU 缓存**：超过 `max_users` 时自动淘汰最久未使用的会话
- **消息串行化**：同一用户的消息排队处理

### 配置

```json
{
  "gateway": {
    "max_users": 100,
    "idle_timeout": 3600
  }
}
```

| 字段 | 默认 | 说明 |
|------|------|------|
| `max_users` | 100 | 最大并发用户数 |
| `idle_timeout` | 3600 | 闲置超时（秒），超时后保存会话 |

### 长消息分割

超过 3500 字符自动按换行符拆分，分批发送。

### 添加新通道

继承 `BaseChannel`，实现 `start()`、`stop()`、`send()` 三个方法，然后在 `gateway/registry.py` 注册。

参考：`gateway/channels/weixin.py`

---

## 常见问题

**Q: 配置文件放哪里？**  
A: `~/.mocode/config.json`（Windows：`C:\Users\<用户名>\.mocode\config.json`）

**Q: API Key 如何获取？**  
A: OpenAI: https://platform.openai.com/api-keys  
   DeepSeek: https://platform.deepseek.com/

**Q: 如何切换供应商？**  
A: `/provider` 或 `/p` 交互选择；`/provider openai` 直接切换。

**Q: 权限询问太多？**  
A: 调整配置：`{"permission": {"*": "ask", "read": "allow", "bash": "allow"}}` 或使用 `/mode yolo`。

**Q: 技能不加载？**
A: 运行 `/skills reload` 重新扫描；确认目录结构和文件命名正确。

**Q: 打包 exe 后内置 skill 还能用吗？**  
A: 可以。内置 skill 被打包到包内，通过 `pkgutil`/`importlib.resources` 访问，路径自动适配。

**Q: 如何查看详细日志？**  
A: `DEBUG=mocode* mocode` 或查看 `~/.mocode/logs/`。

**Q: Token 消耗太快？**
A: 减小 `tool_result_limit`，定期 `/clear` 清理历史。

---

## 快速索引

| 主题 | 核心文件/命令 |
|------|-------------|
| 配置文件 | `~/.mocode/config.json` |
| 供应商设置 | `providers` 段 |
| 权限规则 | `permission` 段 |
| 模式切换 | `/mode` |
| 技能管理 | `/skills` |
| 会话管理 | `/session` |
| 日志目录 | `~/.mocode/logs/` |

---

## 项目资源

- **代码仓库**: https://github.com/Shingwha/mocode
- **问题反馈**: https://github.com/Shingwha/mocode/issues
- **文档索引**: [README](README.md) | [docs/](docs/)
