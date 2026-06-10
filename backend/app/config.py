from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql://healthsync:healthsync@localhost:5432/healthsync"

    # Auth
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7

    # Encryption key for OAuth tokens at rest (Fernet, base64 32-byte key).
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""

    # WHOOP OAuth2 app credentials (https://developer.whoop.com)
    whoop_client_id: str = ""
    whoop_client_secret: str = ""
    whoop_redirect_uri: str = "http://localhost:8000/api/integrations/whoop/callback"

    # Oura OAuth2 app credentials (https://cloud.ouraring.com/oauth/applications)
    oura_client_id: str = ""
    oura_client_secret: str = ""
    oura_redirect_uri: str = "http://localhost:8000/api/integrations/oura/callback"

    # Anthropic — the SDK also reads ANTHROPIC_API_KEY from the environment.
    # The handover doc pinned claude-sonnet-4-20250514, which retires 2026-06-15;
    # claude-sonnet-4-6 is its drop-in replacement.
    anthropic_model: str = "claude-sonnet-4-6"


@lru_cache
def get_settings() -> Settings:
    return Settings()
