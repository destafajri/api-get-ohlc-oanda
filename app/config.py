from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded only from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    oanda_token: Annotated[SecretStr, Field(min_length=1)]
    oanda_environment: Literal["practice", "live"] = "practice"
    oanda_timeout_seconds: Annotated[float, Field(gt=0, le=60)] = 10.0

    @property
    def oanda_base_url(self) -> str:
        host = (
            "api-fxpractice.oanda.com"
            if self.oanda_environment == "practice"
            else "api-fxtrade.oanda.com"
        )
        return f"https://{host}/v3"


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError from exc


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is absent or invalid."""
