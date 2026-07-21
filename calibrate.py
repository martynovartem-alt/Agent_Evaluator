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
CSV_FIELDS = ["id", "human_label", "verdict", "agree", "reasoning",
              "agent_answer", "operator_answer", "dialogue", "assessor_comment"]


def load(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def summarize(pairs: list[tuple[str, str]]) -> dict:
    """pairs = [(human_label, judge_verdict)] for labeled rows. Exact-match agreement + matrix."""
    confusion = {h: {j: 0 for j in LABELS} for h in LABELS}
    agree = 0
    for human, verdict in pairs:
        if human in confusion and verdict in confusion[human]:
            confusion[human][verdict] += 1
        if human == verdict:
            agree += 1
    n = len(pairs)
    return {
        "n": n,
        "agreement": round(agree / n * 100, 1) if n else 0.0,
        "confusion": confusion,
    }


async def score_row(row: dict) -> dict:
    case = {"id": row["id"], "query": row.get("dialogue", ""), "operator_answer": row.get("operator_answer", "")}
    trace = {"case_id": row["id"], "answer": row.get("agent_answer", ""), "tool_calls": [], "chunks": []}
    result = await judge_resolution(case, trace)
    return {
        "id": row.get("id", ""),
        "human_label": row["human_label"],
        "verdict": result["verdict"],
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


async def main(dataset_path: str, csv_path: str | None = None, all_rows: bool = False) -> None:
    rows = [r for r in load(dataset_path) if r.get("human_label") in LABELS]
    spec = config.get("resolution")
    judge = f"LLM ({spec.model}, effort {spec.effort})" if spec.available() else "stub (no LLM)"
    print(f"judge: {judge}")

    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)

    async def scored(row):
        async with sem:
            return await score_row(row)

    records = list(await asyncio.gather(*(scored(r) for r in rows)))
    report = summarize([(r["human_label"], r["verdict"]) for r in records])

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    out_csv = Path(csv_path) if csv_path else run_dir / ("rows.csv" if all_rows else "disagreements.csv")
    n_csv = write_csv(records, out_csv, all_rows)

    print(f"labeled rows: {report['n']} | judge agreement: {report['agreement']}%")
    print("confusion (rows = human, cols = judge):")
    print("            " + "".join(f"{j:>9}" for j in LABELS))
    for h in LABELS:
        print(f"  {h:>8}  " + "".join(f"{report['confusion'][h][j]:>9}" for j in LABELS))
    print(f"{'rows' if all_rows else 'disagreements'}: {n_csv} → {out_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolution-judge calibration vs human labels")
    parser.add_argument("--dataset", default="data/labeled_sample.jsonl")
    parser.add_argument("--csv", default=None, help="CSV path (default: runs/<ts>/disagreements.csv)")
    parser.add_argument("--all-rows", action="store_true", help="write every row, not only disagreements")
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.csv, args.all_rows))
