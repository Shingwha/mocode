"""CLI entry - async version"""

import asyncio
import sys


def main():
    """Main entry point"""
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "gateway":
            return _run_gateway(sys.argv[2:])
        if cmd == "web":
            return _run_web(sys.argv[2:])
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


def _run_web(args: list[str]) -> int:
    """Run web backend: mocode web [--host HOST] [--port PORT]"""
    import uvicorn
    from .web import create_app

    host = "127.0.0.1"
    port = 8000
    i = 0
    while i < len(args):
        if args[i] == "--host" and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        else:
            i += 1

    uvicorn.run(create_app(), host=host, port=port)
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
