from heyclaw_shared.logging import configure_logging as _configure_logging
from heyclaw_shared.settings import Settings


def configure_logging(settings: Settings) -> None:
    _configure_logging(settings, log_basename="heyclaw-satellite")
