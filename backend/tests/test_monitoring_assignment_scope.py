"""Monitoring follows teacher assignments even when section names repeat.

Run from backend: python -m unittest discover -s tests -p test_monitoring_assignment_scope.py
Only isolated SQLite databases and synthetic learner/model rows are used.
"""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import academic, ai, dashboard, pace, staff
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, SectionAdviser, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import AIRiskPrediction, EarlyWarning, Student, StudentEnrollment
from app.services import email, ml_client
from app.services.enrichment import Lookups, sections_for_teacher


class MonitoringAssignmentScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        self.people = {}
        self.predictions = {}
        self.warnings = {}
        async with self.sessions() as db:
            db.add_all([School(school_id=value, name=f"School {value}", status="approved") for value in (1, 2)])
            await db.flush()
            for name, role, school_id in (
                ("Teacher", "teacher", 1), ("Adviser", "teacher", 1),
                ("AdviserOnly", "teacher", 1), ("FormerAdviser", "teacher", 1),
                ("Unassigned", "teacher", 1), ("Wildcard", "teacher", 1),
                ("Admin", "admin", 1), ("Foreign", "teacher", 2),
                ("Operator", "super_admin", None),
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
            ])
            for grade, school_id, base in ((1, 1, 100), (2, 1, 110), (3, 2, 200)):
                db.add_all([
                    Subject(subject_id=base, school_id=school_id, grade_level_id=grade, subject_name="Mathematics"),
                    Subject(subject_id=base + 1, school_id=school_id, grade_level_id=grade, subject_name="English"),
                ])
            await db.flush()
            # Each real class has a single owner. Advisory visibility extends
            # beyond the adviser’s own English grading assignment.
            db.add_all([
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Teacher"], section_id=10, subject_id=100),
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Teacher"], section_id=11, subject_id=111),
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Adviser"], section_id=10, subject_id=101),
                SectionAdviser(school_id=1, school_year_id=1, teacher_id=self.people["Adviser"], section_id=10),
                SectionAdviser(school_id=1, school_year_id=1, teacher_id=self.people["AdviserOnly"], section_id=11),
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["FormerAdviser"], section_id=12, subject_id=None, is_adviser=True),
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Wildcard"], section_id=12, subject_id=None),
                TeacherAssignment(school_id=2, school_year_id=2, teacher_id=self.people["Foreign"], section_id=20, subject_id=None),
            ])
            for student_id, enrollment_id, school_id, grade, section, year in (
                (101, 1001, 1, 1, 10, 1), (102, 1002, 1, 2, 11, 1),
                (103, 1003, 1, 1, 12, 1), (201, 2001, 2, 3, 20, 2),
            ):
                db.add(Student(student_id=student_id, school_id=school_id,
                    first_name="Learner", last_name=str(student_id)))
                await db.flush()
                db.add(StudentEnrollment(enrollment_id=enrollment_id, school_id=school_id,
                    student_id=student_id, grade_level_id=grade, section_id=section,
                    school_year_id=year, is_active=True))
                await db.flush()
                for subject in ("Mathematics", "English"):
                    prediction = AIRiskPrediction(school_id=school_id, student_id=student_id,
                        enrollment_id=enrollment_id, subject=subject,
                        risk_level="HIGH" if subject == "Mathematics" else "LOW",
                        risk_score=.8 if subject == "Mathematics" else .2,
                        source="model", predicted_final=70 if subject == "Mathematics" else 85,
                        model_version="synthetic-year-model", features=json.dumps({"q1_term_grade": 72}))
                    warning = EarlyWarning(school_id=school_id, student_id=student_id,
                        enrollment_id=enrollment_id, subject=subject,
                        risk_level="high" if subject == "Mathematics" else "moderate",
                        risk_score=80 if subject == "Mathematics" else 40,
                        status="At Risk", is_resolved=False)
                    db.add_all([prediction, warning])
                    await db.flush()
                    self.predictions[(section, subject)] = prediction.prediction_id
                    self.warnings[(section, subject)] = warning.warning_id
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
        for router in (academic.router, ai.router, dashboard.router, pace.router, staff.router):
            self.app.include_router(router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[deps.get_current_user] = current_actor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        self.external_calls = []
        for module, function in ((ml_client, "predict"), (ml_client, "predict_many"),
            (ml_client, "service_status"), (ml_client, "model_card"), (email, "_send_email")):
            mock = AsyncMock(side_effect=AssertionError("Monitoring reads must not contact external services"))
            replacement = patch.object(module, function, mock)
            replacement.start()
            self.addCleanup(replacement.stop)
            self.external_calls.append(mock)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        for mock in self.external_calls:
            mock.assert_not_awaited()

    async def _get(self, path, **params):
        response = await self.client.get(path, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def _monitoring_pairs(self, path, **params):
        rows = await self._get(path, **params)
        return {(row["section_id"], row["subject"]) for row in rows}

    async def test_my_classes_reports_grading_and_advisory_separately(self):
        classes = await self._get("/api/my-classes")
        self.assertEqual(classes, {"school_year_id": 1, "unrestricted": False,
            "grade": {"10": ["Mathematics"], "11": ["English"]}, "advisory": []})
        self.actor = self.people["Adviser"]
        classes = await self._get("/api/my-classes")
        self.assertEqual(classes["grade"], {"10": ["English"]})
        self.assertEqual(classes["advisory"], [10])

    async def test_subject_teacher_reads_only_assigned_section_and_subject(self):
        expected = {(10, "Mathematics"), (11, "English")}
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            for mine in (False, True):
                with self.subTest(path=path, mine=mine):
                    self.assertEqual(await self._monitoring_pairs(path, mine=mine), expected)
        stats = await self._get("/api/ai/summary")
        self.assertEqual((stats["total_scored"], stats["high"], stats["low"]), (2, 1, 1))
        warnings = await self._get("/api/early-warnings/summary", mine=True)
        self.assertEqual(warnings["counts"], {"critical": 0, "high": 1, "moderate": 1, "low": 0})
        self.assertEqual(warnings["students_flagged"], 2)

    async def test_adviser_sees_all_subjects_only_in_own_advisory_section(self):
        self.actor = self.people["Adviser"]
        expected = {(10, "Mathematics"), (10, "English")}
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            self.assertEqual(await self._monitoring_pairs(path, mine=True), expected)
        stats = await self._get("/api/early-warnings/summary", mine=True)
        self.assertEqual(stats["total_flagged"], 2)
        self.assertEqual(stats["students_flagged"], 1)
        self.assertEqual((await self._get("/api/ai/summary"))["total_scored"], 2)

    async def test_adviser_without_teaching_assignments_sees_both_monitoring_pages(self):
        self.actor = self.people["AdviserOnly"]
        self.assertEqual(await self._get("/api/my-classes"), {
            "school_year_id": 1, "unrestricted": False, "grade": {}, "advisory": [11],
        })
        async with self.sessions() as db:
            self.assertEqual(await sections_for_teacher(db, self.actor, school_id=1), [11])
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            for mine in (False, True):
                self.assertEqual(await self._monitoring_pairs(path, mine=mine),
                    {(11, "Mathematics"), (11, "English")})
        summary = await self._get("/api/dashboard")
        self.assertEqual(summary["total_students"], 1)

    async def test_old_adviser_flag_alone_does_not_restore_read_or_write_access(self):
        self.actor = self.people["FormerAdviser"]
        self.assertEqual(await self._get("/api/my-classes"), {
            "school_year_id": 1, "unrestricted": False, "grade": {}, "advisory": [],
        })
        async with self.sessions() as db:
            self.assertEqual(await sections_for_teacher(db, self.actor, school_id=1), [])
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            self.assertEqual(await self._get(path, mine=True), [])
        self.assertEqual((await self._get("/api/dashboard"))["total_students"], 0)

    async def test_adviser_names_directory_and_principal_counts_use_independent_records(self):
        async with self.sessions() as db:
            lookups = await Lookups.load(db, school_id=1)
        self.assertEqual(lookups.advisers, {10: "Adviser Staff", 11: "AdviserOnly Staff"})
        self.actor = self.people["Admin"]
        directory = {row["first_name"]: row for row in await self._get("/api/staff/teachers")}
        self.assertEqual(directory["AdviserOnly"]["sections"], ["Faith"])
        self.assertEqual(directory["AdviserOnly"]["advisory_section"], "Faith")
        self.assertEqual(directory["FormerAdviser"]["sections"], [])
        self.assertIsNone(directory["FormerAdviser"]["advisory_section"])
        overview = await self._get("/api/dashboard/admin")
        self.assertEqual(overview["attention"]["sections_without_adviser"], ["Hope"])
        adviser_row = next(row for row in overview["teachers"] if row["id"] == str(self.people["AdviserOnly"]))
        self.assertEqual(adviser_row["classes"], 0)

    async def test_removing_a_teaching_assignment_preserves_advisory_monitoring(self):
        self.actor = self.people["Admin"]
        assignments = await self._get("/api/teacher-assignments")
        own = next(row for row in assignments if row["teacher_id"] == str(self.people["Adviser"]))
        result = await self.client.delete(f'/api/teacher-assignments/{own["assignment_id"]}')
        self.assertEqual(result.status_code, 204, result.text)
        self.actor = self.people["Adviser"]
        self.assertEqual(await self._get("/api/my-classes"), {
            "school_year_id": 1, "unrestricted": False, "grade": {}, "advisory": [10],
        })
        self.assertEqual(await self._monitoring_pairs("/api/ai/predictions", mine=True),
            {(10, "Mathematics"), (10, "English")})

    async def test_wildcard_assignment_covers_its_own_section_only(self):
        self.actor = self.people["Wildcard"]
        classes = await self._get("/api/my-classes")
        self.assertEqual(classes["grade"], {"12": ["*"]})
        self.assertEqual(classes["advisory"], [])
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            self.assertEqual(await self._monitoring_pairs(path), {(12, "Mathematics"), (12, "English")})

    async def test_unassigned_teacher_receives_no_monitoring_rows_or_counts(self):
        self.actor = self.people["Unassigned"]
        self.assertEqual(await self._get("/api/my-classes"), {"school_year_id": 1, "unrestricted": False, "grade": {}, "advisory": []})
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            self.assertEqual(await self._get(path), [])
        self.assertEqual((await self._get("/api/ai/summary"))["total_scored"], 0)
        self.assertEqual((await self._get("/api/early-warnings/summary", mine=True))["total_flagged"], 0)

    async def test_admin_can_read_all_own_school_classes_but_no_foreign_data(self):
        self.actor = self.people["Admin"]
        self.assertTrue((await self._get("/api/my-classes"))["unrestricted"])
        expected = {(section, subject) for section in (10, 11, 12) for subject in ("Mathematics", "English")}
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            self.assertEqual(await self._monitoring_pairs(path), expected)
            self.assertEqual(await self._get(path, section_id=20), [])
        self.assertEqual((await self._get("/api/ai/summary"))["total_scored"], 6)
        self.assertEqual((await self._get("/api/early-warnings/summary"))["total_flagged"], 6)

    async def test_foreign_teacher_is_confined_to_their_school(self):
        self.actor = self.people["Foreign"]
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            self.assertEqual(await self._monitoring_pairs(path), {(20, "Mathematics"), (20, "English")})
            self.assertEqual(await self._get(path, section_id=10), [])
        self.assertEqual((await self._get("/api/ai/summary"))["total_scored"], 2)

    async def test_numeric_section_filter_disambiguates_repeated_names_across_grades(self):
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            for section, subject, grade in ((10, "Mathematics", "Grade 7"), (11, "English", "Grade 8")):
                with self.subTest(path=path, section=section):
                    rows = await self._get(path, section_id=section)
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["section_id"], section)
                    self.assertEqual(rows[0]["section_name"], "Faith")
                    self.assertEqual(rows[0]["grade_level_name"], grade)
                    self.assertEqual(rows[0]["subject"], subject)

    async def test_malicious_section_or_subject_filter_never_expands_teacher_access(self):
        for path in ("/api/ai/predictions", "/api/early-warnings"):
            for section in (12, 20, 99999):
                with self.subTest(path=path, section=section):
                    self.assertEqual(await self._get(path, section_id=section, mine=False), [])
        self.assertEqual(await self._get("/api/ai/predictions", section_id=10, subject="English"), [])
        for key in ((10, "English"), (12, "Mathematics"), (20, "Mathematics")):
            response = await self.client.get(f"/api/ai/predictions/{self.predictions[key]}")
            self.assertEqual(response.status_code, 404, response.text)

    async def test_warning_resolution_obeys_the_same_subject_and_tenant_boundary(self):
        for key in ((10, "English"), (12, "Mathematics"), (20, "Mathematics")):
            response = await self.client.patch(f"/api/early-warnings/{self.warnings[key]}", json={"is_resolved": True})
            self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.patch(f"/api/early-warnings/{self.warnings[(10, 'Mathematics')]}", json={"is_resolved": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["is_resolved"])
        self.assertEqual((await self._get("/api/early-warnings/summary", mine=True))["total_flagged"], 1)

    async def test_system_operator_cannot_read_student_monitoring(self):
        self.actor = self.people["Operator"]
        for path in ("/api/my-classes", "/api/ai/predictions", "/api/ai/summary", "/api/early-warnings", "/api/early-warnings/summary"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
