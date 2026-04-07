"""Plugin installation from GitHub repositories."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from ..core.installer import (
    GitHubInstaller,
    SourceType,
    InstallCandidate,
    InstallResult,
    InstallMethod,
    InstalledItemInfo,
)
from .url_utils import RepoInfo, fetch_raw_file
from .venv_manager import PluginVenvManager
from ..paths import PLUGINS_DIR


@dataclass
class InstalledPluginInfo(InstalledItemInfo):
    """Information about an installed plugin."""
    version: str = "unknown"
    commit: str | None = None


class PluginInstaller(GitHubInstaller):
    """Handles plugin installation from GitHub repositories.

    Features:
    - Parse GitHub URLs (supports owner/repo, full URLs, tree/blob refs)
    - Detect single vs multi-plugin repositories
    - Support git clone (shallow) and ZIP download fallback
    - Track installed plugins for update/uninstall
    """

    PLUGIN_FILE = "plugin.py"
    MANIFEST_FILE = "plugin.yaml"

    @property
    def DETECT_FILE(self) -> str:
        return self.PLUGIN_FILE

    @property
    def registry_key(self) -> str:
        return "plugins"

    @property
    def install_dir(self) -> Path:
        return PLUGINS_DIR

    def __init__(
        self,
        plugins_dir: Path | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ):
        super().__init__(target_dir=plugins_dir, progress_callback=progress_callback)
        self._plugin_versions: dict[str, str] = {}

    def _analyze_repo_structure(
        self,
        repo_info: RepoInfo,
        tree: list[dict],
    ) -> tuple[SourceType, list[InstallCandidate]]:
        paths = {item["path"] for item in tree if item["type"] == "blob"}
        dirs = {item["path"] for item in tree if item["type"] == "tree"}

        candidates: list[InstallCandidate] = []

        # Check if subpath is specified
        if repo_info.subpath:
            subpath = repo_info.subpath.rstrip("/")
            if f"{subpath}/{self.PLUGIN_FILE}" in paths:
                candidate = self._create_candidate(repo_info, subpath)
                if candidate:
                    candidates.append(candidate)
            return SourceType.SINGLE, candidates

        # Check for single plugin at root
        if self.PLUGIN_FILE in paths:
            candidate = self._create_candidate(repo_info, "")
            if candidate:
                candidates.append(candidate)
            return SourceType.SINGLE, candidates

        # Check for plugins/ directory with multiple plugins
        plugins_dir_paths = ["plugins", "Plugins"]
        for pd in plugins_dir_paths:
            if pd in dirs:
                plugin_dirs = set()
                prefix = f"{pd}/"
                for item in tree:
                    if item["type"] == "tree" and item["path"].startswith(prefix):
                        parts = item["path"][len(prefix):].split("/")
                        if parts and parts[0]:
                            plugin_dirs.add(f"{prefix}{parts[0]}")

                for plugin_dir in sorted(plugin_dirs):
                    if f"{plugin_dir}/{self.PLUGIN_FILE}" in paths:
                        candidate = self._create_candidate(repo_info, plugin_dir)
                        if candidate:
                            candidates.append(candidate)

                if candidates:
                    return SourceType.MULTI, candidates

        return SourceType.SINGLE, []

    def _create_candidate(self, repo_info: RepoInfo, path: str) -> InstallCandidate | None:
        name = Path(path).name if path else repo_info.repo
        manifest_path = f"{path}/{self.MANIFEST_FILE}" if path else self.MANIFEST_FILE

        description = ""
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
                    if data.get("name"):
                        name = data["name"]
                    # Store version as side-channel for registry entry
                    self._plugin_versions[name] = version
        except Exception:
            pass

        return InstallCandidate(
            name=name,
            path=path,
            description=description,
            has_metadata=has_metadata,
        )

    def _not_found_error(self) -> str:
        return "No valid plugin found in repository. A plugin needs a plugin.py file."

    def _resolve_candidate_in_multi_repo(
        self,
        source_type: SourceType,
        candidates: list[InstallCandidate],
        **kwargs,
    ) -> InstallCandidate:
        """For multi-plugin repos, select by plugin_name kwarg or first candidate."""
        plugin_name = kwargs.get("plugin_name")
        if source_type == SourceType.MULTI and plugin_name:
            candidate = next(
                (c for c in candidates if c.name == plugin_name), None
            )
            if not candidate:
                # Build error via InstallResult — caller checks error
                available = ", ".join(c.name for c in candidates)
                raise ValueError(
                    f"Plugin '{plugin_name}' not found in repository. "
                    f"Available: {available}"
                )
            return candidate
        return candidates[0]

    def _post_install(self, target_path: Path, candidate: InstallCandidate) -> None:
        """Set up isolated venv if plugin has dependencies."""
        manifest_path = target_path / self.MANIFEST_FILE
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

            self._report(f"Setting up isolated environment for {target_path.name}...")
            venv_manager = PluginVenvManager(target_path)
            venv_manager.create()
            venv_manager.install_dependencies(dependencies)
        except Exception:
            pass

    def _build_registry_entry(
        self,
        candidate: InstallCandidate,
        repo_info: RepoInfo,
        method: InstallMethod,
    ) -> dict:
        entry = super()._build_registry_entry(candidate, repo_info, method)
        entry["version"] = self._plugin_versions.get(candidate.name, "1.0.0")
        return entry

    def _build_installed_info(self, name: str, data: dict) -> InstalledPluginInfo:
        return InstalledPluginInfo(
            name=name,
            source=data.get("source", ""),
            installed_at=data.get("installed_at", ""),
            method=data.get("method", "unknown"),
            version=data.get("version", "unknown"),
            subpath=data.get("subpath"),
        )


# Backward-compatible aliases
PluginCandidate = InstallCandidate
PluginSourceType = SourceType
