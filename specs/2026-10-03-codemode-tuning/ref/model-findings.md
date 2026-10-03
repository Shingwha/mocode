# 模型实测 codemode 使用问题（2026-10-03）

> 来源：用户配置的真实模型（stealth/space-bunny-alpha）在真实任务中使用 codemode 的
> 第一手记录，用户转述。与本组 `ref/field-findings.md`（用户本人 12 条）互补。
> 状态：全部已在 W1–W4 或 v2（`06-w6-codemode-v2.md`）闭环，括号内为落点。

## 阻断性

1. **`import re, asyncio, json` → ImportError**。模型按 Python 肌肉记忆写 import。
   （v2 C4：门控 import，8 模块白名单直接成功。）
2. **`tools.describe_tool(...)` → `unknown tool 'describe_tool'`**。模型把内建挂到
   `tools.` 门面下，脚本直接死掉，一轮往返浪费。
   （v2 C3：门面回退解析，怎么猜都能调；目录仍只列注册工具。）

## 静默丢数据（最危险）

3. **输出截断被忽略**。阶段输出里 `4611 chars truncated` + 临时文件路径被一扫而过，
   一整块查询结果从没被模型读到，下游却写了"完整"总结。
   （v2 C5：通知改祈使句——"before relying on this output, read the full result at
   <path>"。）
4. **store 手动预截断**。模型写 `d["text"][:20000]` 防超限，4-5 万字符的文档被砍半，
   未记录丢失量。根因：不知道默认限额（单值 256KB/总量 1MB）其实远没触到。
   （v2 C6：限额与口径写进 description；大件惯用法 tools.write 落盘 + store 存路径。）

## 附：同一轮的已知良性项

- `asyncio.ensure_future` 不自动等待——W3 已写入文档。
- `sorted(ALL_TOOLS)` TypeError——W1 改名 `all_tools()` 且 `names_only` 给了字符串表
  （本记录发生时模型已在新表面上运行，错误文案为 `use search_tools() or all_tools()`）。
