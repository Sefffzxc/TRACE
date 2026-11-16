"""Test destructive school cleanup on isolated copies of the real schema."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # Registers the full current schema, including retired tables.
from app.db.session import Base
from app.models.staff import Staff
from cleanup_test_schools import CleanupError, cleanup_connection, list_schools


TEST_IDS = [10, 11, 12, 13]
ALL_IDS = TEST_IDS + [99]


class TestSchoolCleanupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="trace-school-cleanup-")
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "isolated.db"
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(self.engine)
        self.people = {school: (uuid4(), uuid4()) for school in ALL_IDS}
        self.operator_id = uuid4()
        self.kept_event = uuid4()
        self.kept_audit = uuid4()
        self.operator_event = uuid4()
        self._seed()

    def _insert(self, conn, table, **values):
        conn.execute(Base.metadata.tables[table].insert().values(**values))

    def _seed(self):
        expires = datetime.now(timezone.utc) + timedelta(minutes=5)
        with self.engine.begin() as conn:
            for sid in ALL_IDS:
                name = {
                    10: "Gumagamot Elementary School", 11: "Test Academy",
                    12: "Gumagamot Elementary School", 13: "test", 99: "Real School",
                }[sid]
                school_status = {10: "rejected", 12: "deactivated", 13: "deactivated"}.get(sid, "approved")
                self._insert(conn, "schools", school_id=sid, name=name, city="Lala", status=school_status)
            for sid in ALL_IDS + [None]:
                identities = self.people[sid] if sid else (self.operator_id,)
                for index, identity in enumerate(identities):
                    self._insert(
                        conn, "staff", id=identity, school_id=sid,
                        email=f"{'principal' if index == 0 else 'teacher'}-{sid}@example.com",
                        password_hash="unused-test-hash", first_name="Test", last_name="Account",
                        contact_number="09123456789", role="super_admin" if sid is None else ("admin" if index == 0 else "teacher"),
                        account_status="approved", is_approved=True,
                    )
                    device = uuid4()
                    self._insert(conn, "staff_devices", id=device, staff_id=identity, device_id=str(device))
                    self._insert(conn, "sessions", staff_id=identity, device_id=device,
                                 access_token="test-access", refresh_token=str(identity), expires_at=expires)
                    self._insert(conn, "otp_codes", staff_id=identity, code="123456", purpose="login", expires_at=expires)
                    self._insert(conn, "password_resets", staff_id=identity, reset_token=str(identity))
                    self._insert(conn, "resubmission_challenges", staff_id=identity,
                                 code_digest="a" * 64, state_digest="b" * 64, expires_at=expires)
            for sid in ALL_IDS:
                principal, teacher = self.people[sid]
                self._insert(conn, "grade_levels", grade_level_id=sid, school_id=sid, level="1", name="Grade 1")
                self._insert(conn, "sections", section_id=sid, school_id=sid, grade_level_id=sid, name="Lily")
                self._insert(conn, "subjects", subject_id=sid, school_id=sid, grade_level_id=sid, subject_name="Math")
                self._insert(conn, "school_years", school_year_id=sid, school_id=sid, year="2026-2027",
                             start_date=date(2026, 6, 1), end_date=date(2027, 3, 31))
                self._insert(conn, "teacher_assignments", school_id=sid, teacher_id=teacher, section_id=sid, subject_id=sid)
                self._insert(conn, "section_advisers", school_id=sid, teacher_id=teacher, section_id=sid)
                self._insert(conn, "class_schedules", school_id=sid, school_year_id=sid, section_id=sid,
                             subject_id=sid, teacher_id=teacher, day_of_week="Monday", start_time="08:00", end_time="09:00")
                self._insert(conn, "teacher_availabilities", school_id=sid, teacher_id=teacher,
                             day="Monday", start_time="09:00", end_time="10:00")
                self._insert(conn, "students", student_id=sid, school_id=sid, login_id="S001",
                             first_name="Test", last_name="Learner", created_by=teacher)
                self._insert(conn, "student_enrollments", enrollment_id=sid, school_id=sid, student_id=sid,
                             grade_level_id=sid, section_id=sid, school_year_id=sid, enrolled_by=teacher)
                self._insert(conn, "student_paces", pace_id=sid, school_id=sid, student_id=sid,
                             enrollment_id=sid, subject_id=sid, subject="Math", ww_scores="[7, 9, 0, null]", recorded_by=teacher)
                self._insert(conn, "class_record_setups", school_id=sid, section_id=sid, subject_id=sid, updated_by=teacher)
                self._insert(conn, "early_warnings", school_id=sid, student_id=sid, enrollment_id=sid,
                             subject="Math", risk_level="high", status="At Risk")
                self._insert(conn, "ai_risk_predictions", school_id=sid, student_id=sid, enrollment_id=sid, pace_id=sid)
                self._insert(conn, "data_quality_logs", school_id=sid, student_id=sid, teacher_id=teacher,
                             student_pace_id=sid, issue_type="test")
                self._insert(conn, "application_events", school_id=sid, staff_id=principal,
                             actor_id=self.operator_id, action="approved")
                # Legacy history has no school_id and still belongs to the selected account.
                self._insert(conn, "application_events", staff_id=teacher, actor_id=principal, action="rejected")
                self._insert(conn, "audit_logs", admin_id=self.operator_id, target_user_id=teacher, action="approve_user")
            # Only the actor belongs to a selected school: keep these histories.
            self._insert(conn, "application_events", id=self.kept_event, school_id=99,
                         staff_id=self.people[99][0], actor_id=self.people[10][0], action="reopened", detail="Keep this history")
            self._insert(conn, "audit_logs", id=self.kept_audit, admin_id=self.people[10][0],
                         target_user_id=self.people[99][0], action="approve_user", detail="Keep this audit")
            self._insert(conn, "audit_logs", id=self.operator_event, admin_id=self.operator_id,
                         target_user_id=self.operator_id, action="login_success")
            # Schema migration bookkeeping must survive, as must its table itself.
            conn.exec_driver_sql("CREATE TABLE trace_schema_migrations (name TEXT PRIMARY KEY)")
            conn.exec_driver_sql("INSERT INTO trace_schema_migrations VALUES ('section_advisers_backfill')")

    def _snapshot(self, conn):
        quote = conn.dialect.identifier_preparer.quote
        return {
            name: sorted((tuple(row) for row in conn.exec_driver_sql(f"SELECT * FROM {quote(name)}")), key=repr)
            for name in inspect(conn).get_table_names()
        }

    def _apply(self, ids=None, confirmation=None):
        selected = TEST_IDS if ids is None else ids
        if confirmation is None:
            confirmation = ",".join(str(sid) for sid in sorted(selected))
        with self.engine.connect() as conn:
            return cleanup_connection(conn, selected, apply=True, confirm_school_ids=confirmation)

    def test_list_and_preview_leave_all_rows_and_schema_unchanged(self):
        with self.engine.connect() as conn:
            before = self._snapshot(conn)
            listed = list_schools(conn)
            self.assertEqual({school["school_id"] for school in listed}, set(ALL_IDS))
            duplicates = [school for school in listed if school["name"] == "Gumagamot Elementary School"]
            self.assertEqual(len(duplicates), 2)
            plan = cleanup_connection(conn, TEST_IDS)
            self.assertFalse(plan["applied"])
            self.assertEqual(plan["table_counts"]["schools"], 4)
            self.assertEqual(plan["table_counts"]["staff"], 8)
            self.assertEqual(plan["table_counts"]["students"], 4)
            self.assertEqual(self._snapshot(conn), before)
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])

    def test_delete_all_linked_test_data_preserve_real_school_and_operator(self):
        with self.engine.connect() as conn:
            before = self._snapshot(conn)
        result = self._apply()
        self.assertTrue(result["applied"])
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT school_id FROM schools").all(), [(99,)])
            self.assertEqual(len(conn.execute(select(Staff.id)).scalars().all()), 3)
            self.assertEqual(conn.execute(select(Staff.id).where(Staff.role == "super_admin")).scalar_one(), self.operator_id)
            after = self._snapshot(conn)
            staff_school_index = list(Staff.__table__.columns.keys()).index("school_id")
            self.assertEqual(after["staff"], [row for row in before["staff"] if row[staff_school_index] in (None, 99)])
            for name, table in Base.metadata.tables.items():
                if "school_id" in table.c and name not in {"application_events", "staff"}:
                    real_row = conn.execute(select(table).where(table.c.school_id == 99)).all()
                    self.assertTrue(real_row, name)
                    # Compare persisted raw values so GUID conversion cannot hide changes.
                    column_index = list(table.columns.keys()).index("school_id")
                    self.assertEqual(after[name], [row for row in before[name] if row[column_index] == 99], name)
            for name in ("sessions", "staff_devices", "otp_codes", "password_resets", "resubmission_challenges"):
                self.assertEqual(len(after[name]), 3, name)
            events = Base.metadata.tables["application_events"]
            kept = conn.execute(select(events).where(events.c.id == self.kept_event)).mappings().one()
            self.assertIsNone(kept["actor_id"])
            self.assertEqual(kept["school_id"], 99)
            self.assertEqual(kept["detail"], "Keep this history")
            self.assertEqual(len(after["application_events"]), 3)
            audit = Base.metadata.tables["audit_logs"]
            kept = conn.execute(select(audit).where(audit.c.id == self.kept_audit)).mappings().one()
            self.assertIsNone(kept["admin_id"])
            self.assertEqual(kept["target_user_id"], self.people[99][0])
            self.assertEqual(kept["detail"], "Keep this audit")
            self.assertEqual(len(after["audit_logs"]), 3)
            self.assertEqual(after["trace_schema_migrations"], before["trace_schema_migrations"])
            self.assertEqual(set(after), set(before))
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])

    def test_reject_unknown_empty_or_unconfirmed_ids_without_any_deletion(self):
        with self.engine.connect() as conn:
            before = self._snapshot(conn)
        for ids, confirmation in (([], ""), ([10, 777], "10,777"), (TEST_IDS, "10,11,12")):
            with self.subTest(ids=ids, confirmation=confirmation):
                with self.assertRaises(CleanupError):
                    self._apply(ids, confirmation)
                with self.engine.connect() as conn:
                    self.assertEqual(self._snapshot(conn), before)

    def test_refuse_school_containing_operator(self):
        with self.engine.begin() as conn:
            conn.execute(Staff.__table__.update().where(Staff.id == self.operator_id).values(school_id=10))
        with self.assertRaises(CleanupError):
            self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM schools").scalar_one(), 5)

    def test_selecting_one_duplicate_name_leaves_the_other_registration(self):
        self._apply(ids=[12])
        with self.engine.connect() as conn:
            remaining = conn.exec_driver_sql("SELECT school_id FROM schools WHERE name='Gumagamot Elementary School'").all()
            self.assertEqual(remaining, [(10,)])
            self.assertIsNotNone(conn.execute(select(Staff.id).where(Staff.id == self.people[10][0])).scalar_one_or_none())
            self.assertIsNone(conn.execute(select(Staff.id).where(Staff.id == self.people[12][0])).scalar_one_or_none())

    def test_event_with_selected_school_but_retained_subject_account_aborts(self):
        with self.engine.begin() as conn:
            self._insert(conn, "application_events", school_id=10, staff_id=self.people[99][0], action="approved")
            before = self._snapshot(conn)
        with self.assertRaises(CleanupError):
            self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(self._snapshot(conn), before)

    def test_foreign_school_cascade_reference_aborts_without_touching_other_school(self):
        assignments = Base.metadata.tables["teacher_assignments"]
        with self.engine.begin() as conn:
            conn.execute(assignments.update().where(assignments.c.school_id == 99).values(teacher_id=self.people[10][1]))
            before = self._snapshot(conn)
        with self.assertRaises(CleanupError):
            self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(self._snapshot(conn), before)

    def test_foreign_school_set_null_reference_also_aborts(self):
        students = Base.metadata.tables["students"]
        with self.engine.begin() as conn:
            conn.execute(students.update().where(students.c.school_id == 99).values(created_by=self.people[10][1]))
            before = self._snapshot(conn)
        with self.assertRaises(CleanupError):
            self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(self._snapshot(conn), before)

    def test_transaction_rolls_back_earlier_deletions_and_actor_updates_on_error(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TRIGGER refuse_cleanup BEFORE DELETE ON schools BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            before = self._snapshot(conn)
        with self.assertRaises(Exception):
            self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(self._snapshot(conn), before)
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])

    def test_detect_legacy_auth_dependency_not_registered_in_current_models(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE old_login_tokens (id INTEGER PRIMARY KEY, session_id CHAR(36) REFERENCES sessions(id) ON DELETE CASCADE)")
            conn.exec_driver_sql("INSERT INTO old_login_tokens (session_id) SELECT id FROM sessions")
        with self.engine.connect() as conn:
            plan = cleanup_connection(conn, TEST_IDS)
            self.assertEqual(plan["table_counts"]["old_login_tokens"], 8)
        self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(conn.exec_driver_sql("SELECT COUNT(*) FROM old_login_tokens").scalar_one(), 3)
            self.assertEqual(conn.exec_driver_sql("PRAGMA foreign_key_check").all(), [])

    def test_refuse_legacy_dependency_that_would_delete_another_accounts_token(self):
        with self.engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE old_login_tokens (id INTEGER PRIMARY KEY, session_id CHAR(36) REFERENCES sessions(id) ON DELETE CASCADE, owner_id CHAR(36) REFERENCES staff(id))")
            session_table = Base.metadata.tables["sessions"]
            session_id = conn.execute(select(session_table.c.id).where(session_table.c.staff_id == self.people[10][1])).scalar_one()
            conn.exec_driver_sql("INSERT INTO old_login_tokens VALUES (1, ?, ?)", (str(session_id), str(self.people[99][1])))
            before = self._snapshot(conn)
        with self.assertRaises(CleanupError):
            self._apply()
        with self.engine.connect() as conn:
            self.assertEqual(self._snapshot(conn), before)

    def test_cli_listing_and_missing_database_never_write_or_expose_auth_data(self):
        script = Path(__file__).resolve().parent.parent / "cleanup_test_schools.py"
        environment = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{self.db_path.as_posix()}")
        with self.engine.connect() as conn:
            before = self._snapshot(conn)
        result = subprocess.run([sys.executable, str(script), "--list"], env=environment,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Gumagamot Elementary School", result.stdout)
        self.assertIn("principal-10@example.com", result.stdout)
        self.assertNotIn("unused-test-hash", result.stdout)
        self.assertNotIn("test-access", result.stdout)
        self.assertNotIn("123456", result.stdout)
        with self.engine.connect() as conn:
            self.assertEqual(self._snapshot(conn), before)
        missing_file = self.db_path.parent / "does-not-exist.db"
        environment["DATABASE_URL"] = f"sqlite+aiosqlite:///{missing_file.as_posix()}"
        result = subprocess.run([sys.executable, str(script), "--list"], env=environment,
                                capture_output=True, text=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing_file.exists())
        self.assertNotIn("Traceback", result.stderr)

    def test_same_school_name_and_principal_email_can_register_again(self):
        self._apply()

        async def register_again():
            from fastapi import Request
            from app.api.routers import schools
            from app.schemas.schools import SchoolRegisterRequest

            engine = create_async_engine(f"sqlite+aiosqlite:///{self.db_path.as_posix()}")

            @event.listens_for(engine.sync_engine, "connect")
            def enable_foreign_keys(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")

            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with sessions() as db:
                    payload = SchoolRegisterRequest(
                        school={"name": "Gumagamot Elementary School", "city": "Lala", "school_type": "public"},
                        first_name="New", last_name="Principal", email="principal-10@example.com",
                        contact_number="09123456789", password="ValidPassword123!",
                    )
                    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 9000)})
                    with patch.object(schools, "verify_captcha", AsyncMock()), patch.object(
                        schools, "async_get_password_hash", AsyncMock(return_value="new-test-hash")
                    ):
                        response = await schools.register_school(payload, request, db)
                    self.assertEqual(response["status"], "pending")
                    principal = (await db.execute(select(Staff).where(Staff.email == "principal-10@example.com"))).scalar_one()
                    self.assertEqual(principal.school_id, response["school_id"])
                    self.assertEqual(principal.account_status, "pending")
                    self.assertNotEqual(principal.id, self.people[10][0])
            finally:
                await engine.dispose()

        asyncio.run(register_again())


if __name__ == "__main__":
    unittest.main()
