"""
The support agent under test (Alfa-Bank transaction consultant). Produces a trace per case:
    {case_id, answer, tool_calls[{name,args,result}], chunks[{doc_id,text}]}

Two implementations behind one run_agent() dispatcher:
- run_llm_agent:     real Claude agent (Anthropic API, tool-use loop). System prompt is
                     prompts/agent.md (derived from agent_prompt_v2.md). Production path.
- run_offline_agent: deterministic baseline that drives the SAME tool layer, so the whole
                     pipeline runs end-to-end with no API key.

Trace mapping (matches the architecture diagram: RAW MCP data + Chunks -> judges):
- MCPClear (history tool) calls        -> tool_calls[]
- getInstruction (grounding) results   -> chunks[{doc_id: topic_key, text}]

Mode via AGENT_MODE=auto|llm|offline (default auto). Model via AGENT_MODEL.
"""
import json
from datetime import date, timedelta

import config
from tools import get_instruction, mcp_clear, rubles

MAX_TOOL_ITERS = 6
DEFAULT_DATE = "2026-07-20"

TOOLS = [
    {
        "name": "MCPClear",
        "description": "Look up the client's transaction history for a date range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fromDate": {"type": "string", "description": "Start date yyyy-MM-dd (inclusive)"},
                "toDate": {"type": "string", "description": "End date yyyy-MM-dd (exclusive)"},
                "operationAmount": {"type": "integer", "description": "Whole-ruble amount filter (optional)"},
            },
            "required": ["fromDate", "toDate"],
        },
    },
    {
        "name": "getInstruction",
        "description": "Fetch verified explanations for topic keys (alfaSmart, alfaCheck, ...).",
        "input_schema": {
            "type": "object",
            "properties": {
                "instructionName": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["instructionName"],
        },
    },
]


def format_rub(amount: dict) -> str:
    r = rubles(amount)
    if r == int(r):
        return f"{int(r):,}".replace(",", " ")
    return f"{r:,.2f}".replace(",", " ").replace(".", ",")


def run_llm_agent(case: dict) -> dict:
    """Drive Claude through a manual tool-use loop, capturing the trace."""
    import anthropic

    spec = config.get("agent")
    client = anthropic.Anthropic(**config.client_kwargs(spec))
    system = spec.prompt_text().format(
        current_date=case.get("current_date", DEFAULT_DATE),
        user_id=case.get("fixture_user", ""),
    )
    messages = [{"role": "user", "content": case["query"]}]

    tool_calls: list[dict] = []
    chunks: list[dict] = []
    answer = ""

    for _ in range(MAX_TOOL_ITERS):
        response = client.messages.create(
            model=spec.model,
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
            if block.name == "MCPClear":
                result = mcp_clear(
                    case.get("fixture_user", ""),
                    block.input["fromDate"],
                    block.input["toDate"],
                    block.input.get("operationAmount"),
                )
                tool_calls.append({"name": block.name, "args": dict(block.input), "result": result})
            elif block.name == "getInstruction":
                instr = get_instruction(block.input.get("instructionName", []))
                chunks.extend({"doc_id": k, "text": v} for k, v in instr.items())
                result = instr
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
    """Deterministic baseline: wires the real tools by the case's routing hints.

    Trusts `needs_history` / `expected_instruction` rather than classifying intent, but
    exercises the full tool + trace + eval path so the harness is verifiable with no API key.
    """
    tool_calls: list[dict] = []
    chunks: list[dict] = []
    ops: list[dict] = []

    if case.get("needs_history"):
        cur = date.fromisoformat(case.get("current_date", DEFAULT_DATE))
        from_date, to_date = (cur - timedelta(days=85)).isoformat(), (cur + timedelta(days=1)).isoformat()
        result = mcp_clear(case.get("fixture_user", ""), from_date, to_date)
        tool_calls.append({
            "name": "MCPClear",
            "args": {"fromDate": from_date, "toDate": to_date},
            "result": result,
        })
        ops = result["operations"]

    if case.get("expected_instruction"):
        instr = get_instruction([case["expected_instruction"]])
        chunks = [{"doc_id": k, "text": v} for k, v in instr.items()]

    if ops:
        planted = case.get("planted_operation_id")
        op = next((o for o in ops if o["id"] == planted), None) or max(ops, key=lambda o: o["operationDate"])
        line = f"{op['title']} — {format_rub(op['amount'])} ₽ {op['operationDate']}"
        extra = f" {chunks[0]['text']}" if chunks else ""
        answer = f"final_answer: {line}.{extra}"
    elif chunks:
        answer = f"final_answer: {chunks[0]['text']}"
    else:
        answer = "no_comments: Рад был помочь."

    return {"case_id": case["id"], "answer": answer, "tool_calls": tool_calls, "chunks": chunks}


def run_agent(case: dict) -> dict:
    """Dispatch to the LLM agent or the offline baseline (see module docstring)."""
    spec = config.get("agent")
    if spec.mode == "offline":
        return run_offline_agent(case)
    if spec.mode == "llm":
        return run_llm_agent(case)
    return run_llm_agent(case) if spec.available() else run_offline_agent(case)
