"""CLI argument parsing — runs before any framework imports."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mocode",
        description="MoCode — Lean agent framework",
    )
    parser.add_argument(
        "-p", "--prompt",
        help="Non-interactive mode: run one query and exit. "
             "Pipe stdin to provide context.",
    )

    # ── Workflow subcommands ───────────────────────────────────
    wf_sub = parser.add_subparsers(dest="command")

    wf_parser = wf_sub.add_parser("workflow", help="Manage and run YAML workflows")
    wf_action = wf_parser.add_subparsers(dest="workflow_action")

    # workflow run <name> [--fg] [--_run-id <id>] [key=value...]
    run_p = wf_action.add_parser("run", help="Run a workflow (background by default)")
    run_p.add_argument("name", help="Workflow name")
    run_p.add_argument(
        "--fg", "--foreground", dest="fg", action="store_true",
        help="Run in foreground (block until done)",
    )
    run_p.add_argument(
        "--_run-id", dest="_run_id", default=None,
        help=argparse.SUPPRESS,  # internal: background child reuses this
    )
    run_p.add_argument("kv_args", nargs="*", help="Workflow args as key=value pairs")

    # workflow list
    wf_action.add_parser("list", help="List available workflows")

    # workflow show <name>
    show_p = wf_action.add_parser("show", help="Show workflow DAG structure")
    show_p.add_argument("name", help="Workflow name")

    # workflow status [run_id]
    status_p = wf_action.add_parser("status", help="Check run status")
    status_p.add_argument("run_id", nargs="?", default=None, help="Run ID (latest if omitted)")

    # workflow stop <run_id>
    stop_p = wf_action.add_parser("stop", help="Kill a running background workflow")
    stop_p.add_argument("run_id", help="Run ID")

    # workflow result [run_id] [--json]
    result_p = wf_action.add_parser("result", help="Print full results")
    result_p.add_argument("run_id", nargs="?", default=None, help="Run ID (latest if omitted)")
    result_p.add_argument("--json", dest="json_output", action="store_true", help="Raw JSON output")

    # workflow runs
    wf_action.add_parser("runs", help="List recent runs")

    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def read_stdin_if_piped() -> str | None:
    """Read stdin if it is piped (not a TTY). Returns None otherwise."""
    if sys.stdin.isatty():
        return None
    return sys.stdin.read()
