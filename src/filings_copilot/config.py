"""Runtime settings, read from environment variables (prefix FILINGS_) or a .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FILINGS_", env_file=".env", extra="ignore")

    # SEC fair-access policy requires a descriptive User-Agent with a contact address.
    sec_user_agent: str = Field(default="", description="e.g. 'filings-copilot you@example.com'")
    sec_max_requests_per_second: float = 8.0
    sec_force_ipv4: bool = False

    db_path: Path = Path("data/filings.duckdb")
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    llm_model: str = "claude-opus-5-5"
    llm_effort: str = "medium"
    max_agent_turns: int = 8

    chunk_size_words: int = 220
    chunk_overlap_words: int = 40


@lru_cache
def get_settings() -> Settings:
    return Settings()
