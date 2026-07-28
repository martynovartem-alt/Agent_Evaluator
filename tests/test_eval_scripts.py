"""eval_fast / eval_full: scheme validation, dataset handling, ETA (no API calls)."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import calibrate
import eval_fast


def _rec(rid="r1", verdict="yes", reason="none", reasoning="looks right"):
    return {"id": rid, "verdict": verdict, "failure_reason": reason, "reasoning": reasoning}


class TestValidate(unittest.TestCase):
    def test_clean_records_pass(self):
        records = [_rec(), _rec("r2", "no", "no_answer", "template reply"),
                   _rec("r3", "partial", "incomplete", "misses the why")]
        self.assertEqual(eval_fast.validate(records), [])

    def test_invalid_verdict_and_reason(self):
        problems = eval_fast.validate([_rec(verdict="maybe"), _rec("r2", reason="banana")])
        self.assertTrue(any("invalid verdict" in p for p in problems))
        self.assertTrue(any("invalid failure_reason" in p for p in problems))

    def test_inconsistent_verdict_reason(self):
        problems = eval_fast.validate([_rec(verdict="yes", reason="incomplete"),
                                       _rec("r2", verdict="no", reason="none")])
        self.assertEqual(len(problems), 2)

    def test_judge_error_and_stub_flagged(self):
        problems = eval_fast.validate([
            _rec(verdict="no", reason="other", reasoning="[judge error: HTTP 406: systemId]"),
            _rec("r2", reasoning="[stub — no judge LLM]"),
        ])
        self.assertTrue(any("judge error" in p for p in problems))
        self.assertTrue(any("offline stub" in p for p in problems))

    def test_empty_reasoning_flagged(self):
        self.assertTrue(any("empty reasoning" in p
                            for p in eval_fast.validate([_rec(reasoning="  ")])))


class TestEnsureJsonl(unittest.TestCase):
    def test_jsonl_passes_through(self):
        self.assertEqual(eval_fast.ensure_jsonl("data/labeled.jsonl"), "data/labeled.jsonl")

    def test_xlsx_converts_to_temp_dir(self):
        rows = [{"id": "row_1", "human_label": "yes", "agent_answer": "a"}]
        with mock.patch.object(eval_fast.dataset, "parse_xlsx", return_value=rows):
            out = eval_fast.ensure_jsonl("some report.xlsx")
        self.assertTrue(out.startswith(tempfile.gettempdir()))  # never inside the repo
        self.assertEqual([json.loads(l) for l in Path(out).read_text().splitlines()], rows)


class TestEta(unittest.TestCase):
    def test_minutes_and_hours(self):
        self.assertEqual(eval_fast.eta_text(60.0, 10, 100), "~10 min")
        self.assertEqual(eval_fast.eta_text(50.0, 10, 1145), "~1.6 h")
        self.assertEqual(eval_fast.eta_text(0.0, 0, 100), "~1 min")


class TestRunSmoke(unittest.TestCase):
    def test_records_flow_through_score_row(self):
        async def fake_judge(case, trace):
            return {"verdict": "no", "resolution_yes": False,
                    "failure_reason": "no_answer", "reasoning": "template"}

        rows = [{"id": f"r{i}", "human_label": "no", "dialogue": "q", "agent_answer": "a",
                 "operator_answer": "o", "assessor_comment": ""} for i in range(4)]
        with mock.patch.object(calibrate, "judge_resolution", new=fake_judge):
            records, elapsed = asyncio.run(eval_fast.run_smoke(rows, 2))
        self.assertEqual(len(records), 2)   # honors n
        self.assertEqual(records[0]["verdict"], "no")
        self.assertEqual(records[0]["failure_reason"], "no_answer")
        self.assertEqual(eval_fast.validate(records), [])
        self.assertGreaterEqual(elapsed, 0.0)


if __name__ == "__main__":
    unittest.main()
