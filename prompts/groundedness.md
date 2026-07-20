# Groundedness Judge

Evaluate whether the agent's answer contains claims NOT supported by the retrieved context.

## Input (JSON)
- `answer`: the agent's response
- `chunks`: list of `{doc_id, text}` retrieved from FAQ
- `tool_calls`: list of `{name, args, result}` from MCP tools

## Task
Identify claims in `answer` that cannot be grounded in `chunks` or `tool_calls`.
A claim is **critical** if it is a specific fact (amount, date, name, status) a user would rely on.

## Output (strict JSON, no prose outside the object)
```json
{
  "has_unsupported_critical_claim": false,
  "unsupported_claims": [],
  "reasoning": "..."
}
```
