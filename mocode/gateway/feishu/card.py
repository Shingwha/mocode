"""Content formatting for Feishu channel.

Handles inbound content extraction (post, interactive cards, mentions)
and outbound format detection with card building.
"""

from __future__ import annotations

import json
import re


# ---------------------------------------------------------------------------
# Inbound content extraction
# ---------------------------------------------------------------------------

def extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from Feishu post (rich text) message.

    Handles three payload shapes:
    - Direct:    {"title": "...", "content": [[...]]}
    - Localized: {"zh_cn": {"title": "...", "content": [...]}}
    - Wrapped:   {"post": {"zh_cn": {"title": "...", "content": [...]}}}
    """

    def _parse_block(block: dict) -> tuple[str | None, list[str]]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts: list[str] = []
        images: list[str] = []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "code_block":
                    lang = el.get("language", "")
                    code_text = el.get("text", "")
                    texts.append(f"\n```{lang}\n{code_text}\n```\n")
                elif tag == "img" and (key := el.get("image_key")):
                    images.append(key)
        return (" ".join(texts).strip() or None), images

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []

    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs

    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs

    return "", []


def extract_interactive_content(content: dict) -> list[str]:
    """Recursively extract text and links from interactive card content."""
    parts: list[str] = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title = content["title"]
        if isinstance(title, dict):
            title_content = title.get("content", "") or title.get("text", "")
            if title_content:
                parts.append(f"title: {title_content}")
        elif isinstance(title, str):
            parts.append(f"title: {title}")

    for elements in (
        content.get("elements", []) if isinstance(content.get("elements"), list) else []
    ):
        for element in elements:
            parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(extract_interactive_content(card))

    header = content.get("header", {})
    if header:
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")

    return parts


def _extract_element_content(element: dict) -> list[str]:
    """Extract content from a single card element."""
    parts: list[str] = []
    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")

    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)
    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str):
            parts.append(text)
        for field in element.get("fields", []):
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    c = field_text.get("content", "")
                    if c:
                        parts.append(c)
    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)
    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            c = text.get("content", "")
            if c:
                parts.append(c)
        url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
        if url:
            parts.append(f"link: {url}")
    elif tag == "img":
        alt = element.get("alt", {})
        parts.append(alt.get("content", "[image]") if isinstance(alt, dict) else "[image]")
    elif tag == "note":
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))
    elif tag == "column_set":
        for col in element.get("columns", []):
            for ce in col.get("elements", []):
                parts.extend(_extract_element_content(ce))
    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)
    else:
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    return parts


def extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Extract text representation from share cards and interactive messages."""
    parts: list[str] = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def resolve_mentions(text: str, mentions: list | None) -> str:
    """Replace @_user_n placeholders with actual user info from mentions."""
    if not mentions or not text:
        return text

    for mention in mentions:
        key = getattr(mention, "key", None)
        if not key or key not in text:
            continue

        user_id_obj = getattr(mention, "id", None)
        if not user_id_obj:
            continue

        open_id = getattr(user_id_obj, "open_id", "")
        user_id = getattr(user_id_obj, "user_id", "")
        name = getattr(mention, "name", "") or key

        if open_id and user_id:
            replacement = f"@{name} ({open_id}, user id: {user_id})"
        elif open_id:
            replacement = f"@{name} ({open_id})"
        else:
            replacement = f"@{name}"

        text = text.replace(key, replacement)

    return text


# ---------------------------------------------------------------------------
# Outbound format detection & card building
# ---------------------------------------------------------------------------

_TABLE_RE = re.compile(
    r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
    re.MULTILINE,
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MD_BOLD_UNDERSCORE_RE = re.compile(r"__(.+?)__")
_MD_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")

_COMPLEX_MD_RE = re.compile(
    r"```"
    r"|^\|.+\|.*\n\s*\|[-:\s|]+\|"
    r"|^#{1,6}\s+",
    re.MULTILINE,
)

_SIMPLE_MD_RE = re.compile(
    r"\*\*.+?\*\*"
    r"|__.+?__"
    r"|(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"
    r"|~~.+?~~",
    re.DOTALL,
)

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
_LIST_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_OLIST_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)

_TEXT_MAX_LEN = 200
_POST_MAX_LEN = 2000


