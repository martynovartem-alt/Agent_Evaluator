"""
Ingest the ground-truth labeled set (current_agent_answers.xlsx) into normalized JSONL.

The xlsx is a labeled eval set: each row is an agent answer that a human assessor graded
against the operator's answer. Columns → normalized record fields:
    agent answer                     -> agent_answer
    Date in agent timezone           -> date
    Operators answer (ground truth)  -> operator_answer
    Full dialogue text               -> dialogue
    Is agents answer correct?        -> human_label  (Да/Частично/Нет -> yes/partial/no; blank -> "")
    Assesors comment                 -> assessor_comment

Usage:
    python3 dataset.py current_agent_answers.xlsx data/labeled.jsonl

No third-party deps — parses the xlsx (a zip of XML) with the standard library.
The raw file holds real customer dialogues; keep it and data/labeled.jsonl out of git.
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
LABEL_MAP = {"да": "yes", "частично": "partial", "нет": "no"}
_HEADERS = {
    "agent answer": "agent_answer",
    "date in agent timezone": "date",
    "operators answer (ground truth)": "operator_answer",
    "full dialogue text": "dialogue",
    "is agents answer correct?": "human_label",
    "assesors comment": "assessor_comment",
    "intent": "intent",
    # Mock identity columns (filled by gen_clients.py; the id header typo is in the workbook)
    "client_name (mock)": "client_name",
    "clietn_id(mock)": "client_id",
    "client_id (mock)": "client_id",
}


def norm_label(value: str) -> str:
    """Да/Частично/Нет → yes/partial/no; anything else → '' (unlabeled)."""
    return LABEL_MAP.get((value or "").strip().lower(), "")


def load_jsonl(path: str) -> list[dict]:
    """One JSON object per non-blank line — the shared loader for every script."""
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _excel_date(value: str) -> str:
    try:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    except (ValueError, TypeError):
        return value or ""


def _col(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref).group(1)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _sheet_rows(z: zipfile.ZipFile, name: str, strings: list[str]) -> list[dict]:
    root = ET.fromstring(z.read(name))
    rows = []
    for row in root.iter(f"{NS}row"):
        cells = {}
        for c in row.findall(f"{NS}c"):
            v, is_ = c.find(f"{NS}v"), c.find(f"{NS}is")
            if c.get("t") == "s" and v is not None:
                val = strings[int(v.text)]
            elif is_ is not None:
                val = "".join(x.text or "" for x in is_.iter(f"{NS}t"))
            else:
                val = v.text if v is not None else ""
            cells[_col(c.get("r"))] = val
        rows.append(cells)
    return rows


def shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    return ["".join(t.text or "" for t in si.iter(f"{NS}t")) for si in root.findall(f"{NS}si")]


def labeled_sheet(z: zipfile.ZipFile, strings: list[str]) -> tuple[str | None, list[dict]]:
    """(worksheet member name, rows) of the labeled table. It isn't always the first sheet
    (some workbooks lead with a pivot): pick the one whose header row matches the most
    known columns; (None, []) when no sheet scores ≥ 3."""
    best_name, best_rows, best = None, [], 0
    for name in sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)):
        candidate = _sheet_rows(z, name, strings)
        if not candidate:
            continue
        score = sum(1 for v in candidate[0].values() if (v or "").strip().lower() in _HEADERS)
        if score > best:
            best_name, best_rows, best = name, candidate, score
    return (best_name, best_rows) if best >= 3 else (None, [])


def parse_xlsx(path: str) -> list[dict]:
    z = zipfile.ZipFile(path)
    _, rows = labeled_sheet(z, shared_strings(z))
    if not rows:
        return []
    header = {i: _HEADERS.get((v or "").strip().lower()) for i, v in rows[0].items()}
    records = []
    for n, cells in enumerate(rows[1:], start=1):
        rec = {field: (cells.get(i, "") or "") for i, field in header.items() if field}
        if not any(rec.get(f) for f in ("agent_answer", "operator_answer", "dialogue")):
            continue
        rec["id"] = f"row_{n}"
        rec["human_label"] = norm_label(rec.get("human_label", ""))
        rec["date"] = _excel_date(rec.get("date", ""))
        records.append(rec)
    return records


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("usage: python3 dataset.py <in.xlsx> <out.jsonl>")
    records = parse_xlsx(sys.argv[1])
    with open(sys.argv[2], "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    labeled = sum(1 for r in records if r["human_label"])
    print(f"wrote {len(records)} rows ({labeled} labeled) to {sys.argv[2]}")


if __name__ == "__main__":
    main()
