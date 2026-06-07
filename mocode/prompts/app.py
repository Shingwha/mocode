"""App system prompt — agents, guidelines, environment, tools, skills.

AGENTS.md
---------
AGENTS.md gives agents the extra, detailed context they need that doesn't
belong in a README: build steps, test commands, code conventions, security
gotchas — anything you'd tell a new teammate.

Two locations are read and merged (global first, then project):
  - ~/.mocode/AGENTS.md   — user-level instructions (applies to all projects)
  - ./AGENTS.md           — project-level instructions (in the working directory)

Both are optional. If neither exists, the <agents> section is simply omitted.

Usage:
    from mocode.prompts.app import build_system_prompt

    prompt_str = build_system_prompt(
        tools=registry,
        skill_manager=mgr,
        cwd="/path",
    )
"""

from pathlib import Path
from typing import Any

from ..core.prompt import Prompt, Section


def build_system_prompt(
    tools: Any = None,
    skill_manager: Any = None,
    vfs: Any = None,
    cwd: str = "",
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
        vfs: VirtualFS — virtual file system for listing available virtual files.
        cwd: Current working directory shown to the LLM.
        home: MoCode home directory path.
        config_path: Config file path.
        skills_dir: Skills directory path.
        sessions_dir: Sessions directory path.
        **ctx: Extra context variables passed to lambda sections.
    """
    sections = []

    # Order: stable → dynamic (maximizes prefix cache hit rate)
    sections.append(Section("guidelines", _render_guidelines, priority=10))
    sections.append(Section("agents", _render_agents(home, cwd), priority=20))
    sections.append(
        Section(
            "environment",
            _render_environment(
                cwd,
                home,
                config_path,
                skills_dir,
                sessions_dir,
            ),
            priority=30,
        )
    )

    if vfs is not None and len(vfs):
        sections.append(Section("vfs", _render_vfs(vfs), priority=35))

    if tools is not None:
        sections.append(Section("tools", _render_tools(tools), priority=40))

    if skill_manager is not None:
        sections.append(Section("skills", _render_skills(skill_manager), priority=50))

    sections.append(Section("workflows", _render_workflows(), priority=60))

    return Prompt(sections).context(**ctx).build(fmt="xml")


def _render_agents(home: str, cwd: str) -> list[Section]:
    """Read AGENTS.md files and render the agents section."""
    header = (
        "The following instructions are loaded from AGENTS.md files — a place for "
        "project-specific and user-specific guidance that helps you work effectively. "
        "Treat them as rules from the project owner: follow build steps, respect code "
        "conventions, and heed any warnings listed below."
    )

    agent_sections = []

    # Global AGENTS.md
    if home:
        global_path = Path(home) / "AGENTS.md"
        if global_path.exists():
            content = global_path.read_text(encoding="utf-8").strip()
            if content:
                agent_sections.append(
                    Section("agent", content, attrs={"source": "global", "path": str(global_path)})
                )

    # Project AGENTS.md
    if cwd:
        project_path = Path(cwd) / "AGENTS.md"
        if project_path.exists():
            content = project_path.read_text(encoding="utf-8").strip()
            if content:
                agent_sections.append(
                    Section("agent", content, attrs={"source": "project", "path": str(project_path)})
                )

    # If no AGENTS.md found, add hint
    if not agent_sections:
        hint = (
            "No AGENTS.md files found yet. You can create them to provide persistent "
            "instructions. Common sections: project overview, build/test commands, code style, "
            "testing instructions, security considerations."
        )
        agent_sections.append(Section("agent", hint, attrs={"source": "hint"}))

    return [Section("header", header)] + agent_sections


def _render_guidelines(_ctx: dict[str, Any]) -> str:
    return "\n".join(
        [
            "- Be concise and direct",
            "- Prefer `edit` over `write` for existing files",
            "- Verify changes before claiming success",
            "- Handle errors gracefully",
        ]
    )


def _render_environment(
    cwd: str,
    home: str,
    config_path: str,
    skills_dir: str,
    sessions_dir: str,
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


def _render_vfs(_vfs: Any) -> str:
    """Render virtual file system usage guidance."""
    return (
        "## Virtual File System (VFS)\n\n"
        "MoCode has a virtual file system that provides read-only access to embedded content. "
        "Virtual files are prefixed with ``vfs://`` and can be accessed using the ``read``, "
        "``glob``, and ``grep`` tools.\n\n"
        "### Usage\n\n"
        "- **Read a virtual file**: ``read(path='vfs://workflow/yaml-reference.md')``\n"
        "- **Search virtual files**: ``grep(pattern='keyword', path='vfs://')``\n"
        "- **List virtual files**: ``glob(pattern='**/*', path='vfs://')``\n\n"
        "Virtual files are provided by skills and contain reference documentation, "
        "templates, and examples."
    )


def _render_tools(tools: Any) -> list[Section]:
    return [Section("tool", t.description, attrs={"name": t.name}) for t in tools.all()]


def _render_skills(skill_manager: Any) -> list[Section]:
    return [
        Section(
            "skill",
            s.metadata.description,
            attrs={"name": s.metadata.name, "path": s.base_dir},
        )
        for s in skill_manager.all()
    ]


def _render_workflows() -> list[Section]:
    """Render workflow usage guidance.

    Individual workflow details are omitted from the system prompt — the LLM
    discovers them on demand via ``/workflow list``.
    """
    usage_guide = (
        "Use the /workflow command in the REPL to manage workflows:\n"
        "- /workflow list                — list available workflows\n"
        "- /workflow show <name>         — show DAG details\n"
        "- /workflow run <name>          — execute a workflow (foreground)\n"
        "- /workflow run-bg <name>       — execute a workflow (background)\n"
        "- /workflow status [run_id]     — check run status\n"
        "- /workflow result [run_id]     — view full results\n"
        "- /workflow runs                — list recent runs"
    )
    return [Section("guide", usage_guide, priority=0)]
