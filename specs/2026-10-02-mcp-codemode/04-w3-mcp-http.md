# ❌ Spec 04 · W3（已作废）：MCP streamable HTTP + resources + subscriptions（手写传输版）

> **2026-10-03 作废**：用户拍板改用官方 `mcp` Python SDK（v2）整体重写 MCP 客户端。
> 本工单的全部设计作废：urllib + 手写 SSE 解析、自建双 era 探测、`x-mcp-header`
> 取舍（SDK 原生处理）、假 HTTP 端点模式（W3-D10）、§1.6 写范围例外（03 个受影响的
> 现存测试改由 `06` 工单处理）。
> 现行计划：`05-w3a1-sdk-stdio.md`（SDK stdio 客户端）→ `06-w3a2-http-sse.md`
> （streamable HTTP + SSE）→ `07-w3b-subscriptions.md` ∥ `08-w3c-resources.md`（并行）。
> git 历史保留本文件原内容；总纲 §3 的 D6/D13/D15 已标注 SUPERSEDED。
