"""Fetch tool — FetchTool for HTTP fetch with markdown conversion."""

from __future__ import annotations

import httpx

from ..core.tool import Tool, ToolError


def FetchTool(result_limit: int = 50000) -> Tool:
    """Create a fetch tool.

    Args:
        result_limit: Max characters for fetched content.
    """

    async def _fetch(args: dict) -> str:
        url = args.get("url")
        if not url:
            raise ToolError("Missing required parameter 'url'", "invalid_input")
        if not url.startswith(("http://", "https://")):
            raise ToolError(
                f"Invalid URL: {url}. Must start with http:// or https://",
                "invalid_url",
            )

        timeout = args.get("timeout", 30)
        fetch_url = f"https://markdown.new/{url}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    fetch_url,
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; mocode/1.0)"},
                )
            response.raise_for_status()
            text = response.text
            if result_limit > 0 and len(text) > result_limit:
                text = text[:result_limit] + "\n...[truncated]"
            return text
        except httpx.TimeoutException:
            raise ToolError(f"Request timed out after {timeout}s", "timeout")
        except httpx.HTTPStatusError as e:
            raise ToolError(f"HTTP error: {e.response.status_code}", "http_error")
        except Exception as e:
            raise ToolError(f"Fetch failed: {e}", "fetch_error")

    return Tool(
        "fetch",
        "Fetch a webpage and convert to Markdown",
        {"url": "string", "timeout": "number?"},
        _fetch,
    )
