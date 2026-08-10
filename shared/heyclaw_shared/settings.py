from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# shared/heyclaw_shared/settings.py → repository root, so every component logs to the
# same directory no matter which working directory it was started from.
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"


class Settings(BaseSettings):
    """Process-local settings read from the component's own `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    logging_level: str = "INFO"
    log_to_file: bool = False
    log_dir: Path = _DEFAULT_LOG_DIR

    @property
    def level(self) -> str:
        return self.logging_level.upper()

    @property
    def debug(self) -> bool:
        return self.level == "DEBUG"


@lru_cache
def get_settings() -> Settings:
    return Settings()
