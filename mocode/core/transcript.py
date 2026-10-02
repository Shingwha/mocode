"""The message dict — the one shape a conversation is written in.

History, exports, replay and the provider protocol all speak OpenAI-flavored
message dicts. This module is the only place that format's shape lives: the
loop writes history through its constructors, the readers read through its
accessors, and a format evolution touches this file and nothing else.

The shape it knows — every message a plain ``dict``, JSON round-trippable:

* ``{"role": "user", "content": ...}`` — content a plain string, or a
  multimodal list of parts: ``{"type": "text", "text": ...}`` and
  ``{"type": "image_url", ...}``;
* ``{"role": "assistant", "content": ...}`` — optionally carrying
  ``reasoning_content`` (a reasoning model's thinking) and ``tool_calls``, a
  list of ``{"id": ..., "type": "function", "function": {"name": ...,
  "arguments": ...}}`` with *arguments* a JSON string;
* ``{"role": "tool", "tool_call_id": ..., "content": ...}`` — the answer to
  one call, keyed by the id of the call it answers.

Fields that carry nothing are omitted rather than sent empty: an endpoint
that never heard of reasoning rejects a message that carries the field.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .provider import ToolCall

__all__ = [
    "IMAGE_PLACEHOLDER",
    "answered_call_id",
    "assistant_message",
    "content_parts",
    "is_assistant",
    "is_tool_result",
    "is_user",
    "reasoning_of",
    "text_of",
    "tool_call_args",
    "tool_call_arguments",
    "tool_call_by_id",
    "tool_call_dicts",
    "tool_call_id",
    "tool_call_name",
    "tool_calls_of",
    "tool_result",
]

#: How a message that carried an image reads as text — one string, so an
#: image placeholder is the same word everywhere it appears.
IMAGE_PLACEHOLDER = "[image]"


# ── Construction ──────────────────────────────────────────────


def assistant_message(
    text: str = "",
    *,
    tool_calls: list[dict] | None = None,
    reasoning: str = "",
) -> dict:
    """An assistant message: its prose, its reasoning, its calls for tools.

    Produces ``{"role": "assistant", "content": text}``; ``reasoning_content``
    and ``tool_calls`` are attached only when non-empty, so a message that
    carried neither stays exactly the two fields it needs.
    """
    msg: dict = {"role": "assistant", "content": text}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def tool_call_dicts(tool_calls: list[ToolCall]) -> list[dict]:
    """The kernel's :class:`~mocode.core.provider.ToolCall` objects as message
    entries — the ``{"id", "type", "function": {"name", "arguments"}}`` form."""
    return [
        {
            "id": t.id,
            "type": "function",
            "function": {"name": t.name, "arguments": t.arguments},
        }
        for t in tool_calls
    ]


def tool_result(tool_call_id: str, content: str) -> dict:
    """A tool's answer, keyed by the call it answers."""
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


# ── Roles ─────────────────────────────────────────────────────


def is_user(msg: dict) -> bool:
    """Whether the message is one the person typed."""
    return msg.get("role") == "user"


def is_assistant(msg: dict) -> bool:
    """Whether the message is one the model produced."""
    return msg.get("role") == "assistant"


def is_tool_result(msg: dict) -> bool:
    """Whether the message is a tool's answer to a call."""
    return msg.get("role") == "tool"


# ── Content ───────────────────────────────────────────────────


def content_parts(content: object) -> list[str]:
    """The text pieces of a message's ``content``.

    A plain string is one piece. A multimodal list contributes each text
    part's text and :data:`IMAGE_PLACEHOLDER` for each image part; anything
    else contributes nothing, so a part a reader does not understand is
    dropped rather than rendered as noise.
    """
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return [str(content)] if content else []
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
            elif part.get("type") == "image_url":
                parts.append(IMAGE_PLACEHOLDER)
    return parts


def text_of(msg: dict, sep: str = " ") -> str:
    """One message's content as text, multimodal parts as placeholders.

    *sep* joins the parts, because each presentation has its own idea of how
    pieces sit together: the terminal keeps them on one row, an export puts
    them on separate lines.
    """
    return sep.join(content_parts(msg.get("content", "")))


def reasoning_of(msg: dict) -> str:
    """The thinking a reasoning model left on an assistant message."""
    return msg.get("reasoning_content", "")


# ── Tool calls ────────────────────────────────────────────────


def tool_calls_of(msg: dict) -> list[dict]:
    """The tool calls an assistant message asked for, oldest first.

    Empty for any other role, and for an assistant message that carried none.
    """
    return msg.get("tool_calls", [])


def tool_call_id(call: dict) -> str:
    """The id a call carries — the key its result answers under."""
    return call.get("id", "")


def tool_call_name(call: dict, default: str = "") -> str:
    """The function a call invokes; *default* when the call names none, because
    a model occasionally emits a call without a name and each presentation
    has its own way of saying so."""
    return call.get("function", {}).get("name", default)


def tool_call_arguments(call: dict, default: str = "") -> object:
    """A call's arguments exactly as stored — a JSON string by contract.

    A hand-edited history may hold a parsed object instead; a caller that
    means to parse must use :func:`tool_call_args`, which tolerates both.
    """
    return call.get("function", {}).get("arguments", default)


def tool_call_args(call: dict) -> dict:
    """A call's arguments as a dict — the JSON string parsed, an object kept.

    Unparseable or non-object arguments mean no arguments: a malformed call
    must still render as a call, not crash the reader.
    """
    arguments: Any = tool_call_arguments(call)
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
    return arguments if isinstance(arguments, dict) else {}


def answered_call_id(msg: dict) -> str:
    """The id of the call a tool-result message answers, ``""`` when it has
    none — an orphaned result names no call."""
    return msg.get("tool_call_id", "")


def tool_call_by_id(messages: list[dict], call_id: str) -> dict | None:
    """The tool call with this id anywhere in the history, or ``None``.

    The lookup an export needs to title a result with the call it answers;
    ids are unique in a well-formed history, so the first match is the call.
    """
    for msg in messages:
        for call in tool_calls_of(msg):
            if tool_call_id(call) == call_id:
                return call
    return None
