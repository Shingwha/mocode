"""PluginVenv — the environment a plugin carries, and how it attaches."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from mocode.host.plugin.env import PluginVenv
from mocode.host.plugin.host import load_plugins
from mocode.host.config import Config

from .test_plugins import _write_plugin


@pytest.fixture(autouse=True)
def _restore_sys_path():
    """Attaching is for the process; a test undoes what it attached."""
    before = list(sys.path)
    yield
    sys.path[:] = before


def _fake_venv(plugin_dir: Path, *, windows: bool = True) -> Path:
    """A venv that exists (pyvenv.cfg) with one package in site-packages."""
    site = (
        plugin_dir / ".venv" / "Lib" / "site-packages"
        if windows
        else plugin_dir / ".venv" / "lib" / "python3.12" / "site-packages"
    )
    site.mkdir(parents=True)
    (plugin_dir / ".venv" / "pyvenv.cfg").write_text("", encoding="utf-8")
    (site / "venv_pack.py").write_text("VALUE = 42\n", encoding="utf-8")
    return site


class TestSitePackages:
    def test_windows_layout(self, tmp_path: Path):
        site = _fake_venv(tmp_path, windows=True)
        assert PluginVenv(tmp_path).site_packages() == site

    def test_posix_layout(self, tmp_path: Path):
        site = _fake_venv(tmp_path, windows=False)
        assert PluginVenv(tmp_path).site_packages() == site

    def test_no_venv_means_none(self, tmp_path: Path):
        assert PluginVenv(tmp_path).site_packages() is None

    def test_venv_without_packages_yet(self, tmp_path: Path):
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("", encoding="utf-8")
        assert PluginVenv(tmp_path).site_packages() is None


class TestAttach:
    def test_appends_at_the_end_of_sys_path(self, tmp_path: Path):
        site = _fake_venv(tmp_path)

        attached = PluginVenv(tmp_path).attach()

        assert attached == site
        assert sys.path[-1] == str(site)

    def test_attach_is_idempotent(self, tmp_path: Path):
        _fake_venv(tmp_path)
        venv = PluginVenv(tmp_path)

        venv.attach()
        second = venv.attach()

        assert second is None
        assert sys.path.count(str(venv.site_packages())) == 1

    def test_no_venv_attaches_nothing(self, tmp_path: Path):
        assert PluginVenv(tmp_path).attach() is None


class TestTheBridge:
    def test_a_plugin_imports_from_its_own_environment(self, tmp_path: Path):
        """Attach happens before the import — the module resolves from .venv."""
        plugins_dir = tmp_path / "plugins"
        plugin_dir = _write_plugin(
            plugins_dir,
            "venvuser",
            """
            import venv_pack

            from mocode.plugins import Plugin

            plugin = Plugin()
            plugin.name = "venvuser"
            """,
        )
        _fake_venv(plugin_dir)

        loaded = load_plugins(plugin_dirs=[plugins_dir], config=Config())

        assert [p.name for p in loaded.plugins if p.name == "venvuser"] == ["venvuser"]

    def test_a_missing_dependency_says_what_to_do(self, tmp_path: Path, capsys):
        _write_plugin(
            tmp_path,
            "needful",
            """
            import a_package_mocode_does_not_ship_12345

            from mocode.plugins import Plugin

            plugin = Plugin()
            """,
        )

        load_plugins(plugin_dirs=[tmp_path], config=Config())

        err = capsys.readouterr().err
        assert "mocode plugin sync" in err
        assert "docs/plugins.md" in err


class TestSync:
    def _declared(self, tmp_path: Path) -> Path:
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname='x'\nversion='0'\n", encoding="utf-8"
        )
        return tmp_path

    def test_runs_uv_sync_in_the_plugin_directory(self, tmp_path, monkeypatch):
        plugin_dir = self._declared(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "C:/fake/uv")
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

        monkeypatch.setattr("mocode.host.plugin.env.subprocess.run", fake_run)

        report = PluginVenv(plugin_dir).sync()

        argv, kwargs = calls[0]
        assert argv == ["C:/fake/uv", "sync"]
        assert kwargs["cwd"] == plugin_dir
        assert "environment ready" in report

    def test_failure_carries_uvs_last_word(self, tmp_path, monkeypatch):
        plugin_dir = self._declared(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "C:/fake/uv")
        result = type(
            "R", (), {"returncode": 2, "stderr": "line\nno version pinned", "stdout": ""}
        )()
        monkeypatch.setattr("mocode.host.plugin.env.subprocess.run", lambda *a, **k: result)

        with pytest.raises(Exception, match="no version pinned"):
            PluginVenv(plugin_dir).sync()

    def test_without_uv_it_says_how_to_get_it(self, tmp_path, monkeypatch):
        plugin_dir = self._declared(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: None)

        with pytest.raises(Exception, match="install it"):
            PluginVenv(plugin_dir).sync()

    def test_without_a_declaration_it_names_the_file(self, tmp_path):
        with pytest.raises(Exception, match="pyproject.toml"):
            PluginVenv(tmp_path).sync()


class TestDescribe:
    def test_the_three_states(self, tmp_path: Path):
        plain = tmp_path / "plain"
        plain.mkdir()
        declared = tmp_path / "declared"
        declared.mkdir()
        (declared / "pyproject.toml").write_text("", encoding="utf-8")
        ready = tmp_path / "ready"
        ready.mkdir()
        (ready / "pyproject.toml").write_text("", encoding="utf-8")
        (ready / ".venv" / "pyvenv.cfg").parent.mkdir(parents=True, exist_ok=True)
        (ready / ".venv" / "pyvenv.cfg").write_text("", encoding="utf-8")

        assert PluginVenv(plain).describe() == "shared"
        assert PluginVenv(declared).describe() == "declared"
        assert PluginVenv(ready).describe() == "own env"
