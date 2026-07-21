"""
Resolution judge: did the agent answer resolve the client's query?
3-way verdict (yes|partial|no ↔ Да/Частично/Нет) to match the human ground-truth labels.
Compared against operator_answer (ground truth); reads the trace answer, never live tools.
Prompt: prompts/resolution.md. Falls back to a stub when no judge LLM is available.
"""
import config
from judges import _llm

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["yes", "partial", "no"]},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "reasoning"],
    "additionalProperties": False,
}


def _payload(case: dict, trace: dict) -> dict:
    return {
        "query": case.get("query", ""),
        "answer": trace.get("answer", ""),
        "operator_answer": case.get("operator_answer", ""),
    }


def _shape(verdict: str, reasoning: str) -> dict:
    # resolution_yes (policy input) is derived here, not trusted from the model.
    return {"verdict": verdict, "resolution_yes": verdict == "yes", "reasoning": reasoning}


async def judge_resolution(case: dict, trace: dict) -> dict:
    spec = config.get("resolution")
    if not spec.available():
        return _shape("yes", "[stub — no judge LLM]")
    try:
        out = await _llm.judge_json(spec, spec.prompt_text(), _payload(case, trace), _SCHEMA)
        verdict = out["verdict"] if out.get("verdict") in ("yes", "partial", "no") else "no"
        return _shape(verdict, out.get("reasoning", ""))
    except Exception as e:
        return _shape("no", f"[judge error: {e}]")
