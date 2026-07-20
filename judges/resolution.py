"""
Resolution judge: did the agent answer resolve the user's query?
Compared against operator_answer (ground truth).
Reads ONLY the captured trace — never live MCP/FAQ.
Phase 4: replace stub with real Claude API call.
"""


async def judge_resolution(case: dict, trace: dict) -> dict:
    # TODO Phase 4: load prompts/resolution.md, call Claude API (temp=0), parse JSON
    return {
        "resolution_yes": True,
        "reasoning": "[stub — Phase 4]",
    }
