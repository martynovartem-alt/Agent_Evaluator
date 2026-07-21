"""
Build a runner eval dataset from the real client questions in the ground-truth set.

Takes data/labeled.jsonl (from dataset.py ← current_agent_answers.xlsx), extracts each
dialogue's CLIENT turns as the query, and points the case at a synthetic MCP user so the
agent (prompts/agent.md, derived from agent_prompt_v2.md) answers from the fake 200×200 data.

    python3 build_eval.py --limit 30 --out data/eval_prompt.jsonl

Output is a golden-style dataset for runner.py. There are no deterministic ground-truth
annotations (must_facts / planted_operation_id / expected_instruction stay empty) because the
answers come from synthetic data — so the fair signals are the LLM judges (groundedness,
resolution) and tool use. operator_answer is carried for the resolution judge (see caveat in
the response). Real customer text → output is gitignored.
"""
import argparse
import json
import re
from pathlib import Path

N_USERS = 200          # matches data/mcp_fake.json
CURRENT_DATE = "2026-07-20"

_HISTORY_HINTS = (
    "списан", "списал", "перевод", "оплат", "возврат", "комисс", "деньг", "руб", "₽",
    "операц", "снятие", "пополн", "подписк", "платеж", "платёж", "чек", "тариф",
    "начислен", "процент", "баланс", "счёт", "счет", "карт",
)


def client_turns(dialogue: str) -> str:
    """Join the CLIENT utterances from a 'CLIENT (..): .. OPERATOR (..): ..' dialogue."""
    parts = re.split(r"(CLIENT|OPERATOR)\s*\([^)]*\):", dialogue)
    turns = [
        parts[i + 1].strip().strip("/").strip()
        for i in range(1, len(parts) - 1, 2)
        if parts[i] == "CLIENT" and parts[i + 1].strip().strip("/").strip()
    ]
    return " ".join(turns) or dialogue.strip()


def needs_history(text: str) -> bool:
    low = text.lower()
    return any(h in low for h in _HISTORY_HINTS)


def build(rows: list[dict]) -> list[dict]:
    cases = []
    for i, row in enumerate(rows):
        query = client_turns(row.get("dialogue", ""))
        cases.append({
            "id": row.get("id", f"row_{i}"),
            "query": query,
            "intent": "from_xlsx",
            "current_date": CURRENT_DATE,
            "operator_answer": row.get("operator_answer", ""),
            "must_facts": [],
            "needs_history": needs_history(query),
            "fixture_user": f"user_{(i % N_USERS) + 1:04d}",
            "planted_operation_id": None,
            "expected_instruction": None,
        })
    return cases


def main() -> None:
    p = argparse.ArgumentParser(description="Build a runner eval set from real client questions")
    p.add_argument("--in", dest="inp", default="data/labeled.jsonl")
    p.add_argument("--out", default="data/eval_prompt.jsonl")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    rows = [json.loads(l) for l in Path(args.inp).read_text().splitlines() if l.strip()]
    if args.limit:
        rows = rows[:args.limit]
    cases = build(rows)
    with open(args.out, "w") as f:
        for case in cases:
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
    hist = sum(1 for c in cases if c["needs_history"])
    print(f"wrote {len(cases)} cases to {args.out} ({hist} need history, {len(cases) - hist} not)")


if __name__ == "__main__":
    main()
