✅ 2026-10-03 @ feat/mcp-sdk-http（已合并 master `82fbf9e`；910 passed exit 0，lead 合并前后各复核一次）

# Spec 06 · W3a-2：streamable HTTP + SSE 传输（波次 W3a-2）

> 分支 `feat/mcp-sdk-http`；worktree 由 lead 建。
> 前置：W3a-1（`05`）已合并 master。
> 深读：`ref/agent-plugins-mcp-json.md` §4/§6（mcp.json 规则）、`ref/mcp-protocol.md`
> §10/§4（HTTP/探测）、`ref/mocode-api.md`、`05` 工单 §1/§2（SDK 事实与 seam）。
> 绝不操作主检出，绝不 merge/push/tag/checkout master。

## 0. 目标

给 SDK 版 `McpSession`（W3a-1 交付）补上 **streamable HTTP 与 legacy SSE 两种传输**：
config 层真解析 `url`/`headers` 条目（替换今天的 report+skip），session 层按
`cfg.transport` 构造 SDK target。**不启用时行为零变化**；stdio 行为不变。

## 1. 已核实的 SDK 事实（step-0 实测 2026-10-03；不符以实测为准并报告）

- `from mcp.client.streamable_http import streamable_http_client`：
  `async def streamable_http_client(url: str, *, http_client: httpx2.AsyncClient |
  None = None, terminate_on_close: bool = True, max_sse_event_size: int = 1MiB)`。
  **没有 headers/timeout 参数**——Authorization 等头与超时必须经预配置的
  `httpx2.AsyncClient(headers=..., timeout=httpx2.Timeout(connect, read))` 传入；
  SDK 的 MCP 头与 client 默认头合并（client 默认优先）。
- `from mcp.client.sse import sse_client`：`async def sse_client(url: str, *,
  headers: dict | None = None, timeout: float = 5.0, sse_read_timeout: float =
  300.0, httpx_client_factory=create_mcp_http_client, auth=None,
  on_session_created=None)`——**sse 直接收 `headers`**（与 streamable-http 不同）。
- `Client(target)`：URL 字符串 → streamable-http；transport 对象原样透传。
  本波统一显式构造 transport 对象后传给 `Client`（两种 HTTP 传输都需要自控
  headers/超时，且 W3a-1 的 `McpSession` 已按 target 分派）。
- 真实托管 streamable-HTTP server（lead step-0 冒烟，连接细节不入库）：
  `mode="auto"` 协商出 **2025-11-25（legacy 回退）**，headers 鉴权可用。

## 2. 冻结实现决策

| # | 决策 |
|---|---|
| W3a-2-D1 | `McpServerConfig` 增 `transport: str`（`"stdio"`/`"streamable-http"`/`"sse"`）、`url: str | None`、`headers: dict[str, str]`；`command` 改可选；docstring 同步 |
| W3a-2-D2 | config 解析：`http` 与 `streamable-http` 都归一为 `"streamable-http"`；`_TRANSPORT_TYPES`/`_HTTP_KEYS`（`config.py` 预留常量）接进真路径；mocode 自有文件无 `type` 时按 `url` 推断 `"streamable-http"`（显式 `type:"sse"` 才走 sse）；严格插件文件 `type` 必填 |
| W3a-2-D3 | url 校验（不合法 → 该条目 report+skip）：绝对 http(s)；非 loopback（`localhost`/IP 字面量以外）必须 https；禁 userinfo；禁 fragment |
| W3a-2-D4 | headers：大小写不敏感重名检测（重名 → report+skip 该条目）；严格插件文件**禁止**对 url/header 名/header 值做 `${VAR}` 或 `!command` 展开（按标准，遇到即 report+skip）；mocode 自有文件只允许对 **url 与 header 值**做 `${VAR}` 展开（header 名不展开） |
| W3a-2-D5 | `sse` 条目**支持**（取代总纲 D15）：skipped 分支删除；解析为 `transport="sse"`。legacy 2024-11-05 HTTP+SSE 语义由 SDK 承载 |
| W3a-2-D6 | session target 构造（`client.py`）：`streamable-http` → `httpx2.AsyncClient(headers=cfg.headers, timeout=httpx2.Timeout(connect, read=cfg.timeout or 60))` + `streamable_http_client(cfg.url, http_client=http)`；`sse` → `sse_client(cfg.url, headers=cfg.headers, timeout=…, sse_read_timeout=…)`；同一 `McpSession`/`Client(mode="auto", read_timeout_seconds=…)` 路径不变 |
| W3a-2-D7 | target 选择落在 `client.py`：`_serve()` 按 `cfg.transport` 构造（stdio 即 W3a-1 现状；streamable-http/sse 按 D6），**构造点保持唯一**；`runtime.py` 不因传输类型增加分支（`_must_be_declared`/背景连接/超时/`sync_tools` 不变，若确需改动须在报告论证） |
| W3a-2-D8 | 测试：新文件 `tests/test_builtin_mcp_http.py`——假端点用 stdlib `http.server`（`ThreadingHTTPServer(("127.0.0.1", 0))`、`server_address` 取端口、daemon 线程 `serve_forever`、teardown `shutdown()+server_close()`），覆盖：modern JSON 响应、SSE 响应、**Authorization/自定义头断言**、legacy `initialize`+session（sse fake）；每个 await 用 `BOUND=15` 包裹。**零网络依赖** |

## 3. 工单（每项一个 commit，每个 commit 独立过全量门禁）

### T1 config 解析 + McpServerConfig
- 按 D1–D5 实现；`config.py` 单测在 `tests/test_builtin_mcp.py` 的配置类内补 http/sse 解析用例。
- **三个现存测试必须翻转**（§1.6 例外 + sse 决策）：`test_sse_is_rejected_with_a_hint`（sse 现在应解析成功）、`test_streamable_http_is_skipped_until_wave_w3`（应解析成功）、`test_type_is_optional_in_mocode_files_and_inferred`（url 条目应解析成功）。除此之外该文件逐字节不变。
- commit：`feat(mcp): parse streamable-http and sse server entries`

### T2 session target + runtime 分派 + HTTP 测试
- 按 D6/D7 实现；`tests/test_builtin_mcp_http.py` 按 D8 建假端点。
- commit：`feat(mcp): add streamable http and sse transports`

## 4. 验收

1. `uv run pytest -q` 全绿、退出码独立确认、数量 ≥ 891（每 commit 各跑）。
2. `uv run pytest tests/test_builtin_mcp_http.py -q` 全绿。
3. `git diff --stat <W3a-1 合并点>` 只含 `builtin/mcp/**`、`tests/test_builtin_mcp.py`、`tests/test_builtin_mcp_http.py`；core/host.py/pyproject/uv.lock/docs 零 diff。
4. `import mocode` <1ms 不受影响（D2：SDK import 不进包顶层）。

## 5. 禁触清单

`mocode/core/**`；`mocode/host/**` 除 `builtin/mcp/**` 外的一切；`README.md`；`docs/**`；`tests/test_builtin_mcp_codemode.py` 及其它测试；`pyproject.toml`/`uv.lock`；spec 文件。

## 6. 最终报告格式

同 `05`（工单状态表/commit 清单/自测真实结论/偏差与取舍/未决问题）。
