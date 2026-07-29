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

OO structure: `ResolutionJudge(LlmJudge)` overrides judge() for the panel path; module-level
`judge_resolution()`, `_payload()`, `_shape()`, `majority_verdict()` are the stable public
seam used by runner.py, calibrate.py and the tests.
"""
import asyncio

import config
from judges import _llm
from judges.base import LlmJudge, error_info

# Why the answer is not correct — assessable from query/answer/operator_answer alone
# (tool misuse is not visible here; in the pipeline it's checks.py tools_ok, and
# hallucination-vs-trace is the groundedness judge).
FAILURE_REASONS = ["none", "wrong_operation", "hallucination", "incomplete",
                   "no_answer", "missed_data", "other"]

_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["yes", "partial", "no"]},
        "failure_reason": {"type": "string", "enum": FAILURE_REASONS},
        "reasoning": {"type": "string"},
    },
    "required": ["verdict", "failure_reason", "reasoning"],
    "additionalProperties": False,
}

_ORDER = {"no": 0, "partial": 1, "yes": 2}


def _payload(case: dict, trace: dict) -> dict:
    return {
        "query": case.get("query", ""),
        "answer": trace.get("answer", ""),
        "operator_answer": case.get("operator_answer", ""),
    }


def _norm_reason(verdict: str, reason: str) -> str:
    """Keep verdict and failure_reason consistent: a correct answer has no failure;
    a not-correct answer without a recognized reason is 'other'."""
    if verdict == "yes":
        return "none"
    return reason if reason in FAILURE_REASONS and reason != "none" else "other"


def _shape(verdict: str, reasoning: str, failure_reason: str = "none") -> dict:
    # resolution_yes (policy input) is derived here, not trusted from the model.
    return {"verdict": verdict, "resolution_yes": verdict == "yes",
            "failure_reason": _norm_reason(verdict, failure_reason), "reasoning": reasoning}


def majority_verdict(verdicts: list[str]) -> str:
    """Majority vote as the lower median on the ordinal scale no < partial < yes.
    Equals the majority verdict whenever one exists (any 2 of 3 agree); a full 3-way
    split resolves to "partial"; even splits lean conservative (away from "yes")."""
    ranked = sorted(verdicts, key=_ORDER.__getitem__)
    return ranked[(len(ranked) - 1) // 2]


class ResolutionJudge(LlmJudge):
    role = "resolution"
    schema = _SCHEMA

    def payload(self, case: dict, trace: dict) -> dict:
        return _payload(case, trace)

    def shape(self, out: dict) -> dict:
        verdict = out["verdict"] if out.get("verdict") in ("yes", "partial", "no") else "no"
        return _shape(verdict, out.get("reasoning", ""),
                      _norm_reason(verdict, out.get("failure_reason", "")))

    def stub(self) -> dict:
        return _shape("yes", "[stub — no judge LLM]")

    def on_error(self, e: Exception) -> dict:
        result = _shape("no", f"[judge error: {e}]", "other")
        result["error"] = error_info(e)
        return result

    async def _vote(self, spec: config.AgentSpec, case: dict, trace: dict) -> dict:
        """One panelist's vote. An error becomes a "no" vote (same semantics as the single
        judge) — with a panel, one transient failure no longer decides the case alone."""
        try:
            out = await _llm.judge_json(spec, spec.prompt_text(), self.payload(case, trace),
                                        self.schema)
            verdict = out["verdict"] if out.get("verdict") in ("yes", "partial", "no") else "no"
            reasoning = out.get("reasoning", "")
            reason, err = _norm_reason(verdict, out.get("failure_reason", "")), None
        except Exception as e:
            verdict, reasoning, reason, err = "no", f"[judge error: {e}]", "other", error_info(e)
        vote = {"judge": spec.name, "model": spec.model, "verdict": verdict,
                "failure_reason": reason, "reasoning": reasoning}
        if err:
            vote["error"] = err
        return vote

    @staticmethod
    def _panel_reason(verdict: str, votes: list[dict]) -> str:
        """Failure reason for the panel verdict: the most common reason among the panelists
        that voted not-correct (ties broken by FAILURE_REASONS order, deterministic)."""
        if verdict == "yes":
            return "none"
        reasons = [v["failure_reason"] for v in votes if v["verdict"] != "yes"]
        if not reasons:
            return "other"
        return min(set(reasons), key=lambda r: (-reasons.count(r), FAILURE_REASONS.index(r)))

    async def judge(self, case: dict, trace: dict) -> dict:
        spec = self.spec()
        if not spec.available():
            return self.stub()
        # A panelist that can never run (e.g. entry overriding mode/endpoint) is skipped
        # rather than voting a permanent "no"; the error→"no" in _vote is for transient failures.
        panel = [s for s in config.panel(self.role) if s.available()]
        if not panel:
            return await super().judge(case, trace)
        votes = list(await asyncio.gather(*(self._vote(s, case, trace) for s in panel)))
        verdict = majority_verdict([v["verdict"] for v in votes])
        tally = " ".join(f"{v['judge']}→{v['verdict']}" for v in votes)
        reasoning = f"panel: {tally} ⇒ {verdict}. " + " ".join(
            f"[{v['judge']}] {v['reasoning']}" for v in votes
        )
        result = _shape(verdict, reasoning, self._panel_reason(verdict, votes))
        result["votes"] = votes
        vote_errors = [v["error"] for v in votes if v.get("error")]
        if vote_errors and verdict != "yes":
            result["error"] = vote_errors[0]
        return result


JUDGE = ResolutionJudge()


async def judge_resolution(case: dict, trace: dict) -> dict:
    return await JUDGE.judge(case, trace)
