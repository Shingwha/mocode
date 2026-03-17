"""LLM 提示词模板"""

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..skills.manager import SkillManager


def get_system_prompt(skill_manager: "SkillManager | None" = None) -> str:
    """获取系统提示词（给 LLM 的）"""
    cwd = os.getcwd()
    base = f"""Concise coding assistant.
Working directory: {cwd}

You have access to the following tools:
- read: Read files with line numbers
- write: Create or overwrite files
- edit: Make targeted edits to files (preferred for modifications)
- glob: Find files by pattern
- grep: Search file contents
- bash: Run shell commands
- skill: Load a skill's instructions by name

Guidelines:
- Be concise and direct
- Use edit instead of write for modifications
- Verify changes before declaring success
- Handle errors gracefully"""

    # 注入 skills 元数据
    if skill_manager:
        skills_metadata = skill_manager.get_all_metadata()
        if skills_metadata:
            skills_section = "\n\n## Available Skills\n\n"
            for m in skills_metadata:
                skills_section += f"- **{m.name}**: {m.description}\n"
            skills_section += (
                "\nUse the `skill` tool to load a skill's full instructions when needed."
            )
            base += skills_section

    return base
