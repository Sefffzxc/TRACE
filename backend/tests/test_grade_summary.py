"""Recorded-grade summaries, tenant boundaries and teacher/adviser visibility.

Only synthetic records in in-memory SQLite are used. Reading this page must
never call inference or append predictions.
"""
from datetime import date
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import grade_summary
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, SectionAdviser, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import AIRiskPrediction, Student, StudentEnrollment, StudentPace
from app.services import ml_client, risk
from app.services.grade_summary import completed_grade, summarise_grades


class GradeSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            await connection.run_sync(Base.metadata.create_all)
        self.people = {name: uuid4() for name in ("Teacher", "Adviser", "Unassigned", "Admin", "Operator", "Foreign")}
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"School {i}", status="approved") for i in (1, 2)])
            await db.flush()
            for name, identity in self.people.items():
                db.add(Staff(id=identity, school_id=None if name == "Operator" else 2 if name == "Foreign" else 1,
                    role="super_admin" if name == "Operator" else "admin" if name == "Admin" else "teacher",
                    email=f"{name}@example.com", password_hash="unused", first_name=name, last_name="Staff",
                    contact_number="", is_active=True, is_approved=True, account_status="approved"))
            for level, school in ((1, 1), (2, 1), (3, 2)):
                db.add(GradeLevel(grade_level_id=level, school_id=school, level=str(7 + level), name=f"Grade {7 + level}"))
            for identity, school, current in ((1, 1, True), (2, 1, False), (3, 2, True)):
                db.add(SchoolYear(school_year_id=identity, school_id=school, year=f"Year {identity}",
                    start_date=date(2026, 6, 1), end_date=date(2027, 3, 31), is_current=current))
            for identity, school in ((1, 1), (2, 1), (3, 2)):
                db.add(Student(student_id=identity, school_id=school, first_name="Invented", last_name=f"Learner {identity}"))
            await db.flush()
            for identity, school, level in ((10, 1, 1), (11, 1, 2), (20, 2, 3)):
                db.add(Section(section_id=identity, school_id=school, grade_level_id=level, name=f"Section {identity}"))
            for identity, school, level, name in ((100, 1, 1, "Mathematics"), (101, 1, 1, "English"),
                    (110, 1, 2, "Mathematics"), (200, 2, 3, "Mathematics")):
                db.add(Subject(subject_id=identity, school_id=school, grade_level_id=level, subject_name=name))
            await db.flush()
            for identity, student, year, section, level, school in (
                (10, 1, 1, 10, 1, 1), (11, 2, 1, 10, 1, 1), (12, 1, 2, 10, 1, 1), (20, 3, 3, 20, 3, 2),
            ):
                db.add(StudentEnrollment(enrollment_id=identity, school_id=school, student_id=student,
                    school_year_id=year, section_id=section, grade_level_id=level, is_active=True))
            for year in (1, 2):
                db.add(TeacherAssignment(school_id=1, school_year_id=year,
                    teacher_id=self.people["Teacher"], section_id=10, subject_id=100))
            db.add(SectionAdviser(school_id=1, school_year_id=1, section_id=10, teacher_id=self.people["Adviser"]))
            await db.commit()
        self.actor = self.people["Teacher"]

        async def isolated_db():
            async with self.sessions() as db:
                yield db
        async def actor(db=Depends(get_db)):
            return await db.get(Staff, self.actor)
        self.app = FastAPI()
        self.app.include_router(grade_summary.router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[deps.get_current_user] = actor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.calls = []
        for function in ("predict", "predict_many", "service_status", "model_card"):
            mock = AsyncMock(side_effect=AssertionError("Grade-summary reads must not call AI"))
            replacement = patch.object(ml_client, function, mock)
            replacement.start()
            self.addCleanup(replacement.stop)
            self.calls.append(mock)

    async def asyncTearDown(self):
        for mock in self.calls:
            mock.assert_not_awaited()
        await self.client.aclose()
        await self.engine.dispose()

    async def record(self, period, grade, enrollment=10, complete=True, subject="Mathematics", coursework=None):
        school, student = (2, 3) if enrollment == 20 else (1, 2 if enrollment == 11 else 1)
        async with self.sessions() as db:
            row = StudentPace(school_id=school, student_id=student, enrollment_id=enrollment,
                subject=subject, grading_period=period, transmuted_grade=grade,
                record_complete=complete, pre_exam_weighted=coursework, pre_exam_max=70,
                ww_percent=80 if grade is not None else None)
            db.add(row)
            await db.commit()
            return row

    async def get_summary(self, status=200, **params):
        response = await self.client.get("/api/grade-summary", params={"section_id": 10, "subject_id": 100, **params})
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    async def test_empty_scores_still_include_roster_and_pending_grades(self):
        summary = await self.get_summary()
        self.assertEqual(len(summary["rows"]), 2)
        self.assertEqual(summary["period_count"], 3)
        for row in summary["rows"]:
            self.assertEqual(row["term_grades"], [None, None, None])
            self.assertIsNone(row["current_average"])
            self.assertIsNone(row["final_grade"])
            self.assertIsNone(row["risk_level"])
            self.assertEqual(row["result"], "Pending")

    async def test_average_uses_only_complete_periods_and_final_needs_all(self):
        await self.record(2, 84)
        await self.record(1, 80)
        await self.record(3, 60, complete=False)
        row = (await self.get_summary())["rows"][0]
        self.assertEqual(row["term_grades"], [80, 84, None])
        self.assertEqual(row["current_average"], 82)
        self.assertIsNone(row["final_grade"])
        async with self.sessions() as db:
            third = await db.scalar(select(StudentPace).where(StudentPace.grading_period == 3))
            third.record_complete = True
            third.transmuted_grade = 88
            await db.commit()
        row = (await self.get_summary())["rows"][0]
        self.assertEqual(row["final_grade"], 84)
        self.assertEqual(row["descriptor"], "Benchmarking")
        self.assertEqual(row["result"], "Passed")

    async def test_current_year_subject_and_foreign_school_records_stay_separate(self):
        await self.record(1, 80)
        await self.record(2, 99, enrollment=12)
        await self.record(3, 99, enrollment=20)
        await self.record(2, 99, subject="English")
        row = (await self.get_summary())["rows"][0]
        self.assertEqual(row["term_grades"], [80, None, None])
        old = await self.get_summary(school_year_id=2)
        self.assertEqual(len(old["rows"]), 1)
        self.assertEqual(old["rows"][0]["term_grades"], [None, 99, None])

    async def test_teacher_subject_limits_and_adviser_visibility(self):
        await self.get_summary(subject_id=101, status=403)
        await self.get_summary(section_id=11, subject_id=110, status=403)
        self.actor = self.people["Unassigned"]
        await self.get_summary(status=403)
        self.actor = self.people["Adviser"]
        summary = await self.get_summary(subject_id=101)
        self.assertEqual(len(summary["rows"]), 2)
        await self.get_summary(school_year_id=2, status=403)

    async def test_foreign_ids_are_not_found_and_mismatched_grade_level_rejected(self):
        await self.get_summary(section_id=20, status=404)
        await self.get_summary(subject_id=200, status=404)
        await self.get_summary(subject_id=110, status=400)
        await self.get_summary(school_year_id=3, status=400)

    async def test_administrator_operator_and_unit_school_are_denied(self):
        for name in ("Admin", "Operator"):
            self.actor = self.people[name]
            await self.get_summary(status=403)
        self.actor = self.people["Teacher"]
        async with self.sessions() as db:
            (await db.get(School, 1)).tracks_units = True
            await db.commit()
        await self.get_summary(status=400)

    async def test_configured_period_count_and_passing_mark_apply(self):
        async with self.sessions() as db:
            school = await db.get(School, 1)
            school.grading_periods = 4
            school.grading_period_label = "Quarter"
            school.passing_mark = 85
            await db.commit()
        for period in (1, 2, 3):
            await self.record(period, 84)
        self.assertIsNone((await self.get_summary())["rows"][0]["final_grade"])
        await self.record(4, 84)
        summary = await self.get_summary()
        self.assertEqual(summary["period_label"], "Quarter")
        self.assertEqual(summary["rows"][0]["final_grade"], 84)
        self.assertEqual(summary["rows"][0]["result"], "Failed")

    async def test_latest_logged_forecast_is_returned_without_inference_or_writes(self):
        await self.record(1, 80)
        async with self.sessions() as db:
            for enrollment, subject, inputs, grade, source in (
                (10, "Mathematics", {"q1_term_grade": 80}, 81, "model"),
                (10, "Mathematics", {"q1_term_grade": 80, "q2_term_grade": 84}, 83, "fallback"),
                (10, "English", {"q1_term_grade": 90}, 99, "model"),
                (12, "Mathematics", {"q1_term_grade": 90}, 99, "model"),
                (10, "Mathematics", {"ww_pct": 90}, 99, "model"),
            ):
                db.add(AIRiskPrediction(school_id=1, student_id=1, enrollment_id=enrollment, subject=subject,
                    features=json.dumps(inputs), predicted_final=grade, range_low=grade - 2, range_high=grade + 2,
                    source=source, model_version="test-checkpoint", risk_level="LOW", risk_score=.1))
                await db.flush()
            await db.commit()
        row = (await self.get_summary())["rows"][0]
        self.assertEqual(row["ai"]["predicted_final"], 83)
        self.assertEqual(row["ai"]["source"], "fallback")
        self.assertEqual(row["ai"]["features"]["q2_term_grade"], 84)
        self.assertIsNotNone(row["ai"]["created_at"])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(AIRiskPrediction)), 5)

    async def test_empty_roster_returns_metadata_and_resolved_risk_matches_shared_rule(self):
        async with self.sessions() as db:
            second = await db.get(StudentEnrollment, 11)
            second.is_active = False
            await db.commit()
        await self.record(1, 77)
        await self.record(2, 79)
        row = (await self.get_summary())["rows"][0]
        self.assertEqual(row["risk_level"], risk.evaluate_year(SimpleNamespace(), [77, 79], 80, 75)["risk_level"])
        async with self.sessions() as db:
            (await db.get(StudentEnrollment, 10)).is_active = False
            await db.commit()
        self.assertEqual((await self.get_summary())["rows"], [])


