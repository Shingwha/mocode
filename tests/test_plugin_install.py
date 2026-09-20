"""Plugin management — install, sync, remove, list, and the CLI subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mocode.cli_args import parse_args
from mocode.host.plugin.install import Source
from mocode.host.plugin import install as plugins
from mocode.host.plugin.env import PluginVenvError

from .test_plugins import _write_plugin


def _source_plugin(root: Path, name: str, *, with_pyproject: bool = False) -> Path:
    """A plugin directory outside any plugins root, ready to be installed."""
    plugin_dir = _write_plugin(
        root, name, "from mocode.plugins import Plugin\nplugin = Plugin()\n"
    )
    if with_pyproject:
        (plugin_dir / "pyproject.toml").write_text(
            "[project]\n"
            f'name = "{name}"\n'
            'version = "0.1.0"\n'
            'dependencies = []\n',
            encoding="utf-8",
        )
    return plugin_dir


def _installed_flag(root: Path, name: str) -> Path:
    return root / name / "plugin.json"


class TestInstall:
    def test_local_directory_is_copied_under_its_manifest_name(self, tmp_path):
        source = _source_plugin(tmp_path / "src", "acme")
        root = tmp_path / "root"

        installed = plugins.install_plugin(str(source), root=root)

        assert installed.name == "acme"
        assert installed.directory == root / "acme"
        assert _installed_flag(root, "acme").is_file()
        assert source.is_dir()  # a copy, never consuming the source

    def test_without_a_manifest_it_is_not_a_plugin(self, tmp_path):
        source = tmp_path / "src" / "loose"
        source.mkdir(parents=True)
        root = tmp_path / "root"

        with pytest.raises(plugins.PluginInstallError, match="not a plugin"):
            plugins.install_plugin(str(source), root=root)

    def test_an_installed_name_is_refused(self, tmp_path):
        source = _source_plugin(tmp_path / "src", "acme")
        root = tmp_path / "root"
        plugins.install_plugin(str(source), root=root)

        with pytest.raises(plugins.PluginInstallError, match="already installed"):
            plugins.install_plugin(str(source), root=root)

    def test_git_source_clones_then_places(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        argv_seen = []

        def fake_run(argv, **kwargs):
            argv_seen.append(argv)
            # A clone that "succeeded": the manifest lands where git put it.
            target = Path(argv[5])
            target.mkdir(parents=True)
            (target / "plugin.json").write_text(
                json.dumps({"name": "from-git", "description": ""}), encoding="utf-8"
            )
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(plugins.subprocess, "run", fake_run)

        installed = plugins.install_plugin("https://example.com/x.git", root=root)

        assert argv_seen[0][:4] == ["git", "clone", "--depth", "1"]
        assert installed.name == "from-git"
        assert _installed_flag(root, "from-git").is_file()

    def test_a_failed_clone_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            plugins.subprocess,
            "run",
            lambda *a, **k: SimpleNamespace(returncode=128, stderr="fatal: no repo", stdout=""),
        )
        with pytest.raises(plugins.PluginInstallError, match="no repo"):
            plugins.install_plugin("https://example.com/nope.git", root=tmp_path)

    def test_an_unknown_source_is_rejected(self, tmp_path):
        with pytest.raises(plugins.PluginInstallError, match="not a git URL"):
            plugins.install_plugin(str(tmp_path / "missing"), root=tmp_path)


class TestSubdirectorySources:
    """A git URL may name a subdirectory — the multi-plugin repository shape."""

    def test_the_source_shapes(self):
        tree = Source.parse("https://github.com/o/mocode-plugins/tree/main/kimi-search")
        assert (tree.url, tree.ref, tree.subdir, tree.local) == (
            "https://github.com/o/mocode-plugins", "main", "kimi-search", False,
        )
        gitlab = Source.parse("https://gitlab.com/o/r/-/tree/v1/plugins/acme")
        assert (gitlab.url, gitlab.ref, gitlab.subdir) == (
            "https://gitlab.com/o/r", "v1", "plugins/acme",
        )
        fragment = Source.parse("https://example.com/x.git#plugins/acme")
        assert (fragment.url, fragment.ref, fragment.subdir) == (
            "https://example.com/x.git", None, "plugins/acme",
        )
        local = Source.parse("/local/acme")
        assert (local.url, local.local) == ("/local/acme", True)

    def test_a_subdirectory_installs_from_the_clone(self, tmp_path, monkeypatch):
        argv_seen = []

        def fake_run(argv, **kwargs):
            argv_seen.append(argv)
            # With -b <ref> the target shifts one argument later.
            plugin = Path(argv[7]) / "plugins" / "acme"
            plugin.mkdir(parents=True)
            (plugin / "plugin.json").write_text(
                json.dumps({"name": "acme", "description": ""}), encoding="utf-8"
            )
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(plugins.subprocess, "run", fake_run)

        installed = plugins.install_plugin(
            "https://github.com/o/mocode-plugins/tree/main/plugins/acme",
            root=tmp_path,
        )

        assert argv_seen[0][4:6] == ["-b", "main"]
        assert installed.name == "acme"
        assert _installed_flag(tmp_path, "acme").is_file()

    def test_a_subdirectory_may_not_escape_the_clone(self, tmp_path, monkeypatch):
        def fake_run(argv, **kwargs):
            Path(argv[5]).mkdir(parents=True)
            return SimpleNamespace(returncode=0, stderr="", stdout="")

        monkeypatch.setattr(plugins.subprocess, "run", fake_run)

        with pytest.raises(plugins.PluginInstallError, match="may not contain"):
            plugins.install_plugin("https://example.com/x.git#../acme", root=tmp_path)


class TestInstallSyncsTheEnvironment:
    def test_a_declared_plugin_syncs_in_the_same_breath(self, tmp_path, monkeypatch):
        source = _source_plugin(tmp_path / "src", "acme", with_pyproject=True)
        synced = []
        monkeypatch.setattr(
            plugins.PluginVenv, "sync", lambda self: synced.append(self.plugin_dir)
        )

        installed = plugins.install_plugin(str(source), root=tmp_path / "root")

        assert synced == [tmp_path / "root" / "acme"]
        assert installed.env_warning is None

    def test_an_undeclared_plugin_never_syncs(self, tmp_path, monkeypatch):
        source = _source_plugin(tmp_path / "src", "acme")
        synced = []
        monkeypatch.setattr(
            plugins.PluginVenv, "sync", lambda self: synced.append(self.plugin_dir)
        )

        plugins.install_plugin(str(source), root=tmp_path / "root")

        assert synced == []

    def test_a_failed_sync_installs_anyway_with_a_warning(self, tmp_path, monkeypatch):
        source = _source_plugin(tmp_path / "src", "acme", with_pyproject=True)
        monkeypatch.setattr(
            plugins.PluginVenv,
            "sync",
            lambda self: (_ for _ in ()).throw(PluginVenvError("uv not found")),
        )

        installed = plugins.install_plugin(str(source), root=tmp_path / "root")

        assert _installed_flag(tmp_path / "root", "acme").is_file()
        assert "mocode plugin sync" in installed.env_warning


class TestSyncRemoveList:
    def test_sync_resolves_the_plugin_across_roots(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        _source_plugin(root, "acme", with_pyproject=True)
        monkeypatch.setattr(
            plugins.PluginVenv, "sync", lambda self: f"{self.plugin_dir.name}: ready"
        )

        report = plugins.sync_plugin("acme", dirs=[tmp_path / "other", root])

        assert report == "acme: ready"

    def test_sync_names_an_unknown_plugin(self, tmp_path):
        with pytest.raises(plugins.PluginInstallError, match="no plugin named"):
            plugins.sync_plugin("ghost", dirs=[tmp_path])

    def test_remove_deletes_the_directory_with_its_environment(self, tmp_path):
        root = tmp_path / "root"
        _source_plugin(root, "acme", with_pyproject=True)
        (root / "acme" / ".venv").mkdir()

        removed = plugins.remove_plugin("acme", roots=[root])

        assert removed == root / "acme"
        assert not (root / "acme").exists()

    def test_remove_handles_read_only_files(self, tmp_path):
        """A git-installed plugin carries read-only .git packs (Windows)."""
        root = tmp_path / "root"
        plugin = _source_plugin(root, "acme")
        locked = plugin / "pack.idx"
        locked.write_text("", encoding="utf-8")
        locked.chmod(0o444)

        plugins.remove_plugin("acme", roots=[root])

        assert not plugin.exists()

    def test_remove_names_an_unknown_plugin(self, tmp_path):
        with pytest.raises(plugins.PluginInstallError, match="no plugin named"):
            plugins.remove_plugin("ghost", roots=[tmp_path])

    def test_list_reports_environment_state_across_roots(self, tmp_path):
        project = tmp_path / "project"
        user = tmp_path / "user"
        _source_plugin(project, "acme", with_pyproject=True)
        (project / "acme" / ".venv").mkdir()  # declared, not yet materialised
        ready = _source_plugin(user, "bored", with_pyproject=True)
        (ready / ".venv").mkdir()
        (ready / ".venv" / "pyvenv.cfg").write_text("", encoding="utf-8")
        (user / "plain.py").write_text("", encoding="utf-8")  # single-file plugin

        names = {
            item.name: item
            for item in plugins.list_plugins([project, user])
        }

        assert names["acme"].env == "declared"
        assert names["bored"].env == "own env"
        assert names["plain"].env == "shared"


class TestTheCliCommand:
    def test_the_subcommand_shapes(self):
        install = parse_args(["plugin", "install", "some/path", "--project"])
        assert (install.command, install.plugin_command) == ("plugin", "install")
        assert install.source == "some/path"
        assert install.project is True

        assert parse_args(["plugin", "sync", "acme"]).plugin_command == "sync"
        assert parse_args(["plugin", "list"]).plugin_command == "list"
        assert parse_args(["plugin", "remove", "acme"]).plugin_command == "remove"

        # The oneshot path is unchanged.
        assert parse_args(["-p", "hi"]).command is None
        assert parse_args(["-p", "hi"]).prompt == "hi"

    def test_install_prints_the_report_and_exits_zero(self, monkeypatch, capsys):
        from mocode import main

        monkeypatch.setattr(
            "mocode.host.plugin.install.install_plugin",
            lambda source, root: plugins.Installed(
                name="acme", directory=Path(root) / "acme"
            ),
        )
        args = parse_args(["plugin", "install", "some/path"])

        code = main._run_plugin(args)

        assert code == 0
        out = capsys.readouterr().out
        assert "installed acme" in out
        assert "Restart MoCode" in out

    def test_a_failure_exits_one_and_says_why(self, monkeypatch, capsys):
        from mocode import main

        def boom(name, *, dirs):
            raise plugins.PluginInstallError("no plugin named 'ghost'")

        monkeypatch.setattr("mocode.host.plugin.install.sync_plugin", boom)
        args = parse_args(["plugin", "sync", "ghost"])

        code = main._run_plugin(args)

        assert code == 1
        assert "no plugin named" in capsys.readouterr().err
