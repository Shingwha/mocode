"""Gateway application entry point"""

import asyncio
import logging
import sys

from ..core.config import Config
from .weixin import WeixinGateway

logger = logging.getLogger(__name__)

GATEWAY_REGISTRY: dict[str, type] = {
    "weixin": WeixinGateway,
}


class GatewayApp:
    """Gateway application launcher.

    Usage:
        gateway = GatewayApp("weixin")
        await gateway.run()
    """

    def __init__(self, gateway_type: str):
        if gateway_type not in GATEWAY_REGISTRY:
            available = ", ".join(GATEWAY_REGISTRY.keys())
            raise ValueError(
                f"Unknown gateway type: {gateway_type}. Available: {available}"
            )
        self._type = gateway_type

    async def run(self) -> None:
        """Load config, create gateway, and run until interrupted."""
        config = Config.load()
        gateway_config = config.gateway

        cls = GATEWAY_REGISTRY[self._type]
        gateway = cls(config=config, gateway_config=gateway_config)

        logger.info("Starting gateway: %s", self._type)
        try:
            await gateway.start()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.error("Gateway error: %s", e)
            sys.exit(1)
        finally:
            await gateway.stop()
