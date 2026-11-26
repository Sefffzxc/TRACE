"""Checkpoint selection, append-only updates and tenant/access regressions.

Uses invented students, in-memory SQLite and locally evaluated fallback rules.
No external AI service or application database is used.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import ai, pace
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import AIRiskPrediction, ClassRecordSetup, Student, StudentEnrollment, StudentPace
from app.services import ml_client
from app.services.forecasting import forecast_records


class ForecastCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.teacher_id = uuid4()
        async with self.sessions() as db:
            for identity in (1, 2):
                db.add(School(school_id=identity, name=f"School {identity}", status="approved"))
            await db.flush()
            db.add(Staff(id=self.teacher_id, school_id=1, role="teacher", email="teacher@example.com",
                password_hash="unused", first_name="Test", last_name="Teacher", contact_number="",
                is_active=True, is_approved=True, account_status="approved"))
            for identity in (1, 2):
                db.add(GradeLevel(grade_level_id=identity, school_id=identity, level="8", name="Grade 8"))
                db.add(SchoolYear(school_year_id=identity, school_id=identity, year="2026-2027",
                    start_date=date(2026, 6, 1), end_date=date(2027, 3, 31), is_current=True))
                db.add(Student(student_id=identity, school_id=identity,
                    first_name="Invented", last_name=f"Learner {identity}"))
            await db.flush()
            for identity in (1, 2):
                db.add(Section(section_id=identity, school_id=identity,
                    grade_level_id=identity, name="Test"))
            db.add(Subject(subject_id=1, school_id=1, grade_level_id=1, subject_name="Mathematics"))
            await db.flush()
            for identity in (1, 2):
                db.add(StudentEnrollment(enrollment_id=identity, school_id=identity, student_id=identity,
                    school_year_id=identity, grade_level_id=identity, section_id=identity, is_active=True))
            db.add(TeacherAssignment(school_id=1, teacher_id=self.teacher_id,
                school_year_id=1, section_id=1, subject_id=1))
            for period in (1, 2, 3):
                db.add(ClassRecordSetup(school_id=1, school_year_id=1, section_id=1, subject_id=1,
                    grading_period=period, ww_highest="[10]", pt_highest="[10]", exam_highest="[10,10,10]"))
            await db.commit()

        async def isolated_db():
            async with self.sessions() as db:
                yield db

        async def actor(db=Depends(get_db)):
            return await db.get(Staff, self.teacher_id)

        self.app = FastAPI()
        self.app.include_router(ai.router)
        self.app.include_router(pace.router)
        self.app.dependency_overrides[get_db] = isolated_db
        self.app.dependency_overrides[deps.get_current_user] = actor
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        disabled = patch.object(ml_client.settings, "AI_ENABLED", False)
        disabled.start()
        self.addCleanup(disabled.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def add_record(self, period, mark=7, school_id=1, subject="Mathematics", complete=True):
        async with self.sessions() as db:
            row = StudentPace(school_id=school_id, student_id=school_id, enrollment_id=school_id,
                grading_period=period, subject=subject, ww_scores=json.dumps([mark]),
                pt_scores=json.dumps([mark]), exam_scores=json.dumps([mark] * 3))
            pace._recompute_grades(row, {**pace._default_setup(), "ww_highest": [10],
                "pt_highest": [10], "exam_highest": [10, 10, 10]})
            row.record_complete = complete
            db.add(row)
            await db.commit()
            return row

    async def rescore(self, **payload):
        response = await self.client.post("/api/ai/rescore", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def predictions(self):
        async with self.sessions() as db:
            return (await db.scalars(select(AIRiskPrediction).order_by(AIRiskPrediction.prediction_id))).all()

    async def test_manual_update_uses_both_periods_and_preserves_original(self):
        first = await self.add_record(1)
        self.assertEqual((await self.rescore())["scored"], 1)
        before = (await self.predictions())[0]
        second = await self.add_record(2, mark=9)
        self.assertEqual((await self.rescore())["scored"], 1)
        original, updated = await self.predictions()
        self.assertEqual(original.features, before.features)
        self.assertNotIn("q2_term_grade", json.loads(original.features))
        inputs = json.loads(updated.features)
        self.assertEqual(inputs["q1_term_grade"], first.transmuted_grade)
        self.assertEqual(inputs["q2_term_grade"], second.transmuted_grade)
        self.assertEqual(updated.pace_id, second.pace_id)
        self.assertIn("second-term", updated.model_version)
        self.assertEqual(updated.source, "fallback")
        latest = (await self.client.get("/api/ai/predictions")).json()
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["prediction_id"], updated.prediction_id)

    async def test_incomplete_second_period_keeps_first_checkpoint(self):
        await self.add_record(1)
        await self.add_record(2, mark=9, complete=False)
        await self.rescore()
        self.assertNotIn("q2_term_grade", json.loads((await self.predictions())[0].features))

    async def test_completed_second_without_completed_first_is_held_back(self):
        await self.add_record(1, complete=False)
        await self.add_record(2, mark=9)
        self.assertEqual((await self.rescore())["scored"], 0)
        self.assertEqual(await self.predictions(), [])

    async def test_subject_and_tenant_boundaries_apply_to_second_period(self):
        await self.add_record(1)
        await self.add_record(2, school_id=2, mark=10)
        await self.add_record(2, subject="English", mark=10)
        await self.rescore()
        rows = await self.predictions()
        self.assertEqual(len(rows), 1)
        self.assertNotIn("q2_term_grade", json.loads(rows[0].features))
        self.assertEqual((await self.rescore(student_id=2))["scored"], 0)
        self.assertEqual((await self.rescore(subject="English"))["scored"], 0)

    async def test_other_school_year_is_not_paired(self):
        await self.add_record(1)
        async with self.sessions() as db:
            db.add(SchoolYear(school_year_id=3, school_id=1, year="2025-2026",
                start_date=date(2025, 6, 1), end_date=date(2026, 3, 31), is_current=False))
            await db.flush()
            db.add(StudentEnrollment(enrollment_id=3, school_id=1, student_id=1,
                school_year_id=3, grade_level_id=1, section_id=1, is_active=False))
            await db.flush()
            old = StudentPace(school_id=1, student_id=1, enrollment_id=3, grading_period=2,
                subject="Mathematics", record_complete=True, ww_percent=100, pt_percent=100,
                exam_percent=100, initial_grade=100, transmuted_grade=100)
            db.add(old)
            await db.commit()
        await self.rescore()
        self.assertNotIn("q2_term_grade", json.loads((await self.predictions())[0].features))

    async def test_single_save_uses_second_period_and_latest_first_period_edit(self):
        first = await self.add_record(1)
        second = await self.add_record(2, mark=9)
        response = await self.client.patch(f"/api/paces/{first.pace_id}", json={"ww_scores": [8]})
        self.assertEqual(response.status_code, 200, response.text)
        prediction = (await self.predictions())[0]
        features = json.loads(prediction.features)
        self.assertEqual(features["q1_ww_pct"], 80)
        self.assertEqual(features["q2_term_grade"], second.transmuted_grade)

    async def test_bulk_save_includes_just_completed_second_period(self):
        await self.add_record(1)
        response = await self.client.post("/api/paces/bulk", json={"section_id": 1, "subject_id": 1,
            "grading_period": 2, "entries": [{"student_id": 1, "enrollment_id": 1,
                "subject": "Mathematics", "current_pace": 0, "expected_pace": 0, "total_paces": 0,
                "ww_scores": [9], "pt_scores": [9], "exam_scores": [9, 9, 9]}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ai"]["scored"], 1)
        inputs = json.loads((await self.predictions())[0].features)
        self.assertEqual(inputs["q2_ww_pct"], 90)

    async def test_in_memory_cleared_second_period_overrides_stored_checkpoint(self):
        first = await self.add_record(1)
        second = await self.add_record(2)
        second.record_complete = False
        async with self.sessions() as db:
            pairs = await forecast_records(db, [second], 1)
        self.assertEqual(pairs[0][0].pace_id, first.pace_id)
        self.assertIsNone(pairs[0][1])

    async def test_third_period_never_leaks_into_forecast_inputs(self):
        await self.add_record(1)
        second = await self.add_record(2)
        third = await self.add_record(3, mark=10)
        async with self.sessions() as db:
            result = await pace._record_prediction(db, third, "Grade 8", 1)
            self.assertEqual(result.pace_id, second.pace_id)
            self.assertEqual(set(json.loads(result.features)), set(ml_client.MODEL_FIELDS + ml_client.SECOND_TERM_FIELDS))

    async def test_model_request_includes_second_period_and_failure_keeps_its_fallback(self):
        first = await self.add_record(1)
        second = await self.add_record(2, mark=9)
        sent = []
        def unavailable(request):
            sent.append(json.loads(request.content))
            return httpx.Response(503, request=request)
        client_class = httpx.AsyncClient
        def fake_client(**kwargs):
            return client_class(transport=httpx.MockTransport(unavailable), **kwargs)
        with patch.object(ml_client.settings, "AI_ENABLED", True), patch.object(
            ml_client.httpx, "AsyncClient", side_effect=fake_client
        ):
            result = await ml_client.predict(first, "Grade 8", second_term=second)
        self.assertEqual(sent[0]["q2_ww_pct"], 90)
        self.assertEqual(result.source, "fallback")
        self.assertIn("second-term", result.model_version)
        expected = ml_client.rule_projection(first.transmuted_grade, 8, second.transmuted_grade)
        self.assertEqual(result.predicted_final, expected["predicted_final"])

    async def test_mixed_checkpoint_batch_sends_distinct_inputs_and_maps_results(self):
        first = await self.add_record(1)
        second = await self.add_record(2, mark=9)
        response = httpx.Response(200, json={"predictions": [
            {"predicted_final": 88, "model_version": "test-second-term"},
            {"predicted_final": 80, "model_version": "test-first-term"}]})
        send = AsyncMock(return_value=response)
        with patch.object(ml_client.settings, "AI_ENABLED", True), patch.object(
            ml_client, "_send_with_wake", send
        ):
            results = await ml_client.predict_many([(first, "Grade 8", second), (first, "Grade 8")])
        records = send.call_args.kwargs["json_body"]["records"]
        self.assertIn("q2_term_grade", records[0])
        self.assertNotIn("q2_term_grade", records[1])
        self.assertEqual([r.predicted_final for r in results], [88, 80])
        self.assertTrue(all(r.source == "model" for r in results))


class ForecastFeatureTests(unittest.TestCase):
    def test_completed_flag_and_finite_components_are_required(self):
        record = SimpleNamespace(grading_period=2, record_complete=True, ww_percent=0,
            pt_percent=75, exam_percent=80, initial_grade=70, transmuted_grade=75)
        self.assertTrue(ml_client.term_ready(record, 2))
        record.exam_percent = float("nan")
        self.assertFalse(ml_client.term_ready(record, 2))
        record.exam_percent = 0
        record.record_complete = False
        self.assertFalse(ml_client.term_ready(record, 2))


if __name__ == "__main__":
    unittest.main()
