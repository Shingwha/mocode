"""SubAgent — 统一的轻量子 Agent 运行器

提供通用的 agent loop，支持从主 Agent 便捷派生或独立构造。
复用主 Agent 的消息序列化格式和 tool call 解析逻辑，
消息列表完全隔离，不影响调用方的对话状态。
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .event import EventType
from .interrupt import Interrupted
from .permission import CheckOutcome
from .tool import ToolRegistry

if TYPE_CHECKING:
    from .event import EventBus
    from .interrupt import CancellationToken
    from .permission import PermissionChecker
    from .provider import Provider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubAgentConfig:
    """SubAgent 配置"""

    system_prompt: str
    tool_names: list[str] | None = None  # None = 全部工具, list = 过滤子集
    max_tool_calls: int = 50
    max_tokens: int = 4096
    bypass_permissions: bool = True  # 默认跳过权限检查
    tool_timeout: int | None = 240  # 默认 240s
    tool_result_limit: int = 0  # 0 = 不截断


@dataclass
class SubAgentResult:
    """SubAgent 运行结果"""

    content: str = ""  # 最终文本回复
    tool_calls_made: int = 0
    messages: list[dict] = field(default_factory=list)  # 完整对话记录
    had_error: bool = False


class SubAgent:
    """统一的轻量子 Agent 运行器

    核心循环复用主 Agent 的消息序列化格式和 tool call 解析逻辑。
    所有可选功能（event_bus / cancel_token / permission_checker）为 None 时跳过。

    v0.2 改进：支持 provider_getter 回调，动态获取最新 provider，
    确保模型切换后 SubAgent 使用新模型。
    """

    def __init__(
        self,
        provider: "Provider | Callable[[], Provider]",
        tools: ToolRegistry,
        config: SubAgentConfig,
        event_bus: "EventBus | None" = None,
        cancel_token: "CancellationToken | None" = None,
        permission_checker: "PermissionChecker | None" = None,
    ):
        # 支持直接传入 provider 或 provider_getter 回调
        if callable(provider) and not hasattr(provider, "call"):
            self._provider_getter = provider
        else:
            self._provider_getter = lambda: provider
        self._tools = tools
        self._config = config
        self._event_bus = event_bus
        self._cancel_token = cancel_token
        self._permission_checker = permission_checker

        # 预计算 tool schemas
        self._tool_schemas = self._compute_tool_schemas()

    @property
    def _provider(self) -> "Provider":
        """动态获取当前 provider"""
        return self._provider_getter()

    def _compute_tool_schemas(self) -> list[dict]:
        """按 tool_names 过滤并预计算 tool schemas"""
        if self._config.tool_names is None:
            return self._tools.all_schemas()
        schemas = []
        for name in self._config.tool_names:
            tool = self._tools.get(name)
            if tool:
                schemas.append(tool.to_schema())
        return schemas

    # ---- Event helpers ----

    _call_seq = 0

    def _next_call_id(self) -> str:
        SubAgent._call_seq += 1
        return f"sub_call_{SubAgent._call_seq}"

    def _emit(self, event_type: EventType, data: dict) -> None:
        if self._event_bus:
            self._event_bus.emit(event_type, data)

    # ---- Tool execution ----

    def _apply_result_limit(self, result: str) -> str:
        if self._config.tool_result_limit > 0:
            from .tools.utils import truncate_result

            return truncate_result(result, self._config.tool_result_limit)
        return result

    async def _execute_tool(self, name: str, args: dict) -> str:
        """执行单个工具：权限检查 → 执行 → 超时 → 截断 → 事件"""
        call_id = self._next_call_id()

        # 1. Permission check (unless bypassed)
        if not self._config.bypass_permissions and self._permission_checker:
            result = await self._permission_checker.check(name, args)
            if result.outcome == CheckOutcome.DENY:
                msg = f"Permission denied: {result.reason}"
                self._emit(
                    EventType.TOOL_START,
                    {"name": name, "args": args, "call_id": call_id},
                )
                self._emit(
                    EventType.TOOL_COMPLETE,
                    {"name": name, "result": msg, "call_id": call_id},
                )
                raise Interrupted()
            elif result.outcome == CheckOutcome.USER_INPUT:
                self._emit(
                    EventType.TOOL_START,
                    {"name": name, "args": args, "call_id": call_id},
                )
                self._emit(
                    EventType.TOOL_COMPLETE,
                    {"name": name, "result": result.user_input, "call_id": call_id},
                )
                return result.user_input or ""

        # 2. Execute tool
        self._emit(
            EventType.TOOL_START, {"name": name, "args": args, "call_id": call_id}
        )

        tool = self._tools.get(name)
        if tool is not None and tool.is_async:
            coro = self._tools.run_async(name, args)
        else:
            coro = asyncio.to_thread(self._tools.run, name, args)
        if self._cancel_token:
            coro = self._cancel_token.cancellable(coro)

        timeout = self._config.tool_timeout
        try:
            if timeout is not None:
                result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                result = await coro
        except asyncio.TimeoutError:
            msg = f"error: tool '{name}' timed out after {timeout}s"
            self._emit(
                EventType.TOOL_COMPLETE,
                {"name": name, "result": msg, "call_id": call_id},
            )
            return msg

        # 3. Truncate
        result = self._apply_result_limit(result)

        self._emit(
            EventType.TOOL_COMPLETE,
            {"name": name, "result": result, "call_id": call_id},
        )
        return result

    async def _execute_tools_parallel(self, tool_calls: list) -> tuple[list[dict], int]:
        """并行执行多个 tool_calls，返回 (results, tool_calls_made_count)"""
        # Fast path: single tool
        if len(tool_calls) == 1:
            tc = tool_calls[0]
            if self._cancel_token:
                self._cancel_token.check()
            try:
                tool_args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                tool_args = {}
            result = await self._execute_tool(tc.name, tool_args)
            return ([{"role": "tool", "tool_call_id": tc.id, "content": result}], 1)

        # Parse all arguments
        if self._cancel_token:
            self._cancel_token.check()
        parsed = []
        for tc in tool_calls:
            try:
                tool_args = json.loads(tc.arguments) if tc.arguments else {}
            except json.JSONDecodeError:
                tool_args = {}
            parsed.append((tc, tool_args))

        async def _run_one(tc, args):
            result = await self._execute_tool(tc.name, args)
            return {"role": "tool", "tool_call_id": tc.id, "content": result}

        raw_results = await asyncio.gather(
            *[_run_one(tc, args) for tc, args in parsed],
            return_exceptions=True,
        )

        tool_results = []
        tool_calls_made = 0

        for i, raw in enumerate(raw_results):
            tc = tool_calls[i]
            if isinstance(raw, BaseException):
                if isinstance(raw, Interrupted):
                    raise raw
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"error: {raw}",
                    }
                )
            else:
                tool_results.append(raw)
            tool_calls_made += 1

        return (tool_results, tool_calls_made)

    # ---- Core loop ----

    async def run(self, user_prompt: str) -> SubAgentResult:
        """从用户提示运行子 Agent"""
        messages = [{"role": "user", "content": user_prompt}]
        return await self.run_messages(messages)

    async def run_messages(self, messages: list[dict]) -> SubAgentResult:
        """核心 agent loop — 消息列表完全隔离"""
        messages = list(messages)  # 浅拷贝，隔离
        tool_calls_made = 0
        had_error = False
        final_content = ""

        for _ in range(self._config.max_tool_calls):
            # Cancel check
            if self._cancel_token:
                self._cancel_token.check()

            # Call provider
            try:
                call_coro = self._provider.call(
                    messages=messages,
                    system=self._config.system_prompt,
                    tools=self._tool_schemas,
                    max_tokens=self._config.max_tokens,
                )
                if self._cancel_token:
                    response = await self._cancel_token.cancellable(call_coro)
                else:
                    response = await call_coro
            except Interrupted:
                raise
            except Exception as e:
                logger.error(f"SubAgent LLM call failed: {e}")
                had_error = True
                break

            # Track content
            if response.content:
                final_content = response.content

            # Build assistant message
            assistant_msg: dict = {
                "role": "assistant",
                "content": response.content or "",
            }
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": tc.arguments},
                    }
                    for tc in response.tool_calls
                ]
            messages.append(assistant_msg)

            # No tool calls → LLM is done
            if not response.tool_calls:
                break

            # Execute tool calls (parallel)
            tool_results, count = await self._execute_tools_parallel(
                response.tool_calls
            )
            tool_calls_made += count
            messages.extend(tool_results)

            if response.finish_reason == "stop":
                break

        return SubAgentResult(
            content=final_content,
            tool_calls_made=tool_calls_made,
            messages=messages,
            had_error=had_error,
        )
