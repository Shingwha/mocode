"""Agent — LLM 对话引擎

v0.2 关键改进：
- 接收 ToolRegistry 实例（非全局）
- 使用 Response DTO（非 OpenAI SDK 类型）
- json 在文件顶部导入
- compact 通过构造函数传入
"""

import asyncio
import json
import logging
from pathlib import Path
import base64
from typing import TYPE_CHECKING

from .event import EventType, EventBus
from .interrupt import CancellationToken, Interrupted, InterruptReason
from .permission import CheckOutcome, PermissionChecker
from .provider import Response, Usage
from openai import BadRequestError
from .tool import ToolRegistry

if TYPE_CHECKING:
    from .compact import CompactManager
    from .config import Config


class Agent:
    """LLM 对话引擎 — 接收全部依赖，无 post-construction mutation"""

    def __init__(
        self,
        provider,
        system_prompt: str,
        tools: ToolRegistry,
        event_bus: EventBus,
        cancel_token: CancellationToken,
        permission_checker: PermissionChecker,
        config: "Config",
        compact: "CompactManager | None" = None,
    ):
        self.provider = provider
        self.system_prompt = system_prompt
        self._tools = tools
        self.event_bus = event_bus
        self.cancel_token = cancel_token
        self.permission_checker = permission_checker
        self.config = config
        self._compact = compact
        self._messages: list[dict] = []
        self._last_usage: Usage | None = None
        self.conversation_id: str | None = None
        self._tools.state["messages"] = self._messages

    @property
    def messages(self) -> list[dict]:
        return self._messages

    @messages.setter
    def messages(self, value: list[dict]) -> None:
        self._messages = value
        self._tools.state["messages"] = value

    # ---- Event helpers ----

    _call_seq = 0

    def _next_call_id(self) -> str:
        Agent._call_seq += 1
        return f"call_{Agent._call_seq}"

    def _emit_tool_start(
        self, tool_name: str, tool_args: dict, call_id: str | None = None
    ) -> None:
        self.event_bus.emit(
            EventType.TOOL_START,
            {
                "name": tool_name,
                "args": tool_args,
                "call_id": call_id,
                "conversation_id": self.conversation_id,
            },
        )

    def _emit_tool_complete(
        self, tool_name: str, result: str, call_id: str | None = None
    ) -> None:
        self.event_bus.emit(
            EventType.TOOL_COMPLETE,
            {
                "name": tool_name,
                "result": result,
                "call_id": call_id,
                "conversation_id": self.conversation_id,
            },
        )

    def _deny_tool(
        self, tool_name: str, tool_args: dict, reason: str, call_id: str | None = None
    ) -> tuple[str, bool]:
        self._emit_tool_start(tool_name, tool_args, call_id)
        msg = f"User {reason} this operation"
        self._emit_tool_complete(tool_name, msg, call_id)
        return (msg, True)

    # ---- Chat loop ----

    async def chat(self, user_input: str, media: list[str] | None = None) -> str:
        """一次对话轮次"""
        self.cancel_token.reset()

        # Auto-compact
        if self._compact and self._compact.should_compact(self.provider.model):
            self.messages = await self._compact.compact(
                self.messages, self.provider.model
            )

        if media:
            content = self._build_user_content(user_input, media)
        else:
            content = user_input

        self.messages.append({"role": "user", "content": content})
        self.event_bus.emit(
            EventType.MESSAGE_ADDED,
            {
                "role": "user",
                "content": user_input,
                "conversation_id": self.conversation_id,
            },
        )

        try:
            return await self._agent_loop()
        except Interrupted as exc:
            self.cancel_token.reset()
            self.event_bus.emit(EventType.INTERRUPTED, {"reason": exc.reason.name})
            return "[interrupted]"

    async def _agent_loop(self) -> str:
        final_response = ""
        while True:
            self.cancel_token.check()

            try:
                response = await self.cancel_token.cancellable(
                    self.provider.call(
                        self.messages,
                        self.system_prompt,
                        self._tools.all_schemas(),
                        self.config.max_tokens,
                    )
                )
            except BadRequestError:
                from .provider import OpenAIProvider

                stripped = OpenAIProvider.strip_image_content(self.messages)
                if stripped is not None:
                    logging.getLogger(__name__).warning(
                        "BadRequestError with image content, retrying without images"
                    )
                    response = await self.cancel_token.cancellable(
                        self.provider.call(
                            stripped,
                            self.system_prompt,
                            self._tools.all_schemas(),
                            self.config.max_tokens,
                        )
                    )
                    OpenAIProvider.strip_image_content_inplace(self.messages)
                else:
                    raise

            # Track token usage
            if response.usage:
                self._last_usage = response.usage
                if self._compact:
                    self._compact.update_usage(response.usage.prompt_tokens)

            tool_results = []

            if response.reasoning_content:
                self.event_bus.emit(
                    EventType.REASONING,
                    {
                        "content": response.reasoning_content,
                        "conversation_id": self.conversation_id,
                    },
                )

            if response.content:
                final_response = response.content
                self.event_bus.emit(
                    EventType.TEXT_COMPLETE,
                    {
                        "content": response.content,
                        "conversation_id": self.conversation_id,
                    },
                )

            if response.tool_calls:
                _interrupted_tc_id: str | None = None
                try:
                    (
                        tool_results,
                        _interrupted_tc_id,
                    ) = await self._run_tool_calls_parallel(response.tool_calls)
                    if _interrupted_tc_id:
                        raise Interrupted(InterruptReason.PERMISSION_DENIED)
                finally:
                    # 为被中断的工具发射合成 TOOL_COMPLETE
                    if _interrupted_tc_id is not None:
                        for tc in response.tool_calls:
                            if tc.id == _interrupted_tc_id:
                                self._emit_tool_complete(tc.name, "[interrupted]")
                                break

                    # 修复消息：只保留有 result 的 tool_calls，合成缺失的 result
                    from .message_utils import repair_interrupted_state

                    all_tc_dicts = [
                        {
                            "id": t.id,
                            "type": "function",
                            "function": {"name": t.name, "arguments": t.arguments},
                        }
                        for t in (response.tool_calls or [])
                    ]
                    trimmed_calls, synthetic_results = repair_interrupted_state(
                        all_tc_dicts, tool_results, _interrupted_tc_id
                    )

                    assistant_msg: dict = {
                        "role": "assistant",
                        "content": response.content or "",
                    }
                    if response.reasoning_content:
                        assistant_msg["reasoning_content"] = response.reasoning_content
                    if trimmed_calls:
                        assistant_msg["tool_calls"] = trimmed_calls
                    self.messages.append(assistant_msg)
                    if tool_results:
                        self.messages.extend(tool_results)
                    if synthetic_results:
                        self.messages.extend(synthetic_results)
            else:
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": response.content or "",
                }
                if response.reasoning_content:
                    assistant_msg["reasoning_content"] = response.reasoning_content
                self.messages.append(assistant_msg)
                break

            if not tool_results:
                break

        return final_response

    @staticmethod
    def _build_user_content(text: str, media: list[str]) -> list[dict] | str:
        """Build multimodal user content with images and file markers."""
        parts: list[dict] = []
        has_images = False

        for path_str in media:
            p = Path(path_str)
            if not p.exists():
                continue
            # Try as image
            IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
            ext = p.suffix.lower()
            if ext in IMAGE_EXTS:
                try:
                    data = p.read_bytes()
                    b64 = base64.b64encode(data).decode()
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{ext[1:]};base64,{b64}"},
                            "_meta": {"path": path_str},
                        }
                    )
                    parts.append(
                        {
                            "type": "text",
                            "text": f"[Image: {p.name} at {path_str}]",
                        }
                    )
                    has_images = True
                    continue
                except Exception:
                    pass
            # Non-image files: do NOT embed content, just inform
            parts.append(
                {
                    "type": "text",
                    "text": (
                        f"[File received: {p.name} at {path_str}]\n\n"
                        f"IMPORTANT: The user has sent a file. "
                        f"DO NOT read, analyze, or process this file automatically. "
                        f"WAIT for the user's instructions. "
                        f"If the user's message does not specify what they want you to do with this file, "
                        f"ask them directly what task they would like you to perform."
                    ),
                }
            )

        if not has_images and not parts:
            return text

        if text:
            parts.append({"type": "text", "text": text})

        return parts

    # ---- Tool execution pipeline ----

    def _apply_result_limit(self, result: str) -> str:
        if self.config.tool_result_limit > 0:
            from .tools.utils import truncate_result

            return truncate_result(result, self.config.tool_result_limit)
        return result

    async def _run_tool_async(
        self, tool_name: str, tool_args: dict
    ) -> tuple[str, bool]:
        """工具执行：权限检查 → 执行 → 截断"""
        call_id = self._next_call_id()

        # 1. Permission check
        if self.permission_checker:
            result = await self.permission_checker.check(tool_name, tool_args)
            if result.outcome == CheckOutcome.DENY:
                return self._deny_tool(tool_name, tool_args, result.reason, call_id)
            elif result.outcome == CheckOutcome.USER_INPUT:
                self._emit_tool_start(tool_name, tool_args, call_id)
                self._emit_tool_complete(tool_name, result.user_input, call_id)
                return (result.user_input, False)

        # 2. Execute tool
        self._emit_tool_start(tool_name, tool_args, call_id)

        timeout = self.config.tool_timeout
        tool = self._tools.get(tool_name)

        try:
            if tool is not None and tool.is_async:
                coro = self.cancel_token.cancellable(
                    self._tools.run_async(tool_name, tool_args)
                )
            else:
                coro = self.cancel_token.cancellable(
                    asyncio.to_thread(self._tools.run, tool_name, tool_args)
                )
            result = await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            self._emit_tool_complete(
                tool_name, f"Tool timed out after {timeout}s", call_id
            )
            return (f"error: tool '{tool_name}' timed out after {timeout}s", False)

        # 3. Truncate and emit
        result = self._apply_result_limit(result)
        self._emit_tool_complete(tool_name, result, call_id)
        return (result, False)

    async def _run_tool_calls_parallel(
        self, tool_calls: list
    ) -> tuple[list[dict], str | None]:
        """并行执行多个 tool_calls，返回 (results, interrupted_tc_id | None)"""
        # Fast path: single tool
        if len(tool_calls) == 1:
            tc = tool_calls[0]
            self.cancel_token.check()
            tool_args = json.loads(tc.arguments)
            try:
                result, denied = await self._run_tool_async(tc.name, tool_args)
            except Interrupted:
                return ([], tc.id)
            return (
                [{"role": "tool", "tool_call_id": tc.id, "content": result}],
                tc.id if denied else None,
            )

        # Parse all arguments first (cheap, sequential)
        self.cancel_token.check()
        parsed = [(tc, json.loads(tc.arguments)) for tc in tool_calls]

        # Per-tool wrapper that captures Interrupted instead of raising
        _INTERRUPTED = object()

        async def _run_one(tc, args):
            try:
                result, denied = await self._run_tool_async(tc.name, args)
                return (
                    {"role": "tool", "tool_call_id": tc.id, "content": result},
                    denied,
                )
            except Interrupted:
                return _INTERRUPTED

        raw_results = await asyncio.gather(
            *[_run_one(tc, args) for tc, args in parsed],
            return_exceptions=True,
        )

        tool_results = []
        interrupted_tc_id = None

        for i, raw in enumerate(raw_results):
            tc = tool_calls[i]
            if raw is _INTERRUPTED:
                interrupted_tc_id = tc.id
            elif isinstance(raw, BaseException):
                tool_results.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": f"error: {raw}",
                    }
                )
            else:
                result_dict, denied = raw
                tool_results.append(result_dict)
                if denied and interrupted_tc_id is None:
                    interrupted_tc_id = tc.id

        return (tool_results, interrupted_tc_id)

    # ---- Mutation methods ----

    def update_provider(self, provider) -> None:
        """切换 provider"""
        self.provider = provider

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        self.system_prompt = prompt
        if clear_history:
            self.messages.clear()

    def clear(self) -> None:
        self.messages = []

    def spawn(self, system_prompt: str, **kwargs) -> "SubAgent":
        """从当前 Agent 的基础设施创建子 Agent"""
        from .subagent import SubAgent, SubAgentConfig

        config = SubAgentConfig(
            system_prompt=system_prompt,
            tool_names=kwargs.get("tool_names"),
            max_tool_calls=kwargs.get("max_tool_calls", 50),
            max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
            bypass_permissions=kwargs.get("bypass_permissions", False),
            tool_timeout=kwargs.get("tool_timeout", self.config.tool_timeout),
            tool_result_limit=kwargs.get(
                "tool_result_limit", self.config.tool_result_limit
            ),
        )
        return SubAgent(
            provider=lambda: self.provider,  # 动态获取最新 provider
            tools=self._tools,
            config=config,
            event_bus=kwargs.get("event_bus", self.event_bus),
            cancel_token=kwargs.get("cancel_token", self.cancel_token),
            permission_checker=kwargs.get(
                "permission_checker", self.permission_checker
            ),
        )

    @property
    def last_usage(self) -> Usage | None:
        return self._last_usage
