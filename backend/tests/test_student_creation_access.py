"""Assigned teachers and advisers create rosters; principals do not.

These exercise the real HTTP handlers and access service against isolated
SQLite databases with foreign keys enabled. No production connection is used.
"""
from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import students
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, SectionAdviser, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import Student, StudentEnrollment
from app.services.access import class_access
from app.services.roster_access import require_roster_section


class StudentCreationAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        self.people = {}
        async with self.sessions() as db:
            db.add_all([School(school_id=value, name=f"School {value}", status="approved") for value in (1, 2)])
            await db.flush()
            for name, role, school_id in (
                ("Teacher", "teacher", 1), ("Adviser", "teacher", 1),
                ("Unassigned", "teacher", 1), ("FormerAdviser", "teacher", 1),
                ("Principal", "admin", 1), ("Operator", "super_admin", None),
                ("ForeignTeacher", "teacher", 2),
            ):
                identity = uuid4()
                self.people[name] = identity
                db.add(Staff(id=identity, school_id=school_id, role=role,
                    first_name=name, last_name="Staff", contact_number="09123456789",
                    email=f"{identity}@example.com", password_hash="unused",
                    is_active=True, is_approved=True, account_status="approved"))
            db.add_all([
                GradeLevel(grade_level_id=1, school_id=1, level="7", name="Grade 7"),
                GradeLevel(grade_level_id=2, school_id=1, level="8", name="Grade 8"),
                GradeLevel(grade_level_id=3, school_id=2, level="7", name="Grade 7"),
                SchoolYear(school_year_id=1, school_id=1, year="2026-2027",
                    start_date=date(2026, 6, 1), end_date=date(2027, 3, 31), is_current=True),
                SchoolYear(school_year_id=2, school_id=2, year="2026-2027",
                    start_date=date(2026, 6, 1), end_date=date(2027, 3, 31), is_current=True),
            ])
            await db.flush()
            db.add_all([
                Section(section_id=10, school_id=1, grade_level_id=1, name="Faith"),
                Section(section_id=11, school_id=1, grade_level_id=2, name="Faith"),
                Section(section_id=12, school_id=1, grade_level_id=1, name="Hope"),
                Section(section_id=20, school_id=2, grade_level_id=3, name="Faith"),
                Subject(subject_id=100, school_id=1, grade_level_id=1, subject_name="Mathematics"),
                Subject(subject_id=101, school_id=2, grade_level_id=3, subject_name="Mathematics"),
            ])
            await db.flush()
            db.add_all([
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Teacher"], section_id=10, subject_id=100),
                SectionAdviser(school_id=1, school_year_id=1, teacher_id=self.people["Adviser"], section_id=11),
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["FormerAdviser"], section_id=12,
                    subject_id=None, is_adviser=True),
                # Even a legacy teaching row cannot turn a principal into a teacher.
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Principal"], section_id=12, subject_id=100),
                TeacherAssignment(school_id=2, school_year_id=2, teacher_id=self.people["ForeignTeacher"], section_id=20, subject_id=101),
            ])
            await db.commit()
        self.actor = self.people["Teacher"]

        async def isolated_db():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def current_actor(db=Depends(get_db)):
            return await db.get(Staff, self.actor)

        self.app = FastAPI()
        self.app.include_router(students.router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[deps.get_current_user] = current_actor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        self.statements = []

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def collect_statements(connection, cursor, statement, parameters, context, executemany):
            self.statements.append(statement.lower().strip())

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    def _student_payload(self, **changes):
        return {"first_name": "Ada", "last_name": "Learner", "grade_level_id": 1,
                "section_id": 10, "school_year_id": 1, **changes}

    def _enrollment_payload(self, student_id, **changes):
        return {"student_id": student_id, "grade_level_id": 1, "section_id": 10,
                "school_year_id": 1, **changes}

    async def _counts(self):
        async with self.sessions() as db:
            return (
                await db.scalar(select(func.count()).select_from(Student)),
                await db.scalar(select(func.count()).select_from(StudentEnrollment)),
            )

    async def _existing_student(self, school=1):
        async with self.sessions() as db:
            row = Student(school_id=school, first_name="Existing", last_name="Learner")
            db.add(row)
            await db.commit()
            return row.student_id

    async def _reject_without_writes(self, path, payload, expected):
        counts = await self._counts()
        start = len(self.statements)
        response = await self.client.post(path, json=payload)
        self.assertEqual(response.status_code, expected, response.text)
        attempted_inserts = [sql for sql in self.statements[start:] if sql.startswith(
            ("insert into students", "insert into student_enrollments"))]
        self.assertEqual(attempted_inserts, [], "Invalid destinations must be rejected before inserts, not just rolled back")
        self.assertEqual(await self._counts(), counts)
        return response

    async def test_fresh_school_subject_teacher_can_create_student_and_enrollment(self):
        self.assertEqual(await self._counts(), (0, 0))
        response = await self.client.post("/api/students", json=self._student_payload())
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual((row["grade_level_id"], row["section_id"], row["school_year_id"]), (1, 10, 1))
        self.assertTrue(row["login_id"].startswith("S"))
        self.assertEqual(await self._counts(), (1, 1))
        async with self.sessions() as db:
            student = await db.get(Student, row["student_id"])
            self.assertEqual(student.created_by, self.actor)
            enrollment = await db.get(StudentEnrollment, row["enrollment_id"])
            self.assertEqual(enrollment.enrolled_by, self.actor)

    async def test_invalid_birth_dates_are_rejected_before_enrollment(self):
        with patch("app.schemas.students.student_today", return_value=date(2026, 11, 23)):
            for value in ("2028-01-01", "2026-11-24", "1899-12-31", "0001-01-01", "2026-02-30", "not-a-date"):
                with self.subTest(birth_date=value):
                    response = await self._reject_without_writes(
                        "/api/students", self._student_payload(birth_date=value), 422,
                    )
                    self.assertEqual(response.json()["detail"][0]["loc"], ["body", "birth_date"])

    async def test_valid_birth_dates_and_unknown_birth_date_remain_supported(self):
        with patch("app.schemas.students.student_today", return_value=date(2026, 11, 23)):
            for value in ("1900-01-01", "2015-02-28", "2016-02-29", "2026-11-23", None):
                with self.subTest(birth_date=value):
                    response = await self.client.post("/api/students", json=self._student_payload(birth_date=value))
                    self.assertEqual(response.status_code, 201, response.text)
                    self.assertEqual(response.json()["birth_date"], value)

    async def test_student_update_validates_birth_date_without_changing_existing_details(self):
        response = await self.client.post("/api/students", json=self._student_payload(birth_date="2015-01-01"))
        self.assertEqual(response.status_code, 201, response.text)
        identity = response.json()["student_id"]
        with patch("app.schemas.students.student_today", return_value=date(2026, 11, 23)):
            for value in ("2028-01-01", "1899-12-31", "2026-02-30"):
                response = await self.client.patch(f"/api/students/{identity}", json={"birth_date": value, "first_name": "Changed"})
                self.assertEqual(response.status_code, 422, response.text)
                async with self.sessions() as db:
                    row = await db.get(Student, identity)
                    self.assertEqual(row.birth_date, date(2015, 1, 1))
                    self.assertEqual(row.first_name, "Ada")
            response = await self.client.patch(f"/api/students/{identity}", json={"birth_date": "2016-02-29"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["birth_date"], "2016-02-29")
            response = await self.client.patch(f"/api/students/{identity}", json={"birth_date": None})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(response.json()["birth_date"])

    async def test_adviser_without_subject_assignment_can_create_roster_only(self):
        self.actor = self.people["Adviser"]
        response = await self.client.post("/api/students", json=self._student_payload(grade_level_id=2, section_id=11))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(await self._counts(), (1, 1))
        async with self.sessions() as db:
            access = await class_access(db, await db.get(Staff, self.actor), 1)
            self.assertEqual(access.grade, {})
            self.assertFalse(access.can_grade(11, "Mathematics"))

    async def test_section_grade_and_current_year_can_be_inferred_safely(self):
        payload = self._student_payload()
        payload.pop("grade_level_id")
        payload.pop("school_year_id")
        response = await self.client.post("/api/students", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual((response.json()["grade_level_id"], response.json()["school_year_id"]), (1, 1))

    async def test_student_creation_cannot_omit_section_to_bypass_assignments(self):
        for actor in ("Teacher", "Unassigned"):
            self.actor = self.people[actor]
            for section in (None, "omit"):
                with self.subTest(actor=actor, section=section):
                    payload = self._student_payload(section_id=section)
                    if section == "omit":
                        payload.pop("section_id")
                    await self._reject_without_writes("/api/students", payload, 400)

    async def test_unassigned_section_and_teacher_cannot_create(self):
        await self._reject_without_writes("/api/students", self._student_payload(section_id=12), 403)
        self.actor = self.people["Unassigned"]
        await self._reject_without_writes("/api/students", self._student_payload(), 403)
        self.actor = self.people["FormerAdviser"]
        await self._reject_without_writes("/api/students", self._student_payload(section_id=12), 403)

    async def test_cross_school_and_unknown_destinations_cannot_create(self):
        for changes in ({"section_id": 20, "grade_level_id": 3}, {"section_id": 99999},
                        {"grade_level_id": 3}, {"school_year_id": 2}):
            with self.subTest(changes=changes):
                await self._reject_without_writes("/api/students", self._student_payload(**changes), 400)

    async def test_grade_mismatch_is_rejected_before_student_insert(self):
        await self._reject_without_writes("/api/students", self._student_payload(grade_level_id=2), 400)

    async def test_no_school_year_is_rejected_before_student_insert(self):
        async with self.sessions() as db:
            (await db.get(SchoolYear, 1)).is_current = False
            await db.commit()
        payload = self._student_payload()
        payload.pop("school_year_id")
        await self._reject_without_writes("/api/students", payload, 400)

    async def test_principal_and_operator_cannot_create_even_with_section_or_omitted_section(self):
        for actor in ("Principal", "Operator"):
            self.actor = self.people[actor]
            for section in (10, 12, None):
                with self.subTest(actor=actor, section=section):
                    await self._reject_without_writes("/api/students", self._student_payload(section_id=section), 403)

    async def test_subject_teacher_can_enroll_existing_student(self):
        student = await self._existing_student()
        response = await self.client.post("/api/enrollments", json=self._enrollment_payload(student))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["section_id"], 10)
        self.assertEqual(await self._counts(), (1, 1))

    async def test_adviser_can_enroll_existing_student_in_advisory_section(self):
        student = await self._existing_student()
        self.actor = self.people["Adviser"]
        response = await self.client.post("/api/enrollments", json=self._enrollment_payload(student, section_id=11, grade_level_id=2))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["section_id"], 11)

    async def test_principal_and_operator_cannot_enroll_existing_student(self):
        student = await self._existing_student()
        for actor in ("Principal", "Operator"):
            with self.subTest(actor=actor):
                self.actor = self.people[actor]
                await self._reject_without_writes("/api/enrollments", self._enrollment_payload(student), 403)

    async def test_enrollment_rejects_unassigned_cross_school_and_grade_mismatch(self):
        student = await self._existing_student()
        for changes, status in (({"section_id": 12}, 403),
                ({"section_id": 20, "grade_level_id": 3}, 400), ({"grade_level_id": 2}, 400),
                ({"school_year_id": 2}, 400), ({"section_id": None}, 422)):
            with self.subTest(changes=changes):
                await self._reject_without_writes("/api/enrollments", self._enrollment_payload(student, **changes), status)

    async def test_cannot_enroll_another_schools_student(self):
        foreign = await self._existing_student(school=2)
        await self._reject_without_writes("/api/enrollments", self._enrollment_payload(foreign), 404)

    async def test_existing_same_year_enrollment_cannot_be_added_twice(self):
        student = await self._existing_student()
        payload = self._enrollment_payload(student)
        self.assertEqual((await self.client.post("/api/enrollments", json=payload)).status_code, 201)
        await self._reject_without_writes("/api/enrollments", payload, 409)

    async def test_roster_access_check_is_read_only_for_import_previews(self):
        async with self.sessions() as db:
            current = await db.get(Staff, self.actor)
            start = len(self.statements)
            section = await require_roster_section(db, current, 1, 10, 1)
            self.assertEqual(section.section_id, 10)
            self.assertFalse(any(sql.startswith(("insert", "update", "delete")) for sql in self.statements[start:]))


if __name__ == "__main__":
    unittest.main()
