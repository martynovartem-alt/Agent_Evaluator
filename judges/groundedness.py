"""
Groundedness judge: does the answer make claims not supported by the captured trace?
Reads ONLY the trace context (answer + tool_calls + chunks) plus the client query — never
the operator answer (that is resolution's job) and never live tools.
Prompt: prompts/groundedness.md. Falls back to a stub when no judge LLM is available.

OO structure: `GroundednessJudge(LlmJudge)`; the module-level `judge_groundedness()` and
`_payload()` are the stable public seam used by runner.py and the tests.
"""
from judges.base import LlmJudge, error_info

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


class GroundednessJudge(LlmJudge):
    role = "groundedness"
    schema = _SCHEMA

    def payload(self, case: dict, trace: dict) -> dict:
        return _payload(case, trace)

    def shape(self, out: dict) -> dict:
        return _result(
            bool(out["has_unsupported_critical_claim"]),
            out.get("unsupported_claims", []),
            out.get("reasoning", ""),
        )

    def stub(self) -> dict:
        return _result(False, [], "[stub — no judge LLM]")

    def on_error(self, e: Exception) -> dict:
        # don't penalize the case for an infrastructure failure
        result = _result(False, [], f"[judge error: {e}]")
        result["error"] = error_info(e)
        return result


JUDGE = GroundednessJudge()


async def judge_groundedness(case: dict, trace: dict) -> dict:
    return await JUDGE.judge(case, trace)
