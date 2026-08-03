"""
Mock client identities + per-client MCP transaction histories for the labeled xlsx.

Reads the assessors' workbook (default: Agents-new-answers(after_20_07_2026).xlsx), where
`Client_Name (Mock)` (col A) already holds 6-char client codes for most rows and
`Clietn_id(mock)` (col I) is empty, and:

1. Assigns a client to every content row. Existing codes are kept verbatim; a row without
   a code reuses the code of an identical dialogue when one exists, else gets a fresh
   `C`-prefixed code (existing codes start with A/B, so new ones are recognizable).
2. Gives each client a 13-digit id, a masked account `40817810***NNNN`, and an
   `account.name` blob with the id digits embedded — the exact shapes in MCP_Answer_Json.
3. Builds a 90-day mock history per client in the MCP answer format ({"operations":[...]},
   fields per MCP_Answer_Json / json_answer_history_operations.md): operations planted
   from what the client's dialogues discuss (ruble amounts, merchant/type keywords,
   explicit "12 июля"-style dates; FEE/SUBSCRIPTION plants recur ~monthly) plus fillers
   from gen_mcp's templates, ≥ --min-ops total, all within the 90 days ending at the
   client's latest dialogue date. Amounts are kopeck-encoded (value / minorUnits).
4. Writes one file per client + index.json into --out (default /tmp/mcp_mock_clients —
   OUTSIDE the repo), and fills xlsx columns A/I in place (backup copy in the system temp
   dir first). Only synthetic values are written: the mock dir carries no dialogue text,
   so it is safe to share; the xlsx itself remains real data — keep it out of git.

    python3 gen_clients.py                       # default xlsx → /tmp/mcp_mock_clients
    python3 gen_clients.py --dry-run             # report only, write nothing
    python3 gen_clients.py --skip-xlsx           # regenerate the mock dir only

Deterministic: same --seed (and unchanged xlsx) → same codes, ids and operations.
"""
import argparse
import json
import random
import re
import shutil
import string
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

import dataset as ds
from gen_mcp import _operation, _uuid

DEFAULT_XLSX = "Agents-new-answers(after_20_07_2026).xlsx"
CODE_CHARS = string.ascii_uppercase + string.digits

# Columns of the labeled sheet (0-based): A=client code, B=agent answer, C=date,
# D=operator answer, E=dialogue, I=client id.
COL_CODE, COL_AGENT, COL_DATE, COL_OPERATOR, COL_DIALOGUE, COL_ID = 0, 1, 2, 3, 4, 8

