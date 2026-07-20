"""
Generate a fake MCP transaction dataset in the real shape (see json_answer_history_operations.md),
using operation/merchant names drawn from the real labeled set (current_agent_answers.xlsx).

Names only are reused (Яндекс Плюс, Пятёрочка, Альфа-Смарт, СБП transfers, commissions, ...);
all amounts, dates, ids, phone/account numbers are synthetic and masked — no customer data.

    python3 gen_mcp.py --users 5 --seed 7 --out data/mcp_fake.json

Amount encoding matches tools.py: value is in kopecks, minorUnits 100 (rubles = value / 100).
Output shape {user_id: [operation, ...]} mirrors fixtures/operations.json, so it can back a
larger golden set or be pointed at by MCPClear.
"""
import argparse
import json
import random
from datetime import date, datetime, timedelta

# (title, direction, operationType, systemCode, operationClass, merchant, mcc, min_rub, max_rub, fixed)
TEMPLATES = [
    ("Оплата «Пятёрочка»", "EXPENSE", "POS", "CARD_PAYMENT", "POSING", "Пятёрочка", 5411, 150, 3500, None),
    ("Оплата «Магнит»", "EXPENSE", "POS", "CARD_PAYMENT", "POSING", "Магнит", 5411, 120, 2600, None),
    ("Оплата «Wildberries»", "EXPENSE", "POS", "CARD_PAYMENT", "POSING", "Wildberries", 5399, 300, 8000, None),
    ("Оплата «Яндекс Плюс»", "EXPENSE", "SUBSCRIPTION", "RECURRENT_PAYMENT", "POSING", "Яндекс", 5968, 199, 399, None),
    ("Оплата «Иви»", "EXPENSE", "SUBSCRIPTION", "RECURRENT_PAYMENT", "POSING", "Иви", 5968, 299, 599, None),
    ("Оплата «Netflix»", "EXPENSE", "SUBSCRIPTION", "RECURRENT_PAYMENT", "POSING", "Netflix", 5968, 599, 799, None),
    ("Оплата «МТС»", "EXPENSE", "POS", "CARD_PAYMENT", "POSING", "МТС", 4814, 350, 900, None),
    ("Абонентская плата Альфа-Смарт", "EXPENSE", "FEE", "SUBSCRIPTION_FEE", "POSING", "Альфа-Банк", None, 299, 299, 299),
    ("Плата за Альфа-Чек", "EXPENSE", "FEE", "SUBSCRIPTION_FEE", "POSING", "Альфа-Банк", None, 99, 99, 99),
    ("Комиссия за обслуживание счёта", "EXPENSE", "FEE", "ACCOUNT_MAINTENANCE_FEE", "POSING", "Альфа-Банк", None, 99, 300, None),
    ("Перевод себе в другой банк через СБП", "EXPENSE", "C42", "FAST_PAYMENT_SYSTEM_TRANSFER_ME2ME", "POSING", "Другое", None, 500, 90000, None),
    ("Перевод через СБП", "EXPENSE", "C2C", "FAST_PAYMENT_SYSTEM_TRANSFER", "POSING", "Другое", None, 100, 40000, None),
    ("Снятие наличных в банкомате", "EXPENSE", "ATM", "ATM_WITHDRAWAL", "POSING", "Банкомат", None, 1000, 50000, None),
    ("Пополнение через СБП", "INCOME", "C2C", "FAST_PAYMENT_SYSTEM_TRANSFER", "POSING", "Другое", None, 1000, 120000, None),
    ("Возврат «Wildberries»", "INCOME", "REFUND", "CARD_REFUND", "POSING", "Wildberries", 5399, 300, 8000, None),
    ("Проценты на остаток", "INCOME", "INTEREST", "INTEREST_ACCRUAL", "POSING", "Альфа-Банк", None, 10, 1500, None),
]


def _uuid(rng: random.Random) -> str:
    s = f"{rng.getrandbits(128):032x}"
    return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}".upper()


def _operation(rng: random.Random, when: date, seq: int) -> dict:
    title, direction, op_type, system_code, op_class, merchant, mcc, lo, hi, fixed = rng.choice(TEMPLATES)
    rub = fixed if fixed is not None else rng.randint(lo, hi)
    stamp = datetime(when.year, when.month, when.day, rng.randint(6, 22), rng.randint(0, 59), rng.randint(0, 59))
    is_sbp = "СБП" in title
    if is_sbp:
        content = f"Перевод через Систему быстрых платежей на +7 (9{rng.randint(0,9)}{rng.randint(0,9)}) ***-**-**. Без НДС."
    elif op_type == "FEE":
        content = f"{title}. Без НДС."
    else:
        content = f"{title} по карте *{rng.randint(1000, 9999)}. Без НДС."
    return {
        "id": f"G{when.strftime('%y%m%d')}MOCOIPFBT{seq:05d}",
        "operationId": _uuid(rng),
        "halfTransactionKey": f"G{when.strftime('%y%m%d')}MOCOIPFBT{seq:05d}",
        "account": {"name": "Основной счёт", "number": f"40817810***{rng.randint(1000, 9999)}"},
        "beneficiaryAccount": {"name": None, "number": f"30232810***{rng.randint(1000, 9999)}" if is_sbp else None},
        "dateTime": stamp.isoformat() + "+03:00",
        "operationDate": when.isoformat(),
        "title": title,
        "amount": {"value": rub * 100, "currency": "RUR", "minorUnits": 100},
        "direction": direction,
        "operationType": op_type,
        "systemCode": system_code,
        "mcc": mcc,
        "operationContent": content,
        "cardDetails": {},
        "merchantDto": {"requestId": _uuid(rng), "id": "-1", "name": merchant},
        "operationClass": op_class,
        "fee": 0,
        "isPos": op_type == "POS",
        "filteringMultiValues": {"accounts": ["40817810***7020"], "operationSign": [direction]},
    }


def generate(seed: int = 7, n_users: int = 5, min_ops: int = 8, max_ops: int = 20,
             base_date: str = "2026-07-20", window_days: int = 90) -> dict:
    rng = random.Random(seed)
    base = date.fromisoformat(base_date)
    dataset = {}
    for u in range(1, n_users + 1):
        ops = []
        for seq in range(rng.randint(min_ops, max_ops)):
            when = base - timedelta(days=rng.randint(0, window_days))
            ops.append(_operation(rng, when, seq))
        ops.sort(key=lambda o: o["operationDate"])
        dataset[f"user_{u:04d}"] = ops
    return dataset


def main() -> None:
    p = argparse.ArgumentParser(description="Generate a fake MCP dataset from real operation names")
    p.add_argument("--users", type=int, default=5)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default="data/mcp_fake.json")
    args = p.parse_args()
    dataset = generate(seed=args.seed, n_users=args.users)
    with open(args.out, "w") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    total = sum(len(v) for v in dataset.values())
    print(f"wrote {len(dataset)} users, {total} operations to {args.out}")


if __name__ == "__main__":
    main()
