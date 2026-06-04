"""Built-in skills for MoCode — factory functions with package data.

Usage:
    from mocode.skills import WorkflowSkill

    skill_mgr = SkillManager([...], vfs=vfs)
    skill_mgr.register(WorkflowSkill())

To add a new built-in skill:
    1. Create ``mocode/skills/my_skill/`` directory with SKILL.md + references
    2. Add a one-liner factory in ``mocode/skills/my_skill/__init__.py``
    3. Import and export it from ``mocode/skills/__init__.py``
    4. Register it in ``mocode/app/cli/app.py`` (_build_agent)
"""

from __future__ import annotations

from mocode.core.skill import make_builtin_skill as make_builtin_skill

from .amesim_tuner import AmesimTunerSkill
from .simulink_tuner import SimulinkTunerSkill
from .workflow import WorkflowSkill

__all__ = [
    "AmesimTunerSkill",
    "SimulinkTunerSkill",
    "WorkflowSkill",
    "make_builtin_skill",
]
