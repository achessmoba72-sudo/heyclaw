from heyclaw_shared.logging import configure_logging as _configure_logging
from heyclaw_shared.settings import Settings

# LiteLLM logs every request at INFO through the stdlib logger, drowning the voice trace.
_NOISY_LOGGERS = ("LiteLLM", "LiteLLM Proxy", "LiteLLM Router")


def configure_logging(settings: Settings) -> None:
    _configure_logging(settings, log_basename="heyclaw", quiet_loggers=_NOISY_LOGGERS)
