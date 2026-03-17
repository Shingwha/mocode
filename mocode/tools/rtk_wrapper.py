"""RTK (Rust Token Killer) 命令包装器

RTK 是一个高性能 CLI 代理工具，可以将 LLM token 消耗降低 60-90%。
它通过智能过滤、分组、截断、去重等策略压缩命令输出。

GitHub: https://github.com/rtk-ai/rtk
"""

import platform
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

# RTK 安装目录（MoCode 管理）
RTK_INSTALL_DIR = Path.home() / ".mocode" / "bin"

# 全局 RTK 路径缓存
_rtk_path: Optional[str] = None


def find_rtk() -> Optional[str]:
    """查找 RTK 可执行文件

    Returns:
        RTK 可执行文件路径，未找到则返回 None
    """
    global _rtk_path

    # 返回缓存结果
    if _rtk_path is not None:
        return _rtk_path if _rtk_path else None

    # 1. 检查系统 PATH
    rtk = shutil.which("rtk")
    if rtk:
        _rtk_path = rtk
        return rtk

    # 2. 检查 MoCode 安装目录（Windows 优先）
    if platform.system() == "Windows":
        rtk_exe = RTK_INSTALL_DIR / "rtk.exe"
    else:
        rtk_exe = RTK_INSTALL_DIR / "rtk"

    if rtk_exe.exists():
        _rtk_path = str(rtk_exe)
        return _rtk_path

    # 标记为未找到
    _rtk_path = ""
    return None


def should_wrap_command(command: str, enabled_commands: list[str]) -> bool:
    """判断命令是否应该被 RTK 包装

    Args:
        command: 要执行的命令
        enabled_commands: RTK 支持的命令列表

    Returns:
        是否应该包装
    """
    stripped = command.strip()
    if not stripped:
        return False

    # 避免重复包装
    if stripped.startswith("rtk "):
        return False

    # 避免包装交互式命令
    interactive_prefixes = ("vim ", "nano ", "less ", "more ", "man ", "top ", "htop ")
    if any(stripped.startswith(prefix) for prefix in interactive_prefixes):
        return False

    cmd_parts = stripped.split()
    if not cmd_parts:
        return False

    cmd_start = cmd_parts[0]
    # 处理带空格的命令如 "git status"
    first_two = " ".join(cmd_parts[:2]) if len(cmd_parts) >= 2 else cmd_start

    for pattern in enabled_commands:
        if cmd_start == pattern or first_two == pattern:
            return True
    return False


def wrap_with_rtk(command: str) -> str:
    """用 RTK 包装命令

    Args:
        command: 原始命令

    Returns:
        包装后的命令，如果 RTK 未安装则返回原命令
    """
    rtk_path = find_rtk()
    if not rtk_path:
        return command
    # 使用完整路径，避免 PATH 问题
    # 转换为正斜杠以兼容 Git Bash
    rtk_path = rtk_path.replace("\\", "/")
    return f'"{rtk_path}" {command}'


def get_rtk_download_url() -> Optional[str]:
    """获取当前平台的 RTK 下载链接

    Returns:
        下载链接，不支持的平台返回 None
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
    """获取适合当前平台的 RTK 安装命令

    Returns:
        安装命令字符串
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
    """自动安装 RTK（跨平台）

    Windows: 下载 zip 解压到 ~/.mocode/bin/
    macOS/Linux: 建议使用包管理器，返回 False 引导用户

    Returns:
        安装是否成功
    """
    system = platform.system()

    if system != "Windows":
        # macOS/Linux 建议使用包管理器
        return False

    url = get_rtk_download_url()
    if not url:
        return False

    try:
        # 创建安装目录
        RTK_INSTALL_DIR.mkdir(parents=True, exist_ok=True)

        # 下载
        zip_path = RTK_INSTALL_DIR / "rtk.zip"
        urllib.request.urlretrieve(url, zip_path)

        # 解压
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(RTK_INSTALL_DIR)

        # 清理
        zip_path.unlink()

        # 验证
        rtk_exe = RTK_INSTALL_DIR / "rtk.exe"
        if rtk_exe.exists():
            # 重置缓存
            global _rtk_path
            _rtk_path = str(rtk_exe)
            return True

    except Exception as e:
        print(f"RTK installation failed: {e}")

    return False


def check_rtk_installation() -> tuple[bool, str]:
    """检查 RTK 安装状态

    Returns:
        (is_installed, message) 元组
    """
    rtk = find_rtk()
    if rtk:
        # 验证是否是正确的 RTK（有 rtk gain 命令）
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


def get_rtk_gain() -> Optional[str]:
    """获取 RTK 的 token 节省统计

    Returns:
        统计信息字符串，失败返回 None
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
