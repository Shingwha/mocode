"""CLI entry point for MoCode v0.2

Supports:
  mocode gateway [--type <type>]   Launch gateway (auto-discovers enabled channels)
  mocode web [--host HOST] [--port PORT]   Launch web backend (placeholder)
  mocode                        Launch CLI mode (placeholder)
"""

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
    """Run gateway mode: mocode gateway [--type <type>]"""
    from .gateway.app import GatewayApp

    gateway_type = None  # None = auto-discover from config
    for i, arg in enumerate(args):
        if arg == "--type" and i + 1 < len(args):
            gateway_type = args[i + 1]

    try:
        asyncio.run(GatewayApp(gateway_type).run())
    except KeyboardInterrupt:
        pass
    return 0


def _run_web(args: list[str]) -> int:
    """Run web backend: mocode web [--host HOST] [--port PORT]

    Placeholder — web backend has not been ported to v0.2 yet.
    """
    print("Web backend is not yet available in v0.2", file=sys.stderr)
    return 1


def _run_cli() -> int:
    """Run CLI mode.

    Placeholder — CLI REPL has not been ported to v0.2 yet.
    """
    print("CLI mode is not yet available in v0.2", file=sys.stderr)
    print("Use: mocode gateway --type weixin", file=sys.stderr)
    return 1


if __name__ == "__main__":
    exit(main())
