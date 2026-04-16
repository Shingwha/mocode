"""Channel auto-discovery via package scanning"""

import importlib
import logging
import pkgutil

from .base import BaseChannel

logger = logging.getLogger(__name__)


def discover_channel_names() -> list[str]:
    """Scan mocode.gateway package for channel sub-packages."""
    import mocode.gateway as pkg

    # Exclude internal utility packages that are not channel implementations
    EXCLUDED = {"cron"}

    return [
        name
        for _, name, is_pkg in pkgutil.iter_modules(pkg.__path__)
        if is_pkg and name not in EXCLUDED
    ]


def load_channel_class(module_name: str) -> type[BaseChannel] | None:
    """Import a channel module and find the BaseChannel subclass."""
    try:
        mod = importlib.import_module(f"mocode.gateway.{module_name}")
    except Exception as e:
        logger.error("Failed to import channel %s: %s", module_name, e)
        return None

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, BaseChannel)
            and attr is not BaseChannel
        ):
            return attr

    logger.warning("No BaseChannel subclass found in %s", module_name)
    return None


def discover_all() -> dict[str, type[BaseChannel]]:
    """Discover and return all available channel classes."""
    channels: dict[str, type[BaseChannel]] = {}
    for name in discover_channel_names():
        cls = load_channel_class(name)
        if cls:
            channels[name] = cls
    return channels
