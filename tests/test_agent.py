"""Agent system-prompt injection + verbatim v2 wiring."""
import unittest

import config
from agent import build_system


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
