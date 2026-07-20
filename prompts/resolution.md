# Resolution Judge

Decide whether the agent's reply resolves the client's question, judged against the
operator's reference answer. Reproduce the human assessor's 3-way verdict.

## Input (JSON)
- `query`: the client's message / full dialogue
- `answer`: the agent's reply
- `operator_answer`: the ground-truth operator reply

## Task
Return one verdict, matching how a human assessor labels these (Да / Частично / Нет):
- `yes` — conveys the same verified outcome as the operator (the operation, amount, or
  correct next step), allowing phrasing differences.
- `partial` — partially right or partially useful: correct topic but missing a key specific,
  or a right step with a wrong/unverified detail.
- `no` — wrong, unhelpful, or resolves a different problem than the client asked about.

An honest cannot-verify reply is `yes` only if the operator's answer is also a cannot-verify.
A prefix mismatch (`final_answer:` vs `no_comments:`) that changes the outcome is `no`.

## Output (strict JSON, no prose outside the object)
```json
{
  "verdict": "yes",
  "resolution_yes": true,
  "reasoning": "..."
}
```
`resolution_yes` must equal `verdict == "yes"`.
