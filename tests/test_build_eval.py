"""Eval-set builder: client-turn extraction + history heuristic + case shape."""
import unittest

from build_eval import build, client_turns, needs_history


class TestClientTurns(unittest.TestCase):
    def test_extracts_only_client_utterances(self):
        d = ("CLIENT (10:00): За что списали 299?\n"
             "OPERATOR (10:01): Секунду, уточняю.\n"
             "CLIENT (10:02): И ещё вопрос по переводу")
        self.assertEqual(client_turns(d), "За что списали 299? И ещё вопрос по переводу")

    def test_handles_slash_delimited(self):
        d = "CLIENT (10:00): Да /  OPERATOR (10:01): Ждите /  CLIENT (10:02): Подтверждаю"
        self.assertEqual(client_turns(d), "Да Подтверждаю")

    def test_falls_back_to_dialogue_when_no_client_marker(self):
        self.assertEqual(client_turns("просто текст"), "просто текст")


class TestNeedsHistory(unittest.TestCase):
    def test_true_on_transaction_words(self):
        self.assertTrue(needs_history("за что списали комиссию"))
        self.assertTrue(needs_history("верните деньги за подписку"))

    def test_false_on_smalltalk(self):
        self.assertFalse(needs_history("спасибо, всё понятно"))


class TestBuild(unittest.TestCase):
    def test_case_shape_and_synthetic_user(self):
        rows = [{"id": "r1", "dialogue": "CLIENT (1): перевод?", "operator_answer": "final_answer: ..."}]
        case = build(rows)[0]
        self.assertEqual(case["id"], "r1")
        self.assertEqual(case["query"], "перевод?")
        self.assertTrue(case["needs_history"])
        self.assertEqual(case["fixture_user"], "user_0001")   # index 0 → user_0001
        self.assertEqual(case["operator_answer"], "final_answer: ...")
        self.assertEqual(case["must_facts"], [])              # no deterministic GT for synthetic answers


if __name__ == "__main__":
    unittest.main()
