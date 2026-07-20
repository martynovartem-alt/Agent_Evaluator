"""Deterministic checks — full edge-case coverage. Run: python3 -m unittest discover -s tests -t ."""
import json
import unittest
from pathlib import Path

from agent import run_offline_agent
from checks import (
    check_instruction_ok,
    check_must_facts,
    check_planted_operation,
    check_tools_ok,
    run_checks,
)

ROOT = Path(__file__).resolve().parent.parent


def _mcp_call(*op_ids):
    return {
        "name": "MCPClear",
        "args": {"fromDate": "2026-01-01", "toDate": "2026-12-31"},
        "result": {"operations": [{"id": i} for i in op_ids]},
    }


class TestToolsOk(unittest.TestCase):
    def test_called_when_needed(self):
        self.assertTrue(check_tools_ok({"needs_history": True}, {"tool_calls": [_mcp_call("o1")]}))

    def test_not_called_when_needed(self):
        self.assertFalse(check_tools_ok({"needs_history": True}, {"tool_calls": []}))

    def test_called_when_not_needed(self):
        self.assertFalse(check_tools_ok({"needs_history": False}, {"tool_calls": [_mcp_call("o1")]}))

    def test_not_called_when_not_needed(self):
        self.assertTrue(check_tools_ok({"needs_history": False}, {"tool_calls": []}))

    def test_getinstruction_alone_does_not_satisfy(self):
        trace = {"tool_calls": [{"name": "getInstruction", "args": {}, "result": {}}]}
        self.assertFalse(check_tools_ok({"needs_history": True}, trace))


class TestMustFacts(unittest.TestCase):
    def test_all_present(self):
        case = {"must_facts": ["Альфа-Смарт", "299"]}
        trace = {"answer": "Плата за «Альфа-Смарт» — 299 ₽."}
        self.assertEqual(check_must_facts(case, trace), {"Альфа-Смарт": True, "299": True})

    def test_one_missing(self):
        case = {"must_facts": ["Альфа-Смарт", "Альфа-Чек"]}
        trace = {"answer": "Плата за «Альфа-Смарт»."}
        self.assertEqual(check_must_facts(case, trace), {"Альфа-Смарт": True, "Альфа-Чек": False})

    def test_case_insensitive(self):
        self.assertEqual(check_must_facts({"must_facts": ["СБП"]}, {"answer": "перевод сбп"}), {"СБП": True})

    def test_empty_and_missing_answer(self):
        self.assertEqual(check_must_facts({"must_facts": []}, {"answer": "x"}), {})
        self.assertEqual(check_must_facts({"must_facts": ["x"]}, {}), {"x": False})


class TestInstructionOk(unittest.TestCase):
    def test_present(self):
        trace = {"chunks": [{"doc_id": "alfaSmart", "text": "..."}]}
        self.assertTrue(check_instruction_ok({"expected_instruction": "alfaSmart"}, trace))

    def test_absent(self):
        trace = {"chunks": [{"doc_id": "alfaCheck", "text": "..."}]}
        self.assertFalse(check_instruction_ok({"expected_instruction": "alfaSmart"}, trace))

    def test_no_expectation_is_none(self):
        self.assertIsNone(check_instruction_ok({"expected_instruction": None}, {"chunks": []}))


class TestPlantedOperation(unittest.TestCase):
    def test_present(self):
        trace = {"tool_calls": [_mcp_call("o1", "op_smart")]}
        self.assertTrue(check_planted_operation({"planted_operation_id": "op_smart"}, trace))

    def test_absent(self):
        trace = {"tool_calls": [_mcp_call("o1")]}
        self.assertFalse(check_planted_operation({"planted_operation_id": "op_smart"}, trace))

    def test_no_expectation_is_none(self):
        self.assertIsNone(check_planted_operation({"planted_operation_id": None}, {"tool_calls": []}))

    def test_no_tool_call_is_false(self):
        self.assertFalse(check_planted_operation({"planted_operation_id": "op_smart"}, {"tool_calls": []}))


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
                if case.get("expected_instruction"):
                    self.assertTrue(result["instruction_ok"])
                if case.get("planted_operation_id") and case.get("needs_history"):
                    self.assertTrue(result["planted_operation_ok"])


if __name__ == "__main__":
    unittest.main()
