"""
Judge calibration: measure how well the resolution judge reproduces the human labels in the
ground-truth set (current_agent_answers.xlsx → data/labeled.jsonl via dataset.py).

For each labeled row the agent answer already exists, so we do NOT run the agent — we score
the given answer with the resolution judge and compare its 3-way verdict to human_label.

    python3 calibrate.py --dataset data/labeled.jsonl

Reports exact-match agreement and a 3x3 confusion matrix (rows = human, cols = judge).
Meaningful once the Phase 4 judge is real; runs on the stub as plumbing.
"""
import argparse
import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path

import config
from judges.resolution import judge_resolution

LABELS = ["yes", "partial", "no"]
LABELS_BINARY = ["acceptable", "wrong"]
CSV_FIELDS = ["id", "human_label", "verdict", "agree", "reasoning",
              "agent_answer", "operator_answer", "dialogue", "assessor_comment"]


def to_binary(human_label: str) -> str:
    """Collapse the human 3-way label to the binary scale: Нет → wrong, Да·Частично → acceptable."""
    return "wrong" if human_label == "no" else "acceptable"


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


_ORDER = {"no": 0, "partial": 1, "yes": 2}          # ordinal scale for within-1
_ORDER_BINARY = {"wrong": 0, "acceptable": 1}


def summarize(pairs: list[tuple[str, str]], labels: list | None = None, order: dict | None = None) -> dict:
    """pairs = [(human_label, judge_verdict)]. Exact agreement + Cohen's kappa + within-1 + matrix.

    On the imbalanced 3-class set, raw agreement rewards the degenerate all-"no" judge (~62.5%),
    so kappa (chance-corrected) and within-1 (adjacent verdicts count as near-miss) are the honest
    headline metrics. Pass labels/order for the binary scale.
    """
    labels, order = labels or LABELS, order or _ORDER
    confusion = {h: {j: 0 for j in labels} for h in labels}
    agree = within1 = 0
    for human, verdict in pairs:
        if human in confusion and verdict in confusion[human]:
            confusion[human][verdict] += 1
        if human == verdict:
            agree += 1
        if human in order and verdict in order and abs(order[human] - order[verdict]) <= 1:
            within1 += 1
    n = len(pairs)
    row = {h: sum(confusion[h].values()) for h in labels}
    col = {j: sum(confusion[h][j] for h in labels) for j in labels}
    po = agree / n if n else 0.0
    pe = sum(row[l] * col[l] for l in labels) / (n * n) if n else 0.0
    kappa = round((po - pe) / (1 - pe), 3) if n and pe != 1 else 0.0
    return {
        "n": n,
        "agreement": round(po * 100, 1) if n else 0.0,
        "kappa": kappa,
        "within1": round(within1 / n * 100, 1) if n else 0.0,
        "confusion": confusion,
    }


async def score_row(row: dict, binary: bool = False) -> dict:
    case = {"id": row["id"], "query": row.get("dialogue", ""), "operator_answer": row.get("operator_answer", "")}
    trace = {"case_id": row["id"], "answer": row.get("agent_answer", ""), "tool_calls": [], "chunks": []}
    # Always run the 3-way judge; collapse to binary for scoring — empirically beats a native
    # binary judge (κ 0.287 vs 0.234), since the finer reasoning yields a better wrong/acceptable call.
    result = await judge_resolution(case, trace)
    verdict = to_binary(result["verdict"]) if binary else result["verdict"]
    human = to_binary(row["human_label"]) if binary else row["human_label"]
    return {
        "id": row.get("id", ""),
        "human_label": human,
        "verdict": verdict,
        "reasoning": result.get("reasoning", ""),
        "agent_answer": row.get("agent_answer", ""),
        "operator_answer": row.get("operator_answer", ""),
        "dialogue": row.get("dialogue", ""),
        "assessor_comment": row.get("assessor_comment", ""),
    }


def select_rows(records: list[dict], all_rows: bool = False) -> list[dict]:
    """Disagreements (human_label != verdict), or every row when all_rows."""
    return [r for r in records if all_rows or r["human_label"] != r["verdict"]]


def write_csv(records: list[dict], path: Path, all_rows: bool = False) -> int:
    rows = select_rows(records, all_rows)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM → Excel reads Cyrillic
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow({**r, "agree": r["human_label"] == r["verdict"]})
    return len(rows)


async def main(dataset_path: str, csv_path: str | None = None, all_rows: bool = False,
               limit: int | None = None, binary: bool = False) -> None:
    rows = [r for r in load(dataset_path) if r.get("human_label") in LABELS]
    if limit:
        rows = rows[:limit]
    labels, order = (LABELS_BINARY, _ORDER_BINARY) if binary else (LABELS, _ORDER)
    spec = config.get("resolution")
    judge = f"LLM ({spec.model}, effort {spec.effort})" if spec.available() else "stub (no LLM)"
    print(f"judge: {judge}{'  [binary: acceptable/wrong]' if binary else ''}")

    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)

    async def scored(row):
        async with sem:
            return await score_row(row, binary)

    records = list(await asyncio.gather(*(scored(r) for r in rows)))
    report = summarize([(r["human_label"], r["verdict"]) for r in records], labels, order)

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    out_csv = Path(csv_path) if csv_path else run_dir / ("rows.csv" if all_rows else "disagreements.csv")
    n_csv = write_csv(records, out_csv, all_rows)

    print(f"labeled rows: {report['n']} | agreement: {report['agreement']}% | "
          f"kappa: {report['kappa']} | within-1: {report['within1']}%")
    print("confusion (rows = human, cols = judge):")
    print("            " + "".join(f"{j:>11}" for j in labels))
    for h in labels:
        print(f"  {h:>10}  " + "".join(f"{report['confusion'][h][j]:>11}" for j in labels))
    print(f"{'rows' if all_rows else 'disagreements'}: {n_csv} → {out_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolution-judge calibration vs human labels")
    parser.add_argument("--dataset", default="data/labeled_sample.jsonl")
    parser.add_argument("--csv", default=None, help="CSV path (default: runs/<ts>/disagreements.csv)")
    parser.add_argument("--all-rows", action="store_true", help="write every row, not only disagreements")
    parser.add_argument("--limit", type=int, help="score only the first N labeled rows")
    parser.add_argument("--binary", action="store_true", help="binary judge: acceptable vs wrong")
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.csv, args.all_rows, args.limit, args.binary))
