"""MoCode 0.3 — CLI entry point.

Usage:
    mocode              Launch interactive CLI
    mocode -c "prompt"  One-shot mode (TODO)
"""

from .app.cli import CLIApp

def main():
    CLIApp().run()

if __name__ == "__main__":
    main()
