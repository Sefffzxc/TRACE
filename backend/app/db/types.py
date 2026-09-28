"""Portable column types.

The original system was PostgreSQL-only (it used ``postgresql.UUID``).  These
type decorators keep the exact same Python-side behaviour while also working on
SQLite, so the project can be developed locally without a database server.
"""
from __future__ import annotations

import uuid

from sqlalchemy import CHAR, String, TypeDecorator
from sqlalchemy.dialects.postgresql import INET as PG_INET
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID when available, otherwise stores the value as
    a 36-character string.  Always returns ``uuid.UUID`` objects to Python.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class IPAddress(TypeDecorator):
    """PostgreSQL INET, plain VARCHAR everywhere else."""

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_INET())
        return dialect.type_descriptor(String(45))
