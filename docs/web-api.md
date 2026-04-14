# MoCode Web API

MoCode Web Backend 提供基于 FastAPI 的 REST API，用于与 MocodeCore 交互。

启动方式：

```bash
mocode web [--host HOST] [--port PORT]
# 默认: host=127.0.0.1, port=8000
```

FastAPI 自动文档：启动后访问 `http://host:port/docs`（Swagger UI）或 `http://host:port/redoc`。

## 通用约定

### 响应格式

成功响应返回 JSON 对象，具体结构见各端点。

错误响应统一格式：

```json
{
  "error": "error description"
}
```

### 状态码

| 状态码 | 含义 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误（如 provider/model 不存在） |
| 404 | 资源不存在（如 session 未找到） |
| 409 | 冲突（如 agent 正忙时发消息） |
| 500 | 内部错误 |

---

## Chat

### POST /api/chat

发送消息，同步等待完整回复。

**请求体：**

```json
{
  "message": "hello",
  "media": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| message | string | 是 | 用户消息 |
| media | string[] | 否 | 媒体文件路径列表 |

**响应：**

```json
{
  "response": "Hello! How can I help you?"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| response | string | agent 完整回复文本 |

> 如果 agent 正在处理另一条消息，返回 **409**。

### POST /api/interrupt

中断当前操作（如正在进行的 chat）。

**请求体：** 无

**响应：**

```json
{
  "ok": true
}
```

### GET /api/status

获取 agent 当前状态。

**响应：**

```json
{
  "busy": false,
  "model": "glm-5",
  "provider": "zhipu"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| busy | boolean | agent 是否正在处理消息 |
| model | string | 当前使用的模型 |
| provider | string | 当前使用的供应商 key |

---

## Sessions

### GET /api/sessions

列出所有会话（不含消息内容）。

**响应：**

```json
{
  "sessions": [
    {
      "id": "session_20260414_143022",
      "created_at": "2026-04-14T14:30:22.123456",
      "updated_at": "2026-04-14T14:35:00.654321",
      "workdir": "/home/user/project",
      "model": "glm-5",
      "provider": "zhipu",
      "message_count": 8
    }
  ]
}
```

### POST /api/sessions

保存当前对话为一个 session。如果已有 current session 则更新，否则创建新的。

**请求体：** 无

**响应：**

```json
{
  "session": {
    "id": "session_20260414_143022",
    "created_at": "...",
    "updated_at": "...",
    "workdir": "...",
    "model": "glm-5",
    "provider": "zhipu",
    "message_count": 8
  }
}
```

### GET /api/sessions/{session_id}

加载指定 session 并设为当前会话（恢复对话历史到 agent）。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| session_id | string | session ID，格式 `session_YYYYMMDD_HHMMSS` |

**响应：**

```json
{
  "id": "session_20260414_143022",
  "created_at": "...",
  "updated_at": "...",
  "workdir": "...",
  "model": "glm-5",
  "provider": "zhipu",
  "message_count": 8,
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "Hi!"}
  ]
}
```

> session 不存在时返回 **404**。

### DELETE /api/sessions/{session_id}

删除指定 session。

**响应：**

```json
{
  "ok": true
}
```

> session 不存在时返回 **404**。

---

## History

### GET /api/history

获取当前对话消息历史。

**响应：**

```json
{
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": "Hi! How can I help you?"}
  ]
}
```

消息格式遵循 OpenAI Chat Completion API 标准：`role` 为 `system` / `user` / `assistant`，`content` 为文本。tool call 相关消息还会包含 `tool_calls` 和 `tool_call_id` 等字段。

### DELETE /api/history

清空当前对话历史。如果当前有未保存的对话，会自动保存后再清空。

**响应：**

```json
{
  "ok": true
}
```

---

## Config

### GET /api/config

读取完整配置。API key 会被遮蔽，仅返回是否已设置。

**响应：**

```json
{
  "current_provider": "zhipu",
  "current_model": "glm-5",
  "providers": {
    "zhipu": {
      "name": "Zhipu",
      "base_url": "https://open.bigmodel.cn/api/coding/paas/v4/",
      "api_key_set": true,
      "models": ["glm-5.1", "glm-5"]
    },
    "step": {
      "name": "Step",
      "base_url": "https://api.stepfun.com/step_plan/v1",
      "api_key_set": false,
      "models": ["step-3.5-flash"]
    }
  },
  "max_tokens": 8192,
  "tool_result_limit": 25000,
  "mode": "yolo"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| current_provider | string | 当前供应商 key |
| current_model | string | 当前模型 |
| providers | object | 所有供应商配置（key -> ProviderInfo） |
| providers.*.name | string | 供应商显示名称 |
| providers.*.base_url | string | API 端点 |
| providers.*.api_key_set | boolean | API key 是否已配置（不暴露实际值） |
| providers.*.models | string[] | 该供应商支持的模型列表 |
| max_tokens | integer | 最大生成 token 数 |
| tool_result_limit | integer | 工具结果字符上限 |
| mode | string | 当前模式：`normal` 或 `yolo` |

### PUT /api/config/model

切换当前模型。

**请求体：**

```json
{
  "model": "glm-5.1",
  "provider": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | 是 | 模型名称 |
| provider | string | 否 | 同时切换到指定供应商 |

**响应：** 返回更新后的完整配置，同 `GET /api/config`。

### PUT /api/config/provider

切换当前供应商。默认选择该供应商的第一个模型。

**请求体：**

```json
{
  "provider": "step",
  "model": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| provider | string | 是 | 供应商 key |
| model | string | 否 | 指定模型（不填则用该供应商第一个模型） |

**响应：** 返回更新后的完整配置。

> 供应商不存在时返回 **400**。

### PUT /api/config/mode

切换运行模式。

**请求体：**

```json
{
  "mode": "normal"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| mode | string | 是 | `normal` 或 `yolo` |

**响应：** 返回更新后的完整配置。

> 模式不存在时返回 **400**。

### POST /api/config/providers

添加新供应商。

**请求体：**

```json
{
  "key": "openai",
  "name": "OpenAI",
  "base_url": "https://api.openai.com/v1",
  "api_key": "sk-...",
  "models": ["gpt-4o", "gpt-4o-mini"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| key | string | 是 | 供应商唯一标识 |
| name | string | 是 | 显示名称 |
| base_url | string | 是 | API 端点 URL |
| api_key | string | 否 | API key，默认空字符串 |
| models | string[] | 否 | 支持的模型列表 |

**响应：** 返回更新后的完整配置。

> key 已存在时返回 **400**。

### PUT /api/config/providers/{key}

更新供应商配置。只传需要修改的字段。

**请求体：**

```json
{
  "name": "OpenAI Inc",
  "base_url": null,
  "api_key": "sk-new-key"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| name | string | 否 | 新的显示名称 |
| base_url | string | 否 | 新的 API 端点 |
| api_key | string | 否 | 新的 API key |

**响应：** 返回更新后的完整配置。

> 供应商不存在时返回 **400**。

### DELETE /api/config/providers/{key}

删除供应商。不能删除最后一个供应商。如果删除的是当前供应商，会自动切换到另一个。

**响应：** 返回更新后的完整配置。

> 供应商不存在或为最后一个时返回 **400**。

### POST /api/config/models

向供应商添加模型。

**请求体：**

```json
{
  "model": "gpt-4o",
  "provider": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| model | string | 是 | 模型名称 |
| provider | string | 否 | 供应商 key，不填则用当前供应商 |

**响应：** 返回更新后的完整配置。

> 供应商不存在时返回 **400**。

### DELETE /api/config/models/{model}

从供应商移除模型。如果移除的是当前使用的模型，会自动切换到该供应商的另一个模型。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| model | string | 要移除的模型名称（URL 编码） |

**查询参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| provider | string | 否 | 供应商 key，不填则用当前供应商 |

**响应：** 返回更新后的完整配置。

> 供应商或模型不存在时返回 **400**。

---

## Compact

### POST /api/compact

手动触发上下文压缩。会将当前对话历史通过 LLM 总结为精简版本，减少 token 占用。

**请求体：** 无

**响应：**

```json
{
  "action": "compact_complete",
  "old_count": 24,
  "new_count": 6
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| action | string | 固定值 `compact_complete` |
| old_count | integer | 压缩前消息条数 |
| new_count | integer | 压缩后消息条数 |

> agent 正忙时返回 **409**。

---

## 前端开发参考

### 典型工作流

```
1. GET  /api/status         → 检查连接和当前模型
2. GET  /api/config         → 获取供应商/模型列表，渲染设置页
3. POST /api/chat           → 发消息，等回复
4. POST /api/interrupt      → 用户想停止生成
5. GET  /api/sessions       → 渲染历史会话列表
6. GET  /api/sessions/{id}  → 恢复某个历史会话
7. POST /api/sessions       → 手动保存当前会话
8. DELETE /api/history      → 开始新对话
```

### Chat 交互建议

- chat 端点是同步阻塞的，前端应做好超时处理（建议 120s+）
- 发送前先检查 `GET /api/status` 的 `busy` 字段，避免 409
- 或直接发 chat 请求，遇到 409 提示用户先 interrupt
- interrupt 后 agent 会停止生成，已生成的部分文本不会丢失
- 当前版本不支持流式（SSE/WebSocket），后续版本会添加

### Config 管理建议

- `api_key_set` 为 false 时，前端应提示用户配置 API key
- 切换 provider/model 后，`ConfigResponse` 会返回最新的完整配置
- mode 切换影响的是工具执行权限：`yolo` 自动批准安全工具，`normal` 需要确认
