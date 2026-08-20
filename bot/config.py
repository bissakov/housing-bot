from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings(BaseSettings):
    """Validated application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: str = ""
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'bot.db'}"
    admin_ids: Annotated[set[int], NoDecode] = Field(default_factory=set)
    dev_mode: bool = False
    escalation_minutes: int = Field(default=20, ge=1, le=24 * 60)
    display_timezone: str = "Asia/Almaty"
    redis_url: str = ""

    llm_enabled: bool = False
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = Field(default=12.0, gt=0, le=120)
    llm_auto_category_threshold: float = Field(default=0.85, ge=0, le=1)
    # A model may block a draft as a duplicate only above this threshold and
    # only after the resident has answered a distinguishing question.
    llm_duplicate_confidence_threshold: float = Field(default=0.92, ge=0, le=1)

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value):
        if isinstance(value, str):
            if not value.strip():
                return set()
            try:
                return {int(item.strip()) for item in value.split(",") if item.strip()}
            except ValueError as exc:
                raise ValueError("ADMIN_IDS must be comma-separated integers") from exc
        return value

    @field_validator("display_timezone")
    @classmethod
    def validate_display_timezone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                "DISPLAY_TIMEZONE must be a valid IANA timezone, for example Asia/Almaty"
            ) from exc
        return value

    def validate_runtime(self) -> None:
        if not self.bot_token.strip():
            raise RuntimeError("BOT_TOKEN is required")
        if not self.dev_mode and self.database_url.startswith("sqlite"):
            import logging

            logging.getLogger(__name__).warning(
                "SQLite is configured outside DEV_MODE; PostgreSQL is recommended for production"
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Compatibility aliases. New infrastructure code should use get_settings().
settings = get_settings()
BOT_TOKEN = settings.bot_token
DATABASE_URL = settings.database_url
ADMIN_IDS = settings.admin_ids
DEV_MODE = settings.dev_mode
ESCALATION_MINUTES = settings.escalation_minutes
DISPLAY_TIMEZONE = settings.display_timezone
REDIS_URL = settings.redis_url
LLM_ENABLED = settings.llm_enabled
LLM_API_KEY = settings.llm_api_key
LLM_BASE_URL = settings.llm_base_url
LLM_MODEL = settings.llm_model
LLM_TIMEOUT_SECONDS = settings.llm_timeout_seconds
LLM_AUTO_CATEGORY_THRESHOLD = settings.llm_auto_category_threshold
LLM_DUPLICATE_CONFIDENCE_THRESHOLD = settings.llm_duplicate_confidence_threshold


def is_admin(telegram_id: int) -> bool:
    """Return whether Telegram configuration designates an administrator."""
    return telegram_id in ADMIN_IDS
