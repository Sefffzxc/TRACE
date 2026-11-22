"""Concurrent account writes must not claim the same mobile number."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.api import deps
from app.api.routers import auth, schools, staff
from app.db.session import Base, get_db
from app.models.schools import School
from app.models.staff import Staff


class ContactConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(prefix="trace-contact-test-")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.temp.name).as_posix()}/test.db")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"Test School {i}", city="Lala", status="approved") for i in (1, 2)])
            await db.flush()
            person = Staff(school_id=1, email="profile@example.com", password_hash="unused", first_name="Profile",
                           last_name="Test", contact_number="09111111111", role="teacher",
                           account_status="approved", is_approved=True)
            db.add(person)
            await db.commit()
            self.profile_id = person.id
        async def database():
            async with self.sessions() as db:
                try:
                    yield db
                except Exception:
                    await db.rollback()
                    raise
        async def current(db=Depends(get_db)):
            return await db.get(Staff, self.profile_id)
        async def no_limit():
            pass
        application = FastAPI()
        for router in (auth.router, schools.router, staff.router):
            application.include_router(router)
        application.dependency_overrides.update({get_db: database, deps.limit_register: no_limit,
                                                deps.get_current_user: current})
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://testserver")
        for owner in (auth, schools):
            for name in ("check_proof", "consume_proof", "async_get_password_hash"):
                patcher = patch.object(owner, name, AsyncMock(return_value="synthetic-hash"))
                patcher.start()
                self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.temp.cleanup()

    def teacher(self, number, index=1):
        return {"school_id": index, "email": f"teacher{index}@example.com", "first_name": "Test", "last_name": "Teacher",
                "contact_number": number, "password": "SyntheticPassword1!"}

    async def assert_one_owner(self, number, requests, expected):
        responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=15)
        self.assertEqual(sorted(response.status_code for response in responses), sorted(expected),
                         [response.text for response in responses])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Staff).where(Staff.contact_number == number)), 1)

    async def test_different_school_registrations_compete_for_one_number(self):
        number = "09987654321"
        await self.assert_one_owner(number, [self.client.post("/api/auth/register", json=self.teacher(number, i))
                                            for i in (1, 2)], [201, 409])

    async def test_teacher_and_principal_registrations_compete_for_one_number(self):
        number = "09987654321"
        principal = {**self.teacher(number), "email": "principal@example.com",
                     "school": {"name": "New Test School", "city": "Lala"}}
        principal.pop("school_id")
        await self.assert_one_owner(number, [self.client.post("/api/auth/register", json=self.teacher(number)),
                                            self.client.post("/api/schools/register", json=principal)], [201, 409])

    async def test_profile_update_and_registration_compete_for_one_number(self):
        number = "09987654321"
        responses = await asyncio.wait_for(asyncio.gather(
            self.client.post("/api/auth/register", json=self.teacher(number)),
            self.client.patch("/api/staff/me", json={"contact_number": number}),
        ), timeout=15)
        statuses = [response.status_code for response in responses]
        self.assertIn(statuses, ([201, 409], [409, 200]), [response.text for response in responses])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Staff).where(Staff.contact_number == number)), 1)


if __name__ == "__main__":
    unittest.main()
