"""Tests for FetchTool — Jina Reader API integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from mocode.core.tool import ToolError
from mocode.tools.fetch import FetchTool


def _make_ok_response(text: str = "content") -> MagicMock:
    """Create a sync MagicMock that simulates an httpx.Response with raise_for_status()."""
    resp = MagicMock(spec=httpx.Response)
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


def _make_error_response(status_code: int = 404) -> MagicMock:
    """Create a sync MagicMock whose raise_for_status() raises HTTPStatusError."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = ""
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP error: {status_code}", request=MagicMock(), response=resp
    )
    return resp


class TestFetchTool:
    """FetchTool core behavior."""

    def test_tool_metadata(self):
        """Tool has correct name, description, and params."""
        t = FetchTool()
        assert t.name == "fetch"
        assert "webpage" in t.description.lower()
        assert "url" in t.params
        assert "timeout" in t.params
        assert t.params["timeout"].get("default") == 30

    def test_result_limit(self):
        """result_limit is configurable."""
        t = FetchTool(result_limit=100)
        assert t.name == "fetch"

    @pytest.mark.asyncio
    async def test_invalid_url(self):
        """Raises ToolError for non-http/https URLs."""
        t = FetchTool()

        for bad_url in ["ftp://example.com", "file:///tmp/test", "javascript:alert(1)"]:
            with pytest.raises(ToolError, match="Invalid URL"):
                await t.run_async({"url": bad_url})

    @pytest.mark.asyncio
    async def test_missing_url(self):
        """Raises ToolError when url param is missing."""
        t = FetchTool()

        with pytest.raises(ToolError, match="Missing required parameter"):
            await t.run_async({})

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        """Successful fetch returns content from Jina Reader API."""
        t = FetchTool(result_limit=50000)

        mock_response = _make_ok_response("# Hello\n\nThis is a test page.")

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = (
                mock_response
            )

            result = await t.run_async({"url": "https://example.com"})

            # Verify correct Jina URL was used
            call_args = mock_client.return_value.__aenter__.return_value.get.call_args
            assert call_args[0][0] == "https://r.jina.ai/https://example.com"
            # Verify Accept header
            assert call_args[1]["headers"]["Accept"] == "text/plain"
            # No auth header without API key
            assert "Authorization" not in call_args[1]["headers"]

        assert "# Hello" in result
        assert "test page" in result

    @pytest.mark.asyncio
    async def test_truncation(self):
        """Content is truncated when exceeding result_limit."""
        t = FetchTool(result_limit=20)

        mock_response = _make_ok_response(
            "This is a long piece of content that should be truncated."
        )

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = (
                mock_response
            )

            result = await t.run_async({"url": "https://example.com"})

        assert result.endswith("...[truncated]")
        assert len(result) <= 20 + len("\n...[truncated]")

    @pytest.mark.asyncio
    async def test_api_key_from_env(self, monkeypatch):
        """JINA_API_KEY env var adds Authorization header."""
        monkeypatch.setenv("JINA_API_KEY", "test-key-123")
        t = FetchTool()

        mock_response = _make_ok_response("content")

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = (
                mock_response
            )

            await t.run_async({"url": "https://example.com"})

            call_headers = (
                mock_client.return_value.__aenter__.return_value.get.call_args[1][
                    "headers"
                ]
            )
            assert call_headers["Authorization"] == "Bearer test-key-123"

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """TimeoutException is converted to ToolError."""
        t = FetchTool()

        with patch("httpx.AsyncClient") as mock_client:
            mock_get = mock_client.return_value.__aenter__.return_value.get
            mock_get.side_effect = httpx.TimeoutException("timeout")

            with pytest.raises(ToolError, match="timed out"):
                await t.run_async({"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_http_error(self):
        """HTTP status errors are converted to ToolError."""
        t = FetchTool()

        mock_response = _make_error_response(404)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = (
                mock_response
            )

            with pytest.raises(ToolError, match="HTTP error"):
                await t.run_async({"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_http_error_has_status_code(self):
        """HTTP error includes status code in message."""
        t = FetchTool()

        mock_response = _make_error_response(404)

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = (
                mock_response
            )

            with pytest.raises(ToolError, match="404"):
                await t.run_async({"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_url_with_path_preserved(self):
        """URL paths are preserved in the Jina URL."""
        t = FetchTool()

        mock_response = _make_ok_response("content")

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get.return_value = (
                mock_response
            )

            await t.run_async({"url": "https://example.com/path/to/page"})

            call_url = (
                mock_client.return_value.__aenter__.return_value.get.call_args[0][0]
            )
            assert call_url == "https://r.jina.ai/https://example.com/path/to/page"
