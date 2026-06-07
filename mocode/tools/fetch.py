"""Fetch tool — FetchTool for HTTP fetch with markdown conversion."""
from __future__ import annotations

import httpx

from ..core.tool import Tool, ToolError

_USER_AGENT = "Mozilla/5.0 (compatible; mocode/1.0)"
_BASE_URL = "https://markdown.new/"
_client = httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, follow_redirects=True, timeout=30)


class FetchTool(Tool):
    """Fetch a webpage and convert to Markdown."""
    def __init__(self, result_limit: int = 50000) -> None:
        self._result_limit = result_limit
        super().__init__(
            name="fetch",
            description="Fetch a webpage and convert to Markdown",
            params={
                "url": {"type": "string", "description": "The URL to fetch"},
                "timeout": {"type": "number", "description": "Request timeout in seconds (default: 30)", "default": 30},
            },
            func=self._execute,
        )

    async def _execute(self, args: dict) -> str:
        url = args.get("url")
        if not url.startswith(("http://", "https://")):
            raise ToolError(f"Invalid URL: {url}. Must start with http:// or https://", "invalid_url")
        timeout = args.get("timeout", 30)
        fetch_url = _BASE_URL + url.lstrip("/")
        try:
            response = await _client.get(fetch_url, timeout=timeout)
            response.raise_for_status()
            text = response.text
            if self._result_limit > 0 and len(text) > self._result_limit:
                text = text[: self._result_limit] + "\n...[truncated]"
            return text
        except httpx.TimeoutException:
            raise ToolError(f"Request timed out after {timeout}s: {url}", "timeout")
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP {e.response.status_code} for {url}", "http_error")
        except Exception as e:
            raise ToolError(f"Fetch failed for {url}: {e}", "fetch_error")
