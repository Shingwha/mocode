"""Installing plugins — fetch, place, and give the plugin its environment.

``mocode plugin install <source>`` is the one command: fetch the plugin (a
git URL or a local directory), place it under a plugins root named by its
manifest, and — when it declares dependencies — materialise its environment
in the same breath. ``list`` and ``remove`` manage what install produced;
``sync`` re-runs the environment half alone, after dependencies were edited.

Installation is an act of trust: a plugin is code mocode imports and runs on
its next start. Nothing here executes plugin code — fetching and placing are
file operations — but that is the whole trust decision, made by the command's
caller.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .env import PluginVenv, PluginVenvError
from .loader import MANIFEST, discover, read_manifest


class PluginInstallError(Exception):
    """Why a plugin could not be installed, synced, listed or removed."""


@dataclass(frozen=True)
class Installed:
    """What ``install_plugin`` produced.

    ``env_warning`` is set when the plugin is on disk but its environment is
    not: installed and loadable, its dependencies missing until
    ``mocode plugin sync`` succeeds.
    """

    name: str
    directory: Path
    env_warning: str | None = None


@dataclass(frozen=True)
class PluginListing:
    """One plugin as ``list_plugins`` reports it."""

    name: str
    version: str
    source: str
    #: ``"own env"`` (declared and materialised), ``"declared"`` (needs a
    #: sync) or ``"shared"`` (no declaration — mocode's environment is its
    #: environment).
    env: str


def install_plugin(source: str, *, root: Path) -> Installed:
    """Install from *source* — a git URL or a local directory — under *root*.

    The manifest decides the directory name, so what lands on disk is
    addressable by the name every other command uses. A plugin that declares
    dependencies gets its environment in the same breath; a sync that fails
    leaves the plugin installed, with ``env_warning`` saying why.
    """
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mocode-plugin-") as tmp:
        fetched = _fetch(source, Path(tmp))
        spec = read_manifest(fetched / MANIFEST)
        if spec is None:
            raise PluginInstallError(
                f"{source}: not a plugin — no readable {MANIFEST} at its root"
            )
        target = root / spec.name
        if target.exists():
            raise PluginInstallError(
                f"'{spec.name}' is already installed ({target}) — "
                "remove it first: mocode plugin remove " + spec.name
            )
        shutil.copytree(fetched, target)

    venv = PluginVenv(target)
    warning = None
    if venv.declared:
        try:
            venv.sync()
        except PluginVenvError as e:
            warning = (
                f"{spec.name} installed, but its environment did not sync "
                f"({e}) — retry with: mocode plugin sync {spec.name}"
            )
    return Installed(name=spec.name, directory=target, env_warning=warning)


def sync_plugin(name: str, *, dirs: list[Path]) -> str:
    """Materialise the environment of plugin *name* found across *dirs*."""
    directory = _directory_of(name, dirs)
    try:
        return PluginVenv(directory).sync()
    except PluginVenvError as e:
        raise PluginInstallError(str(e)) from e


def remove_plugin(name: str, *, roots: list[Path]) -> Path:
    """Delete plugin *name* — its directory, environment included."""
    directory = _directory_of(name, dirs=roots)
    _rmtree(directory)
    return directory


def list_plugins(roots: list[Path]) -> list[PluginListing]:
    """Every discoverable plugin across *roots*, with its environment state."""
    out = []
    for spec in discover(list(roots)):
        env = (
            PluginVenv(spec.directory).describe()
            if spec.directory is not None
            else "shared"
        )
        out.append(
            PluginListing(
                name=spec.name, version=spec.version, source=spec.source, env=env
            )
        )
    return out


# ── Helpers ─────────────────────────────────────────────────


def _rmtree(directory: Path) -> None:
    """Delete a plugin directory however read-only its files are.

    A git-installed plugin carries ``.git``, and git marks its pack files
    read-only — ``shutil.rmtree`` on Windows refuses to unlink those. Clear
    the flag and retry, per file.
    """

    def _clear_readonly(func, path, _exc) -> None:
        os.chmod(path, stat.S_IWRITE)
        func(path)

    if sys.version_info >= (3, 12):
        shutil.rmtree(directory, onexc=_clear_readonly)
    else:
        shutil.rmtree(directory, onerror=_clear_readonly)


def _fetch(source: str, tmp: Path) -> Path:
    """A local directory holding the plugin-to-be: cloned, or the source itself."""
    if _is_git(source):
        target = tmp / "repo"
        result = subprocess.run(
            ["git", "clone", "--depth", "1", source, str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not target.is_dir():
            detail = (result.stderr or "").strip().splitlines()
            raise PluginInstallError(
                f"git clone failed: {detail[-1] if detail else source}"
            )
        return target
    directory = Path(source).expanduser()
    if directory.is_dir():
        return directory
    raise PluginInstallError(
        f"{source}: not a git URL and not a local directory"
    )


def _is_git(source: str) -> bool:
    return "://" in source or source.startswith("git@") or source.endswith(".git")


def _directory_of(name: str, dirs: list[Path]) -> Path:
    """The on-disk directory of plugin *name*, or raise trying.

    The manifest is the name authority everywhere: what ``list`` shows is
    what ``sync`` and ``remove`` address. A single-file plugin has no
    directory and therefore no environment of its own.
    """
    for spec in discover(list(dirs)):
        if spec.name == name:
            if spec.directory is None:
                raise PluginInstallError(
                    f"'{name}' is a single-file plugin — it ships no "
                    "environment; its packages belong to mocode's own"
                )
            return spec.directory
    raise PluginInstallError(
        f"no plugin named '{name}' under "
        + ", ".join(str(d) for d in dirs or [])
    )
