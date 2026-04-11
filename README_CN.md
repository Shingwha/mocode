# mocode

基于 LLM 的 CLI 编程助手，支持工具调用。

[English](README.md) | [文档索引](docs/)

## 前置条件

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) 包管理器

## 安装

```bash
uv tool install git+https://github.com/Shingwha/mocode.git
```

### 开发者安装

```bash
git clone https://github.com/Shingwha/mocode.git
cd mocode
uv tool install -e .
```

更新：

```bash
git pull
uv tool install -e .
```

## 快速开始

```bash
mocode
```

**首次使用？** 请先创建 `~/.mocode/config.json` 配置文件（详见下方【配置说明】）。

## 配置说明

创建配置文件 `~/.mocode/config.json`：

```json
{
  "current": {
    "provider": "zhipu",
    "model": "glm-5"
  },
  "providers": {
    "zhipu": {
      "base_url": "https://open.bigmodel.cn/api/coding/paas/v4/",
      "api_key": "your-api-key",
      "models": ["glm-5.1", "glm-5"]
    },
    "step": {
      "base_url": "https://api.stepfun.com/step_plan/v1",
      "api_key": "your-api-key",
      "models": ["step-3.5-flash", "step-3.5-flash-2603"]
    }
  },
  "permission": {
    "*": "ask",
    "read": "allow",
    "bash": {
      "*": "ask",
      "ls *": "allow",
      "cat *": "allow",
      "git *": "allow",
      "rm *": "deny"
    }
  },
  "tool_result_limit": 25000
}
```

### 权限规则

| 规则 | 说明 |
|------|------|
| `*` | 所有工具的默认规则 |
| `"allow"` | 始终允许 |
| `"ask"` | 执行前确认 |
| `"deny"` | 始终禁止 |
| 嵌套对象 | 按命令细分规则（如 bash） |

### 模式系统

```bash
/mode          # 显示当前模式
/mode yolo     # 自动批准安全工具
/mode normal   # 恢复默认模式
```

### 常用命令

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

## Gateway

连接到微信 ClawBot：

```bash
mocode gateway
```

首次运行时，使用浏览器打开终端中提示的链接并扫码，即可连接到微信。

详见 [Gateway 文档](docs/gateway.md)。

## 文档

- [CLI 命令](docs/cli.md)
- [插件系统](docs/plugins.md)
- [Gateway](docs/gateway.md)

## 许可证

MIT
