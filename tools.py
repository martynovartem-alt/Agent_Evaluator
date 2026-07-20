"""
The support agent's two tools, backed by deterministic fixtures.

- get_transactions: the MCP-served transactions tool. This in-process function is
  the offline fixture adapter; in production it is served over MCP. The agent-facing
  contract (name, args, result shape) is what the trace captures, so the transport
  can be swapped behind this seam without touching the runner or judges.
- retrieve_faq: FAQ retrieval over a fixture corpus (deterministic token-overlap scorer).

Both read only from fixtures/ — no network, no live services — so every run is reproducible.
"""
import json
from functools import lru_cache
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# Common words stripped before FAQ scoring so overlap reflects content, not glue words.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "how", "i", "if",
    "in", "is", "it", "my", "of", "on", "or", "the", "to", "was", "what", "when",
    "where", "which", "will", "with", "you", "your",
}


@lru_cache(maxsize=1)
def _transactions() -> dict:
    return json.loads((FIXTURES / "transactions.json").read_text())


@lru_cache(maxsize=1)
def _faq_docs() -> tuple:
    return tuple(json.loads((FIXTURES / "faq.json").read_text()))


def _tokens(text: str) -> set[str]:
    words = "".join(c.lower() if c.isalnum() else " " for c in text).split()
    return {w for w in words if w not in _STOPWORDS}


def get_transactions(user_id: str) -> list[dict]:
    """Return the fixture transactions for a user (empty list if unknown)."""
    return _transactions().get(user_id, [])


def retrieve_faq(query: str, k: int = 3) -> list[dict]:
    """Return up to k FAQ docs [{doc_id, text}] ranked by query token overlap.

    Deterministic: score ties are broken by the doc's order in the corpus.
    Docs with zero overlap are dropped.
    """
    q = _tokens(query)
    scored = []
    for i, doc in enumerate(_faq_docs()):
        score = len(q & _tokens(doc["text"]))
        if score:
            scored.append((-score, i, doc))
    scored.sort()
    return [{"doc_id": doc["doc_id"], "text": doc["text"]} for _, _, doc in scored[:k]]