class GradeArithmeticTests(unittest.TestCase):
    def record(self, period, grade, complete=True, **kwargs):
        return SimpleNamespace(grading_period=period, transmuted_grade=grade, record_complete=complete, **kwargs)

    def test_invalid_values_and_blank_are_excluded_while_completed_zero_is_preserved(self):
        for value in (None, float("nan"), float("inf"), -1, 101, "bad"):
            self.assertIsNone(completed_grade(self.record(1, value)))
        self.assertIsNone(completed_grade(self.record(1, 0, complete=False)))
        self.assertEqual(completed_grade(self.record(1, 0)), 0)
        self.assertEqual(summarise_grades([self.record(1, 0)], 3, 75)["current_average"], 0)

    def test_later_incomplete_edit_overrides_old_complete_record_for_same_period(self):
        result = summarise_grades([self.record(1, 80), self.record(1, 70, complete=False)], 3, 75)
        self.assertIsNone(result["current_average"])
        self.assertEqual(result["completed_periods"], 0)

    def test_final_average_is_not_transmuted_again_and_outside_periods_do_not_count(self):
        result = summarise_grades([self.record(3, 88), self.record(1, 80), self.record(2, 84), self.record(4, 100)], 3, 75)
        self.assertEqual(result["term_grades"], [80, 84, 88])
        self.assertEqual(result["final_grade"], 84)

    def test_displayed_average_and_passing_status_use_the_same_rounding(self):
        result = summarise_grades([self.record(1, 74.996)], 1, 75)
        self.assertEqual(result["final_grade"], 75)
        self.assertEqual(result["result"], "Passed")

    def test_incomplete_term_risk_reads_coursework_and_never_its_provisional_grade(self):
        row = self.record(1, 60, complete=False, pre_exam_weighted=63, pre_exam_max=70)
        result = summarise_grades([row], 3, 75)
        self.assertIsNone(result["current_average"])
        self.assertEqual(result["risk_level"], "low")


if __name__ == "__main__":
    unittest.main()
