# checks.py Specification

Deterministic checks — no LLM. All functions are pure `(case, trace) → result`.

## Checks

### tools_ok
`MCPClear` was called iff `case.needs_history == true`.
- Called when not needed → FAIL
- Not called when needed → FAIL

### must_facts
Every string in `case.must_facts[]` appears as a substring in `trace.answer` (case-insensitive).
Returns per-fact dict + `all_must_facts_present` boolean. Works for Russian facts.

### instruction_ok
`case.expected_instruction` (a getInstruction topic key) appears in `trace.chunks[].doc_id`.
Returns `None` when `expected_instruction` is null (no expectation for this case).

### planted_operation_ok
`case.planted_operation_id` appears as an operation `id` in some `MCPClear` result in
`trace.tool_calls[]`. Retrieval-side check for the MCP tool (mirror of `instruction_ok`):
verifies the tool path surfaced the planted operation, independent of the answer.
Returns `None` when `planted_operation_id` is null.

## Policy (lives in aggregate.py, not checks.py)

```
solved = resolution_yes AND NOT has_unsupported_critical_claim AND tools_ok
```
