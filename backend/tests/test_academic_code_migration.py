"""Migration preservation checks on disposable legacy SQLite databases."""
from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
from migrate_academic_names import MigrationError, migrate_connection


class AcademicCodeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            for sql in (
                "CREATE TABLE schools (school_id INTEGER PRIMARY KEY)",
                "CREATE TABLE grade_levels (grade_level_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id))",
                "CREATE TABLE sections (section_id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), "
                "grade_level_id INTEGER NOT NULL REFERENCES grade_levels(grade_level_id), section_code VARCHAR(20) NOT NULL, "
                "name VARCHAR(40) NOT NULL, capacity INTEGER NOT NULL DEFAULT 40 CHECK(capacity > 0), room TEXT, "
                "note TEXT DEFAULT 'keep,a,b', CONSTRAINT uq_section_code_per_school UNIQUE (school_id, section_code))",
                "CREATE TABLE subjects (subject_id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), "
                "grade_level_id INTEGER NOT NULL REFERENCES grade_levels(grade_level_id), subject_code VARCHAR(50) NOT NULL, "
                "subject_name TEXT NOT NULL, total_paces INTEGER DEFAULT 12, CONSTRAINT uq_subject_code_per_school UNIQUE (school_id, subject_code))",
                "CREATE TABLE links (link_id INTEGER PRIMARY KEY, section_id INTEGER REFERENCES sections(section_id) ON DELETE RESTRICT, "
                "subject_id INTEGER REFERENCES subjects(subject_id) ON DELETE CASCADE, recorded_value TEXT)",
                "CREATE TABLE audit (identity INTEGER, label TEXT)",
                "CREATE INDEX ix_section_room_custom ON sections(room)",
                "CREATE INDEX ix_section_code_old ON sections(section_code)",
                "CREATE TRIGGER section_audit AFTER UPDATE ON sections BEGIN INSERT INTO audit VALUES (NEW.section_id, NEW.name); END",
                "INSERT INTO schools VALUES (1)",
                "INSERT INTO grade_levels VALUES (7, 1)",
                "INSERT INTO sections (section_id, school_id, grade_level_id, section_code, name, room) VALUES (10, 1, 7, 'G7-FAI', 'Faith', 'Room 1')",
                "INSERT INTO subjects VALUES (100, 1, 7, 'MATH7', 'Mathematics', 12)",
                "INSERT INTO links VALUES (5, 10, 100, 'original grade record')",
            ):
                conn.exec_driver_sql(sql)

    def _columns(self, table):
        with self.engine.connect() as conn:
            return {column["name"] for column in inspect(conn).get_columns(table)}

    def _apply(self):
        with self.engine.connect() as conn:
            return migrate_connection(conn, apply=True)

    def test_dry_run_leaves_schema_data_and_triggers_unchanged(self):
        with self.engine.connect() as conn:
            before = conn.exec_driver_sql("SELECT type, name, sql FROM sqlite_master ORDER BY name").all()
            plan = migrate_connection(conn)
            after = conn.exec_driver_sql("SELECT type, name, sql FROM sqlite_master ORDER BY name").all()
            self.assertEqual(before, after)
            self.assertTrue(all(item["remove_code"] for item in plan))
            self.assertEqual(conn.exec_driver_sql("SELECT * FROM links").one(), (5, 10, 100, "original grade record"))

    def test_apply_preserves_ids_values_links_indexes_triggers_and_foreign_keys(self):
        self._apply()
        self.assertNotIn("section_code", self._columns("sections"))
        self.assertNotIn("subject_code", self._columns("subjects"))
        with self.engine.begin() as conn:
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one(), 1)
            self.assertEqual(conn.exec_driver_sql("SELECT section_id, name, capacity, room, note FROM sections").one(), (10, "Faith", 40, "Room 1", "keep,a,b"))
            self.assertEqual(conn.exec_driver_sql("SELECT * FROM links").one(), (5, 10, 100, "original grade record"))
            indexes = {row[0] for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertIn("ix_section_room_custom", indexes)
            self.assertNotIn("ix_section_code_old", indexes)
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            conn.exec_driver_sql("UPDATE sections SET name='Hope' WHERE section_id=10")
            self.assertEqual(conn.exec_driver_sql("SELECT * FROM audit").one(), (10, "Hope"))
        with self.assertRaises(IntegrityError):
            with self.engine.begin() as conn:
                conn.exec_driver_sql("DELETE FROM sections WHERE section_id=10")

    def test_new_records_need_only_names_and_uniqueness_is_per_grade(self):
        self._apply()
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO grade_levels VALUES (8, 1)")
            conn.exec_driver_sql("INSERT INTO sections (section_id, school_id, grade_level_id, name) VALUES (11, 1, 8, 'Faith')")
            conn.exec_driver_sql("INSERT INTO subjects (subject_id, school_id, grade_level_id, subject_name) VALUES (101, 1, 8, 'Mathematics')")
        for sql in (
            "INSERT INTO sections (school_id, grade_level_id, name) VALUES (1, 7, 'Faith')",
            "INSERT INTO subjects (school_id, grade_level_id, subject_name) VALUES (1, 7, 'Mathematics')",
        ):
            with self.assertRaises(IntegrityError):
                with self.engine.begin() as conn:
                    conn.exec_driver_sql(sql)

    def test_normalized_duplicate_section_names_stop_before_any_schema_change(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO sections (section_id, school_id, grade_level_id, section_code, name) VALUES (11, 1, 7, 'OTHER', ' FAITH ')")
        with self.assertRaisesRegex(MigrationError, "Duplicate names in sections"):
            self._apply()
        self.assertIn("section_code", self._columns("sections"))
        self.assertIn("subject_code", self._columns("subjects"))

    def test_duplicate_subject_names_stop_before_any_schema_change(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO subjects VALUES (101, 1, 7, 'OTHER', ' mathematics ', 12)")
        with self.assertRaisesRegex(MigrationError, "Duplicate names in subjects"):
            self._apply()
        self.assertIn("section_code", self._columns("sections"))

    def test_failure_on_second_table_rolls_back_first_table_and_restores_fk_mode(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TRIGGER subject_code_audit AFTER UPDATE ON subjects BEGIN INSERT INTO audit VALUES (NEW.subject_id, NEW.subject_code); END")
        with self.assertRaisesRegex(MigrationError, "Trigger subject_code_audit"):
            self._apply()
        self.assertIn("section_code", self._columns("sections"))
        self.assertIn("subject_code", self._columns("subjects"))
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_keys").scalar_one(), 1)
            self.assertEqual(conn.exec_driver_sql("SELECT * FROM links").one(), (5, 10, 100, "original grade record"))
            self.assertFalse(inspect(conn).has_table("_trace_new_sections"))

    def test_migration_is_idempotent(self):
        self._apply()
        second = self._apply()
        self.assertTrue(all(not item["remove_code"] and not item["add_unique"] for item in second))
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM sections").scalar_one(), 1)
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM links").scalar_one(), 1)

    def test_schema_diagnostic_reports_the_required_migration(self):
        from app.db.schema_check import _compare

        with self.engine.connect() as conn:
            result = _compare(conn)
            self.assertEqual(result["obsolete_columns"], {"sections": ["section_code"], "subjects": ["subject_code"]})
        self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(_compare(conn)["obsolete_columns"], {})

    def test_missing_name_constraints_are_added_when_code_columns_are_already_absent(self):
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE sections (section_id INTEGER PRIMARY KEY, school_id INTEGER, grade_level_id INTEGER, name TEXT)")
            conn.exec_driver_sql("CREATE TABLE subjects (subject_id INTEGER PRIMARY KEY, school_id INTEGER, grade_level_id INTEGER, subject_name TEXT)")
        with engine.connect() as conn:
            migrate_connection(conn, apply=True)
            plan = migrate_connection(conn)
            self.assertTrue(all(not item["remove_code"] and not item["add_unique"] for item in plan))


if __name__ == "__main__":
    unittest.main()