# ── what a dialogue mentions → what to plant ─────────────────────────────────────────
# (title, direction, operationType, systemCode, merchant, mcc, default_rub)
PLANT = {
    "alfa_smart": ("Абонентская плата Альфа-Смарт", "EXPENSE", "FEE", "SUBSCRIPTION_FEE", "Альфа-Банк", None, 299),
    "alfa_check": ("Плата за Альфа-Чек", "EXPENSE", "FEE", "SUBSCRIPTION_FEE", "Альфа-Банк", None, 99),
    "insurance": ("Оплата страховки «АльфаСтрахование»", "EXPENSE", "POS", "CARD_PAYMENT", "АльфаСтрахование", 6300, 1200),
    "credit": ("Платёж по кредиту", "EXPENSE", "LOAN", "LOAN_PAYMENT", "Альфа-Банк", None, 5000),
    "commission": ("Комиссия за обслуживание счёта", "EXPENSE", "FEE", "ACCOUNT_MAINTENANCE_FEE", "Альфа-Банк", None, 199),
    "cashback": ("Кэшбэк за покупки", "INCOME", "CASHBACK", "CASHBACK_ACCRUAL", "Альфа-Банк", None, 250),
    "interest": ("Проценты на остаток", "INCOME", "INTEREST", "INTEREST_ACCRUAL", "Альфа-Банк", None, 320),
    "topup": ("Пополнение через СБП", "INCOME", "C2C", "FAST_PAYMENT_SYSTEM_TRANSFER", "Другое", None, 5000),
    "sbp_me2me": ("Перевод себе в другой банк через СБП", "EXPENSE", "C42", "FAST_PAYMENT_SYSTEM_TRANSFER_ME2ME", "Другое", None, 3000),
    "sbp_out": ("Перевод через СБП", "EXPENSE", "C2C", "FAST_PAYMENT_SYSTEM_TRANSFER", "Другое", None, 2500),
    "atm": ("Снятие наличных в банкомате", "EXPENSE", "ATM", "ATM_WITHDRAWAL", "Банкомат", None, 5000),
    "yandex": ("Оплата «Яндекс Плюс»", "EXPENSE", "SUBSCRIPTION", "RECURRENT_PAYMENT", "Яндекс", 5968, 299),
    "pyaterochka": ("Оплата «Пятёрочка»", "EXPENSE", "POS", "CARD_PAYMENT", "Пятёрочка", 5411, 780),
    "magnit": ("Оплата «Магнит»", "EXPENSE", "POS", "CARD_PAYMENT", "Магнит", 5411, 540),
    "wb": ("Оплата «Wildberries»", "EXPENSE", "POS", "CARD_PAYMENT", "Wildberries", 5399, 1900),
    "ozon": ("Оплата «Ozon»", "EXPENSE", "POS", "CARD_PAYMENT", "Ozon", 5399, 1400),
    "mts": ("Оплата «МТС»", "EXPENSE", "POS", "CARD_PAYMENT", "МТС", 4814, 450),
    "subscription": ("Оплата «Иви»", "EXPENSE", "SUBSCRIPTION", "RECURRENT_PAYMENT", "Иви", 5968, 399),
    "generic": ("Оплата покупки", "EXPENSE", "POS", "CARD_PAYMENT", "Другое", 5999, 500),
}
# Ordered: specific brands/products before generic verbs — first match pairs with the
# first extracted amount, so specificity wins.
KEYWORDS = [
    (re.compile(r"альфа[\s-]?смарт", re.I), "alfa_smart"),
    (re.compile(r"альфа[\s-]?чек", re.I), "alfa_check"),
    (re.compile(r"страхов|альфазащитник|будь здоров|членск\w* взнос", re.I), "insurance"),
    (re.compile(r"кэшб[еэ]к", re.I), "cashback"),
    (re.compile(r"кредит", re.I), "credit"),
    (re.compile(r"процент", re.I), "interest"),
    (re.compile(r"комисси", re.I), "commission"),
    (re.compile(r"яндекс", re.I), "yandex"),
    (re.compile(r"пят[её]рочк", re.I), "pyaterochka"),
    (re.compile(r"магнит(?!н)", re.I), "magnit"),
    (re.compile(r"wildberries|вайлдберр|валдберр", re.I), "wb"),
    (re.compile(r"озон|ozon", re.I), "ozon"),
    (re.compile(r"мтс", re.I), "mts"),
    (re.compile(r"снял|снят|наличн|банкомат", re.I), "atm"),
    (re.compile(r"в другой банк", re.I), "sbp_me2me"),
    (re.compile(r"сбп|быстрых платеж", re.I), "sbp_out"),
    (re.compile(r"пополн|поступ|зачисл|пришл", re.I), "topup"),
    (re.compile(r"подписк", re.I), "subscription"),
    (re.compile(r"перев[её]л|перевод", re.I), "sbp_out"),
    (re.compile(r"списа|оплат|плат[её]ж", re.I), "generic"),
]

AMOUNT_RE = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(?:₽|руб\w*|р\.)", re.I)
MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
DATE_WORD_RE = re.compile(
    r"\b(\d{1,2})(?:-?го)?\s+(январ|феврал|март|апрел|ма[яе]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*",
    re.I)
DATE_NUM_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?(?!\d|[.,]\d|\s*%)\b")


def extract_amounts(text: str, cap: int = 4) -> list[int]:
    """Currency-marked amounts, in kopecks, deduped in order of appearance."""
    out = []
    for whole, frac in AMOUNT_RE.findall(text):
        rub = int(re.sub(r"\D", "", whole))
        kop = rub * 100 + (int(frac.ljust(2, "0")) if frac else 0)
        if 100 <= kop <= 200_000_000 and kop not in out:   # 1 ₽ .. 2 M ₽
            out.append(kop)
    return out[:cap]


def extract_keywords(text: str, cap: int = 4) -> list[str]:
    out = []
    for pat, key in KEYWORDS:
        if key not in out and pat.search(text):
            out.append(key)
    return out[:cap]


def extract_date(text: str, row_date: date) -> date | None:
    """First explicit '12 июля' / '12.07[.2026]' mention resolved to a past date."""
    m = DATE_WORD_RE.search(text)
    if m:
        word = m.group(2).lower()
        day = int(m.group(1))
        # longest prefix first, so «март» can never be shadowed by «ма» (мая)
        month = MONTHS[next(k for k in sorted(MONTHS, key=len, reverse=True) if word.startswith(k))]
    else:
        m = DATE_NUM_RE.search(text)
        if not m:
            return None
        day, month = int(m.group(1)), int(m.group(2))
        if m.group(3) and not 2020 <= int(m.group(3)) <= 2030:
            return None
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    for year in (row_date.year, row_date.year - 1):
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        if d <= row_date:
            return d
    return None


