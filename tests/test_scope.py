"""Scope classification: intent rules, cache, segmentation summaries (no API calls)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import calibrate
from judges import scope as scope_mod


class TestNormIntent(unittest.TestCase):
    def test_set_literal_export(self):
        self.assertEqual(scope_mod.norm_intent("{'Возврат комиссии обращение*'}"),
                         "Возврат комиссии обращение")
        self.assertEqual(scope_mod.norm_intent("{'Сроки и статус перевода*', 'История операций*'}"),
                         "Сроки и статус перевода, История операций")

    def test_backend_and_blank_are_empty(self):
        self.assertEqual(scope_mod.norm_intent("Интент оперделен на бэке"), "")
        self.assertEqual(scope_mod.norm_intent(""), "")
        self.assertEqual(scope_mod.norm_intent("   "), "")

    def test_plain_string_passthrough(self):
        self.assertEqual(scope_mod.norm_intent("История операций*"), "История операций")


class TestScopeFromIntent(unittest.TestCase):
    def test_domain_intents_in_scope(self):
        for name in ("Возврат комиссии обращение", "История операций",
                     "Сроки и статус перевода", "Списание подписки", "Комиссия за снятие ДК"):
            self.assertEqual(scope_mod.scope_from_intent(name), "in_scope", name)

    def test_foreign_intent_out_of_scope(self):
        self.assertEqual(scope_mod.scope_from_intent("Ипотека"), "out_of_scope")
        self.assertEqual(scope_mod.scope_from_intent("Перевыпуск карты"), "out_of_scope")

    def test_no_intent_is_undecided(self):
        self.assertEqual(scope_mod.scope_from_intent(""), "")


class TestCache(unittest.TestCase):
    def test_roundtrip_and_key_stability(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(scope_mod, "CACHE_PATH", Path(d) / "cache.json"):
                self.assertEqual(scope_mod.load_cache(), {})
                key = scope_mod.cache_key("диалог про списание")
                scope_mod.save_cache({key: {"scope": "in_scope", "topic": "история"}})
                self.assertEqual(scope_mod.load_cache()[key]["scope"], "in_scope")
        self.assertEqual(scope_mod.cache_key("x"), scope_mod.cache_key("x"))
        self.assertNotEqual(scope_mod.cache_key("x"), scope_mod.cache_key("y"))


class TestClassifyOffline(unittest.TestCase):
    def test_stub_returns_unknown_not_a_guess(self):
        os.environ["SCOPE_MODE"] = "offline"
        try:
            out = asyncio.run(scope_mod.classify_scope("любой диалог"))
        finally:
            del os.environ["SCOPE_MODE"]
        self.assertEqual(out["scope"], "unknown")
        self.assertIn("[stub", out["reasoning"])


class TestAssignScopes(unittest.TestCase):
    def test_rule_only_without_classify(self):
        rows = [{"intent": "{'История операций*'}", "dialogue": "a"},
                {"intent": "Интент оперделен на бэке", "dialogue": "b"},
                {"intent": "", "dialogue": "c"}]
        asyncio.run(calibrate.assign_scopes(rows, classify=False))
        self.assertEqual([r["scope"] for r in rows], ["in_scope", "unknown", "unknown"])
        self.assertEqual(rows[0]["intent_norm"], "История операций")

    def test_classify_uses_cache_then_llm(self):
        rows = [{"intent": "", "dialogue": "cached one"}, {"intent": "", "dialogue": "fresh one"}]
        cached_key = scope_mod.cache_key("cached one")

        async def fake_llm(dialogue):
            return {"scope": "out_of_scope", "topic": "кредит", "reasoning": "r"}

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cache.json"
            path.write_text(json.dumps({cached_key: {"scope": "in_scope", "topic": "x"}}))
            with mock.patch.object(scope_mod, "CACHE_PATH", path), \
                 mock.patch.object(scope_mod, "classify_scope", new=fake_llm):
                asyncio.run(calibrate.assign_scopes(rows, classify=True))
            saved = json.loads(path.read_text())
        self.assertEqual(rows[0]["scope"], "in_scope")      # from cache, no LLM call
        self.assertEqual(rows[1]["scope"], "out_of_scope")  # from the classifier
        self.assertIn(scope_mod.cache_key("fresh one"), saved)  # new result cached


class TestSegmentSummaries(unittest.TestCase):
    _RECORDS = [
        {"scope": "in_scope", "intent": "История операций", "human_label": "yes", "verdict": "yes"},
        {"scope": "in_scope", "intent": "История операций", "human_label": "no", "verdict": "no"},
        {"scope": "out_of_scope", "intent": "", "human_label": "no", "verdict": "yes"},
        {"scope": "unknown", "intent": "", "human_label": "partial", "verdict": "no"},
    ]

    def test_scope_summary(self):
        s = calibrate.scope_summary(self._RECORDS)
        self.assertEqual(s["in_scope"]["n"], 2)
        self.assertEqual(s["in_scope"]["human_correct_pct"], 50.0)
        self.assertEqual(s["in_scope"]["agreement"], 100.0)
        self.assertEqual(s["out_of_scope"]["agreement"], 0.0)
        self.assertEqual(s["unknown"]["agreement"], 100.0)  # partial→incorrect == no→incorrect

    def test_intent_summary_min_n_and_math(self):
        self.assertEqual(calibrate.intent_summary(self._RECORDS, min_n=3), [])
        by = calibrate.intent_summary(self._RECORDS, min_n=2)
        self.assertEqual(by, [{"intent": "История операций", "n": 2,
                               "human_correct_pct": 50.0, "judge_agreement_pct": 100.0}])


if __name__ == "__main__":
    unittest.main()
