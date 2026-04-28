"""SubAgent tool — LLM 可将任务委派给子 Agent"""

from collections.abc import Callable

from ..tool import Tool, ToolRegistry
from ..subagent import SubAgent, SubAgentConfig

SUBAGENT_SYSTEM_PROMPT = """\
You are a sub-agent executing a specific task delegated by the parent agent.
Your output is returned to the parent agent — it is not shown to the user directly.

Guidelines:
- Focus on completing the task. No greetings, no summaries, no unnecessary explanations.
- Use your tools freely to get the job done. If something fails, diagnose and retry before reporting back.
- Return clear, concise results. Report file paths, key data, or a brief status — whatever the task requires.
- If the task cannot be completed with your available tools, state what is missing briefly.
- You cannot ask follow-up questions. Work autonomously with what you have.
- Any special requirements or constraints are specified in the task description below — follow them precisely.\
"""


def register_subagent_tools(registry: ToolRegistry, config, provider_or_getter) -> None:
    """注册 sub_agent 工具

    Args:
        provider_or_getter: Provider 对象或返回 Provider 的回调函数
    """
    parent_tools = registry

    # 统一为 getter 函数
    if callable(provider_or_getter) and not hasattr(provider_or_getter, "call"):
        get_provider = provider_or_getter
    else:
        _provider = provider_or_getter
        get_provider = lambda: _provider

    async def _sub_agent(args: dict) -> str:
        task = args.get("task", "")
        tool_names = None
        if args.get("tools"):
            tool_names = [t.strip() for t in args["tools"].split(",") if t.strip()]

        # 禁止子 Agent 使用的工具：防止嵌套派生、上下文压缩、后台反思等
        _BLOCKED_TOOLS = {"sub_agent", "compact", "dream"}
        if tool_names is not None:
            tool_names = [t for t in tool_names if t not in _BLOCKED_TOOLS]
        # blocked tools 也从 derived registry 中移除，双重保险
        derived_tools = parent_tools.derived(exclude=_BLOCKED_TOOLS)

        sub_config = SubAgentConfig(
            system_prompt=SUBAGENT_SYSTEM_PROMPT,
            tool_names=tool_names,
            max_tool_calls=args.get("max_tool_calls", 50),
            max_tokens=args.get("max_tokens", 8192),
            bypass_permissions=True,
            tool_timeout=config.tool_timeout,
        )
        sub = SubAgent(provider=get_provider, tools=derived_tools, config=sub_config)
        result = await sub.run(task)
        if result.had_error:
            return f"[SubAgent error] {result.content}"
        return result.content

    registry.register(
        Tool(
            "sub_agent",
            "Delegate a task to a sub-agent that inherits ALL your tools by default. "
            "The sub-agent runs autonomously with its own message history and returns the final result. "
            "Put any special requirements or constraints directly in the task description.",
            {
                "task": {
                    "type": "string",
                    "description": "The task to delegate to the sub-agent",
                },
                "tools": {
                    "type": "string",
                    "description": "Comma-separated allowlist of tool names. LEAVE EMPTY to give the sub-agent full access to all tools — "
                    "this is strongly recommended unless you have a clear reason to restrict (e.g. a read-only research task). "
                    "Unnecessarily limiting tools will likely cause the sub-agent to fail.",
                    "default": "",
                },
                "max_tool_calls": {
                    "type": "integer",
                    "description": "Max tool calls (default 50)",
                    "default": 50,
                },
                "max_tokens": {
                    "type": "integer",
                    "description": "Max response tokens (default 8192)",
                    "default": 8192,
                },
            },
            _sub_agent,
        )
    )
