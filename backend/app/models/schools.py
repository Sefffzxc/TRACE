"""The school itself — the tenant every other record belongs to.

One deployment serves many schools. Every table that holds a school's work
carries a ``school_id``, and every query that reads that work is filtered by
the caller's own school. A teacher at one school cannot see another school's
students, and that is enforced in one place (``app/api/deps.py``) rather than
remembered separately in each endpoint.

Why a school is a record rather than a configuration file
---------------------------------------------------------
Schools differ in ways that are not cosmetic. A School of Tomorrow school
tracks progress through numbered PACE booklets; an ordinary DepEd school has no
such thing and would be shown an empty, meaningless screen. That difference is
not "public vs private" — most private schools in the Philippines follow the
DepEd curriculum too — so it is asked and stored as its own question.

The approval chain
------------------
    super admin  ->  approves a school
    principal    ->  approves their own teachers
    teacher      ->  works inside that one school

Which is the same mechanism at two levels, not two mechanisms.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.db.session import Base
from app.db.types import GUID

# How a school is run. Administrative only — it does NOT imply a curriculum.
TYPE_PUBLIC = "public"
TYPE_PRIVATE = "private"

# Where a school is in the approval chain.
SCHOOL_PENDING = "pending"
SCHOOL_APPROVED = "approved"
SCHOOL_REJECTED = "rejected"
SCHOOL_DEACTIVATED = "deactivated"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class School(Base):
    __tablename__ = "schools"

    school_id = Column(Integer, primary_key=True, index=True)

    # ── Identity ──────────────────────────────────────────────────────────
    name = Column(String(200), nullable=False, index=True)
    short_name = Column(String(40), nullable=True)
    # public | private. This is who runs the school. It is recorded because it
    # is worth reporting on, and it is deliberately NOT used to decide which
    # features appear — see `tracks_units` for that.
    school_type = Column(String(20), nullable=False, default=TYPE_PRIVATE)

    # ── Location ──────────────────────────────────────────────────────────
    # City matters: there are many schools in the Philippines sharing a name,
    # so the teacher's sign-up list shows the city alongside it.
    address = Column(String(255), nullable=False, default="")
    city = Column(String(100), nullable=False, default="")
    province = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)

    # ── Contact ───────────────────────────────────────────────────────────
    contact_number = Column(String(30), nullable=True)
    contact_email = Column(String(255), nullable=True)

    # ── How this school teaches ───────────────────────────────────────────
    # The question that actually decides what the teacher sees. A School of
    # Tomorrow school answers yes and calls them PACEs; a DepEd school answers
    # no and never sees that screen.
    tracks_units = Column(Boolean, nullable=False, default=False)
    unit_label_singular = Column(String(40), nullable=False, default="Module")
    unit_label_plural = Column(String(40), nullable=False, default="Modules")
    default_units_per_subject = Column(Integer, nullable=False, default=12)

    # ── How this school grades ────────────────────────────────────────────
    # Stored rather than hard-coded: passing marks differ between schools, and
    # national policy changes. A grading rule written into the code is a rule
    # that goes stale without anyone noticing.
    passing_mark = Column(Float, nullable=False, default=75.0)
    # Three terms under the MATATAG class record, not four quarters.
    grading_periods = Column(Integer, nullable=False, default=3)
    grading_period_label = Column(String(30), nullable=False, default="Quarter")

    # ── Approval ──────────────────────────────────────────────────────────
    status = Column(String(20), nullable=False, default=SCHOOL_PENDING, index=True)
    rejection_reason = Column(Text, nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by = Column(GUID(), nullable=True)  # the super admin who decided

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    @property
    def is_usable(self) -> bool:
        """Whether people may sign in and work inside this school."""
        return self.status == SCHOOL_APPROVED

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.city})" if self.city else self.name

    def __repr__(self) -> str:  # pragma: no cover
        return f"<School {self.name} [{self.status}]>"
