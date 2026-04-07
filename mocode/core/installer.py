"""Base installer for GitHub-based skill/plugin installation."""

import json
import shutil
import tempfile
from abc import ABC, abstractmethod
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


# --- Shared types ---

class InstallMethod(Enum):
    """Installation method used."""
    GIT = "git"
    ZIP = "zip"


class SourceType(Enum):
    """Type of repository structure."""
    SINGLE = "single"
    MULTI = "multi"


@dataclass
class InstallCandidate:
    """A candidate item discovered in a remote repository."""
    name: str
    path: str  # Relative path within repo
    description: str = ""
    has_metadata: bool = False


@dataclass
class InstallResult:
    """Result of installation."""
    success: bool
    item_name: str | None = None
    item_path: Path | None = None
    error: str | None = None
    method: InstallMethod | None = None
    already_installed: bool = False
    updated: bool = False


@dataclass
class InstalledItemInfo:
    """Information about an installed item."""
    name: str
    source: str
    installed_at: str
    method: str
    subpath: str | None = None


INSTALLED_FILE = "installed.json"


class GitHubInstaller(ABC):
    """Base class for installing items from GitHub repositories.

    Subclasses implement abstract hooks to customize detection,
    candidate creation, and post-install behavior.
    """

    @property
    @abstractmethod
    def DETECT_FILE(self) -> str:
        """File that identifies an installable item (e.g. SKILL.md, plugin.py)."""

    @property
    @abstractmethod
    def registry_key(self) -> str:
        """Key used in the installed.json registry (e.g. 'skills', 'plugins')."""

    @property
    @abstractmethod
    def install_dir(self) -> Path:
        """Target directory for installation."""

    @abstractmethod
    def _analyze_repo_structure(
        self,
        repo_info: RepoInfo,
        tree: list[dict],
    ) -> tuple[SourceType, list[InstallCandidate]]:
        """Analyze repository tree to find installable items."""

    @abstractmethod
    def _create_candidate(
        self, repo_info: RepoInfo, path: str,
    ) -> InstallCandidate | None:
        """Create a candidate from a repository path."""

    def __init__(
        self,
        target_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        self._target_dir = target_dir
        self.progress_callback = progress_callback
        self._installed_registry: dict | None = None

    @property
    def _install_dir(self) -> Path:
        return self._target_dir or self.install_dir

    # --- Public API ---

    def parse_url(self, url: str) -> RepoInfo:
        return parse_github_url(url)

    def discover_from_repo(self, url: str) -> tuple[SourceType, list[InstallCandidate]]:
        repo_info = self.parse_url(url)
        self._report(f"Fetching repository info for {repo_info.owner}/{repo_info.repo}...")

        repo_info = self._resolve_default_branch(repo_info)

        try:
            tree = fetch_repo_tree(repo_info.owner, repo_info.repo, repo_info.branch)
        except (GitHubApiError, NetworkError) as e:
            raise NetworkError(f"Failed to fetch repository tree: {e}")

        return self._analyze_repo_structure(repo_info, tree)

    def install(
        self,
        url: str,
        candidate: InstallCandidate | None = None,
        **kwargs,
    ) -> InstallResult:
        try:
            repo_info = self.parse_url(url)
            repo_info = self._resolve_default_branch(repo_info)

            if candidate is None:
                source_type, candidates = self.discover_from_repo(url)

                if not candidates:
                    return InstallResult(
                        success=False,
                        error=self._not_found_error(),
                    )

                candidate = self._resolve_candidate_in_multi_repo(
                    source_type, candidates, **kwargs,
                )

            # Check if already installed
            target_path = self._install_dir / candidate.name
            if target_path.exists():
                existing_info = self.get_installed_info(candidate.name)
                if existing_info and existing_info.source == repo_info.url:
                    return InstallResult(
                        success=True,
                        item_name=candidate.name,
                        item_path=target_path,
                        already_installed=True,
                    )
                else:
                    return InstallResult(
                        success=False,
                        error=f"'{candidate.name}' already exists. "
                              f"Use update to update or uninstall first.",
                    )

            return self._download_and_install(repo_info, candidate)

        except ValueError as e:
            return InstallResult(success=False, error=str(e))
        except NetworkError as e:
            return InstallResult(success=False, error=f"Network error: {e}")
        except GitHubApiError as e:
            return InstallResult(success=False, error=f"GitHub API error: {e}")
        except Exception as e:
            return InstallResult(success=False, error=f"Installation failed: {e}")

    def install_multiple(
        self,
        url: str,
        candidates: list[InstallCandidate],
    ) -> list[InstallResult]:
        results = []
        repo_info = self.parse_url(url)
        repo_info = self._resolve_default_branch(repo_info)

        for candidate in candidates:
            self._report(f"Installing {candidate.name}...")
            result = self._download_and_install(repo_info, candidate)
            results.append(result)

        return results

    def update(self, name: str) -> InstallResult:
        info = self.get_installed_info(name)
        if not info:
            return InstallResult(
                success=False,
                error=f"'{name}' was not installed via install command",
            )

        try:
            repo_info = parse_github_url(info.source)
            candidate = InstallCandidate(
                name=name,
                path=info.subpath or "",
            )

            target_path = self._install_dir / name
            if target_path.exists():
                shutil.rmtree(target_path)

            result = self._download_and_install(repo_info, candidate)
            result.updated = True
            return result

        except Exception as e:
            return InstallResult(success=False, error=f"Update failed: {e}")

    def uninstall(self, name: str) -> bool:
        target_path = self._install_dir / name
        if not target_path.exists():
            return False

        shutil.rmtree(target_path)
        self._remove_from_registry(name)
        return True

    # --- Download & install ---

    def _download_and_install(
        self,
        repo_info: RepoInfo,
        candidate: InstallCandidate,
    ) -> InstallResult:
        target_path = self._install_dir / candidate.name
        method = InstallMethod.GIT

        if target_path.exists():
            shutil.rmtree(target_path)

        # Try git clone first
        if check_git_available():
            self._report("Cloning repository...")
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_repo_path = Path(tmpdir) / "repo"
                success, output = run_git_clone(repo_info, tmp_repo_path, shallow=True)

                if success:
                    src_path = tmp_repo_path / candidate.path if candidate.path else tmp_repo_path

                    if (src_path / self.DETECT_FILE).exists():
                        shutil.copytree(src_path, target_path)
                    else:
                        return InstallResult(
                            success=False,
                            error=f"{self.DETECT_FILE} not found in {candidate.path or 'root'}",
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
                return InstallResult(success=False, error=str(e))

            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                strip_prefix = f"{repo_info.repo}-{repo_info.branch}/"
                extract_zip_to_dir(zip_data, tmp_path, strip_prefix)

                src_path = tmp_path / candidate.path if candidate.path else tmp_path

                if not (src_path / self.DETECT_FILE).exists():
                    return InstallResult(
                        success=False,
                        error=f"{self.DETECT_FILE} not found in {candidate.path or 'root'}",
                    )

                if target_path.exists():
                    shutil.rmtree(target_path)
                shutil.copytree(src_path, target_path)

        # Verify installation
        if not (target_path / self.DETECT_FILE).exists():
            shutil.rmtree(target_path, ignore_errors=True)
            return InstallResult(
                success=False,
                error=f"Installation failed: {self.DETECT_FILE} not found after extraction",
            )

        # Post-install hook
        self._post_install(target_path, candidate)

        # Record installation
        self._record_installation(candidate, repo_info, method)

        return InstallResult(
            success=True,
            item_name=candidate.name,
            item_path=target_path,
            method=method,
        )

    # --- Overridable hooks ---

    def _not_found_error(self) -> str:
        return f"No valid item found in repository. A {self.DETECT_FILE} file is required."

    def _resolve_candidate_in_multi_repo(
        self,
        source_type: SourceType,
        candidates: list[InstallCandidate],
        **kwargs,
    ) -> InstallCandidate:
        """Select a candidate from discovery results. Default: first candidate."""
        return candidates[0]

    def _post_install(self, target_path: Path, candidate: InstallCandidate) -> None:
        """Hook for post-installation steps. Default: no-op."""

    def _build_registry_entry(
        self,
        candidate: InstallCandidate,
        repo_info: RepoInfo,
        method: InstallMethod,
    ) -> dict:
        """Build registry entry for recording installation."""
        return {
            "source": repo_info.url,
            "installed_at": datetime.now().isoformat(),
            "method": method.value,
            "subpath": candidate.path or None,
        }

    def _build_installed_info(self, name: str, data: dict) -> InstalledItemInfo:
        """Build InstalledItemInfo from registry data."""
        return InstalledItemInfo(
            name=name,
            source=data.get("source", ""),
            installed_at=data.get("installed_at", ""),
            method=data.get("method", "unknown"),
            subpath=data.get("subpath"),
        )

    # --- Registry management ---

    def _get_installed_file(self) -> Path:
        return self._install_dir / INSTALLED_FILE

    def _load_installed_registry(self) -> dict:
        if self._installed_registry is not None:
            return self._installed_registry

        registry_file = self._get_installed_file()
        if registry_file.exists():
            try:
                content = registry_file.read_text(encoding="utf-8")
                self._installed_registry = json.loads(content)
            except Exception:
                self._installed_registry = {self.registry_key: {}}
        else:
            self._installed_registry = {self.registry_key: {}}

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
        candidate: InstallCandidate,
        repo_info: RepoInfo,
        method: InstallMethod,
    ) -> None:
        registry = self._load_installed_registry()
        registry[self.registry_key][candidate.name] = self._build_registry_entry(
            candidate, repo_info, method,
        )
        self._save_installed_registry()

    def _remove_from_registry(self, name: str) -> None:
        registry = self._load_installed_registry()
        if name in registry.get(self.registry_key, {}):
            del registry[self.registry_key][name]
            self._save_installed_registry()

    def get_installed_info(self, name: str) -> InstalledItemInfo | None:
        registry = self._load_installed_registry()
        data = registry.get(self.registry_key, {}).get(name)
        if not data:
            return None
        return self._build_installed_info(name, data)

    def list_installed(self) -> list[InstalledItemInfo]:
        registry = self._load_installed_registry()
        result = []
        for name, data in registry.get(self.registry_key, {}).items():
            result.append(self._build_installed_info(name, data))
        return result

    # --- Helpers ---

    def _resolve_default_branch(self, repo_info: RepoInfo) -> RepoInfo:
        """Resolve default branch if 'main' is used as placeholder."""
        if repo_info.branch != "main":
            return repo_info
        try:
            default_branch = get_default_branch(repo_info.owner, repo_info.repo)
            return RepoInfo(
                owner=repo_info.owner,
                repo=repo_info.repo,
                branch=default_branch,
                subpath=repo_info.subpath,
            )
        except (GitHubApiError, NetworkError):
            return repo_info

    def _report(self, message: str) -> None:
        if self.progress_callback:
            self.progress_callback(message)
