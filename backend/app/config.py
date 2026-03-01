"""Application settings via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load from environment; .env supported."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/defi_vault"
    redis_url: str = "redis://localhost:6379/0"
    supabase_url: str = ""
    supabase_jwt_secret: str = ""
    alchemy_api_key: str = ""
    helius_api_key: str = ""
    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""
    app_secret_key: str = "change-me"
    environment: str = "development"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
