"""Plugin installation from GitHub repositories."""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

import yaml

from .url_utils import (
    RepoInfo,
    parse_github_url,
    fetch_repo_tree,
    fetch_repo_info,
    fetch_raw_file,
    download_repo_zip,
    extract_zip_to_dir,
    run_git_clone,
    check_git_available,
    get_default_branch,
    GitHubApiError,
    NetworkError,
)
from .venv_manager import PluginVenvManager, VenvError
from ..paths import PLUGINS_DIR


class InstallMethod(Enum):
    """Installation method used."""
    GIT = "git"
    ZIP = "zip"


class PluginSourceType(Enum):
    """Type of plugin repository."""
    SINGLE = "single"  # Root has plugin.py
    MULTI = "multi"    # plugins/ subdir contains plugin folders


@dataclass
class PluginCandidate:
    """A candidate plugin discovered in a remote repository."""
    name: str
    path: str  # Relative path within repo
    description: str = ""
    version: str = "1.0.0"
    has_metadata: bool = False


@dataclass
class InstallResult:
    """Result of plugin installation."""
    success: bool
    plugin_name: str | None = None
    plugin_path: Path | None = None
    error: str | None = None
    method: InstallMethod | None = None
    already_installed: bool = False
    updated: bool = False


@dataclass
class InstalledPluginInfo:
    """Information about an installed plugin."""
    name: str
    source: str
    installed_at: str
    method: str
    version: str = "unknown"
    commit: str | None = None
    subpath: str | None = None


INSTALLED_FILE = "installed.json"


