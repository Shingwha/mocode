# mocode WebUI 实现方案

## Context

mocode 是一个 CLI 编码助手，核心架构是 UI 无关的：`MocodeCore` 通过 `EventBus` 发布事件，CLI 层订阅事件并渲染到终端。用户希望添加 WebUI，通过浏览器使用全部功能（对话、session 列表、命令、配置管理等）。

**核心结论：完全可行，架构天然支持。** WebUI 定位为与 CLI 对等的"浏览器前端"，直接调用 MocodeCore，而非塞进 Gateway 的 channel 模型。

---

## 架构决策：独立 Web App（非 Gateway Channel）

**WebUI 作为独立入口**，与 CLI 并列，直接调用 `MocodeCore`：

```
mocode              # CLI 入口 → CLIApp → MocodeCore
mocode web           # WebUI 入口 → WebApp → MocodeCore
mocode gateway       # Gateway 入口 → GatewayApp → MocodeCore (多用户)
```

**为什么不用 Gateway Channel：**

| 功能需求 | Gateway Channel 的限制 | 独立 Web App 的优势 |
|---------|----------------------|-------------------|
| 权限控制 | 强制 yolo 模式（自动批准所有工具） | 完整的 allow/deny 交互式权限 UI |
| 命令系统 | 无（gateway 只有裸 chat） | 完整暴露 /help, /provider, /session, /mode 等 |
| 配置管理 | 受限（gateway 模式下不可随意切换） | 完整：模型/提供商/模式切换 |
| BaseChannel 接口 | `send()` 变空操作（设计异味） | 不受约束 |
| MessageBus | 绕过（SSE 不走队列） | 直接调用，无中间层 |
| 功能上限 | 受 gateway 桥接架构约束 | 与 CLI 完全对等，可无限扩展 |

**可从 Gateway 复用的部分：** `UserRouter` 的多用户隔离模式（LRU 淘汰、per-user Lock）可参考但独立实现，不依赖 gateway 包。

---

## 方案概览

```
mocode web              # 单用户模式（本地开发）
mocode web --port 8080  # 指定端口
mocode web --multi-user # 多用户模式
```

技术栈：**FastAPI + uvicorn + SSE 流式 + WebSocket 实时通信 + 内嵌前端**

---

## 一、依赖（可选安装）

```toml
[project.optional-dependencies]
web = ["fastapi>=0.110.0", "uvicorn[standard]>=0.27.0", "sse-starlette>=1.6.0"]
```

```bash
pip install mocode[web]   # 或 uv tool install -e ".[web]"
```

---

## 二、文件结构

```
mocode/
    web/                          # 新增：WebUI 模块（与 cli/ 并列）
        __init__.py
        app.py                    # WebApp 入口，创建 MocodeCore + FastAPI
        server.py                 # FastAPI 应用工厂
        deps.py                   # 依赖注入（session -> MocodeCore）
        permission.py             # WebPermissionHandler（WebSocket 权限交互）
        events.py                 # SSEEventBridge（EventBus -> SSE 队列）
        routes/
            __init__.py
            chat.py               # POST /api/chat (SSE) + WebSocket /ws
            sessions.py           # Session CRUD
            commands.py           # 命令执行（/help, /provider, /session 等）
            config.py             # 配置/模型/提供商管理
            files.py              # 文件浏览/上传/下载
        static/                   # 内嵌前端
            index.html
            app.js
            styles.css
    main.py                       # 添加 web 子命令入口
```

**关键：** `mocode/web/` 与 `mocode/cli/` 是并列关系，都直接使用 `MocodeCore`。

---

## 三、分阶段实施

### Phase 1：流式响应基础

当前 `AsyncOpenAIProvider.call()` 是非流式的，`TEXT_DELTA` 事件已定义但从未触发。

**修改文件：**

| 文件 | 改动 |
|------|------|
| `mocode/providers/openai.py` | 新增 `call_stream()` 异步生成器（`stream=True`），现有 `call()` 不变 |
| `mocode/core/agent.py` | 新增 `chat_stream()` 方法，emit `TEXT_STREAMING` → `TEXT_DELTA`(逐 chunk) → `TEXT_COMPLETE`，工具调用部分复用现有逻辑 |
| `mocode/core/orchestrator.py` | 新增 `chat_stream()` 透传方法 |

