"""A future-period label must never become a checkpoint input."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "train_second_term_model.py"
spec = importlib.util.spec_from_file_location("checkpoint_training", SCRIPT)
training = importlib.util.module_from_spec(spec)
spec.loader.exec_module(training)


def record(period, grade, school="Invented School", name="Invented Learner"):
    return {"ID": period, "Name": name, "Sex": "unused", "School": school,
        "Subject": "Mathematics", "School Year": "2025-2026", "Grade Level": 6,
        "Quarter": period, "Term Grade": grade, "Initial Grade": grade - 5,
        "Written Works Percentage Score": 80, "Performance Task Percentage Score": 85,
        "Term Exam Percentage Score": 75, "Final Grade": 99}


class CheckpointDatasetTests(unittest.TestCase):
    def build(self, rows):
        with patch.object(training.pd, "read_excel", return_value=pd.DataFrame(rows)):
            return training.build(Path("unused.xlsx"))

    def test_three_period_target_excludes_fourth_period_and_workbook_final(self):
        result = self.build([record(1, 70), record(2, 80), record(3, 90), record(4, 100)])
        self.assertEqual(len(result), 1)
        self.assertEqual(result.final_grade.iloc[0], 80)
        self.assertEqual(result.anchor.iloc[0], 75)
        self.assertEqual(result.change.iloc[0], 5)
        self.assertFalse(any(key.startswith(("q3_", "q4_")) for key in training.FEATURES))
        self.assertNotIn("final_grade", training.FEATURES)
        self.assertNotIn("Name", result.columns)

    def test_incomplete_historical_year_is_excluded(self):
        self.assertTrue(self.build([record(1, 70), record(2, 80), record(4, 90)]).empty)

    def test_conflicting_duplicate_checkpoint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate period 2"):
            self.build([record(1, 70), record(2, 80), record(2, 81), record(3, 90)])

    def test_same_name_in_different_schools_is_not_paired(self):
        result = self.build([record(1, 70, school="A"), record(2, 80, school="B"),
                             record(3, 90, school="B")])
        self.assertTrue(result.empty)


if __name__ == "__main__":
    unittest.main()
