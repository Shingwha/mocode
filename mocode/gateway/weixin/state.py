"""WeChat channel state persistence"""

import json
import logging
import tempfile
from pathlib import Path

from ...paths import MOCODE_HOME

logger = logging.getLogger(__name__)


class WeixinState:
    """Persistent state for WeChat channel, stored at ~/.mocode/weixin/state.json"""

    def __init__(self) -> None:
        self.token: str = ""
        self.base_url: str = "https://ilinkai.weixin.qq.com"
        self.context_tokens: dict[str, str] = {}  # user_id -> context_token
        self.typing_tickets: dict[str, dict] = {}  # user_id -> {ticket, next_fetch_at, ...}
        self.poll_cursor: str = ""  # get_updates_buf

    @property
    def _state_dir(self) -> Path:
        return MOCODE_HOME / "weixin"

    @property
    def _state_file(self) -> Path:
        return self._state_dir / "state.json"

    def load(self) -> bool:
        """Load saved state. Returns True if a valid token was found."""
        if not self._state_file.exists():
            return False
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            self.token = data.get("token", "")
            self.base_url = data.get(
                "base_url", "https://ilinkai.weixin.qq.com"
            )
            self.poll_cursor = data.get("poll_cursor", "")
            ctx = data.get("context_tokens", {})
            if isinstance(ctx, dict):
                self.context_tokens = {
                    str(k): str(v)
                    for k, v in ctx.items()
                    if str(k).strip() and str(v).strip()
                }
            tickets = data.get("typing_tickets", {})
            if isinstance(tickets, dict):
                self.typing_tickets = {
                    str(k): v
                    for k, v in tickets.items()
                    if str(k).strip() and isinstance(v, dict)
                }
            return bool(self.token)
        except Exception:
            logger.debug("Failed to load WeChat state", exc_info=True)
            return False

    def save(self) -> None:
        """Atomic write state to disk (temp + rename)."""
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            data = {
                "token": self.token,
                "base_url": self.base_url,
                "poll_cursor": self.poll_cursor,
                "context_tokens": self.context_tokens,
                "typing_tickets": self.typing_tickets,
            }
            content = json.dumps(data, ensure_ascii=False, indent=2)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_dir), suffix=".tmp"
            )
            try:
                with open(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                Path(tmp_path).replace(self._state_file)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        except Exception:
            logger.debug("Failed to save WeChat state", exc_info=True)

    def clear(self) -> None:
        """Wipe state (e.g. on logout)."""
        self.token = ""
        self.poll_cursor = ""
        self.context_tokens.clear()
        self.typing_tickets.clear()
        try:
            if self._state_file.exists():
                self._state_file.unlink()
        except Exception:
            pass
