"""用户界面消息 - 欢迎语等用户 facing 文本"""

from ...core.config import Config


def get_welcome_message(config: Config) -> str:
    """生成欢迎消息"""
    provider_name = config.current_provider.name if config.current_provider else "Unknown"
    model = config.current.model if config.current else "Unknown"
    return (
        f"nanocode v0.1.0  │  "
        f"{model} ({provider_name})"
    )
