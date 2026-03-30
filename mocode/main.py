"""CLI entry - async version"""

import asyncio
import sys


def main():
    """Main entry point"""
    if len(sys.argv) > 1 and sys.argv[1] == "gateway":
        return _run_gateway(sys.argv[2:])
    return _run_cli()


def _run_gateway(args: list[str]) -> int:
    """Run gateway mode: mocode gateway --type <type>"""
    from .gateway.app import GatewayApp

    gateway_type = "weixin"
    for i, arg in enumerate(args):
        if arg == "--type" and i + 1 < len(args):
            gateway_type = args[i + 1]

    try:
        asyncio.run(GatewayApp(gateway_type).run())
    except KeyboardInterrupt:
        pass
    return 0


def _run_cli() -> int:
    """Run CLI mode"""
    from .cli import CLIApp

    app = CLIApp()
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    exit(main())
