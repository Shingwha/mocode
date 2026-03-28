"""Skill installation from GitHub repositories."""

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from ..plugins.url_utils import (
    RepoInfo,
    parse_github_url,
    fetch_repo_tree,
    fetch_raw_file,
    download_repo_zip,
    extract_zip_to_dir,
    run_git_clone,
    check_git_available,
    get_default_branch,
    GitHubApiError,
    NetworkError,
)
from ..paths import SKILLS_DIR


class InstallMethod(Enum):
    """Installation method used."""
    GIT = "git"
    ZIP = "zip"


class SkillSourceType(Enum):
    """Type of skill repository."""
    SINGLE = "single"  # Root has SKILL.md
    MULTI = "multi"    # Subdirectories each have SKILL.md


@dataclass
class SkillCandidate:
    """A candidate skill discovered in a remote repository."""
    name: str
    path: str  # Relative path within repo
    description: str = ""
    has_metadata: bool = False


@dataclass
class SkillInstallResult:
    """Result of skill installation."""
    success: bool
    skill_name: str | None = None
    skill_path: Path | None = None
    error: str | None = None
    already_installed: bool = False
    updated: bool = False


@dataclass
class InstalledSkillInfo:
    """Information about an installed skill."""
    name: str
    source: str
    installed_at: str
    method: str
    subpath: str | None = None


INSTALLED_FILE = "installed.json"


