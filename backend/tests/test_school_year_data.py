"""Switching a school's year isolates academic data without erasing its archive.

Only invented rows, in-memory SQLite and mocked external inference are used.
"""
from __future__ import annotations

from datetime import date
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import ai, class_import, dashboard, pace, staff, students
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, SectionAdviser, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import AIRiskPrediction, ClassRecordSetup, DataQualityLog, EarlyWarning, Student, StudentEnrollment, StudentPace
from test_eclass_import import make_record, workbook_bytes


class SchoolYearDataTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        self.teacher, self.principal = uuid4(), uuid4()
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"School {i}", status="approved") for i in (1, 2)])
            await db.flush()
            db.add_all([Staff(id=identity, school_id=1, role=role,
                email=f"{role}@example.com", first_name="Test", last_name=role,
                password_hash="unused", contact_number="", is_active=True,
                is_approved=True, account_status="approved")
                for identity, role in ((self.teacher, "teacher"), (self.principal, "admin"))])
            for year_id, school_id, label, active in (
                (1, 1, "2026-2027", True), (2, 1, "2027-2028", False),
                (3, 2, "2027-2028", True),
            ):
                db.add(SchoolYear(school_year_id=year_id, school_id=school_id,
                    year=label, start_date=date(int(label[:4]), 6, 1),
                    end_date=date(int(label[-4:]), 3, 31), is_current=active))
            db.add_all([GradeLevel(grade_level_id=i, school_id=i, level="6", name="Grade 6") for i in (1, 2)])
            await db.flush()
            db.add_all([Section(section_id=i, school_id=i, grade_level_id=i, name="TEST") for i in (1, 2)])
            db.add(Subject(subject_id=1, school_id=1, grade_level_id=1, subject_name="English"))
            db.add_all([Student(student_id=1, school_id=1, login_id="S0001", first_name="Test", last_name="Santos", middle_name="A."),
                Student(student_id=2, school_id=2, login_id="S0002", first_name="Test", last_name="Foreign")])
            await db.flush()
            db.add_all([StudentEnrollment(enrollment_id=1, school_id=1, student_id=1,
                grade_level_id=1, section_id=1, school_year_id=1),
                TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.teacher, section_id=1, subject_id=1),
                SectionAdviser(school_id=1, school_year_id=1, teacher_id=self.teacher, section_id=1),
                ClassRecordSetup(school_id=1, school_year_id=1, section_id=1, subject_id=1,
                    grading_period=1, ww_highest="[10]", pt_highest="[10]", exam_highest="[10,10,10]")])
            await db.flush()
            db.add(StudentPace(pace_id=1, school_id=1, student_id=1, enrollment_id=1,
                subject="English", grading_period=1, ww_scores="[5]", pt_scores="[5]",
                exam_scores="[5,5,5]", transmuted_grade=60, current_pace=1,
                expected_pace=10, total_paces=12))
            db.add(EarlyWarning(warning_id=1, school_id=1, student_id=1,
                enrollment_id=1, subject="English", risk_level="high", risk_score=80,
                status="At Risk", is_resolved=False))
            await db.flush()
            db.add(AIRiskPrediction(prediction_id=1, school_id=1, student_id=1,
                enrollment_id=1, pace_id=1, subject="English", risk_level="HIGH",
                risk_score=.8, source="model", model_version="synthetic",
                features=json.dumps({"q1_term_grade": 60})))
            db.add_all([
                DataQualityLog(log_id=1, school_id=1, student_id=1, student_pace_id=1, issue_type="missing_score"),
                DataQualityLog(log_id=2, school_id=1, student_id=1, issue_type="legacy_unlinked"),
            ])
            await db.commit()
        self.actor = self.principal

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
        for router in (students.router, pace.router, ai.router, dashboard.router, class_import.router, staff.router):
            self.app.include_router(router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[deps.get_current_user] = current_actor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        self.mock_score = patch.object(pace, "_score_batch", AsyncMock(return_value=None))
        self.mock_score.start()
        self.addCleanup(self.mock_score.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def switch(self, year_id):
        async with self.sessions() as db:
            for year in (await db.scalars(select(SchoolYear).where(SchoolYear.school_id == 1))).all():
                year.is_current = year.school_year_id == year_id
            await db.commit()

    async def assign_new_year(self):
        async with self.sessions() as db:
            db.add(TeacherAssignment(school_id=1, school_year_id=2, teacher_id=self.teacher, section_id=1, subject_id=1))
            await db.commit()

    async def enroll_returning(self):
        self.actor = self.teacher
        response = await self.client.post("/api/enrollments", json={
            "student_id": 1, "school_year_id": 2, "grade_level_id": 1, "section_id": 1,
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["enrollment_id"]

    async def test_new_year_is_empty_for_both_roles_and_switch_back_restores(self):
        await self.switch(2)
        for actor in (self.principal, self.teacher):
            self.actor = actor
            for route in ("students", "enrollments", "paces", "early-warnings", "ai/predictions"):
                response = await self.client.get(f"/api/{route}")
                self.assertEqual(response.status_code, 200, (route, response.text))
                self.assertEqual(response.json(), [], route)
            response = await self.client.get("/api/dashboard")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total_students"], 0)
            self.assertEqual(response.json()["critical"], 0)
            self.assertEqual(response.json()["top_concerns"], [])
            self.assertEqual((await self.client.get("/api/ai/summary")).json()["total_scored"], 0)
            self.assertEqual((await self.client.get("/api/early-warnings/summary")).json()["total_flagged"], 0)
            self.assertEqual((await self.client.get("/api/students/1/profile")).status_code, 404)
            self.assertEqual((await self.client.get("/api/ai/predictions/1")).status_code, 404)
        self.actor = self.principal
        response = await self.client.get("/api/dashboard/admin")
        self.assertEqual(response.json()["counts"]["teachers"], 1)
        self.assertEqual(response.json()["counts"]["learners"], 0)
        self.assertEqual(response.json()["teachers"][0]["classes"], 0)
        await self.switch(1)
        self.assertEqual(len((await self.client.get("/api/students")).json()), 1)
        self.assertEqual((await self.client.get("/api/paces")).json()[0]["transmuted_grade"], 60)
        self.assertEqual(len((await self.client.get("/api/early-warnings")).json()), 1)
        self.assertEqual(len((await self.client.get("/api/ai/predictions")).json()), 1)

    async def test_stale_id_writes_rejected_and_archive_not_modified(self):
        await self.switch(2)
        for method, route, payload in (
            ("patch", "/api/paces/1", {"current_pace": 9}),
            ("patch", "/api/early-warnings/1", {"is_resolved": True}),
            ("patch", "/api/enrollments/1", {"is_active": False}),
            ("post", "/api/paces", {"student_id": 1, "enrollment_id": 1, "subject": "Other"}),
            ("post", "/api/paces/bulk", {"entries": [{"student_id": 1, "enrollment_id": 1,
                "subject": "English", "current_pace": 1, "expected_pace": 2}]}),
        ):
            response = await getattr(self.client, method)(route, json=payload)
            self.assertEqual(response.status_code, 409, (route, response.text))
        for route in ("/api/paces/1", "/api/enrollments/1"):
            response = await self.client.delete(route)
            self.assertEqual(response.status_code, 409, (route, response.text))
        async with self.sessions() as db:
            self.assertEqual((await db.get(StudentPace, 1)).transmuted_grade, 60)
            self.assertEqual((await db.get(StudentPace, 1)).current_pace, 1)
            self.assertTrue((await db.get(StudentEnrollment, 1)).is_active)
            self.assertFalse((await db.get(EarlyWarning, 1)).is_resolved)

    async def test_setup_save_starts_fresh_and_does_not_regrade_previous_year(self):
        await self.switch(2)
        route = "/api/class-record/setup?section_id=1&subject_id=1&grading_period=1"
        response = await self.client.get(route)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ww_highest"], [])
        response = await self.client.put(route, json={"ww_highest": [50], "pt_highest": [50]})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(StudentPace, 1)).transmuted_grade, 60)
            rows = (await db.scalars(select(ClassRecordSetup).order_by(ClassRecordSetup.school_year_id))).all()
            self.assertEqual([(r.school_year_id, r.ww_highest) for r in rows], [(1, "[10]"), (2, "[50.0]")])
        await self.switch(1)
        self.assertEqual((await self.client.get(route)).json()["ww_highest"], [10])

    async def test_returning_profile_is_reused_without_old_grades(self):
        await self.switch(2)
        self.actor = self.teacher
        response = await self.client.get("/api/students/returning?section_id=1&search=Test")
        self.assertEqual(response.status_code, 403)
        await self.assign_new_year()
        response = await self.client.get("/api/students/returning?section_id=1&search=Test")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r["student_id"] for r in response.json()], [1])
        self.assertNotIn("subjects", response.json()[0])
        self.assertNotIn("guardian_email", response.json()[0])
        enrollment_id = await self.enroll_returning()
        self.assertNotEqual(enrollment_id, 1)
        response = await self.client.get("/api/students")
        self.assertEqual([r["student_id"] for r in response.json()], [1])
        self.assertEqual(response.json()[0]["subjects_tracked"], 0)
        self.assertIsNone(response.json()[0]["ai_risk_level"])
        self.assertEqual((await self.client.get("/api/paces")).json(), [])
        self.assertEqual((await self.client.get("/api/students/returning?section_id=1&search=Test")).json(), [])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Student).where(Student.school_id == 1)), 1)
            self.assertTrue((await db.get(StudentEnrollment, 1)).is_active)

    async def test_principal_cannot_enroll_returning_students(self):
        await self.switch(2)
        self.assertEqual((await self.client.get("/api/students/returning?section_id=1&search=Test")).status_code, 403)
        response = await self.client.post("/api/enrollments", json={
            "student_id": 1, "school_year_id": 2, "grade_level_id": 1, "section_id": 1})
        self.assertEqual(response.status_code, 403)

    async def test_new_year_warning_does_not_overwrite_or_resolve_archive(self):
        await self.switch(2)
        await self.assign_new_year()
        enrollment_id = await self.enroll_returning()
        response = await self.client.post("/api/paces", json={"student_id": 1,
            "enrollment_id": enrollment_id, "subject": "English", "current_pace": 1,
            "expected_pace": 10, "total_paces": 12})
        self.assertEqual(response.status_code, 201, response.text)
        async with self.sessions() as db:
            old = await db.get(EarlyWarning, 1)
            self.assertEqual(old.enrollment_id, 1)
            self.assertFalse(old.is_resolved)
            self.assertEqual(old.risk_score, 80)
            new = (await db.scalars(select(EarlyWarning).where(EarlyWarning.enrollment_id == enrollment_id))).one()
            self.assertNotEqual(new.warning_id, old.warning_id)
        response = await self.client.patch(f"/api/paces/{response.json()['pace_id']}", json={
            "current_pace": 12, "expected_pace": 12})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            self.assertFalse((await db.get(EarlyWarning, 1)).is_resolved)

    async def test_missing_current_year_never_falls_back_to_old_data(self):
        await self.switch(None)
        for route in ("students", "enrollments", "paces", "early-warnings", "ai/predictions"):
            self.assertEqual((await self.client.get(f"/api/{route}")).json(), [], route)
        response = await self.client.put("/api/class-record/setup?section_id=1&subject_id=1", json={})
        self.assertEqual(response.status_code, 400, response.text)

    async def test_foreign_school_year_and_enrollment_are_not_available(self):
        response = await self.client.get("/api/enrollments?school_year_id=3")
        self.assertEqual(response.status_code, 400)
        self.actor = self.teacher
        response = await self.client.get("/api/students/returning?section_id=2&search=Test")
        self.assertEqual(response.status_code, 400)

    async def test_removal_only_leaves_current_roster_and_preserves_archive(self):
        await self.switch(2)
        await self.assign_new_year()
        new_enrollment = await self.enroll_returning()
        response = await self.client.delete("/api/students/1")
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual((await self.client.get("/api/students")).json(), [])
        async with self.sessions() as db:
            self.assertIsNotNone(await db.get(Student, 1))
            self.assertFalse((await db.get(StudentEnrollment, new_enrollment)).is_active)
            self.assertTrue((await db.get(StudentEnrollment, 1)).is_active)
            self.assertEqual((await db.get(StudentPace, 1)).transmuted_grade, 60)
        self.assertEqual(await self.enroll_returning(), new_enrollment)
        await self.switch(1)
        self.assertEqual(len((await self.client.get("/api/students")).json()), 1)

    async def test_old_year_workbook_is_refused_before_saving(self):
        await self.switch(2)
        await self.assign_new_year()
        self.actor = self.teacher
        book, _ = make_record(compact=True)
        response = await self.client.post("/api/class-record/import", data={
            "section_id": "1", "subject_id": "1", "save": "true"},
            files={"file": ("record.xlsx", workbook_bytes(book), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("2026-2027", response.json()["detail"])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(StudentPace)), 1)

    async def test_fresh_year_import_reuses_profiles_and_preserves_old_scores(self):
        await self.switch(2)
        await self.assign_new_year()
        self.actor = self.teacher
        book, _ = make_record(compact=True)
        book["INPUT"].cell(5, 25, "2027-2028")
        with patch.object(class_import, "_score_batch", AsyncMock(return_value=None)):
            response = await self.client.post("/api/class-record/import", data={
                "section_id": "1", "subject_id": "1", "save": "true"},
                files={"file": ("record.xlsx", workbook_bytes(book), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["saved"]["enrolled"], 1)
        self.assertEqual(response.json()["saved"]["added"], 2)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Student).where(Student.school_id == 1)), 3)
            self.assertEqual((await db.get(StudentPace, 1)).transmuted_grade, 60)
            self.assertEqual(json.loads((await db.get(ClassRecordSetup, 1)).ww_highest), [10])
            self.assertEqual((await db.get(EarlyWarning, 1)).enrollment_id, 1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(StudentEnrollment).where(
                StudentEnrollment.student_id == 1)), 2)
        await self.switch(1)
        self.assertEqual((await self.client.get("/api/paces")).json()[0]["transmuted_grade"], 60)

    async def test_teacher_accounts_remain_but_responsibilities_follow_current_year(self):
        before = (await self.client.get("/api/staff/teachers")).json()
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0]["sections"], ["TEST"])
        self.assertEqual(before[0]["advisory_section"], "TEST")
        await self.switch(2)
        after = (await self.client.get("/api/staff/teachers")).json()
        self.assertEqual([r["id"] for r in after], [str(self.teacher)])
        self.assertEqual(after[0]["sections"], [])
        self.assertIsNone(after[0]["advisory_section"])
        await self.switch(1)
        self.assertEqual((await self.client.get("/api/staff/teachers")).json()[0]["sections"], ["TEST"])

    async def test_mixed_year_bulk_request_rolls_back_current_year_partial_writes(self):
        await self.switch(2)
        await self.assign_new_year()
        new_enrollment = await self.enroll_returning()
        response = await self.client.post("/api/paces/bulk", json={"entries": [
            {"student_id": 1, "enrollment_id": new_enrollment, "subject": "English", "current_pace": 1, "expected_pace": 2},
            {"student_id": 1, "enrollment_id": 1, "subject": "English", "current_pace": 12, "expected_pace": 12},
        ]})
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(StudentPace)), 1)
            self.assertEqual((await db.get(StudentPace, 1)).current_pace, 1)

    async def test_quality_reports_follow_linked_current_record_and_preserve_unlinked_history(self):
        response = await self.client.get("/api/data-quality-logs")
        self.assertEqual([r["log_id"] for r in response.json()], [1])
        await self.switch(2)
        self.assertEqual((await self.client.get("/api/data-quality-logs")).json(), [])
        response = await self.client.post("/api/data-quality-logs", json={
            "student_id": 1, "student_pace_id": 1, "issue_type": "missing_score"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self.client.patch("/api/data-quality-logs/1")).status_code, 409)
        response = await self.client.post("/api/data-quality-logs", json={
            "student_id": 1, "issue_type": "unlinked"})
        self.assertEqual(response.status_code, 400, response.text)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(DataQualityLog)), 2)
            self.assertFalse((await db.get(DataQualityLog, 1)).resolved)
        await self.switch(1)
        self.assertEqual(len((await self.client.get("/api/data-quality-logs")).json()), 1)

    async def test_import_refuses_ambiguous_profiles_instead_of_guessing(self):
        await self.switch(2)
        await self.assign_new_year()
        self.actor = self.teacher
        async with self.sessions() as db:
            db.add(Student(school_id=1, first_name="Test", last_name="Santos", middle_name="B."))
            await db.commit()
        book, _ = make_record(compact=True)
        book["INPUT"].cell(5, 25, "2027-2028")
        response = await self.client.post("/api/class-record/import", data={
            "section_id": "1", "subject_id": "1", "save": "true"},
            files={"file": ("record.xlsx", workbook_bytes(book), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("More than one student profile", response.json()["detail"])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(StudentEnrollment)), 1)
            self.assertEqual((await db.get(StudentPace, 1)).transmuted_grade, 60)


if __name__ == "__main__":
    unittest.main()
