"""Ground-truth ingestion + calibration math."""
import unittest

from calibrate import summarize
from dataset import norm_label


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
        self.assertEqual(r["confusion"]["partial"]["no"], 1)
        self.assertEqual(r["confusion"]["no"]["yes"], 1)
        self.assertEqual(r["confusion"]["yes"]["yes"], 1)

    def test_empty(self):
        self.assertEqual(summarize([])["agreement"], 0.0)


if __name__ == "__main__":
    unittest.main()
