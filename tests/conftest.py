"""Shared fixtures: one config shape, one runtime, one way to read a terminal.

Nothing here touches the real ``~/.mocode`` — every runtime's home lives inside
the test's ``tmp_path``. Factories that need a directory are fixtures
(``make_mc``, ``wired``, ``plugin_host``); the rest are plain helpers imported
from this module (``from .conftest import write_plugin``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

import pytest

from mocode.core.agent import AgentConfig, AgentLoop
from mocode.core.events import Notice
from mocode.core.hook import HookRunner
from mocode.core.tool import Tool, ToolRegistry
from mocode.host.command import Command, CommandContext, CommandRegistry, CommandResult
from mocode.host.config import Config, ModelEntry, ProviderEntry
from mocode.host.plugin.context import BuildContext
from mocode.host.plugin.host import PluginHost, load_plugins
from mocode.host.plugin.loader import HOST_NAMESPACE
from mocode.host.runtime import MoCode
from mocode.testing import MockProvider, say

if TYPE_CHECKING:
    from mocode.core.hook import AgentHook
    from mocode.host.conversation import Conversation

#: Every escape a terminal draw can emit — colour, cursor moves, clears.
ESCAPES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """What a terminal draw looks like as plain text."""
    return ESCAPES.sub("", text)


def make_config() -> Config:
    """A config with two providers and no reachable endpoint.

    Tests replace the conversation's provider with a double, so the values here
    only have to be well-formed — and never real.
    """
    return Config(
        provider="test",
        model="test-model",
        providers={
            "test": ProviderEntry(
                name="Test",
                api_key="sk-test",
                base_url="http://localhost",
                models=[ModelEntry(id="test-model"), ModelEntry(id="other-model")],
            ),
            "second": ProviderEntry(
                name="Second",
                api_key="sk-other",
                base_url="http://localhost",
                models=[ModelEntry(id="second-model")],
            ),
        },
    )


@pytest.fixture
def make_mc(tmp_path: Path):
    """A runtime factory, for tests that need a config of their own.

    *overrides* are keyword arguments passed straight to :class:`MoCode` (its
    ``freeze_interface`` decision, say); everything else is the shape every
    test shares: a home inside ``tmp_path`` and no third-party plugin dirs.
    """

    def _make(
        config: Config | None = None,
        *,
        plugin_dirs: list[Path] | None = None,
        **overrides,
    ) -> MoCode:
        return MoCode(
            config=config if config is not None else make_config(),
            home=tmp_path / "home",
            plugin_dirs=plugin_dirs if plugin_dirs is not None else [],
            **overrides,
        )

    return _make


@pytest.fixture
def mc(make_mc) -> MoCode:
    """A runtime whose sessions and plugin loading live inside the test."""
    return make_mc()


# ── a conversation on a scripted model ──────────────────────


def script(*entries) -> list:
    """Script entries the way a test writes them.

    A string is plain text (``say``), anything else — a :class:`Response` or
    an exception to raise — passes through untouched.
    """
    return [say(entry) if isinstance(entry, str) else entry for entry in entries]


def wire(conversation: "Conversation", *entries, chunk_size: int = 0) -> MockProvider:
    """Swap a MockProvider into an existing conversation — a CLIApp's too."""
    provider = MockProvider(script(*entries), chunk_size=chunk_size)
    conversation.agent.provider = provider
    return provider


@pytest.fixture
def wired(mc: MoCode, tmp_path: Path):
    """A conversation with a MockProvider swapped in — (conversation, provider).

    Entries are script items (:func:`script`); ``cwd`` names the project the
    conversation works in, and ``mc=`` swaps in a runtime of the test's own
    (an unpinned one, say).
    """
    default_runtime = mc

    def _wired(*entries, cwd: Path | None = None, mc: MoCode | None = None):
        target = mc if mc is not None else default_runtime
        conversation = target.new_conversation(
            cwd=cwd if cwd is not None else tmp_path
        )
        return conversation, wire(conversation, *entries)

    return _wired


def project(tmp_path: Path, name: str) -> Path:
    """A project directory inside the test's temporary tree."""
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# ── a plugin directory ──────────────────────────────────────


