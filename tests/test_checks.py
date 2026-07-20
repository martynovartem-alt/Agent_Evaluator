"""Deterministic checks — full edge-case coverage. Run: python3 -m unittest discover -s tests -t ."""
import json
import unittest
from pathlib import Path

from agent import run_offline_agent
from checks import (
    check_faq_doc,
    check_must_facts,
    check_planted_txn,
    check_tools_ok,
    run_checks,
)

ROOT = Path(__file__).resolve().parent.parent


def _txn_call(*txn_ids):
    return {
        "name": "get_transactions",
        "args": {"user_id": "u"},
        "result": {"transactions": [{"id": i} for i in txn_ids]},
    }


class TestToolsOk(unittest.TestCase):
    def test_called_when_needed(self):
        self.assertTrue(check_tools_ok({"needs_transactions": True}, {"tool_calls": [_txn_call("t1")]}))

    def test_not_called_when_needed(self):
        self.assertFalse(check_tools_ok({"needs_transactions": True}, {"tool_calls": []}))

    def test_called_when_not_needed(self):
        self.assertFalse(check_tools_ok({"needs_transactions": False}, {"tool_calls": [_txn_call("t1")]}))

    def test_not_called_when_not_needed(self):
        self.assertTrue(check_tools_ok({"needs_transactions": False}, {"tool_calls": []}))

    def test_multiple_calls_still_ok(self):
        trace = {"tool_calls": [_txn_call("t1"), _txn_call("t2")]}
        self.assertTrue(check_tools_ok({"needs_transactions": True}, trace))

    def test_other_tool_does_not_satisfy(self):
        trace = {"tool_calls": [{"name": "search_faq", "args": {}, "result": {}}]}
        self.assertFalse(check_tools_ok({"needs_transactions": True}, trace))


class TestMustFacts(unittest.TestCase):
    def test_all_present(self):
        case = {"must_facts": ["Netflix", "50"]}
        trace = {"answer": "You paid $50 to Netflix."}
        self.assertEqual(check_must_facts(case, trace), {"Netflix": True, "50": True})

    def test_one_missing(self):
        case = {"must_facts": ["Netflix", "Spotify"]}
        trace = {"answer": "You paid $50 to Netflix."}
        self.assertEqual(check_must_facts(case, trace), {"Netflix": True, "Spotify": False})

    def test_case_insensitive_both_directions(self):
        self.assertEqual(check_must_facts({"must_facts": ["NETFLIX"]}, {"answer": "netflix"}), {"NETFLIX": True})
        self.assertEqual(check_must_facts({"must_facts": ["pin"]}, {"answer": "Reset your PIN"}), {"pin": True})

    def test_substring_semantics(self):
        # Spec is substring match: "50" is found inside "$500".
        self.assertEqual(check_must_facts({"must_facts": ["50"]}, {"answer": "balance $500"}), {"50": True})

    def test_empty_must_facts(self):
        self.assertEqual(check_must_facts({"must_facts": []}, {"answer": "anything"}), {})

    def test_missing_answer(self):
        self.assertEqual(check_must_facts({"must_facts": ["x"]}, {}), {"x": False})


class TestFaqDoc(unittest.TestCase):
    def test_present(self):
        case = {"expected_faq_doc": "faq_pin_reset"}
        trace = {"chunks": [{"doc_id": "faq_pin_reset", "text": "..."}]}
        self.assertTrue(check_faq_doc(case, trace))

    def test_absent(self):
        case = {"expected_faq_doc": "faq_pin_reset"}
        trace = {"chunks": [{"doc_id": "faq_fees", "text": "..."}]}
        self.assertFalse(check_faq_doc(case, trace))

    def test_no_expectation_is_none(self):
        self.assertIsNone(check_faq_doc({"expected_faq_doc": None}, {"chunks": []}))
        self.assertIsNone(check_faq_doc({}, {"chunks": []}))

    def test_present_among_many(self):
        trace = {"chunks": [{"doc_id": "a"}, {"doc_id": "faq_pin_reset"}, {"doc_id": "b"}]}
        self.assertTrue(check_faq_doc({"expected_faq_doc": "faq_pin_reset"}, trace))


class TestPlantedTxn(unittest.TestCase):
    def test_present(self):
        case = {"planted_txn_id": "txn_abc"}
        trace = {"tool_calls": [_txn_call("txn_aaa", "txn_abc")]}
        self.assertTrue(check_planted_txn(case, trace))

    def test_absent(self):
        case = {"planted_txn_id": "txn_abc"}
        trace = {"tool_calls": [_txn_call("txn_aaa")]}
        self.assertFalse(check_planted_txn(case, trace))

    def test_no_expectation_is_none(self):
        self.assertIsNone(check_planted_txn({"planted_txn_id": None}, {"tool_calls": []}))

    def test_no_tool_call_is_false(self):
        case = {"planted_txn_id": "txn_abc"}
        self.assertFalse(check_planted_txn(case, {"tool_calls": []}))


class TestRunChecksIntegration(unittest.TestCase):
    """run_checks over real offline-agent traces on the golden set (fixture regression guard)."""

    def _cases(self):
        path = ROOT / "data" / "golden_mini.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_golden_mini_all_pass(self):
        for case in self._cases():
            trace = run_offline_agent(case)
            result = run_checks(case, trace)
            with self.subTest(case=case["id"]):
                self.assertTrue(result["tools_ok"])
                self.assertTrue(result["all_must_facts_present"])
                # Diagnostic checks are None when the case has no expectation.
                if case.get("expected_faq_doc"):
                    self.assertTrue(result["faq_doc_ok"])
                if case.get("planted_txn_id") and case.get("needs_transactions"):
                    self.assertTrue(result["planted_txn_ok"])


if __name__ == "__main__":
    unittest.main()
