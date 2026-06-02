"""MoCode 0.3 — CLI entry point.

Usage:
    mocode              Launch interactive CLI
    mocode -c "prompt"  One-shot mode (TODO)
"""

import sys

from .app.cli import CLIApp


def main():
    app = CLIApp()
    if app.config is None:
        print("Config not found. Create ~/.mocode/config.json first.", file=sys.stderr)
        sys.exit(1)
    app.run()


if __name__ == "__main__":
    main()