def write_plugin(
    root: Path,
    name: str,
    code: str = "",
    *,
    manifest: dict | None = None,
    raw_manifest: str | None = None,
    skills: list[str] | None = None,
    package: dict[str, str] | None = None,
    cli: str = "",
    cli_package: dict[str, str] | None = None,
) -> Path:
    """Create ``root/<name>/`` in the Agent Plugins layout.

    *code* writes the single-file entry ``mocode/plugin.py``; *package* writes
    the package entry ``mocode/plugin/<file>`` instead — the multi-file form.
    *cli* / *cli_package* write the terminal's own namespace, so one directory
    can carry both surfaces. *skills* adds ``skills/<name>/SKILL.md`` stubs.
    """
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    if raw_manifest is not None:
        (plugin_dir / "plugin.json").write_text(raw_manifest, encoding="utf-8")
    else:
        data = {"$schema": "https://agent-plugins.org/schemas/v1.json", "name": name}
        data.update(manifest or {})
        (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")

    if code:
        module = plugin_dir / HOST_NAMESPACE / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(code), encoding="utf-8")

    for filename, text in (package or {}).items():
        module = plugin_dir / HOST_NAMESPACE / "plugin" / filename
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(text), encoding="utf-8")

    for skill in skills or []:
        skill_dir = plugin_dir / "skills" / skill
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: from a plugin\n---\n\nDo {skill}.",
            encoding="utf-8",
        )

    if cli:
        module = plugin_dir / "mocode.cli" / "plugin.py"
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(cli), encoding="utf-8")

    for filename, text in (cli_package or {}).items():
        module = plugin_dir / "mocode.cli" / "plugin" / filename
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(textwrap.dedent(text), encoding="utf-8")

    return plugin_dir


def skill_dir(base: Path, name: str, description: str, body: str = "") -> Path:
    """A standalone skill directory: SKILL.md with frontmatter, no plugin."""
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )
    return path


# ── a host, without the runtime ─────────────────────────────


@pytest.fixture
def plugin_host(tmp_path: Path):
    """The runtime's own assembly sequence, without the runtime.

    ``BuildContext`` → ``PluginHost`` → ``build_all()`` → ``assemble()`` — the
    agent arrives with a MockProvider under it. ``load=`` discovers plugins
    from directories the way a project does; ``build=False`` / ``assemble=False``
    stop at that stage, for a test that is about the stage itself.
    """

    def _host(
        *,
        cwd: Path | None = None,
        config: Config | None = None,
        config_kwargs: dict | None = None,
        plugins: Sequence | None = None,
        sources: Sequence[str] | None = None,
        tools: ToolRegistry | None = None,
        hook=None,
        responses=None,
        load: list[Path] | None = None,
        build: bool = True,
        assemble: bool = True,
    ) -> PluginHost:
        ctx = BuildContext(
            home=tmp_path / "home",
            cwd=cwd if cwd is not None else tmp_path,
            config=config
            if config is not None
            else Config(provider="p", model="m", **(config_kwargs or {})),
        )
        if tools is not None:
            ctx.tools = tools
        if hook is not None:
            ctx.hooks.append(hook(ctx))
        loaded = (
            load_plugins(plugin_dirs=list(load), config=ctx.config)
            if load is not None
            else None
        )
        if loaded is not None:
            ctx.plugin_sources = list(loaded.sources)
        if plugins is not None:
            members = list(plugins)
        elif loaded is not None:
            members = loaded.plugins
        else:
            members = []
        host = PluginHost(
            ctx,
            members,
            sources=sources
            if sources is not None
            else (list(loaded.tool_sources) if loaded is not None else None),
        )
        if build:
            host.build_all()
        if assemble:
            host.assemble(
                provider=MockProvider(
                    responses if responses is not None else [say("done")]
                ),
                config=AgentConfig(),
            )
        return host

    return _host


# ── commands ────────────────────────────────────────────────


async def run_command(
    command: Command,
    conversation: "Conversation",
    *,
    args: str = "",
    commands: CommandRegistry | None = None,
) -> tuple[CommandResult, list]:
    """Run a command and collect what it published — what the user saw."""
    reader = conversation.subscribe()
    result = await command.handler(
        CommandContext(conversation=conversation, args=args, commands=commands)
    )
    seen = []
    while (event := reader.take()) is not None:
        seen.append(event)
    return result, seen


def notices(events: list) -> list[Notice]:
    """The Notice events among *events* — what the frontend was told."""
    return [e for e in events if isinstance(e, Notice)]


def updates(conversation: "Conversation") -> list[dict]:
    """The cache-protect announcements in a conversation's history."""
    return [
        m
        for m in conversation.messages
        if m.get("role") == "user" and "[context update" in str(m.get("content", ""))
    ]


# ── bare agents ─────────────────────────────────────────────


def echo_tool(name: str = "echo", **kwargs) -> Tool:
    """A tool that answers ``echo:<value>`` — the usual stand-in."""
    return Tool(
        name=name,
        description="echo",
        schema={
            "type": "object",
            "properties": {"value": {"type": "string", "description": "v"}},
            "required": ["value"],
        },
        func=lambda args: f"echo:{args['value']}",
        **kwargs,
    )


