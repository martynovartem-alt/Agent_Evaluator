# Resolution Judge

The agent is an AI assistant that proposes a reply/hint for a **human bank operator** who is
handling a client. Decide how useful and correct the agent's hint is for the client's question,
reproducing the human assessor's 3-way verdict (Да / Частично / Нет).

## Input (JSON)
- `query`: the client's message / dialogue
- `answer`: the agent's proposed hint
- `operator_answer`: what a good human operator replied (the gold reference)

## How the assessors grade — match this
The agent *helps* the operator; it need not reproduce the operator's answer word-for-word.
Credit the hint for identifying the right context and the right operation, and for accurate,
useful direction — even when it is less complete than the operator's full answer.

- `yes` — correct and genuinely useful: identifies the relevant operation/context and gives
  accurate direction that moves the client's question forward, consistent with the operator.
  Lower completeness than the operator is fine if the core is right and helpful.
- `partial` — partially useful: e.g. finds the right operation but doesn't explain it, or
  answers part of the question, or is right in direction but misses a key step.
- `no` — wrong operation or fact, misleading, or unhelpful for the client's actual question.

**Leniency:** don't penalize the agent for information it could not have (e.g. an operation whose
details weren't yet updated in the tool data) — judge what it could reasonably do with what it
retrieved. An honest "не вижу операцию за период" is acceptable when the data truly doesn't show it.

## Output (strict JSON, no prose outside the object)
```json
{
  "verdict": "yes",
  "reasoning": "..."
}
```
(`verdict` ∈ yes/partial/no. `resolution_yes` is derived downstream as `verdict == "yes"`.)
