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


class TestDiagnostician(unittest.TestCase):
    def test_categorizes_by_where_with_remediation(self):
        records = [
            # API failure with structured error info from the judge
            {"id": "r1", "verdict": "no", "failure_reason": "other",
             "reasoning": "[judge error: cannot reach ...]",
             "error": {"where": "api", "what_to_do": "connect to the bank VPN/VDI", "detail": "m cannot reach url"}},
            {"id": "r2", "verdict": "no", "failure_reason": "other",
             "reasoning": "[judge error: cannot reach ...]",
             "error": {"where": "api", "what_to_do": "connect to the bank VPN/VDI", "detail": "m cannot reach url"}},
            # stub — a config problem
            {"id": "r3", "verdict": "yes", "failure_reason": "none", "reasoning": "[stub — no judge LLM]"},
            # model answered but broke the schema — llm_output
            {"id": "r4", "verdict": "maybe", "failure_reason": "none", "reasoning": "ok"},
        ]
        findings = eval_fast.SchemeDiagnostician().diagnose(records)
        wheres = [f.where for f in findings]
        self.assertEqual(wheres, ["config", "api", "llm_output"])  # ordered by category
        api = next(f for f in findings if f.where == "api")
        self.assertEqual(api.rows, ["r1", "r2"])                   # grouped, not repeated
        self.assertIn("VPN", api.what_to_do)
        self.assertIn("invalid verdict", next(f for f in findings if f.where == "llm_output").problem)

    def test_clean_records_produce_no_findings(self):
        records = [{"id": "r1", "verdict": "yes", "failure_reason": "none", "reasoning": "fine"}]
        self.assertEqual(eval_fast.SchemeDiagnostician().diagnose(records), [])

    def test_dataset_failures_diagnosed_before_api(self):
        self.assertEqual(eval_fast.main("no/such/file.jsonl", 2), 1)      # missing file → exit 1
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.jsonl"
            bad.write_text("{not json\n")
            self.assertEqual(eval_fast.main(str(bad), 2), 1)              # malformed → exit 1


class TestSampleAndErrorLog(unittest.TestCase):
    def test_sample_keeps_dataset_order_and_size(self):
        rows = [{"id": f"r{i}"} for i in range(50)]
        picked = eval_fast.sample_rows(rows, 20)
        self.assertEqual(len(picked), 20)
        idx = [int(r["id"][1:]) for r in picked]
        self.assertEqual(idx, sorted(idx))            # dataset order preserved
        self.assertEqual(len({r["id"] for r in picked}), 20)   # no duplicates

    def test_small_set_passes_through(self):
        rows = [{"id": "a"}, {"id": "b"}]
        self.assertEqual(eval_fast.sample_rows(rows, 20), rows)

    def test_error_log_carries_dialogue_and_error(self):
        records = [{"id": "row_7", "dialogue": "CLIENT: где мои деньги",
                    "agent_answer": "final_answer: ...", "operator_answer": "оператор ответил"}]
        findings = [eval_fast.Finding("api", "HTTP 400 HAS_PERSONAL_DATA",
                                      "extend privacy.py", ["row_7"])]
        with tempfile.TemporaryDirectory() as d:
            path = eval_fast.write_error_log(findings, records, Path(d) / "errors.log")
            text = path.read_text()
        for expected in ("row_7", "HTTP 400 HAS_PERSONAL_DATA", "extend privacy.py",
                         "где мои деньги", "final_answer", "оператор ответил"):
            self.assertIn(expected, text)


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

    def test_error_passthrough_judge_to_record_to_diagnosis(self):
        # The full chain: judge error dict → score_row record → diagnostician finding.
        async def failing_judge(case, trace):
            return {"verdict": "no", "resolution_yes": False, "failure_reason": "other",
                    "reasoning": "[judge error: boom]",
                    "error": {"where": "api", "what_to_do": "connect VPN", "detail": "boom"}}

        rows = [{"id": "r0", "human_label": "no", "dialogue": "q", "agent_answer": "a",
                 "operator_answer": "o", "assessor_comment": ""}]
        with mock.patch.object(calibrate, "judge_resolution", new=failing_judge):
            records, _ = asyncio.run(eval_fast.run_smoke(rows, 1))
        self.assertEqual(records[0]["error"]["where"], "api")   # score_row passes it through
        findings = eval_fast.SchemeDiagnostician().diagnose(records)
        self.assertEqual(findings[0].where, "api")
        self.assertEqual(findings[0].what_to_do, "connect VPN")

    def test_reasoning_only_judge_error_still_diagnosed(self):
        # A vote error absorbed by a yes majority has no structured error — the scheme
        # gate and the diagnosis must still agree (no "SCHEME FAILED with empty diagnosis").
        records = [{"id": "r0", "verdict": "yes", "failure_reason": "none",
                    "reasoning": "panel: judge→yes ⇒ yes. [judge] [judge error: timeout]"}]
        problems = eval_fast.validate(records)
        findings = eval_fast.SchemeDiagnostician().diagnose(records)
        self.assertTrue(problems)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].where, "api")
        self.assertEqual(bool(problems), bool(findings))  # gate == diagnosis, by construction


if __name__ == "__main__":
    unittest.main()
