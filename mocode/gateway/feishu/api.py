"""Feishu API wrappers using lark-oapi SDK.

All methods are synchronous (lark SDK is sync), intended to be called
via ``run_in_executor`` from async code.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import lark_oapi as lark

logger = logging.getLogger(__name__)


def fetch_bot_open_id(client: lark.Client) -> str | None:
    """Fetch the bot's own open_id via GET /open-apis/bot/v3/info."""
    import lark_oapi as lark

    try:
        request = (
            lark.BaseRequest.builder()
            .http_method(lark.HttpMethod.GET)
            .uri("/open-apis/bot/v3/info")
            .token_types({lark.AccessTokenType.APP})
            .build()
        )
        response = client.request(request)
        if response.success():
            data = json.loads(response.raw.content)
            bot = (data.get("data") or data).get("bot") or data.get("bot") or {}
            return bot.get("open_id")
        logger.warning("Failed to get bot info: code=%s, msg=%s", response.code, response.msg)
        return None
    except Exception as e:
        logger.warning("Error fetching bot info: %s", e)
        return None


def send_message(
    client: lark.Client,
    receive_id_type: str,
    receive_id: str,
    msg_type: str,
    content: str,
) -> str | None:
    """Send a message and return the message_id on success."""
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    try:
        request = (
            CreateMessageRequest.builder()
            .receive_id_type(receive_id_type)
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = client.im.v1.message.create(request)
        if not response.success():
            logger.error(
                "Failed to send Feishu %s message: code=%s, msg=%s",
                msg_type, response.code, response.msg,
            )
            return None
        msg_id = getattr(response.data, "message_id", None)
        logger.debug("Feishu %s message sent to %s: %s", msg_type, receive_id, msg_id)
        return msg_id
    except Exception as e:
        logger.error("Error sending Feishu %s message: %s", msg_type, e)
        return None


def reply_message(
    client: lark.Client,
    parent_message_id: str,
    msg_type: str,
    content: str,
) -> bool:
    """Reply to an existing message. Returns True on success."""
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

    try:
        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = client.im.v1.message.reply(request)
        if not response.success():
            logger.error(
                "Failed to reply to message %s: code=%s, msg=%s",
                parent_message_id, response.code, response.msg,
            )
            return False
        logger.debug("Feishu reply sent to message %s", parent_message_id)
        return True
    except Exception as e:
        logger.error("Error replying to message %s: %s", parent_message_id, e)
        return False


def get_message_content(client: lark.Client, message_id: str, max_len: int = 200) -> str | None:
    """Fetch text content of a message for reply context.

    Returns ``"[Reply to: ...]"`` or None on failure.
    """
    from lark_oapi.api.im.v1 import GetMessageRequest

    try:
        request = GetMessageRequest.builder().message_id(message_id).build()
        response = client.im.v1.message.get(request)
        if not response.success():
            return None
        items = getattr(response.data, "items", None)
        if not items:
            return None
        msg_obj = items[0]
        raw_content = getattr(msg_obj, "body", None)
        raw_content = getattr(raw_content, "content", None) if raw_content else None
        if not raw_content:
            return None
        try:
            content_json = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError):
            return None
        msg_type = getattr(msg_obj, "msg_type", "")
        if msg_type == "text":
            text = content_json.get("text", "").strip()
        elif msg_type == "post":
            from .card import extract_post_content
            text, _ = extract_post_content(content_json)
            text = text.strip()
        else:
            text = ""
        if not text:
            return None
        if len(text) > max_len:
            text = text[:max_len] + "..."
        return f"[Reply to: {text}]"
    except Exception as e:
        logger.debug("Error fetching parent message %s: %s", message_id, e)
        return None
