"""Independent advisers, safe legacy review, and server-side write boundaries."""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
import sys
import unittest

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_teacher_assignments import AssignmentFixture

from app.api.routers import class_import, pace
from app.models.academic import SchoolYear, SectionAdviser, Subject, TeacherAssignment
from app.models.staff import Staff
from app.models.students import ClassRecordSetup, Student, StudentEnrollment, StudentPace
from app.services.access import class_access


class SectionAdviserTests(AssignmentFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app.include_router(pace.router)
        self.app.include_router(class_import.router)

    async def _adviser(self, teacher="Teacher A", section=10):
        return await self.client.put(f"/api/sections/{section}/adviser",
                                     json={"teacher_id": str(self.people[teacher].id)})

    async def _legacy(self, teacher="Teacher A", section=10, adviser=True):
        async with self.sessions() as db:
            row = TeacherAssignment(school_id=1, school_year_id=1, teacher_id=self.people[teacher].id,
                                    section_id=section, subject_id=None, is_adviser=adviser)
            db.add(row)
            await db.commit()
            return row.assignment_id

    async def _access(self, teacher="Teacher A"):
        async with self.sessions() as db:
            return await class_access(db, await db.get(Staff, self.people[teacher].id), 1)

    async def test_adviser_only_sees_all_subjects_and_cannot_grade(self):
        response = await self._adviser()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["teacher_name"], "Teacher A Example")
        self.assertEqual(response.json()["grade_level_name"], "Grade 7")
        access = await self._access()
        self.assertEqual(access.grade, {})
        self.assertEqual(access.advisory, {10})
        for subject in ("English", "Science"):
            self.assertTrue(access.can_see(10, subject))
            self.assertFalse(access.can_grade(10, subject))
        self.assertFalse(access.can_see(11, "English"))
        self.actor = self.people["Teacher A"].id
        response = await self.client.get("/api/my-classes")
        self.assertEqual(response.json(), {"school_year_id": 1, "unrestricted": False, "grade": {}, "advisory": [10]})

    async def test_adviser_and_teaching_rights_are_independent(self):
        self.assertEqual((await self._adviser()).status_code, 200)
        self.assertEqual((await self._create()).status_code, 201)
        self.assertEqual((await self._create("Teacher B", subject=101)).status_code, 201)
        access = await self._access()
        self.assertTrue(access.can_grade(10, "English"))
        self.assertTrue(access.can_see(10, "Science"))
        self.assertFalse(access.can_grade(10, "Science"))
        assignments = (await self.client.get("/api/teacher-assignments")).json()
        identity = next(row["assignment_id"] for row in assignments if row["teacher_id"] == str(self.people["Teacher A"].id))
        self.assertEqual((await self.client.delete(f"/api/teacher-assignments/{identity}")).status_code, 204)
        self.assertEqual((await self._access()).advisory, {10})
        self.assertEqual((await self._access()).grade, {})

    async def test_removing_adviser_preserves_taught_subject_and_ignores_old_flags(self):
        await self._adviser()
        await self._legacy()
        self.assertEqual((await self._create()).status_code, 201)
        self.assertEqual((await self.client.delete("/api/sections/10/adviser")).status_code, 204)
        access = await self._access()
        self.assertEqual(access.advisory, set())
        self.assertTrue(access.can_grade(10, "English"))
        self.assertFalse(access.can_see(10, "Science"))

    async def test_subject_deletion_does_not_remove_adviser_or_expand_teaching_access(self):
        await self._adviser()
        await self._create()
        response = await self.client.delete("/api/subjects/100")
        self.assertEqual(response.status_code, 204, response.text)
        access = await self._access()
        self.assertEqual(access.advisory, {10})
        self.assertEqual(access.grade, {})
        self.assertFalse(access.can_grade(10, "Science"))

    async def test_old_adviser_blank_is_never_grading_and_nonadviser_blank_retains_compatibility(self):
        await self._legacy()
        await self._legacy("Teacher B", section=11, adviser=False)
        self.assertEqual((await self._access()).grade, {})
        self.assertEqual((await self._access()).advisory, set())
        self.assertTrue((await self._access("Teacher B")).can_grade(11, "Science"))
        self.assertEqual((await self._create("Teacher C")).status_code, 201)

    async def test_adviser_replacement_updates_one_row_without_reassigning_subjects(self):
        await self._create()
        first = await self._adviser()
        second = await self._adviser("Teacher B")
        self.assertEqual(first.json()["adviser_id"], second.json()["adviser_id"])
        response = await self.client.get("/api/section-advisers")
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["teacher_id"], str(self.people["Teacher B"].id))
        self.assertTrue((await self._access()).can_grade(10, "English"))
        self.assertEqual((await self._access()).advisory, set())
        sections = (await self.client.get("/api/sections")).json()
        self.assertEqual(next(row for row in sections if row["section_id"] == 10)["adviser_name"], "Teacher B Example")

    async def test_adviser_choices_are_scoped_active_approved_teachers(self):
        for teacher, section in (("Private Other Teacher", 10), ("Teacher A", 20), ("Principal", 10)):
            response = await self._adviser(teacher, section)
            self.assertIn(response.status_code, (400, 404), response.text)
            self.assertNotIn("Private Other Teacher", response.text)
        for state, approved, active in (("pending", False, True), ("blocked", False, False),
                                        ("approved", False, True), ("approved", True, False)):
            async with self.sessions() as db:
                teacher = await db.get(Staff, self.people["Teacher A"].id)
                teacher.account_status, teacher.is_approved, teacher.is_active = state, approved, active
                await db.commit()
            self.assertEqual((await self._adviser()).status_code, 400)

    async def test_teachers_and_operator_cannot_manage_or_list_advisers(self):
        identity = await self._legacy()
        for teacher in ("Teacher A", "Operator"):
            self.actor = self.people[teacher].id
            self.assertEqual((await self._adviser()).status_code, 403)
            self.assertEqual((await self.client.delete("/api/sections/10/adviser")).status_code, 403)
            self.assertEqual((await self.client.get("/api/section-advisers")).status_code, 403)
            self.assertEqual((await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": []})).status_code, 403)

    async def test_concurrent_adviser_updates_leave_exactly_one_adviser(self):
        self.start_barrier = asyncio.Barrier(2)
        try:
            responses = await asyncio.gather(self._adviser(), self._adviser("Teacher B"))
        finally:
            self.start_barrier = None
        self.assertEqual([r.status_code for r in responses], [200, 200], [r.text for r in responses])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(SectionAdviser)), 1)

    async def test_review_replaces_only_legacy_row_and_keeps_adviser(self):
        await self._adviser()
        identity = await self._legacy()
        response = await self.client.get("/api/teacher-assignments")
        self.assertTrue(response.json()[0]["needs_subject_review"])
        response = await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": [100, 101]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({row["subject_id"] for row in response.json()}, {100, 101})
        self.assertTrue(all(not row["is_adviser"] and not row["needs_subject_review"] for row in response.json()))
        async with self.sessions() as db:
            self.assertIsNone(await db.get(TeacherAssignment, identity))
        access = await self._access()
        self.assertEqual(access.advisory, {10})
        self.assertTrue(access.can_grade(10, "English"))

    async def test_review_empty_means_adviser_only_and_does_not_require_teacher_approval(self):
        await self._adviser()
        identity = await self._legacy(adviser=False)
        await self._legacy("Teacher B", adviser=False)
        async with self.sessions() as db:
            teacher = await db.get(Staff, self.people["Teacher A"].id)
            teacher.is_approved = False
            await db.commit()
        response = await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": []})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])
        self.assertEqual((await self._access()).advisory, {10})
        self.assertEqual((await self._access()).grade, {})

    async def test_review_reuses_same_teacher_existing_subject_without_duplicate(self):
        identity = await self._legacy()
        response = await self._create()
        original = response.json()["assignment_id"]
        response = await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": [100, 101]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(next(row for row in response.json() if row["subject_id"] == 100)["assignment_id"], original)
        self.assertEqual(await self._count(), 2)

    async def test_review_conflicts_and_invalid_subjects_are_atomic(self):
        identity = await self._legacy()
        await self._adviser()
        self.assertEqual((await self._create("Teacher B", subject=101)).status_code, 201)
        for subjects, expected in (([100, 101], 409), ([100, 102], 400), ([100, 200], 400), ([100, 100], 422)):
            response = await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": subjects})
            self.assertEqual(response.status_code, expected, response.text)
            self.assertEqual(await self._count(), 2)
            async with self.sessions() as db:
                self.assertIsNotNone(await db.get(TeacherAssignment, identity))
            self.assertEqual((await self._access()).advisory, {10})

    async def test_review_cannot_read_foreign_assignments_or_re_review_concrete_rows(self):
        async with self.sessions() as db:
            row = TeacherAssignment(school_id=2, school_year_id=2, teacher_id=self.people["Private Other Teacher"].id,
                                    section_id=20, subject_id=None)
            db.add(row)
            await db.commit()
            identity = row.assignment_id
        response = await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": []})
        self.assertEqual(response.status_code, 404, response.text)
        identity = (await self._create()).json()["assignment_id"]
        response = await self.client.post(f"/api/teacher-assignments/{identity}/review", json={"subject_ids": []})
        self.assertEqual(response.status_code, 400, response.text)

    async def test_adviser_reads_saved_scores_and_setup_but_all_write_paths_refuse(self):
        await self._adviser()
        async with self.sessions() as db:
            db.add(Student(student_id=1, school_id=1, first_name="Test", last_name="Learner"))
            await db.flush()
            db.add(StudentEnrollment(enrollment_id=1, student_id=1, school_id=1,
                school_year_id=1, grade_level_id=1, section_id=10))
            await db.flush()
            db.add(StudentPace(pace_id=1, school_id=1, student_id=1, enrollment_id=1,
                subject_id=100, subject="English", ww_scores="[7]", grading_period=1))
            await db.commit()
        self.actor = self.people["Teacher A"].id
        response = await self.client.get("/api/paces", params={"section_id": 10})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["ww_scores"], [7])
        response = await self.client.get("/api/class-record/setup", params={"section_id": 10, "subject_id": 100})
        self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.put("/api/class-record/setup", params={"section_id": 10, "subject_id": 100}, json={"ww_highest": [10]})
        self.assertEqual(response.status_code, 403, response.text)
        response = await self.client.patch("/api/paces/1", json={"ww_scores": [9]})
        self.assertEqual(response.status_code, 403, response.text)
        response = await self.client.post("/api/paces", json={"student_id": 1, "enrollment_id": 1,
            "subject": "English", "current_pace": 0, "expected_pace": 0, "total_paces": 12})
        self.assertEqual(response.status_code, 403, response.text)
        response = await self.client.post("/api/paces/bulk", json={"section_id": 10, "subject_id": 100,
            "grading_period": 1, "entries": [{"student_id": 1, "enrollment_id": 1,
                "subject": "English", "ww_scores": [9], "current_pace": 0,
                "expected_pace": 0, "total_paces": 12}]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["saved"], 0)
        self.assertEqual(len(response.json()["skipped"]), 1)
        for save in ("false", "true"):
            response = await self.client.post("/api/class-record/import",
                data={"section_id": "10", "subject_id": "100", "save": save},
                files={"file": ("record.xlsx", b"not a workbook", "application/octet-stream")})
            self.assertEqual(response.status_code, 403, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(StudentPace, 1)).ww_scores, "[7]")
            self.assertEqual(await db.scalar(select(func.count()).select_from(ClassRecordSetup)), 0)
