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
    workflow_registry: Any = None,
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
        workflow_registry: WorkflowRegistry — available workflows are listed in the prompt.
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

    if tools is not None:
        sections.append(Section("tools", _render_tools(tools), priority=40))

    if skill_manager is not None:
        sections.append(Section("skills", _render_skills(skill_manager), priority=50))

    if workflow_registry is not None:
        wf_sections = _render_workflows(workflow_registry, cwd)
        if wf_sections:
            sections.append(Section("workflows", wf_sections, priority=60))

    return Prompt(sections).context(**ctx).build(fmt="xml")


def _render_agents(home: str, cwd: str) -> str:
    """Read AGENTS.md files and render the agents section."""
    parts = []
    for p in (
        Path(home) / "AGENTS.md" if home else None,
        Path(cwd) / "AGENTS.md" if cwd else None,
    ):
        if p is not None and p.exists():
            content = p.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
    content = "\n\n".join(parts)

    sources = []
    if home:
        sources.append(f"  - {home}/AGENTS.md  (global, applies to all projects)")
    if cwd:
        sources.append(f"  - {cwd}/AGENTS.md  (project, specific to this project)")
    header = (
        "The following instructions are loaded from AGENTS.md files — a place for "
        "project-specific and user-specific guidance that helps you work effectively. "
        "Treat them as rules from the project owner: follow build steps, respect code "
        "conventions, and heed any warnings listed below."
    )
    if sources:
        header += "\n\nSources (edit these files to customize):\n" + "\n".join(sources)
    if not content:
        header += (
            "\n\nNo AGENTS.md files found yet. You can create them to provide persistent "
            "instructions. Common sections: project overview, build/test commands, code style, "
            "testing instructions, security considerations."
        )
    return f"{header}\n\n{content}" if content else header


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


def _render_tools(tools: Any) -> list[Section]:
    return [Section(t.name, t.description, attrs={"type": "tool"}) for t in tools.all()]


def _render_skills(skill_manager: Any) -> list[Section]:
    return [
        Section(m.name, m.description, attrs={"type": "skill"})
        for m in skill_manager.all_metadata()
    ]


def _render_workflows(registry: Any, cwd: str = "") -> list[Section] | None:
    """Render available workflows with usage guidance and per-workflow details."""
    wfs = registry.list()
    if not wfs:
        return None

    usage_guide = (
        "MoCode Workflows are DAG-based multi-step task orchestrations. "
        "CLI commands (run in bash):\n"
        "- mocode workflow list              — list all available workflows\n"
        "- mocode workflow show <name>       — show workflow details\n"
        "- mocode workflow run <name>        — execute a workflow\n\n"
        "Full reference: read vfs://workflow/cli-reference.md"
    )

    sections: list[Section] = [
        Section("guide", usage_guide, priority=0),
    ]

    for wf in wfs:
        attrs: dict[str, str] = {"name": wf.name, "nodes": str(len(wf.nodes))}
        if wf.path and cwd:
            try:
                attrs["path"] = str(wf.path.relative_to(Path(cwd)))
            except ValueError:
                attrs["path"] = str(wf.path)
        desc = wf.description or "(no description)"
        sections.append(Section("workflow", desc, attrs=attrs))

    return sections
