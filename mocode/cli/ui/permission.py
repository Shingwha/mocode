"""CLI permission handler using interactive prompts."""

from ...core.permission import PermissionHandler
from .prompt import select, ask
from .styles import BOLD, BLUE, DIM, GREEN, RESET


class CLIPermissionHandler(PermissionHandler):
    """CLI permission handler with interactive allow/deny/custom menu."""

    def __init__(self, display=None):
        self.display = display

    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        if self.display:
            self.display.set_thinking(False)

        target = (
            tool_args.get("cmd")
            or tool_args.get("command")
            or tool_args.get("path")
            or ""
        )

        if target:
            preview = target[:60] + "..." if len(target) > 60 else target
            title = f"Permission required for {GREEN}{tool_name}{RESET} ({DIM}{preview}{RESET})"
        else:
            title = f"Permission required for {GREEN}{tool_name}{RESET}"

        result = select(title, [
            ("allow", "Allow (execute the tool)"),
            ("deny", "Deny (cancel the operation)"),
            ("input", "Type something (provide custom response)"),
        ])

        if result == "allow":
            return "allow"
        elif result is None:
            return "interrupt"
        elif result == "deny":
            return "deny"
        elif result == "input":
            try:
                print(f"\n{BOLD}{BLUE}>{RESET} ", end="", flush=True)
                user_input = input()
                return user_input
            except (KeyboardInterrupt, EOFError):
                return "deny"

        return "deny"
