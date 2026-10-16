"""Which classes a teacher may see and grade.

A class is a section and a subject. The principal sets them up in Class
Management -> Teacher assignments, and this module turns those rows into the
one rule every screen follows:

* **Grading.** A teacher enters or edits grades only for the subjects they
  are assigned to teach in a section.
* **Seeing.** A teacher sees the learners and subjects they teach. In the
  section they ADVISE they see every subject, because following the whole
  class and talking to the parents is the adviser's job, but they still
  grade only their own subjects there.
* **A blank subject** on an assignment means "every subject in this section".
  That keeps older set-ups (a teacher attached to a section with no subject)
  working. Once a teacher has any specific subject in a section, they are
  limited to those subjects in it.

Administrators are not limited: they run the school, and their screens show
school-level figures rather than individual records.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.academic import Subject, TeacherAssignment
from app.models.staff import ROLE_ADMIN, Staff
from app.models.students import StudentEnrollment

ALL = "*"  # stands for "every subject" in a section


@dataclass
class ClassAccess:
    unrestricted: bool = False
    # section_id -> set of subject names the teacher grades there, or {ALL}
    grade: dict[int, set[str]] = field(default_factory=dict)
    # sections the teacher advises: every subject there is visible
    advisory: set[int] = field(default_factory=set)
    # enrollment_id -> section_id, for checks made from a record
    section_of: dict[int, int] = field(default_factory=dict)

    @property
    def sections(self) -> set[int]:
        """Every section the teacher has anything to do with."""
        return set(self.grade) | self.advisory

    def can_grade(self, section_id: Optional[int], subject: Optional[str]) -> bool:
        if self.unrestricted:
            return True
        subjects = self.grade.get(section_id)
        return bool(subjects) and (ALL in subjects or subject in subjects)

    def can_see(self, section_id: Optional[int], subject: Optional[str]) -> bool:
        if self.unrestricted:
            return True
        return section_id in self.advisory or self.can_grade(section_id, subject)

    def can_see_section(self, section_id: Optional[int]) -> bool:
        return self.unrestricted or section_id in self.sections

    def can_see_record(self, enrollment_id: Optional[int], subject: Optional[str]) -> bool:
        """For anything keyed by enrolment and subject: records, flags, forecasts."""
        if self.unrestricted:
            return True
        return self.can_see(self.section_of.get(enrollment_id), subject)

    def require_grade(self, section_id: Optional[int], subject: Optional[str]) -> None:
        if not self.can_grade(section_id, subject):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You are not assigned to teach {subject or 'this subject'} "
                       "in this section.",
            )

    def require_section(self, section_id: Optional[int]) -> None:
        if not self.can_see_section(section_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not assigned to this section.",
            )


async def class_access(db: AsyncSession, current: Staff, school_id: int) -> ClassAccess:
    """The classes this person may see and grade, read from their assignments."""
    if current.role == ROLE_ADMIN:
        return ClassAccess(unrestricted=True)

    subjects = {
        s.subject_id: s.subject_name
        for s in (
            await db.execute(select(Subject).where(Subject.school_id == school_id))
        ).scalars()
    }
    rows = (
        await db.execute(
            select(TeacherAssignment).where(
                TeacherAssignment.school_id == school_id,
                TeacherAssignment.teacher_id == current.id,
            )
        )
    ).scalars().all()

    access = ClassAccess()
    specific: dict[int, set[str]] = {}
    blank: set[int] = set()
    for a in rows:
        if a.is_adviser:
            access.advisory.add(a.section_id)
        if a.subject_id is None:
            blank.add(a.section_id)
        elif a.subject_id in subjects:
            specific.setdefault(a.section_id, set()).add(subjects[a.subject_id])

    for section_id in blank | set(specific):
        # Any specific subject in a section limits the teacher to those there.
        access.grade[section_id] = specific.get(section_id) or {ALL}

    if access.sections:
        access.section_of = {
            e.enrollment_id: e.section_id
            for e in (
                await db.execute(
                    select(StudentEnrollment).where(
                        StudentEnrollment.school_id == school_id,
                        StudentEnrollment.section_id.in_(access.sections),
                    )
                )
            ).scalars()
        }
    return access
