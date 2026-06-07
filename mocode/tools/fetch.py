"""Fetch tool — FetchTool for HTTP fetch with markdown conversion."""
from __future__ import annotations

import asyncio
import urllib.error
import urllib.request

from ..core.tool import Tool, ToolError

_USER_AGENT = "Mozilla/5.0 (compatible; mocode/1.0)"
_BASE_URL = "https://markdown.new/"


def _fetch_sync(url: str, timeout: int) -> str:
    """Fetch a URL synchronously using stdlib urllib."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


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
            text = await asyncio.to_thread(_fetch_sync, fetch_url, timeout)
            if self._result_limit > 0 and len(text) > self._result_limit:
                text = text[: self._result_limit] + "\n...[truncated]"
            return text
        except urllib.error.URLError as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            raise ToolError(f"Fetch failed for {url}: {reason}", "fetch_error")
        except TimeoutError:
            raise ToolError(f"Request timed out after {timeout}s: {url}", "timeout")
        except Exception as e:
            raise ToolError(f"Fetch failed for {url}: {e}", "fetch_error")
