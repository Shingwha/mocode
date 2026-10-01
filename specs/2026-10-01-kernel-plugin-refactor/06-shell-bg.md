# Spec 06 · S 系列：shell 后台任务 + PluginMessage（波次 W4-A）

> 分支 `spec/shell-bg`；worktree `C:\Users\shifu\.worktrees\mocode\spec-shell-bg`；
> 前置：W3 已合并（BuildContext/HostContext 分裂、spawn、无子类化内置插件）；开工前通读 `mocode/host/plugin/builtin/shell.py` 与 `mocode/core/events.py` 校准；
> 深读：总纲 + `ref/kernel-plugin-api.md` 的 S0-S3 + `ref/cli-tui-api.md` 的 §3.5（open→sealed→committed）与 §5.5（emit_message API 形态）。
> 你只读本 worktree 内的文件；绝不操作主检出，绝不 merge/push/tag。

## 现状事实（W3 后的期望形态）

- `shell.py`：bash 工具前台 `await proc.wait()`（模型阻塞）；无句柄、无后台；超时 `proc.kill()` 只杀直接子进程（**孙进程泄漏**）；输出经 `_pump` 逐行 `ctx.emit(ToolOutput(call_id, text, stream))`；`BashSession` 持久 cwd/env（cd/export 本地拦截）；restart 重建。W2 已迁 `schema=`/`with_context`/policy；W3 已去子类化。
- `Plugin.close(ctx)` 存在（**不新增 teardown**，总纲取舍 #2）——后台任务清理挂这里。
- 事件面（W1 后）：`origin/parent_call_id` 已上事件与 ToolCallContext；`HostContext.emit` 盖 run_id（K9）。**尚无 PluginMessage**。
- 配置面：`ctx.plugin_config("shell")` 可读 `plugins.shell` 配置段。
- 平台事实：开发与 CI 均为 **Windows**——`start_new_session`/`os.killpg` 仅 POSIX；所有进程组行为按 `sys.platform` 分支并在 Windows 跳过对应测试。

## 目标

shell 三件套（后台启动 / 增量输出 / 终止）+ 进程组治理 + 生命周期清理 + 完成通知的宿主 API（PluginMessage / emit_message / seal_message）。**前台 bash 行为零变化**（现有测试断言不动）。

## 工单（每项一个 commit）

### T1 PluginMessage 事件（core 层机制）
- `mocode/core/events.py` 新事件（通用机制，无任何插件名）：
  ```python
  @dataclass(kw_only=True)
  class PluginMessage(Event):
      type: ClassVar[str] = "plugin_message"
      kind: str = ""          # 命名空间约定 "<插件名>/<类型>"；非该前缀的 kind 为内置
      data: dict = field(default_factory=dict)
      block_id: str = ""      # 同 block_id 的后续消息更新同一块；缺省每次即新块
      sealed: bool = False    # seal_message 发出；晚到的同 block_id 更新走"追加跟随块"（CLI 层策略）
  ```
  `summary()` 覆写（如 `f"plugin message: {kind}"`）；`to_dict` 往返（嵌套 data 走既有 `_plain`）。继承 `Event`、只扩展不重做——遵守内核事件契约。
- 测试：序列化往返、summary、经 `HostContext.emit` 自动盖 run_id。

### T2 emit_message / seal_message（host 层）
- `HostContext`：
  ```python
  async def emit_message(self, kind: str, data: dict, *, block_id: str = "") -> None
  async def seal_message(self, block_id: str) -> None
  ```
  即 `emit(PluginMessage(kind=..., data=..., block_id=...))` / `emit(PluginMessage(block_id=..., sealed=True))` 的便利封装；在 prepare/close 期与工具 call time 均可用（后者经 ToolCallContext.emit 直接发 PluginMessage 同样合法）。
- **不做 session 持久化**（总纲取舍 #1）：`docs/plugins.md` 明示"PluginMessage 不随 session 落盘，重启后不回放——已知限制"。
- SDK：`mocode/plugins/__init__.py` 导出 `PluginMessage`。

### T3 后台三件套（shell.py）
- `bash` 参数加 `run_in_background: bool`（schema，default false）：
  - true：立即返回 `ToolResult(content="started <shell_id> (running in background)", details={"shell_id", "command", "status": "running"})`，无 exit_code；早期输出继续走 ToolOutput 进当前 open 块（现状机制）。
- 新工具 `bash_output(shell_id, filter=None, wait=False, timeout=None)`：
  - 增量游标（消费式）：只返回**自上次调用以来**的新行；`filter` 为正则时只返回匹配行且匹配行即被消费（不匹配的行保留在 ring 里）。
  - 返回 `details={"lines", "status": "running|completed|killed|timed_out", "exit_code"}`；`wait=True` 用 `asyncio.Event` 阻塞到完成或 timeout——**禁止 sleep 轮询**。
