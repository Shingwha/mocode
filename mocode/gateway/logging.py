"""Gateway logging configuration"""

import logging
from logging.handlers import RotatingFileHandler
import sys

from ..paths import MOCODE_HOME

CONSOLE_FORMAT = "%(asctime)s %(levelname)-5s %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_gateway_logging() -> None:
    """Configure the mocode.gateway logger with console and file handlers."""
    gw_logger = logging.getLogger("mocode.gateway")
    gw_logger.setLevel(logging.DEBUG)

    # Avoid duplicate handlers if called multiple times
    if gw_logger.handlers:
        return

    # Console handler - concise format
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT))
    gw_logger.addHandler(console)

    # File handler - detailed format with rotation
    log_dir = MOCODE_HOME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "gateway.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT))
    gw_logger.addHandler(file_handler)
