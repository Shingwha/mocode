# Spec FINALIZE · 终验与收尾（波次 W2，lead 亲自执行）
> 状态：✅ 2026-10-03 lead 亲自执行（守卫硬失败、空洞补齐、孤儿门禁、计时终验、文档、登记）

> 分支 `test/slim-w2-finalize`；前置：W1-A / W1-B / W1-C 均已合并进 main
> 本工单由 lead 逐条执行，不派 worker。

## 目标

把三个分支的成果合成一个全绿、快速、稳健的套件，并把时序守卫从 recording 翻成硬失败。

## 工单（按序执行，每项一个 commit）

### T1 合并（顺序 A → B → C，每次 merge --no-ff）

每次合并前后各跑一次全量门禁，独立确认退出码（`uv run pytest -q; echo "EXIT=$?"`，
禁止管道吞码）。红了 → 整分支打回对应 agent 修复后重合并；lead 只做跨模块小修。
合并前读该 agent 的最终报告：偏差、取舍、未决逐条裁决后再 merge。

### T2 守卫翻硬失败

改 `tests/conftest.py` 的 `_sleep_guard`：recording 模式 → 直接 raise，错误信息指向
替代 API（`wait_until` / 事件门控 / `settle` / `real_time`）。若翻硬后出现失败：

- 属于合法真睡眠（子进程假件内部、shell 看门狗时限）→ 在其包一层 `real_time()`；
- 属于漏网裸睡 → 修法同 W1 纪律；不得靠放宽守卫过关。

重新全量绿。

### T3 补覆盖空洞（小而快，契约级）

新增 `tests/test_transcript.py`（不改产品代码，纯契约测试）：

- `tool_call_args` 对坏 JSON 的失败路径（返回 None / 抛错的既有行为是什么就断言什么）；
- `content_parts` / `text_of` / `reasoning_of` 对多模态 content 列表的提取；
- `answered_call_id`、`tool_call_by_id` 的匹配语义。

新增 `tests/test_host_tools.py`：`host/io.py` `read_json`/`write_json` 往返与坏文件行为；
`host/text.py` `decode_bytes` / `one_line`。总量 <25 个测试。

### T4 孤儿模块门禁

```bash
for f in $(find mocode -name '*.py' ! -name '__init__.py'); do
  grep -rlq "${f##*/}" tests/ || echo "ORPHAN: $f"
done
```

有 ORPHAN → 补一条最小契约测试或在本组 ref/ 下登记豁免理由（已知豁免：
`cli/input.py`、`cli/text.py`、`cli/dialogs.py` 终端交互层）。

### T5 计时与规模终验

- `uv run pytest -q; echo "EXIT=$?"` → 全绿，用例数 450±50，墙钟 <15s；
- `uv run pytest --durations=15 -q` → 无单项 >1s；`grep -rn "time.sleep\|asyncio.sleep" tests/`
  → 仅子进程假件内部、settle/real_time、脚本内 sleep 语境；
- 各波次保留底线逐项核对（总纲表），低于底线即补；
- `git diff main --stat -- mocode/` → 空（产品代码零 diff）。

### T6 文档同步

- `docs/testing.md`：新增"时间是纪律不是运气"一节（wait_until/事件门控/FakeClock/
  settle/real_time 的选用规则与守卫），重写「Holding a background job open」，示例改结构化
  断言风格；在文末登记 specs 组路径。
- `AGENTS.md`：Testing Patterns 段补一条——测试时序纪律引用 docs/testing.md，裸睡守卫
  硬失败；事件/钩子新增的四处清单不变。

### T7 收尾

- 汇总各组删除登记为 `specs/2026-10-03-test-suite-slimming/ref/deletions.md`
  （测试名 + 理由 + 去向）；
- 清理 `~/.worktrees/mocode/` 下已合并分支的 worktree，保留分支；
- 终验报告：做了什么 / 取舍登记 / 遗留清单 / 等用户指令事项。**不 push、不打 tag。**

## 验收

1. `uv run pytest -q; echo "EXIT=$?"` → exit 0，450±50 用例，<15s
2. `uv run pytest --durations=15 -q` → 无 >1s 单项
3. 守卫硬失败模式下 grep 裸睡仅三类合法语境
4. 孤儿门禁输出仅已知豁免三项
5. `git log --oneline main` 含 W0/W1A/W1B/W1C/W2 五个 merge commit，`git diff main --stat -- mocode/` 空
6. docs/testing.md 与 AGENTS.md 变更随本波提交
