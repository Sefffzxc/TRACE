"""Keep a staff mobile number attached to one account across public flows."""
from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException
from sqlalchemy import false, select, text, update

from app.models.staff import Staff


CONTACT_IN_USE = "An account with that contact number already exists."


def _legacy_contact_identity(value: str) -> str:
    """Older forms allowed separators and country prefixes; compare the number."""
    value = re.sub(r"[\s()\-]", "", value)
    if value.startswith("+63"):
        value = "0" + value[3:]
    elif value.startswith("63") and len(value) == 12:
        value = "0" + value[2:]
    return value


async def ensure_contact_available(db, number: str, *, exclude_staff_id=None) -> None:
    """Serialize the check with account writes without rewriting legacy data.

    PostgreSQL uses a transaction lock per number. SQLite takes its writer lock
    before checking, including when no matching account exists yet. Callers
    keep this transaction open until the account change is committed.
    """
    if db.get_bind().dialect.name == "postgresql":
        digest = hashlib.sha256(("trace-phone:" + number).encode()).digest()
        key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    else:
        await db.execute(update(Staff).where(false())
                         .values(contact_number=Staff.contact_number)
                         .execution_options(synchronize_session=False))
    # Legacy values are formatted inconsistently. Compare these two columns
    # rather than loading staff objects or rewriting another person's details.
    stmt = select(Staff.id, Staff.contact_number)
    if exclude_staff_id is not None:
        stmt = stmt.where(Staff.id != exclude_staff_id)
    rows = (await db.execute(stmt)).all()
    if any(_legacy_contact_identity(value) == number for _, value in rows):
        raise HTTPException(409, detail=CONTACT_IN_USE)
