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
| 404 | 资源不存在（如 session/permission 未找到） |
| 409 | 冲突（如 agent 正忙时发消息） |
| 500 | 内部错误 |

---

## Chat

### POST /api/chat

发送消息，通过 **SSE（Server-Sent Events）** 流式返回 agent 的处理过程和最终回复。

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

**响应：** `Content-Type: text/event-stream`

每个 SSE 事件格式为：

```
event: <event_type>
data: <json_payload>

```

#### 事件类型

| 事件 | 数据格式 | 说明 |
|------|----------|------|
| text_complete | `{"content": "Hello!"}` | LLM 返回的完整文本 |
| tool_start | `{"id": "tc_1", "name": "bash", "args": {"command": "ls"}}` | 工具开始执行 |
| tool_complete | `{"id": "tc_1", "name": "bash", "result": "file1.txt\nfile2.txt"}` | 工具执行完成 |
| permission_ask | `{"request_id": "perm_abc", "tool": "bash", "args": {...}, "description": "..."}` | 请求用户审批（normal 模式） |
| permission_resolved | `{"request_id": "perm_abc", "approved": true}` | 权限审批结果 |
| interrupted | `{}` 或 `{"reason": "denied", "tool": "bash"}` | agent 被中断 |
| done | `{"response": "完整回复文本"}` | 整个 turn 结束 |
| error | `{"message": "error description"}` | 发生错误 |

> `: keepalive` 注释会每 30 秒发送一次以保持连接。

#### 事件时序示例

纯文本对话：

```
→ event: text_complete
  data: {"content": "Hello! How can I help you?"}

→ event: done
  data: {"response": "Hello! How can I help you?"}
```

带工具调用和权限审批的对话：

```
→ event: text_complete
  data: {"content": ""}

→ event: tool_start
  data: {"id": "tc_1", "name": "bash", "args": {"command": "rm foo"}}

→ event: permission_ask
  data: {"request_id": "perm_abc", "tool": "bash", "args": {"command": "rm foo"}, "description": "bash: rm foo"}
  (agent 挂起，等待前端审批)

← POST /api/permission/perm_abc  {"response": "allow"}

→ event: permission_resolved
  data: {"request_id": "perm_abc", "approved": true}

→ event: tool_complete
  data: {"id": "tc_1", "name": "bash", "result": "removed 'foo'"}

→ event: text_complete
  data: {"content": "I've removed the file foo."}

→ event: done
  data: {"response": "I've removed the file foo."}
```

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

## Permission

### POST /api/permission/{request_id}

审批或拒绝挂起的权限请求。当 agent 在 `normal` 模式下需要执行标记为 `ask` 的工具时，会通过 SSE 发送 `permission_ask` 事件，前端需调用此端点回复。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| request_id | string | `permission_ask` 事件中的 `request_id` |

**请求体：**

```json
{
  "response": "allow"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| response | string | 是 | `"allow"` 允许执行，`"deny"` 拒绝执行 |

**响应：**

```json
{
  "ok": true
}
```

> request_id 不存在或已过期时返回 **404**。

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
1. GET  /api/status              → 检查连接和当前模型
2. GET  /api/config              → 获取供应商/模型列表，渲染设置页
3. PUT  /api/config/mode         → 选择 normal 或 yolo 模式
4. POST /api/chat (SSE)          → 发消息，接收流式事件
5. POST /api/permission/{id}     → 审批工具执行权限（normal 模式）
6. POST /api/interrupt           → 用户想停止生成
7. GET  /api/sessions            → 渲染历史会话列表
8. GET  /api/sessions/{id}       → 恢复某个历史会话
9. POST /api/sessions            → 手动保存当前会话
10. DELETE /api/history          → 开始新对话
```

### Chat SSE 接入示例

`POST /api/chat` 返回 SSE 流，前端使用 `fetch` + `ReadableStream` 处理：

```javascript
async function chat(message) {
  const resp = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop(); // 保留未完成部分

    for (const part of parts) {
      if (!part.trim()) continue;
      const lines = part.split("\n");
      const type = lines[0].replace("event: ", "");
      const data = JSON.parse(lines[1].replace("data: ", ""));

      switch (type) {
        case "text_complete":
          appendToMessage(data.content);
          break;
        case "tool_start":
          showToolCard(data.id, data.name, data.args, "running");
          break;
        case "tool_complete":
          updateToolCard(data.id, data.result);
          break;
        case "permission_ask":
          showPermissionDialog(data);
          break;
        case "permission_resolved":
          updatePermissionStatus(data.request_id, data.approved);
          break;
        case "interrupted":
          showInterrupted();
          break;
        case "done":
          finalizeMessage(data.response);
          break;
        case "error":
          showError(data.message);
          break;
      }
    }
  }
}

// 审批权限请求
async function approvePermission(requestId, approved) {
  await fetch(`/api/permission/${requestId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response: approved ? "allow" : "deny" }),
  });
}
```

### SSE 解析注意事项

- `fetch` 的 `ReadableStream` 不保证按事件边界分割，需要在 buffer 中累积直到 `\n\n`
- 建议使用成熟的 SSE 解析库（如 `eventsource-parser`）简化处理
- `: keepalive` 开头的行是注释，用于保持连接，前端可忽略
- 事件按顺序到达，`tool_start` 一定在 `tool_complete` 之前

### 模式与权限

- `normal` 模式：标记为 `ask` 的工具触发 `permission_ask` 事件，需前端审批
- `yolo` 模式：安全工具自动批准，仅危险 bash 命令需审批
- 通过 `PUT /api/config/mode` 随时切换

### Config 管理建议

- `api_key_set` 为 false 时，前端应提示用户配置 API key
- 切换 provider/model 后，`ConfigResponse` 会返回最新的完整配置
