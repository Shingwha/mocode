# Gateway Mode

Gateway mode enables mocode to run as a multi-channel bot, allowing users to interact with the AI assistant through platforms like Telegram.

## Overview

Gateway mode provides:
- **Multi-channel support** - Telegram, extensible to other platforms
- **User isolation** - Each user gets an independent session
- **Command interface** - Built-in commands for model/provider switching
- **Auto-approval** - Tools are automatically approved in gateway mode

## Configuration

Gateway configuration is stored in `~/.mocode/config.json`:

```json
{
  "gateway": {
    "channels": {
      "telegram": {
        "enabled": true,
        "token": "your-bot-token",
        "allowFrom": ["123456789"]
      }
    }
  }
}
```

### Telegram Configuration

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | No | Enable/disable the channel (default: true) |
| `token` | Yes | Bot token from BotFather |
| `allowFrom` | No | List of allowed Telegram user IDs |

If `allowFrom` is empty or not set, all users can interact with the bot.

### Getting Telegram User ID

1. Start a chat with @userinfobot on Telegram
2. Send any message
3. The bot will reply with your user ID

## Running Gateway

```bash
# Using the installed command
mocode gateway

# Or using uv run
uv run mocode gateway
```

## Built-in Commands

| Command | Description |
|---------|-------------|
| `/start` | Start conversation with the bot |
| `/help` | Show available commands |
| `/clear` | Clear conversation history |
| `/model [name]` | View or switch model (searches all providers) |
| `/provider [name]` | View or switch provider |
| `/status` | Show session status |
| `/cancel` | Cancel ongoing operation |

### Examples

```
/start                  # Initialize the bot
/help                   # Show commands
/model                  # List all models across providers
/model gpt-4o           # Switch to gpt-4o (auto-finds provider)
/provider               # List all providers
/provider deepseek      # Switch to deepseek
/status                 # Show current session info
/clear                  # Clear history
/cancel                 # Cancel current operation
```

## User Session Management

Each user gets an isolated `MocodeClient` instance:
- Independent conversation history
- Independent model/provider settings
- Independent event bus

Sessions are created on first message and persist until gateway restart.

## Event Handling

The gateway subscribes to these events:
- `TEXT_COMPLETE` - Send AI response to user
- `TOOL_START` - Notify user of tool execution
- `INTERRUPTED` - Handle cancellation
- `ERROR` - Handle errors

## Permission Handling

Gateway mode uses `DefaultPermissionHandler` which automatically approves all tool executions. This is suitable for trusted users.

For more granular control, implement a custom `PermissionHandler`:

```python
from mocode.core.permission_handler import PermissionHandler

class CustomPermissionHandler(PermissionHandler):
    async def ask_permission(self, tool_name: str, tool_args: dict) -> str:
        # Return "allow", "deny", or custom response
        return "allow"
```

## Channel Development

To add a new channel, implement the `BaseChannel` interface:

```python
from mocode.gateway.base import BaseChannel

class MyChannel(BaseChannel):
    name = "my-channel"

    async def start(self) -> None:
        """Start the channel"""
        pass

    async def stop(self) -> None:
        """Stop the channel"""
        pass

    async def send_message(self, user_id: str, text: str) -> None:
        """Send message to user"""
        pass

    def on_message(self, handler) -> None:
        """Register message handler"""
        # handler(user_id: str, text: str) -> None
        pass

    def is_user_allowed(self, user_id: str) -> bool:
        """Check if user is allowed"""
        return True
```

Then register the channel in `GatewayManager`:

```python
# In GatewayManager.__init__ or start()
channel = MyChannel(config)
channel.on_message(lambda uid, txt: asyncio.create_task(
    self._handle_message("my-channel", uid, txt)
))
self.channels["my-channel"] = channel
```

## Dependencies

For Telegram support, install the required package:

```bash
uv pip install python-telegram-bot
```

## Complete Configuration Example

```json
{
  "current": {
    "provider": "openai",
    "model": "gpt-4o"
  },
  "providers": {
    "openai": {
      "name": "OpenAI",
      "base_url": "https://api.openai.com/v1",
      "api_key": "sk-...",
      "models": ["gpt-4o", "gpt-4o-mini"]
    }
  },
  "gateway": {
    "channels": {
      "telegram": {
        "enabled": true,
        "token": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
        "allowFrom": ["123456789", "987654321"]
      }
    }
  },
  "max_tokens": 8192
}
```
