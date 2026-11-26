"""Exercise the packaged models through the same HTTP endpoints as the API.

Run with ml/.venv-ml/Scripts/python.exe -m unittest discover -s ai-service/tests.
Uses invented component percentages; no database or private workbook is read.
"""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
os.environ["MODEL_DIR"] = str(ROOT / "model")
os.environ["AI_SERVICE_TOKEN"] = ""
sys.path.insert(0, str(ROOT))
import main


def first_record(reference="first"):
    return {"q1_term_grade": 82, "q1_initial_grade": 78,
        "q1_ww_pct": 76, "q1_pt_pct": 80, "q1_exam_pct": 74,
        "q1_component_gap": 6, "grade_level": 8, "reference": reference}


def updated_record(reference="second", mark=90):
    return {**first_record(reference), "q2_term_grade": mark, "q2_initial_grade": mark - 5,
        "q2_ww_pct": mark - 4, "q2_pt_pct": mark, "q2_exam_pct": mark - 6,
        "q2_component_gap": 6}


class CheckpointServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def predict(self, record):
        response = self.client.post("/predict", json=record)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_each_checkpoint_uses_its_own_model_and_second_period_changes_answer(self):
        first = self.predict(first_record())
        improved = self.predict(updated_record(mark=94))
        declined = self.predict(updated_record(mark=70))
        self.assertIn("first-term", first["model_version"])
        self.assertIn("second-term", improved["model_version"])
        self.assertGreater(improved["predicted_final"], declined["predicted_final"] + 5)
        self.assertEqual(improved["imputed_fields"], [])
        self.assertFalse(improved["used_baseline_defaults"])
        for prediction in (first, improved, declined):
            self.assertLessEqual(prediction["range_low"], prediction["predicted_final"])
            self.assertLessEqual(prediction["predicted_final"], prediction["range_high"])

    def test_mixed_batch_keeps_order_and_matches_individual_predictions(self):
        records = [updated_record("A"), first_record("B"), updated_record("C", mark=75)]
        response = self.client.post("/predict/batch", json={"records": records})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["count"], 3)
        self.assertEqual([p["reference"] for p in body["predictions"]], ["A", "B", "C"])
        for record, result in zip(records, body["predictions"]):
            self.assertEqual(result, self.predict(record))

    def test_partial_second_period_is_rejected_instead_of_imputed(self):
        for record in ({**first_record(), "q2_term_grade": 90},
                       {**updated_record(), "q2_exam_pct": None},
                       {**first_record(), "q2_term_grade": None}):
            response = self.client.post("/predict", json=record)
            self.assertEqual(response.status_code, 422, response.text)

    def test_unknown_fields_and_nonfinite_components_are_rejected(self):
        response = self.client.post("/predict", json={**updated_record(), "q3_term_grade": 100})
        self.assertEqual(response.status_code, 422)
        response = self.client.post("/predict", json={**updated_record(), "q2_ww_pct": "NaN"})
        self.assertEqual(response.status_code, 422)

    def test_missing_second_model_returns_unavailable_instead_of_first_model(self):
        with patch.dict(main.STATE, {"second_bundle": None, "second_error": "Test unavailable"}):
            response = self.client.post("/predict", json=updated_record())
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn("Second-period", response.json()["detail"])
            self.assertEqual(self.client.post("/predict", json=first_record()).status_code, 200)
            self.assertFalse(self.client.get("/health").json()["model_loaded"])

    def test_cards_publish_separate_measured_results_and_inputs(self):
        response = self.client.get("/model-card")
        self.assertEqual(response.status_code, 200)
        first = response.json()
        second = first["second_term_model"]
        self.assertEqual(len(first["features"]), 7)
        self.assertEqual(len(second["features"]), 13)
        for card in (first, second):
            self.assertTrue(card["reported_metrics"])
            self.assertTrue(card["baseline_for_comparison"])
            self.assertIn("Three-period", card["target"])
            self.assertTrue(card["limitations"])

    def test_updated_delta_is_added_once_to_the_mean_of_both_grades(self):
        class ConstantChange:
            def predict(self, frame):
                return np.full(len(frame), 3.0)
        b = {**main.bundle(True), "model": ConstantChange()}
        record = main.StudentRecord(**updated_record(mark=92))
        self.assertEqual(main.predict_one(record, b).predicted_final, 90.0)


if __name__ == "__main__":
    unittest.main()
