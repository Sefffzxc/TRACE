"""School-year validation and seat limits against disposable SQLite only."""
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_teacher_assignments import AssignmentFixture

from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.routers import academic, students
from app.models.academic import SchoolYear, Section, SectionAdviser
from app.models.students import Student, StudentEnrollment


class AcademicYearValidationTests(AssignmentFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async with self.sessions() as db:
            db.add(SchoolYear(school_year_id=3, school_id=1, year="2027-2028",
                start_date=date(2027, 6, 1), end_date=date(2028, 5, 31)))
            await db.commit()

    async def test_duplicate_edit_returns_conflict_without_switching_current_year(self):
        response = await self.client.patch("/api/school-years/3", json={
            "year": "2026-2027", "is_current": True,
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "This school year already exists.")
        async with self.sessions() as db:
            self.assertTrue((await db.get(SchoolYear, 1)).is_current)
            row = await db.get(SchoolYear, 3)
            self.assertEqual(row.year, "2027-2028")
            self.assertFalse(row.is_current)
            self.assertEqual(row.start_date, date(2027, 6, 1))

    async def test_unchanged_name_is_allowed_and_other_schools_do_not_conflict(self):
        response = await self.client.patch("/api/school-years/3", json={"year": "2027-2028"})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.sessions() as db:
            db.add(SchoolYear(school_id=2, year="2028-2029",
                start_date=date(2028, 6, 1), end_date=date(2029, 5, 31)))
            await db.commit()
        response = await self.client.patch("/api/school-years/3", json={"year": "2028-2029"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_duplicate_create_has_same_clear_message(self):
        response = await self.client.post("/api/school-years", json={"year": "2026-2027"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "This school year already exists.")

    async def test_database_uniqueness_conflict_is_rolled_back_and_translated(self):
        async with self.sessions() as db:
            (await db.get(SchoolYear, 3)).year = "2026-2027"
            with self.assertRaises(HTTPException) as caught:
                await academic._commit_school_year(db)
            self.assertEqual(caught.exception.status_code, 409)
            self.assertEqual(caught.exception.detail, "This school year already exists.")
            self.assertEqual((await db.get(SchoolYear, 3)).year, "2027-2028")

    async def test_concurrent_edits_cannot_claim_the_same_name(self):
        self.start_barrier = asyncio.Barrier(2)
        try:
            responses = await asyncio.gather(*[
                self.client.patch(f"/api/school-years/{identity}", json={"year": "2029-2030"})
                for identity in (1, 3)
            ])
        finally:
            self.start_barrier = None
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409],
                         [r.text for r in responses])


class SectionCapacityTests(AssignmentFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.app.include_router(students.router)
        self.actor = self.people["Teacher A"].id
        async with self.sessions() as db:
            for section_id in (10, 11):
                (await db.get(Section, section_id)).capacity = 1
                db.add(SectionAdviser(school_id=1, school_year_id=1,
                    teacher_id=self.actor, section_id=section_id))
            await db.commit()

    def _new(self, name="New", section_id=10):
        return {"first_name": name, "last_name": "Learner", "section_id": section_id,
                "grade_level_id": 1, "school_year_id": 1}

    def _enroll(self, student_id, section_id=10):
        return {"student_id": student_id, "section_id": section_id,
                "grade_level_id": 1, "school_year_id": 1}

    async def _profile(self, *, active=None, section_id=10, year_id=1):
        async with self.sessions() as db:
            student = Student(school_id=1, first_name="Returning", last_name="Learner")
            db.add(student)
            await db.flush()
            enrollment = None
            if active is not None:
                enrollment = StudentEnrollment(school_id=1, student_id=student.student_id,
                    section_id=section_id, grade_level_id=1, school_year_id=year_id,
                    is_active=active)
                db.add(enrollment)
            await db.commit()
            return student.student_id, enrollment.enrollment_id if enrollment else None

    async def _counts(self):
        async with self.sessions() as db:
            return tuple([await db.scalar(select(func.count()).select_from(model))
                          for model in (Student, StudentEnrollment)])

    async def test_add_student_rejects_full_section_before_creating_an_orphan(self):
        self.assertEqual((await self.client.post("/api/students", json=self._new())).status_code, 201)
        response = await self.client.post("/api/students", json=self._new("Another"))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("1/1", response.json()["detail"])
        self.assertEqual(await self._counts(), (1, 1))

    async def test_returning_enrollment_uses_the_same_capacity_rule(self):
        await self._profile(active=True)
        student_id, _ = await self._profile()
        response = await self.client.post("/api/enrollments", json=self._enroll(student_id))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("1/1", response.json()["detail"])
        self.assertEqual(await self._counts(), (2, 1))

    async def test_inactive_and_previous_year_enrollments_do_not_consume_current_seats(self):
        async with self.sessions() as db:
            db.add(SchoolYear(school_year_id=3, school_id=1, year="2025-2026",
                start_date=date(2025, 6, 1), end_date=date(2026, 5, 31)))
            await db.commit()
        await self._profile(active=True, year_id=3)
        await self._profile(active=False)
        response = await self.client.post("/api/students", json=self._new())
        self.assertEqual(response.status_code, 201, response.text)

    async def test_returning_reactivation_checks_capacity_and_reuses_existing_enrollment(self):
        student_id, enrollment_id = await self._profile(active=False)
        _, occupied_id = await self._profile(active=True)
        response = await self.client.post("/api/enrollments", json=self._enroll(student_id))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self.client.patch(f"/api/enrollments/{occupied_id}",
                                                json={"is_active": False})).status_code, 200)
        response = await self.client.post("/api/enrollments", json=self._enroll(student_id))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["enrollment_id"], enrollment_id)
        self.assertEqual(await self._counts(), (2, 2))

    async def test_patch_reactivation_checks_capacity_but_active_edits_do_not_use_an_extra_seat(self):
        _, enrollment_id = await self._profile(active=False)
        _, occupied_id = await self._profile(active=True)
        route = f"/api/enrollments/{enrollment_id}"
        response = await self.client.patch(route, json={"is_active": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self.client.patch(f"/api/enrollments/{occupied_id}",
            json={"end_of_year_status": "Promoted"})).status_code, 200)
        await self.client.patch(f"/api/enrollments/{occupied_id}", json={"is_active": False})
        self.assertEqual((await self.client.patch(route, json={"is_active": True})).status_code, 200)

    async def test_transfer_to_full_section_is_rejected_without_moving_the_learner(self):
        _, enrollment_id = await self._profile(active=True)
        _, occupied_id = await self._profile(active=True, section_id=11)
        route = f"/api/enrollments/{enrollment_id}"
        response = await self.client.patch(route, json={"section_id": 11})
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            self.assertEqual((await db.get(StudentEnrollment, enrollment_id)).section_id, 10)
        await self.client.patch(f"/api/enrollments/{occupied_id}", json={"is_active": False})
        self.assertEqual((await self.client.patch(route, json={"section_id": 11})).status_code, 200)

    async def test_explicit_activation_rechecks_a_concurrent_withdrawal_and_replacement(self):
        _, enrollment_id = await self._profile(active=True)
        lock_section = students.lock_roster_section

        async def withdraw_then_replace(db, scope, section_id):
            async with self.sessions() as concurrent:
                (await concurrent.get(StudentEnrollment, enrollment_id)).is_active = False
                await concurrent.commit()
            await self._profile(active=True)
            return await lock_section(db, scope, section_id)

        with patch.object(students, "lock_roster_section", side_effect=withdraw_then_replace) as locked:
            response = await self.client.patch(f"/api/enrollments/{enrollment_id}",
                                               json={"is_active": True})
        locked.assert_awaited_once()
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            self.assertFalse((await db.get(StudentEnrollment, enrollment_id)).is_active)

    async def test_section_only_patch_rechecks_a_concurrently_activated_enrollment(self):
        _, enrollment_id = await self._profile(active=False)
        await self._profile(active=True, section_id=11)
        lock_section = students.lock_roster_section

        async def activate_before_transfer(db, scope, section_id):
            async with self.sessions() as concurrent:
                (await concurrent.get(StudentEnrollment, enrollment_id)).is_active = True
                await concurrent.commit()
            return await lock_section(db, scope, section_id)

        with patch.object(students, "lock_roster_section", side_effect=activate_before_transfer) as locked:
            response = await self.client.patch(f"/api/enrollments/{enrollment_id}",
                                               json={"section_id": 11})
        locked.assert_awaited_once()
        self.assertEqual(response.status_code, 409, response.text)
        async with self.sessions() as db:
            row = await db.get(StudentEnrollment, enrollment_id)
            self.assertTrue(row.is_active)
            self.assertEqual(row.section_id, 10)

    async def test_implicit_destination_rejects_a_concurrent_section_move(self):
        _, enrollment_id = await self._profile(active=False)
        lock_section = students.lock_roster_section

        async def move_before_activation(db, scope, section_id):
            async with self.sessions() as concurrent:
                (await concurrent.get(StudentEnrollment, enrollment_id)).section_id = 11
                await concurrent.commit()
            return await lock_section(db, scope, section_id)

        with patch.object(students, "lock_roster_section", side_effect=move_before_activation):
            response = await self.client.patch(f"/api/enrollments/{enrollment_id}",
                                               json={"is_active": True})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("changed sections", response.json()["detail"])
        async with self.sessions() as db:
            row = await db.get(StudentEnrollment, enrollment_id)
            self.assertEqual(row.section_id, 11)
            self.assertFalse(row.is_active)

    async def test_explicit_active_patch_keeps_its_own_seat_when_section_is_full(self):
        _, enrollment_id = await self._profile(active=True)
        response = await self.client.patch(f"/api/enrollments/{enrollment_id}",
                                           json={"is_active": True, "section_id": 10})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["is_active"])

    async def _race(self, requests, expected):
        self.start_barrier = asyncio.Barrier(len(requests))
        try:
            responses = await asyncio.gather(*requests)
        finally:
            self.start_barrier = None
        self.assertEqual(sorted(r.status_code for r in responses), expected,
                         [r.text for r in responses])
        async with self.sessions() as db:
            active = await db.scalar(select(func.count()).select_from(StudentEnrollment).where(
                StudentEnrollment.section_id == 10, StudentEnrollment.is_active.is_(True),
            ))
            self.assertEqual(active, 1)

    async def test_simultaneous_new_students_cannot_take_the_same_last_seat(self):
        await self._race([
            self.client.post("/api/students", json=self._new(name)) for name in ("One", "Two")
        ], [201, 409])
        self.assertEqual(await self._counts(), (1, 1))

    async def test_new_and_returning_student_share_the_same_last_seat_lock(self):
        student_id, _ = await self._profile()
        await self._race([
            self.client.post("/api/students", json=self._new()),
            self.client.post("/api/enrollments", json=self._enroll(student_id)),
        ], [201, 409])

    async def test_simultaneous_reactivations_cannot_take_the_same_last_seat(self):
        _, first = await self._profile(active=False)
        _, second = await self._profile(active=False)
        await self._race([
            self.client.patch(f"/api/enrollments/{identity}", json={"is_active": True})
            for identity in (first, second)
        ], [200, 409])


if __name__ == "__main__":
    unittest.main()
