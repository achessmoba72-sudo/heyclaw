import logging
import sys
from collections.abc import Sequence

from loguru import logger

from heyclaw_shared.settings import Settings


def configure_logging(
    settings: Settings,
    *,
    log_basename: str,
    quiet_loggers: Sequence[str] = (),
) -> None:
    """Route loguru to stderr and, when enabled, to a daily rotated file."""
    for logger_name in quiet_loggers:
        logging.getLogger(logger_name).setLevel(logging.ERROR)

    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.level,
        colorize=True,
        enqueue=True,
        backtrace=settings.debug,
        diagnose=False,
    )
    if settings.log_to_file:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            settings.log_dir / f"{log_basename}-{{time:YYYY-MM-DD}}.log",
            level=settings.level,
            rotation="00:00",
            retention="14 days",
            compression="gz",
            enqueue=True,
            diagnose=False,
        )
