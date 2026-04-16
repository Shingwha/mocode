"""Fetch 工具 — 工厂函数注册模式"""

import httpx

from ..tool import Tool, ToolError, ToolRegistry
from .utils import truncate_result


def register_fetch_tools(registry: ToolRegistry) -> None:
    """注册 fetch 工具"""

    def _fetch(args: dict) -> str:
        url = args.get("url")
        if not url:
            raise ToolError("Missing required parameter 'url'", "invalid_input")

        if not url.startswith(("http://", "https://")):
            raise ToolError(
                f"Invalid URL: {url}. URL must start with http:// or https://",
                "invalid_url",
            )

        timeout = args.get("timeout", 30)
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
            raise ToolError(f"HTTP error: {e.response.status_code}", "http_error")
        except Exception as e:
            raise ToolError(f"Fetch failed: {e}", "fetch_error")

    registry.register(Tool(
        "fetch",
        "Fetch a webpage and convert its content to Markdown. Just provide the URL; the tool converts it automatically via the markdown.new service.",
        {"url": "string", "timeout": "number?"},
        _fetch,
    ))