def make_code(rng: random.Random, taken: set[str]) -> str:
    while True:
        code = "C" + "".join(rng.choice(CODE_CHARS) for _ in range(5))
        if code not in taken:
            taken.add(code)
            return code


def blob(rng: random.Random, n: int) -> str:
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(n))


def planted_op(rng: random.Random, key: str, kop: int | None, when: date, seq: int) -> dict:
    title, direction, op_type, system_code, merchant, mcc, default_rub = PLANT[key]
    kop = kop if kop is not None else default_rub * 100
    stamp = datetime(when.year, when.month, when.day,
                     rng.randint(6, 22), rng.randint(0, 59), rng.randint(0, 59))
    if "СБП" in title:
        content = f"Перевод через Систему быстрых платежей на +7 (9{rng.randint(0, 9)}{rng.randint(0, 9)}) ***-**-**. Без НДС."
    elif op_type in ("FEE", "LOAN", "CASHBACK", "INTEREST"):
        content = f"{title}. Без НДС."
    else:
        content = f"{title} по карте *{rng.randint(1000, 9999)}. Без НДС."
    return {
        "id": f"1{when.strftime('%y%m%d')}MOCOIPFBT{seq:05d}",
        "operationId": _uuid(rng),
        "halfTransactionKey": f"1{when.strftime('%y%m%d')}MOCOIPFBT{seq:05d}",
        "account": {"name": None, "number": None},          # patched per client
        "beneficiaryAccount": {"name": None,
                               "number": f"30232810***{rng.randint(1000, 9999)}" if "СБП" in title else None},
        "dateTime": stamp.isoformat() + f".{rng.randint(0, 999):03d}+03:00",
        "operationDate": when.isoformat(),
        "title": title,
        "amount": {"value": kop, "currency": "RUR", "minorUnits": 100},
        "direction": direction,
        "operationType": op_type,
        "systemCode": system_code,
        "mcc": mcc,
        "operationContent": content,
        "cardDetails": {},
        "merchantDto": {"requestId": _uuid(rng), "id": "-1", "name": merchant},
        "operationClass": "POSING",
        "fee": 0,
        "isPos": op_type == "POS",
        "filteringMultiValues": {"accounts": [], "operationSign": [direction]},
    }


def _mention_pairs(m: dict) -> list[tuple[str, int | None]]:
    """(template key, kopecks) per planted op: mentioned amounts cycle through the
    mentioned keywords; no amounts → up to two keyword-typed ops at default prices."""
    keys = m["keywords"] or ["generic"]
    if m["amounts"]:
        return [(keys[i % len(keys)], kop) for i, kop in enumerate(m["amounts"])]
    return [(k, None) for k in keys[:2] if k != "generic"]


def _op_dates(rng: random.Random, key: str, when: date, window_start: date) -> list[date]:
    """FEE/SUBSCRIPTION plants recur ~monthly back toward the window start."""
    dates = [when]
    if PLANT[key][2] in ("FEE", "SUBSCRIPTION"):
        for back in (30, 60):
            prev = when - timedelta(days=back + rng.randint(-2, 2))
            if prev >= window_start:
                dates.append(prev)
    return dates


def build_client_ops(rng: random.Random, mentions: list[dict], window_end: date,
                     days: int, min_ops: int) -> tuple[list[dict], list[dict]]:
    """mentions: [{row_id, date, amounts[], keywords[], explicit_date}] for one client.
    Returns (operations, planted_summary)."""
    window_start = window_end - timedelta(days=days - 1)
    ops, planted = [], []
    seq = 1

    def clamp(d: date) -> date:
        return min(max(d, window_start), window_end)

    for m in mentions:
        for key, kop in _mention_pairs(m):
            when = clamp(m["explicit_date"] or
                         m["date"] - timedelta(days=rng.randint(0, 12)))
            for d in _op_dates(rng, key, when, window_start):
                op = planted_op(rng, key, kop, d, seq)
                ops.append(op)
                planted.append({"row_id": m["row_id"], "id": op["id"], "title": op["title"],
                                "rub": op["amount"]["value"] / 100, "date": d.isoformat()})
                seq += 1
        if len(ops) >= 60:      # safety cap for very chatty clients
            break

    n_fill = max(min_ops - len(ops), 12)
    for _ in range(n_fill):
        when = window_end - timedelta(days=rng.randint(0, days - 1))
        ops.append(_operation(rng, when, seq))
        seq += 1
    ops.sort(key=lambda o: o["operationDate"])
    return ops, planted


