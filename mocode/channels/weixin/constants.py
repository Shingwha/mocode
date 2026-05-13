"""WeChat-specific constants and type classification"""

from pathlib import Path

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"})
VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".wmv"})
VOICE_EXTS = frozenset({".silk", ".amr", ".mp3", ".wav", ".ogg", ".m4a"})

# Upload media type constants (single source of truth)
UPLOAD_MEDIA_IMAGE = 1
UPLOAD_MEDIA_VIDEO = 2
UPLOAD_MEDIA_FILE = 3
UPLOAD_MEDIA_VOICE = 4


def classify_upload_type(path: str | Path) -> int:
    """Classify file extension to WeChat upload media type."""

    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return UPLOAD_MEDIA_IMAGE
    if ext in VIDEO_EXTS:
        return UPLOAD_MEDIA_VIDEO
    if ext in VOICE_EXTS:
        return UPLOAD_MEDIA_VOICE
    return UPLOAD_MEDIA_FILE
