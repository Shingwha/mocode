"""WeChat QR code login handler"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .api import MAX_QR_REFRESH_COUNT, WeixinApi
from .state import WeixinState

logger = logging.getLogger(__name__)


class LoginHandler:
    """Handles WeChat QR code login flow."""

    def __init__(self, api: WeixinApi, state: WeixinState) -> None:
        self._api = api
        self._state = state
        self.running: bool = True

    async def run(self) -> bool:
        """Try restore token, verify, QR login if needed. Returns True on success."""
        if self._state.load():
            try:
                _, _, _ = await self._api.get_updates(
                    self._state.token, cursor=self._state.poll_cursor,
                    timeout=1,
                )
                logger.info("WeChat token restored from state")
                return True
            except Exception:
                logger.info("Saved token expired, re-login required")

        if not await self._qr_login():
            logger.error("WeChat login failed")
            self.running = False
            return False
        return True

    async def _qr_login(self) -> bool:
        """Perform QR code login flow. Returns True on success."""
        refresh_count = 0
        try:
            qrcode_id, scan_url = await self._api.get_qrcode()
            self._print_qr_code(scan_url)
            current_poll_base: str | None = None

            while self.running:
                try:
                    status_data = await self._api.check_qr_status(
                        qrcode_id, base_url=current_poll_base
                    )
                except (httpx.TimeoutException, httpx.TransportError):
                    await asyncio.sleep(1)
                    continue
                except httpx.HTTPStatusError as e:
                    if e.response.status_code >= 500:
                        await asyncio.sleep(1)
                        continue
                    raise

                if not isinstance(status_data, dict):
                    await asyncio.sleep(1)
                    continue

                status = status_data.get("status", "")
                if status == "confirmed":
                    token = status_data.get("bot_token", "")
                    base_url = status_data.get("baseurl", "")
                    if not token:
                        logger.error("Login confirmed but no token in response")
                        return False
                    user_name = status_data.get("user_name", "")
                    if user_name:
                        logger.info("QR confirmed by user: %s", user_name)
                    self._state.token = token
                    if base_url:
                        self._state.base_url = base_url
                        self._api.base_url = base_url
                    self._state.save()
                    logger.info("WeChat login successful")
                    return True

                elif status == "scaned_but_redirect":
                    redirect_host = str(
                        status_data.get("redirect_host", "") or ""
                    ).strip()
                    if redirect_host:
                        if not redirect_host.startswith(("http://", "https://")):
                            redirect_host = f"https://{redirect_host}"
                        current_poll_base = redirect_host

                elif status == "expired":
                    refresh_count += 1
                    if refresh_count > MAX_QR_REFRESH_COUNT:
                        logger.warning("QR expired too many times, giving up")
                        return False
                    qrcode_id, scan_url = await self._api.get_qrcode()
                    current_poll_base = None
                    self._print_qr_code(scan_url)

                # "wait" status: keep polling
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("WeChat QR login failed: %s", e)
        return False

    @staticmethod
    def _print_qr_code(url: str) -> None:
        """Print QR code or URL for scanning."""
        try:
            import qrcode as qr_lib

            qr = qr_lib.QRCode(border=1)
            qr.add_data(url)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except ImportError:
            print(f"\nWeChat Login URL: {url}\n")
