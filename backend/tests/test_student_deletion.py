"""Regression tests for deleting learners with dependent records.

Run from backend with: python -m unittest discover -s tests -p test_student_deletion.py
The real route and models run against a separate, in-memory SQLite database.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from datetime import date
from uuid import uuid4

# Importing the app constructs its configured engine. Never use a local .env
# or a deployed DATABASE_URL when importing these tests.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import HTTPException, Request
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routers.students import delete_enrollment, delete_student
from app.db.session import Base
from app.models.academic import GradeLevel, SchoolYear, Section
from app.models.schools import School
from app.models.staff import AuditLog, ROLE_ADMIN, Staff
from app.models.students import (
    AIRiskPrediction,
    DataQualityLog,
    EarlyWarning,
    Student,
    StudentEnrollment,
    StudentPace,
)
from app.services.audit import Action


STUDENT_MODELS = (
    Student, StudentEnrollment, StudentPace, EarlyWarning,
    AIRiskPrediction, DataQualityLog,
)


class StudentDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(self.engine.sync_engine, "connect")
        def enable_foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.sessions = async_sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False,
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.admin_id = uuid4()
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"School {i}") for i in (1, 2)])
            await db.flush()
            db.add(Staff(
                id=self.admin_id, school_id=1, role=ROLE_ADMIN,
                email="admin@example.com", password_hash="unused",
                first_name="School", last_name="Admin", contact_number="",
            ))
            for school_id in (1, 2):
                db.add_all([
                    GradeLevel(
                        grade_level_id=school_id, school_id=school_id,
                        level="7", name="Grade 7",
                    ),
                    SchoolYear(
                        school_year_id=school_id, school_id=school_id,
                        year="2026-2027", start_date=date(2026, 6, 1),
                        end_date=date(2027, 3, 31), is_current=True,
                    ),
                ])
            await db.flush()
            db.add_all([
                Section(
                    section_id=i, school_id=i, grade_level_id=i,
                    section_code="A", name="Section A",
                ) for i in (1, 2)
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _add_student(self, db, school_id=1):
        student = Student(school_id=school_id, first_name="Test", last_name="Learner")
        db.add(student)
        await db.flush()
        return student

    async def _add_full_graph(self, db, school_id=1):
        student = await self._add_student(db, school_id)
        enrollment = StudentEnrollment(
            school_id=school_id, student_id=student.student_id,
            grade_level_id=school_id, section_id=school_id,
            school_year_id=school_id,
        )
        db.add(enrollment)
        await db.flush()
        pace = StudentPace(
            school_id=school_id, student_id=student.student_id,
            enrollment_id=enrollment.enrollment_id, subject="Mathematics",
        )
        db.add(pace)
        await db.flush()
        db.add_all([
            EarlyWarning(
                school_id=school_id, student_id=student.student_id,
                enrollment_id=enrollment.enrollment_id, subject="Mathematics",
                risk_level="high", status="At Risk",
            ),
            AIRiskPrediction(
                school_id=school_id, student_id=student.student_id,
                enrollment_id=enrollment.enrollment_id, pace_id=pace.pace_id,
            ),
            DataQualityLog(
                school_id=school_id, student_id=student.student_id,
                student_pace_id=pace.pace_id, issue_type="missing_score",
            ),
        ])
        return student, enrollment

    async def _delete_student(self, student_id, scope=1):
        async with self.sessions() as db:
            admin = await db.get(Staff, self.admin_id)
            result = await delete_student(
                student_id=student_id, request=Request({"type": "http", "headers": []}),
                current=admin, scope=scope, db=db,
            )
            self.assertIsNone(result)

    async def _counts(self, student_id):
        async with self.sessions() as db:
            return tuple([
                (await db.execute(
                    select(func.count()).select_from(model)
                    .where(model.student_id == student_id)
                )).scalar_one()
                for model in STUDENT_MODELS
            ])

    async def test_delete_student_without_related_rows(self):
        async with self.sessions() as db:
            student = await self._add_student(db)
            await db.commit()
        await self._delete_student(student.student_id)
        self.assertEqual(await self._counts(student.student_id), (0, 0, 0, 0, 0, 0))
        async with self.sessions() as db:
            audit = (await db.execute(select(AuditLog))).scalar_one()
            self.assertEqual(audit.action, Action.STUDENT_DELETED)
            self.assertEqual(audit.admin_id, self.admin_id)
            self.assertEqual(audit.detail, "Test Learner")

    async def test_delete_student_with_independent_prediction(self):
        async with self.sessions() as db:
            student = await self._add_student(db)
            db.add(AIRiskPrediction(school_id=1, student_id=student.student_id))
            await db.commit()
        await self._delete_student(student.student_id)
        self.assertEqual(await self._counts(student.student_id), (0, 0, 0, 0, 0, 0))

    async def test_delete_student_with_independent_quality_log(self):
        async with self.sessions() as db:
            student = await self._add_student(db)
            db.add(DataQualityLog(
                school_id=1, student_id=student.student_id, issue_type="missing_record",
            ))
            await db.commit()
        await self._delete_student(student.student_id)
        self.assertEqual(await self._counts(student.student_id), (0, 0, 0, 0, 0, 0))

    async def test_delete_full_graph_preserves_other_students_and_school_scope(self):
        async with self.sessions() as db:
            target, _ = await self._add_full_graph(db)
            same_school, _ = await self._add_full_graph(db)
            other_school, _ = await self._add_full_graph(db, school_id=2)
            await db.commit()
        expected = (1, 1, 1, 1, 1, 1)
        self.assertEqual(await self._counts(target.student_id), expected)
        with self.assertRaises(HTTPException) as raised:
            await self._delete_student(other_school.student_id)
        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(await self._counts(other_school.student_id), expected)

        await self._delete_student(target.student_id)

        self.assertEqual(await self._counts(target.student_id), (0, 0, 0, 0, 0, 0))
        self.assertEqual(await self._counts(same_school.student_id), expected)
        self.assertEqual(await self._counts(other_school.student_id), expected)
        async with self.sessions() as db:
            self.assertEqual((await db.execute(
                select(func.count()).select_from(AuditLog)
            )).scalar_one(), 1)

    async def test_delete_enrollment_removes_predictions_and_preserves_student(self):
        async with self.sessions() as db:
            student, enrollment = await self._add_full_graph(db)
            await db.commit()
        async with self.sessions() as db:
            admin = await db.get(Staff, self.admin_id)
            await delete_enrollment(
                enrollment_id=enrollment.enrollment_id, _=admin, scope=1, db=db,
            )
        self.assertEqual(await self._counts(student.student_id), (1, 0, 0, 0, 0, 1))
        async with self.sessions() as db:
            log = (await db.execute(select(DataQualityLog))).scalar_one()
            self.assertIsNone(log.student_pace_id)


if __name__ == "__main__":
    unittest.main()
