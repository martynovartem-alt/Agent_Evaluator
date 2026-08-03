"""
Eval runner: reads golden_set.jsonl → run_agent() per case → captures trace →
runs 3 parallel evaluations → aggregates → writes runs/<timestamp>/report.json.

Cases are processed concurrently, bounded by config.JUDGE_CONCURRENCY ([pipeline].concurrency):
per case the (blocking) agent runs in a worker thread, then its 3 evaluations run in parallel.
Results stay in dataset order (asyncio.gather preserves it), so the report is deterministic.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import config
import dataset
import progress
from agent import run_agent
from aggregate import aggregate_results, format_report
from checks import run_checks
from judges.groundedness import judge_groundedness
from judges.resolution import judge_resolution


def load_cases(path: str) -> list[dict]:
    return dataset.load_jsonl(path)


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


async def _process_case(case: dict, traces_dir: Path, sem: asyncio.Semaphore,
                        bar: progress.ProgressBar | None = None) -> dict:
    """Run the agent, persist its trace, then evaluate it — one case, bounded by `sem`.
    The agent call is blocking, so it runs in a worker thread to free the event loop."""
    async with sem:
        trace = await asyncio.to_thread(run_agent, case)
        (traces_dir / f"{case['id']}.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2)
        )
        result = await evaluate_case(case, trace)
    if bar:
        bar.advance(note=case["id"])
    return result


async def main(dataset_path: str, case_id: str | None = None, limit: int | None = None) -> None:
    cases = load_cases(dataset_path)
    if case_id:
        cases = [c for c in cases if c["id"] == case_id]
        if not cases:
            print(f"Case {case_id!r} not found in {dataset_path}", file=sys.stderr)
            sys.exit(1)
    if limit:
        cases = cases[:limit]

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%dT%H%M%S")
    traces_dir = run_dir / "traces"
    traces_dir.mkdir(parents=True)

    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)
    bar = progress.ProgressBar(len(cases), label="cases")
    ticker = asyncio.ensure_future(bar.tick())
    try:
        results = list(await asyncio.gather(
            *(_process_case(case, traces_dir, sem, bar) for case in cases)
        ))
    finally:
        ticker.cancel()
        bar.close()

    report = aggregate_results(results, run_dir)
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(format_report(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent eval runner")
    parser.add_argument("--dataset", required=True, help="Path to golden_set.jsonl")
    parser.add_argument("--case-id", help="Run a single case by ID")
    parser.add_argument("--limit", type=int, help="Run only the first N cases")
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.case_id, args.limit))
