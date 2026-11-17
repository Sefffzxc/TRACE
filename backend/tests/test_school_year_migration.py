"""Year migration on isolated legacy schemas; production data is never used."""
from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest
from uuid import uuid4

from sqlalchemy import Integer, MetaData, create_engine, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from migrate_school_year_scope import MARKER, MigrationError, SPECS, migrate_connection


TEST_POSTGRES_URL = os.environ.get("TRACE_TEST_POSTGRES_URL")


class SchoolYearMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
            for sql in (
                "CREATE TABLE schools (school_id INTEGER PRIMARY KEY, name TEXT)",
                "CREATE TABLE school_years (school_year_id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), year TEXT NOT NULL, is_current BOOLEAN NOT NULL)",
                "CREATE TABLE staff (id TEXT PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), role TEXT, email TEXT)",
                "CREATE TABLE sections (section_id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), grade_level_id INTEGER NOT NULL, name TEXT)",
                "CREATE TABLE subjects (subject_id INTEGER PRIMARY KEY, school_id INTEGER NOT NULL REFERENCES schools(school_id), grade_level_id INTEGER NOT NULL, subject_name TEXT)",
                "CREATE TABLE students (student_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), first_name TEXT, guardian_contact TEXT)",
                "CREATE TABLE student_enrollments (enrollment_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), student_id INTEGER REFERENCES students(student_id), section_id INTEGER REFERENCES sections(section_id), grade_level_id INTEGER, school_year_id INTEGER REFERENCES school_years(school_year_id), is_active BOOLEAN)",
                "CREATE TABLE student_paces (pace_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), student_id INTEGER REFERENCES students(student_id), enrollment_id INTEGER REFERENCES student_enrollments(enrollment_id), subject_id INTEGER REFERENCES subjects(subject_id), subject TEXT, grading_period INTEGER, ww_scores TEXT, transmuted_grade REAL)",
                "CREATE TABLE teacher_assignments (assignment_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), teacher_id TEXT REFERENCES staff(id), section_id INTEGER REFERENCES sections(section_id), subject_id INTEGER REFERENCES subjects(subject_id), is_adviser BOOLEAN, CONSTRAINT uq_teacher_section_subject UNIQUE(teacher_id, section_id, subject_id))",
                "CREATE TABLE section_advisers (adviser_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), section_id INTEGER REFERENCES sections(section_id), teacher_id TEXT REFERENCES staff(id), CONSTRAINT uq_section_adviser_per_school UNIQUE(school_id, section_id))",
                "CREATE TABLE class_record_setups (setup_id INTEGER PRIMARY KEY, school_id INTEGER REFERENCES schools(school_id), section_id INTEGER REFERENCES sections(section_id), subject_id INTEGER REFERENCES subjects(subject_id), grading_period INTEGER, ww_highest TEXT, template TEXT, CONSTRAINT uq_class_record_setup UNIQUE(section_id, subject_id, grading_period))",
                "CREATE INDEX ix_legacy_assignment_teacher ON teacher_assignments(teacher_id)",
                "INSERT INTO schools VALUES (1, 'Alpha School'), (2, 'Beta School')",
                "INSERT INTO school_years VALUES (1,1,'2026-2027',0),(2,1,'2025-2026',0),(3,1,'2027-2028',1),(4,2,'2026-2027',1)",
                "INSERT INTO staff VALUES ('teacher-a',1,'teacher','a@example.com'),('teacher-b',1,'teacher','b@example.com'),('foreign',2,'teacher','f@example.com')",
                "INSERT INTO sections VALUES (10,1,1,'Faith'),(11,1,1,'Hope'),(20,2,2,'Other')",
                "INSERT INTO subjects VALUES (100,1,1,'English'),(101,1,1,'Science'),(200,2,2,'Other English')",
                "INSERT INTO students VALUES (1,1,'Learner One','09170000000'),(2,2,'Learner Two','09171111111')",
                "INSERT INTO student_enrollments VALUES (1,1,1,10,1,1,1),(2,1,1,10,1,2,1),(3,2,2,20,2,4,1)",
                "INSERT INTO student_paces VALUES (1,1,1,1,100,'English',1,'[7,9,0,null]',82.5),(2,1,1,2,NULL,'English',1,'[8,8]',91.0),(3,2,2,3,200,'Other English',1,'[2,4]',80.0)",
                "INSERT INTO teacher_assignments VALUES (1,1,'teacher-a',10,100,0),(2,1,'teacher-b',11,NULL,1),(3,2,'foreign',20,200,0)",
                "INSERT INTO section_advisers VALUES (1,1,10,'teacher-a'),(2,2,20,'foreign')",
                "INSERT INTO class_record_setups VALUES (1,1,10,100,1,'[10,10]','deped'),(2,1,11,101,1,'[20]','deped'),(3,2,20,200,1,'[5,5]','deped')",
            ):
                conn.exec_driver_sql(sql)

    def _snapshot(self):
        with self.engine.connect() as conn:
            schema = conn.exec_driver_sql("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").all()
            rows = {name: conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1').all()
                    for name in inspect(conn).get_table_names()}
            return schema, rows

    def _apply(self, **kwargs):
        with self.engine.connect() as conn:
            return migrate_connection(conn, apply=True, **kwargs)

    def test_dry_run_reports_proposed_legacy_year_without_mutating_schema_or_rows(self):
        before = self._snapshot()
        with self.engine.connect() as conn:
            plan = migrate_connection(conn)
        self.assertEqual(plan["errors"], [])
        self.assertEqual(plan["confirmation_required_for_schools"], [1, 2])
        self.assertEqual(plan["tables"]["class_record_setups"]["header_copies"], 1)
        self.assertTrue(plan["tables"]["teacher_assignments"]["add_year_column"])
        self.assertEqual(self._snapshot(), before)

    def test_apply_requires_explicit_legacy_year_even_when_current_exists(self):
        before = self._snapshot()
        with self.assertRaisesRegex(MigrationError, "Confirm the legacy assignment year"):
            self._apply()
        self.assertEqual(self._snapshot(), before)

    def test_backfill_preserves_scores_profiles_year_flags_and_original_assignment_identity(self):
        before = self._snapshot()[1]
        self._apply(legacy_year="2026-2027")
        after = self._snapshot()[1]
        for name in ("schools", "school_years", "students", "staff", "student_enrollments", "student_paces", "sections", "subjects"):
            self.assertEqual(after[name], before[name], name)
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT assignment_id,school_id,teacher_id,section_id,subject_id,is_adviser FROM teacher_assignments ORDER BY assignment_id").all(), before["teacher_assignments"])
            self.assertEqual(conn.exec_driver_sql("SELECT assignment_id,school_year_id FROM teacher_assignments ORDER BY assignment_id").all(), [(1,1),(2,1),(3,4)])
            self.assertEqual(conn.exec_driver_sql("SELECT adviser_id,school_year_id FROM section_advisers ORDER BY adviser_id").all(), [(1,1),(2,4)])
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])
            indexes = inspect(conn).get_indexes("teacher_assignments")
            self.assertIn("ix_legacy_assignment_teacher", {item["name"] for item in indexes})

    def test_headers_copy_only_to_existing_score_years_never_empty_future_year(self):
        self._apply(legacy_year="2026-2027")
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql("SELECT school_id,section_id,subject_id,grading_period,school_year_id,ww_highest,template FROM class_record_setups ORDER BY school_id,section_id,school_year_id").all()
            self.assertEqual(rows, [
                (1,10,100,1,1,"[10,10]","deped"),(1,10,100,1,2,"[10,10]","deped"),
                (1,11,101,1,1,"[20]","deped"),(2,20,200,1,4,"[5,5]","deped"),
            ])
            self.assertEqual(conn.exec_driver_sql("SELECT count(*) FROM class_record_setups WHERE school_year_id=3").scalar_one(), 0)

    def test_sqlite_rebuild_preserves_existing_table_triggers(self):
        sql = ("CREATE TRIGGER keep_assignment_audit AFTER INSERT ON teacher_assignments "
               "BEGIN INSERT INTO assignment_audit(assignment_id) VALUES (NEW.assignment_id); END")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE assignment_audit (assignment_id INTEGER)")
            conn.exec_driver_sql(sql)
        self._apply(legacy_year="2026-2027")
        with self.engine.begin() as conn:
            self.assertEqual(conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE name='keep_assignment_audit'").scalar_one(), sql)
            conn.exec_driver_sql("INSERT INTO teacher_assignments(assignment_id,school_id,teacher_id,section_id,subject_id,is_adviser,school_year_id) VALUES (12,1,'teacher-a',10,100,0,3)")
            self.assertEqual(conn.exec_driver_sql("SELECT assignment_id FROM assignment_audit").all(), [(12,)])

    def test_same_section_teacher_subject_and_adviser_are_allowed_in_a_new_year(self):
        self._apply(legacy_year="2026-2027")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO teacher_assignments(assignment_id,school_id,teacher_id,section_id,subject_id,is_adviser,school_year_id) VALUES (10,1,'teacher-a',10,100,0,3)")
            conn.exec_driver_sql("INSERT INTO section_advisers(adviser_id,school_id,section_id,teacher_id,school_year_id) VALUES (10,1,10,'teacher-b',3)")
            conn.exec_driver_sql("INSERT INTO class_record_setups(setup_id,school_id,section_id,subject_id,grading_period,school_year_id) VALUES (10,1,10,100,1,3)")
        for sql in (
            "INSERT INTO teacher_assignments(school_id,teacher_id,section_id,subject_id,is_adviser,school_year_id) VALUES (1,'teacher-a',10,100,0,3)",
            "INSERT INTO section_advisers(school_id,section_id,teacher_id,school_year_id) VALUES (1,10,'teacher-a',3)",
            "INSERT INTO class_record_setups(school_id,section_id,subject_id,grading_period,school_year_id) VALUES (1,10,100,1,3)",
        ):
            with self.assertRaises(IntegrityError):
                with self.engine.begin() as conn:
                    conn.exec_driver_sql(sql)

    def test_year_not_null_and_foreign_keys_are_enforced_on_every_new_table(self):
        self._apply(legacy_year="2026-2027")
        for name, spec in SPECS.items():
            with self.engine.connect() as conn:
                columns = {col["name"]: col for col in inspect(conn).get_columns(name)}
                self.assertFalse(columns["school_year_id"]["nullable"], name)
                year_fks = [fk for fk in inspect(conn).get_foreign_keys(name) if fk["constrained_columns"] == ["school_year_id"]]
                self.assertEqual(year_fks[0]["referred_table"], "school_years")
            for value in ("NULL", "999"):
                with self.assertRaises(IntegrityError):
                    with self.engine.begin() as conn:
                        conn.exec_driver_sql(f'UPDATE "{name}" SET school_year_id={value} WHERE "{spec["pk"]}"=1')

    def test_idempotent_rerun_does_not_reassign_records_after_current_year_changes(self):
        self._apply(legacy_year="2026-2027")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("UPDATE school_years SET is_current=0 WHERE school_id=1")
            conn.exec_driver_sql("UPDATE school_years SET is_current=1 WHERE school_year_id=2")
        before = self._snapshot()
        plan = self._apply()
        self.assertTrue(plan["already_applied"])
        self.assertEqual(self._snapshot(), before)
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT migration_name FROM trace_schema_migrations").scalar_one(), MARKER)

    def test_override_maps_each_school_explicitly_and_invalid_targets_roll_back(self):
        for kwargs in ({"legacy_year": "2099-2100"}, {"school_years": {1:"2027-2028", 2:"missing"}}, {"legacy_year":"2026-2027", "school_years":{99:"2026-2027"}}):
            before = self._snapshot()
            with self.assertRaises(MigrationError):
                self._apply(**kwargs)
            self.assertEqual(self._snapshot(), before)
        self._apply(legacy_year="2026-2027", school_years={1:"2025-2026"})
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT school_year_id FROM teacher_assignments WHERE school_id=1").all(), [(2,),(2,)])

    def test_cross_school_teacher_section_subject_or_existing_year_aborts_without_mutation(self):
        for table, change, restore in (
            ("teacher_assignments", "teacher_id='foreign'", "teacher_id='teacher-a'"),
            ("teacher_assignments", "section_id=20", "section_id=10"),
            ("class_record_setups", "subject_id=200", "subject_id=100"),
        ):
            pk = SPECS[table]["pk"]
            with self.engine.begin() as conn:
                conn.exec_driver_sql(f'UPDATE "{table}" SET {change} WHERE {pk}=1')
            before = self._snapshot()
            with self.assertRaises(MigrationError):
                self._apply(legacy_year="2026-2027")
            self.assertEqual(self._snapshot(), before)
            with self.engine.begin() as conn:
                conn.exec_driver_sql(f'UPDATE "{table}" SET {restore} WHERE {pk}=1')
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE teacher_assignments ADD COLUMN school_year_id INTEGER")
            conn.exec_driver_sql("UPDATE teacher_assignments SET school_year_id=4 WHERE assignment_id=1")
        before = self._snapshot()
        with self.assertRaises(MigrationError):
            self._apply(legacy_year="2026-2027")
        self.assertEqual(self._snapshot(), before)

    def test_conflicting_preexisting_and_backfilled_headers_abort_atomically(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE class_record_setups RENAME TO old_headers")
            conn.exec_driver_sql("CREATE TABLE class_record_setups (setup_id INTEGER PRIMARY KEY,school_id INTEGER,section_id INTEGER,subject_id INTEGER,grading_period INTEGER,ww_highest TEXT,template TEXT,school_year_id INTEGER)")
            conn.exec_driver_sql("INSERT INTO class_record_setups SELECT *,NULL FROM old_headers")
            conn.exec_driver_sql("DROP TABLE old_headers")
            conn.exec_driver_sql("INSERT INTO class_record_setups VALUES (9,1,10,100,1,'[99]','deped',1)")
        before = self._snapshot()
        with self.assertRaisesRegex(MigrationError, "Conflicting class_record_setups"):
            self._apply(legacy_year="2026-2027")
        self.assertEqual(self._snapshot(), before)

    def test_partial_schema_existing_year_column_is_finished_with_foreign_key(self):
        with self.engine.begin() as conn:
            for table in SPECS:
                conn.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN school_year_id INTEGER')
        self._apply(legacy_year="2026-2027")
        with self.engine.connect() as conn:
            for table in SPECS:
                year_fks = [fk for fk in inspect(conn).get_foreign_keys(table) if fk["constrained_columns"] == ["school_year_id"]]
                self.assertEqual(len(year_fks), 1, table)
                self.assertEqual(year_fks[0]["referred_table"], "school_years", table)

    def test_completed_marker_with_schema_drift_is_rejected_without_mutation(self):
        self._apply(legacy_year="2026-2027")
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE UNIQUE INDEX accidental_old_assignment_key ON teacher_assignments(teacher_id,section_id,subject_id)")
        before = self._snapshot()
        with self.assertRaisesRegex(MigrationError, "marked complete"):
            self._apply()
        self.assertEqual(self._snapshot(), before)

    def test_wrong_existing_year_foreign_key_is_rejected_without_mutation(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("ALTER TABLE teacher_assignments ADD COLUMN school_year_id INTEGER REFERENCES schools(school_id)")
        before = self._snapshot()
        with self.assertRaisesRegex(MigrationError, "unexpected school-year foreign key"):
            self._apply(legacy_year="2026-2027")
        self.assertEqual(self._snapshot(), before)

    def test_unexpected_incoming_reference_rolls_back_added_columns_and_all_rows(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE custom_assignment_links(id INTEGER PRIMARY KEY,assignment_id INTEGER REFERENCES teacher_assignments(assignment_id))")
            conn.exec_driver_sql("INSERT INTO custom_assignment_links VALUES (1,1)")
        before = self._snapshot()
        with self.assertRaisesRegex(MigrationError, "Unexpected incoming reference"):
            self._apply(legacy_year="2026-2027")
        self.assertEqual(self._snapshot(), before)

    def test_cross_school_score_enrollment_blocks_apply_without_partial_schema_changes(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("UPDATE student_paces SET enrollment_id=3 WHERE pace_id=1")
        before = self._snapshot()
        with self.assertRaisesRegex(MigrationError, "invalid enrolment"):
            self._apply(legacy_year="2026-2027")
        self.assertEqual(self._snapshot(), before)

    def test_empty_academic_tables_can_be_prepared_without_inventing_legacy_records(self):
        with self.engine.begin() as conn:
            for table in ("student_paces", "student_enrollments", "class_record_setups", "section_advisers", "teacher_assignments"):
                conn.exec_driver_sql(f'DELETE FROM "{table}"')
        before = self._snapshot()[1]
        plan = self._apply()
        self.assertEqual(plan["legacy_years"], [])
        after = self._snapshot()[1]
        for table, rows in before.items():
            self.assertEqual(after[table], rows)
        self.assertTrue(self._apply()["already_applied"])


@unittest.skipUnless(TEST_POSTGRES_URL, "Set TRACE_TEST_POSTGRES_URL to run isolated local PostgreSQL checks.")
class SchoolYearPostgresMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if make_url(TEST_POSTGRES_URL).host not in {"localhost", "127.0.0.1", "::1"}:
            self.fail("Migration tests only run against an explicitly configured loopback test database.")
        self.engine = create_async_engine(TEST_POSTGRES_URL)
        self.schema = "trace_year_migration_test_" + uuid4().hex
        self.conn = await self.engine.connect()
        await self.conn.exec_driver_sql(f'CREATE SCHEMA "{self.schema}"')
        await self.conn.exec_driver_sql(f'SET search_path TO "{self.schema}"')
        # Use the same explicit pre-migration fixture on both engines. SQLAlchemy
        # emits PostgreSQL-native SERIAL, foreign-key and unique definitions.
        template = SchoolYearMigrationTests("test_dry_run_reports_proposed_legacy_year_without_mutating_schema_or_rows")
        template.setUp()
        try:
            metadata = MetaData()
            with template.engine.connect() as source:
                metadata.reflect(source)
                content = {t.name: list(source.execute(select(t)).mappings()) for t in metadata.sorted_tables}
            await self.conn.run_sync(metadata.create_all)
            for table in metadata.sorted_tables:
                if content[table.name]:
                    await self.conn.execute(table.insert(), [dict(row) for row in content[table.name]])
                keys = list(table.primary_key.columns)
                if len(keys) == 1 and isinstance(keys[0].type, Integer):
                    maximum = max((row[keys[0].name] for row in content[table.name]), default=None)
                    if maximum is not None:
                        await self.conn.exec_driver_sql(
                            "SELECT setval(pg_get_serial_sequence($1, $2), $3)",
                            (table.name, keys[0].name, maximum))
            await self.conn.commit()
        finally:
            template.doCleanups()

    async def asyncTearDown(self):
        try:
            await self.conn.rollback()
            if not self.schema.startswith("trace_year_migration_test_") or len(self.schema.rsplit("_", 1)[-1]) != 32:
                raise RuntimeError("Unexpected disposable test schema name.")
            await self.conn.exec_driver_sql(f'DROP SCHEMA "{self.schema}" CASCADE')
            await self.conn.commit()
        finally:
            await self.conn.close()
            await self.engine.dispose()

    async def _apply(self, **kwargs):
        await self.conn.commit()
        return await self.conn.run_sync(lambda conn: migrate_connection(conn, apply=True, **kwargs))

    async def _snapshot(self):
        names = await self.conn.run_sync(lambda conn: inspect(conn).get_table_names())
        return {name: (await self.conn.exec_driver_sql(f'SELECT * FROM "{name}" ORDER BY 1')).all() for name in names}

    async def _columns(self, name):
        return await self.conn.run_sync(lambda conn: {c["name"]: c for c in inspect(conn).get_columns(name)})

    async def test_apply_preserves_data_and_only_copies_headers_into_existing_score_years(self):
        before = await self._snapshot()
        plan = await self.conn.run_sync(migrate_connection)
        self.assertEqual(plan["tables"]["class_record_setups"]["header_copies"], 1)
        self.assertNotIn("school_year_id", await self._columns("teacher_assignments"))
        await self._apply(legacy_year="2026-2027")
        after = await self._snapshot()
        for name in ("students", "student_enrollments", "student_paces", "school_years", "staff", "sections", "subjects"):
            self.assertEqual(after[name], before[name], name)
        self.assertEqual((await self.conn.exec_driver_sql("SELECT assignment_id,school_year_id FROM teacher_assignments ORDER BY assignment_id")).all(), [(1,1),(2,1),(3,4)])
        self.assertEqual((await self.conn.exec_driver_sql("SELECT school_year_id FROM class_record_setups WHERE section_id=10 ORDER BY school_year_id")).all(), [(1,),(2,)])
        self.assertEqual((await self.conn.exec_driver_sql("SELECT count(*) FROM class_record_setups WHERE school_year_id=3")).scalar_one(), 0)
        snapshot = await self._snapshot()
        self.assertTrue((await self._apply())["already_applied"])
        self.assertEqual(await self._snapshot(), snapshot)

    async def test_new_year_uniqueness_not_null_and_foreign_key_guards(self):
        await self._apply(legacy_year="2026-2027")
        for name in SPECS:
            self.assertFalse((await self._columns(name))["school_year_id"]["nullable"])
        await self.conn.exec_driver_sql("INSERT INTO teacher_assignments(school_id,teacher_id,section_id,subject_id,is_adviser,school_year_id) VALUES (1,'teacher-a',10,100,false,3)")
        await self.conn.exec_driver_sql("INSERT INTO section_advisers(school_id,section_id,teacher_id,school_year_id) VALUES (1,10,'teacher-b',3)")
        await self.conn.exec_driver_sql("INSERT INTO class_record_setups(school_id,section_id,subject_id,grading_period,school_year_id) VALUES (1,10,100,1,3)")
        await self.conn.commit()
        for name, spec in SPECS.items():
            for value in ("NULL", "999"):
                with self.assertRaises(IntegrityError):
                    await self.conn.exec_driver_sql(f'UPDATE "{name}" SET school_year_id={value} WHERE "{spec["pk"]}"=1')
                await self.conn.rollback()
        with self.assertRaises(IntegrityError):
            await self.conn.exec_driver_sql("INSERT INTO section_advisers(school_id,section_id,teacher_id,school_year_id) VALUES (1,10,'teacher-a',3)")
        await self.conn.rollback()

    async def test_header_copy_database_failure_rolls_back_schema_rows_and_old_constraints(self):
        await self.conn.exec_driver_sql("CREATE FUNCTION reject_header_copy() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'intentional migration test failure'; END;$$")
        await self.conn.exec_driver_sql("CREATE TRIGGER reject_header_copy BEFORE INSERT ON class_record_setups FOR EACH ROW EXECUTE FUNCTION reject_header_copy()")
        await self.conn.commit()
        before = await self._snapshot()
        with self.assertRaises(SQLAlchemyError):
            await self._apply(legacy_year="2026-2027")
        for name in SPECS:
            self.assertNotIn("school_year_id", await self._columns(name))
        self.assertEqual(await self._snapshot(), before)
        constraints = await self.conn.run_sync(lambda conn: inspect(conn).get_unique_constraints("teacher_assignments"))
        self.assertIn(("teacher_id", "section_id", "subject_id"), [tuple(c["column_names"]) for c in constraints])

    async def test_invalid_legacy_mapping_refuses_before_any_ddl(self):
        before = await self._snapshot()
        with self.assertRaises(MigrationError):
            await self._apply(legacy_year="2099-2100")
        self.assertEqual(await self._snapshot(), before)
        self.assertNotIn("school_year_id", await self._columns("teacher_assignments"))


if __name__ == "__main__":
    unittest.main()
