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
        gateway = GatewayApp("weixin")
        await gateway.run()
    """

    def __init__(self, gateway_type: str):
        available = discover_all()
        if gateway_type not in available:
            names = ", ".join(available.keys()) or "(none)"
            raise ValueError(
                f"Unknown gateway type: {gateway_type}. Available: {names}"
            )
        self._type = gateway_type
        self._channel_cls = available[gateway_type]

    async def run(self) -> None:
        """Load config, create channel, and run until interrupted."""
        setup_gateway_logging()

        # Load config via FileConfigStore (v0.2 pattern)
        store = FileConfigStore()
        data = store.load()
        config = Config.from_dict(data) if data else Config()
        gateway_config = data.get("gateway", {}) if data else {}

        bus = MessageBus()
        router = UserRouter(config, gateway_config)
        manager = ChannelManager(
            bus, router,
            cron_config=gateway_config.get("cron", {}),
        )

        channel = self._channel_cls(
            name=self._type,
            config=config,
            gateway_config=gateway_config,
            bus=bus,
        )
        manager.register(channel)

        logger.info("Starting gateway: %s", self._type)
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
