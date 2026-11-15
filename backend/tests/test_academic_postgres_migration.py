"""Optional PostgreSQL integration checks; only an explicit loopback test DB.

Set TRACE_TEST_POSTGRES_URL to a disposable local PostgreSQL asyncpg URL.
Each test uses and removes its own uniquely named schema.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from migrate_academic_names import MigrationError, migrate_connection


TEST_URL = os.environ.get("TRACE_TEST_POSTGRES_URL")


@unittest.skipUnless(TEST_URL, "Set TRACE_TEST_POSTGRES_URL to run local PostgreSQL integration checks.")
class AcademicPostgresMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(TEST_URL).host not in {"localhost", "127.0.0.1", "::1"}:
            self.fail("These tests only run against an explicitly configured loopback database.")
        self.engine = create_async_engine(TEST_URL)
        self.schema = "trace_academic_test_" + uuid4().hex
        self.conn = await self.engine.connect()
        await self.conn.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
        await self.conn.exec_driver_sql(f'SET search_path TO "{self.schema}"')
        for sql in (
            "CREATE TABLE schools (school_id INTEGER PRIMARY KEY)",
            "CREATE TABLE grade_levels (grade_level_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id))",
            "CREATE TABLE sections (section_id SERIAL PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), "
            "grade_level_id INTEGER NOT NULL REFERENCES grade_levels(grade_level_id), section_code VARCHAR(20) NOT NULL, "
            "name VARCHAR(40) NOT NULL, room TEXT, capacity INTEGER DEFAULT 40, UNIQUE(school_id, section_code))",
            "CREATE TABLE subjects (subject_id SERIAL PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), "
            "grade_level_id INTEGER NOT NULL REFERENCES grade_levels(grade_level_id), subject_code VARCHAR(50) NOT NULL, "
            "subject_name TEXT NOT NULL, total_paces INTEGER DEFAULT 12, UNIQUE(school_id, subject_code))",
            "CREATE TABLE links (identity INTEGER PRIMARY KEY, section_id INTEGER REFERENCES sections(section_id) ON DELETE RESTRICT, "
            "subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, recorded_value TEXT)",
            "INSERT INTO schools VALUES (1)",
            "INSERT INTO grade_levels VALUES (7, 1)",
            "INSERT INTO sections (section_id, school_id, grade_level_id, section_code, name, room) VALUES (10, 1, 7, 'G7-FAI', 'Faith', 'Room 1')",
            "INSERT INTO subjects VALUES (100, 1, 7, 'MATH7', 'Mathematics', 12)",
            "SELECT setval(pg_get_serial_sequence('sections', 'section_id'), 10)",
            "SELECT setval(pg_get_serial_sequence('subjects', 'subject_id'), 100)",
            "INSERT INTO links VALUES (5, 10, 100, 'original grade record')",
        ):
            await self.conn.exec_driver_sql(sql)
        await self.conn.commit()

    async def asyncTearDown(self):
        try:
            await self.conn.rollback()
            # Only the randomly named schema created by this test is removed.
            if not self.schema.startswith("trace_academic_test_") or len(self.schema) != 52:
                raise RuntimeError("Unexpected test schema name.")
            await self.conn.exec_driver_sql(f'DROP SCHEMA "{self.schema}" CASCADE')
            await self.conn.commit()
        finally:
            await self.conn.close()
            await self.engine.dispose()

    async def _apply(self):
        await self.conn.commit()
        return await self.conn.run_sync(lambda conn: migrate_connection(conn, apply=True))

    async def _columns(self, table):
        return await self.conn.run_sync(lambda conn: {column["name"] for column in inspect(conn).get_columns(table)})

    async def test_apply_preserves_links_sequences_and_names_without_codes(self):
        await self._apply()
        self.assertNotIn("section_code", await self._columns("sections"))
        self.assertNotIn("subject_code", await self._columns("subjects"))
        self.assertEqual((await self.conn.exec_driver_sql("SELECT * FROM links")).one(), (5, 10, 100, "original grade record"))
        self.assertEqual((await self.conn.exec_driver_sql("SELECT room FROM sections WHERE section_id=10")).scalar_one(), "Room 1")
        await self.conn.exec_driver_sql("INSERT INTO grade_levels VALUES (8, 1)")
        identity = (await self.conn.exec_driver_sql(
            "INSERT INTO sections (school_id, grade_level_id, name) VALUES (1, 8, 'Faith') RETURNING section_id"
        )).scalar_one()
        self.assertEqual(identity, 11)
        await self.conn.commit()
        with self.assertRaises(SQLAlchemyError):
            await self.conn.exec_driver_sql("INSERT INTO sections (school_id, grade_level_id, name) VALUES (1, 7, 'Faith')")
        await self.conn.rollback()

    async def test_dry_run_and_idempotence(self):
        plan = await self.conn.run_sync(migrate_connection)
        self.assertTrue(all(item["remove_code"] for item in plan))
        self.assertIn("section_code", await self._columns("sections"))
        await self._apply()
        second = await self._apply()
        self.assertTrue(all(not item["remove_code"] and not item["add_unique"] for item in second))

    async def test_duplicate_names_prevent_all_changes(self):
        await self.conn.exec_driver_sql("INSERT INTO subjects VALUES (101, 1, 7, 'OTHER', ' mathematics ', 12)")
        with self.assertRaisesRegex(MigrationError, "Duplicate names in subjects"):
            await self._apply()
        self.assertIn("section_code", await self._columns("sections"))
        self.assertIn("subject_code", await self._columns("subjects"))

    async def test_dependent_view_failure_rolls_back_both_tables(self):
        await self.conn.exec_driver_sql("CREATE VIEW code_view AS SELECT subject_code FROM subjects")
        with self.assertRaises(SQLAlchemyError):
            await self._apply()
        self.assertIn("section_code", await self._columns("sections"))
        self.assertIn("subject_code", await self._columns("subjects"))
        self.assertEqual((await self.conn.exec_driver_sql("SELECT COUNT(*) FROM links")).scalar_one(), 1)


if __name__ == "__main__":
    unittest.main()
