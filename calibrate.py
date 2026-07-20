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
import json
from datetime import datetime
from pathlib import Path

from judges.resolution import judge_resolution

LABELS = ["yes", "partial", "no"]


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


async def score_row(row: dict) -> tuple[str, str]:
    case = {"id": row["id"], "query": row.get("dialogue", ""), "operator_answer": row.get("operator_answer", "")}
    trace = {"case_id": row["id"], "answer": row.get("agent_answer", ""), "tool_calls": [], "chunks": []}
    result = await judge_resolution(case, trace)
    return row["human_label"], result["verdict"]


async def main(dataset_path: str) -> None:
    rows = [r for r in load(dataset_path) if r.get("human_label") in LABELS]
    pairs = await asyncio.gather(*(score_row(r) for r in rows))
    report = summarize(list(pairs))

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "calibration.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))

    print(f"labeled rows: {report['n']} | judge agreement: {report['agreement']}%")
    print("confusion (rows = human, cols = judge):")
    print("            " + "".join(f"{j:>9}" for j in LABELS))
    for h in LABELS:
        print(f"  {h:>8}  " + "".join(f"{report['confusion'][h][j]:>9}" for j in LABELS))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolution-judge calibration vs human labels")
    parser.add_argument("--dataset", default="data/labeled_sample.jsonl")
    args = parser.parse_args()
    asyncio.run(main(args.dataset))
