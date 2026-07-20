# Resolution Judge

Decide whether the agent's reply resolves the client's question, judged against the
operator's reference answer. Both are Russian and prefixed `final_answer:` or `no_comments:`.

## Input (JSON)
- `query`: the client's message
- `answer`: the agent's reply
- `operator_answer`: the ground-truth human-agent reply

## Task
- A `final_answer:` reply resolves iff it conveys the same verified outcome as the operator
  (the operation, amount, or explanation), allowing phrasing differences.
- A `no_comments:` reply resolves iff the operator also treated the message as out-of-scope
  or nothing-to-add.
- An honest cannot-verify reply resolves iff the operator's answer is also a cannot-verify.
- A prefix mismatch (agent answers where the operator said no_comments, or vice versa) does
  not resolve.

## Output (strict JSON, no prose outside the object)
```json
{
  "resolution_yes": true,
  "reasoning": "..."
}
```