**向后兼容：** `chat()` 不变，CLI 继续用。`chat_stream()` 是新方法。

### Phase 2：Web 应用骨架

**`mocode/web/app.py`** — WebApp 入口：
- 创建 `MocodeCore` 实例（与 CLIApp 类似的初始化流程）
- 创建 `WebPermissionHandler`（替代 CLI 的 `CLIPermissionHandler`）
- 创建 FastAPI app 并启动 uvicorn

**`mocode/web/server.py`** — FastAPI 应用工厂：
- 注册路由模块
- 挂载静态文件
- CORS 中间件
- 会话 cookie 管理

**`mocode/web/permission.py`** — `WebPermissionHandler(PermissionHandler)`：
```python
class WebPermissionHandler(PermissionHandler):
    """通过 WebSocket 推送权限请求，等待浏览器响应"""
    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}

    async def ask_permission(self, tool_name, tool_args) -> str:
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        # 通过 WebSocket 推送权限请求
        # 等待浏览器响应（5 分钟超时自动 deny）
        return await asyncio.wait_for(future, timeout=300)

    def resolve_permission(self, request_id, action):
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(action)
```

**`mocode/web/events.py`** — `SSEEventBridge`：
- 订阅 EventBus 事件，转换为 SSE 兼容格式
- asyncio.Queue 缓冲事件
- try/finally 确保连接断开时 unsubscribe

**`mocode/main.py`** — 添加 `web` 子命令：
```python
def _run_web(args):
    from mocode.web.app import WebApp
    app = WebApp(port=args.port, host=args.host)
    asyncio.run(app.run())
```

### Phase 3：API 端点

#### Chat（SSE 流式）
```
POST /api/chat
  Body: {"message": "...", "stream": true}
  Response: SSE 事件流
    event: text_delta    data: {"content": "..."}
    event: tool_start    data: {"name": "bash", "args": {...}}
    event: tool_complete data: {"name": "bash", "result": "..."}
    event: text_complete data: {"content": "完整回复"}
    event: error         data: {"error": "..."}
    event: done          data: {}
```

#### WebSocket 实时通道
```
WebSocket /ws
  Server -> Client:
    {"type": "permission_request", "id": "uuid", "tool_name": "bash", ...}
    {"type": "status_update", "status": "thinking"}
    {"type": "tool_progress", ...}
  Client -> Server:
    {"type": "permission_response", "id": "uuid", "action": "allow"|"deny"}
    {"type": "interrupt"}
    {"type": "command", "command": "/clear"}   # 命令执行
```

#### 命令系统
```
POST /api/command
  Body: {"command": "/provider openai gpt-4o"}
  Response: {"output": "...", "success": true}

GET /api/commands
  Response: [{"name": "/help", "description": "..."}, ...]
```

直接复用 `mocode/cli/commands/` 中的 `CommandRegistry`，WebUI 调用 `Command.execute()` 获取结果。

#### Session 管理
```
GET    /api/sessions           列出会话
GET    /api/sessions/{id}      获取会话详情（含消息列表）
POST   /api/sessions/save      保存当前会话
DELETE /api/sessions/{id}      删除会话
POST   /api/sessions/{id}/load 加载会话到当前对话
GET    /api/messages           获取当前对话消息列表
```

#### 配置管理
```
GET    /api/config             获取配置（脱敏，不暴露 api_key）
PUT    /api/config             更新配置
GET    /api/config/models      列出可用模型
POST   /api/config/model       切换模型 {"model": "...", "provider": "..."}
GET    /api/config/providers   列出提供商
POST   /api/config/providers   新增提供商
DELETE /api/config/providers/{key}  删除提供商
GET    /api/config/mode        获取当前模式
POST   /api/config/mode        切换模式 {"mode": "yolo"|"normal"}
```

#### 其他
```
GET    /api/status             当前状态 {busy, model, session_id, token_usage}
POST   /api/interrupt          中断当前操作
POST   /api/compact            触发上下文压缩
POST   /api/clear              清空当前对话
```

### Phase 4：前端 MVP

**内嵌式单页应用**（纯 HTML/JS/CSS，无需 node/构建工具）：

