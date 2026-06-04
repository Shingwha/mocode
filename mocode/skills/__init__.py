"""Built-in skills for MoCode.

Each module exports a factory function that returns a ``Skill`` instance
with embedded content — no filesystem required.

Usage:
    from mocode.skills import WorkflowSkill

    skill_mgr = SkillManager([...])
    skill_mgr.register(WorkflowSkill())

To add a new built-in skill:
    1. Create ``mocode/skills/my_skill.py`` with a factory function
    2. Import and export it from ``mocode/skills/__init__.py``
    3. Register it in ``mocode/app/cli/app.py`` (_build_agent)
"""

from __future__ import annotations

from .amesim_tuner import AmesimTunerSkill
from .simulink_tuner import SimulinkTunerSkill
from .workflow import WorkflowSkill

__all__ = [
    "AmesimTunerSkill",
    "SimulinkTunerSkill",
    "WorkflowSkill",
]
