"""Config: per-agent specs (endpoint/model/prompt), .env parsing, MCP dispatch."""
import os
import unittest

import config
import tools

ROLES = ("agent", "groundedness", "resolution")


class TestParseDotenv(unittest.TestCase):
    def test_parses_and_strips(self):
        d = config.parse_dotenv('# c\n\nAGENT_API_KEY=sk-a\nX="y"\nMCP_MODE=live   # inline\nE=\n')
        self.assertEqual(d["AGENT_API_KEY"], "sk-a")
        self.assertEqual(d["X"], "y")            # quotes stripped
        self.assertEqual(d["MCP_MODE"], "live")  # inline comment stripped
        self.assertEqual(d["E"], "")


class TestAgentSpecs(unittest.TestCase):
    def test_every_role_resolves_endpoint_model_prompt(self):
        for role in ROLES:
            s = config.get(role)
            self.assertIsInstance(s.base_url, str)          # per-agent endpoint
            self.assertTrue(s.model)
            self.assertIn(s.effort, {"low", "medium", "high"})
            self.assertTrue(s.prompt_text().strip())        # per-agent prompt reads

    def test_roles_are_independent(self):
        # each role is its own spec object — editable independently in agents.toml
        self.assertEqual({config.get(r).role for r in ROLES}, set(ROLES))

    def test_api_key_env_resolution_and_fallback(self):
        env_name = config._RAW["resolution"]["api_key_env"]   # the role's configured key var
        saved = {k: os.environ.get(k) for k in (env_name, "ANTHROPIC_API_KEY")}
        try:
            os.environ[env_name] = "sk-role"
            os.environ.pop("ANTHROPIC_API_KEY", None)
            self.assertEqual(config.get("resolution").api_key, "sk-role")       # from api_key_env
            os.environ.pop(env_name, None)
            os.environ["ANTHROPIC_API_KEY"] = "sk-fallback"
            self.assertEqual(config.get("resolution").api_key, "sk-fallback")   # fallback
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_provider_field(self):
        self.assertIn(config.get("agent").provider, {"anthropic", "openai"})

    def test_mode_override_via_env(self):
        os.environ["RESOLUTION_MODE"] = "offline"
        try:
            self.assertEqual(config.get("resolution").mode, "offline")
            self.assertFalse(config.get("resolution").available())
        finally:
            del os.environ["RESOLUTION_MODE"]


class TestMcpAndPipeline(unittest.TestCase):
    def test_mcp_default_and_concurrency(self):
        self.assertEqual(config.MCP_MODE, "fixture")
        self.assertIsInstance(config.JUDGE_CONCURRENCY, int)

    def test_live_stdio_without_command_raises(self):
        saved = (config.MCP_MODE, config.MCP_TRANSPORT, config.MCP_COMMAND)
        config.MCP_MODE, config.MCP_TRANSPORT, config.MCP_COMMAND = "live", "stdio", ""
        try:
            with self.assertRaises(RuntimeError):
                tools.mcp_clear("user_alfa", "2026-01-01", "2027-01-01")
        finally:
            config.MCP_MODE, config.MCP_TRANSPORT, config.MCP_COMMAND = saved

    def test_fixture_mode_reads_local_data(self):
        self.assertTrue(tools.mcp_clear("user_alfa", "2026-01-01", "2027-01-01")["operations"])


if __name__ == "__main__":
    unittest.main()