class PluginInstaller:
    """Handles plugin installation from GitHub repositories.

    Features:
    - Parse GitHub URLs (supports owner/repo, full URLs, tree/blob refs)
    - Detect single vs multi-plugin repositories
    - Support git clone (shallow) and ZIP download fallback
    - Track installed plugins for update/uninstall
    """

    PLUGIN_FILE = "plugin.py"
    MANIFEST_FILE = "plugin.yaml"

    def __init__(
        self,
        plugins_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        """Initialize installer.

        Args:
            plugins_dir: Target directory for plugin installation
            progress_callback: Optional callback for progress messages
        """
        self.plugins_dir = plugins_dir or PLUGINS_DIR
        self.progress_callback = progress_callback
        self._installed_registry: dict | None = None

    def parse_url(self, url: str) -> RepoInfo:
        """Parse GitHub URL into structured information.

        Args:
            url: GitHub URL or owner/repo shorthand

        Returns:
            RepoInfo with parsed components

        Raises:
            ValueError: If URL format is invalid
        """
        return parse_github_url(url)

    def discover_from_repo(self, url: str) -> tuple[PluginSourceType, list[PluginCandidate]]:
        """Discover plugins available in a repository.

        Args:
            url: GitHub URL

        Returns:
            Tuple of (source_type, list of candidates)
        """
        repo_info = self.parse_url(url)
        self._report(f"Fetching repository info for {repo_info.owner}/{repo_info.repo}...")

        # Get default branch if not specified
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
                pass  # Use default "main"

        # Fetch repository tree
        try:
            tree = fetch_repo_tree(repo_info.owner, repo_info.repo, repo_info.branch)
        except (GitHubApiError, NetworkError) as e:
            raise NetworkError(f"Failed to fetch repository tree: {e}")

        # Analyze structure
        return self._analyze_repo_structure(repo_info, tree)

    def _analyze_repo_structure(
        self,
        repo_info: RepoInfo,
        tree: list[dict],
    ) -> tuple[PluginSourceType, list[PluginCandidate]]:
        """Analyze repository structure to find plugins.

        Args:
            repo_info: Repository info
            tree: Repository tree from GitHub API

        Returns:
            Tuple of (source_type, list of candidates)
        """
        # Build path set for quick lookup
        paths = {item["path"] for item in tree if item["type"] == "blob"}
        dirs = {item["path"] for item in tree if item["type"] == "tree"}

        candidates: list[PluginCandidate] = []

        # Check if subpath is specified
        if repo_info.subpath:
            # Only look in the specified subpath
            subpath = repo_info.subpath.rstrip("/")
            if f"{subpath}/{self.PLUGIN_FILE}" in paths:
                candidate = self._create_candidate(repo_info, subpath)
                if candidate:
                    candidates.append(candidate)
            return PluginSourceType.SINGLE, candidates

        # Check for single plugin at root
        if self.PLUGIN_FILE in paths:
            candidate = self._create_candidate(repo_info, "")
            if candidate:
                candidates.append(candidate)
            return PluginSourceType.SINGLE, candidates

        # Check for plugins/ directory with multiple plugins
        plugins_dir_paths = ["plugins", "Plugins"]
        for pd in plugins_dir_paths:
            if pd in dirs:
                # Find plugin directories under plugins/
                plugin_dirs = set()
                prefix = f"{pd}/"
                for item in tree:
                    if item["type"] == "tree" and item["path"].startswith(prefix):
                        # Get direct subdirectories
                        parts = item["path"][len(prefix):].split("/")
                        if parts and parts[0]:
                            plugin_dirs.add(f"{prefix}{parts[0]}")

                # Check each directory for plugin.py
                for plugin_dir in sorted(plugin_dirs):
                    if f"{plugin_dir}/{self.PLUGIN_FILE}" in paths:
                        candidate = self._create_candidate(repo_info, plugin_dir)
                        if candidate:
                            candidates.append(candidate)

                if candidates:
                    return PluginSourceType.MULTI, candidates

        # No plugins found
        return PluginSourceType.SINGLE, []

    def _create_candidate(self, repo_info: RepoInfo, path: str) -> PluginCandidate | None:
        """Create a plugin candidate from repository path.

        Args:
            repo_info: Repository info
            path: Path to plugin directory (relative to repo root)

        Returns:
            PluginCandidate or None if invalid
        """
        name = Path(path).name if path else repo_info.repo
        manifest_path = f"{path}/{self.MANIFEST_FILE}" if path else self.MANIFEST_FILE

        # Try to fetch metadata
        description = ""
        version = "1.0.0"
        has_metadata = False

        try:
            manifest_content = fetch_raw_file(
                repo_info.owner, repo_info.repo, manifest_path, repo_info.branch
            )
            if manifest_content:
                data = yaml.safe_load(manifest_content)
                if data:
                    has_metadata = True
                    description = data.get("description", "")
                    version = data.get("version", "1.0.0")
                    # Use name from manifest if available
                    if data.get("name"):
                        name = data["name"]
        except Exception:
            pass

        return PluginCandidate(
            name=name,
            path=path,
            description=description,
            version=version,
            has_metadata=has_metadata,
        )

    def install(
        self,
        url: str,
        plugin_name: str | None = None,
        candidate: PluginCandidate | None = None,
    ) -> InstallResult:
        """Install a plugin from a GitHub URL.

        Args:
            url: GitHub repository URL
            plugin_name: Specific plugin name (for multi-plugin repos)
            candidate: Pre-discovered candidate (optional)

        Returns:
            InstallResult with success status and details
        """
        try:
            repo_info = self.parse_url(url)

            # Get default branch if not specified
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
                    pass  # Use default "main"

            # Discover plugins if not provided
            if candidate is None:
                source_type, candidates = self.discover_from_repo(url)

                if not candidates:
                    return InstallResult(
                        success=False,
                        error="No valid plugin found in repository. "
                              "A plugin needs a plugin.py file.",
                    )

                if source_type == PluginSourceType.SINGLE:
                    candidate = candidates[0]
                else:
                    # Multi-plugin repo, find by name
                    if plugin_name:
                        candidate = next(
                            (c for c in candidates if c.name == plugin_name),
                            None
                        )
                        if not candidate:
                            return InstallResult(
                                success=False,
                                error=f"Plugin '{plugin_name}' not found in repository. "
                                      f"Available: {', '.join(c.name for c in candidates)}",
                            )
                    else:
                        # Use first candidate
                        candidate = candidates[0]

            # Check if already installed
            target_path = self.plugins_dir / candidate.name
            if target_path.exists():
                existing_info = self.get_installed_info(candidate.name)
                if existing_info and existing_info.source == url:
                    return InstallResult(
                        success=True,
                        plugin_name=candidate.name,
                        plugin_path=target_path,
                        already_installed=True,
                    )
                else:
                    # Different source or manually installed
                    return InstallResult(
                        success=False,
                        error=f"Plugin '{candidate.name}' already exists. "
                              "Use /plugin update to update or uninstall first.",
                    )

            # Download and install
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
        candidates: list[PluginCandidate],
    ) -> list[InstallResult]:
        """Install multiple plugins from a multi-plugin repository.

        Args:
            url: GitHub repository URL
            candidates: List of candidates to install

        Returns:
            List of InstallResult for each plugin
        """
        results = []
        repo_info = self.parse_url(url)

        # Get default branch if not specified
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
                pass  # Use default "main"

        for candidate in candidates:
            self._report(f"Installing {candidate.name}...")
            result = self._download_and_install(repo_info, candidate)
            results.append(result)

        return results

    def _download_and_install(
        self,
        repo_info: RepoInfo,
        candidate: PluginCandidate,
    ) -> InstallResult:
        """Download and install a plugin.

        Args:
            repo_info: Repository info
            candidate: Plugin candidate to install

        Returns:
            InstallResult
        """
        target_path = self.plugins_dir / candidate.name
        method = InstallMethod.GIT

        # Clean target if exists
        if target_path.exists():
            shutil.rmtree(target_path)

        # Try git clone first
        if check_git_available():
            self._report("Cloning repository...")

            # Always clone to temp dir first, then copy (avoids .git permission issues)
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_repo_path = Path(tmpdir) / "repo"
                success, output = run_git_clone(repo_info, tmp_repo_path, shallow=True)

                if success:
                    # Determine source path
                    if candidate.path:
                        src_path = tmp_repo_path / candidate.path
                    else:
                        src_path = tmp_repo_path

                    if (src_path / self.PLUGIN_FILE).exists():
                        shutil.copytree(src_path, target_path)
                    else:
                        return InstallResult(
                            success=False,
                            error=f"Plugin file not found in {candidate.path or 'root'}",
                        )
                else:
                    # Fallback to ZIP
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

            # Extract to temp directory first
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)
                strip_prefix = f"{repo_info.repo}-{repo_info.branch}/"
                extract_zip_to_dir(zip_data, tmp_path, strip_prefix)

                # Determine source path
                if candidate.path:
                    src_path = tmp_path / candidate.path
                else:
                    src_path = tmp_path

                if not (src_path / self.PLUGIN_FILE).exists():
                    return InstallResult(
                        success=False,
                        error=f"Plugin file not found in {candidate.path or 'root'}",
                    )

                # Clean and copy to target
                if target_path.exists():
                    shutil.rmtree(target_path)
                shutil.copytree(src_path, target_path)

        # Verify installation
        if not (target_path / self.PLUGIN_FILE).exists():
            shutil.rmtree(target_path, ignore_errors=True)
            return InstallResult(
                success=False,
                error="Installation failed: plugin.py not found after extraction",
            )

        # Set up isolated venv if plugin has dependencies
        self._setup_plugin_venv(target_path)

        # Record installation
        self._record_installation(candidate, repo_info, method)

        return InstallResult(
            success=True,
            plugin_name=candidate.name,
            plugin_path=target_path,
            method=method,
        )

    def update(self, plugin_name: str) -> InstallResult:
        """Update an installed plugin to latest version.

        Args:
            plugin_name: Name of plugin to update

        Returns:
            InstallResult
        """
        info = self.get_installed_info(plugin_name)
        if not info:
            return InstallResult(
                success=False,
                error=f"Plugin '{plugin_name}' was not installed via /plugin install",
            )

        # Disable plugin if enabled
        # Note: Caller should handle this

        # Re-download
        try:
            repo_info = parse_github_url(info.source)
            candidate = PluginCandidate(
                name=plugin_name,
                path=info.subpath or "",
            )

            # Remove old installation
            target_path = self.plugins_dir / plugin_name
            if target_path.exists():
                shutil.rmtree(target_path)

            # Re-install
            result = self._download_and_install(repo_info, candidate)
            result.updated = True
            return result

        except Exception as e:
            return InstallResult(success=False, error=f"Update failed: {e}")

    def uninstall(self, plugin_name: str) -> bool:
        """Remove an installed plugin.

        Args:
            plugin_name: Name of plugin to remove

        Returns:
            True if successfully removed
        """
        target_path = self.plugins_dir / plugin_name
        if not target_path.exists():
            return False

        # Remove directory
        shutil.rmtree(target_path)

        # Remove from registry
        self._remove_from_registry(plugin_name)

        return True

    def _report(self, message: str) -> None:
        """Report progress message."""
        if self.progress_callback:
            self.progress_callback(message)

    def _setup_plugin_venv(self, plugin_path: Path) -> None:
        """Set up isolated venv for a plugin after installation.

        Args:
            plugin_path: Path to the plugin directory
        """
        manifest_path = plugin_path / self.MANIFEST_FILE
        if not manifest_path.exists():
            return

        try:
            content = manifest_path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not data or not isinstance(data, dict):
                return

            dependencies = data.get("dependencies", [])
            if not dependencies:
                return

            self._report(f"Setting up isolated environment for {plugin_path.name}...")
            venv_manager = PluginVenvManager(plugin_path)
            venv_manager.create()
            venv_manager.install_dependencies(dependencies)
        except Exception:
            # Venv setup is best-effort, don't fail installation
            pass

    # Registry management

    def _get_installed_file(self) -> Path:
        """Get path to installed plugins registry."""
        return self.plugins_dir / INSTALLED_FILE

    def _load_installed_registry(self) -> dict:
        """Load installed plugins registry."""
        if self._installed_registry is not None:
            return self._installed_registry

        registry_file = self._get_installed_file()
        if registry_file.exists():
            try:
                content = registry_file.read_text(encoding="utf-8")
                self._installed_registry = json.loads(content)
            except Exception:
                self._installed_registry = {"plugins": {}}
        else:
            self._installed_registry = {"plugins": {}}

        return self._installed_registry

    def _save_installed_registry(self) -> None:
        """Save installed plugins registry."""
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
        candidate: PluginCandidate,
        repo_info: RepoInfo,
        method: InstallMethod,
    ) -> None:
        """Record plugin installation for future updates."""
        registry = self._load_installed_registry()

        registry["plugins"][candidate.name] = {
            "source": repo_info.url,
            "installed_at": datetime.now().isoformat(),
            "method": method.value,
            "version": candidate.version,
            "subpath": candidate.path or None,
        }

        self._save_installed_registry()

    def _remove_from_registry(self, plugin_name: str) -> None:
        """Remove plugin from registry."""
        registry = self._load_installed_registry()
        if plugin_name in registry.get("plugins", {}):
            del registry["plugins"][plugin_name]
            self._save_installed_registry()

    def get_installed_info(self, plugin_name: str) -> InstalledPluginInfo | None:
        """Get installation info for a plugin.

        Args:
            plugin_name: Plugin name

        Returns:
            InstalledPluginInfo or None if not tracked
        """
        registry = self._load_installed_registry()
        data = registry.get("plugins", {}).get(plugin_name)
        if not data:
            return None

        return InstalledPluginInfo(
            name=plugin_name,
            source=data.get("source", ""),
            installed_at=data.get("installed_at", ""),
            method=data.get("method", "unknown"),
            version=data.get("version", "unknown"),
            subpath=data.get("subpath"),
        )

    def list_installed(self) -> list[InstalledPluginInfo]:
        """List all plugins installed via /plugin install.

        Returns:
            List of InstalledPluginInfo
        """
        registry = self._load_installed_registry()
        result = []

        for name, data in registry.get("plugins", {}).items():
            result.append(InstalledPluginInfo(
                name=name,
                source=data.get("source", ""),
                installed_at=data.get("installed_at", ""),
                method=data.get("method", "unknown"),
                version=data.get("version", "unknown"),
                subpath=data.get("subpath"),
            ))

        return result
