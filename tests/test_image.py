"""Image tool tests"""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mocode.config import Config, ImageConfig
from mocode.tool import ToolRegistry


@pytest.fixture
def image_config():
    return Config.from_dict({
        "current": {"provider": "test", "model": "test-model"},
        "providers": {
            "test": {
                "name": "Test",
                "base_url": "https://api.test.com/v1",
                "api_key": "test-key",
                "models": ["test-model"],
            }
        },
        "image": {
            "enabled": True,
            "base_url": "https://api.test.com",
            "api_key": "img-key",
            "model": "gpt-image-2",
        },
    })


class TestImageConfig:
    def test_defaults(self):
        ic = ImageConfig()
        assert ic.enabled is False
        assert ic.base_url == "https://api.openai.com"
        assert ic.api_key == ""
        assert ic.model == "gpt-image-2"

    def test_from_dict(self, image_config):
        assert image_config.image.enabled is True
        assert image_config.image.base_url == "https://api.test.com"
        assert image_config.image.api_key == "img-key"
        assert image_config.image.model == "gpt-image-2"

    def test_to_dict_roundtrip(self, image_config):
        d = image_config.to_dict()
        assert "image" in d
        assert d["image"]["enabled"] is True
        assert d["image"]["model"] == "gpt-image-2"
        restored = Config.from_dict(d)
        assert restored.image.enabled is True
        assert restored.image.model == "gpt-image-2"


class TestImageRegistration:
    def test_not_registered_when_disabled(self, registry, config):
        from mocode.tools import register_basic_tools
        register_basic_tools(registry, config)
        assert registry.get("image") is None

    def test_registered_when_enabled(self, registry, image_config):
        from mocode.tools import register_basic_tools
        register_basic_tools(registry, image_config)
        assert registry.get("image") is not None

    def test_schema(self, registry, image_config):
        from mocode.tools.image import register_image_tools
        register_image_tools(registry, image_config)
        schema = registry.get("image").to_schema()
        params = schema["function"]["parameters"]
        assert "mode" in params["properties"]
        assert "prompt" in params["properties"]
        assert "mode" in params["required"]
        assert "prompt" in params["required"]
        # Optional params should not be required
        for opt in ("output_dir", "image_paths", "size", "quality", "format"):
            assert opt not in params["required"]


class TestImageGenerate:
    @pytest.fixture(autouse=True)
    def setup(self, registry, image_config):
        from mocode.tools.image import register_image_tools
        register_image_tools(registry, image_config)
        self.registry = registry

    @pytest.mark.asyncio
    async def test_generate_saves_b64(self, tmp_path):
        b64_data = base64.b64encode(b"fake_png_data").decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": b64_data}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await self.registry.run_async("image", {
                "mode": "generate",
                "prompt": "a cat",
                "output_dir": str(tmp_path),
            })
        assert "1 image(s) saved" in result
        saved = list(tmp_path.glob("image_*.png"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == b"fake_png_data"

    @pytest.mark.asyncio
    async def test_generate_passes_optional_params(self, tmp_path):
        b64_data = base64.b64encode(b"img").decode()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": b64_data}]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            await self.registry.run_async("image", {
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
    async def test_generate_api_error(self):
        import httpx
        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
            result = await self.registry.run_async("image", {
                "mode": "generate",
                "prompt": "test",
            })
        assert "error" in result.lower() or "timeout" in result.lower()

    @pytest.mark.asyncio
    async def test_generate_multiple(self, tmp_path):
        imgs = [base64.b64encode(f"img{i}".encode()).decode() for i in range(3)]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"b64_json": d} for d in imgs]}
        mock_resp.raise_for_status = MagicMock()

        with patch("mocode.tools.image.httpx.AsyncClient") as MockClient:
            instance = MockClient.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await self.registry.run_async("image", {
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
        names = [p.name for p in saved]
        assert any("_0.png" in n for n in names)
        assert any("_2.png" in n for n in names)


class TestImageEdit:
    @pytest.fixture(autouse=True)
    def setup(self, registry, image_config):
        from mocode.tools.image import register_image_tools
        register_image_tools(registry, image_config)
        self.registry = registry

    @pytest.mark.asyncio
    async def test_edit_requires_image_paths(self):
        result = await self.registry.run_async("image", {
            "mode": "edit",
            "prompt": "make it blue",
        })
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_edit_missing_file(self):
        result = await self.registry.run_async("image", {
            "mode": "edit",
            "prompt": "make it blue",
            "image_paths": "/nonexistent.png",
        })
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_edit_sends_multiple_images(self, tmp_path):
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
            result = await self.registry.run_async("image", {
                "mode": "edit",
                "prompt": "merge them",
                "image_paths": f"{img1},{img2}",
                "output_dir": str(tmp_path),
            })
            call_kwargs = instance.post.call_args
            files = call_kwargs.kwargs.get("files") or call_kwargs[1].get("files")
            # files is a list of tuples: [("image", (...)), ...]
            image_fields = [f for f in files if f[0] == "image"]
            assert len(image_fields) == 2

        assert "1 image(s) saved" in result
