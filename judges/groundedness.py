"""
Groundedness judge: are claims in the agent answer supported by trace context?
Reads ONLY the captured trace — never live MCP/FAQ.
Phase 4: replace stub with real Claude API call.
"""


async def judge_groundedness(case: dict, trace: dict) -> dict:
    # TODO Phase 4: load prompts/groundedness.md, call Claude API (temp=0), parse JSON
    return {
        "has_unsupported_critical_claim": False,
        "unsupported_claims": [],
        "reasoning": "[stub — Phase 4]",
    }