class SkillInstaller:
    """Handles skill installation from GitHub repositories.

    Detects SKILL.md files to identify skills. Supports:
    - Single skill repos (SKILL.md at root)
    - Multi skill repos (subdirectories with SKILL.md)
    - Git clone and ZIP download fallback
    - Registry tracking for update/uninstall
    """

    SKILL_FILE = "SKILL.md"

    def __init__(
        self,
        skills_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        self.skills_dir = skills_dir or SKILLS_DIR
        self.progress_callback = progress_callback
        self._installed_registry: dict | None = None

    def parse_url(self, url: str) -> RepoInfo:
        """Parse GitHub URL into structured information."""
        return parse_github_url(url)

    def discover_from_repo(self, url: str) -> tuple[SkillSourceType, list[SkillCandidate]]:
        """Discover skills available in a repository.

        Args:
            url: GitHub URL

        Returns:
            Tuple of (source_type, list of candidates)
        """
        repo_info = self.parse_url(url)
        self._report(f"Fetching repository info for {repo_info.owner}/{repo_info.repo}...")

        if repo_info.branch == "main":
            try:
                default_branch = get_default_branch(repo_info.owner, repo_info.repo)
                repo_info = RepoInfo(
                    owner=repo_info.owner,
                    repo=repo_info.repo,
                    branch=default_branch,
                    subpath=repo_info.subpath,
                )
            except (GitHubApiError, NetworkError):
                pass

        try:
            tree = fetch_repo_tree(repo_info.owner, repo_info.repo, repo_info.branch)
        except (GitHubApiError, NetworkError) as e:
            raise NetworkError(f"Failed to fetch repository tree: {e}")

        return self._analyze_repo_structure(repo_info, tree)

    def _analyze_repo_structure(
        self,
        repo_info: RepoInfo,
        tree: list[dict],
    ) -> tuple[SkillSourceType, list[SkillCandidate]]:
        """Analyze repository structure to find skills."""
        paths = {item["path"] for item in tree if item["type"] == "blob"}

        candidates: list[SkillCandidate] = []

        # Check if subpath is specified
        if repo_info.subpath:
            subpath = repo_info.subpath.rstrip("/")
            if f"{subpath}/{self.SKILL_FILE}" in paths:
                candidate = self._create_candidate(repo_info, subpath)
                if candidate:
                    candidates.append(candidate)
            return SkillSourceType.SINGLE, candidates

        # Check for single skill at root
        if self.SKILL_FILE in paths:
            candidate = self._create_candidate(repo_info, "")
            if candidate:
                candidates.append(candidate)
            return SkillSourceType.SINGLE, candidates

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
            return SkillSourceType.MULTI, candidates

        return SkillSourceType.SINGLE, []

    def _create_candidate(self, repo_info: RepoInfo, path: str) -> SkillCandidate | None:
        """Create a skill candidate from repository path."""
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

        return SkillCandidate(
            name=name,
            path=path,
            description=description,
            has_metadata=has_metadata,
        )

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

    def install(
        self,
        url: str,
        candidate: SkillCandidate | None = None,
    ) -> SkillInstallResult:
        """Install a skill from a GitHub URL.

        Args:
            url: GitHub repository URL
            candidate: Pre-discovered candidate (optional)

        Returns:
            SkillInstallResult with success status and details
        """
        try:
            repo_info = self.parse_url(url)

            if repo_info.branch == "main":
                try:
                    default_branch = get_default_branch(repo_info.owner, repo_info.repo)
                    repo_info = RepoInfo(
                        owner=repo_info.owner,
                        repo=repo_info.repo,
                        branch=default_branch,
                        subpath=repo_info.subpath,
                    )
                except (GitHubApiError, NetworkError):
                    pass

            if candidate is None:
                source_type, candidates = self.discover_from_repo(url)

                if not candidates:
                    return SkillInstallResult(
                        success=False,
                        error="No valid skill found in repository. "
                              "A skill needs a SKILL.md file.",
                    )

                candidate = candidates[0]

            # Check if already installed
            target_path = self.skills_dir / candidate.name
            if target_path.exists():
                existing_info = self.get_installed_info(candidate.name)
                if existing_info and existing_info.source == repo_info.url:
                    return SkillInstallResult(
                        success=True,
                        skill_name=candidate.name,
                        skill_path=target_path,
                        already_installed=True,
                    )
                else:
                    return SkillInstallResult(
                        success=False,
                        error=f"Skill '{candidate.name}' already exists. "
                              "Use /skills update to update or uninstall first.",
                    )

            return self._download_and_install(repo_info, candidate)

        except ValueError as e:
            return SkillInstallResult(success=False, error=str(e))
        except NetworkError as e:
            return SkillInstallResult(success=False, error=f"Network error: {e}")
        except GitHubApiError as e:
            return SkillInstallResult(success=False, error=f"GitHub API error: {e}")
        except Exception as e:
            return SkillInstallResult(success=False, error=f"Installation failed: {e}")

    def install_multiple(
        self,
        url: str,
        candidates: list[SkillCandidate],
    ) -> list[SkillInstallResult]:
        """Install multiple skills from a multi-skill repository."""
        results = []
        repo_info = self.parse_url(url)

        if repo_info.branch == "main":
            try:
                default_branch = get_default_branch(repo_info.owner, repo_info.repo)
                repo_info = RepoInfo(
                    owner=repo_info.owner,
                    repo=repo_info.repo,
                    branch=default_branch,
                    subpath=repo_info.subpath,
                )
            except (GitHubApiError, NetworkError):
                pass

        for candidate in candidates:
            self._report(f"Installing {candidate.name}...")
            result = self._download_and_install(repo_info, candidate)
            results.append(result)

        return results

    def _download_and_install(
        self,
        repo_info: RepoInfo,
        candidate: SkillCandidate,
    ) -> SkillInstallResult:
        """Download and install a skill."""
        target_path = self.skills_dir / candidate.name
        method = InstallMethod.GIT

        if target_path.exists():
            shutil.rmtree(target_path)

        # Try git clone first
        if check_git_available():
            self._report("Cloning repository...")
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_repo_path = Path(tmpdir) / "repo"
                success, output = run_git_clone(repo_info, tmp_repo_path, shallow=True)

                if success:
                    if candidate.path:
                        src_path = tmp_repo_path / candidate.path
                    else:
                        src_path = tmp_repo_path

                    if (src_path / self.SKILL_FILE).exists():
                        shutil.copytree(src_path, target_path)
                    else:
                        return SkillInstallResult(
                            success=False,
                            error=f"SKILL.md not found in {candidate.path or 'root'}",
                        )
                else:
                    method = InstallMethod.ZIP
        else:
            method = InstallMethod.ZIP

        # ZIP download fallback
        if method == InstallMethod.ZIP:
            self._report("Downloading ZIP archive...")
            try:
                zip_data = download_repo_zip(
                    repo_info.owner, repo_info.repo, repo_info.branch
                )
            except NetworkError as e:
                return SkillInstallResult(success=False, error=str(e))

            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                strip_prefix = f"{repo_info.repo}-{repo_info.branch}/"
                extract_zip_to_dir(zip_data, tmp_path, strip_prefix)

                if candidate.path:
                    src_path = tmp_path / candidate.path
                else:
                    src_path = tmp_path

                if not (src_path / self.SKILL_FILE).exists():
                    return SkillInstallResult(
                        success=False,
                        error=f"SKILL.md not found in {candidate.path or 'root'}",
                    )

                if target_path.exists():
                    shutil.rmtree(target_path)
                shutil.copytree(src_path, target_path)

        # Verify installation
        if not (target_path / self.SKILL_FILE).exists():
            shutil.rmtree(target_path, ignore_errors=True)
            return SkillInstallResult(
                success=False,
                error="Installation failed: SKILL.md not found after extraction",
            )

        # Record installation
        self._record_installation(candidate, repo_info, method)

        return SkillInstallResult(
            success=True,
            skill_name=candidate.name,
            skill_path=target_path,
        )

    def update(self, skill_name: str) -> SkillInstallResult:
        """Update an installed skill to latest version."""
        info = self.get_installed_info(skill_name)
        if not info:
            return SkillInstallResult(
                success=False,
                error=f"Skill '{skill_name}' was not installed via /skills install",
            )

        try:
            repo_info = parse_github_url(info.source)
            candidate = SkillCandidate(
                name=skill_name,
                path=info.subpath or "",
            )

            target_path = self.skills_dir / skill_name
            if target_path.exists():
                shutil.rmtree(target_path)

            result = self._download_and_install(repo_info, candidate)
            result.updated = True
            return result

        except Exception as e:
            return SkillInstallResult(success=False, error=f"Update failed: {e}")

    def uninstall(self, skill_name: str) -> bool:
        """Remove an installed skill."""
        target_path = self.skills_dir / skill_name
        if not target_path.exists():
            return False

        shutil.rmtree(target_path)
        self._remove_from_registry(skill_name)
        return True

    def _report(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)

    # --- Registry management ---

    def _get_installed_file(self) -> Path:
        return self.skills_dir / INSTALLED_FILE

    def _load_installed_registry(self) -> dict:
        if self._installed_registry is not None:
            return self._installed_registry

        registry_file = self._get_installed_file()
        if registry_file.exists():
            try:
                content = registry_file.read_text(encoding="utf-8")
                self._installed_registry = json.loads(content)
            except Exception:
                self._installed_registry = {"skills": {}}
        else:
            self._installed_registry = {"skills": {}}

        return self._installed_registry

    def _save_installed_registry(self) -> None:
        if self._installed_registry is None:
            return

        registry_file = self._get_installed_file()
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        registry_file.write_text(
            json.dumps(self._installed_registry, indent=2),
            encoding="utf-8",
        )

    def _record_installation(
        self,
        candidate: SkillCandidate,
        repo_info: RepoInfo,
        method: InstallMethod,
    ) -> None:
        registry = self._load_installed_registry()

        registry["skills"][candidate.name] = {
            "source": repo_info.url,
            "installed_at": datetime.now().isoformat(),
            "method": method.value,
            "subpath": candidate.path or None,
        }

        self._save_installed_registry()

    def _remove_from_registry(self, skill_name: str) -> None:
        registry = self._load_installed_registry()
        if skill_name in registry.get("skills", {}):
            del registry["skills"][skill_name]
            self._save_installed_registry()

    def get_installed_info(self, skill_name: str) -> InstalledSkillInfo | None:
        """Get installation info for a skill."""
        registry = self._load_installed_registry()
        data = registry.get("skills", {}).get(skill_name)
        if not data:
            return None

        return InstalledSkillInfo(
            name=skill_name,
            source=data.get("source", ""),
            installed_at=data.get("installed_at", ""),
            method=data.get("method", "unknown"),
            subpath=data.get("subpath"),
        )

    def list_installed(self) -> list[InstalledSkillInfo]:
        """List all skills installed via /skills install."""
        registry = self._load_installed_registry()
        result = []

        for name, data in registry.get("skills", {}).items():
            result.append(InstalledSkillInfo(
                name=name,
                source=data.get("source", ""),
                installed_at=data.get("installed_at", ""),
                method=data.get("method", "unknown"),
                subpath=data.get("subpath"),
            ))

        return result
