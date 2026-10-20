"""Runtime settings, read from environment variables (prefix FILINGS_) or a .env file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FILINGS_", env_file=".env", extra="ignore")

    # SEC fair-access policy requires a descriptive User-Agent with a contact address.
    sec_user_agent: str = Field(default="", description="e.g. 'filings-copilot you@example.com'")
    sec_max_requests_per_second: float = 8.0
    sec_force_ipv4: bool = False

    db_path: Path = Path("data/filings.duckdb")
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # "anthropic": Claude via the Anthropic SDK (credentials: ANTHROPIC_API_KEY).
    # "openrouter": any model via OpenRouter, e.g. FILINGS_LLM_MODEL=openai/gpt-5.
    llm_provider: Literal["anthropic", "openrouter"] = "anthropic"
    llm_model: str = "claude-opus-5-5"
    openrouter_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "FILINGS_OPENROUTER_API_KEY"),
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_effort: str = "medium"
    max_agent_turns: int = 8

    # HTTP API. Limits protect LLM credits on a public demo; 0 disables a limit.
    cors_origins: str = "*"
    demo_questions_per_hour: int = 0
    demo_daily_question_cap: int = 0

    chunk_size_words: int = 220
    chunk_overlap_words: int = 40


@lru_cache
def get_settings() -> Settings:
    return Settings()
