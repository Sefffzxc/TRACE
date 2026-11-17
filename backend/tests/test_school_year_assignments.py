"""Academic responsibilities and section summaries belong to one school year."""
from __future__ import annotations

from datetime import date
import asyncio
from pathlib import Path
import sys
import unittest

from fastapi import HTTPException
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_teacher_assignments import AssignmentFixture, TEST_POSTGRES_URL

from app.models.academic import SchoolYear, SectionAdviser, TeacherAssignment
from app.models.staff import Staff
from app.models.students import Student, StudentEnrollment
from app.services.access import class_access
from app.services.roster_access import require_roster_section
from app.services.school_years import require_current_year_record, resolve_school_year_id


class SchoolYearAssignmentTests(AssignmentFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.sessions() as db:
            db.add(SchoolYear(school_year_id=3, school_id=1, year="2027-2028",
                start_date=date(2027, 6, 1), end_date=date(2028, 5, 31), is_current=False))
            await db.commit()

    async def _switch(self, year_id):
        response = await self.client.patch(f"/api/school-years/{year_id}", json={"is_current": True})
        self.assertEqual(response.status_code, 200, response.text)

    async def _adviser(self, teacher="Teacher A", year_id=None):
        payload = {"teacher_id": str(self.people[teacher].id)}
        if year_id is not None:
            payload["school_year_id"] = year_id
        return await self.client.put("/api/sections/10/adviser", json=payload)

    async def _access(self):
        async with self.sessions() as db:
            return await class_access(db, await db.get(Staff, self.people["Teacher A"].id), 1)

    async def test_new_year_is_empty_and_switch_back_restores_assignments_and_advice(self):
        teaching = await self._create()
        advice = await self._adviser()
        self.assertEqual(teaching.status_code, 201, teaching.text)
        self.assertEqual(advice.status_code, 200, advice.text)
        await self._switch(3)
        self.assertEqual((await self.client.get("/api/teacher-assignments")).json(), [])
        self.assertEqual((await self.client.get("/api/section-advisers")).json(), [])
        self.assertEqual((await self._access()).sections, set())
        # Reusable definitions remain available in the empty year.
        self.assertEqual(len((await self.client.get("/api/sections")).json()), 2)
        self.assertEqual(len((await self.client.get("/api/subjects")).json()), 3)
        await self._switch(1)
        restored = (await self.client.get("/api/teacher-assignments")).json()
        self.assertEqual([row["assignment_id"] for row in restored], [teaching.json()["assignment_id"]])
        self.assertEqual((await self.client.get("/api/section-advisers")).json()[0]["adviser_id"], advice.json()["adviser_id"])
        self.assertTrue((await self._access()).can_grade(10, "English"))
        self.assertEqual((await self._access()).advisory, {10})

    async def test_same_teacher_and_subject_can_be_assigned_independently_in_both_years(self):
        old = await self._create()
        old_adviser = await self._adviser()
        await self._switch(3)
        new = await self._create()
        new_adviser = await self._adviser("Teacher B")
        self.assertEqual(new.status_code, 201, new.text)
        self.assertEqual(new_adviser.status_code, 200, new_adviser.text)
        self.assertEqual(new.json()["school_year_id"], 3)
        self.assertNotEqual(new.json()["assignment_id"], old.json()["assignment_id"])
        self.assertNotEqual(new_adviser.json()["adviser_id"], old_adviser.json()["adviser_id"])
        self.assertEqual((await self._create("Teacher B")).status_code, 409)
        await self._switch(1)
        self.assertEqual((await self.client.get("/api/section-advisers")).json()[0]["teacher_id"], str(self.people["Teacher A"].id))

    async def test_old_assignment_id_and_stale_payload_cannot_mutate_archived_year(self):
        old = (await self._create()).json()
        async with self.sessions() as db:
            legacy = TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people["Teacher A"].id,
                                       section_id=11, subject_id=None)
            db.add(legacy)
            await db.commit()
            legacy_id = legacy.assignment_id
        await self._switch(3)
        self.assertEqual((await self.client.delete(f"/api/teacher-assignments/{old['assignment_id']}")).status_code, 409)
        self.assertEqual((await self.client.post(f"/api/teacher-assignments/{legacy_id}/review", json={"subject_ids": []})).status_code, 409)
        payload = {**self._payload(), "school_year_id": 1}
        self.assertEqual((await self.client.post("/api/teacher-assignments", json=payload)).status_code, 409)
        self.assertEqual((await self._adviser(year_id=1)).status_code, 409)
        async with self.sessions() as db:
            self.assertIsNotNone(await db.get(TeacherAssignment, old["assignment_id"]))
            self.assertIsNotNone(await db.get(TeacherAssignment, legacy_id))

    async def test_removing_new_adviser_never_removes_old_adviser(self):
        old = await self._adviser()
        await self._switch(3)
        self.assertEqual((await self._adviser("Teacher B")).status_code, 200)
        self.assertEqual((await self.client.delete("/api/sections/10/adviser")).status_code, 204)
        self.assertEqual((await self.client.get("/api/section-advisers")).json(), [])
        await self._switch(1)
        self.assertEqual((await self.client.get("/api/section-advisers")).json()[0]["adviser_id"], old.json()["adviser_id"])

    async def test_section_and_grade_counts_and_record_access_only_use_current_year(self):
        await self._create()
        await self._adviser()
        async with self.sessions() as db:
            db.add_all([Student(student_id=i, school_id=1, first_name=f"Learner {i}", last_name="Test") for i in (1, 2)])
            await db.flush()
            db.add_all([StudentEnrollment(enrollment_id=i, student_id=i, school_id=1, school_year_id=year,
                grade_level_id=1, section_id=10, is_active=True) for i, year in ((1, 1), (2, 3))])
            await db.commit()
        access = await self._access()
        self.assertEqual(access.section_of, {1: 10})
        self.assertTrue(access.can_see_record(1, "English"))
        self.assertFalse(access.can_see_record(2, "English"))
        self.assertEqual((await self.client.get("/api/sections")).json()[0]["enrolled_count"], 1)
        self.assertEqual((await self.client.get("/api/grade-levels")).json()[0]["student_count"], 1)
        await self._switch(3)
        section = (await self.client.get("/api/sections")).json()[0]
        self.assertEqual(section["enrolled_count"], 1)
        self.assertIsNone(section["adviser_name"])

    async def test_roster_creation_requires_responsibility_in_current_year(self):
        await self._adviser()
        await self._switch(3)
        async with self.sessions() as db:
            teacher = await db.get(Staff, self.people["Teacher A"].id)
            with self.assertRaises(HTTPException) as caught:
                await require_roster_section(db, teacher, 1, 10)
            self.assertEqual(caught.exception.status_code, 403)
        await self._create()
        async with self.sessions() as db:
            teacher = await db.get(Staff, self.people["Teacher A"].id)
            self.assertEqual((await require_roster_section(db, teacher, 1, 10)).section_id, 10)
            with self.assertRaises(HTTPException) as caught:
                await require_roster_section(db, teacher, 1, 10, school_year_id=1)
            self.assertEqual(caught.exception.status_code, 409)

    async def test_no_current_year_has_no_latest_year_or_null_assignment_fallback(self):
        await self._create()
        await self._adviser()
        async with self.sessions() as db:
            db.add(TeacherAssignment(school_id=1, teacher_id=self.people["Teacher A"].id,
                section_id=11, subject_id=100, school_year_id=None))
            db.add(SectionAdviser(school_id=1, teacher_id=self.people["Teacher A"].id,
                section_id=11, school_year_id=None))
            await db.commit()
        response = await self.client.patch("/api/school-years/1", json={"is_current": False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone((await self.client.get("/api/school-years/current")).json())
        self.assertEqual((await self.client.get("/api/teacher-assignments")).json(), [])
        self.assertEqual((await self.client.get("/api/section-advisers")).json(), [])
        self.assertEqual((await self._access()).sections, set())
        self.assertEqual((await self._create()).status_code, 400)
        self.assertEqual((await self._adviser()).status_code, 400)
        await self._switch(1)
        self.assertEqual((await self._access()).sections, {10})

    async def test_explicit_year_is_school_owned_and_stale_record_is_rejected(self):
        async with self.sessions() as db:
            self.assertEqual(await resolve_school_year_id(db, 1, 3), 3)
            with self.assertRaises(HTTPException) as caught:
                await resolve_school_year_id(db, 1, 2)
            self.assertEqual(caught.exception.status_code, 400)
            with self.assertRaises(HTTPException) as caught:
                await require_current_year_record(db, 1, 3)
            self.assertEqual(caught.exception.status_code, 409)

    async def test_year_and_subject_deletion_preserve_assignment_history(self):
        await self._create()
        await self._adviser()
        await self._switch(3)
        response = await self.client.delete("/api/school-years/1")
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.delete("/api/subjects/100")
        self.assertEqual(response.status_code, 409, response.text)
        await self._switch(1)
        self.assertEqual(len((await self.client.get("/api/teacher-assignments")).json()), 1)

    async def test_stale_browser_header_rejects_reads_and_writes_without_touching_new_year(self):
        await self._create()
        await self._adviser()
        await self._switch(3)
        stale = {"X-School-Year-Id": "1"}
        for endpoint in ("/api/teacher-assignments", "/api/section-advisers", "/api/my-classes", "/api/sections"):
            response = await self.client.get(endpoint, headers=stale)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(response.json()["detail"]["code"], "school_year_changed")
            self.assertEqual(response.json()["detail"]["current_school_year_id"], 3)
        response = await self.client.post("/api/teacher-assignments", json=self._payload(), headers=stale)
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.delete("/api/sections/10/adviser", headers=stale)
        self.assertEqual(response.status_code, 409, response.text)
        response = await self.client.get("/api/teacher-assignments", headers={"X-School-Year-Id": "3"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])
        await self._switch(1)
        self.assertEqual(len((await self.client.get("/api/section-advisers")).json()), 1)
        self.assertEqual(len((await self.client.get("/api/teacher-assignments")).json()), 1)

    async def test_invalid_browser_year_header_cannot_choose_foreign_or_invalid_year(self):
        for value in ("invalid", "0", "-1"):
            response = await self.client.get("/api/my-classes", headers={"X-School-Year-Id": value})
            self.assertEqual(response.status_code, 400, response.text)
        response = await self.client.get("/api/my-classes", headers={"X-School-Year-Id": "2"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["current_school_year_id"], 1)


@unittest.skipUnless(TEST_POSTGRES_URL, "Set TRACE_TEST_POSTGRES_URL for isolated local PostgreSQL year checks.")
class SchoolYearAssignmentPostgresTests(SchoolYearAssignmentTests):
    postgres_url = TEST_POSTGRES_URL

    async def test_concurrent_year_switch_and_old_browser_save_never_write_into_new_year(self):
        self.start_barrier = asyncio.Barrier(2)
        try:
            switched, saved = await asyncio.gather(
                self.client.patch("/api/school-years/3", json={"is_current": True}),
                self.client.post("/api/teacher-assignments", json=self._payload(),
                                 headers={"X-School-Year-Id": "1"}),
            )
        finally:
            self.start_barrier = None
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertIn(saved.status_code, (201, 409), saved.text)
        async with self.sessions() as db:
            rows = (await db.scalars(select(TeacherAssignment))).all()
            self.assertEqual(len(rows), int(saved.status_code == 201))
            self.assertTrue(all(row.school_year_id == 1 for row in rows))
        self.assertEqual((await self.client.get("/api/teacher-assignments")).json(), [])
        self.assertEqual((await self.client.get("/api/school-years/current")).json()["school_year_id"], 3)


if __name__ == "__main__":
    unittest.main()
