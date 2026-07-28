"""Judge payload contract, result shaping, panel voting, and offline stub fallback (no API calls)."""
import asyncio
import unittest
from unittest import mock

import config
from judges import resolution as resolution_mod
from judges.groundedness import _payload as g_payload
from judges.groundedness import judge_groundedness
from judges.resolution import _payload as r_payload
from judges.resolution import _shape, judge_resolution, majority_verdict


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

    def test_failure_reason_normalization(self):
        self.assertEqual(_shape("yes", "", "incomplete")["failure_reason"], "none")  # yes → none
        self.assertEqual(_shape("partial", "", "incomplete")["failure_reason"], "incomplete")
        self.assertEqual(_shape("no", "")["failure_reason"], "other")               # missing → other
        self.assertEqual(_shape("no", "", "bogus_reason")["failure_reason"], "other")


class TestMajorityVerdict(unittest.TestCase):
    def test_two_of_three_wins(self):
        self.assertEqual(majority_verdict(["yes", "yes", "no"]), "yes")
        self.assertEqual(majority_verdict(["no", "yes", "no"]), "no")
        self.assertEqual(majority_verdict(["partial", "yes", "partial"]), "partial")

    def test_unanimous(self):
        self.assertEqual(majority_verdict(["yes", "yes", "yes"]), "yes")

    def test_three_way_split_is_partial(self):
        self.assertEqual(majority_verdict(["yes", "partial", "no"]), "partial")

    def test_single_vote_passthrough(self):
        self.assertEqual(majority_verdict(["partial"]), "partial")

    def test_even_split_leans_conservative(self):
        self.assertEqual(majority_verdict(["yes", "no"]), "no")        # lower median
        self.assertEqual(majority_verdict(["yes", "partial"]), "partial")


def _panel_spec(name: str) -> config.AgentSpec:
    # provider=openai + base_url ⇒ available() without a key or the anthropic package
    return config.AgentSpec(role="resolution", provider="openai", mode="llm",
                            base_url="http://sandbox.test", api_key="", model="test-model",
                            effort="medium", prompt="prompts/resolution.md", name=name)


class TestPanelVoting(unittest.TestCase):
    """Panel path with the LLM call mocked out — votes keyed by panelist name."""

    def _run(self, votes_by_name: dict, panel_names=("judge", "prosecutor", "defender")):
        async def fake_judge(spec, system, payload, schema):
            v = votes_by_name[spec.name]
            if isinstance(v, Exception):
                raise v
            return {"verdict": v, "reasoning": f"{spec.name} reasoning"}

        with mock.patch.object(resolution_mod.config, "get", return_value=_panel_spec("resolution")), \
             mock.patch.object(resolution_mod.config, "panel",
                               return_value=[_panel_spec(n) for n in panel_names]), \
             mock.patch.object(resolution_mod._llm, "judge_json", new=fake_judge):
            return asyncio.run(judge_resolution(
                {"query": "q", "operator_answer": "o"}, {"answer": "a"}))

    def test_majority_yes(self):
        r = self._run({"judge": "yes", "prosecutor": "no", "defender": "yes"})
        self.assertEqual(r["verdict"], "yes")
        self.assertTrue(r["resolution_yes"])
        self.assertEqual([v["verdict"] for v in r["votes"]], ["yes", "no", "yes"])
        self.assertEqual(r["votes"][1]["judge"], "prosecutor")
        self.assertIn("prosecutor→no", r["reasoning"])

    def test_split_resolves_to_partial(self):
        r = self._run({"judge": "yes", "prosecutor": "no", "defender": "partial"})
        self.assertEqual(r["verdict"], "partial")
        self.assertFalse(r["resolution_yes"])

    def test_panel_failure_reason_majority(self):
        async def fake_judge(spec, system, payload, schema):
            v = {"judge": ("no", "no_answer"), "prosecutor": ("no", "incomplete"),
                 "defender": ("no", "incomplete")}[spec.name]
            return {"verdict": v[0], "failure_reason": v[1], "reasoning": spec.name}

        with mock.patch.object(resolution_mod.config, "get", return_value=_panel_spec("resolution")), \
             mock.patch.object(resolution_mod.config, "panel",
                               return_value=[_panel_spec(n) for n in ("judge", "prosecutor", "defender")]), \
             mock.patch.object(resolution_mod._llm, "judge_json", new=fake_judge):
            r = asyncio.run(judge_resolution({"query": "q"}, {"answer": "a"}))
        self.assertEqual(r["verdict"], "no")
        self.assertEqual(r["failure_reason"], "incomplete")  # 2 of 3 not-correct votes say incomplete
        self.assertEqual(r["votes"][0]["failure_reason"], "no_answer")

    def test_one_panelist_error_absorbed(self):
        r = self._run({"judge": "yes", "prosecutor": RuntimeError("boom"), "defender": "yes"})
        self.assertEqual(r["verdict"], "yes")   # 2 real votes outvote the errored "no"
        self.assertEqual(r["votes"][1]["verdict"], "no")
        self.assertIn("[judge error:", r["votes"][1]["reasoning"])

    def test_unusable_panelist_skipped_not_counted_as_no(self):
        # A panelist whose spec can never run (mode=offline) must be skipped, not become
        # a standing "no" vote — only the 2 usable panelists vote here.
        offline = config.AgentSpec(role="resolution", provider="openai", mode="offline",
                                   base_url="http://sandbox.test", api_key="", model="m",
                                   effort="medium", prompt="prompts/resolution.md", name="broken")

        async def fake_judge(spec, system, payload, schema):
            return {"verdict": "yes", "reasoning": spec.name}

        with mock.patch.object(resolution_mod.config, "get", return_value=_panel_spec("resolution")), \
             mock.patch.object(resolution_mod.config, "panel",
                               return_value=[_panel_spec("judge"), offline, _panel_spec("defender")]), \
             mock.patch.object(resolution_mod._llm, "judge_json", new=fake_judge):
            r = asyncio.run(judge_resolution({"query": "q"}, {"answer": "a"}))
        self.assertEqual([v["judge"] for v in r["votes"]], ["judge", "defender"])
        self.assertEqual(r["verdict"], "yes")

    def test_no_panel_keeps_single_judge_shape(self):
        async def fake_judge(spec, system, payload, schema):
            return {"verdict": "partial", "reasoning": "solo"}

        with mock.patch.object(resolution_mod.config, "get", return_value=_panel_spec("resolution")), \
             mock.patch.object(resolution_mod.config, "panel", return_value=[]), \
             mock.patch.object(resolution_mod._llm, "judge_json", new=fake_judge):
            r = asyncio.run(judge_resolution({"query": "q"}, {"answer": "a"}))
        self.assertEqual(r["verdict"], "partial")
        self.assertNotIn("votes", r)


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
