# Gateway

Gateway allows running mocode as a bot on messaging platforms. Each user gets an isolated conversation session.

## Quick Start

```bash
# Start WeChat gateway
mocode gateway --type weixin
```

## Architecture

```
┌─────────────┐
│   Channel   │ (WeChat, etc.)
│   (weixin)   │
└──────┬──────┘
       │ receives messages
       ▼
┌────────────────┐
│  ChannelMgr    │ ← Dispatches inbound/outbound
│  + Router      │    with retry logic
└──────┬─────────┘
       │ routes per user
       ▼
┌────────────────┐
│  UserRouter    │ ← LRU session management
│  (per-user)    │    with asyncio.Lock
└──────┬─────────┘
       │ creates
       ▼
┌────────────────┐
│ MocodeCore     │ ← One per user, yolo mode
│ (isolated)     │    auto-approves safe tools
└────────────────┘
```

**Components**:
- **Channel**: Platform-specific adapter (WeChat, etc.)
- **ChannelManager**: Lifecycle management + message dispatch with retry
- **UserRouter**: Per-user session management with LRU eviction
- **MocodeCore**: One isolated instance per user (forced yolo mode)

## Configuration

Add gateway settings to `~/.mocode/config.json`:

```json
{
  "gateway": {
    "max_users": 100,
    "idle_timeout": 3600,
    "allow_from": ["*"]
  }
}
```

| Field | Default | Description |
|-------|---------|-------------|
| `max_users` | 100 | Maximum concurrent user sessions (LRU eviction) |
| `idle_timeout` | 3600 | Seconds of inactivity before session eviction |
| `allow_from` | `["*"]` | List of allowed sender IDs (empty = all allowed) |

## WeChat Gateway

### Start

```bash
mocode gateway --type weixin
```

### Features

- QR code login (no password required)
- Long-poll message fetching with auto-reconnect
- Media download (images, voice, video, files) with CDN + AES decryption
- Media upload via 3-phase CDN upload
- Voice message transcription (uses configured LLM)
- Typing indicator ("正在输入..." status)
- Auto message splitting at 3500 chars

### How It Works

1. **Login**: QR code displayed in terminal → scan with WeChat → bot token obtained
2. **Polling**: Long-poll `getUpdates` API (60s timeout) with exponential backoff on errors
3. **Session expired**: Automatically pauses for 10 minutes, then re-login
4. **Media**: Downloaded from CDN with decryption → saved to `~/.mocode/media/weixin/<user_id>/`
5. **Send**: Text sent via `sendMessage`; files queued via `send_file` tool → 3-phase CDN upload

### Media Handling

**Inbound** (user → bot):
- Images/voice/video/files automatically downloaded
- Stored: `~/.mocode/media/weixin/<user_id>/<hash><ext>`
- Content replaced with `[image]`, `[voice]`, `[video]`, `[file: name]` placeholders
- Voice messages transcribed to text using Whisper (if configured)

**Outbound** (bot → user):
- Use `send_file` tool to queue files for sending
- Files uploaded via CDN with AES encryption
- Automatically sent after text response

Example:
```python
# In your conversation, tell the AI:
# "Save this chart to /tmp/chart.png and send it to the user"
# The AI will use write + send_file tools
```

### Session Management

- Each WeChat user ID gets independent `MocodeCore` instance
- Sessions auto-saved on eviction/shutdown
- Per-user `asyncio.Lock` ensures serialized message processing
- LRU eviction when `max_users` exceeded

### State Persistence

WeChat state saved to `~/.mocode/gateway/weixin_state.json`:
- Bot token and base URL
- Poll cursor
- Context tokens per user
- Typing ticket cache

## Gateway Tools

### `send_file`

Send a file to the current user. Only works in gateway mode.

```python
# Used internally by AI when asked to send files
core._pending_media.append("/path/to/file.png")
```

Files are automatically uploaded to WeChat CDN and sent to the user.

## CLI Commands

```bash
mocode gateway --type weixin    # Start WeChat gateway
mocode gateway --type <name>    # Start other gateway (if available)
```

## Adding New Gateways

Subclass `BaseChannel` in `mocode/gateway/`:

```python
from mocode.gateway.base import BaseChannel

class MyChannel(BaseChannel):
    async def start(self):
        # Connect to platform, start polling
        pass

    async def stop(self):
        # Cleanup
        pass

    async def send(self, msg: OutboundMessage):
        # Send message to user
        pass
```

Register in `mocode/gateway/registry.py`. See `WeixinChannel` for a complete reference implementation.

## Event Flow

1. Platform → Channel receives message
2. Channel → MessageBus.publish_inbound(InboundMessage)
3. ChannelManager._dispatch_inbound() consumes inbound
4. UserRouter.get_or_create(session_key) gets/creates user session
5. Session.core.chat(media=...) processes with LLM
6. Tools can queue files via `send_file`
7. Response text → MessageBus.publish_outbound(OutboundMessage)
8. Queued files → separate OutboundMessage with media paths
9. ChannelManager._dispatch_outbound() sends via Channel.send()

## Logging

Gateway logs to `~/.mocode/gateway/gateway.log`:

```
[info] Starting gateway: weixin
[tool] user123 → bash(ls -la)
[tool-done] user123 ← bash: file1.py file2.py
[reply] user123: Here are the files...
```

## Troubleshooting

### Login QR not showing

Install `qrcode` library:
```bash
uv pip install qrcode
```

### Session expires frequently

WeChat server may throttle. The gateway auto-pauses for 10 minutes on session expiry, then re-logins.

### Media download fails

Check CDN connectivity. Media uses encrypted query parameters and AES decryption.

### High memory usage

Adjust `max_users` in config. Default 100 users × ~10MB each = ~1GB.