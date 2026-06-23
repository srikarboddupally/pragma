"""Declarative base for all ORM models.

Phase 1 adds tables to this metadata; Alembic's ``env.py`` imports ``Base`` to autogenerate
migrations. Keep this module import-light so Alembic can load it cheaply.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
