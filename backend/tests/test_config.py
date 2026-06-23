import pytest

from app.config import EMBEDDING_DIMS, Settings, get_settings


def test_settings_load_from_test_env() -> None:
    settings = get_settings()
    assert settings.anthropic_api_key == "sk-ant-test-key"
    assert settings.embedding_provider == "voyage"


def test_embedding_dim_matches_provider() -> None:
    settings = get_settings()
    assert settings.embedding_dim == EMBEDDING_DIMS["voyage"] == 1024


@pytest.mark.parametrize(
    ("provider", "dim"),
    [("voyage", 1024), ("openai", 1536), ("local", 768)],
)
def test_embedding_dim_per_provider(provider: str, dim: int) -> None:
    settings = Settings(embedding_provider=provider)  # type: ignore[arg-type]
    assert settings.embedding_dim == dim
