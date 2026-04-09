"""WeChat ilinkai HTTP API client"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Protocol constants
WEIXIN_CHANNEL_VERSION = "2.1.1"
ILINK_APP_ID = "bot"
POLL_TIMEOUT_S = 35
MAX_CONSECUTIVE_FAILURES = 3
BACKOFF_DELAY_S = 30
SESSION_PAUSE_DURATION_S = 3600
ERRCODE_SESSION_EXPIRED = -14
MAX_QR_REFRESH_COUNT = 3
TYPING_STATUS_TYPING = 1
TYPING_STATUS_CANCEL = 2
TYPING_TICKET_TTL_S = 24 * 60 * 60
TYPING_KEEPALIVE_INTERVAL_S = 5
CONFIG_CACHE_INITIAL_RETRY_S = 2
CONFIG_CACHE_MAX_RETRY_S = 3600
RETRY_DELAY_S = 2

# Message item types
ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

# Upload media type constants
UPLOAD_MEDIA_IMAGE = 1
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3
UPLOAD_MEDIA_VOICE = 4

CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

# Message types
MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2
MESSAGE_STATE_FINISH = 2


def _build_client_version(version: str) -> int:
    """Encode semantic version as 0x00MMNNPP."""
    parts = version.split(".")

    def _as_int(idx: int) -> int:
        try:
            return int(parts[idx])
        except Exception:
            return 0

    major, minor, patch = _as_int(0), _as_int(1), _as_int(2)
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


ILINK_APP_CLIENT_VERSION = _build_client_version(WEIXIN_CHANNEL_VERSION)
BASE_INFO: dict[str, str] = {"channel_version": WEIXIN_CHANNEL_VERSION}


class WeixinApiError(Exception):
    """Error from WeChat API"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"WeChat API error {code}: {message}")


class WeixinApi:
    """Thin async HTTP client for ilinkai.weixin.qq.com API."""

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, url: str) -> None:
        self._base_url = url.rstrip("/")

    # -- Headers --

    @staticmethod
    def _random_wechat_uin() -> str:
        """Random X-WECHAT-UIN: random uint32 as decimal string -> base64."""
        uint32 = int.from_bytes(os.urandom(4), "big")
        return base64.b64encode(str(uint32).encode()).decode()

    def _make_headers(self, token: str = "", *, auth: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-WECHAT-UIN": self._random_wechat_uin(),
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(ILINK_APP_CLIENT_VERSION),
        }
        if auth and token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # -- Auth --

    async def get_qrcode(self) -> tuple[str, str]:
        """Fetch QR code. Returns (qrcode_id, scan_url)."""
        url = f"{self._base_url}/ilink/bot/get_bot_qrcode"
        resp = await self._client.get(
            url,
            params={"bot_type": "3"},
            headers=self._make_headers(auth=False),
        )
        resp.raise_for_status()
        data = resp.json()
        qrcode_id = data.get("qrcode", "")
        if not qrcode_id:
            raise WeixinApiError(-1, f"No QR code in response: {data}")
        scan_url = data.get("qrcode_img_content", "") or qrcode_id
        return qrcode_id, scan_url

    async def check_qr_status(
        self, qrcode_id: str, *, base_url: str | None = None
    ) -> dict[str, Any]:
        """Check QR code scan status. Optionally use a different base_url."""
        url_base = (base_url or self._base_url).rstrip("/")
        url = f"{url_base}/ilink/bot/get_qrcode_status"
        resp = await self._client.get(
            url,
            params={"qrcode": qrcode_id},
            headers=self._make_headers(auth=False),
        )
        resp.raise_for_status()
        return resp.json()

    # -- Polling --

    async def get_updates(
        self, token: str, cursor: str = "", timeout: int = POLL_TIMEOUT_S
    ) -> tuple[list[dict], str]:
        """Long-poll for new messages. Returns (messages, new_cursor)."""
        body: dict[str, Any] = {
            "get_updates_buf": cursor,
            "base_info": BASE_INFO,
        }
        url = f"{self._base_url}/ilink/bot/getupdates"
        resp = await self._client.post(
            url, json=body, headers=self._make_headers(token)
        )
        resp.raise_for_status()
        data = resp.json()

        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
            raise WeixinApiError(
                errcode or ret,
                data.get("errmsg", "getUpdates failed"),
            )

        msgs = data.get("msgs", []) or []
        new_cursor = data.get("get_updates_buf", "")
        timeout_ms = data.get("longpolling_timeout_ms")
        return msgs, new_cursor, timeout_ms

    # -- Sending --

    async def send_message(
        self,
        token: str,
        to_user_id: str,
        context_token: str,
        text: str,
        client_id: str = "",
        *,
        item_list: list[dict] | None = None,
    ) -> None:
        """Send a text message to a user."""
        import uuid

        if not client_id:
            client_id = f"mocode-{uuid.uuid4().hex[:12]}"

        if item_list is None:
            item_list = [{"type": ITEM_TEXT, "text_item": {"text": text}}]
        weixin_msg: dict[str, Any] = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": MESSAGE_TYPE_BOT,
            "message_state": MESSAGE_STATE_FINISH,
            "item_list": item_list,
        }
        if context_token:
            weixin_msg["context_token"] = context_token

        body: dict[str, Any] = {
            "msg": weixin_msg,
            "base_info": BASE_INFO,
        }
        url = f"{self._base_url}/ilink/bot/sendmessage"
        resp = await self._client.post(
            url, json=body, headers=self._make_headers(token)
        )
        resp.raise_for_status()
        data = resp.json()
        errcode = data.get("errcode", 0)
        if errcode and errcode != 0:
            raise WeixinApiError(errcode, data.get("errmsg", "send failed"))

    # -- Typing --

    async def send_typing(
        self, token: str, user_id: str, typing_ticket: str, status: int
    ) -> None:
        """Send typing indicator."""
        if not typing_ticket:
            return
        body: dict[str, Any] = {
            "ilink_user_id": user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": BASE_INFO,
        }
        url = f"{self._base_url}/ilink/bot/sendtyping"
        await self._client.post(
            url, json=body, headers=self._make_headers(token)
        )

    async def get_config(
        self, token: str, user_id: str, context_token: str = ""
    ) -> dict[str, Any]:
        """Get typing ticket via getconfig."""
        body: dict[str, Any] = {
            "ilink_user_id": user_id,
            "context_token": context_token or None,
            "base_info": BASE_INFO,
        }
        url = f"{self._base_url}/ilink/bot/getconfig"
        resp = await self._client.post(
            url, json=body, headers=self._make_headers(token)
        )
        resp.raise_for_status()
        return resp.json()

    # -- CDN Upload --

    async def get_upload_url(
        self,
        token: str,
        filekey: str,
        to_user_id: str,
        raw_size: int,
        raw_md5: str,
        padded_size: int,
        media_type: int,
        aes_key_hex: str,
    ) -> dict[str, Any]:
        """Get CDN upload URL for sending media files."""
        body: dict[str, Any] = {
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": raw_size,
            "rawfilemd5": raw_md5,
            "filesize": padded_size,
            "no_need_thumb": True,
            "aeskey": aes_key_hex,
        }
        url = f"{self._base_url}/ilink/bot/getuploadurl"
        resp = await self._client.post(
            url, json=body, headers=self._make_headers(token)
        )
        resp.raise_for_status()
        data = resp.json()
        ret = data.get("ret", 0)
        errcode = data.get("errcode", 0)
        if (ret is not None and ret != 0) or (errcode is not None and errcode != 0):
            raise WeixinApiError(
                errcode or ret, data.get("errmsg", "getuploadurl failed")
            )
        return data
