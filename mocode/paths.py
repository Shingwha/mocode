"""全局路径配置

所有 mocode 相关路径在此统一管理，便于：
- 修改默认存储位置
- 添加新的配置/数据目录
- 保持各模块路径一致
"""

from pathlib import Path

# 主目录 - 修改此处即可改变所有相关路径
MOCODE_HOME: Path = Path.home() / ".mocode"

# 配置文件
CONFIG_PATH: Path = MOCODE_HOME / "config.json"

# Skills 目录
SKILLS_DIR: Path = MOCODE_HOME / "skills"

# Sessions 目录
SESSIONS_DIR: Path = MOCODE_HOME / "sessions"

# 项目级 skills 目录名（相对于当前工作目录）
PROJECT_SKILLS_DIRNAME: str = ".mocode"

# Plugins 目录
PLUGINS_DIR: Path = MOCODE_HOME / "plugins"
