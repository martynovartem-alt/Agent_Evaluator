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

    def test_sandbox_roles_carry_contract_fields(self):
        # Invariant, not a deployment pin: any role pointing at the Alfa Sandbox must carry
        # the documented contract ("4. Sandbox API.pdf": systemid header + 0.2 RPS limit).
        # Roles moved to other endpoints (DeepSeek, local, ...) are exempt.
        specs = [config.get(r) for r in ROLES] + config.panel("resolution")
        sandbox = [s for s in specs if "agenapisandbox" in s.base_url]
        if not sandbox:
            self.skipTest("no role points at the Alfa Sandbox in this config")
        for s in sandbox:
            self.assertEqual(s.system_id, "sanduser")
            self.assertEqual(s.rps, 0.2)

    def test_mode_override_via_env(self):
        os.environ["RESOLUTION_MODE"] = "offline"
        try:
            self.assertEqual(config.get("resolution").mode, "offline")
            self.assertFalse(config.get("resolution").available())
        finally:
            del os.environ["RESOLUTION_MODE"]


class TestTlsKnobs(unittest.TestCase):
    """insecure (curl -k) / ca_bundle: toml keys, env overrides, panel inheritance."""

    def test_defaults_are_secure(self):
        # a spec built without TLS keys verifies certificates (the shipped Sandbox toml
        # opts in explicitly with insecure = true — assert the dataclass, not config.get)
        s = config.AgentSpec(role="x", provider="openai", mode="llm", base_url="u",
                             api_key="k", model="m", effort="medium", prompt="p")
        self.assertFalse(s.insecure)
        self.assertEqual(s.ca_bundle, "")

    def test_insecure_from_toml_and_env_force_off(self):
        saved = config._RAW.get("resolution", {}).get("insecure")
        config._RAW.setdefault("resolution", {})["insecure"] = True
        try:
            self.assertTrue(config.get("resolution").insecure)
            os.environ["RESOLUTION_INSECURE"] = "0"      # env can force verification back on
            self.assertFalse(config.get("resolution").insecure)
        finally:
            os.environ.pop("RESOLUTION_INSECURE", None)
            if saved is None:
                config._RAW["resolution"].pop("insecure", None)
            else:
                config._RAW["resolution"]["insecure"] = saved

    def test_ca_bundle_env_override_and_root_resolution(self):
        os.environ["RESOLUTION_CA_BUNDLE"] = "certs/alfa_ca.pem"
        try:
            spec = config.get("resolution")
            self.assertEqual(spec.ca_bundle, str(config.ROOT / "certs/alfa_ca.pem"))
            os.environ["RESOLUTION_CA_BUNDLE"] = "/abs/alfa_ca.pem"
            self.assertEqual(config.get("resolution").ca_bundle, "/abs/alfa_ca.pem")
        finally:
            del os.environ["RESOLUTION_CA_BUNDLE"]

    def test_panel_inherits_tls(self):
        saved = config._RAW.get("resolution", {}).get("insecure")
        config._RAW.setdefault("resolution", {})["insecure"] = True
        try:
            panel = config.panel("resolution")
            self.assertTrue(panel)
            self.assertTrue(all(p.insecure for p in panel))
        finally:
            if saved is None:
                config._RAW["resolution"].pop("insecure", None)
            else:
                config._RAW["resolution"]["insecure"] = saved


class TestPanel(unittest.TestCase):
    def test_resolution_panel_from_toml(self):
        panel = config.panel("resolution")
        self.assertEqual([p.name for p in panel], ["judge", "prosecutor", "defender"])
        base = config.get("resolution")
        for p in panel:
            self.assertEqual(p.model, base.model)           # inherited from [resolution]
            self.assertEqual(p.base_url, base.base_url)
            self.assertTrue(p.prompt_text().strip())        # each persona prompt resolves
        self.assertEqual(len({p.prompt for p in panel}), 3)  # distinct personas

    def test_panel_off_switch(self):
        os.environ["RESOLUTION_PANEL"] = "off"
        try:
            self.assertEqual(config.panel("resolution"), [])
        finally:
            del os.environ["RESOLUTION_PANEL"]

    def test_role_without_panel_is_empty(self):
        self.assertEqual(config.panel("groundedness"), [])

    def test_env_model_override_applies_to_inherited_base_only(self):
        # RESOLUTION_MODEL overrides the [resolution] base the entries inherit — but an
        # explicit per-entry `model =` (a mixed-model bench) must still win over the env var.
        entry = dict(config._RAW["resolution"]["panel"][1])
        entry["model"] = "per-entry-model"
        saved = config._RAW["resolution"]["panel"][1]
        os.environ["RESOLUTION_MODEL"] = "env-model"
        config._RAW["resolution"]["panel"][1] = entry
        try:
            panel = config.panel("resolution")
            self.assertEqual(panel[0].model, "env-model")        # inherits base → env wins
            self.assertEqual(panel[1].model, "per-entry-model")  # explicit entry wins
        finally:
            del os.environ["RESOLUTION_MODEL"]
            config._RAW["resolution"]["panel"][1] = saved


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
