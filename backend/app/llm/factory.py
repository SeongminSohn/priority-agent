from langchain.chat_models import init_chat_model
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import Settings, get_settings

_GOOGLE_GENAI = "google_genai"


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    settings = settings or get_settings()
    kwargs: dict[str, object] = {}
    if settings.llm_provider == _GOOGLE_GENAI:
        kwargs["api_key"] = settings.gemini_api_key.get_secret_value()
    return init_chat_model(
        model=settings.llm_model,
        model_provider=settings.llm_provider,
        **kwargs,
    )


def get_embeddings(settings: Settings | None = None) -> Embeddings:
    settings = settings or get_settings()
    if settings.llm_provider == _GOOGLE_GENAI:
        return GoogleGenerativeAIEmbeddings(
            model=settings.embedding_model,
            output_dimensionality=settings.embedding_dim,
            api_key=settings.gemini_api_key.get_secret_value(),
        )
    raise ValueError(f"Unsupported embeddings provider: {settings.llm_provider!r}")
