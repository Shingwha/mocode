"""Fetch tool — FetchTool for HTTP fetch via Jina Reader API with markdown conversion."""

from __future__ import annotations

import os

from ..core.tool import Tool, ToolError


def FetchTool(result_limit: int = 50000) -> Tool:
    """Create a fetch tool using Jina Reader API.

    Args:
        result_limit: Max characters for fetched content.
    """

    async def _fetch(args: dict) -> str:
        url = args.get("url")
        if not url.startswith(("http://", "https://")):
            raise ToolError(
                f"Invalid URL: {url}. Must start with http:// or https://",
                "invalid_url",
            )

        timeout = args.get("timeout", 30)
        fetch_url = "https://r.jina.ai/" + url.lstrip("/")

        import httpx

        headers = {"Accept": "text/plain"}
        api_key = os.environ.get("JINA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    fetch_url,
                    headers=headers,
                    follow_redirects=True,
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
        {
            "url": {"type": "string", "description": "The URL to fetch"},
            "timeout": {
                "type": "number",
                "description": "Request timeout in seconds (default: 30)",
                "default": 30,
            },
        },
        _fetch,
    )
