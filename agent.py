"""
The support agent under test. Produces a trace per case:
    {case_id, answer, tool_calls[{name,args,result}], chunks[{doc_id,text}]}

Two implementations behind one run_agent() dispatcher:
- run_llm_agent:     real Claude agent (Anthropic API, tool-use loop). Production path.
- run_offline_agent: deterministic rule-based baseline that drives the SAME tool layer.
                     Lets the whole pipeline run end-to-end with no API key (CI, local dev).

Both call the real tools in tools.py. Trace mapping (fixed schema in CLAUDE.md):
- get_transactions (MCP tool) calls  -> tool_calls[]
- FAQ retrieval (search_faq) results -> chunks[]

Mode is chosen by AGENT_MODE=auto|llm|offline (default auto: LLM iff an API key and
the anthropic SDK are both available, else offline). Model via AGENT_MODEL.
"""
import json
import os

from tools import get_transactions, retrieve_faq

MODEL = os.getenv("AGENT_MODEL", "claude-opus-4-8")
MAX_TOOL_ITERS = 6

SYSTEM_PROMPT = (
    "You are a customer support agent. Answer the user's question concisely, using ONLY "
    "information returned by your tools — never invent account details.\n"
    "- get_transactions: look up the current user's transactions.\n"
    "- search_faq: search the help center for policy/how-to answers.\n"
    "The current user's id is {user_id}. Call the tools you need, then give a direct answer."
)

TOOLS = [
    {
        "name": "get_transactions",
        "description": "Look up a user's transaction history.",
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "string", "description": "The user's id"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "search_faq",
        "description": "Search the help center FAQ for an answer to a support question.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The support question"}},
            "required": ["query"],
        },
    },
]


def _anthropic_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def run_llm_agent(case: dict) -> dict:
    """Drive Claude through a manual tool-use loop, capturing the trace."""
    import anthropic

    client = anthropic.Anthropic()
    system = SYSTEM_PROMPT.format(user_id=case.get("fixture_user", ""))
    messages = [{"role": "user", "content": case["query"]}]

    tool_calls: list[dict] = []
    chunks: list[dict] = []
    answer = ""

    for _ in range(MAX_TOOL_ITERS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            answer = "".join(b.text for b in response.content if b.type == "text").strip()
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "get_transactions":
                result = {"transactions": get_transactions(block.input.get("user_id", ""))}
                tool_calls.append({"name": block.name, "args": dict(block.input), "result": result})
            elif block.name == "search_faq":
                hits = retrieve_faq(block.input.get("query", ""))
                chunks.extend(hits)
                result = {"chunks": hits}
            else:
                result = {"error": f"unknown tool {block.name}"}
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        messages.append({"role": "user", "content": tool_results})

    return {"case_id": case["id"], "answer": answer, "tool_calls": tool_calls, "chunks": chunks}


def run_offline_agent(case: dict) -> dict:
    """Deterministic baseline: wires the real tools by the case's routing hint.

    Not a stand-in for the LLM's judgment — it trusts `needs_transactions` rather than
    classifying intent — but it exercises the full tool + trace + eval path so the
    harness is verifiable without an API key.
    """
    chunks = retrieve_faq(case["query"])
    tool_calls: list[dict] = []
    answer = None

    if case.get("needs_transactions"):
        user_id = case.get("fixture_user", "")
        txns = get_transactions(user_id)
        tool_calls.append({
            "name": "get_transactions",
            "args": {"user_id": user_id},
            "result": {"transactions": txns},
        })
        if txns:
            latest = max(txns, key=lambda t: t["date"])
            answer = (
                f"Your most recent transaction was a payment of ${latest['amount']} "
                f"to {latest['merchant']} on {latest['date']}."
            )

    if answer is None:
        answer = chunks[0]["text"] if chunks else "I'm sorry, I couldn't find an answer to that."

    return {"case_id": case["id"], "answer": answer, "tool_calls": tool_calls, "chunks": chunks}


def run_agent(case: dict) -> dict:
    """Dispatch to the LLM agent or the offline baseline (see module docstring)."""
    mode = os.getenv("AGENT_MODE", "auto")
    if mode == "offline":
        return run_offline_agent(case)
    if mode == "llm":
        return run_llm_agent(case)
    if os.getenv("ANTHROPIC_API_KEY") and _anthropic_available():
        return run_llm_agent(case)
    return run_offline_agent(case)