- 新工具 `kill_shell(shell_id)`：终止进程组（POSIX）/进程（Windows），标记 killed。
- `_Job`（挂在 BashSession/插件实例上的 per-conversation 状态）：
  ```python
  @dataclass
  class _Job:
      id: str; command: str; proc; collector: asyncio.Task
      buf_out/buf_err: deque[str]        # 有界 ring：各 2000 行 / 256KB，溢出丢最旧 + 计数
      discarded: int                     # "…(N earlier lines discarded)"
      consumed: int; done: asyncio.Event; exit_code: int | None; status: str
  ```
- 治理：
  - **所有** subprocess 创建加 `start_new_session=True`（仅 POSIX；Windows 省略该参数）；前台与后台超时终止改为 POSIX `os.killpg(proc.pid, SIGKILL)` → 降级 `proc.kill()`（**修复孙进程泄漏**）；Windows 保持 `proc.kill()` 并在 docstring 注明局限。
  - 并发上限 16（`plugins.shell.max_background` 可配）；到达后新后台启动返回明确错误。
  - 后台超时：默认无；`plugins.shell.background_timeout` 硬上限默认 3600s，到期 killpg + `status="timed_out"`。
  - 生命周期：`restart` 与 `Plugin.close(ctx)` 杀掉并清理全部后台任务（close 异常隔离——shell 插件自身 close 失败不阻断其他插件）。
  - 会话语义：后台任务启动时快照 cwd/env，运行中 cd/export 不影响（与前台一致）。

### T4 完成通知（emit_message 的第一个用户）
- 任务完成且**当前无运行中的 turn**（`ctx.agent` 空闲）→ `emit_message("shell/background-done", {"jobs": [{id, command, exit_code, status}, ...]}, block_id=f"shell-bg-{n}")`；同刻多任务完成**合并为一条**（实现：完成侧只入队，一个 notifier 协程 ~300ms 窗口聚批后 emit 一次）。turn 仍在跑时（模型会自己调 bash_output）不发。
- turn 内启动、turn 结束时仍在跑的 job：块被 seal 时标注"仍在后台运行"（content 层面即可）。

### T5 测试 + 文档
- 测试（Windows 可跑的部分全跑；POSIX 专属用 `sys.platform == "posix"` 守卫或 mock proc）：
  - 后台启动立即返回句柄；`bash_output` 增量语义（两次调用不重复）、filter 消费语义、wait=True 到完成；
  - ring 有界（灌 3000 行，断言丢弃计数）；
  - kill_shell / close() 清理全部 job；
  - 并发上限拒绝；完成通知合并（两个 job 同刻完成 → 一条 PluginMessage）；
  - 前台行为回归：现有 shell 测试原样通过。
- `docs/plugins.md`：emit_message/seal_message 一节（含"不落盘"限制与 CLI 侧"追加跟随块"策略的前向说明）。

## 验收

1. `uv run pytest -q` 全绿，独立确认退出码 0。
2. `uv run python - <<'EOF'` 冒烟：起一个后台 job（如 `python -c "import time; time.sleep(0.2)"`），bash_output 拿到完成态，close 清理（Windows 路径即可）——报告真实输出。
3. `git diff <merge-base> --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/core/dispatch.py`、`mocode/core/agent.py`、`mocode/core/tool.py`、`mocode/core/hook.py`、`mocode/host/plugin/host.py`、`mocode/host/plugin/base.py`、`mocode/host/plugin/loader.py`、`mocode/host/config.py`、`mocode/host/runtime.py`、`mocode/cli/**`、`mocode/testing/**`、`tests/providers.py`（W4-B 并行领地）。
- `mocode/core/events.py` 只允许新增 PluginMessage（不动既有事件字段）；`mocode/host/plugin/context.py` 只允许加两个方法。
- 测试文件：只动/建 `tests/test_builtin_plugins.py` 的 shell 区与新文件 `tests/test_shell_bg.py`；**不动** `tests/test_plugins.py`、`tests/test_cli_plugin.py`、`tests/test_agent_loop.py`、`tests/test_events.py`（如需 PluginMessage 事件级测试，放进 test_shell_bg.py 或新 `tests/test_plugin_message.py`）。
- `docs/ARCHITECTURE.md`（W4-B 并行领地）；`TODO.md`；spec 文件。

## 最终报告格式

工单状态表｜commit 清单（hash+message）｜自测真实结论（命令+退出码）｜偏差与取舍（尤其 Windows/POSIX 分支的实际覆盖）｜未决问题。遇阻塞：报告后停止，不越界自救。
