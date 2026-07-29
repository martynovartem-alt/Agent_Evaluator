"""
The support agent under test (Alfa-Bank transaction consultant). Produces a trace per case:
    {case_id, answer, tool_calls[{name,args,result}], chunks[{doc_id,text}]}

OO structure — two implementations of one `Agent` interface behind the run_agent() dispatcher:
- LlmAgent:     real agent tool-use loop against the endpoint in config.get("agent") —
                Anthropic or OpenAI-compatible (the Sandbox). Production path.
- OfflineAgent: deterministic baseline that drives the SAME tool layer, so the whole
                pipeline runs end-to-end with no API key.

Trace mapping (matches the architecture diagram: RAW MCP data + Chunks -> judges):
- MCPClear (history tool) calls        -> tool_calls[]
- getInstruction (grounding) results   -> chunks[{doc_id: topic_key, text}]

Endpoint/model/prompt/provider/mode all come from agents.toml [agent] (see config.py).
Module-level run_agent()/run_llm_agent()/run_offline_agent() are the stable public seam.
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


def build_system(raw: str, current_date: str, user_id: str) -> str:
    """Inject runtime context into the agent's system prompt.

    Prompts that declare {current_date}/{user_id} (e.g. prompts/agent.md) are filled in place;
    a verbatim prompt without them (e.g. agent_prompt_v2.md) gets a prepended context header.
    Uses str.replace, never .format, so literal braces in the prompt are safe.
    """
    if "{current_date}" in raw or "{user_id}" in raw:
        return raw.replace("{current_date}", current_date).replace("{user_id}", user_id)
    return f"Current date: {current_date}. Current user id: {user_id}.\n\n{raw}"


def _execute_tool(name: str, args: dict, user_id: str, current_date: str,
                  tool_calls: list, chunks: list) -> dict:
    """Run a tool call and record it in the trace (MCPClear → tool_calls, getInstruction → chunks).
    Missing MCPClear dates default to the 85-day window so a malformed call doesn't return empty."""
    if name == "MCPClear":
        frm = args.get("fromDate") or (date.fromisoformat(current_date) - timedelta(days=85)).isoformat()
        to = args.get("toDate") or (date.fromisoformat(current_date) + timedelta(days=1)).isoformat()
        result = mcp_clear(user_id, frm, to, args.get("operationAmount"))
        tool_calls.append({"name": name, "args": dict(args), "result": result})
        return result
    if name == "getInstruction":
        instr = get_instruction(args.get("instructionName", []))
        chunks.extend({"doc_id": k, "text": v} for k, v in instr.items())
        return instr
    return {"error": f"unknown tool {name}"}


def _openai_tools() -> list:
    """TOOLS in OpenAI function-calling format."""
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"],
                                          "parameters": t["input_schema"]}}
        for t in TOOLS
    ]


class Agent:
    """One interface: a case in, a trace out."""

    def run(self, case: dict) -> dict:
        raise NotImplementedError


class LlmAgent(Agent):
    """Manual tool-use loop against the [agent] endpoint (Anthropic or OpenAI-compatible)."""

    def run(self, case: dict) -> dict:
        spec = config.get("agent")
        if spec.provider == "openai":
            return self._run_openai(case, spec)
        return self._run_anthropic(case, spec)

    @staticmethod
    def _context(case: dict) -> tuple[str, str]:
        return case.get("fixture_user", ""), case.get("current_date", DEFAULT_DATE)

    def _run_anthropic(self, case: dict, spec) -> dict:
        import anthropic

        client = anthropic.Anthropic(**config.client_kwargs(spec))
        uid, cd = self._context(case)
        system = build_system(spec.prompt_text(), cd, uid)
        messages = [{"role": "user", "content": case["query"]}]
        tool_calls, chunks, answer = [], [], ""

        for _ in range(MAX_TOOL_ITERS):
            response = client.messages.create(
                model=spec.model, max_tokens=4096, thinking={"type": "adaptive"},
                system=system, tools=TOOLS, messages=messages,
            )
            if response.stop_reason != "tool_use":
                answer = "".join(b.text for b in response.content if b.type == "text").strip()
                break
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = _execute_tool(block.name, dict(block.input), uid, cd, tool_calls, chunks)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id,
                                     "content": json.dumps(result, ensure_ascii=False)})
            messages.append({"role": "user", "content": tool_results})

        return {"case_id": case["id"], "answer": answer, "tool_calls": tool_calls, "chunks": chunks}

    def _run_openai(self, case: dict, spec) -> dict:
        import oai

        uid, cd = self._context(case)
        system = build_system(spec.prompt_text(), cd, uid)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": case["query"]}]
        tools, tool_calls, chunks, answer = _openai_tools(), [], [], ""

        for _ in range(MAX_TOOL_ITERS):
            msg = oai.chat(spec, messages, tools=tools, temperature=0.0, max_tokens=4096)
            tcs = msg.get("tool_calls")
            if not tcs:
                answer = (msg.get("content") or "").strip()
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                fn = tc["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _execute_tool(fn["name"], args, uid, cd, tool_calls, chunks)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "content": json.dumps(result, ensure_ascii=False)})

        return {"case_id": case["id"], "answer": answer, "tool_calls": tool_calls, "chunks": chunks}


class OfflineAgent(Agent):
    """Deterministic baseline: wires the real tools by the case's routing hints.

    Trusts `needs_history` / `expected_instruction` rather than classifying intent, but
    exercises the full tool + trace + eval path so the harness is verifiable with no API key.
    """

    def run(self, case: dict) -> dict:
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


_LLM_AGENT = LlmAgent()
_OFFLINE_AGENT = OfflineAgent()


def run_llm_agent(case: dict) -> dict:
    return _LLM_AGENT.run(case)


def run_offline_agent(case: dict) -> dict:
    return _OFFLINE_AGENT.run(case)


def run_agent(case: dict) -> dict:
    """Dispatch to the LLM agent or the offline baseline (see module docstring)."""
    spec = config.get("agent")
    if spec.mode == "offline":
        return _OFFLINE_AGENT.run(case)
    if spec.mode == "llm":
        return _LLM_AGENT.run(case)
    return _LLM_AGENT.run(case) if spec.available() else _OFFLINE_AGENT.run(case)
