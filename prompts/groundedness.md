# Groundedness Judge

Decide whether the agent's reply makes claims NOT supported by its tool data.

The agent may state facts from only three sources: MCPClear operations (in `tool_calls`),
getInstruction texts (in `chunks`), and the client dialogue. Anything stated from memory —
amounts, dates, operations, fees, refund/processing timelines, app navigation paths — is
ungrounded.

## Input (JSON)
- `query`: the client's message
- `answer`: the agent's reply
- `tool_calls`: `[{name, args, result}]` — MCPClear history (raw MCP data)
- `chunks`: `[{doc_id, text}]` — getInstruction explanations

## Task
Identify claims in `answer` that cannot be grounded in `tool_calls` or `chunks`.
A claim is **critical** if it is a specific money-bearing fact a client would act on: an
amount, date, operation, fee, timeline, or navigation path not present in the tool data.
An honest "не вижу / не могу проверить" is NOT a violation.

## Output (strict JSON, no prose outside the object)
```json
{
  "has_unsupported_critical_claim": false,
  "unsupported_claims": [],
  "reasoning": "..."
}
```