def make_agent(
    *tools: Tool,
    hooks: "list[AgentHook] | HookRunner | None" = None,
    config: AgentConfig | None = None,
    provider: MockProvider | None = None,
    **kwargs,
) -> AgentLoop:
    """An AgentLoop with a registry built from *tools* and a scripted model.

    *hooks* may be the raw list a hook-producing factory returns; anything a
    caller states outright (``system_prompt``, ``model``) wins over the
    defaults.
    """
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    kwargs.setdefault("provider", provider or MockProvider())
    kwargs.setdefault("system_prompt", "sys")
    if "tools" not in kwargs:
        kwargs["tools"] = registry
    if "hooks" not in kwargs:
        kwargs["hooks"] = HookRunner(hooks) if hooks else HookRunner()
    if "config" not in kwargs:
        kwargs["config"] = config or AgentConfig()
    return AgentLoop(**kwargs)


# ── 时序地基（W0 交付：受批等待与假时钟，W1 起各波次统一使用） ──

#: conftest 导入期捕获的 ``asyncio.sleep`` 真身。守卫（recording 模式）
#: 在每个测试里 patch ``asyncio.sleep``，但 patch 不到这里保存的引用——
#: ``settle()`` 因此天然豁免，不进 BARE SLEEPS 清单。
_REAL_ASYNCIO_SLEEP = asyncio.sleep

#: 同理捕获的 ``time.sleep`` 真身，守卫对测试进程内的同步裸睡同样只记真睡。
_REAL_TIME_SLEEP = time.sleep

#: ``real_time()`` 的嵌套深度：>0 时守卫只睡不记。
_real_time_depth = 0


async def settle(seconds: float = 0.01) -> None:
    """受批短睡：等待是"被测行为本身"时的唯一合法入睡入口。

    只用于两类场景——真子进程观察窗（等 fake server 写 pidfile、等端口
    就绪）与看门狗时限（等一个超时真的到点）。它不是同步原语：等条件
    成立请用 :func:`wait_until`，等事件请用 ``asyncio.Event``。这里入睡
    走 import 期捕获的真身，守卫记录不到它。
    """
    await _REAL_ASYNCIO_SLEEP(seconds)


async def wait_until(
    predicate, *, bound: float = 5.0, step: float = 0.01, what: str = ""
) -> bool:
    """条件轮询：*predicate* 一旦成真即返回 ``True``，超时抛 ``AssertionError``。

    取代各处手写的 ``for ...: await asyncio.sleep(POLL)`` 轮询。超时消息
    带 *what* 与实际耗时，红了直接可读。轮询步长走 :func:`settle`，不
    计入裸睡清单。*bound* 是墙钟秒数上限，*step* 是每次轮询的间隔。
    """
    started = time.monotonic()
    while not predicate():
        elapsed = time.monotonic() - started
        if elapsed >= bound:
            raise AssertionError(
                f"wait_until 超时：{what or 'condition'}"
                f"（耗时 {elapsed:.2f}s > bound {bound:.2f}s）"
            )
        await settle(step)
    return True


