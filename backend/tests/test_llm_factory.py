import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.config import Settings
from app.llm.factory import get_chat_model, get_embeddings

SECRET = "dummy-secret-key-123"


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, gemini_api_key=SECRET, **overrides)  # type: ignore[arg-type]


def test_google_genai_chat_model() -> None:
    model = get_chat_model(_settings())
    assert isinstance(model, BaseChatModel)


def test_google_genai_embeddings() -> None:
    emb = get_embeddings(_settings(embedding_dim=768))
    assert isinstance(emb, Embeddings)


def test_unsupported_embeddings_provider_raises_without_leaking_key() -> None:
    with pytest.raises(ValueError, match="Unsupported embeddings provider") as exc:
        get_embeddings(_settings(llm_provider="other"))
    assert SECRET not in str(exc.value)


def test_unsupported_chat_provider_raises_without_leaking_key() -> None:
    with pytest.raises(ValueError) as exc:
        get_chat_model(_settings(llm_provider="not_a_provider"))
    assert SECRET not in str(exc.value)
