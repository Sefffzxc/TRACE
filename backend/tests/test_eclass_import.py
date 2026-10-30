"""Class-record import regressions using invented, in-memory workbooks only.

Run from backend: python -m unittest discover -s tests -p test_eclass_import.py
These tests import the workbook reader directly and never open a database.
"""
from __future__ import annotations

import io
from pathlib import Path
import sys
import unittest

from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.eclass_import import NotAnEClassRecord, read
from app.services.grading import compute


MALE_NAMES = ("SANTOS, TEST A.", "REYES, SAMPLE B.")
FEMALE_NAMES = ("CRUZ, DEMO C.",)


def workbook_bytes(book):
    stream = io.BytesIO()
    book.save(stream)
    return stream.getvalue()


def make_record(*, compact=False, wide=False, row_shift=0, col_shift=0,
                sheet_names=("TERM 1", "TERM 2", "TERM 3")):
    """Model the two supplied template families without their learner data.

    The wide variation models GMRC's additional domains and shifted exam
    columns. Row/column shifts apply to both the input and term sheets.
    """
    book = Workbook()
    info = book.active
    info.title = "INPUT" if compact else "INPUT DATA"

    def put(sheet, row, col, value):
        sheet.cell(row + row_shift, col + col_shift, value)

    if compact:
        for row, col, label, value_col, value in (
            (5, 2, "SCHOOL NAME", 7, "Test Elementary School"),
            (5, 17, "SCHOOL ID", 19, "123456"),
            (5, 22, "SCHOOL YEAR", 25, "2026-2027"),
            (7, 6, "GRADE & SECTION:", 10, "6 TEST"),
            (7, 14, "TEACHER:", 17, "Test Teacher"),
            (7, 23, "SUBJECT:", 26, "English"),
        ):
            put(info, row, col, label)
            put(info, row, value_col, value)
        put(info, 8, 2, "LEARNERS' NAMES")
        put(info, 10, 2, "HIGHEST POSSIBLE SCORE")
        for gender, first_row, names in (
            ("MALE", 12, MALE_NAMES), ("FEMALE", 63, FEMALE_NAMES),
        ):
            put(info, first_row - 1, 2, gender)
            for offset, name in enumerate(names):
                put(info, first_row + offset, 1, offset + 1)
                put(info, first_row + offset, 2, name)
    else:
        put(info, 1, 1, "Input Data Sheet for Electronic-Class Record (ECR)")
        put(info, 7, 2, "SCHOOL INFORMATION")
        put(info, 7, 9, "LEARNERS' NAMES")
        for row, label, value in (
            (13, "SCHOOL ID", "123456"),
            (14, "SCHOOL NAME", "Test Elementary School"),
            (15, "SCHOOL YEAR", "2026-2027"),
            (23, "SUBJECT TEACHER", "Test Teacher"),
            (24, "SUBJECT", "GMRC" if wide else "English"),
            (25, "GRADE LEVEL", "6"),
            (26, "SECTION", "TEST"),
        ):
            put(info, row, 3, label)
            put(info, row, 4, ":")
            put(info, row, 5, value)
        for label_col, name_col, gender, names in (
            (10, 11, "MALE", MALE_NAMES),
            (13, 14, "FEMALE", FEMALE_NAMES),
        ):
            put(info, 10, label_col, gender)
            for offset, name in enumerate(names):
                put(info, 11 + offset, label_col, offset + 1)
                put(info, 11 + offset, name_col, name)

    layouts = []
    for number, title in enumerate(sheet_names, 1):
        sheet = book.create_sheet(title)
        group_row = 8 if compact else 12
        highest_row = 10 if compact else (16 if wide else 15)
        item_row = highest_row - 1
        male_row = highest_row + (2 if compact else 3)
        female_row = male_row + 51
        name_col = 2 if compact else 3
        put(sheet, 2, 2, "CLASS RECORD")
        put(sheet, 7 if compact else 10, 1 if compact else 2,
            f"TERM {number}")
        put(sheet, group_row, 6, "WRITTEN / ORAL WORKS (WWs)")
        pt_start, exam_start = (22, 40) if wide else (14, 20)
        put(sheet, group_row, pt_start, "PRODUCT / PERFORMANCE TASKS (PTs)")
        put(sheet, group_row, exam_start, "EXAMINATIONS (EXs)")
        put(sheet, highest_row, 2, "HIGHEST POSSIBLE SCORE")
        put(sheet, male_row - 2 if not compact else group_row, 2,
            "LEARNERS' NAMES")
        put(sheet, male_row - 1, 2, "MALE")
        put(sheet, female_row - 1, 2, "FEMALE")

        ww_cols = list(range(6, 11))
        pt_cols = list(range(14, 17))
        exam_cols = list(range(20, 23))
        ww_groups = [(ww_cols, 13, .2)]
        pt_groups = [(pt_cols, 19, .5)]
        exam_weight_col = 25 if compact else 27
        if wide:
            ww_cols = list(range(6, 11)) + list(range(14, 19))
            pt_cols = list(range(22, 25)) + list(range(28, 31)) + list(range(34, 37))
            exam_cols = [40, 41, 42]
            ww_groups = [(list(range(6, 11)), 13, .1),
                         (list(range(14, 19)), 21, .1)]
            pt_groups = [(list(range(22, 25)), 27, .1),
                         (list(range(28, 31)), 33, .1),
                         (list(range(34, 37)), 39, .3)]
            exam_weight_col = 47
            for start, label in (
                (6, "COGNITIVE DOMAIN"), (14, "AFFECTIVE DOMAIN"),
                (22, "COGNITIVE DOMAIN"), (28, "AFFECTIVE DOMAIN"),
                (34, "BEHAVIORAL DOMAIN"),
            ):
                put(sheet, 14, start, label)

        for cols, weight_col, weight in ww_groups + pt_groups:
            for index, col in enumerate(cols, 1):
                put(sheet, item_row, col, index)
                put(sheet, highest_row, col, 10)
            put(sheet, item_row, cols[-1] + 1, "Total")
            put(sheet, item_row, cols[-1] + 2, "PS")
            put(sheet, highest_row, cols[-1] + 2, 100)
            put(sheet, item_row, weight_col, "WS")
            put(sheet, highest_row, weight_col, weight)
        for col, label, cap in zip(exam_cols, ("ST1", "ST2", "TE"), (20, 20, 40)):
            put(sheet, item_row, col, label)
            put(sheet, highest_row, col, cap)
        if not compact:
            for offset, label in enumerate(("WS ST1", "WS ST2", "WS TE"), 1):
                put(sheet, item_row, exam_cols[-1] + offset, label)
                put(sheet, highest_row, exam_cols[-1] + offset,
                    40 if offset == 3 else 30)
        else:
            put(sheet, item_row, 23, "Total")
        put(sheet, item_row, exam_weight_col - 1, "PS")
        put(sheet, highest_row, exam_weight_col - 1, 100)
        put(sheet, item_row, exam_weight_col, "WS")
        put(sheet, highest_row, exam_weight_col, .3)
        put(sheet, group_row, exam_weight_col + 1, "Initial Grade")

        for first_row, names in ((male_row, MALE_NAMES), (female_row, FEMALE_NAMES)):
            for offset, name in enumerate(names):
                row = first_row + offset
                put(sheet, row, name_col - 1, offset + 1)
                put(sheet, row, name_col, name)
                if number == 1:
                    for col in ww_cols + pt_cols:
                        put(sheet, row, col, 7 + offset)
                    for col, score in zip(exam_cols, (12, 15, 30)):
                        put(sheet, row, col, score)
        layouts.append({
            "sheet": sheet, "group_row": group_row + row_shift,
            "item_row": item_row + row_shift, "highest_row": highest_row + row_shift,
            "male_row": male_row + row_shift, "female_row": female_row + row_shift,
            "name_col": name_col + col_shift,
            "ww_cols": [c + col_shift for c in ww_cols],
            "pt_cols": [c + col_shift for c in pt_cols],
            "exam_cols": [c + col_shift for c in exam_cols],
        })
    return book, layouts


