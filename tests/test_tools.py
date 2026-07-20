"""Tool layer — determinism and lookup. The harness's reproducibility rests on these."""
import unittest

from tools import get_transactions, retrieve_faq


class TestRetrieveFaq(unittest.TestCase):
    def test_pin_query_ranks_expected_doc_first(self):
        hits = retrieve_faq("How do I reset my PIN?")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["doc_id"], "faq_pin_reset")

    def test_deterministic(self):
        q = "How do I reset my PIN?"
        self.assertEqual(retrieve_faq(q), retrieve_faq(q))

    def test_k_limits_results(self):
        self.assertLessEqual(len(retrieve_faq("card transaction PIN limits fees", k=2)), 2)

    def test_no_overlap_returns_empty(self):
        self.assertEqual(retrieve_faq("zzxq wobblegonk"), [])


class TestGetTransactions(unittest.TestCase):
    def test_known_user(self):
        txns = get_transactions("user_123")
        self.assertEqual(len(txns), 3)
        self.assertIn("txn_abc", {t["id"] for t in txns})

    def test_unknown_user_is_empty(self):
        self.assertEqual(get_transactions("nobody"), [])


if __name__ == "__main__":
    unittest.main()
