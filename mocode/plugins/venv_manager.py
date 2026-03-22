"""Plugin virtual environment management."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


class VenvError(Exception):
    """Raised when venv operations fail."""
    pass


def _find_uv_executable() -> str:
    """Find the uv executable.

    Returns:
        Path to uv executable

    Raises:
        VenvError: If uv is not found
    """
    # Try to find uv in PATH
    if sys.platform == "win32":
        uv_exe = shutil.which("uv") or shutil.which("uv.exe")
        if uv_exe:
            return uv_exe
        # Check common install locations
        local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
        uv_path = local_appdata / "uv" / "uv.exe"
        if uv_path.exists():
            return str(uv_path)
    else:
        uv_exe = shutil.which("uv")
        if uv_exe:
            return uv_exe

    raise VenvError("uv not found in PATH. Please install uv.")


class PluginVenvManager:
    """Manages isolated virtual environments for plugins.

    Each plugin can have its own .venv/ directory with dependencies
    isolated from other plugins and the host application.
    """

    VENV_DIR = ".venv"

    def __init__(self, plugin_path: Path):
        """Initialize venv manager for a plugin.

        Args:
            plugin_path: Path to the plugin directory
        """
        self.plugin_path = plugin_path
        self._venv_path = plugin_path / self.VENV_DIR

    @property
    def venv_path(self) -> Path:
        """Get the path to the virtual environment."""
        return self._venv_path

    @property
    def exists(self) -> bool:
        """Check if the virtual environment exists."""
        return self._venv_path.exists() and (self._venv_path / "pyvenv.cfg").exists()

    @property
    def python_executable(self) -> Path:
        """Get the python executable path for this venv.

        Returns:
            Path to python.exe (Windows) or python (Unix)
        """
        if sys.platform == "win32":
            return self._venv_path / "Scripts" / "python.exe"
        return self._venv_path / "bin" / "python"

    def create(self) -> bool:
        """Create a new virtual environment for this plugin.

        Returns:
            True if successful

        Raises:
            VenvError: If venv creation fails
        """
        if self.exists:
            return True

        try:
            uv_path = _find_uv_executable()
            result = subprocess.run(
                [uv_path, "venv", str(self._venv_path)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise VenvError(f"Failed to create venv: {result.stderr}")
            return True
        except FileNotFoundError:
            # Fall back to standard venv if uv not available
            import venv
            venv.create(self._venv_path, with_pip=True)
            return True

    def install_dependencies(self, dependencies: list[str]) -> bool:
        """Install dependencies into the plugin's venv.

        Args:
            dependencies: List of dependency specs (e.g., ["requests>=2.28", "numpy"])

        Returns:
            True if successful

        Raises:
            VenvError: If installation fails
        """
        if not dependencies:
            return True

        if not self.exists:
            self.create()

        try:
            uv_path = _find_uv_executable()
            result = subprocess.run(
                [
                    uv_path, "pip", "install",
                    "--python", str(self.python_executable),
                    *dependencies
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise VenvError(f"Failed to install dependencies: {result.stderr}")
            return True
        except FileNotFoundError:
            raise VenvError("uv not available for dependency installation")

    def cleanup(self) -> bool:
        """Remove the plugin's virtual environment.

        Returns:
            True if successful
        """
        if self._venv_path.exists():
            shutil.rmtree(self._venv_path)
        return True

    def get_site_packages_path(self) -> Optional[Path]:
        """Get the site-packages path for this venv.

        Returns:
            Path to site-packages directory, or None if venv doesn't exist
        """
        if not self.exists:
            return None

        venv_python = str(self.python_executable)

        result = subprocess.run(
            [venv_python, "-c", "import site; print(site.getsitepackages())"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            import ast
            sites = ast.literal_eval(result.stdout.strip())
            # On Windows with uv, site.getsitepackages() may return multiple paths.
            # The last one is typically the proper site-packages.
            # Alternatively, use Lib/site-packages directly for Windows.
            if sys.platform == "win32":
                return self._venv_path / "Lib" / "site-packages"
            return Path(sites[-1]) if sites else None
        return None