- `index.html` — 聊天主界面 + 左侧边栏（会话列表、设置）+ 顶部（模型选择器）
- `app.js` — SSE 流式渲染、WebSocket 权限交互、命令面板、会话管理
- `styles.css` — 深色主题，响应式布局

**MVP 功能清单：**
1. 聊天消息列表（用户/助手/工具调用卡片）
2. 流式文本输出（SSE 逐字渲染）
3. 工具调用展示（名称、参数、结果预览）
4. 权限确认弹窗（allow/deny）
5. 会话列表 + 切换 + 新建
6. 模型/提供商选择器
7. 模式切换（normal/yolo）
8. 中断按钮
9. 命令输入（`/` 前缀命令，如 `/clear`, `/compact`）

**后续增强（非 MVP）：**
- Markdown 渲染 + 代码高亮
- CodeMirror 代码编辑器集成
- 文件上传/下载
- 多标签对话
- 键盘快捷键
- 移动端适配

### Phase 5：测试与收尾

新增 `tests/test_web/`：
- `test_server.py` — FastAPI app 创建和生命周期
- `test_routes_chat.py` — SSE 端点（用 TestClient）
- `test_routes_sessions.py` — Session CRUD
- `test_routes_commands.py` — 命令执行
- `test_routes_config.py` — 配置管理
- `test_permission.py` — WebPermissionHandler 超时和解析
- `test_events.py` — SSEEventBridge 订阅/取消

---

## 四、启动方式

```bash
# 安装
uv tool install -e ".[web]"

# 单用户模式（默认）
mocode web

# 自定义端口
mocode web --port 3000 --host 0.0.0.0

# 浏览器访问
open http://localhost:8080
```

---

## 五、关键设计决策

### 1. 命令系统复用

CLI 的 `CommandRegistry` 已经是 UI 无关的（`CommandContext` 接受 `client` 和 `display`）。WebUI 的命令路由直接调用：

```python
@router.post("/api/command")
async def execute_command(req: CommandRequest, core = Depends(get_core)):
    ctx = CommandContext(client=core, args=req.args, display=None, pending_message=None)
    cmd = command_registry.get(req.command_name)
    result = cmd.execute(ctx)
    return {"success": result}
```

### 2. 权限交互流程

```
1. MocodeCore.chat() 触发工具调用
2. PermissionChecker 调用 WebPermissionHandler.ask_permission()
3. ask_permission() 创建 Future，通过 WebSocket 推送请求
4. 浏览器弹出确认对话框
5. 用户点击 allow/deny
6. WebSocket 收到响应，调用 resolve_permission()
7. Future 被 resolve，chat() 继续执行
```

### 3. 事件桥接

```
AsyncAgent.chat_stream()
  → emit(TEXT_DELTA) → SSEEventBridge → asyncio.Queue → SSE /api/chat
  → emit(TOOL_START)  → SSEEventBridge → asyncio.Queue → SSE /api/chat
  → emit(TOOL_COMPLETE) → SSEEventBridge → asyncio.Queue → SSE /api/chat
  → emit(TEXT_COMPLETE) → SSEEventBridge → asyncio.Queue → SSE /api/chat
```

---

## 六、潜在挑战与应对

| 挑战 | 应对 |
|------|------|
| EventBus 订阅泄漏 | SSE 生成器 try/finally 确保 unsubscribe |
| WebPermissionHandler 需要 WebSocket 引用 | 在 MocodeCore 创建时注入 handler，WebSocket 连接时注册引用 |
| 流式不影响 CLI | `chat()` 不变，`chat_stream()` 是新方法 |
| 前端静态文件路径 | `__file__` 相对路径 + StaticFiles |
| Command 的 display 参数 | 传 None 或创建 WebDisplay 适配器 |

---

## 七、验证方式

1. `mocode web` 启动无报错，浏览器访问 `http://localhost:8080`
2. 发送消息，SSE 流式返回文本
3. 触发工具调用（bash），看到 tool_start/tool_complete 事件卡片
4. 触发权限请求，浏览器弹出确认对话框，点击 allow 继续
5. `/clear`, `/compact` 等命令正常执行
6. 会话保存/加载/切换正常
7. 模型/提供商切换正常
8. 中断按钮可中断当前操作
9. `uv run pytest tests/ -v` 全部通过，CLI 功能不受影响
