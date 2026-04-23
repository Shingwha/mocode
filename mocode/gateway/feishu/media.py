"""Media upload/download for Feishu channel via lark-oapi SDK."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import lark_oapi as lark

logger = logging.getLogger(__name__)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
_AUDIO_EXTS = {".opus"}
_VIDEO_EXTS = {".mp4", ".mov", ".avi"}
_FILE_TYPE_MAP = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}


def download_image(
    client: lark.Client, message_id: str, image_key: str,
) -> tuple[bytes | None, str | None]:
    """Download an image from a Feishu message."""
    from lark_oapi.api.im.v1 import GetMessageResourceRequest

    try:
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        response = client.im.v1.message_resource.get(request)
        if response.success():
            file_data = response.file
            if hasattr(file_data, "read"):
                file_data = file_data.read()
            return file_data, response.file_name
        logger.error("Failed to download image: code=%s, msg=%s", response.code, response.msg)
        return None, None
    except Exception as e:
        logger.error("Error downloading image %s: %s", image_key, e)
        return None, None


def download_file(
    client: lark.Client, message_id: str, file_key: str, resource_type: str = "file",
) -> tuple[bytes | None, str | None]:
    """Download a file/audio/media from a Feishu message."""
    from lark_oapi.api.im.v1 import GetMessageResourceRequest

    if resource_type in ("audio", "media"):
        resource_type = "file"

    try:
        request = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(file_key)
            .type(resource_type)
            .build()
        )
        response = client.im.v1.message_resource.get(request)
        if response.success():
            file_data = response.file
            if hasattr(file_data, "read"):
                file_data = file_data.read()
            return file_data, response.file_name
        logger.error("Failed to download %s: code=%s, msg=%s", resource_type, response.code, response.msg)
        return None, None
    except Exception:
        logger.exception("Error downloading %s %s", resource_type, file_key)
        return None, None


def upload_image(client: lark.Client, file_path: str) -> str | None:
    """Upload an image to Feishu and return the image_key."""
    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

    try:
        with open(file_path, "rb") as f:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder().image_type("message").image(f).build()
                )
                .build()
            )
            response = client.im.v1.image.create(request)
            if response.success():
                return response.data.image_key
            logger.error("Failed to upload image: code=%s, msg=%s", response.code, response.msg)
            return None
    except Exception as e:
        logger.error("Error uploading image %s: %s", file_path, e)
        return None


def upload_file(client: lark.Client, file_path: str) -> str | None:
    """Upload a file to Feishu and return the file_key."""
    from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

    ext = os.path.splitext(file_path)[1].lower()
    file_type = _FILE_TYPE_MAP.get(ext, "stream")
    file_name = os.path.basename(file_path)
    try:
        with open(file_path, "rb") as f:
            request = (
                CreateFileRequest.builder()
                .request_body(
                    CreateFileRequestBody.builder()
                    .file_type(file_type)
                    .file_name(file_name)
                    .file(f)
                    .build()
                )
                .build()
            )
            response = client.im.v1.file.create(request)
            if response.success():
                return response.data.file_key
            logger.error("Failed to upload file: code=%s, msg=%s", response.code, response.msg)
            return None
    except Exception as e:
        logger.error("Error uploading file %s: %s", file_path, e)
        return None


async def download_and_save(
    client: lark.Client,
    msg_type: str,
    content_json: dict,
    message_id: str | None = None,
    user_id: str = "",
) -> tuple[str | None, str]:
    """Download media from Feishu and save to local disk.

    Returns (file_path, content_text).
    """
    import asyncio

    from ..media import ensure_media_dir

    loop = asyncio.get_running_loop()
    data: bytes | None = None
    filename: str | None = None

    if msg_type == "image":
        image_key = content_json.get("image_key")
        if image_key and message_id:
            data, filename = await loop.run_in_executor(
                None, download_image, client, message_id, image_key,
            )
            if not filename:
                filename = f"{image_key[:16]}.jpg"

    elif msg_type in ("audio", "file", "media"):
        file_key = content_json.get("file_key")
        if not file_key:
            return None, f"[{msg_type}: missing file_key]"
        if not message_id:
            return None, f"[{msg_type}: missing message_id]"

        data, filename = await loop.run_in_executor(
            None, download_file, client, message_id, file_key, msg_type,
        )

        if not data:
            return None, f"[{msg_type}: download failed]"
        if not filename:
            filename = file_key[:16]
        if msg_type == "audio":
            if not any(filename.endswith(ext) for ext in (".opus", ".ogg", ".oga")):
                filename = f"{filename}.ogg"

    if data and filename:
        ext = os.path.splitext(filename)[1] if "." in filename else ""
        media_dir = ensure_media_dir("feishu", user_id, ext)
        file_path = media_dir / filename
        file_path.write_bytes(data)
        logger.debug("Downloaded %s to %s", msg_type, file_path)
        return str(file_path), f"[{msg_type}: {filename}]"

    return None, f"[{msg_type}: download failed]"
