from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/priority_agent"
    gemini_api_key: SecretStr = SecretStr("")
    llm_provider: str = "google_genai"
    llm_model: str = "gemini-2.5-flash"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 1536
    trello_api_key: SecretStr = SecretStr("")
    trello_token: SecretStr = SecretStr("")
    log_level: str = "INFO"
    app_env: str = "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