class FakeClock:
    """可手推的假单调时钟：``monotonic()`` 读 ``now``，``now`` 由测试推进。

    对齐 ``test_retry.py`` 的 ``_Clock`` 与 ``test_agent_loop.py`` 的
    ``Clock``/``FastClock`` 用法——把模块里的 ``time`` 整体换成它
    （``monkeypatch.setattr(module, "time", clock)``）之后，被测代码的
    每次 ``time.monotonic()`` 都读到这里的 ``now``。用 :func:`advance`
    推进时间，断言由时钟读数驱动，不用真睡。
    """

    def __init__(self, start: float = 0.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now


def advance(clock: FakeClock, dt: float) -> float:
    """把 *clock* 的 ``now`` 向前推 *dt* 秒，返回推进后的读数。

    一次失败的尝试消耗多少墙钟，测试里就是 ``advance(clock, burn)``——
    退避、截止这些逻辑全部在假时间上证明。
    """
    clock.now += dt
    return clock.now


@contextlib.contextmanager
def real_time():
    """裸睡赦免区：块内 ``time.sleep``/``asyncio.sleep`` 只睡不记，退出即恢复。

    这是逃生舱，普通测试不该见到它——存在即说明等待方式该被 W1/W2 改造。
    只用于 :func:`settle` 覆盖不了的"必须真实阻塞一段墙钟"的场景（罕见）。
    可嵌套，深度归零后守卫恢复记录。
    """
    global _real_time_depth
    _real_time_depth += 1
    try:
        yield
    finally:
        _real_time_depth -= 1


# ── 裸睡守卫（W2 起硬失败：tests/ 下的裸睡直接报错；产品侧只记录） ──

#: 裸睡清单：``(nodeid, 调用点 文件:行号, 秒数)``，会话末统一输出。
_BARE_SLEEPS: list[tuple[str, str, float]] = []

#: 守卫报告的调用点路径，统一相对仓库根目录，便于 grep 与聚合。
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _record_bare_sleep(nodeid: str, seconds: float) -> None:
    """记一条裸睡；调用点取守卫包装器之外真正调用 sleep 的那一帧。

    **硬失败**（W2 起）：调用点在 ``tests/`` 下的裸睡直接抛
    ``RuntimeError``，消息指向替代 API。产品代码里的 sleep
    （``mocode/**``）与 codemode 脚本内的 sleep（``<codemode>``）只
    记录不失败——那是被测行为本身，不是测试脆弱性（总纲不变量 1
    的调用点归属规则）。
    """
    frame = sys._getframe(2)  # 0=本函数，1=守卫包装器，2=真正的调用者
    filename = frame.f_code.co_filename
    try:
        filename = os.path.relpath(filename, _REPO_ROOT)
    except ValueError:  # 跨盘符（Windows）时保留绝对路径
        pass
    parts = Path(filename).parts
    if parts and parts[0] == "tests":
        raise RuntimeError(
            f"裸睡禁止：{nodeid} 在 {filename}:{frame.f_lineno} "
            f"入睡 {seconds}s。等条件用 wait_until(predicate, bound=…)；"
            "等事件用 asyncio.Event；等待即被测行为用 settle()；"
            "确有真实阻塞理由时用 real_time() 块。"
        )
    _BARE_SLEEPS.append((nodeid, f"{filename}:{frame.f_lineno}", seconds))


@pytest.fixture(autouse=True)
def _sleep_guard(monkeypatch, request):
    """把 ``time.sleep`` / ``asyncio.sleep`` 换成记录版（**硬失败模式**）。

    调用点在 ``tests/`` 下的裸睡直接抛 ``RuntimeError``，消息指向
    替代 API（:func:`wait_until`、事件门控、:func:`settle`、
    :func:`real_time`）；``mocode/**`` 产品代码与被 ``<codemode>``
    沙箱脚本内的 sleep 只记录进 :data:`_BARE_SLEEPS`，会话末由
    ``pytest_terminal_summary`` 输出清单——那是产品行为与被测脚本
    行为，不是测试脆弱性。豁免：:func:`settle` 走 import 期捕获的
    真身、守卫 patch 不到它；:func:`real_time` 块内只睡不记。真子
    进程（MCP fake server）跑在别的解释器里，天然豁免。patch 经
    ``monkeypatch`` 完成，测试结束即恢复，错误隔离不渗漏。
    """
    nodeid = request.node.nodeid

    def recording_time_sleep(seconds):
        if _real_time_depth == 0:
            _record_bare_sleep(nodeid, seconds)
        return _REAL_TIME_SLEEP(seconds)

    async def recording_asyncio_sleep(delay, result=None):
        if _real_time_depth == 0:
            _record_bare_sleep(nodeid, delay)
        return await _REAL_ASYNCIO_SLEEP(delay, result)

    monkeypatch.setattr(time, "sleep", recording_time_sleep)
    monkeypatch.setattr(asyncio, "sleep", recording_asyncio_sleep)
    yield


def pytest_terminal_summary(terminalreporter) -> None:
    """会话末输出产品侧睡眠清单：总条数、命中测试数、按文件分布 top。

    这些是被测行为里的 sleep（产品轮询、退避、脚本占位），按总纲
    不变量 1 的归属规则只记录不失败；tests/ 侧的裸睡已在发生时
    直接失败，不会出现在这里。
    """
    if not _BARE_SLEEPS:
        return
    per_file: dict[str, int] = {}
    for _, caller, _ in _BARE_SLEEPS:
        path = caller.rsplit(":", 1)[0]
        per_file[path] = per_file.get(path, 0) + 1
    tests = {nodeid for nodeid, _, _ in _BARE_SLEEPS}
    terminalreporter.write_sep(
        "=",
        f"BARE SLEEPS (product-side only, recorded): "
        f"{len(_BARE_SLEEPS)} calls in {len(tests)} tests",
    )
    for path, count in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0]))[:15]:
        terminalreporter.write_line(f"  {count:5d}  {path}")
