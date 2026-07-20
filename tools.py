"""
The support agent's two tools, backed by deterministic fixtures in the real MCP shape.

- MCPClear:      transaction-history lookup. Returns {operations[], summary} where each
                 operation matches the real MCP JSON (see json_answer_history_operations.md).
                 The offline fixture adapter; in production this is served over MCP.
- getInstruction: verified explanations keyed by topic (alfaSmart, alfaCheck, ...).

Both read only from fixtures/ — no network — so every run is reproducible.

Amount encoding: operation `amount` is {value, currency, minorUnits}; rubles = value / minorUnits
(value is in minor units / kopecks — e.g. the 299 ₽ Альфа-Смарт fee is value 29900, minorUnits 100).
"""
import json
from functools import lru_cache
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@lru_cache(maxsize=1)
def _operations() -> dict:
    return json.loads((FIXTURES / "operations.json").read_text())


@lru_cache(maxsize=1)
def _instructions() -> dict:
    return json.loads((FIXTURES / "instructions.json").read_text())


def rubles(amount: dict) -> float:
    """Convert an MCP amount object to rubles (value is in minor units)."""
    return amount["value"] / amount["minorUnits"]


def _summary(ops: list[dict]) -> dict:
    return {
        "operationsCount": len(ops),
        "totalExpense": sum(rubles(o["amount"]) for o in ops if o["direction"] == "EXPENSE"),
        "totalIncome": sum(rubles(o["amount"]) for o in ops if o["direction"] == "INCOME"),
    }


def mcp_clear(user_id: str, from_date: str, to_date: str, operation_amount: int | None = None) -> dict:
    """Transaction history for [from_date, to_date) (yyyy-MM-dd; to_date exclusive).

    operation_amount (whole rubles) optionally filters to operations of exactly that value.
    Returns {operations[], summary{operationsCount, totalExpense, totalIncome}}.
    """
    ops = [
        o for o in _operations().get(user_id, [])
        if from_date <= o["operationDate"] < to_date
    ]
    if operation_amount is not None:
        ops = [o for o in ops if rubles(o["amount"]) == operation_amount]
    return {"operations": ops, "summary": _summary(ops)}


def get_instruction(instruction_names: list[str]) -> dict[str, str]:
    """Return verified instruction texts for the given topic keys (unknown keys omitted)."""
    data = _instructions()
    return {name: data[name] for name in instruction_names if name in data}
