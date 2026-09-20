"""Installing plugins — fetch, place, and give the plugin its environment.

``mocode plugin install <source>`` is the one command: fetch the plugin (a
git URL or a local directory — the URL may name a subdirectory of a
repository, the shape a plugin collection takes), place it under a plugins
root named by its manifest, and — when it declares dependencies —
materialise its environment in the same breath. ``list`` and ``remove``
manage what install produced; ``sync`` re-runs the environment half alone,
after dependencies were edited.

Installation is an act of trust: a plugin is code mocode imports and runs on
its next start. Nothing here executes plugin code — fetching and placing are
file operations — but that is the whole trust decision, made by the command's
caller.
"""

from __future__ import annotations

import os
import re
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


#: A tree URL — what a browser shows for a directory on GitHub or GitLab —
#: names a repository, a ref, and a path inside it.
_TREE_URL = re.compile(
    r"^(?P<repo>https?://[^/]+/[^/]+/[^/]+?)(?:\.git)?/(?:-/)?tree/(?P<ref>[^/]+)(?P<path>/.+)$"
)


def _split_source(source: str) -> tuple[str, str | None, str | None]:
    """Split an install source into ``(repository, ref, subdirectory)``.

    A tree URL carries its own ref: ``https://github.com/o/r/tree/main/sub``
    installs the ``sub`` directory of that repository at ``main``. Any git
    URL may instead take a ``#subdir`` fragment — a path on the default
    branch. Local paths and plain git URLs come back untouched.
    """
    match = _TREE_URL.match(source)
    if match:
        path = match.group("path").strip("/")
        return match.group("repo"), match.group("ref"), path or None
    if _is_git(source) and "#" in source:
        url, _, fragment = source.partition("#")
        return url, None, fragment.strip("/") or None
    return source, None, None


def _subdirectory(root: Path, subdir: str, source: str) -> Path:
    """The directory *subdir* names inside a freshly cloned *root*.

    The source chose the path, so it is checked like one: ``..`` never
    escapes the clone, and a name that is no directory names nothing
    installable.
    """
    if ".." in subdir.split("/"):
        raise PluginInstallError(f"{source}: '{subdir}' may not contain '..'")
    directory = (root / subdir).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise PluginInstallError(f"{source}: '{subdir}' escapes the repository")
    if not directory.is_dir():
        raise PluginInstallError(
            f"{source}: no directory '{subdir}' in the repository"
        )
    return directory


def _fetch(source: str, tmp: Path) -> Path:
    """A local directory holding the plugin-to-be: cloned, or the source itself."""
    url, ref, subdir = _split_source(source)
    if _is_git(url):
        target = tmp / "repo"
        argv = ["git", "clone", "--depth", "1"]
        if ref:
            argv += ["-b", ref]
        result = subprocess.run([*argv, url, str(target)], capture_output=True, text=True)
        if result.returncode != 0 or not target.is_dir():
            detail = (result.stderr or "").strip().splitlines()
            raise PluginInstallError(
                f"git clone failed: {detail[-1] if detail else source}"
            )
        return _subdirectory(target, subdir, source) if subdir else target
    directory = Path(url).expanduser()
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
