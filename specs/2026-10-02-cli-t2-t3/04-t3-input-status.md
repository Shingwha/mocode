# Spec 04 · T3 输入层与状态栏：键位 / 中断语义 / 中间件 / CLIContext 全量（波次 B）
> ✅ 2026-10-02 @ merge（`spec/t3-input-status`）—— T1-T6 全部完成并合并；合并后 747 passed。

> 分支 `spec/t3-input-status`；worktree `C:\Users\shifu\.worktrees\mocode\spec-t3-input-status`；
> 前置：波 A 全部合并（02 号的 `promote()` API 已在；01 号的实时区/spinner/verbose 标志已就位）；深读：总纲（裁决 #1/#3/#4）+ `ref/cli-tui-api.md` §4.1/§4.2/§5.1/§5.6/§5.7。
> 只读本 worktree；绝不 merge/push/tag。**本波与 05/06 并行**，禁触清单逐条遵守。

## 现状事实（已核实）

- `mocode/cli/input.py`（175 行）：键位写死于模块级 `build_keybindings(paste_handler)`（:84-123，tab/enter/escape+enter/c-j/BracketedPaste）；`Input(registry, ps1)` 只收命令表；PromptSession 延迟创建（:138）；`SlashCompleter` 对 `registry.all()` 前缀匹配；`prompt()` 返回后清行 + paste 解析 + Windows 代理字符净化。
- `mocode/cli/app.py`：REPL（`_repl` :196）→ `_dispatch` → `_run_chat`（:145，SIGINT 经 `signal.signal` 临时接管 → cancel follow task）→ `_follow`（:169，读到本 turn 终止事件即返回，finally `turn.cancel()`）。**运行期无键位读取**（只有 SIGINT）。
- `mocode/cli/plugin.py`：`CLIContext(commands, drawers, ui)`（:127）；`UI(conversation, *, is_interactive)` 仅 `message()`（:99-124）。
- `mocode/cli/theme.py`：`Theme` frozen dataclass + `DEFAULT_THEME`。
- 01 号工单（已合并）后：painter 有实时区管理器、spinner、`painter.verbose` 公开属性（键位未接）。
- 02 号工单（已合并）后：`bash` 工具有 `session.promote(call_id=None) -> dict`（None = 唯一前台调用）。
- `prompt_toolkit` 依赖已在（>=3.0.52）；`bottom_toolbar` 是 PromptSession 原生参数。

## 目标

CLIContext 长成 §5.1 的全量面：插件可注册键位（idle/running 两态）、叠加输入中间件、贡献状态栏段、读主题、拿只读会话视图、注册清理回调；终端自身的 Ctrl-C/Esc 语义升级为 Claude Code 式；Ctrl+B/Ctrl+O 作为运行期键位的旗舰用户落地。

## 工单（每项一个 commit）

### T1 CLIContext 全量成员
```python
@dataclass
class CLIContext:
    commands: CommandRegistry
    drawers: DrawerRegistry
    ui: UI                      # 本波不扩（07 号工单加 confirm/select/input）
    keys: KeyRegistry           # add(key, handler, *, when="idle"|"running", description="")
    input: InputMiddleware      # use(fn: str -> str | None)  # None = 已消费，跳过 dispatch
    status: StatusRegistry      # use(fn: StatusState -> Segment | None)
    header: HeaderRegistry      # set(lines: list[str])  # 打印式装饰（总纲裁决 #4）
    theme: Theme                # 只读视图（样式注册 v1 不做，docstring 注明）
    conversation: Conversation  # 只读使用
    def on_close(self, fn) -> None
```
- `StatusState`（model/cwd/running/usage/pending_approvals 字段按 §5.6）与 `Segment(text, priority)`；合并器按 priority 排序、超宽截断、分隔符 ` · `。
- app 装配点相应扩展；`on_close` 在 REPL finally 与 run_oneshot finally 逐个调用（异常隔离、顺序注册逆序执行）。
- §5.8 红线：**不提供** `ctx.app / ctx.display / ctx.renderer`。

