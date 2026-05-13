"""App system prompt — main agent identity, guidelines, environment, tools, skills.

Aligned with 0.2's system_prompt() sections: soul, user, memory, environment, tools, skills.
Content for soul/user/memory is passed in at runtime (no filesystem assumptions in core).

Usage:
    from mocode.prompts.app import build_system_prompt

    prompt_str = build_system_prompt(
        tools=registry,
        skill_manager=mgr,
        cwd="/path",
        soul="You are MoCode, a concise coding assistant.",
        user="User profile content here.",
        memory="Long-term memory content here.",
    )
"""

from typing import Any

from ..core.prompt import Prompt, Section


def build_system_prompt(
    tools: Any = None,
    skill_manager: Any = None,
    cwd: str = "",
    soul: str = "",
    user: str = "",
    memory: str = "",
    home: str = "",
    config_path: str = "",
    skills_dir: str = "",
    sessions_dir: str = "",
    **ctx: Any,
) -> str:
    """Build the main agent system prompt as an XML string.

    Args:
        tools: ToolRegistry — tool name/description pairs are listed in the prompt.
        skill_manager: SkillManager — skill metadata is listed in the prompt.
        cwd: Current working directory shown to the LLM.
        soul: Soul content (identity, behavioral guidelines).
        user: User profile content.
        memory: Long-term memory content.
        home: MoCode home directory path.
        config_path: Config file path.
        skills_dir: Skills directory path.
        sessions_dir: Sessions directory path.
        **ctx: Extra context variables passed to lambda sections.
    """
    sections = [
        Section("identity", _render_soul(soul), priority=10),
        Section("user", _render_user(user), priority=11),
        Section("memory", _render_memory(memory), priority=12),
        Section("guidelines", _render_guidelines, priority=20),
        Section("environment", _render_environment(
            cwd, home, config_path, skills_dir, sessions_dir,
        ), priority=30),
    ]

    if tools is not None:
        sections.append(Section("tools", _render_tools(tools), priority=40))

    if skill_manager is not None:
        sections.append(Section("skills", _render_skills(skill_manager), priority=50))

    return Prompt(sections).context(**ctx).build(format="xml")


DEFAULT_SOUL = (
    "# Identity\n"
    "You are MoCode, a concise coding assistant.\n"
    "\n"
    "# Guidelines\n"
    "- Be concise and direct\n"
    "- Prefer `edit` over `write` for existing files\n"
    "- Verify changes before claiming success\n"
    "- Handle errors gracefully\n"
    "- Respond in the same language the user uses\n"
)

DEFAULT_USER = (
    "# User Profile\n"
    "(Information about the user will be stored here. Edit this file to customize.)\n"
)

DEFAULT_MEMORY = (
    "# Long-term Memory\n"
    "(Important facts, decisions, and context will be stored here. "
    "Edit this file to add persistent knowledge.)\n"
)


def _render_soul(content: str) -> str:
    return content or DEFAULT_SOUL


def _render_user(content: str) -> str:
    return content or DEFAULT_USER


def _render_memory(content: str) -> str:
    return content or DEFAULT_MEMORY


def _render_guidelines(_ctx: dict[str, Any]) -> str:
    return "\n".join([
        "- Be concise and direct",
        "- Prefer `edit` over `write` for existing files",
        "- Verify changes before claiming success",
        "- Handle errors gracefully",
    ])


def _render_environment(
    cwd: str, home: str, config_path: str, skills_dir: str, sessions_dir: str,
) -> str:
    parts = [f"cwd: {cwd}"]
    if home:
        parts.append(f"home: {home}")
    if config_path:
        parts.append(f"config: {config_path}")
    if skills_dir:
        parts.append(f"skills: {skills_dir}")
    if sessions_dir:
        parts.append(f"sessions: {sessions_dir}")
    return "\n".join(parts)


def _render_tools(tools: Any) -> list[Section]:
    return [
        Section(t.name, t.description, attrs={"type": "tool"})
        for t in tools.all()
    ]


def _render_skills(skill_manager: Any) -> list[Section]:
    return [
        Section(m.name, m.description, attrs={"type": "skill"})
        for m in skill_manager.all_metadata()
    ]
