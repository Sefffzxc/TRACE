"""Grade/section saves and name-only academic records against an isolated DB."""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import academic
from app.db.session import Base, get_db
from app.models.academic import GradeLevel, SchoolYear, Section, SectionAdviser, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import Staff
from app.models.students import ClassRecordSetup, Student, StudentEnrollment


class AcademicStructureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            await conn.run_sync(Base.metadata.create_all)
        self.admin_id, self.teacher_id = uuid4(), uuid4()
        self.actor = self.admin_id
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"Test School {i}", status="approved") for i in (1, 2)])
            await db.flush()
            db.add_all([
                Staff(id=identity, school_id=1, email=f"{role}@example.com", password_hash="unused",
                      first_name="Test", last_name=role, contact_number="", role=role,
                      account_status="approved", is_approved=True, is_active=True)
                for identity, role in ((self.admin_id, "admin"), (self.teacher_id, "teacher"))
            ])
            db.add_all([
                GradeLevel(grade_level_id=1, school_id=1, level="7", name="Grade 7"),
                GradeLevel(grade_level_id=2, school_id=1, level="8", name="Grade 8"),
                GradeLevel(grade_level_id=3, school_id=2, level="7", name="Other Grade 7"),
            ])
            await db.flush()
            db.add_all([
                Section(section_id=10, school_id=1, grade_level_id=1, name="A", room="Room 1", capacity=40),
                Section(section_id=11, school_id=1, grade_level_id=1, name="B", capacity=40),
                Section(section_id=12, school_id=1, grade_level_id=2, name="C", capacity=40),
                Section(section_id=20, school_id=2, grade_level_id=3, name="A", capacity=40),
                Subject(subject_id=100, school_id=1, grade_level_id=1, subject_name="English", total_paces=0),
                Subject(subject_id=101, school_id=1, grade_level_id=2, subject_name="English", total_paces=0),
                Subject(subject_id=200, school_id=2, grade_level_id=3, subject_name="English", total_paces=0),
            ])
            await db.commit()

        async def database():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise

        async def user(db=Depends(get_db)):
            return await db.get(Staff, self.actor)

        self.app = FastAPI()
        self.app.include_router(academic.router)
        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[deps.get_current_user] = user
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def _sections(self, grade=1):
        response = await self.client.get("/api/sections", params={"grade_level_id": grade})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_create_grade_and_multiple_sections_without_codes(self):
        response = await self.client.post("/api/grade-levels/with-sections", json={
            "level": "9", "name": "Grade 9", "sections": [{"name": "Faith", "capacity": 35}, {"name": "Hope"}],
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["section_count"], 2)
        rows = await self._sections(response.json()["grade_level_id"])
        self.assertEqual({row["name"] for row in rows}, {"Faith", "Hope"})
        self.assertTrue(all("section_code" not in row for row in rows))

    async def test_grade_display_name_is_derived_for_create_and_update(self):
        response = await self.client.post("/api/grade-levels/with-sections", json={
            "level": " 2 ", "sections": [{"name": "Charity"}],
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["name"], "Grade 2")
        identity = response.json()["grade_level_id"]
        response = await self.client.patch(f"/api/grade-levels/{identity}", json={"level": "3", "name": "Custom"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Grade 3")
        response = await self.client.patch(f"/api/grade-levels/{identity}", json={"name": "Custom again"})
        self.assertEqual(response.json()["name"], "Grade 3")
        rows = await self._sections(identity)
        self.assertEqual(rows[0]["grade_level_name"], "Grade 3")
        async with self.sessions() as db:
            self.assertEqual((await db.get(GradeLevel, identity)).name, "Grade 3")

    async def test_legacy_grade_name_is_derived_in_grade_and_section_responses(self):
        async with self.sessions() as db:
            (await db.get(GradeLevel, 1)).name = "Custom legacy name"
            await db.commit()
        response = await self.client.get("/api/grade-levels")
        self.assertEqual(next(row["name"] for row in response.json() if row["grade_level_id"] == 1), "Grade 7")
        self.assertTrue(all(row["grade_level_name"] == "Grade 7" for row in await self._sections()))

    async def test_duplicate_section_names_roll_back_new_grade(self):
        response = await self.client.post("/api/grade-levels", json={
            "level": "9", "name": "Grade 9", "sections": [{"name": " Faith "}, {"name": "FAITH"}],
        })
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            self.assertIsNone((await db.execute(select(GradeLevel).where(GradeLevel.level == "9"))).scalar_one_or_none())

    async def test_edit_preserves_ids_and_unrelated_fields_while_adding_and_removing_sections(self):
        response = await self.client.patch("/api/grade-levels/1/with-sections", json={
            "name": "Year 7", "sections": [{"section_id": 10, "name": "Faith", "capacity": 35}, {"name": "Hope"}],
        })
        self.assertEqual(response.status_code, 200, response.text)
        rows = await self._sections()
        self.assertEqual({row["name"] for row in rows}, {"Faith", "Hope"})
        self.assertEqual(next(row for row in rows if row["name"] == "Faith")["section_id"], 10)
        async with self.sessions() as db:
            self.assertEqual((await db.get(Section, 10)).room, "Room 1")
            self.assertIsNone(await db.get(Section, 11))
            self.assertEqual((await db.get(Section, 20)).name, "A")
            self.assertEqual((await db.get(Subject, 100)).subject_name, "English")

    async def test_existing_sections_can_exchange_names_in_one_save(self):
        response = await self.client.patch("/api/grade-levels/1", json={"sections": [
            {"section_id": 10, "name": "B"}, {"section_id": 11, "name": "A"},
        ]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["section_id"]: row["name"] for row in await self._sections()}, {10: "B", 11: "A"})

    async def test_omitted_sections_preserve_rows_and_empty_list_removes_only_unused_rows(self):
        response = await self.client.patch("/api/grade-levels/1", json={"name": "Year 7"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(await self._sections()), 2)
        response = await self.client.patch("/api/grade-levels/1", json={"sections": []})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self._sections(), [])
        self.assertEqual(len(await self._sections(2)), 1)

    async def test_removing_assigned_section_rolls_back_the_entire_grade_edit(self):
        async with self.sessions() as db:
            db.add(TeacherAssignment(school_id=1, teacher_id=self.teacher_id, section_id=11, is_adviser=True))
            await db.commit()
        response = await self.client.patch("/api/grade-levels/1", json={
            "name": "Should not save", "sections": [{"section_id": 10, "name": "Changed"}],
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("teacher assignments", response.json()["detail"])
        async with self.sessions() as db:
            self.assertEqual((await db.get(GradeLevel, 1)).name, "Grade 7")
            self.assertEqual((await db.get(Section, 10)).name, "A")
        response = await self.client.delete("/api/sections/11")
        self.assertEqual(response.status_code, 409, response.text)

    async def test_adviser_without_teaching_rows_prevents_accidental_section_removal(self):
        async with self.sessions() as db:
            db.add(SectionAdviser(school_id=1, teacher_id=self.teacher_id, section_id=11))
            await db.commit()
        response = await self.client.patch("/api/grade-levels/1", json={
            "name": "Should not save", "sections": [{"section_id": 10, "name": "Changed"}],
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("adviser", response.json()["detail"])
        response = await self.client.delete("/api/sections/11")
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(GradeLevel, 1)).name, "Grade 7")
            self.assertEqual((await db.get(Section, 10)).name, "A")
            self.assertEqual((await db.get(Section, 11)).name, "B")

    async def test_historical_enrollments_prevent_section_and_grade_removal(self):
        async with self.sessions() as db:
            db.add(Student(student_id=1, school_id=1, first_name="Test", last_name="Learner"))
            db.add(SchoolYear(school_year_id=1, school_id=1, year="2026-2027", start_date=date(2026, 6, 1), end_date=date(2027, 5, 31)))
            await db.flush()
            db.add(StudentEnrollment(school_id=1, student_id=1, grade_level_id=1, section_id=11, school_year_id=1, is_active=False))
            await db.commit()
        response = await self.client.patch("/api/grade-levels/1", json={"sections": []})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(await self._sections()), 2)
        response = await self.client.delete("/api/grade-levels/1")
        self.assertEqual(response.status_code, 409, response.text)

    async def test_sections_with_class_records_cannot_be_removed(self):
        async with self.sessions() as db:
            db.add(ClassRecordSetup(school_id=1, section_id=11, subject_id=100))
            await db.commit()
        response = await self.client.patch("/api/grade-levels/1/with-sections", json={"sections": []})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("class records", response.json()["detail"])
        response = await self.client.delete("/api/sections/11")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(await self._sections()), 2)

    async def test_cross_grade_cross_school_and_duplicate_ids_cannot_be_submitted(self):
        for sections in (
            [{"section_id": 12, "name": "X"}], [{"section_id": 20, "name": "X"}],
            [{"section_id": 999, "name": "X"}],
            [{"section_id": 10, "name": "A"}, {"section_id": 10, "name": "B"}],
        ):
            with self.subTest(sections=sections):
                response = await self.client.patch("/api/grade-levels/1", json={"name": "Should not save", "sections": sections})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual({row["name"] for row in await self._sections()}, {"A", "B"})
        response = await self.client.patch("/api/grade-levels/3", json={"name": "Other"})
        self.assertEqual(response.status_code, 404, response.text)

    async def test_section_names_are_unique_within_grade_but_reusable_in_another_grade(self):
        response = await self.client.post("/api/sections", json={"grade_level_id": 1, "name": " a "})
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.post("/api/sections", json={"grade_level_id": 2, "name": "A"})
        self.assertEqual(response.status_code, 201, response.text)
        response = await self.client.patch("/api/sections/11", json={"name": "A"})
        self.assertEqual(response.status_code, 409, response.text)

    async def test_subjects_need_no_code_and_use_grade_scoped_name_uniqueness(self):
        for grade in (1, 2):
            response = await self.client.post("/api/subjects", json={"grade_level_id": grade, "subject_name": "Mathematics"})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertNotIn("subject_code", response.json())
        response = await self.client.post("/api/subjects", json={"grade_level_id": 1, "subject_name": " english "})
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.patch("/api/subjects/100", json={"subject_name": "Mathematics"})
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.patch("/api/subjects/200", json={"subject_name": "Changed"})
        self.assertEqual(response.status_code, 404, response.text)

    async def test_blank_names_invalid_capacity_and_teacher_writes_are_rejected(self):
        for section in ({"name": " "}, {"name": "Faith", "capacity": 0}):
            response = await self.client.post("/api/grade-levels", json={"level": "9", "name": "Grade 9", "sections": [section]})
            self.assertEqual(response.status_code, 422, response.text)
        self.actor = self.teacher_id
        response = await self.client.post("/api/grade-levels", json={"level": "9", "name": "Grade 9", "sections": []})
        self.assertEqual(response.status_code, 403, response.text)

    def test_openapi_no_longer_has_section_or_subject_code_fields(self):
        schema = json.dumps(self.app.openapi())
        self.assertNotIn("section_code", schema)
        self.assertNotIn("subject_code", schema)


if __name__ == "__main__":
    unittest.main()
