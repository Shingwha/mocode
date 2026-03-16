"""CLI 入口 - 异步版本"""

import asyncio

from .cli import AsyncApp


def main():
    """主入口"""
    app = AsyncApp()
    asyncio.run(app.run())
    return 0


if __name__ == "__main__":
    exit(main())