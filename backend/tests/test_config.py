import pytest
from pydantic import ValidationError

from app.config import Settings


def test_local_allows_empty_secrets() -> None:
    Settings(app_env="local")


def test_non_local_requires_secrets_and_reports_names_only() -> None:
    with pytest.raises(ValidationError) as exc:
        Settings(app_env="prod", gemini_api_key="s3cret")
    msg = str(exc.value)
    assert "TRELLO_TOKEN" in msg and "TRIAGE_API_KEY" in msg
    assert "s3cret" not in msg


def test_non_local_with_all_secrets_ok() -> None:
    Settings(
        app_env="prod",
        gemini_api_key="a",
        trello_api_key="b",
        trello_token="c",
        triage_api_key="d",
    )
