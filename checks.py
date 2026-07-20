"""
Deterministic checks — no LLM.
See docs/checks_spec.md for full specification.
"""


def check_tools_ok(case: dict, trace: dict) -> bool:
    """get_transactions called iff needs_transactions is true."""
    tool_names = [tc["name"] for tc in trace.get("tool_calls", [])]
    return ("get_transactions" in tool_names) == bool(case.get("needs_transactions"))


def check_must_facts(case: dict, trace: dict) -> dict[str, bool]:
    """Each must_fact must appear in the answer (case-insensitive substring)."""
    answer = (trace.get("answer") or "").lower()
    return {fact: fact.lower() in answer for fact in case.get("must_facts", [])}


def check_faq_doc(case: dict, trace: dict) -> bool | None:
    """expected_faq_doc must appear in retrieved chunk doc_ids. None = no expectation."""
    expected = case.get("expected_faq_doc")
    if not expected:
        return None
    retrieved = {ch["doc_id"] for ch in trace.get("chunks", [])}
    return expected in retrieved


def check_planted_txn(case: dict, trace: dict) -> bool | None:
    """planted_txn_id must appear in a get_transactions result. None = no expectation.

    Retrieval-side check for the MCP tool (mirror of faq_doc_ok): did the tool path
    actually surface the case's planted transaction, independent of the final answer.
    """
    planted = case.get("planted_txn_id")
    if not planted:
        return None
    for tc in trace.get("tool_calls", []):
        if tc.get("name") == "get_transactions":
            txns = tc.get("result", {}).get("transactions", [])
            if any(t.get("id") == planted for t in txns):
                return True
    return False


def run_checks(case: dict, trace: dict) -> dict:
    must_facts = check_must_facts(case, trace)
    return {
        "tools_ok": check_tools_ok(case, trace),
        "must_facts": must_facts,
        "all_must_facts_present": all(must_facts.values()) if must_facts else True,
        "faq_doc_ok": check_faq_doc(case, trace),
        "planted_txn_ok": check_planted_txn(case, trace),
    }
