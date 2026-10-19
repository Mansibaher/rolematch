"""Application settings. All values come from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ROLEMATCH_", extra="ignore")

    environment: str = "development"  # development | test | production
    database_url: str = "postgresql+psycopg://rolematch:rolematch@localhost:5432/rolematch"
    redis_url: str = "redis://localhost:6379/0"
    # Run Celery tasks inline (useful for tests and single-process demos).
    celery_eager: bool = False

    # Auth
    session_cookie_name: str = "rm_session"
    session_ttl_hours: int = 24 * 14
    cookie_secure: bool = False  # set True behind HTTPS
    csrf_header: str = "X-RoleMatch-CSRF"
    allow_registration: bool = True

    # Files
    upload_dir: Path = REPO_ROOT / "var" / "uploads"
    max_upload_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 12
    extraction_timeout_seconds: int = 20

    # Config files
    config_dir: Path = REPO_ROOT / "config"
    fixtures_dir: Path = REPO_ROOT / "fixtures"

    # Embeddings
    embedding_backend: str = "minilm"  # minilm | hashing
    embedding_model_dir: Path = REPO_ROOT / "models" / "all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # Job sources
    http_timeout_seconds: float = 20.0
    http_connect_timeout_seconds: float = 5.0
    http_max_retries: int = 3
    http_min_interval_seconds: float = 1.0  # per-host politeness delay
    lever_max_pages: int = 20
    close_after_consecutive_misses: int = 2
    stale_after_hours: int = 72
    user_agent: str = "RoleMatch/0.1 (personal job-search tool; contact: set ROLEMATCH_USER_AGENT)"

    # Optional LLM integration (off by default; the core workflow never needs it)
    llm_provider: str = "none"  # none | anthropic
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""

    cors_origins: list[str] = ["http://localhost:3000"]

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
