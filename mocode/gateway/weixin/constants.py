"""WeChat-specific constants and type classification"""

from pathlib import Path

# Upload media type constants (single source of truth)
UPLOAD_MEDIA_IMAGE = 1
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3
UPLOAD_MEDIA_VOICE = 4


def classify_upload_type(path: str | Path) -> int:
    """Classify file extension to WeChat upload media type."""
    from ..media import IMAGE_EXTS, VIDEO_EXTS, VOICE_EXTS

    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return UPLOAD_MEDIA_IMAGE
    if ext in VIDEO_EXTS:
        return UPLOAD_MEDIA_VIDEO
    if ext in VOICE_EXTS:
        return UPLOAD_MEDIA_VOICE
    return UPLOAD_MEDIA_FILE
