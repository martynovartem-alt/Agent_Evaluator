"""
Deterministic checks — no LLM.
See docs/checks_spec.md for full specification.
"""


def check_tools_ok(case: dict, trace: dict) -> bool:
    """MCPClear called iff needs_history is true."""
    tool_names = [tc["name"] for tc in trace.get("tool_calls", [])]
    return ("MCPClear" in tool_names) == bool(case.get("needs_history"))


def check_must_facts(case: dict, trace: dict) -> dict[str, bool]:
    """Each must_fact must appear in the answer (case-insensitive substring)."""
    answer = (trace.get("answer") or "").lower()
    return {fact: fact.lower() in answer for fact in case.get("must_facts", [])}


def check_instruction_ok(case: dict, trace: dict) -> bool | None:
    """expected_instruction must appear among retrieved chunk doc_ids. None = no expectation."""
    expected = case.get("expected_instruction")
    if not expected:
        return None
    retrieved = {ch["doc_id"] for ch in trace.get("chunks", [])}
    return expected in retrieved


def check_planted_operation(case: dict, trace: dict) -> bool | None:
    """planted_operation_id must appear in an MCPClear result. None = no expectation.

    Retrieval-side check for the MCP tool (mirror of instruction_ok): did the tool path
    actually surface the case's planted operation, independent of the final answer.
    """
    planted = case.get("planted_operation_id")
    if not planted:
        return None
    for tc in trace.get("tool_calls", []):
        if tc.get("name") == "MCPClear":
            ops = tc.get("result", {}).get("operations", [])
            if any(o.get("id") == planted for o in ops):
                return True
    return False


def run_checks(case: dict, trace: dict) -> dict:
    must_facts = check_must_facts(case, trace)
    return {
        "tools_ok": check_tools_ok(case, trace),
        "must_facts": must_facts,
        "all_must_facts_present": all(must_facts.values()) if must_facts else True,
        "instruction_ok": check_instruction_ok(case, trace),
        "planted_operation_ok": check_planted_operation(case, trace),
    }
