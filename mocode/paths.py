"""全局路径配置

所有 mocode 相关路径在此统一管理。
"""

from pathlib import Path

# 主目录
MOCODE_HOME: Path = Path.home() / ".mocode"

# 配置文件
CONFIG_PATH: Path = MOCODE_HOME / "config.json"

# Skills 目录
SKILLS_DIR: Path = MOCODE_HOME / "skills"

# Sessions 目录
SESSIONS_DIR: Path = MOCODE_HOME / "sessions"

# 项目级 skills 目录名
PROJECT_SKILLS_DIRNAME: str = ".mocode"

# Gateway 目录
GATEWAY_DIR: Path = MOCODE_HOME / "gateway"

# Media 目录
MEDIA_DIR: Path = MOCODE_HOME / "media"
IMAGES_DIR: Path = MEDIA_DIR / "images"

# Memory 目录
MEMORY_DIR: Path = MOCODE_HOME / "memory"

# Cron 任务存储目录
CRON_DIR: Path = MOCODE_HOME / "cron"

# Dream 目录
DREAM_DIR: Path = MOCODE_HOME / "dream"
