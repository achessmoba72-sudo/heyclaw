from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from time import monotonic

from loguru import logger

# nullcontext is stateless and reentrant, so one instance serves every disabled call site.
_NOT_TRACKING: AbstractContextManager[None] = nullcontext()

_tracking = False


def set_performance_tracking(enabled: bool) -> None:
    """Turn the timing records on; `configure_logging` enables them only at DEBUG."""
    global _tracking
    _tracking = enabled


def measure_performance(operation: str) -> AbstractContextManager[None]:
    """Time a potentially slow operation, or do nothing at all when not tracking.

    Returning a shared no-op context manager keeps the disabled path free of the
    generator and `contextlib` machinery, so instrumenting a hot path costs nothing
    outside DEBUG.
    """
    if not _tracking:
        return _NOT_TRACKING
    return _record_performance(operation)


@contextmanager
def _record_performance(operation: str) -> Iterator[None]:
    """Emit consistent DEBUG start/end records for a potentially slow operation.

    Depth 2 skips this generator frame and the `contextlib` frame driving it, so every
    record is attributed to the code that opened the block instead of to `contextlib`.
    """
    started_at = monotonic()
    logger.opt(depth=2).debug("performance operation={} phase=start", operation)
    try:
        yield
    except BaseException as exc:
        logger.opt(depth=2).debug(
            "performance operation={} phase=end status=error error_type={} duration_ms={:.1f}",
            operation,
            type(exc).__name__,
            (monotonic() - started_at) * 1000,
        )
        raise
    else:
        logger.opt(depth=2).debug(
            "performance operation={} phase=end status=ok duration_ms={:.1f}",
            operation,
            (monotonic() - started_at) * 1000,
        )
