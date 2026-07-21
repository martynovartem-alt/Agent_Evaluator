"""Judge payload contract, result shaping, and offline stub fallback (no API calls)."""
import asyncio
import unittest

import config
from judges.groundedness import _payload as g_payload
from judges.groundedness import judge_groundedness
from judges.resolution import _payload as r_payload
from judges.resolution import _shape, judge_resolution


class TestPayloadContract(unittest.TestCase):
    def test_groundedness_sees_trace_not_operator_answer(self):
        p = g_payload(
            {"query": "q", "operator_answer": "GROUND TRUTH"},
            {"answer": "a", "tool_calls": [{"name": "MCPClear"}], "chunks": [{"doc_id": "x"}]},
        )
        self.assertNotIn("operator_answer", p)  # grounding is vs tool data, not the reference
        self.assertEqual(p["tool_calls"], [{"name": "MCPClear"}])
        self.assertEqual(p["chunks"], [{"doc_id": "x"}])

    def test_resolution_sees_operator_answer_not_tools(self):
        p = r_payload({"query": "q", "operator_answer": "GT"}, {"answer": "a", "tool_calls": [1]})
        self.assertEqual(p["operator_answer"], "GT")
        self.assertNotIn("tool_calls", p)


class TestShaping(unittest.TestCase):
    def test_resolution_yes_derivation(self):
        self.assertTrue(_shape("yes", "")["resolution_yes"])
        self.assertFalse(_shape("partial", "")["resolution_yes"])
        self.assertFalse(_shape("no", "")["resolution_yes"])


class TestOfflineFallback(unittest.TestCase):
    def setUp(self):
        if config.get("resolution").available():
            self.skipTest("judge LLM available — fallback path not exercised")

    def test_available_false(self):
        self.assertFalse(config.get("resolution").available())
        self.assertFalse(config.get("groundedness").available())

    def test_resolution_stub_shape(self):
        r = asyncio.run(judge_resolution({"query": "q", "operator_answer": "o"}, {"answer": "a"}))
        self.assertIn(r["verdict"], ("yes", "partial", "no"))
        self.assertEqual(r["resolution_yes"], r["verdict"] == "yes")

    def test_groundedness_stub_shape(self):
        r = asyncio.run(judge_groundedness({"query": "q"}, {"answer": "a", "tool_calls": [], "chunks": []}))
        self.assertIn("has_unsupported_critical_claim", r)
        self.assertIn("unsupported_claims", r)


if __name__ == "__main__":
    unittest.main()
