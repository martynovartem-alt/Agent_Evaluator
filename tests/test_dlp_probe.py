"""DLP probe: the bisection must corner the triggering fragment (no network)."""
import unittest
from unittest import mock

import dlp_probe


class TestBisect(unittest.TestCase):
    def _bisect(self, units, trigger, joiner="\n"):
        fake = lambda spec, text: trigger in text
        with mock.patch.object(dlp_probe, "triggers", fake):
            return dlp_probe.bisect_units(None, units, joiner, "test")

    def test_single_trigger_line_found(self):
        lines = [f"строка {i}" for i in range(20)]
        lines[13] = "тут паспорт клиента"
        self.assertEqual(self._bisect(lines, "паспорт"), ["тут паспорт клиента"])

    def test_word_level_narrows_to_word(self):
        words = "а б в СЕКРЕТ г д е ж з".split()
        self.assertEqual(self._bisect(words, "СЕКРЕТ", joiner=" "), ["СЕКРЕТ"])

    def test_split_trigger_spans_both_parts(self):
        # a trigger needing two separated units → the window covers both
        lines = ["x", "часть-А", "y", "y", "часть-Б", "z"]
        fake = lambda spec, text: "часть-А" in text and "часть-Б" in text
        with mock.patch.object(dlp_probe, "triggers", fake):
            window = dlp_probe.bisect_units(None, lines, "\n", "test")
        self.assertEqual(window[0], "часть-А")
        self.assertEqual(window[-1], "часть-Б")


if __name__ == "__main__":
    unittest.main()
