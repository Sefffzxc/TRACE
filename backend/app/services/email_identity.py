"""Serialize case-insensitive email ownership changes across public flows."""
from __future__ import annotations

import hashlib

from sqlalchemy import text


def canonical_email(value) -> str:
    return str(value).strip().lower()


async def lock_email_identity(db, email) -> None:
    # All account creation/correction writers use the same PostgreSQL lock,
    # before their final lower(email) uniqueness check. No password/provider
    # work may run while this transaction-scoped lock holds a connection.
    if db.get_bind().dialect.name == "postgresql":
        digest = hashlib.sha256(("trace-email:" + canonical_email(email)).encode()).digest()
        key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
