"""WeChat media download and upload handler"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path

import httpx

from ..crypto import aes_ecb_decrypt, aes_ecb_encrypt, parse_aes_key
from ..media import ensure_media_dir
from .api import (
    CDN_BASE_URL,
    ITEM_FILE,
    ITEM_IMAGE,
    ITEM_VIDEO,
    ITEM_VOICE,
    WeixinApi,
)
from .constants import (
    UPLOAD_MEDIA_FILE,
    UPLOAD_MEDIA_IMAGE,
    UPLOAD_MEDIA_VIDEO,
    UPLOAD_MEDIA_VOICE,
    classify_upload_type,
)
from .state import WeixinState

logger = logging.getLogger(__name__)


class MediaHandler:
    """Handles WeChat CDN media download and upload."""

    def __init__(
        self, api: WeixinApi, client: httpx.AsyncClient, state: WeixinState
    ) -> None:
        self._api = api
        self._client = client
        self._state = state

    async def download(self, item: dict, user_id: str) -> str | None:
        """Download and decrypt a media item from WeChat CDN."""
        item_type = item.get("type", 0)
        type_map = {
            ITEM_IMAGE: "image_item",
            ITEM_VOICE: "voice_item",
            ITEM_FILE: "file_item",
            ITEM_VIDEO: "video_item",
        }
        typed_key = type_map.get(item_type)
        if not typed_key:
            return None

        typed_item = item.get(typed_key) or {}
        media_info = item.get("media") or {}

        # Get CDN download URL
        cdn_url = typed_item.get("full_url") or ""
        if not cdn_url:
            eqp = (
                typed_item.get("encrypted_query_param")
                or media_info.get("encrypted_query_param")
                or ""
            )
            if eqp:
                cdn_url = f"{CDN_BASE_URL}/download?encrypted_query_param={eqp}"
        if not cdn_url:
            return None

        # Get AES key for decryption
        aes_key = None
        aeskey_hex = typed_item.get("aeskey") or ""
        aeskey_b64 = media_info.get("aes_key") or ""
        if aeskey_hex:
            try:
                b64_from_hex = base64.b64encode(aeskey_hex.encode()).decode()
                aes_key = parse_aes_key(b64_from_hex)
            except Exception:
                pass
        elif aeskey_b64:
            try:
                aes_key = parse_aes_key(aeskey_b64)
            except Exception:
                pass

        # Download
        try:
            resp = await self._client.get(cdn_url)
            resp.raise_for_status()
            data = resp.content
        except Exception as e:
            logger.warning("CDN download failed: %s", e)
            return None

        # Decrypt if key present
        if aes_key:
            try:
                data = aes_ecb_decrypt(data, aes_key)
            except Exception as e:
                logger.warning("CDN decrypt failed: %s", e)
                return None

        # Determine filename and save
        media_dir = ensure_media_dir("weixin", user_id)

        ext = ""
        filename = typed_item.get("file_name") or media_info.get("file_name") or ""
        if filename:
            ext = "".join(Path(filename).suffixes)
        if not ext:
            ext_map = {
                ITEM_IMAGE: ".jpg",
                ITEM_VOICE: ".amr",
                ITEM_FILE: ".bin",
                ITEM_VIDEO: ".mp4",
            }
            ext = ext_map.get(item_type, ".bin")

        h = hashlib.md5(data).hexdigest()[:12]
        suffix = f"{h}{ext}"
        local_path = media_dir / suffix

        try:
            local_path.write_bytes(data)
        except Exception as e:
            logger.warning("Save media failed: %s", e)
            return None

        logger.info("Downloaded media: %s (%d bytes)", local_path.name, len(data))
        return str(local_path)

    async def upload(
        self, file_path: str, chat_id: str, ctx_token: str
    ) -> None:
        """Upload and send a media file via WeChat CDN (3-phase upload)."""
        p = Path(file_path)
        if not p.exists():
            logger.warning("File not found for upload: %s", file_path)
            return

        raw_data = p.read_bytes()
        raw_size = len(raw_data)
        raw_md5 = hashlib.md5(raw_data).hexdigest()

        # Generate random AES key
        aes_key_raw = os.urandom(16)
        aes_key_hex = aes_key_raw.hex()

        # Encrypt with raw key bytes
        encrypted = aes_ecb_encrypt(raw_data, aes_key_raw)
        padded_size = len(encrypted)

        # Generate filekey: pure hex random
        filekey = os.urandom(16).hex()

        media_type = classify_upload_type(file_path)

        # Phase 1: get upload URL
        upload_info = await self._api.get_upload_url(
            token=self._state.token,
            filekey=filekey,
            to_user_id=chat_id,
            raw_size=raw_size,
            raw_md5=raw_md5,
            padded_size=padded_size,
            media_type=media_type,
            aes_key_hex=aes_key_hex,
        )

        upload_url = upload_info.get("upload_full_url", "")
        upload_param = upload_info.get("upload_param") or {}
        if not upload_url:
            cdn_key = upload_info.get("cdn_key", "")
            if cdn_key:
                upload_url = f"{CDN_BASE_URL}/upload?{cdn_key}"
        if not upload_url:
            logger.warning("No upload URL in response")
            return

        # Phase 2: upload encrypted data to CDN
        resp = await self._client.post(
            upload_url,
            content=encrypted,
            headers={"Content-Type": "application/octet-stream", **upload_param},
        )
        resp.raise_for_status()

        # Get encrypted_param from response header
        enc_param = resp.headers.get("x-encrypted-param", "")

        # Phase 3: send message with media item
        type_map = {
            UPLOAD_MEDIA_IMAGE: ITEM_IMAGE,
            UPLOAD_MEDIA_VIDEO: ITEM_VIDEO,
            UPLOAD_MEDIA_VOICE: ITEM_VOICE,
            UPLOAD_MEDIA_FILE: ITEM_FILE,
        }
        item_type = type_map.get(media_type, ITEM_FILE)

        # aes_key for sendmessage: base64 of the hex string
        aes_key_b64 = base64.b64encode(aes_key_hex.encode()).decode()

        # Slim media dict - only 3 required fields
        media_dict = {
            "encrypt_query_param": enc_param,
            "aes_key": aes_key_b64,
            "encrypt_type": 1,
        }

        # Map upload media type to item wrapper key
        item_key_map = {
            UPLOAD_MEDIA_IMAGE: "image_item",
            UPLOAD_MEDIA_VOICE: "voice_item",
            UPLOAD_MEDIA_VIDEO: "video_item",
            UPLOAD_MEDIA_FILE: "file_item",
        }
        item_key = item_key_map.get(media_type, "file_item")

        # Build container with media inside + type-specific fields
        container: dict = {"media": media_dict}
        if media_type == UPLOAD_MEDIA_IMAGE:
            container["mid_size"] = raw_size
        elif media_type == UPLOAD_MEDIA_VIDEO:
            container["video_size"] = raw_size
        elif media_type == UPLOAD_MEDIA_FILE:
            container["file_name"] = p.name
            container["len"] = str(raw_size)

        media_item = {"type": item_type, item_key: container}

        await self._api.send_message(
            self._state.token, chat_id, ctx_token, "",
            item_list=[media_item],
        )
        logger.info("Sent media file: %s -> %s", p.name, chat_id)
