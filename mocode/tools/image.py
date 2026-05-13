"""Image tool — generate and edit images via OpenAI-compatible API."""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path

import httpx

from ..core.tool import Tool, ToolError


def ImageTool(
    base_url: str = "https://api.openai.com",
    api_key: str = "",
    model: str = "gpt-image-2",
    output_dir: str = "~/.mocode/media/images/",
    timeout: float = 120.0,
) -> Tool:
    """Create an image generation/edit tool.

    Args:
        base_url: API base URL (e.g. "https://api.openai.com").
        api_key: API key for authentication.
        model: Image model name (e.g. "gpt-image-2").
        output_dir: Default directory for saving images.
        timeout: Request timeout in seconds.
    """
    _base_url = base_url.rstrip("/")
    _default_dir = Path(output_dir).expanduser()

    async def _image(args: dict) -> str:
        mode = args["mode"]
        prompt = args["prompt"]
        n = int(args.get("n") or 1)
        out_raw = args.get("output_dir", "")
        image_paths_raw = args.get("image_paths", "")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(out_raw) if out_raw else _default_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        headers = {"Authorization": f"Bearer {api_key}"}

        optional = {}
        for key in ("size", "quality", "format", "background", "moderation"):
            val = args.get(key, "")
            if val:
                optional[key] = val

        client_timeout = httpx.Timeout(timeout, connect=30.0)
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            if mode == "generate":
                images = await _generate(client, headers, prompt, n, optional)
            elif mode == "edit":
                if not image_paths_raw:
                    raise ToolError(
                        "image_paths is required for edit mode", "invalid_input"
                    )
                paths = [
                    Path(p.strip()) for p in image_paths_raw.split(",") if p.strip()
                ]
                for p in paths:
                    if not p.exists():
                        raise ToolError(
                            f"Input image not found: {p}", "file_not_found"
                        )

                mask_path = args.get("mask_path", "")
                mask = Path(mask_path) if mask_path else None
                if mask and not mask.exists():
                    raise ToolError(
                        f"Mask image not found: {mask}", "file_not_found"
                    )

                images = await _edit(client, headers, paths, prompt, n, optional, mask)
            else:
                raise ToolError(
                    f"Invalid mode: {mode}. Use 'generate' or 'edit'",
                    "invalid_input",
                )

        saved = []
        for i, img_bytes in enumerate(images):
            suffix = f"_{i}" if len(images) > 1 else ""
            path = out_dir / f"image_{ts}{suffix}.png"
            path.write_bytes(img_bytes)
            saved.append(f"  {path} ({len(img_bytes) / 1024:.1f} KB)")

        return f"{len(saved)} image(s) saved:\n" + "\n".join(saved)

    async def _generate(client, headers, prompt, n, optional):
        url = f"{_base_url}/v1/images/generations"
        payload = {"prompt": prompt, "n": n, "model": model, **optional}
        return await _post_and_extract(client, url, headers, n, json=payload)

    async def _edit(client, headers, paths, prompt, n, optional, mask=None):
        url = f"{_base_url}/v1/images/edits"
        files = [("image", (p.name, p.read_bytes())) for p in paths]
        if mask:
            files.append(("mask", (mask.name, mask.read_bytes())))
        data = {"prompt": prompt, "n": str(n), "model": model, **optional}
        return await _post_and_extract(client, url, headers, n, files=files, data=data)

    async def _post_and_extract(client, url, headers, n, *, json=None, files=None, data=None):
        try:
            if json is not None:
                resp = await client.post(url, headers=headers, json=json)
            else:
                resp = await client.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise ToolError(
                f"Image API request timed out ({timeout}s)", "timeout"
            )
        except httpx.HTTPStatusError as e:
            raise ToolError(
                f"Image API error: {e.response.status_code} - {e.response.text[:500]}",
                "http_error",
            )
        except Exception as e:
            raise ToolError(f"Image API request failed: {e}", "api_error")

        body = resp.json()
        items = body.get("data")
        if not items:
            raise ToolError(f"API returned no image data: {body}", "api_error")

        results = []
        for item in items:
            if "b64_json" in item:
                results.append(base64.b64decode(item["b64_json"]))
            elif "url" in item:
                dl = await client.get(item["url"], headers=headers)
                dl.raise_for_status()
                results.append(dl.content)
            else:
                raise ToolError(
                    f"API response missing b64_json and url: {item}", "api_error"
                )
        return results

    return Tool(
        "image",
        "Generate an image from a text prompt, or edit an existing image. "
        "Use mode='generate' to create a new image from scratch. "
        "Use mode='edit' when the user uploads a reference image or wants to modify an existing image.",
        {
            "mode": {
                "type": "string",
                "description": "'generate' to create a new image, 'edit' to modify an existing image",
                "enum": ["generate", "edit"],
            },
            "prompt": {
                "type": "string",
                "description": "Text description of the image to generate or edit instructions",
            },
            "n": {
                "type": "number",
                "description": "Number of images to generate (1-10). Default 1.",
                "default": 1,
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to save images. Defaults to factory-configured output_dir.",
                "default": "",
            },
            "image_paths": {
                "type": "string",
                "description": "Comma-separated paths to reference images. REQUIRED for edit mode.",
                "default": "",
            },
            "size": {
                "type": "string",
                "description": "Image size (e.g. 1024x1024, 1536x1024, auto).",
                "default": "",
            },
            "quality": {
                "type": "string",
                "description": "Image quality: low, medium, high, auto.",
                "default": "",
            },
            "format": {
                "type": "string",
                "description": "Image format: png, jpeg, webp.",
                "default": "",
            },
            "background": {
                "type": "string",
                "description": "Background: opaque, auto. Edit mode only.",
                "default": "",
            },
            "mask_path": {
                "type": "string",
                "description": "Path to mask PNG for edit mode.",
                "default": "",
            },
            "moderation": {
                "type": "string",
                "description": "Content moderation: low, auto. Edit mode only.",
                "default": "",
            },
        },
        _image,
    )