### T2 键位注册与两态分发
- idle 键位：KeyRegistry 收集 → 首次建 PromptSession 时并入 `build_keybindings` 产物（此后注册的键下一次 session 重建生效——docstring 写明）。handler 签名 `(ctx: CLIContext) -> None | "clear"` 之类按需定（入参给足：当前 buffer 文本、conversation）。
- **运行期键位**：`prompt_toolkit.input.create_input(stdin)` 的 raw 读取循环——turn 开始时启动，`read_keys` 异步分发给 `when="running"` 的 handler，turn 结束即停（取消安全、非 TTY 不启动、`is_interactive` 为假不启动）。Esc/Ctrl-C 也走这条通道（T3）。
- **Ctrl+B 旗舰**（内置注册，非插件）：运行中按下 → `conversation.tools.get("bash")` 存在则 `await promote(None)`，结果经 `ui.message` 告知（"moved to background as shell_N"）；无 bash 工具/无前台调用 → 静默或一行提示。这是文档 §4.1 的第一个真实用户。
- **Ctrl+O**：切 `painter.verbose`（01 号预留的公开属性），立即重画实时区。

### T3 中断语义升级（§4.1）
- 空闲 Ctrl-C：输入非空 → 清空输入（不退出）；输入为空 → 提示后**再按一次**退出（两次确认，Claude Code 语义）。prompt_toolkit 的 `c-c` binding 实现。
- 运行中 Ctrl-C / Esc：`turn.cancel()`（经 running 通道；SIGINT signal 路径保留为兜底——两者并存时幂等）。
- 现有 `_on_sigint` 行为兼容：无 running 键位通道时（非交互/旧路径）语义不变。

### T4 输入中间件
- `Input.prompt` 返回前依次过中间件链（text → text；返回 None 表示已消费、跳过命令分发与模型调用，直接回到提示符）。
- 注册时序：build() 期注册，首 prompt 生效。
- 测试用例给一个内置示例：`/` 开头且含未闭合引号的输入不提交（可选，不作硬性要求——示例只为驱动 API）。

### T5 状态栏
- PromptSession `bottom_toolbar`：每次重画跑全部 status provider（合并器：priority 排序、截断、分隔符）；内置贡献段：`{model}`、`↑{in} ↓{out} tokens`（有 usage 时）、`{cwd}`（home 缩写 `~`）。
- 非交互/非 TTY：无 toolbar（现行为）。
- `docs/plugins.md`（**本波归你**）：新增 "Keys, middleware and the status bar" 一节——keys 两态、中间件链、status/header 贡献点、on_close、Ctrl+B/Ctrl+O 内置键位表、theme 只读说明。

### T6 测试
- `tests/test_input.py`（新）：中间件链（含 None 消费）、idle 键位合并、StatusRegistry 合并/截断/空段。
- `tests/test_cli_plugin.py`：CLIContext 新成员可见性、on_close 顺序与隔离。
- 运行期键位：raw 读取循环用 fake input（prompt_toolkit 的 Input 可注入/或抽象出 key 源接口）驱动——Ctrl+B 提升、Ctrl+O 翻转、Esc 取消。Windows 真跑为主。

## 验收

1. `uv run pytest -q` 全绿，独立退出码（基线 = 波 A 合并后数量，只增不减）。
2. 冒烟真实输出：交互终端下 Ctrl+B 转后台一条 sleep 命令（报告按键序列与屏显）；状态栏显示 model/tokens/cwd。
3. `git diff <merge-base> --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/cli/transcript.py`、`painter.py`、`display.py`、`render.py`、`lines.py`（01/05 领地；Ctrl+O 只调 `painter.verbose` 公开属性）。
- `mocode/core/**`、`mocode/host/**`（含 shell.py——02 已合，勿再动）、`mocode/testing/**`、`providers/**`、`examples/**`。
- `docs/ARCHITECTURE.md`（06 领地）、`pyproject.toml`（05 领地）。
- tests：`tests/test_input.py`（新）、`tests/test_cli_plugin.py`、`tests/test_display.py` **禁触**（05 领地）、`tests/test_session.py`/`test_conversations.py`（06 领地）。
- spec 文件。

## 最终报告格式

工单状态表（T1-T6）｜commit 清单｜自测真实结论（pytest + Ctrl+B/Ctrl+O 冒烟）｜偏差与取舍｜未决问题。阻塞即停。
