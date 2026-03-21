"""ripgrep wrapper utilities

ripgrep (rg) is an extremely fast search tool, 10-100x faster than grep.
GitHub: https://github.com/BurntSushi/ripgrep
"""

import platform
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

# MoCode installation directory
MOCODE_BIN_DIR = Path.home() / ".mocode" / "bin"

# Global ripgrep path cache
_rg_path: Optional[str] = None


def find_ripgrep() -> Optional[str]:
    """Find ripgrep executable

    Priority: ~/.mocode/bin > system PATH

    Returns:
        ripgrep executable path, or None if not found
    """
    global _rg_path

    # Return cached result
    if _rg_path is not None:
        return _rg_path if _rg_path else None

    # 1. Check MoCode installation directory first (higher priority)
    if platform.system() == "Windows":
        rg_exe = MOCODE_BIN_DIR / "rg.exe"
    else:
        rg_exe = MOCODE_BIN_DIR / "rg"

    if rg_exe.exists():
        _rg_path = str(rg_exe)
        return _rg_path

    # 2. Fallback to system PATH
    rg = shutil.which("rg")
    if rg:
        _rg_path = rg
        return rg

    # Mark as not found
    _rg_path = ""
    return None


def run_ripgrep(pattern: str, path: str = ".", limit: int = 100) -> Optional[str]:
    """Run ripgrep search and return formatted results

    Args:
        pattern: Search pattern (regex)
        path: Search path (default: current directory)
        limit: Maximum number of results

    Returns:
        Formatted results string, or None if ripgrep unavailable/error
    """
    rg = find_ripgrep()
    if not rg:
        return None

    # ripgrep args:
    # -n: show line numbers
    # --: end of options (pattern follows)
    cmd = [rg, "-n", "--", pattern, path]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        lines = result.stdout.strip().split("\n")
        if not lines or lines == [""]:
            return "none"

        # Limit results
        return "\n".join(lines[:limit])
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None


def check_installation() -> tuple[bool, str]:
    """Check ripgrep installation status

    Returns:
        (is_installed, message) tuple
    """
    rg = find_ripgrep()
    if rg:
        try:
            result = subprocess.run(
                [rg, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                version = result.stdout.strip().split()[1] if result.stdout.strip() else "unknown"
                # Check if it's MoCode installed or system
                if str(MOCODE_BIN_DIR) in rg:
                    return True, f"ripgrep {version} (MoCode)"
                return True, f"ripgrep {version} (system)"
            return True, "ripgrep installed"
        except Exception:
            return True, "ripgrep installed"
    return False, "ripgrep not installed"


def get_download_url() -> Optional[str]:
    """Get the download URL for ripgrep for the current platform

    Returns:
        Download URL, or None for unsupported platforms
    """
    system = platform.system()
    machine = platform.machine().lower()

    # GitHub releases URL
    base_url = "https://github.com/BurntSushi/ripgrep/releases/latest/download"

    if system == "Darwin":  # macOS
        if machine in ["arm64", "aarch64"]:
            return f"{base_url}/ripgrep-14.1.0-aarch64-apple-darwin.tar.gz"
        else:
            return f"{base_url}/ripgrep-14.1.0-x86_64-apple-darwin.tar.gz"
    elif system == "Linux":
        return f"{base_url}/ripgrep-14.1.0-x86_64-unknown-linux-musl.tar.gz"
    elif system == "Windows":
        return f"{base_url}/ripgrep-14.1.0-x86_64-pc-windows-msvc.zip"

    return None


def get_install_command() -> str:
    """Get the platform-appropriate ripgrep install command

    Returns:
        Install command string
    """
    system = platform.system()

    if system == "Darwin":  # macOS
        return "brew install ripgrep"
    elif system == "Linux":
        return "sudo apt install ripgrep  # or: cargo install ripgrep"
    elif system == "Windows":
        return "Use /ripgrep install inside mocode CLI, or: cargo install ripgrep"

    return "cargo install ripgrep"


def install_ripgrep() -> bool:
    """Auto-install ripgrep (cross-platform)

    Windows: Download zip and extract to ~/.mocode/bin/
    macOS/Linux: Recommend package manager, return False to guide user

    Returns:
        True if installation succeeded
    """
    import tarfile
    import tempfile

    system = platform.system()

    if system != "Windows":
        # macOS/Linux recommend package manager
        return False

    url = get_download_url()
    if not url:
        return False

    try:
        # Create installation directory
        MOCODE_BIN_DIR.mkdir(parents=True, exist_ok=True)

        # Download
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            urllib.request.urlretrieve(url, tmp.name)
            zip_path = Path(tmp.name)

        # Extract
        with zipfile.ZipFile(zip_path, "r") as z:
            # Find rg.exe in the archive
            for name in z.namelist():
                if name.endswith("rg.exe"):
                    # Extract to temp first
                    z.extract(name, MOCODE_BIN_DIR)
                    # Move to bin directory
                    extracted = MOCODE_BIN_DIR / name
                    target = MOCODE_BIN_DIR / "rg.exe"
                    if extracted != target:
                        extracted.rename(target)
                    break

        # Cleanup
        zip_path.unlink()

        # Verify
        rg_exe = MOCODE_BIN_DIR / "rg.exe"
        if rg_exe.exists():
            # Reset cache
            global _rg_path
            _rg_path = str(rg_exe)
            return True

    except Exception as e:
        print(f"ripgrep installation failed: {e}")

    return False
