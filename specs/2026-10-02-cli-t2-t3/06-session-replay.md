# Spec 06 · PluginMessage 持久化与回放（波次 B）

> 分支 `spec/session-replay`；worktree `C:\Users\shifu\.worktrees\mocode\spec-session-replay`；
> 前置：波 A 全部合并；深读：总纲（裁决 #6——翻案上一组取舍 #1）+ `ref/cli-tui-api.md` §5.4/§5.5。
> 只读本 worktree；绝不 merge/push/tag。**本波与 04/05 并行**（host 领土，文件不相交）。

## 现状事实（已核实）

- `Session`（`host/session.py:30-89`）12 个字段全无事件类内容；`_as_session`（`conversation.py:314-328`）只收 messages/system_prompt/tool_schemas/plugin_state。
- `Conversation` 没有自己的事件订阅（`subscribe()` :114 只是转出 channel）；`load_session`（:215-241）流程：busy 检查 → save 旧 → identity/model → `adopt(messages)` → `reinstate(session)` → plugin_states → `await self.changed()`（先 materialize 再 publish `ConversationChanged`）。
- `PluginMessage`（`core/events.py:275-302`）`kind/data/block_id/sealed`，`to_dict()` 往返已有测试；`channel.publish` 会重新盖 seq（:236-244），run_id 不会被覆盖。
- `docs/plugins.md:557-560` 现写着"不持久化"的限制段——**本波改写它**。
- CLI 侧：transcript 的 PluginMessage 折叠、跟随块、apply_history 已就绪（T1）；渲染端收到重发事件即正常出块。

## 目标

`emit_message` 发出的 PluginMessage 随 session 保存；resume 后重放进事件流，transcript 原样重建这些块——兑现文档 §5.4"进事件通道、随 session 持久化、重启可回放"。

## 工单（每项一个 commit）

### T1 捕获
- `Conversation` 内部持有有界捕获：`deque(maxlen=200)` + channel 订阅（只认 `PluginMessage` 且**非重放**事件——防重复见 T2）。订阅随 `aclose()` 关闭。
- 捕获存**序列化 dict**（`event.to_dict()`），不持事件对象（跨 resume 无对象生命周期问题）。

### T2 持久化
- `Session` 新字段 `plugin_messages: list[dict] = []`（from_dict/to_dict 往返；`from_dict` 对坏条目宽容——非 dict 丢弃）。
- `_as_session` 收入捕获列表；`save()` 自然落盘。

### T3 回放
- `load_session` 尾部（`changed()` **之后**——ConversationChanged 会清 transcript 重灌历史，回放必须在其后）：逐条经 `agent.channel.publish` 重发（seq 重新盖章是既有语义；run_id 保留存储值——渲染端不依赖它）。重放期间置 `self._replaying = True`，捕获订阅看到重放事件即跳过（防下次 save 重复）。
- 有界语义：超过 200 条的旧消息已丢弃（deque 语义），文档写明"最近 200 条"。
- `resume`/`load_session` 之外（如 `adopt`）不回放。

### T4 测试 + 文档
- `tests/test_session.py`：字段往返、坏条目宽容。
- `tests/test_conversations.py`（**本波归你**）：emit → save → 新 conversation load → 重放事件被订阅者按原顺序收到、kind/data/block_id/sealed 保真、捕获不重复（二次 save 后列表长度不变）、200 上限截断。
- `docs/plugins.md`？——**禁触**（04 号本波拥有它）。改写限制段落：落点改为 `docs/ARCHITECTURE.md`（本波归你）：会话持久化小节补 plugin_messages 捕获/回放/上限；`docs/testing.md` 若有涉及回放的测试惯例可补一句（可改）。

## 验收

1. `uv run pytest -q` 全绿，独立退出码。
2. 端到端证据：测试内 emit → save → load → 断言重放；列出测试名。
3. `git diff <merge-base> --stat` ⊆ 写入范围。

## 禁触清单

- `mocode/cli/**`（渲染端已就绪，零改动）、`mocode/core/**`（events.py 零改动——PluginMessage 已有序列化）、`mocode/host/plugin/**`（host.py 的 adopt/materialize 不动——回放走 Conversation，不复用 _pending_session 机制）、`mocode/testing/**`、`providers/**`、`examples/**`、`pyproject.toml`。
- `docs/plugins.md`（04 领地——下波 07 的审批配方会与你的 ARCHITECTURE 改动并行无冲突）。
- tests：`tests/test_session.py`、`tests/test_conversations.py` 之外全禁（04/05 领地）。
- spec 文件。

## 最终报告格式

工单状态表（T1-T4）｜commit 清单｜自测真实结论（pytest + 端到端测试名）｜偏差与取舍｜未决问题。阻塞即停。
