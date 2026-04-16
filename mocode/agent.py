"""Agent — LLM 对话引擎

v0.2 关键改进：
- 接收 ToolRegistry 实例（非全局）
- 使用 Response DTO（非 OpenAI SDK 类型）
- json 在文件顶部导入
- compact 通过构造函数传入
"""

import asyncio
import json
from pathlib import Path
import base64
from typing import TYPE_CHECKING

from .event import EventType, EventBus
from .interrupt import CancellationToken, Interrupted, InterruptReason
from .permission import CheckOutcome, PermissionChecker
from .provider import Response, Usage
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
        self.messages: list[dict] = []
        self._last_usage: Usage | None = None
        self.conversation_id: str | None = None
        self._tools.state["messages"] = self.messages

    # ---- Event helpers ----

    def _emit_tool_start(self, tool_name: str, tool_args: dict) -> None:
        self.event_bus.emit(EventType.TOOL_START, {
            "name": tool_name, "args": tool_args,
            "conversation_id": self.conversation_id,
        })

    def _emit_tool_complete(self, tool_name: str, result: str) -> None:
        self.event_bus.emit(EventType.TOOL_COMPLETE, {
            "name": tool_name, "result": result,
            "conversation_id": self.conversation_id,
        })

    def _deny_tool(self, tool_name: str, tool_args: dict, reason: str) -> tuple[str, bool]:
        self._emit_tool_start(tool_name, tool_args)
        msg = f"User {reason} this operation"
        self._emit_tool_complete(tool_name, msg)
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
        self.event_bus.emit(EventType.MESSAGE_ADDED, {
            "role": "user", "content": user_input,
            "conversation_id": self.conversation_id,
        })

        try:
            return await self._agent_loop()
        except Interrupted as exc:
            self.event_bus.emit(EventType.INTERRUPTED, {"reason": exc.reason.name})
            return "[interrupted]"

    async def _agent_loop(self) -> str:
        final_response = ""
        while True:
            self.cancel_token.check()
            response = await self.cancel_token.cancellable(
                self.provider.call(
                    self.messages, self.system_prompt,
                    self._tools.all_schemas(), self.config.max_tokens,
                )
            )

            # Track token usage
            if response.usage:
                self._last_usage = response.usage
                if self._compact:
                    self._compact.update_usage(response.usage.prompt_tokens)

            tool_results = []

            if response.content:
                final_response = response.content
                self.event_bus.emit(EventType.TEXT_COMPLETE, {
                    "content": response.content,
                    "conversation_id": self.conversation_id,
                })

            if response.tool_calls:
                try:
                    for tc in response.tool_calls:
                        self.cancel_token.check()
                        tool_args = json.loads(tc.arguments)
                        result, denied = await self._run_tool_async(tc.name, tool_args)
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })
                        if denied:
                            raise Interrupted(InterruptReason.PERMISSION_DENIED)
                finally:
                    # Save partial state (assistant + any tool results collected)
                    self.messages.append({
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [
                            {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                            for t in (response.tool_calls or [])
                        ],
                    })
                    if tool_results:
                        self.messages.extend(tool_results)
            else:
                self.messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                        for t in (response.tool_calls or [])
                    ],
                })
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
                    parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{ext[1:]};base64,{b64}"},
                    })
                    has_images = True
                    continue
                except Exception:
                    pass
            parts.append({"type": "text", "text": f"User sent you a file: {p.name} (path: {path_str}). If user did not include any message with this file, ask the user what they would like to do with it."})

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

    async def _run_tool_async(self, tool_name: str, tool_args: dict) -> tuple[str, bool]:
        """工具执行：权限检查 → 执行 → 截断"""
        # 1. Permission check
        if self.permission_checker:
            result = await self.permission_checker.check(tool_name, tool_args)
            if result.outcome == CheckOutcome.DENY:
                return self._deny_tool(tool_name, tool_args, result.reason)
            elif result.outcome == CheckOutcome.USER_INPUT:
                self._emit_tool_start(tool_name, tool_args)
                self._emit_tool_complete(tool_name, result.user_input)
                return (result.user_input, False)

        # 2. Execute tool
        self._emit_tool_start(tool_name, tool_args)

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
            self._emit_tool_complete(tool_name, f"Tool timed out after {timeout}s")
            return (f"error: tool '{tool_name}' timed out after {timeout}s", False)

        # 3. Truncate and emit
        result = self._apply_result_limit(result)
        self._emit_tool_complete(tool_name, result)
        return (result, False)

    # ---- Mutation methods ----

    def update_provider(self, provider) -> None:
        """切换 provider"""
        self.provider = provider

    def update_system_prompt(self, prompt: str, clear_history: bool = False) -> None:
        self.system_prompt = prompt
        if clear_history:
            self.messages.clear()

    def clear(self) -> None:
        self.messages.clear()

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
            tool_result_limit=kwargs.get("tool_result_limit", self.config.tool_result_limit),
        )
        return SubAgent(
            provider=self.provider,
            tools=self._tools,
            config=config,
            event_bus=kwargs.get("event_bus", self.event_bus),
            cancel_token=kwargs.get("cancel_token", self.cancel_token),
            permission_checker=kwargs.get("permission_checker", self.permission_checker),
        )

    @property
    def last_usage(self) -> Usage | None:
        return self._last_usage
