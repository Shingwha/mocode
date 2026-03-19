"""RTK (Rust Token Killer) wrapper utilities

RTK is a high-performance CLI proxy that reduces LLM token consumption by 60-90%.
It compresses command output through smart filtering, grouping, truncation, and deduplication.

GitHub: https://github.com/rtk-ai/rtk
"""

import platform
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

# RTK installation directory (managed by MoCode)
RTK_INSTALL_DIR = Path.home() / ".mocode" / "bin"

# Global RTK path cache
_rtk_path: Optional[str] = None


def find_rtk() -> Optional[str]:
    """Find RTK executable

    Returns:
        RTK executable path, or None if not found
    """
    global _rtk_path

    # Return cached result
    if _rtk_path is not None:
        return _rtk_path if _rtk_path else None

    # 1. Check system PATH
    rtk = shutil.which("rtk")
    if rtk:
        _rtk_path = rtk
        return rtk

    # 2. Check MoCode installation directory (Windows priority)
    if platform.system() == "Windows":
        rtk_exe = RTK_INSTALL_DIR / "rtk.exe"
    else:
        rtk_exe = RTK_INSTALL_DIR / "rtk"

    if rtk_exe.exists():
        _rtk_path = str(rtk_exe)
        return _rtk_path

    # Mark as not found
    _rtk_path = ""
    return None


# Blacklist: commands that should NOT be wrapped
EXCLUDE_PREFIXES = (
    # Interactive commands
    "vim ",
    "nano ",
    "less ",
    "more ",
    "man ",
    # Real-time update commands
    "top ",
    "htop ",
    "watch ",
    # Avoid double-wrapping
    "rtk ",
)


def should_wrap(command: str) -> bool:
    """Check if a command should be wrapped with RTK

    Uses blacklist mode: wrap all commands except those in EXCLUDE_PREFIXES.

    Args:
        command: The command to check

    Returns:
        True if the command should be wrapped
    """
    stripped = command.strip()
    if not stripped:
        return False

    # Check against blacklist
    return not any(stripped.startswith(p) for p in EXCLUDE_PREFIXES)


def wrap(command: str) -> str:
    """Wrap a command with RTK

    Args:
        command: The original command

    Returns:
        The wrapped command, or original if RTK is not installed
    """
    rtk_path = find_rtk()
    if not rtk_path:
        return command

    # Use full path to avoid PATH issues
    # Convert to forward slashes for Git Bash compatibility
    rtk_path = rtk_path.replace("\\", "/")
    return f'"{rtk_path}" {command}'


def get_rtk_download_url() -> Optional[str]:
    """Get the download URL for RTK for the current platform

    Returns:
        Download URL, or None for unsupported platforms
    """
    system = platform.system()
    machine = platform.machine().lower()

    # GitHub releases URL
    base_url = "https://github.com/rtk-ai/rtk/releases/latest/download"

    if system == "Darwin":  # macOS
        if machine in ["arm64", "aarch64"]:
            return f"{base_url}/rtk-aarch64-apple-darwin.tar.gz"
        else:
            return f"{base_url}/rtk-x86_64-apple-darwin.tar.gz"
    elif system == "Linux":
        return f"{base_url}/rtk-x86_64-unknown-linux-gnu.tar.gz"
    elif system == "Windows":
        return f"{base_url}/rtk-x86_64-pc-windows-msvc.zip"

    return None


def get_install_command() -> str:
    """Get the platform-appropriate RTK install command

    Returns:
        Install command string
    """
    system = platform.system()

    if system == "Darwin":  # macOS
        return "brew install rtk"
    elif system == "Linux":
        return "curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/master/install.sh | sh"
    elif system == "Windows":
        return "Use /rtk install inside mocode CLI, or: cargo install --git https://github.com/rtk-ai/rtk"

    return "cargo install --git https://github.com/rtk-ai/rtk"


def install_rtk() -> bool:
    """Auto-install RTK (cross-platform)

    Windows: Download zip and extract to ~/.mocode/bin/
    macOS/Linux: Recommend package manager, return False to guide user

    Returns:
        True if installation succeeded
    """
    system = platform.system()

    if system != "Windows":
        # macOS/Linux recommend package manager
        return False

    url = get_rtk_download_url()
    if not url:
        return False

    try:
        # Create installation directory
        RTK_INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        # Download
        zip_path = RTK_INSTALL_DIR / "rtk.zip"
        urllib.request.urlretrieve(url, zip_path)

        # Extract
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(RTK_INSTALL_DIR)

        # Cleanup
        zip_path.unlink()

        # Verify
        rtk_exe = RTK_INSTALL_DIR / "rtk.exe"
        if rtk_exe.exists():
            # Reset cache
            global _rtk_path
            _rtk_path = str(rtk_exe)
            return True

    except Exception as e:
        print(f"RTK installation failed: {e}")

    return False


def check_installation() -> tuple[bool, str]:
    """Check RTK installation status

    Returns:
        (is_installed, message) tuple
    """
    rtk = find_rtk()
    if rtk:
        # Verify it's the correct RTK (has 'rtk gain' command)
        try:
            result = subprocess.run(
                [rtk, "gain"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True, "RTK installed and working"
            else:
                return False, "RTK installed but 'rtk gain' failed (might be wrong RTK)"
        except subprocess.TimeoutExpired:
            return False, "RTK found but 'rtk gain' timed out"
        except Exception as e:
            return False, f"RTK found but error: {e}"
    else:
        return False, f"RTK not installed. Install with: {get_install_command()}"


def get_gain() -> Optional[str]:
    """Get RTK token savings statistics

    Returns:
        Statistics string, or None if failed
    """
    rtk = find_rtk()
    if not rtk:
        return None

    try:
        result = subprocess.run(
            [rtk, "gain"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return result.stderr.strip() or None
    except Exception:
        return None
