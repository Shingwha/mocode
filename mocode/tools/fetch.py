"""Fetch 工具 - 获取网页内容并转换为 Markdown"""

import httpx

from .base import Tool, ToolError, ToolRegistry
from .utils import truncate_result


def _fetch(args: dict) -> str:
    """获取网页内容并转换为 Markdown 格式"""
    url = args.get("url")
    if not url:
        raise ToolError("Missing required parameter 'url'", "invalid_input")

    # 验证 URL 格式
    if not url.startswith(("http://", "https://")):
        raise ToolError(
            f"Invalid URL: {url}. URL must start with http:// or https://",
            "invalid_url",
        )

    timeout = args.get("timeout", 30)

    # 构造 markdown.new URL
    fetch_url = f"https://markdown.new/{url}"

    try:
        response = httpx.get(
            fetch_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; mocode/1.0)"},
        )
        response.raise_for_status()
        return truncate_result(response.text, limit=50000)
    except httpx.TimeoutException:
        raise ToolError(f"Request timed out after {timeout}s", "timeout")
    except httpx.HTTPStatusError as e:
        raise ToolError(
            f"HTTP error: {e.response.status_code}",
            "http_error",
        )
    except Exception as e:
        raise ToolError(f"Fetch failed: {e}", "fetch_error")


def register_fetch_tools():
    """注册 fetch 工具"""
    ToolRegistry.register(
        Tool(
            "fetch",
            "Fetch a webpage and convert its content to Markdown. Just provide the URL; the tool converts it automatically via the markdown.new service.",
            {"url": "string", "timeout": "number?"},
            _fetch,
        )
    )
