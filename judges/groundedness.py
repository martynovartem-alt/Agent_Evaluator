"""
Groundedness judge: does the answer make claims not supported by the captured trace?
Reads ONLY the trace context (answer + tool_calls + chunks) plus the client query — never
the operator answer (that is resolution's job) and never live tools.
Prompt: prompts/groundedness.md. Falls back to a stub when no judge LLM is available.
"""
from judges import _llm

_SCHEMA = {
    "type": "object",
    "properties": {
        "has_unsupported_critical_claim": {"type": "boolean"},
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
    "required": ["has_unsupported_critical_claim", "unsupported_claims", "reasoning"],
    "additionalProperties": False,
}


def _payload(case: dict, trace: dict) -> dict:
    return {
        "query": case.get("query", ""),
        "answer": trace.get("answer", ""),
        "tool_calls": trace.get("tool_calls", []),
        "chunks": trace.get("chunks", []),
    }


def _result(has_claim: bool, claims: list, reasoning: str) -> dict:
    return {
        "has_unsupported_critical_claim": has_claim,
        "unsupported_claims": claims,
        "reasoning": reasoning,
    }


async def judge_groundedness(case: dict, trace: dict) -> dict:
    if not _llm.available():
        return _result(False, [], "[stub — no judge LLM]")
    try:
        out = await _llm.judge_json(_llm.load_prompt("groundedness.md"), _payload(case, trace), _SCHEMA)
        return _result(
            bool(out["has_unsupported_critical_claim"]),
            out.get("unsupported_claims", []),
            out.get("reasoning", ""),
        )
    except Exception as e:  # don't let one row kill a batch; surface the error, don't penalize
        return _result(False, [], f"[judge error: {e}]")
