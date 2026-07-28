"""
Resolution judge: did the agent answer resolve the client's query?
3-way verdict (yes|partial|no ↔ Да/Частично/Нет) to match the human ground-truth labels.
Compared against operator_answer (ground truth); reads the trace answer, never live tools.
Prompt: prompts/resolution.md. Falls back to a stub when no judge LLM is available.

Panel voting ("trial"): with [[resolution.panel]] entries in agents.toml, the panelists
(neutral judge / prosecutor / defender — same verdict definitions, different scrutiny) vote
concurrently and the majority wins; a full yes/partial/no split lands on "partial" (ordinal
median). Per-vote details are kept in the result's `votes[]`. RESOLUTION_PANEL=off → single
judge, for A/B against the panel in calibrate.py.
"""
import asyncio

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

_ORDER = {"no": 0, "partial": 1, "yes": 2}


def _payload(case: dict, trace: dict) -> dict:
    return {
        "query": case.get("query", ""),
        "answer": trace.get("answer", ""),
        "operator_answer": case.get("operator_answer", ""),
    }


def _shape(verdict: str, reasoning: str) -> dict:
    # resolution_yes (policy input) is derived here, not trusted from the model.
    return {"verdict": verdict, "resolution_yes": verdict == "yes", "reasoning": reasoning}


def majority_verdict(verdicts: list[str]) -> str:
    """Majority vote as the lower median on the ordinal scale no < partial < yes.
    Equals the majority verdict whenever one exists (any 2 of 3 agree); a full 3-way
    split resolves to "partial"; even splits lean conservative (away from "yes")."""
    ranked = sorted(verdicts, key=_ORDER.__getitem__)
    return ranked[(len(ranked) - 1) // 2]


async def _vote(spec: config.AgentSpec, case: dict, trace: dict) -> dict:
    """One panelist's vote. An error becomes a "no" vote (same semantics as the single
    judge) — with a panel, one transient failure no longer decides the case alone."""
    try:
        out = await _llm.judge_json(spec, spec.prompt_text(), _payload(case, trace), _SCHEMA)
        verdict = out["verdict"] if out.get("verdict") in ("yes", "partial", "no") else "no"
        reasoning = out.get("reasoning", "")
    except Exception as e:
        verdict, reasoning = "no", f"[judge error: {e}]"
    return {"judge": spec.name, "model": spec.model, "verdict": verdict, "reasoning": reasoning}


async def judge_resolution(case: dict, trace: dict) -> dict:
    spec = config.get("resolution")
    if not spec.available():
        return _shape("yes", "[stub — no judge LLM]")
    # A panelist that can never run (e.g. entry overriding mode/endpoint) is skipped rather
    # than voting a permanent "no"; the error→"no" path in _vote is for transient failures.
    panel = [s for s in config.panel("resolution") if s.available()]
    if not panel:
        vote = await _vote(spec, case, trace)
        return _shape(vote["verdict"], vote["reasoning"])
    votes = list(await asyncio.gather(*(_vote(s, case, trace) for s in panel)))
    verdict = majority_verdict([v["verdict"] for v in votes])
    tally = " ".join(f"{v['judge']}→{v['verdict']}" for v in votes)
    reasoning = f"panel: {tally} ⇒ {verdict}. " + " ".join(
        f"[{v['judge']}] {v['reasoning']}" for v in votes
    )
    result = _shape(verdict, reasoning)
    result["votes"] = votes
    return result
