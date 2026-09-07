"""Configuration shared by service entrypoints; agents receive no database handle."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    environment: str = "development"
    database_url: str = "sqlite:///./securebank.db"
    redis_url: str = ""
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "securebank-demo"
    jwt_audience: str = "securebank-api"
    access_minutes: int = 15
    session_minutes: int = 60
    model_provider: str = "mock"
    local_model_provider: str = "ollama"
    local_model_name: str = "llama3.2"
    external_model_provider: str = ""
    external_model_name: str = ""
    allow_external_llm: bool = False
    model_input_usd_per_million: float | None = None
    model_output_usd_per_million: float | None = None
    ollama_base_url: str = "http://localhost:11434"
    banking_api_url: str = "http://127.0.0.1:8000"
    accounts_mcp_url: str = "http://127.0.0.1:8101/mcp"
    transactions_mcp_url: str = "http://127.0.0.1:8102/mcp"
    services_mcp_url: str = "http://127.0.0.1:8103/mcp"
    knowledge_mcp_url: str = "http://127.0.0.1:8104/mcp"
    checkpoint_url: str = "checkpoints.db"
    demo_otp: bool = True
    mount_ui: bool = True
    request_limit: int = 120
    cookie_secure: bool = False
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