# ── xlsx: fill empty cells in place ──────────────────────────────────────────────────

def _cell_xml(ref: str, attrs: str, value: str) -> str:
    attrs = re.sub(r'\s+t="[^"]*"', "", attrs)
    return f'<c r="{ref}"{attrs} t="inlineStr"><is><t>{escape(value)}</t></is></c>'


def fill_sheet_xml(xml: str, fills: dict[str, str]) -> str:
    """Set inline-string values on cells expected to be empty. Existing empty cells are
    rewritten in place; cells beyond a row's span (e.g. I on a spans="1:8" row) are
    inserted in column order, extending the span."""
    pending = dict(fills)

    def repl_empty(m):
        ref, attrs = m.group(1), m.group(2)
        if ref not in pending:
            return m.group(0)
        return _cell_xml(ref, attrs, pending.pop(ref))

    xml = re.sub(r'<c r="([A-Z]+\d+)"([^>]*?)/>', repl_empty, xml)
    for ref in list(pending):   # non-self-closing but valueless (e.g. empty shared string)
        pat = re.compile(rf'<c r="{ref}"([^>]*?)>(?:(?!</c>).)*?</c>', re.S)
        m = pat.search(xml)
        if m:
            xml = pat.sub(_cell_xml(ref, m.group(1), pending.pop(ref)), xml, count=1)

    # Remaining refs: the row exists but stops short of this column — insert the cell.
    by_row: dict[int, list[str]] = {}
    for ref in pending:
        by_row.setdefault(int(re.search(r"\d+", ref).group(0)), []).append(ref)
    for rownum, refs in by_row.items():
        rowpat = re.compile(rf'(<row r="{rownum}"[^>]*>)(.*?)(</row>)', re.S)
        m = rowpat.search(xml)
        if not m:
            continue
        head, body, tail = m.group(1), m.group(2), m.group(3)
        for ref in sorted(refs, key=ds._col):
            col = ds._col(ref)
            insert_at = len(body)
            for cm in re.finditer(r'<c r="([A-Z]+\d+)"', body):
                if ds._col(cm.group(1)) > col:
                    insert_at = cm.start()
                    break
            body = body[:insert_at] + _cell_xml(ref, "", pending.pop(ref)) + body[insert_at:]
        spans = re.search(r'spans="(\d+):(\d+)"', head)
        if spans:
            hi = max(int(spans.group(2)), max(ds._col(r) for r in refs) + 1)
            head = head[:spans.start(2)] + str(hi) + head[spans.end(2):]
        xml = xml[:m.start()] + head + body + tail + xml[m.end():]

    if pending:
        sys.exit(f"xlsx fill failed — cells not found: {sorted(pending)[:8]} "
                 f"(+{max(0, len(pending) - 8)} more)")
    return xml


def rewrite_xlsx(path: str, sheet_name: str, new_xml: str) -> None:
    backup = Path(tempfile.gettempdir()) / (Path(path).name + ".bak")
    shutil.copy2(path, backup)
    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = new_xml.encode("utf-8") if item.filename == sheet_name else zin.read(item.filename)
            zout.writestr(item, data)
    Path(tmp).replace(path)
    print(f"xlsx updated in place (backup: {backup})")


def labeled_sheet(z: zipfile.ZipFile, strings: list[str]) -> tuple[str, list[dict]]:
    """dataset.labeled_sheet with a CLI-friendly failure."""
    name, rows = ds.labeled_sheet(z, strings)
    if name is None:
        sys.exit("no sheet in the workbook looks like the labeled table")
    return name, rows


