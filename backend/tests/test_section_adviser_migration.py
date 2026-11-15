"""Backfill advisers safely on isolated legacy schemas, with no grade writes."""
from pathlib import Path
import sys
import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from migrate_section_advisers import MigrationError, migrate_connection


class SectionAdviserMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            for sql in (
                "CREATE TABLE schools (school_id INTEGER PRIMARY KEY)",
                "CREATE TABLE staff (id CHAR(36) PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), role TEXT)",
                "CREATE TABLE sections (section_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id))",
                "CREATE TABLE teacher_assignments (assignment_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), "
                "teacher_id CHAR(36) REFERENCES staff(id), section_id INTEGER REFERENCES sections(section_id), subject_id INTEGER, is_adviser BOOLEAN)",
                "CREATE TABLE grades (id INTEGER PRIMARY KEY, section_id INTEGER REFERENCES sections(section_id), score TEXT)",
                "INSERT INTO schools VALUES (1), (2)",
                "INSERT INTO staff VALUES ('teacher-a', 1, 'teacher'), ('teacher-b', 1, 'teacher'), ('foreign', 2, 'teacher')",
                "INSERT INTO sections VALUES (10, 1), (20, 2)",
                "INSERT INTO teacher_assignments VALUES (1, 1, 'teacher-a', 10, NULL, 1), (2, 1, 'teacher-a', 10, 100, 1)",
                "INSERT INTO grades VALUES (1, 10, '[7, 9, 0, null]')",
            ):
                conn.exec_driver_sql(sql)

    def _apply(self):
        with self.engine.connect() as conn:
            return migrate_connection(conn, apply=True)

    def test_dry_run_does_not_change_any_schema_or_rows(self):
        with self.engine.connect() as conn:
            before = conn.exec_driver_sql("SELECT type, name, sql FROM sqlite_master ORDER BY name").all()
            plan = migrate_connection(conn)
            self.assertTrue(plan["create_table"])
            self.assertEqual(len(plan["copy"]), 1)
            self.assertEqual(plan["legacy_subject_review_count"], 1)
            self.assertEqual(conn.exec_driver_sql("SELECT type, name, sql FROM sqlite_master ORDER BY name").all(), before)

    def test_backfill_is_idempotent_preserves_original_assignments_and_scores(self):
        self._apply()
        plan = self._apply()
        self.assertEqual(plan["copy"], [])
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT school_id, section_id, teacher_id FROM section_advisers").all(), [(1, 10, "teacher-a")])
            self.assertEqual(conn.exec_driver_sql("SELECT * FROM teacher_assignments").all(), [(1, 1, "teacher-a", 10, None, 1), (2, 1, "teacher-a", 10, 100, 1)])
            self.assertEqual(conn.exec_driver_sql("SELECT * FROM grades").one(), (1, 10, "[7, 9, 0, null]"))
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.exec_driver_sql("INSERT INTO section_advisers (school_id, section_id, teacher_id) VALUES (1, 10, 'teacher-b')")

    def test_existing_explicit_adviser_is_never_overwritten(self):
        self._apply()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("UPDATE section_advisers SET teacher_id='teacher-b'")
        plan = self._apply()
        self.assertTrue(plan["preserved"][0]["differs_from_legacy"])
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT teacher_id FROM section_advisers").scalar_one(), "teacher-b")

    def test_rerun_does_not_resurrect_deliberately_removed_adviser(self):
        self._apply()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM section_advisers")
        plan = self._apply()
        self.assertTrue(plan["backfill_completed"])
        self.assertEqual(plan["copy"], [])
        self.assertEqual(plan["skipped_legacy_sections"], [{"school_id": 1, "section_id": 10}])
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM section_advisers").scalar_one(), 0)

    def test_conflicting_legacy_advisers_abort_without_creating_table_or_losing_rows(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO teacher_assignments VALUES (3, 1, 'teacher-b', 10, NULL, 1)")
        with self.engine.connect() as conn:
            self.assertEqual(len(migrate_connection(conn)["conflicts"]), 1)
        with self.assertRaises(MigrationError):
            self._apply()
        with self.engine.connect() as conn:
            self.assertFalse(inspect(conn).has_table("section_advisers"))
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM teacher_assignments").scalar_one(), 3)

    def test_invalid_cross_school_legacy_adviser_aborts(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO teacher_assignments VALUES (3, 1, 'foreign', 10, NULL, 1)")
        with self.engine.connect() as conn:
            self.assertEqual(migrate_connection(conn)["invalid_assignment_ids"], [3])
        with self.assertRaises(MigrationError):
            self._apply()

    def test_new_empty_database_structure_gets_empty_adviser_table(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("DELETE FROM teacher_assignments")
        self.assertEqual(self._apply()["copy"], [])
        with self.engine.connect() as conn:
            self.assertTrue(inspect(conn).has_table("section_advisers"))
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM section_advisers").scalar_one(), 0)
