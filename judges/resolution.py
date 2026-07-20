"""
Resolution judge: did the agent answer resolve the client's query?
Compared against operator_answer (ground truth). 3-way verdict to match the human labels
in the ground-truth set: yes | partial | no  (Да | Частично | Нет).
Reads ONLY the captured trace — never live tools.
Phase 4: replace stub with real Claude API call.
"""


async def judge_resolution(case: dict, trace: dict) -> dict:
    # TODO Phase 4: load prompts/resolution.md, call Claude API, parse JSON.
    verdict = "yes"
    return {
        "verdict": verdict,                 # yes | partial | no
        "resolution_yes": verdict == "yes",  # policy input (partial/no are not "solved")
        "reasoning": "[stub — Phase 4]",
    }
