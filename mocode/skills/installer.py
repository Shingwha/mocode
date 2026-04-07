"""Skill installation from GitHub repositories."""

from pathlib import Path
from typing import Callable

from ..core.installer import (
    GitHubInstaller,
    SourceType,
    InstallCandidate,
    InstallResult,
    InstallMethod,
    InstalledItemInfo,
)
from ..plugins.url_utils import RepoInfo, fetch_raw_file
from ..paths import SKILLS_DIR


class SkillInstaller(GitHubInstaller):
    """Handles skill installation from GitHub repositories.

    Detects SKILL.md files to identify skills. Supports:
    - Single skill repos (SKILL.md at root)
    - Multi skill repos (subdirectories with SKILL.md)
    - Git clone and ZIP download fallback
    - Registry tracking for update/uninstall
    """

    SKILL_FILE = "SKILL.md"

    @property
    def DETECT_FILE(self) -> str:
        return self.SKILL_FILE

    @property
    def registry_key(self) -> str:
        return "skills"

    @property
    def install_dir(self) -> Path:
        return SKILLS_DIR

    def __init__(
        self,
        skills_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        super().__init__(target_dir=skills_dir, progress_callback=progress_callback)

    def _analyze_repo_structure(
        self,
        repo_info: RepoInfo,
        tree: list[dict],
    ) -> tuple[SourceType, list[InstallCandidate]]:
        paths = {item["path"] for item in tree if item["type"] == "blob"}
        candidates: list[InstallCandidate] = []

        # Check if subpath is specified
        if repo_info.subpath:
            subpath = repo_info.subpath.rstrip("/")
            if f"{subpath}/{self.SKILL_FILE}" in paths:
                candidate = self._create_candidate(repo_info, subpath)
                if candidate:
                    candidates.append(candidate)
            return SourceType.SINGLE, candidates

        # Check for single skill at root
        if self.SKILL_FILE in paths:
            candidate = self._create_candidate(repo_info, "")
            if candidate:
                candidates.append(candidate)
            return SourceType.SINGLE, candidates

        # Scan all directories for SKILL.md
        skill_dirs = set()
        for item in tree:
            if item["type"] == "blob" and item["path"].endswith(f"/{self.SKILL_FILE}"):
                parent = str(Path(item["path"]).parent)
                if parent:
                    skill_dirs.add(parent)

        for skill_dir in sorted(skill_dirs):
            candidate = self._create_candidate(repo_info, skill_dir)
            if candidate:
                candidates.append(candidate)

        if candidates:
            return SourceType.MULTI, candidates

        return SourceType.SINGLE, []

    def _create_candidate(self, repo_info: RepoInfo, path: str) -> InstallCandidate | None:
        name = Path(path).name if path else repo_info.repo
        skill_md_path = f"{path}/{self.SKILL_FILE}" if path else self.SKILL_FILE

        description = ""
        has_metadata = False

        try:
            content = fetch_raw_file(
                repo_info.owner, repo_info.repo, skill_md_path, repo_info.branch
            )
            if content:
                frontmatter = self._parse_frontmatter(content)
                if frontmatter:
                    has_metadata = True
                    description = frontmatter.get("description", "")
                    if frontmatter.get("name"):
                        name = frontmatter["name"]
        except Exception:
            pass

        return InstallCandidate(
            name=name,
            path=path,
            description=description,
            has_metadata=has_metadata,
        )

    def _not_found_error(self) -> str:
        return "No valid skill found in repository. A skill needs a SKILL.md file."

    @staticmethod
    def _parse_frontmatter(content: str) -> dict | None:
        """Parse YAML frontmatter from SKILL.md content."""
        if not content.startswith("---"):
            return None
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None
        try:
            import yaml
            return yaml.safe_load(parts[1])
        except Exception:
            return None


# Backward-compatible aliases
SkillCandidate = InstallCandidate
SkillInstallResult = InstallResult
SkillSourceType = SourceType
