"""Config: .env parsing, defaults, and MCP mode dispatch."""
import unittest

import config
import tools


class TestParseDotenv(unittest.TestCase):
    def test_parses_and_strips(self):
        text = (
            "# a comment\n"
            "\n"
            "ANTHROPIC_API_KEY=sk-abc\n"
            'AGENT_MODEL="claude-opus-4-8"\n'
            "MCP_MODE=live              # inline comment\n"
            "EMPTY=\n"
        )
        d = config.parse_dotenv(text)
        self.assertEqual(d["ANTHROPIC_API_KEY"], "sk-abc")
        self.assertEqual(d["AGENT_MODEL"], "claude-opus-4-8")   # quotes stripped
        self.assertEqual(d["MCP_MODE"], "live")                 # inline comment stripped
        self.assertEqual(d["EMPTY"], "")
        self.assertNotIn("# a comment", d)


class TestDefaults(unittest.TestCase):
    def test_offline_defaults(self):
        self.assertEqual(config.MCP_MODE, "fixture")
        self.assertIn(config.AGENT_MODE, {"auto", "llm", "offline"})
        self.assertIn(config.JUDGE_EFFORT, {"low", "medium", "high"})
        self.assertIsInstance(config.JUDGE_CONCURRENCY, int)


class TestMcpDispatch(unittest.TestCase):
    def test_live_stdio_without_command_raises(self):
        saved = (config.MCP_MODE, config.MCP_TRANSPORT, config.MCP_COMMAND)
        config.MCP_MODE, config.MCP_TRANSPORT, config.MCP_COMMAND = "live", "stdio", ""
        try:
            with self.assertRaises(RuntimeError):
                tools.mcp_clear("user_alfa", "2026-01-01", "2027-01-01")
        finally:
            config.MCP_MODE, config.MCP_TRANSPORT, config.MCP_COMMAND = saved

    def test_fixture_mode_reads_local_data(self):
        self.assertEqual(config.MCP_MODE, "fixture")  # default unchanged
        res = tools.mcp_clear("user_alfa", "2026-01-01", "2027-01-01")
        self.assertTrue(res["operations"])


if __name__ == "__main__":
    unittest.main()
