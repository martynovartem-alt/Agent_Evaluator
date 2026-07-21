"""Agent system-prompt injection + verbatim v2 wiring."""
import unittest

import config
from agent import _execute_tool, _openai_tools, build_system


class TestToolExecution(unittest.TestCase):
    def test_mcpclear_records_call(self):
        tool_calls, chunks = [], []
        res = _execute_tool("MCPClear", {"fromDate": "2026-01-01", "toDate": "2027-01-01"},
                            "user_alfa", "2026-07-20", tool_calls, chunks)
        self.assertIn("operations", res)
        self.assertEqual(tool_calls[0]["name"], "MCPClear")

    def test_mcpclear_defaults_missing_dates(self):
        tool_calls, chunks = [], []
        res = _execute_tool("MCPClear", {}, "user_alfa", "2026-07-20", tool_calls, chunks)
        self.assertIn("operations", res)   # missing dates → 85-day window, no crash

    def test_getinstruction_records_chunk(self):
        tool_calls, chunks = [], []
        res = _execute_tool("getInstruction", {"instructionName": ["alfaSmart"]},
                            "u", "2026-07-20", tool_calls, chunks)
        self.assertIn("alfaSmart", res)
        self.assertEqual(chunks[0]["doc_id"], "alfaSmart")

    def test_openai_tools_shape(self):
        tools = _openai_tools()
        names = {t["function"]["name"] for t in tools}
        self.assertEqual(names, {"MCPClear", "getInstruction"})
        self.assertEqual(tools[0]["type"], "function")
        self.assertIn("parameters", tools[0]["function"])


class TestBuildSystem(unittest.TestCase):
    def test_fills_placeholders_when_present(self):
        out = build_system("Date {current_date}, user {user_id}.", "2026-07-20", "user_0001")
        self.assertEqual(out, "Date 2026-07-20, user user_0001.")

    def test_prepends_context_when_no_placeholders(self):
        out = build_system("You are an agent.", "2026-07-20", "user_0001")
        self.assertIn("Current date: 2026-07-20", out)
        self.assertIn("user_0001", out)
        self.assertTrue(out.endswith("You are an agent."))

    def test_literal_braces_do_not_crash(self):
        # a verbatim prompt may contain { } (JSON examples, tokens) — must not use .format
        out = build_system('Output {"verdict": "yes"} strictly.', "2026-07-20", "u")
        self.assertIn('{"verdict": "yes"}', out)


class TestVerbatimV2(unittest.TestCase):
    def test_agent_points_at_verbatim_prompt(self):
        spec = config.get("agent")
        self.assertEqual(spec.prompt, "agent_prompt_v2.md")

    def test_v2_loads_and_builds_without_error(self):
        spec = config.get("agent")
        text = spec.prompt_text()
        self.assertIn("MCPClear", text)          # references the real tools
        self.assertIn("getInstruction", text)
        system = build_system(text, "2026-07-20", "user_0001")   # must not raise
        self.assertIn("Current date: 2026-07-20", system)


if __name__ == "__main__":
    unittest.main()
