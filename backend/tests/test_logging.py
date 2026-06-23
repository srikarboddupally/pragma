from loguru import logger

from app.config import get_settings
from app.logging import configure_logging


def test_secret_is_redacted_from_logs() -> None:
    configure_logging()
    secret = get_settings().anthropic_api_key
    assert secret  # sanity: the test env set it

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="INFO", format="{message}")
    try:
        logger.info(f"calling Claude with key {secret}")
    finally:
        logger.remove(sink_id)

    joined = "".join(captured)
    assert secret not in joined
    assert "***REDACTED***" in joined
