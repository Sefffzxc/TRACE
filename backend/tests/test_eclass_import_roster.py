"""Roster extraction uses supported class records without importing assessments."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.eclass_import import NotAnEClassRecord, read, read_roster
from test_eclass_import import make_record, workbook_bytes


class RosterReaderTests(unittest.TestCase):
    def test_supported_layouts_preserve_names_without_reading_assessments(self):
        for options in ({}, {"compact": True}, {"wide": True},
                        {"row_shift": 3, "col_shift": 2}):
            with self.subTest(options=options):
                book, layouts = make_record(**options)
                original = read(workbook_bytes(book))
                for layout in layouts:
                    sheet = layout["sheet"]
                    sheet.cell(layout["male_row"], layout["ww_cols"][0], 1001)
                    # Remove the highest-score label: names still need to be
                    # importable even if the assessment setup is unfinished.
                    for column in range(1, sheet.max_column + 1):
                        if sheet.cell(layout["highest_row"], column).value == "HIGHEST POSSIBLE SCORE":
                            sheet.cell(layout["highest_row"], column).value = None
                content = workbook_bytes(book)
                with self.assertRaises(NotAnEClassRecord):
                    read(content)
                result = read_roster(content)
                self.assertEqual(result.learners, original.learners)
                self.assertEqual(result.header, original.header)
                self.assertEqual(result.terms, [])

    def test_duplicate_learners_still_rejected(self):
        book, layouts = make_record()
        first = layouts[0]
        sheet = first["sheet"]
        sheet.cell(first["male_row"] + 1, 3,
                   sheet.cell(first["male_row"], 3).value)
        with self.assertRaisesRegex(NotAnEClassRecord, "same learner more than once"):
            read_roster(workbook_bytes(book))

    def test_invalid_file_and_unsupported_periods_still_rejected(self):
        with self.assertRaises(NotAnEClassRecord):
            read_roster(b"not an xlsx workbook")
        book, _ = make_record(sheet_names=("Quarter 1", "Quarter 2", "Quarter 3", "Quarter 4"))
        with self.assertRaises(NotAnEClassRecord):
            read_roster(workbook_bytes(book))


if __name__ == "__main__":
    unittest.main()
