"""Gateway application entry point

v0.2 adaptation:
- Config.load() replaced with FileConfigStore
- Gateway config extracted from raw config dict
- register_gateway_tools() no longer called globally (done per-instance in UserRouter)
"""

import asyncio
import logging
import sys

from ..store import FileConfigStore
from ..config import Config
from .bus import MessageBus
from .logging import setup_gateway_logging
from .manager import ChannelManager
from .registry import discover_all
from .router import UserRouter

logger = logging.getLogger(__name__)


class GatewayApp:
    """Gateway application launcher.

    Usage:
        gateway = GatewayApp()          # auto-discover enabled channels
        gateway = GatewayApp("weixin")   # explicit single channel
        await gateway.run()
    """

    def __init__(self, gateway_type: str | None = None):
        self._type = gateway_type  # None = auto-discover from config

    async def run(self) -> None:
        """Load config, create channels, and run until interrupted."""
        setup_gateway_logging()

        # Load config via FileConfigStore (v0.2 pattern)
        store = FileConfigStore()
        data = store.load()
        config = Config.from_dict(data) if data else Config()
        gateway_config = data.get("gateway", {}) if data else {}

        available = discover_all()

        # Discover which channels to start
        if self._type:
            if self._type not in available:
                names = ", ".join(available.keys()) or "(none)"
                raise ValueError(
                    f"Unknown gateway type: {self._type}. Available: {names}"
                )
            types_to_start = [self._type]
        else:
            types_to_start = self._find_enabled_channels(
                gateway_config, available,
            )

        if not types_to_start:
            logger.error(
                "No channels to start. "
                "Configure gateway.channels.*.enabled=true in config.json"
            )
            return

        bus = MessageBus()
        router = UserRouter(config, gateway_config)
        manager = ChannelManager(
            bus, router,
            cron_config=gateway_config.get("cron", {}),
        )

        for channel_type in types_to_start:
            cls = available[channel_type]
            channel_config = (
                gateway_config.get("channels", {}).get(channel_type, {})
            )
            channel = cls(
                name=channel_type,
                config=config,
                gateway_config=channel_config,
                bus=bus,
            )
            manager.register(channel)
            logger.info("Registered channel: %s", channel_type)

        logger.info("Starting gateway: %s", ", ".join(types_to_start))
        try:
            await manager.start_all()
            # Block forever (channels run as tasks)
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.error("Gateway error: %s", e)
            sys.exit(1)
        finally:
            await manager.stop_all()

    @staticmethod
    def _find_enabled_channels(
        gateway_config: dict,
        available: dict[str, type],
    ) -> list[str]:
        """Find channels with enabled=true in config."""
        channels_cfg = gateway_config.get("channels", {})
        enabled: list[str] = []
        for name in available:
            ch_cfg = channels_cfg.get(name, {})
            if ch_cfg.get("enabled", False):
                enabled.append(name)
        return enabled
