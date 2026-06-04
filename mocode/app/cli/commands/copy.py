"""Copy command — copies the last assistant response to clipboard."""

from . import CommandContext, CommandResult


class CopyCommand:
    name = "/copy"
    description = "Copy the last assistant response to clipboard"
    aliases = ()

    async def run(self, ctx: CommandContext) -> CommandResult:
        messages = ctx.app.agent.messages

        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls")
                if content and not tool_calls:
                    try:
                        import pyperclip

                        pyperclip.copy(content)
                        preview = content[:60].replace("\n", " ").strip()
                        suffix = "…" if len(content) > 60 else ""
                        ctx.display.info(f"Copied: {preview}{suffix}")
                    except Exception as e:
                        ctx.display.error(f"Clipboard error: {e}")
                    return CommandResult.CONTINUE

        ctx.display.warn("No assistant response to copy.")
        return CommandResult.CONTINUE
