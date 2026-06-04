"""Built-in skill: Workflow — DAG-based multi-step task orchestration."""

from __future__ import annotations

from pathlib import Path

from mocode.core.skill import make_builtin_skill


def WorkflowSkill():
    """MoCode Workflows — DAG-based multi-step task orchestration."""
    return make_builtin_skill(Path(__file__).parent, default_name="workflow")
