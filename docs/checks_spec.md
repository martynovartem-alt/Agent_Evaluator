# checks.py Specification

Deterministic checks — no LLM. All functions are pure `(case, trace) → result`.

## Checks

### tools_ok
`get_transactions` was called iff `case.needs_transactions == true`.
- Called when not needed → FAIL
- Not called when needed → FAIL

### must_facts
Every string in `case.must_facts[]` appears as a substring in `trace.answer` (case-insensitive).
Returns per-fact dict + `all_must_facts_present` boolean.

### faq_doc_ok
`case.expected_faq_doc` appears in `trace.chunks[].doc_id`.
Returns `None` when `expected_faq_doc` is null (no expectation for this case).

## Policy (lives in aggregate.py, not checks.py)

```
solved = resolution_yes AND NOT has_unsupported_critical_claim AND tools_ok
```
