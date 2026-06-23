"""Logging setup (loguru) with a secret-redaction filter.

The redactor scrubs any configured secret value out of log messages before they are written.
It is a safety net, not a license to log secrets — never pass credentials to the logger.
"""

from __future__ import annotations

import sys

from loguru import logger

from app.config import get_settings

# Settings fields whose values must never appear in logs.
_SECRET_FIELDS: tuple[str, ...] = (
    "anthropic_api_key",
    "voyage_api_key",
    "openai_api_key",
    "stripe_secret_key",
    "slack_client_secret",
    "github_app_private_key",
    "notion_client_secret",
    "pragma_encryption_key",
)

_REDACTION = "***REDACTED***"


def _make_patcher():
    settings = get_settings()
    secrets = [v for f in _SECRET_FIELDS if (v := getattr(settings, f))]

    def patch(record) -> None:
        message = record["message"]
        for secret in secrets:
            if secret in message:
                message = message.replace(secret, _REDACTION)
        record["message"] = message

    return patch


def configure_logging(level: str = "INFO"):
    """Configure the global loguru logger. Idempotent — safe to call at startup."""
    logger.remove()
    logger.configure(patcher=_make_patcher())
    logger.add(
        sys.stderr,
        level=level,
        backtrace=False,
        diagnose=False,  # never expand variables into tracebacks (may contain secrets)
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
        ),
    )
    return logger
