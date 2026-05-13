"""Image tool tests."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.core.tool import ToolRegistry, ToolError
from mocode.tools.image import ImageTool


@pytest.fixture
def registry_with_image(tmp_path):
    registry = ToolRegistry()
    tool = ImageTool(
        base_url="https://api.test.com",
        api_key="test-key",
        model="gpt-image-2",
        output_dir=str(tmp_path),
    )
    registry.register(tool)
    return registry


class TestImageSchema:
    def test_required_params(self, registry_with_image):
        schema = registry_with_image.get("image").to_schema()
        params = schema["function"]["parameters"]
        assert "mode" in params["required"]
        assert "prompt" in params["required"]

    def test_optional_params_not_required(self, registry_with_image):
        schema = registry_with_image.get("image").to_schema()
        params = schema["function"]["parameters"]
        for opt in ("output_dir", "image_paths", "size", "quality", "format"):
            assert opt not in params["required"]

    def test_mode_enum(self, registry_with_image):
        schema = registry_with_image.get("image").to_schema()
        props = schema["function"]["parameters"]["properties"]
        assert props["mode"]["enum"] == ["generate", "edit"]


class TestImageGenerate:
    @pytest.mark.asyncio
    async def test_generate_saves_b64(self, registry_with_image, tmp_path):
        b64_data = base64.b64encode(b"fake_png_data").decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": b64_data}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await registry_with_image.get("image").run_async({
                "mode": "generate",
                "prompt": "a cat",
                "output_dir": str(tmp_path),
            })
        assert "1 image(s) saved" in result
        saved = list(tmp_path.glob("image_*.png"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"fake_png_data"

    @pytest.mark.asyncio
    async def test_generate_passes_optional_params(self, registry_with_image, tmp_path):
        b64_data = base64.b64encode(b"img").decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": b64_data}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            await registry_with_image.get("image").run_async({
                "mode": "generate",
                "prompt": "a dog",
                "output_dir": str(tmp_path),
                "size": "1536x1024",
                "quality": "high",
            })
            call_kwargs = instance.post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert payload["size"] == "1536x1024"
            assert payload["quality"] == "high"
            assert "format" not in payload

    @pytest.mark.asyncio
    async def test_generate_api_error(self, registry_with_image):
        import httpx

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(
                side_effect=httpx.TimeoutException("timeout")
            )
            with pytest.raises(ToolError, match="timed out"):
                await registry_with_image.get("image").run_async({
                    "mode": "generate",
                    "prompt": "test",
                })

    @pytest.mark.asyncio
    async def test_generate_multiple(self, registry_with_image, tmp_path):
        imgs = [base64.b64encode(f"img{i}".encode()).decode() for i in range(3)]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": d} for d in imgs]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await registry_with_image.get("image").run_async({
                "mode": "generate",
                "prompt": "three cats",
                "n": 3,
                "output_dir": str(tmp_path),
            })
            call_kwargs = instance.post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            assert payload["n"] == 3

        assert "3 image(s) saved" in result
        saved = sorted(tmp_path.glob("image_*.png"))
        assert len(saved) == 3


class TestImageEdit:
    @pytest.mark.asyncio
    async def test_edit_requires_image_paths(self, registry_with_image):
        with pytest.raises(ToolError, match="image_paths is required"):
            await registry_with_image.get("image").run_async({
                "mode": "edit",
                "prompt": "make it blue",
            })

    @pytest.mark.asyncio
    async def test_edit_missing_file(self, registry_with_image):
        with pytest.raises(ToolError, match="Input image not found"):
            await registry_with_image.get("image").run_async({
                "mode": "edit",
                "prompt": "make it blue",
                "image_paths": "/nonexistent.png",
            })

    @pytest.mark.asyncio
    async def test_edit_sends_multiple_images(self, registry_with_image, tmp_path):
        img1 = tmp_path / "a.png"
        img2 = tmp_path / "b.png"
        img1.write_bytes(b"img1")
        img2.write_bytes(b"img2")

        b64_data = base64.b64encode(b"result").decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": b64_data}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await registry_with_image.get("image").run_async({
                "mode": "edit",
                "prompt": "merge them",
                "image_paths": f"{img1},{img2}",
                "output_dir": str(tmp_path),
            })
            call_kwargs = instance.post.call_args
            files = call_kwargs.kwargs.get("files") or call_kwargs[1].get("files")
            image_fields = [f for f in files if f[0] == "image"]
            assert len(image_fields) == 2

        assert "1 image(s) saved" in result