class EClassImportTests(unittest.TestCase):
    def test_existing_template_preserves_header_roster_and_raw_scores(self):
        book, _ = make_record()
        record = read(workbook_bytes(book))
        self.assertEqual(record.header["school_id"], "123456")
        self.assertEqual(record.header["school_name"], "Test Elementary School")
        self.assertEqual(record.header["school_year"], "2026-2027")
        self.assertEqual(record.header["teacher"], "Test Teacher")
        self.assertEqual(record.header["subject"], "English")
        self.assertEqual(record.header["grade_level"], "6")
        self.assertEqual(record.header["section"], "TEST")
        self.assertEqual([learner.written for learner in record.learners],
                         list(MALE_NAMES + FEMALE_NAMES))
        self.assertEqual([learner.gender for learner in record.learners],
                         ["Male", "Male", "Female"])
        self.assertEqual([term.term for term in record.terms], [1])
        term = record.terms[0]
        self.assertEqual(term.ww_highest, [10] * 5)
        self.assertEqual(term.pt_highest, [10] * 3)
        self.assertEqual(term.exam_highest, [20, 20, 40])
        self.assertEqual(term.weights, (20, 50, 30))
        self.assertEqual(term.scores[0], {"ww": [7] * 5, "pt": [7] * 3,
                                          "exam": [12, 15, 30]})

    def test_compact_input_and_unspaced_term_names_are_accepted(self):
        book, _ = make_record(compact=True, sheet_names=("TERM1", "TERM2", "TERM3"))
        record = read(workbook_bytes(book))
        self.assertEqual(len(record.learners), 3)
        self.assertEqual(record.header["subject"], "English")
        self.assertEqual(record.terms[0].weights, (20, 50, 30))
        self.assertEqual(record.terms[0].scores[0]["ww"], [7] * 5)
        self.assertEqual(record.terms[0].exam_subweights, (25, 25, 50))
        term, scores = record.terms[0], record.terms[0].scores[0]
        grade = compute(
            ww_scores=scores["ww"], pt_scores=scores["pt"],
            exam_scores=scores["exam"], ww_highest=term.ww_highest,
            pt_highest=term.pt_highest, exam_highest=term.exam_highest,
            ww_weight=term.weights[0], pt_weight=term.weights[1],
            ex_weight=term.weights[2], exam_subweights=term.exam_subweights,
        )
        # Older records pool all three exams: (12+15+30)/(20+20+40).
        self.assertAlmostEqual(grade["initial_grade"], 70.38)

    def test_group_headers_support_gmrc_domains_and_shifted_exam_columns(self):
        book, layouts = make_record(wide=True)
        layout = layouts[0]
        for index, col in enumerate(layout["pt_cols"]):
            layout["sheet"].cell(layout["male_row"], col, 10 if index < 3 else 0)
        record = read(workbook_bytes(book))
        self.assertEqual(record.header["subject"], "GMRC")
        term = record.terms[0]
        self.assertEqual(term.ww_highest, [10, 10])
        self.assertEqual(term.pt_highest, [10, 10, 30])
        self.assertEqual(term.exam_highest, [20, 20, 40])
        self.assertEqual(term.weights, (20, 50, 30))
        self.assertEqual(term.scores[0]["exam"], [12, 15, 30])
        scores = term.scores[0]
        grade = compute(
            ww_scores=scores["ww"], pt_scores=scores["pt"],
            exam_scores=scores["exam"], ww_highest=term.ww_highest,
            pt_highest=term.pt_highest, exam_highest=term.exam_highest,
            ww_weight=term.weights[0], pt_weight=term.weights[1],
            ex_weight=term.weights[2], exam_subweights=term.exam_subweights,
        )
        # WW: 70% of 20; PT: 100% of its first 10-point domain;
        # exams: (60%*30 + 75%*30 + 75%*40)% of 30.
        self.assertAlmostEqual(grade["initial_grade"], 45.15)

    def test_rows_and_columns_can_move_without_changing_the_record(self):
        ordinary, _ = make_record()
        shifted, _ = make_record(row_shift=7, col_shift=4)
        self.assertEqual(read(workbook_bytes(shifted)), read(workbook_bytes(ordinary)))

    def test_term_names_allow_case_spacing_and_ordinal_words(self):
        book, layouts = make_record(sheet_names=("Term1", "term 2", "Third Term"))
        for layout, score in zip(layouts[1:], (4, 5)):
            layout["sheet"].cell(layout["male_row"], layout["ww_cols"][0], score)
        terms = read(workbook_bytes(book)).terms
        self.assertEqual([term.term for term in terms], [1, 2, 3])
        self.assertEqual(terms[1].scores[0]["ww"][0], 4)
        self.assertEqual(terms[2].scores[0]["ww"][0], 5)

    def test_reordered_term_learners_are_matched_to_their_names(self):
        book, layouts = make_record()
        layout = layouts[0]
        sheet, row, col = layout["sheet"], layout["male_row"], layout["name_col"]
        sheet.cell(row, col, MALE_NAMES[1])
        sheet.cell(row + 1, col, MALE_NAMES[0])
        record = read(workbook_bytes(book))
        indexes = {learner.written: index for index, learner in enumerate(record.learners)}
        self.assertEqual(record.terms[0].scores[indexes[MALE_NAMES[0]]]["ww"], [8] * 5)
        self.assertEqual(record.terms[0].scores[indexes[MALE_NAMES[1]]]["ww"], [7] * 5)

    def test_zero_is_a_score_and_empty_cells_remain_blank(self):
        book, layouts = make_record()
        layout = layouts[0]
        sheet, row = layout["sheet"], layout["male_row"]
        for col in layout["ww_cols"] + layout["pt_cols"] + layout["exam_cols"]:
            sheet.cell(row, col).value = None
        sheet.cell(row, layout["ww_cols"][0], 0)
        record = read(workbook_bytes(book))
        score = record.terms[0].scores[0]
        self.assertEqual(score["ww"], [0, None, None, None, None])
        self.assertEqual(score["pt"], [None] * 3)
        self.assertEqual(score["exam"], [None] * 3)

    def test_domain_conversion_keeps_zero_distinct_from_blank(self):
        book, layouts = make_record(wide=True)
        layout = layouts[0]
        for index, col in enumerate(layout["ww_cols"]):
            layout["sheet"].cell(layout["male_row"], col).value = 0 if index < 5 else None
        score = read(workbook_bytes(book)).terms[0].scores[0]
        self.assertEqual(score["ww"], [0, None])

    def test_empty_gmrc_domain_keeps_its_share_in_the_denominator(self):
        book, layouts = make_record(wide=True)
        for layout in layouts:
            for col in layout["ww_cols"][5:]:
                layout["sheet"].cell(layout["highest_row"], col).value = None
                for row in (layout["male_row"], layout["male_row"] + 1,
                            layout["female_row"]):
                    layout["sheet"].cell(row, col).value = None
        first = layouts[0]
        for col in first["ww_cols"][:5]:
            first["sheet"].cell(first["male_row"], col, 10)
        term = read(workbook_bytes(book)).terms[0]
        self.assertEqual(term.ww_highest, [10, 10])
        self.assertEqual(term.pt_highest, [10, 10, 30])
        self.assertEqual(term.scores[0]["ww"], [10, None])
        grade = compute(ww_scores=term.scores[0]["ww"], ww_highest=term.ww_highest,
                        ww_weight=term.weights[0])
        self.assertEqual(grade["ww_weighted"], 10)

    def test_similar_names_in_different_gender_lists_remain_distinct(self):
        book, layouts = make_record()
        male_name, female_name = "SANTOS, DANIEL", "SANTOS, DANIELA"
        book["INPUT DATA"].cell(11, 11, male_name)
        book["INPUT DATA"].cell(11, 14, female_name)
        for layout in layouts:
            layout["sheet"].cell(layout["male_row"], layout["name_col"], male_name)
            layout["sheet"].cell(layout["female_row"], layout["name_col"], female_name)
        for col in layouts[0]["ww_cols"]:
            layouts[0]["sheet"].cell(layouts[0]["female_row"], col, 3)
        record = read(workbook_bytes(book))
        self.assertEqual(len(record.learners), 3)
        indexes = {learner.written: index for index, learner in enumerate(record.learners)}
        self.assertEqual(record.learners[indexes[male_name]].gender, "Male")
        self.assertEqual(record.learners[indexes[female_name]].gender, "Female")
        self.assertEqual(record.terms[0].scores[indexes[male_name]]["ww"], [7] * 5)
        self.assertEqual(record.terms[0].scores[indexes[female_name]]["ww"], [3] * 5)

    def test_nonfinite_score_strings_are_read_as_blank(self):
        book, layouts = make_record()
        first = layouts[0]
        for col, value in zip(first["ww_cols"], ("NaN", "inf", "-inf", 0, 7)):
            first["sheet"].cell(first["male_row"], col, value)
        record = read(workbook_bytes(book))
        self.assertEqual(record.terms[0].scores[0]["ww"], [None, None, None, 0, 7])

    def test_only_three_distinct_terms_are_accepted(self):
        for titles in (
            ("TERM 1", "TERM 2"),
            ("TERM 1", "TERM 2", "TERM 3", "TERM 4"),
            ("TERM 1", "TERM1", "TERM 2", "TERM 3"),
        ):
            with self.subTest(titles=titles):
                book, _ = make_record(sheet_names=titles)
                with self.assertRaises(NotAnEClassRecord):
                    read(workbook_bytes(book))

    def test_fourth_term_with_a_subject_suffix_is_rejected(self):
        book, _ = make_record()
        book.create_sheet("TERM 4 Science")
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_copied_term_heading_is_reported_without_rejecting_the_record(self):
        book, layouts = make_record()
        layouts[1]["sheet"].cell(10, 2, "FIRST TERM")
        record = read(workbook_bytes(book))
        self.assertEqual([term.term for term in record.terms], [1])
        self.assertTrue(any("term 2" in warning.lower() and "heading" in warning.lower()
                            for warning in record.warnings))

    def test_quarterly_workbooks_are_rejected_even_with_term_tabs(self):
        book, _ = make_record()
        for title in ("Quarter 1", "Quarter 2", "Quarter 3", "Quarter 4"):
            book.create_sheet(title)
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_quarterly_tables_cannot_be_imported_by_renaming_their_tabs(self):
        book, layouts = make_record()
        for number, layout in enumerate(layouts, 1):
            layout["sheet"].cell(10, 2, f"Quarter {number}")
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_quarter_and_semester_titles_with_subject_names_are_rejected(self):
        for title in ("Science Quarter 1", "English 1st Quarter", "Math 2nd Semester",
                      "GMRC First Semester", "Science Semester 1"):
            with self.subTest(title=title):
                book, _ = make_record()
                book.create_sheet(title)
                with self.assertRaises(NotAnEClassRecord):
                    read(workbook_bytes(book))

    def test_term_sheet_names_do_not_make_an_unrelated_workbook_a_class_record(self):
        book = Workbook()
        book.active.title = "INPUT DATA"
        for title in ("TERM 1", "TERM 2", "TERM 3"):
            book.create_sheet(title)
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_missing_category_and_exam_labels_are_rejected(self):
        for category in ("ww", "pt", "exam"):
            with self.subTest(category=category):
                book, layouts = make_record()
                layout = layouts[0]
                col = layout[f"{category}_cols"][0]
                layout["sheet"].cell(layout["group_row"], col).value = None
                with self.assertRaises(NotAnEClassRecord):
                    read(workbook_bytes(book))

    def test_missing_highest_score_label_is_rejected(self):
        book, layouts = make_record()
        layouts[0]["sheet"].cell(layouts[0]["highest_row"], 2).value = None
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_unreadable_assessment_headers_are_rejected(self):
        for category in ("ww", "pt", "exam"):
            with self.subTest(category=category):
                book, layouts = make_record()
                layout = layouts[0]
                for col in layout[f"{category}_cols"]:
                    layout["sheet"].cell(layout["item_row"], col).value = "unknown"
                with self.assertRaises(NotAnEClassRecord):
                    read(workbook_bytes(book))

    def test_missing_roster_is_rejected(self):
        book, layouts = make_record()
        info = book["INPUT DATA"]
        for row in (11, 12):
            info.cell(row, 11).value = None
        info.cell(11, 14).value = None
        for layout in layouts:
            for row in (layout["male_row"], layout["male_row"] + 1, layout["female_row"]):
                layout["sheet"].cell(row, layout["name_col"]).value = None
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_unreadable_roster_is_rejected(self):
        book, layouts = make_record()
        info = book["INPUT DATA"]
        for row, col in ((11, 11), (12, 11), (11, 14)):
            info.cell(row, col, "unknown")
        for layout in layouts:
            for row in (layout["male_row"], layout["male_row"] + 1, layout["female_row"]):
                layout["sheet"].cell(row, layout["name_col"], "unknown")
        with self.assertRaises(NotAnEClassRecord):
            read(workbook_bytes(book))

    def test_term_roster_is_used_when_input_is_absent_or_stale(self):
        for remove_input in (True, False):
            with self.subTest(remove_input=remove_input):
                book, _ = make_record()
                if remove_input:
                    book.remove(book["INPUT DATA"])
                else:
                    info = book["INPUT DATA"]
                    for row, col in ((11, 11), (12, 11), (11, 14)):
                        info.cell(row, col, "unknown")
                record = read(workbook_bytes(book))
                self.assertEqual([learner.written for learner in record.learners],
                                 list(MALE_NAMES + FEMALE_NAMES))
                self.assertEqual(record.terms[0].scores[0]["ww"], [7] * 5)

    def test_uncached_formulas_can_reference_the_input_roster(self):
        book, layouts = make_record()
        for layout in layouts:
            for row, source in ((layout["male_row"], "$K$11"),
                                (layout["male_row"] + 1, "$K$12"),
                                (layout["female_row"], "$N$11")):
                reference = f"'INPUT DATA'!{source}"
                formula = f'=IF({reference}="","",{reference})'
                layout["sheet"].cell(row, layout["name_col"], formula)
        record = read(workbook_bytes(book))
        self.assertEqual(len(record.learners), 3)
        first = next(i for i, learner in enumerate(record.learners)
                     if learner.written == MALE_NAMES[0])
        self.assertEqual(record.terms[0].scores[first]["ww"], [7] * 5)

    def test_uncached_score_formula_is_blank_with_a_recalculate_warning(self):
        book, layouts = make_record()
        first = layouts[0]
        first["sheet"].cell(first["male_row"], first["ww_cols"][0], "=SUM(1,2)")
        first["sheet"].cell(first["male_row"], first["ww_cols"][1], 0)
        record = read(workbook_bytes(book))
        self.assertEqual(record.terms[0].scores[0]["ww"], [None, 0, 7, 7, 7])
        self.assertTrue(any("1 score formula" in warning.lower()
                            and "recalculate" in warning.lower()
                            and "save" in warning.lower()
                            for warning in record.warnings))

    def test_arbitrary_name_formulas_do_not_become_raw_input_names(self):
        book, layouts = make_record()
        first = layouts[0]
        first["sheet"].cell(first["male_row"], first["name_col"],
                            '=CONCAT(\'INPUT DATA\'!$K$11," other")')
        record = read(workbook_bytes(book))
        indexes = {learner.written: index for index, learner in enumerate(record.learners)}
        # Term 2/3 establish the real INPUT learner. Term 1's transformed
        # formula must not silently assign that different name's scores to him.
        self.assertIn(MALE_NAMES[0], indexes)
        self.assertNotIn(indexes[MALE_NAMES[0]], record.terms[0].scores)
        self.assertIn(indexes[MALE_NAMES[1]], record.terms[0].scores)
        self.assertIn(indexes[FEMALE_NAMES[0]], record.terms[0].scores)

    def test_files_larger_than_five_megabytes_are_rejected(self):
        with self.assertRaisesRegex(NotAnEClassRecord, "5 MB"):
            read(b"x" * (5 * 1024 * 1024 + 1))

    def test_non_excel_and_corrupt_excel_are_rejected(self):
        for content in (b"not an excel workbook", b"PK\x03\x04broken"):
            with self.subTest(content=content), self.assertRaises(NotAnEClassRecord):
                read(content)


if __name__ == "__main__":
    unittest.main()
