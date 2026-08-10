from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic

from loguru import logger


@contextmanager
def measure_performance(operation: str) -> Iterator[None]:
    """Emit consistent DEBUG start/end records for a potentially slow operation."""
    started_at = monotonic()
    logger.opt(depth=1).debug("performance operation={} phase=start", operation)
    try:
        yield
    except BaseException as exc:
        logger.opt(depth=1).debug(
            "performance operation={} phase=end status=error error_type={} duration_ms={:.1f}",
            operation,
            type(exc).__name__,
            (monotonic() - started_at) * 1000,
        )
        raise
    else:
        logger.opt(depth=1).debug(
            "performance operation={} phase=end status=ok duration_ms={:.1f}",
            operation,
            (monotonic() - started_at) * 1000,
        )
