from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AnyHttpUrl, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


SETTINGS_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    case_sensitive=False,
)


class Settings(BaseSettings):
    """OANDA application settings loaded from environment variables or .env."""

    model_config = SETTINGS_CONFIG

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


class McpSettings(BaseSettings):
    """Remote MCP transport and access settings."""

    model_config = SETTINGS_CONFIG

    # Legacy/static Bearer access for clients such as Codex and Claude Code.
    mcp_auth_token: SecretStr | None = None

    # OAuth resource-server settings. When all three are configured, the MCP
    # endpoint advertises RFC 9728 metadata and validates OAuth JWTs while still
    # accepting MCP_AUTH_TOKEN when it is present.
    mcp_public_url: AnyHttpUrl | None = None
    mcp_oauth_issuer_url: AnyHttpUrl | None = None
    mcp_oauth_jwks_url: AnyHttpUrl | None = None

    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        raise ConfigurationError from exc


@lru_cache
def get_mcp_settings() -> McpSettings:
    return McpSettings()


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is absent or invalid."""
