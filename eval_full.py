"""
Full eval with a built-in preflight. First the fast scheme check (eval_fast) on N
dialogues — if the scheme is broken the run aborts immediately instead of burning hours
at the Sandbox 0.2 RPS limit. Then the complete calibration over every labeled row:
agreement, kappa, per-class precision/recall/F1, the binary correct/incorrect scale
(Частично counts as incorrect), the failure-reason breakdown, and
runs/<ts>/disagreements.csv.

    python3 eval_full.py                                    # data/labeled.jsonl
    python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"
    python3 eval_full.py --dataset data/labeled_new.jsonl --all-rows --repeat 3
    python3 eval_full.py --skip-smoke                       # straight to the full run

Accepts .jsonl or .xlsx (converted to a temp jsonl outside the repo, like eval_fast).
"""
import argparse
import asyncio
import sys

import calibrate
import eval_fast


def main() -> int:
    parser = argparse.ArgumentParser(description="Full eval: scheme preflight + complete calibration")
    parser.add_argument("--dataset", default="data/labeled.jsonl",
                        help="labeled .jsonl, or the source .xlsx (auto-converted to a temp file)")
    parser.add_argument("--n", type=int, default=eval_fast.SMOKE_N,
                        help="random dialogues in the preflight (default 20)")
    parser.add_argument("--skip-smoke", action="store_true", help="skip the preflight")
    parser.add_argument("--csv", default=None, help="CSV path (default: runs/<ts>/disagreements.csv)")
    parser.add_argument("--all-rows", action="store_true", help="write every row to the CSV")
    parser.add_argument("--repeat", type=int, default=1, help="run N times → mean ± std")
    parser.add_argument("--classify-scope", action="store_true",
                        help="LLM-classify scope for rows without a backend intent (cached)")
    args = parser.parse_args()

    jsonl = eval_fast.ensure_jsonl(args.dataset)

    if not args.skip_smoke:
        print("── preflight ──")
        if eval_fast.main(jsonl, args.n) != 0:
            print("aborting the full eval — fix the scheme first (or --skip-smoke to override)")
            return 1
        print("── full eval ──")

    asyncio.run(calibrate.main(jsonl, csv_path=args.csv, all_rows=args.all_rows,
                               repeat=args.repeat, classify_scope=args.classify_scope))
    return 0


if __name__ == "__main__":
    sys.exit(main())
