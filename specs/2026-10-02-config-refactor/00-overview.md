# Spec 00 · config 机制重构 — 总纲

> 2026-10-02 启动。三波串行：W1 core → W2 host → W3 cli+docs。
> Git / worktree 协议见 spec-team SKILL.md「Git / worktree 协议」一节，本组不重复。

## 目标

重构 MoCode 的 provider/config 机制：provider 配置对标 pi 的 models.json
设计（models 为数组、`id`/`name`、能力声明），删除 `extra_body`，新增开放的
自定义 efforts（推理档位）系列，并提供运行时切换。不考虑向后兼容，追求
内核与配置的极致简洁、无冗余。

## 目标态 config.json（权威示例）

```jsonc
{
  "provider": "commandcode",              // 顶层选择：新会话的默认 provider/model
  "model": "deepseek/deepseek-v4.1-flash", // （原 active_provider / active_model 短化）

  "agent": {                              // = core AgentConfig，字段不变
    "tool_timeout": 240,
    "max_iterations": 0,
    "max_tool_calls": 0,
    "max_turn_seconds": 0,
    "tool_result_limit": 50000
  },

  "providers": {
    "commandcode": {
      "type": "openai",                   // 可省，默认 "openai"；注册见 runtime.register_provider_type
      "name": "Command Code",             // 可省显示名
      "base_url": "https://api.commandcode.ai/provider/v1",
      "api_key": "sk-...",                // 可省 → 自动 $COMMANDCODE_API_KEY
      "models": [                         // 数组，保序；id 为唯一键
        {
          "id": "deepseek/deepseek-v4.1-flash",
          "name": "DeepSeek V4.1 Flash",  // 可省显示名
          "context_window": 1000000,      // 可省 = 未知
          "max_tokens": 65536,            // 可省 = 请求不带上限（原 max_output 改名）
          "efforts": ["low", "high", "max"], // 可省 = 默认 ("low","medium","high")
          "effort": "high",               // 可省 = 不发该参数，服务端自定
          "retry": { "max_attempts": 3 }  // 可省；per-model RetryPolicy 覆盖
        },
        {
          "id": "xiaomi/mimo-v2.6-pro",
          "efforts": ["high", "xhigh", "max"],  // 另一套自定义档位
          "effort": "xhigh"
        }
      ]
    }
  },

  "plugins": { "shell": { "enabled": false } }
}
```

## efforts 系列设计（本重构的核心语义）

- **开放有序系列**：`Effort = str`。core 提供缺省系列
  `EFFORTS = ("low", "medium", "high")`。config 可在模型条目上用
  `efforts` 声明任意自定义档位表（如 `["high","xhigh","max"]`）。
- **档位名 verbatim 上电线**：内置 OpenAI provider 在 `effort` 非 None 时
  发送 OpenAI 标准字段 `reasoning_effort: <档位名>`；自定义档位名原样透传。
  MoCode 代码零 vendor 适配 —— 用别的字段表达思考的端点（如 DeepSeek 官方
  `thinking`）写一个自定义 provider type 注册即可（既有机制）。
- **absent 语义**：`efforts` 缺席 → 缺省三档；`effort` 缺席 → 请求不带
  reasoning_effort，完全由服务端决定。MoCode 不发明默认值。
- **运行时切换**：`Conversation.set_effort(level)` 内存切换，不写
  config.json（与 `set_model` 同语义）；CLI `/effort` 提供选择器。
- **无 reasoning 能力位**：大多数模型已是思考模型，`reasoning: bool`
  字段删除；模型是否会思考由「是否配置了 effort/efforts」表达。

## 波次表

| 波 | 分支 | 内容 | 工单 |
|---|---|---|---|
| W1 | `config-refactor-w1-core` | core/provider 协议 + ModelSpec + OpenAIProvider 删 extra_body + MockProvider + examples + 直接受损测试 | 01-w1-core.md |
| W2 | `config-refactor-w2-host` | host/config.py 重写 + runtime + conversation.set_effort + host 侧测试（前置：W1 已合并） | 02-w2-host.md |
| W3 | `config-refactor-w3-cli-docs` | cli /model 适配 + /effort 新命令 + 文档/AGENTS.md/README（前置：W2 已合并） | 03-w3-cli-docs.md |

写入范围文件级不相交，逐文件清单见各工单「写入范围」与「禁触清单」。

## 全局不变量（不可破坏）

1. AGENTS.md 十条硬不变量原样保持 —— 特别是：唯一构建/执行路径、事件流
   观察、分层依赖 `core ← host ← cli`、`import mocode` 毫秒级惰性。
2. 配置键 snake_case 全仓统一，键名 = Python 字段名，无映射表。
3. 旧键（`active_provider`/`active_model`/`extra_body`/`max_output`/
   models 字典）零 shim 直接删除。
4. `env_var_for` 自动环境变量回退、`foreign` 未知顶层键保留机制不变。
5. 代码注释与文档语言跟随仓内现状：英文；commit message 英文短句。
6. **门禁口径**：跨层改名/换签名会让消费方在另一波范围内的中间 commit
   结构性变红 —— 允许；但**每个波次合并进 master 前，其分支最终状态必须
   `uv run pytest` 全绿**（独立确认退出码，不管道吞）。波内允许 lead 批准
   一行级「桥接」variance（机械适配另一波将整体重写的调用点）来达成这一点。

## 回归红线

- `uv run pytest` 全绿（W1/W2/W3 各自的 worktree 内，合并前后各一次）。
- `tests/test_runtime.py::TestThePackage::test_importing_mocode_stays_lazy`
  必须保持通过（含在全量中）。

## 取舍登记（已拍板，不再反复）

- `max_output` → `max_tokens`（与电线字段及 pi 命名对齐）。
- pi 的 `input`/`cost`/`api` 等字段不引入：core 无消费方，违反无冗余。
- OpenAIProvider 的 `stream_options` 可覆盖路径随 extra_body 一并删除，
  硬编码 `{"include_usage": True}`；拒参端点走自定义 provider type。
- `retry` 块保留（限流后端真实需要），字段 = RetryPolicy 字段名。
