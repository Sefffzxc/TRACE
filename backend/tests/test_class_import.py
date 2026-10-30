"""Import-route regressions using invented workbooks and in-memory SQLite.

Run from backend: python -m unittest discover -s tests -p test_class_import.py
No local database is opened and the external AI call is mocked.
"""
from __future__ import annotations

from datetime import date
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

# Route imports construct an engine from settings. Keep even that unused
# engine separate from any local or deployed database.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import HTTPException, Request, UploadFile
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routers import class_import
from app.db.session import Base
from app.models.academic import GradeLevel, SchoolYear, Section, Subject, TeacherAssignment
from app.models.schools import School
from app.models.staff import AuditLog, ROLE_TEACHER, Staff
from app.models.students import ClassRecordSetup, EarlyWarning, Student, StudentEnrollment, StudentPace
from app.services import grading
from test_eclass_import import make_record, workbook_bytes


IMPORTED_MODELS = (Student, StudentEnrollment, ClassRecordSetup, StudentPace, EarlyWarning, AuditLog)


class ClassImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        @event.listens_for(self.engine.sync_engine, "connect")
        def enable_foreign_keys(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.teacher_id, self.unassigned_id = uuid4(), uuid4()
        async with self.sessions() as db:
            db.add_all([School(school_id=i, name=f"School {i}") for i in (1, 2)])
            await db.flush()
            db.add_all([
                Staff(
                    id=staff_id, school_id=1, role=ROLE_TEACHER,
                    email=f"{label}@example.com", password_hash="unused",
                    first_name="Test", last_name=label, contact_number="",
                ) for staff_id, label in ((self.teacher_id, "Teacher"),
                                          (self.unassigned_id, "Unassigned"))
            ])
            for school_id in (1, 2):
                db.add_all([
                    GradeLevel(grade_level_id=school_id, school_id=school_id,
                               level="6", name="Grade 6"),
                    SchoolYear(school_year_id=school_id, school_id=school_id,
                               year="2026-2027", start_date=date(2026, 6, 1),
                               end_date=date(2027, 3, 31), is_current=True),
                ])
            await db.flush()
            db.add_all([
                Section(section_id=i, school_id=i, grade_level_id=i,
                        section_code="TEST", name="TEST") for i in (1, 2)
            ])
            db.add_all([
                Subject(subject_id=i, school_id=1, grade_level_id=1,
                        subject_name=name, subject_code=code)
                for i, name, code in ((1, "English", "ENG6"), (2, "GMRC", "GMRC6"))
            ])
            await db.flush()
            db.add_all([
                TeacherAssignment(school_id=1, teacher_id=self.teacher_id,
                                  section_id=1, subject_id=i) for i in (1, 2)
            ])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _import(self, content, *, save=False, subject_id=1,
                      section_id=1, teacher_id=None):
        async with self.sessions() as db:
            teacher = await db.get(Staff, teacher_id or self.teacher_id)
            upload = UploadFile(filename="invented.xlsx", file=io.BytesIO(content))
            try:
                return await class_import.import_class_record(
                    request=Request({"type": "http", "headers": []}),
                    file=upload, section_id=section_id, subject_id=subject_id,
                    save=save, current=teacher, scope=1, db=db,
                )
            finally:
                await upload.close()

    async def _counts(self):
        async with self.sessions() as db:
            return tuple([
                (await db.execute(select(func.count()).select_from(model))).scalar_one()
                for model in IMPORTED_MODELS
            ])

    async def test_compact_preview_and_save_preserve_pooled_exam_grading(self):
        book, _ = make_record(compact=True, sheet_names=("TERM1", "TERM2", "TERM3"))
        content = workbook_bytes(book)
        with patch.object(class_import.grading, "compute", wraps=grading.compute) as compute:
            preview = await self._import(content)
        self.assertEqual(await self._counts(), (0,) * len(IMPORTED_MODELS))
        self.assertEqual(preview["counts"]["new"], 3)
        self.assertEqual(preview["terms"][0]["graded"], 3)
        self.assertEqual(preview["terms"][0]["exam_subweights"], [25, 25, 50])
        self.assertTrue(all(tuple(call.kwargs["exam_subweights"]) == (25, 25, 50)
                            for call in compute.call_args_list))

        # An import must replace a class record's previous grading header,
        # including its exam shares, instead of retaining an old default.
        async with self.sessions() as db:
            db.add(ClassRecordSetup(
                school_id=1, section_id=1, subject_id=1,
                ww_highest="[5]", pt_highest="[5]", exam_highest="[10, 10, 10]",
                exam_subweights="[30, 30, 40]",
            ))
            await db.commit()

        with patch.object(class_import, "_score_batch", new_callable=AsyncMock,
                          return_value={"scored": 3}) as score:
            saved = await self._import(content, save=True)
        score.assert_awaited_once()
        self.assertEqual(saved["terms"], preview["terms"])
        self.assertEqual(saved["saved"]["added"], 3)
        self.assertEqual(saved["saved"]["terms"], [{"term": 1, "saved": 3, "skipped": []}])
        async with self.sessions() as db:
            setup = (await db.execute(select(ClassRecordSetup))).scalar_one()
            self.assertEqual(json.loads(setup.ww_highest), [10] * 5)
            self.assertEqual(json.loads(setup.pt_highest), [10] * 3)
            self.assertEqual(json.loads(setup.exam_highest), [20, 20, 40])
            self.assertEqual(json.loads(setup.exam_subweights), [25, 25, 50])
            rows = (await db.execute(select(Student, StudentPace).join(
                StudentPace, StudentPace.student_id == Student.student_id
            ))).all()
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(row.record_complete for _, row in rows),
                             preview["terms"][0]["graded"])
            for student, row in rows:
                mark = 8 if student.last_name.upper() == "REYES" else 7
                self.assertEqual(json.loads(row.ww_scores), [mark] * 5)
                self.assertEqual(json.loads(row.pt_scores), [mark] * 3)
                self.assertEqual(json.loads(row.exam_scores), [12, 15, 30])
                # Compact templates use SUM(exams)/SUM(caps), which becomes
                # per-exam shares of 25/25/50 for caps 20/20/40.
                expected_initial = mark / 10 * 70 + 57 / 80 * 30
                self.assertAlmostEqual(row.initial_grade, round(expected_initial, 2))
                self.assertEqual(row.transmuted_grade, grading.transmute(expected_initial))
                self.assertAlmostEqual(row.exam_percent, 71.25)

    async def test_domain_import_persists_each_domains_contribution(self):
        book, layouts = make_record(wide=True)
        layout = layouts[0]
        for index, col in enumerate(layout["pt_cols"]):
            layout["sheet"].cell(layout["male_row"], col, 10 if index < 3 else 0)
        content = workbook_bytes(book)
        preview = await self._import(content, subject_id=2)
        self.assertEqual(preview["terms"][0]["exam_subweights"], [30, 30, 40])
        with patch.object(class_import, "_score_batch", new_callable=AsyncMock):
            saved = await self._import(content, save=True, subject_id=2)
        self.assertEqual(saved["terms"], preview["terms"])
        async with self.sessions() as db:
            setup = (await db.execute(select(ClassRecordSetup))).scalar_one()
            self.assertEqual(json.loads(setup.ww_highest), [10, 10])
            self.assertEqual(json.loads(setup.pt_highest), [10, 10, 30])
            self.assertEqual(json.loads(setup.exam_highest), [20, 20, 40])
            self.assertEqual(json.loads(setup.exam_subweights), [30, 30, 40])
            row = (await db.execute(select(StudentPace).join(Student).where(
                func.upper(Student.last_name) == "SANTOS"
            ))).scalar_one()
            self.assertEqual(json.loads(row.ww_scores), [7, 7])
            self.assertEqual(json.loads(row.pt_scores), [10, 0, 0])
            self.assertEqual(json.loads(row.exam_scores), [12, 15, 30])
            self.assertAlmostEqual(row.initial_grade, 45.15)
            self.assertEqual(row.transmuted_grade, grading.transmute(45.15))
            self.assertTrue(row.record_complete)

    async def test_non_three_term_file_is_rejected_before_any_mutation(self):
        for titles in (("TERM 1", "TERM 2"),
                       ("TERM 1", "TERM 2", "TERM 3", "TERM 4"),
                       ("Quarter 1", "Quarter 2", "Quarter 3", "Quarter 4")):
            with self.subTest(titles=titles):
                book, _ = make_record(sheet_names=titles)
                with self.assertRaises(HTTPException) as raised:
                    await self._import(workbook_bytes(book), save=True)
                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual(await self._counts(), (0,) * len(IMPORTED_MODELS))

    async def test_unassigned_teacher_and_other_school_section_are_rejected(self):
        book, _ = make_record()
        content = workbook_bytes(book)
        for kwargs, status in (({"teacher_id": self.unassigned_id}, 403),
                               ({"section_id": 2}, 404)):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(HTTPException) as raised:
                    await self._import(content, save=True, **kwargs)
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(await self._counts(), (0,) * len(IMPORTED_MODELS))


if __name__ == "__main__":
    unittest.main()
