"""ConfigStore Protocol + 文件/内存实现"""

import json
from pathlib import Path
from typing import Protocol

from .paths import CONFIG_PATH


class ConfigStore(Protocol):
    """配置存储协议"""

    def load(self) -> dict | None: ...
    def save(self, data: dict) -> None: ...


class FileConfigStore:
    """从文件读写配置"""

    def __init__(self, path: Path | None = None):
        self._path = path or CONFIG_PATH

    def load(self) -> dict | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, IOError):
            return None

    def save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


class InMemoryConfigStore:
    """内存存储，用于测试和 gateway"""

    def __init__(self, data: dict | None = None):
        self._data = data

    def load(self) -> dict | None:
        return self._data

    def save(self, data: dict) -> None:
        self._data = data
