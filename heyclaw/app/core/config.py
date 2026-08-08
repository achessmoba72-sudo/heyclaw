from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    logging_level: str = "INFO"
    log_to_file: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