# ── main ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="Mock clients + MCP histories for the labeled xlsx")
    p.add_argument("--xlsx", default=DEFAULT_XLSX)
    p.add_argument("--out", default="/tmp/mcp_mock_clients", help="mock MCP dir (outside the repo)")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--min-ops", type=int, default=30)
    p.add_argument("--seed", type=int, default=20260730)
    p.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    p.add_argument("--skip-xlsx", action="store_true", help="write the mock dir only")
    args = p.parse_args()

    z = zipfile.ZipFile(args.xlsx)
    sheet_name, rows = labeled_sheet(z, ds.shared_strings(z))

    # ── pass 1: rows → clients ──
    rng = random.Random(args.seed)
    taken = set()
    dialogue_code: dict[str, str] = {}
    content_rows: list[dict] = []
    for n, cells in enumerate(rows[1:], start=1):   # row ids match dataset.py's row_N
        get = lambda i: (cells.get(i) or "").strip()
        if not any(get(i) for i in (COL_AGENT, COL_OPERATOR, COL_DIALOGUE)):
            continue
        code, dlg = get(COL_CODE), get(COL_DIALOGUE)
        if code:
            taken.add(code)
            dialogue_code.setdefault(dlg, code)
        content_rows.append({"row_id": f"row_{n}", "sheet_row": n + 1, "code": code,
                             "had_code": bool(code), "dialogue": dlg,
                             "operator": get(COL_OPERATOR), "existing_id": get(COL_ID),
                             "date": ds._excel_date(get(COL_DATE))})
    new_codes = 0
    for r in content_rows:
        if not r["code"]:
            code = dialogue_code.get(r["dialogue"])
            if not code:
                code = make_code(rng, taken)
                dialogue_code[r["dialogue"]] = code
                new_codes += 1
            r["code"] = code

    # ── pass 2: per-client identity + mentions ──
    clients: dict[str, dict] = {}
    used_ids, used_accts = set(), set()
    for r in content_rows:
        c = clients.get(r["code"])
        if c is None:
            crng = random.Random(f"{args.seed}:{r['code']}")
            while (cid := str(crng.randint(10**12, 10**13 - 1))) in used_ids:
                pass
            used_ids.add(cid)
            while (acct := f"40817810***{crng.randint(0, 9999):04d}") in used_accts:
                pass
            used_accts.add(acct)
            c = clients[r["code"]] = {
                "rng": crng, "client_id": cid, "account_number": acct,
                "account_name": blob(crng, crng.randint(20, 26)) + cid + blob(crng, crng.randint(28, 34)),
                "mentions": [], "rows": [],
            }
        try:
            row_date = date.fromisoformat(r["date"])
        except ValueError:
            row_date = date(2026, 7, 20)
        text = r["dialogue"] + "\n" + r["operator"]
        c["mentions"].append({"row_id": r["row_id"], "date": row_date,
                              "amounts": extract_amounts(text),
                              "keywords": extract_keywords(text),
                              "explicit_date": extract_date(text, row_date)})
        c["rows"].append(r["row_id"])

    # ── pass 3: histories ──
    index, total_ops, rows_with_plants = {}, 0, set()
    out_dir = Path(args.out)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
    for code, c in sorted(clients.items()):
        window_end = max(m["date"] for m in c["mentions"])
        ops, planted = build_client_ops(c["rng"], c["mentions"], window_end,
                                        args.days, args.min_ops)
        for op in ops:      # stamp this client's identity everywhere (MCP_Answer_Json shape)
            op["account"] = {"name": c["account_name"], "number": c["account_number"]}
            op["filteringMultiValues"]["accounts"] = [c["account_number"]]
        rows_with_plants.update(pl["row_id"] for pl in planted)
        total_ops += len(ops)
        fname = f"history_operations_{code}.json"
        if not args.dry_run:
            (out_dir / fname).write_text(
                json.dumps({"operations": ops}, ensure_ascii=False, indent=1))
        index[code] = {"client_id": c["client_id"], "account_number": c["account_number"],
                       "account_name": c["account_name"], "file": fname, "rows": c["rows"],
                       "n_operations": len(ops),
                       "window": [(window_end - timedelta(days=args.days - 1)).isoformat(),
                                  window_end.isoformat()],
                       "planted": planted}
    if not args.dry_run:
        (out_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1))

    # ── pass 4: fill xlsx columns A (missing codes) and I (client ids) ──
    fills: dict[str, str] = {}
    for r in content_rows:
        if not r["had_code"]:
            fills[f"A{r['sheet_row']}"] = r["code"]
        if not r["existing_id"]:
            fills[f"I{r['sheet_row']}"] = clients[r["code"]]["client_id"]
    if not args.skip_xlsx and not args.dry_run and fills:
        xml = z.read(sheet_name).decode("utf-8")
        z.close()
        rewrite_xlsx(args.xlsx, sheet_name, fill_sheet_xml(xml, fills))

    print(f"rows: {len(content_rows)} | clients: {len(clients)} ({new_codes} new codes) | "
          f"operations: {total_ops} (≥{args.min_ops}/client, {args.days}-day window)")
    print(f"rows with planted operations: {len(rows_with_plants)}/{len(content_rows)}")
    print(f"xlsx cells {'to fill' if args.dry_run or args.skip_xlsx else 'filled'}: {len(fills)} "
          f"| mock dir: {out_dir}")


if __name__ == "__main__":
    main()
