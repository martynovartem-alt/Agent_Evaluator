"""Ground-truth ingestion + calibration math."""
import csv
import tempfile
import unittest
from pathlib import Path

from calibrate import (CSV_FIELDS, LABELS_BINARY, _ORDER_BINARY, agg_metrics,
                       select_rows, summarize, to_binary, write_csv)
from dataset import norm_label

_RECORDS = [
    {"id": "a", "human_label": "yes", "verdict": "yes", "reasoning": "", "agent_answer": "",
     "operator_answer": "", "dialogue": "", "assessor_comment": ""},
    {"id": "b", "human_label": "no", "verdict": "yes", "reasoning": "wrong", "agent_answer": "x",
     "operator_answer": "", "dialogue": "line1\nline2", "assessor_comment": ""},
]


class TestNormLabel(unittest.TestCase):
    def test_maps_russian_labels(self):
        self.assertEqual(norm_label("Да"), "yes")
        self.assertEqual(norm_label("Частично"), "partial")
        self.assertEqual(norm_label("Нет"), "no")

    def test_case_and_whitespace_insensitive(self):
        self.assertEqual(norm_label("  нет "), "no")

    def test_blank_or_unknown_is_empty(self):
        self.assertEqual(norm_label(""), "")
        self.assertEqual(norm_label("maybe"), "")


class TestSummarize(unittest.TestCase):
    def test_agreement_and_confusion(self):
        pairs = [("yes", "yes"), ("no", "no"), ("partial", "no"), ("no", "yes")]
        r = summarize(pairs)
        self.assertEqual(r["n"], 4)
        self.assertEqual(r["agreement"], 50.0)  # 2 of 4 exact
        self.assertEqual(r["within1"], 75.0)    # (no,yes) is the only 2-apart pair
        self.assertIsInstance(r["kappa"], float)
        self.assertEqual(r["confusion"]["partial"]["no"], 1)
        self.assertEqual(r["confusion"]["no"]["yes"], 1)
        self.assertEqual(r["confusion"]["yes"]["yes"], 1)

    def test_empty(self):
        self.assertEqual(summarize([])["agreement"], 0.0)


class TestAggMetrics(unittest.TestCase):
    def test_mean_and_std(self):
        reports = [{"agreement": 60.0, "kappa": 0.20, "within1": 90.0},
                   {"agreement": 64.0, "kappa": 0.30, "within1": 92.0}]
        a = agg_metrics(reports)
        self.assertEqual(a["agreement_mean"], 62.0)
        self.assertAlmostEqual(a["kappa_mean"], 0.25)
        self.assertGreater(a["kappa_std"], 0)

    def test_single_run_std_zero(self):
        a = agg_metrics([{"agreement": 60.0, "kappa": 0.2, "within1": 90.0}])
        self.assertEqual(a["kappa_std"], 0.0)


class TestBinary(unittest.TestCase):
    def test_to_binary_mapping(self):
        self.assertEqual(to_binary("no"), "wrong")
        self.assertEqual(to_binary("yes"), "acceptable")
        self.assertEqual(to_binary("partial"), "acceptable")

    def test_summarize_binary_labels(self):
        pairs = [("wrong", "wrong"), ("acceptable", "acceptable"),
                 ("acceptable", "wrong"), ("wrong", "acceptable")]
        r = summarize(pairs, LABELS_BINARY, _ORDER_BINARY)
        self.assertEqual(r["agreement"], 50.0)
        self.assertEqual(r["confusion"]["acceptable"]["wrong"], 1)
        self.assertIn("wrong", r["confusion"])


class TestDisagreementCsv(unittest.TestCase):
    def test_select_only_disagreements_by_default(self):
        self.assertEqual([r["id"] for r in select_rows(_RECORDS)], ["b"])
        self.assertEqual([r["id"] for r in select_rows(_RECORDS, all_rows=True)], ["a", "b"])

    def test_write_csv_header_rows_and_agree_column(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "out.csv"
            n = write_csv(_RECORDS, path)
            self.assertEqual(n, 1)  # only the disagreement
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(list(rows[0].keys()), CSV_FIELDS)
            self.assertEqual(rows[0]["id"], "b")
            self.assertEqual(rows[0]["agree"], "False")
            self.assertIn("line1\nline2", rows[0]["dialogue"])  # multiline survives round-trip


if __name__ == "__main__":
    unittest.main()