def detect_msg_format(content: str) -> str:
    """Determine the optimal Feishu message format for content.

    Returns "text", "post", or "interactive".
    """
    stripped = content.strip()

    if _COMPLEX_MD_RE.search(stripped):
        return "interactive"
    if len(stripped) > _POST_MAX_LEN:
        return "interactive"
    if _SIMPLE_MD_RE.search(stripped):
        return "interactive"
    if _LIST_RE.search(stripped) or _OLIST_RE.search(stripped):
        return "interactive"
    if _MD_LINK_RE.search(stripped):
        return "post"
    if len(stripped) <= _TEXT_MAX_LEN:
        return "text"
    return "post"


def _strip_md_formatting(text: str) -> str:
    """Strip markdown formatting markers for plain-text surfaces."""
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_BOLD_UNDERSCORE_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    return text


def parse_md_table(table_text: str) -> dict | None:
    """Parse a markdown table into a Feishu table element."""
    lines = [_line.strip() for _line in table_text.strip().split("\n") if _line.strip()]
    if len(lines) < 3:
        return None

    def split(_line: str) -> list[str]:
        return [c.strip() for c in _line.strip("|").split("|")]

    headers = [_strip_md_formatting(h) for h in split(lines[0])]
    rows = [[_strip_md_formatting(c) for c in split(_line)] for _line in lines[2:]]
    columns = [
        {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
        for i, h in enumerate(headers)
    ]
    return {
        "tag": "table",
        "page_size": len(rows) + 1,
        "columns": columns,
        "rows": [
            {f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows
        ],
    }


def _split_headings(content: str) -> list[dict]:
    """Split content by headings, converting headings to div elements."""
    protected = content
    code_blocks: list[str] = []
    for m in _CODE_BLOCK_RE.finditer(content):
        code_blocks.append(m.group(1))
        protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks) - 1}\x00", 1)

    elements: list[dict] = []
    last_end = 0
    for m in _HEADING_RE.finditer(protected):
        before = protected[last_end : m.start()].strip()
        if before:
            elements.append({"tag": "markdown", "content": before})
        text = _strip_md_formatting(m.group(2).strip())
        display_text = f"**{text}**" if text else ""
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": display_text}})
        last_end = m.end()
    remaining = protected[last_end:].strip()
    if remaining:
        elements.append({"tag": "markdown", "content": remaining})

    for i, cb in enumerate(code_blocks):
        for el in elements:
            if el.get("tag") == "markdown":
                el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

    return elements or [{"tag": "markdown", "content": content}]


def build_card_elements(content: str) -> list[dict]:
    """Split content into div/markdown + table elements for Feishu card."""
    elements: list[dict] = []
    last_end = 0
    for m in _TABLE_RE.finditer(content):
        before = content[last_end : m.start()]
        if before.strip():
            elements.extend(_split_headings(before))
        elements.append(
            parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)}
        )
        last_end = m.end()
    remaining = content[last_end:]
    if remaining.strip():
        elements.extend(_split_headings(remaining))
    return elements or [{"tag": "markdown", "content": content}]


def split_elements_by_table_limit(elements: list[dict], max_tables: int = 1) -> list[list[dict]]:
    """Split card elements into groups with at most max_tables table elements each."""
    if not elements:
        return [[]]
    groups: list[list[dict]] = []
    current: list[dict] = []
    table_count = 0
    for el in elements:
        if el.get("tag") == "table":
            if table_count >= max_tables:
                if current:
                    groups.append(current)
                current = []
                table_count = 0
            current.append(el)
            table_count += 1
        else:
            current.append(el)
    if current:
        groups.append(current)
    return groups or [[]]


def markdown_to_post(content: str) -> str:
    """Convert markdown content to Feishu post message JSON.

    Handles links ``[text](url)`` as ``a`` tags; everything else as ``text`` tags.
    """
    lines = content.strip().split("\n")
    paragraphs: list[list[dict]] = []

    for line in lines:
        elements: list[dict] = []
        last_end = 0

        for m in _MD_LINK_RE.finditer(line):
            before = line[last_end : m.start()]
            if before:
                elements.append({"tag": "text", "text": before})
            elements.append({"tag": "a", "text": m.group(1), "href": m.group(2)})
            last_end = m.end()

        remaining = line[last_end:]
        if remaining:
            elements.append({"tag": "text", "text": remaining})

        if not elements:
            elements.append({"tag": "text", "text": ""})

        paragraphs.append(elements)

    post_body = {"zh_cn": {"content": paragraphs}}
    return json.dumps(post_body, ensure_ascii=False)
