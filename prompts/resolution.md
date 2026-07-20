# Resolution Judge

Evaluate whether the agent's answer resolves the user's query.

## Input (JSON)
- `query`: the user's question
- `answer`: the agent's response
- `operator_answer`: ground-truth correct answer

## Task
Judge whether `answer` conveys the same resolution as `operator_answer`.
Minor phrasing differences are acceptable. Missing key information is not.

## Output (strict JSON, no prose outside the object)
```json
{
  "resolution_yes": true,
  "reasoning": "..."
}
```
