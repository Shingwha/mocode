"""CLI 入口 - 异步版本"""

import asyncio
import sys


def main():
    """主入口"""
    from .cli import CLIApp

    app = CLIApp()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass  # 静默退出
    return 0


if __name__ == "__main__":
    exit(main())
