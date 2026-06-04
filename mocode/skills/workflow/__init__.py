"""Built-in skill: Workflow — DAG-based multi-step task orchestration.

Teaches the agent how to design, create, and run MoCode Workflows so it
can guide users through the workflow lifecycle.
"""

from __future__ import annotations

from pathlib import Path

from mocode.core.skill import Skill, discover_references, read_skill

_PKG_DIR = Path(__file__).parent


def WorkflowSkill() -> Skill:
    """MoCode Workflows — DAG-based multi-step task orchestration."""
    fm, content = read_skill(_PKG_DIR)
    name = fm.get("name", "workflow")
    description = fm.get("description", "")
    virtual_files = discover_references(_PKG_DIR, name)
    return Skill.builtin(
        name=name,
        description=description,
        content=content,
        virtual_files=virtual_files,
    )
