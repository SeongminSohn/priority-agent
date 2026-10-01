from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5433/priority_agent"
    gemini_api_key: SecretStr = SecretStr("")
    llm_provider: str = "google_genai"
    llm_model: str = "gemini-2.5-flash"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 1536
    trello_api_key: SecretStr = SecretStr("")
    trello_token: SecretStr = SecretStr("")
    triage_api_key: SecretStr = SecretStr("")
    log_level: str = "INFO"
    app_env: str = "local"

    @model_validator(mode="after")
    def _require_secrets_outside_local(self) -> "Settings":
        if self.app_env == "local":
            return self
        required = ("gemini_api_key", "trello_api_key", "trello_token", "triage_api_key")
        missing = [name.upper() for name in required if not getattr(self, name).get_secret_value()]
        if missing:
            raise ValueError(f"Missing required settings: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
