"""
Eval runner: reads golden_set.jsonl → run_agent() per case → captures trace →
runs 3 parallel evaluations → aggregates → writes runs/<timestamp>/report.json
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from agent import run_agent
from aggregate import aggregate_results
from checks import run_checks
from judges.groundedness import judge_groundedness
from judges.resolution import judge_resolution


def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


async def evaluate_case(case: dict, trace: dict) -> dict:
    checks_result, groundedness_result, resolution_result = await asyncio.gather(
        asyncio.to_thread(run_checks, case, trace),
        judge_groundedness(case, trace),
        judge_resolution(case, trace),
    )
    return {
        "case_id": case["id"],
        "trace": trace,
        "checks": checks_result,
        "groundedness": groundedness_result,
        "resolution": resolution_result,
    }


async def main(dataset_path: str, case_id: str | None = None) -> None:
    cases = load_cases(dataset_path)
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
        if not cases:
            print(f"Case {case_id!r} not found in {dataset_path}", file=sys.stderr)
            sys.exit(1)

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%dT%H%M%S")
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(parents=True)

    results = []
    for case in cases:
        trace = run_agent(case)
        (traces_dir / f"{case['id']}.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2)
        )
        result = await evaluate_case(case, trace)
        results.append(result)

    report = aggregate_results(results, run_dir)
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent eval runner")
    parser.add_argument("--dataset", required=True, help="Path to golden_set.jsonl")
    parser.add_argument("--case-id", help="Run a single case by ID")
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.case_id))
