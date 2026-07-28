"""
Fast scheme check (preflight) — run this BEFORE a long eval to catch a broken setup in
minutes, not hours (the Sandbox rate limit is 0.2 RPS, so a full run is expensive).

Sends N dialogues (default 10) from the labeled set through the resolution judge and
validates the *scheme*, not the quality:
  - every row returns verdict ∈ yes/partial/no and failure_reason ∈ the taxonomy
  - verdict and failure_reason are consistent (yes ↔ none)
  - no "[judge error: ...]" rows (endpoint/auth/schema problems surface here)
  - the judge is a live LLM, not the offline stub (a stub run proves nothing)
Then prints an ETA for the full dataset at the measured rate.

    python3 eval_fast.py                                    # data/labeled.jsonl, 10 dialogues
    python3 eval_fast.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"
    python3 eval_fast.py --dataset data/labeled_new.jsonl --n 5

Accepts .jsonl (dataset.py output) or .xlsx directly — an xlsx is converted to a temp
jsonl OUTSIDE the repo, so real customer data cannot end up in git.

Exit code 0 = scheme OK, safe to launch eval_full.py; 1 = scheme broken (details printed).
"""
import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

import calibrate
import config
import dataset
from judges.resolution import FAILURE_REASONS

SMOKE_N = 10


def ensure_jsonl(path: str) -> str:
    """Return a jsonl path for `path`: jsonl passes through; xlsx is converted into the
    system temp dir (never into the repo — the raw rows are real customer data)."""
    p = Path(path)
    if p.suffix.lower() != ".xlsx":
        return path
    records = dataset.parse_xlsx(path)
    out = Path(tempfile.gettempdir()) / f"{p.stem}.labeled.jsonl"
    with open(out, "w") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"converted {path} → {out} ({len(records)} rows)")
    return str(out)


def load_labeled(path: str) -> list[dict]:
    return [r for r in calibrate.load(path) if r.get("human_label") in calibrate.LABELS]


def validate(records: list[dict]) -> list[str]:
    """Scheme violations in judged records — empty list means the scheme is sound."""
    problems = []
    for r in records:
        rid, verdict, reason = r.get("id", "?"), r.get("verdict"), r.get("failure_reason")
        reasoning = r.get("reasoning", "")
        if verdict not in calibrate.LABELS:
            problems.append(f"{rid}: invalid verdict {verdict!r}")
        if reason not in FAILURE_REASONS:
            problems.append(f"{rid}: invalid failure_reason {reason!r}")
        elif verdict == "yes" and reason != "none":
            problems.append(f"{rid}: verdict yes but failure_reason {reason!r}")
        elif verdict in ("partial", "no") and reason == "none":
            problems.append(f"{rid}: verdict {verdict} but failure_reason 'none'")
        if "[judge error:" in reasoning:
            problems.append(f"{rid}: judge error — {reasoning[:160]}")
        elif "[stub" in reasoning:
            problems.append(f"{rid}: judge is the offline stub — no live LLM was exercised "
                            f"(check the key, network/VPN, and *_MODE)")
        elif not reasoning.strip():
            problems.append(f"{rid}: empty reasoning")
    return problems


async def run_smoke(rows: list[dict], n: int) -> tuple[list[dict], float]:
    """Judge the first n rows (concurrency-bounded like calibrate); return (records, seconds)."""
    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)

    async def scored(row):
        async with sem:
            return await calibrate.score_row(row)

    start = time.monotonic()
    records = list(await asyncio.gather(*(scored(r) for r in rows[:n])))
    return records, time.monotonic() - start


def eta_text(elapsed: float, n: int, total: int) -> str:
    seconds = elapsed / n * total if n else 0.0
    if seconds >= 5400:
        return f"~{seconds / 3600:.1f} h"
    return f"~{max(1, round(seconds / 60))} min"


def main(dataset_path: str, n: int) -> int:
    jsonl = ensure_jsonl(dataset_path)
    rows = load_labeled(jsonl)
    if not rows:
        print(f"SCHEME FAILED: no labeled rows in {dataset_path}")
        return 1
    print(f"judge: {calibrate.judge_banner()}")
    print(f"smoke: {min(n, len(rows))} of {len(rows)} labeled dialogues")

    records, elapsed = asyncio.run(run_smoke(rows, n))
    for r in records:
        flag = "" if r["failure_reason"] == "none" else f"·{r['failure_reason']}"
        print(f"  {r['id']:>10}  {r['verdict']}{flag}")

    problems = validate(records)
    print(f"elapsed: {elapsed:.0f}s for {len(records)} dialogues | "
          f"full run over {len(rows)} rows: {eta_text(elapsed, len(records), len(rows))}")
    if problems:
        print(f"SCHEME FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("SCHEME OK — safe to run the full eval (python3 eval_full.py)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast scheme check before a full eval")
    parser.add_argument("--dataset", default="data/labeled.jsonl",
                        help="labeled .jsonl, or the source .xlsx (auto-converted to a temp file)")
    parser.add_argument("--n", type=int, default=SMOKE_N, help="dialogues to check (default 10)")
    args = parser.parse_args()
    sys.exit(main(args.dataset, args.n))
