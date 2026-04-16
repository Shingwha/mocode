"""Skill 管理器 - 发现、加载和管理 skills"""

from pathlib import Path
from typing import Optional

import yaml

from .schema import Skill, SkillMetadata
from ..paths import SKILLS_DIR, PROJECT_SKILLS_DIRNAME
from ..tool import Tool, ToolRegistry


class SkillManager:
    """Skill 发现、加载和管理 - 非单例模式，由 App 创建实例"""

    DEFAULT_SKILLS_DIRS = [
        SKILLS_DIR,  # 全局 skills (~/.mocode/skills)
    ]

    def __init__(self, skills_dirs: Optional[list[Path]] = None, registry: Optional[ToolRegistry] = None):
        self.skills_dirs = skills_dirs or self.DEFAULT_SKILLS_DIRS.copy()
        # 添加项目级 skills 目录（优先级最高）
        project_skills = Path.cwd() / PROJECT_SKILLS_DIRNAME / "skills"
        if project_skills not in self.skills_dirs:
            self.skills_dirs.append(project_skills)

        # 确保技能目录存在
        for skills_dir in self.skills_dirs:
            skills_dir.mkdir(parents=True, exist_ok=True)

        self._skills: dict[str, Skill] = {}
        self._discover_skills()

        # 如果提供了 registry，注册 skill 工具
        if registry is not None:
            self._register_tool(registry)

    def _discover_skills(self):
        """发现所有可用 skills"""
        for skills_dir in self.skills_dirs:
            if not skills_dir.exists():
                continue
            for skill_folder in skills_dir.iterdir():
                if skill_folder.is_dir():
                    skill_md = skill_folder / "SKILL.md"
                    if skill_md.exists():
                        skill = self._load_skill(skill_folder)
                        if skill:
                            self._skills[skill.metadata.name] = skill

    def _load_skill(self, path: Path) -> Optional[Skill]:
        """加载单个 skill (仅元数据)"""
        skill_md = path / "SKILL.md"
        try:
            content = skill_md.read_text(encoding="utf-8")
            frontmatter = self._parse_frontmatter(content)
            if frontmatter:
                metadata = SkillMetadata.from_frontmatter(frontmatter)
                if metadata.name:  # 确保有有效名称
                    return Skill(path=path, metadata=metadata)
        except Exception:
            pass
        return None

    def _parse_frontmatter(self, content: str) -> Optional[dict]:
        """解析 YAML frontmatter"""
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        try:
            return yaml.safe_load(parts[1])
        except yaml.YAMLError:
            return None

    def _register_tool(self, registry: ToolRegistry) -> None:
        """注册 skill 工具到指定的 ToolRegistry"""

        def _use_skill(args: dict) -> str:
            """激活指定 skill，返回其目录路径和正文内容"""
            name = args.get("name")
            if not name:
                return "error: missing required parameter 'name'"

            skill = self.get_skill(name)
            if not skill:
                available = self.list_skills()
                if available:
                    return f"error: skill '{name}' not found. Available skills: {available}"
                return f"error: skill '{name}' not found. No skills are currently available."

            return f"Base directory for this skill: {skill.path}\n\n{skill.load_content()}"

        registry.register(Tool(
            "skill",
            "Load a skill by name. Use when the user's request matches a skill's description. "
            "Returns the skill's instructions for you to follow.",
            {"name": "string"},
            _use_skill,
        ))

    def get_all_metadata(self) -> list[SkillMetadata]:
        """获取所有 skill 元数据 (用于系统提示)"""
        return [s.metadata for s in self._skills.values()]

    def get_skill(self, name: str) -> Optional[Skill]:
        """按名称获取 skill"""
        return self._skills.get(name)

    def list_skills(self) -> list[str]:
        """列出所有可用 skill 名称"""
        return list(self._skills.keys())

    def refresh(self) -> None:
        """Re-discover skills without creating a new instance."""
        self._skills.clear()
        self._discover_skills()
