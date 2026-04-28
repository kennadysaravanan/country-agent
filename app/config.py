"""
Centralized configuration.

All env vars are read here ONCE and exposed as a frozen `settings` object.
Other modules import `settings` instead of reading os.environ themselves.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Required
    groq_api_key: str

    # LLM
    groq_model: str = "llama-3.3-70b-versatile"

    # REST Countries API
    countries_api_base: str = "https://restcountries.com/v3.1"
    http_timeout_seconds: int = 10

    # Memory
    max_history_messages: int = 20  # cap to keep context window small

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()
