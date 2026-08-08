import sys
from pathlib import Path

from loguru import logger

from app.config import Settings


def configure_logging(settings: Settings) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.logging_level.upper(),
        colorize=True,
        enqueue=True,
        backtrace=settings.logging_level.upper() == "DEBUG",
        diagnose=False,
    )
    if settings.log_to_file:
        Path("logs").mkdir(exist_ok=True)
        logger.add(
            "logs/heyclaw-satellite-{time:YYYY-MM-DD}.log",
            level=settings.logging_level.upper(),
            rotation="00:00",
            retention="14 days",
            compression="gz",
            enqueue=True,
            diagnose=False,
        )
