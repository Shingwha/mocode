"""Skill 数据结构定义"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SkillMetadata:
    """Skill 元数据 (Level 1) - 始终加载到系统提示"""

    name: str
    description: str
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    @classmethod
    def from_frontmatter(cls, frontmatter: dict) -> "SkillMetadata":
        """从 YAML frontmatter 创建元数据"""
        return cls(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            metadata=frontmatter.get("metadata", {}),
        )


@dataclass
class Skill:
    """完整的 Skill 对象"""

    path: Path  # Skill 目录路径
    metadata: SkillMetadata  # 元数据
    content: Optional[str] = None  # SKILL.md 正文 (Level 2，按需加载)

    @property
    def skill_md_path(self) -> Path:
        """SKILL.md 文件路径"""
        return self.path / "SKILL.md"

    def load_content(self) -> str:
        """加载 SKILL.md 正文内容 (去除 frontmatter)"""
        if self.content is None:
            content = self.skill_md_path.read_text(encoding="utf-8")
            # 去除 YAML frontmatter
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    self.content = parts[2].strip()
                else:
                    self.content = content
            else:
                self.content = content
        return self.content
