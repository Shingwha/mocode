"""SubAgent prompt — system prompt for delegated sub-agents.

The sub-agent's prompt is built by layering sub-agent-specific sections
(identity + guidelines) onto the parent agent's full system prompt.
This ensures the sub-agent inherits AGENTS.md, environment, tools,
skills, and all other context from the parent.
"""

_SUB_AGENT_IDENTITY = (
    "You are a sub-agent executing a specific task delegated by the parent agent. "
    "Your output is returned to the parent agent — it is not shown to the user directly."
)

_SUB_AGENT_GUIDELINES = "\n".join(
    [
        "- Focus on completing the task. No greetings, no summaries, no unnecessary explanations",
        "- Use tools freely. If something fails, diagnose and retry before reporting back",
        "- Return clear, concise results: file paths, key data, or brief status",
        "- If the task cannot be completed, state what is missing briefly",
        "- You cannot ask follow-up questions. Work autonomously",
        "- Follow any special requirements in the task description precisely",
        "- Follow the parent agent's guidelines, conventions, and constraints from AGENTS.md",
    ]
)


def build_subagent_prompt(parent_prompt: str) -> str:
    """Build sub-agent system prompt by layering sub-agent sections onto parent prompt.

    The parent's AGENTS.md, environment, tools, skills, and other context are preserved.
    Sub-agent identity is prepended and sub-agent-specific guidelines are appended.
    """
    parts = []
    # Sub-agent identity first (so it's the first thing the LLM sees)
    parts.append(f"<identity>\n{_SUB_AGENT_IDENTITY}\n</identity>")
    # Parent prompt in full (AGENTS.md, env, tools, skills, etc.)
    parts.append(parent_prompt)
    # Sub-agent execution guidelines last
    parts.append(f"<guidelines>\n{_SUB_AGENT_GUIDELINES}\n</guidelines>")
    return "\n\n".join(parts)
