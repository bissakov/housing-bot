import pytest
from pydantic import ValidationError

from bot.config import Settings


def test_settings_parse_comma_separated_admin_ids():
    settings = Settings(admin_ids="10, 20,30")
    assert settings.admin_ids == {10, 20, 30}


def test_settings_reject_invalid_escalation_interval():
    with pytest.raises(ValidationError):
        Settings(escalation_minutes=0)


def test_settings_reject_invalid_llm_threshold():
    with pytest.raises(ValidationError):
        Settings(llm_auto_category_threshold=1.5)

    with pytest.raises(ValidationError):
        Settings(llm_duplicate_confidence_threshold=-0.1)


def test_runtime_validation_requires_bot_token():
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        Settings(bot_token="").validate_runtime()
