"""CLI 入口 - 异步版本"""

import asyncio
import sys


def main():
    """主入口"""
    # 检查是否有 gateway 子命令
    if len(sys.argv) > 1 and sys.argv[1] == "gateway":
        return run_gateway()

    # 正常 CLI 模式
    from .cli import AsyncApp

    app = AsyncApp()
    asyncio.run(app.run())
    return 0


def run_gateway():
    """运行 Gateway"""
    from .gateway import run_gateway as _run_gateway

    asyncio.run(_run_gateway())
    return 0


if __name__ == "__main__":
    exit(main())
