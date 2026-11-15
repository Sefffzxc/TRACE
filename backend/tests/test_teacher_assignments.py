"""Exclusive teaching assignments, wildcard overlap and concurrent saves.

Default tests use isolated temporary SQLite files. Optional PostgreSQL races
require TRACE_TEST_POSTGRES_URL pointing explicitly to a disposable loopback DB;
each PostgreSQL test creates and removes its own randomly named schema.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

import app.models
from app.api import deps
from app.api.routers import academic
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, Section, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff


TEST_POSTGRES_URL = os.environ.get("TRACE_TEST_POSTGRES_URL")


class AssignmentFixture:
    postgres_url = None

    async def asyncSetUp(self):
        self.temporary_directory = None
        self.schema = None
        if self.postgres_url:
            if make_url(self.postgres_url).host not in {"localhost", "127.0.0.1", "::1"}:
                self.fail("Assignment tests require an explicitly configured loopback PostgreSQL database.")
            self.schema = "trace_assignment_test_" + uuid4().hex
            self.engine = create_async_engine(self.postgres_url, connect_args={
                "server_settings": {"search_path": self.schema},
            })
        else:
            self.temporary_directory = TemporaryDirectory(prefix="trace_assignment_test_")
            database_path = Path(self.temporary_directory.name) / "assignments.sqlite3"
            self.engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            if self.schema:
                await connection.execute(CreateSchema(self.schema))
            else:
                await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        self.people = {}
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"Test School {i}", status="approved") for i in (1, 2)])
            await db.flush()
            for name, role, school_id in (
                ("Principal", "admin", 1), ("Teacher A", "teacher", 1),
                ("Teacher B", "teacher", 1), ("Teacher C", "teacher", 1),
                ("Private Other Teacher", "teacher", 2), ("Operator", "super_admin", None),
            ):
                identity = uuid4()
                person = Staff(id=identity, school_id=school_id, email=f"{identity}@example.com",
                    password_hash="unused", first_name=name, last_name="Example", contact_number="",
                    role=role, account_status="approved", is_approved=True, is_active=True)
                db.add(person)
                self.people[name] = person
            db.add_all([
                GradeLevel(grade_level_id=1, school_id=1, level="7", name="Grade 7"),
                GradeLevel(grade_level_id=2, school_id=1, level="8", name="Grade 8"),
                GradeLevel(grade_level_id=3, school_id=2, level="7", name="Grade 7"),
            ])
            await db.flush()
            db.add_all([
                Section(section_id=10, school_id=1, grade_level_id=1, name="Faith"),
                Section(section_id=11, school_id=1, grade_level_id=1, name="Hope"),
                Section(section_id=20, school_id=2, grade_level_id=3, name="Other School Section"),
                Subject(subject_id=100, school_id=1, grade_level_id=1, subject_name="English"),
                Subject(subject_id=101, school_id=1, grade_level_id=1, subject_name="Science"),
                Subject(subject_id=102, school_id=1, grade_level_id=2, subject_name="Grade 8 Mathematics"),
                Subject(subject_id=200, school_id=2, grade_level_id=3, subject_name="Other School Subject"),
            ])
            await db.commit()
        self.actor = self.people["Principal"].id
        self.start_barrier = None

        async def database():
            async with self.sessions() as db:
                try:
                    if self.start_barrier is not None:
                        await self.start_barrier.wait()
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def user(db=Depends(get_db)):
            return await db.get(Staff, self.actor)

        application = FastAPI()
        self.app = application
        application.include_router(academic.router)
        application.dependency_overrides[get_db] = database
        application.dependency_overrides[deps.get_current_user] = user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()
        try:
            if self.schema:
                if not self.schema.startswith("trace_assignment_test_") or len(self.schema.rsplit("_", 1)[-1]) != 32:
                    raise RuntimeError("Unexpected test schema name.")
                async with self.engine.begin() as connection:
                    await connection.execute(DropSchema(self.schema, cascade=True))
        finally:
            await self.engine.dispose()
            if self.temporary_directory is not None:
                self.temporary_directory.cleanup()

    def _payload(self, teacher="Teacher A", section=10, subject=100, adviser=False):
        return {"teacher_id": str(self.people[teacher].id), "section_id": section,
                "subject_id": subject, "is_adviser": adviser}

    async def _create(self, teacher="Teacher A", section=10, subject=100, adviser=False):
        return await self.client.post("/api/teacher-assignments", json=self._payload(teacher, section, subject, adviser))

    async def _count(self):
        async with self.sessions() as db:
            return await db.scalar(select(func.count()).select_from(TeacherAssignment))

    async def _assert_race(self, first_subject, second_subject, expected):
        self.start_barrier = asyncio.Barrier(2)
        try:
            responses = await asyncio.gather(
                self._create("Teacher A", subject=first_subject),
                self._create("Teacher B", subject=second_subject),
            )
        finally:
            self.start_barrier = None
        self.assertEqual(sorted(response.status_code for response in responses), sorted(expected),
                         [response.text for response in responses])
        self.assertEqual(await self._count(), expected.count(201))


class TeacherAssignmentTests(AssignmentFixture, unittest.IsolatedAsyncioTestCase):
    async def test_another_teacher_cannot_take_an_occupied_section_and_subject(self):
        response = await self._create()
        self.assertEqual(response.status_code, 201, response.text)
        original_id = response.json()["assignment_id"]
        response = await self._create("Teacher B")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("Teacher A Example", response.json()["detail"])
        self.assertIn("Faith", response.json()["detail"])
        self.assertIn("English", response.json()["detail"])
        self.assertEqual(await self._count(), 1)
        async with self.sessions() as db:
            self.assertEqual((await db.get(TeacherAssignment, original_id)).teacher_id, self.people["Teacher A"].id)

    async def test_same_teacher_cannot_add_the_same_assignment_twice(self):
        self.assertEqual((await self._create()).status_code, 201)
        self.assertEqual((await self._create()).status_code, 409)
        self.assertEqual(await self._count(), 1)

    async def test_different_subjects_and_sections_can_have_different_teachers(self):
        for teacher, section, subject in (("Teacher A", 10, 100), ("Teacher B", 10, 101), ("Teacher B", 11, 100)):
            response = await self._create(teacher, section, subject)
            self.assertEqual(response.status_code, 201, response.text)
        response = await self.client.get("/api/teacher-assignments")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 3)
        self.assertTrue(all(row["teacher_name"] and row["section_name"] and row["subject_name"] for row in response.json()))

    async def test_legacy_all_subjects_blocks_specific_assignments_until_reviewed(self):
        async with self.sessions() as db:
            db.add(TeacherAssignment(school_id=1, teacher_id=self.people["Teacher A"].id,
                                     section_id=10, subject_id=None, is_adviser=False))
            await db.commit()
        for teacher, subject in (("Teacher A", 100), ("Teacher B", 101)):
            response = await self._create(teacher, subject=subject)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertIn("all subjects", response.json()["detail"])
        self.assertEqual(await self._count(), 1)

    async def test_new_assignments_require_specific_subject_and_separate_advice(self):
        for subject, adviser in ((None, False), (100, True), (None, True)):
            response = await self._create(subject=subject, adviser=adviser)
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(await self._count(), 0)

    async def test_remove_then_assign_replaces_teacher_explicitly(self):
        response = await self._create()
        identity = response.json()["assignment_id"]
        response = await self.client.delete(f"/api/teacher-assignments/{identity}")
        self.assertEqual(response.status_code, 204, response.text)
        response = await self._create("Teacher B")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["teacher_id"], str(self.people["Teacher B"].id))
        self.assertEqual(await self._count(), 1)

    async def test_legacy_adviser_only_row_does_not_occupy_subject(self):
        async with self.sessions() as db:
            db.add(TeacherAssignment(school_id=1, teacher_id=self.people["Teacher A"].id,
                                     section_id=10, subject_id=None, is_adviser=True))
            await db.commit()
        response = await self._create("Teacher B")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(await self._count(), 2)

    async def test_only_active_approved_teachers_are_assignable(self):
        for role, status, approved, active in (
            ("teacher", "pending", False, True), ("teacher", "rejected", False, True),
            ("teacher", "blocked", False, False), ("teacher", "deactivated", True, False),
            ("teacher", "approved", False, True), ("teacher", "approved", True, False),
            ("admin", "approved", True, True),
        ):
            async with self.sessions() as db:
                person = await db.get(Staff, self.people["Teacher B"].id)
                person.role, person.account_status, person.is_approved, person.is_active = role, status, approved, active
                await db.commit()
            response = await self._create("Teacher B")
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("active, approved teacher", response.json()["detail"])
        self.assertEqual(await self._count(), 0)

    async def test_other_school_teacher_section_subject_and_assignment_stay_private(self):
        async with self.sessions() as db:
            other = TeacherAssignment(school_id=2, teacher_id=self.people["Private Other Teacher"].id,
                                      section_id=20, subject_id=200)
            db.add(other)
            await db.commit()
            identity = other.assignment_id
        for payload in (self._payload("Private Other Teacher"), self._payload(section=20), self._payload(subject=200)):
            response = await self.client.post("/api/teacher-assignments", json=payload)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertNotIn("Private Other Teacher", response.text)
            self.assertNotIn("Other School", response.text)
        response = await self.client.delete(f"/api/teacher-assignments/{identity}")
        self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.get("/api/teacher-assignments")
        self.assertEqual(response.json(), [])
        self.assertEqual(await self._count(), 1)

    async def test_subject_must_belong_to_the_sections_grade(self):
        response = await self._create(subject=102)
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("grade level", response.json()["detail"])
        self.assertEqual(await self._count(), 0)

    async def test_legacy_duplicates_are_retained_and_new_conflicts_do_not_crash(self):
        async with self.sessions() as db:
            db.add_all([
                TeacherAssignment(school_id=1, teacher_id=self.people[teacher].id, section_id=10, subject_id=subject)
                for teacher, subject in (("Teacher A", 100), ("Teacher B", 100), ("Teacher A", None), ("Teacher A", None))
            ])
            await db.commit()
        for subject in (100, 101):
            response = await self._create("Teacher C", subject=subject)
            self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self._count(), 4)

    async def test_teachers_and_system_operators_cannot_manage_assignments(self):
        response = await self._create()
        identity = response.json()["assignment_id"]
        for name in ("Teacher A", "Operator"):
            self.actor = self.people[name].id
            self.assertEqual((await self._create("Teacher B", subject=101)).status_code, 403)
            self.assertEqual((await self.client.delete(f"/api/teacher-assignments/{identity}")).status_code, 403)
        self.assertEqual(await self._count(), 1)

    async def test_concurrent_teachers_cannot_claim_the_same_subject(self):
        await self._assert_race(100, 100, [201, 409])

    async def test_concurrent_wildcard_and_subject_cannot_overlap(self):
        await self._assert_race(None, 100, [400, 201])

    async def test_concurrent_nonoverlapping_subjects_both_save(self):
        await self._assert_race(100, 101, [201, 201])


@unittest.skipUnless(TEST_POSTGRES_URL, "Set TRACE_TEST_POSTGRES_URL for isolated local PostgreSQL assignment races.")
class TeacherAssignmentPostgresConcurrencyTests(AssignmentFixture, unittest.IsolatedAsyncioTestCase):
    postgres_url = TEST_POSTGRES_URL

    async def test_concurrent_teachers_cannot_claim_the_same_subject(self):
        await self._assert_race(100, 100, [201, 409])

    async def test_concurrent_wildcard_and_subject_cannot_overlap(self):
        await self._assert_race(None, 100, [400, 201])

    async def test_concurrent_nonoverlapping_subjects_both_save(self):
        await self._assert_race(100, 101, [201, 201])


if __name__ == "__main__":
    unittest.main()
