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
    postgres_user: str = "defi"
    postgres_password: str = "defi"
    postgres_db: str = "defi_vault"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_service_role_key: str = ""
    alchemy_api_key: str = ""
    helius_api_key: str = ""
    lifi_api_key: str = ""
    anthropic_api_key: str = ""
    domain: str = "localhost"
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""
    app_secret_key: str = "change-me"
    environment: str = "development"
    monthly_budget_usd: float = 100.0
    claude_budget_usd: float = 50.0
    vps_cost_usd: float = 14.0


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
