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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import calibrate
import config
import dataset
import progress
from errors import ConfigError, DatasetError, LlmOutputError
from judges.resolution import FAILURE_REASONS

SMOKE_N = 10

_WHERE_LABELS = {"dataset": "DATASET", "config": "CONFIG",
                 "api": "API / NETWORK", "llm_output": "LLM OUTPUT"}
_WHERE_ORDER = {"dataset": 0, "config": 1, "api": 2, "llm_output": 3}


@dataclass
class Finding:
    """One diagnosed problem: where it lives, what happened, what to do, which rows."""
    where: str
    problem: str
    what_to_do: str
    rows: list = field(default_factory=list)


class SchemeDiagnostician:
    """Turns judged records into categorized findings — WHERE the scheme is broken
    (dataset / config / api / llm_output) and WHAT TO DO. Structured `error` dicts from
    the judges (see errors.py) are used directly; format violations mean the model
    answered but ignored the schema."""

    def diagnose(self, records: list[dict]) -> list[Finding]:
        findings: dict[tuple, Finding] = {}

        def add(where: str, problem: str, fix: str, rid: str) -> None:
            key = (where, problem[:120], fix)
            findings.setdefault(key, Finding(where, problem, fix)).rows.append(rid)

        for r in records:
            rid = r.get("id", "?")
            err, reasoning = r.get("error"), r.get("reasoning", "")
            if err:  # the judge call itself failed — trust its structured diagnosis
                add(err.get("where", "api"), err.get("detail", "judge call failed"),
                    err.get("what_to_do", ""), rid)
                continue
            if "[stub" in reasoning:
                add("config", "the judge is the offline stub — no live LLM was exercised",
                    ConfigError.default_fix, rid)
                continue
            if "[judge error:" in reasoning:
                # error text without structured info (e.g. a panel vote absorbed by a yes
                # majority) — still a scheme problem: the infrastructure is flaky
                add("api", f"judge error in reasoning: {reasoning[:160]}",
                    "read the error text: 'cannot reach' → connect the bank VPN/VDI; "
                    "HTTP codes → the Sandbox error table ('4. Sandbox API.pdf')", rid)
                continue
            verdict, reason = r.get("verdict"), r.get("failure_reason")
            if verdict not in calibrate.LABELS:
                add("llm_output", f"invalid verdict {verdict!r}", LlmOutputError.default_fix, rid)
            elif reason not in FAILURE_REASONS:
                add("llm_output", f"invalid failure_reason {reason!r}", LlmOutputError.default_fix, rid)
            elif (verdict == "yes") != (reason == "none"):
                add("llm_output", f"verdict {verdict} inconsistent with failure_reason {reason!r}",
                    LlmOutputError.default_fix, rid)
            elif not reasoning.strip():
                add("llm_output", "empty reasoning", LlmOutputError.default_fix, rid)
        return sorted(findings.values(), key=lambda f: (_WHERE_ORDER.get(f.where, 9), -len(f.rows)))

    @staticmethod
    def print_findings(findings: list[Finding], total: int) -> None:
        print("diagnosis — where is the problem and what to do:")
        for f in findings:
            rows = ", ".join(f.rows[:8]) + (f" (+{len(f.rows) - 8} more)" if len(f.rows) > 8 else "")
            print(f"  [{_WHERE_LABELS.get(f.where, f.where)}] {len(f.rows)}/{total} dialogues: {rows}")
            print(f"    problem:    {f.problem[:200]}")
            print(f"    what to do: {f.what_to_do}")


def _fail(where: str, problem: str, what_to_do: str) -> int:
    """Print a single pre-flight finding (dataset/config level) and fail."""
    print("SCHEME FAILED")
    SchemeDiagnostician.print_findings([Finding(where, problem, what_to_do, ["-"])], 1)
    return 1


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
    return calibrate.load_labeled(path)


def validate(records: list[dict]) -> list[str]:
    """Scheme violations in judged records — empty list means the scheme is sound.
    Derived from SchemeDiagnostician (single source of truth): one problem line per
    affected row, so the pass/fail gate can never disagree with the printed diagnosis."""
    return [f"{rid}: {f.problem}"
            for f in SchemeDiagnostician().diagnose(records) for rid in f.rows]


async def run_smoke(rows: list[dict], n: int) -> tuple[list[dict], float]:
    """Judge the first n rows (concurrency-bounded like calibrate); return (records, seconds)."""
    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)
    todo = rows[:n]
    bar = progress.ProgressBar(len(todo), label="smoke")

    async def scored(row):
        async with sem:
            rec = await calibrate.score_row(row)
        bar.advance(note=rec.get("id", ""))
        return rec

    start = time.monotonic()
    ticker = asyncio.ensure_future(bar.tick())
    try:
        records = list(await asyncio.gather(*(scored(r) for r in todo)))
    finally:
        ticker.cancel()
        bar.close()
    return records, time.monotonic() - start


def eta_text(elapsed: float, n: int, total: int) -> str:
    seconds = elapsed / n * total if n else 0.0
    if seconds >= 5400:
        return f"~{seconds / 3600:.1f} h"
    return f"~{max(1, round(seconds / 60))} min"


def main(dataset_path: str, n: int) -> int:
    # ── dataset-level checks first: a broken input never reaches the API ──
    try:
        jsonl = ensure_jsonl(dataset_path)
    except FileNotFoundError:
        return _fail("dataset", f"file not found: {dataset_path}",
                     "check the path; pass the assessors' .xlsx or a dataset.py .jsonl")
    except zipfile.BadZipFile:
        return _fail("dataset", f"not a valid .xlsx file: {dataset_path}", DatasetError.default_fix)
    try:
        rows = load_labeled(jsonl)
    except FileNotFoundError:
        return _fail("dataset", f"file not found: {jsonl}",
                     "check the path; pass the assessors' .xlsx or a dataset.py .jsonl")
    except json.JSONDecodeError as e:
        return _fail("dataset", f"malformed jsonl ({e})",
                     "regenerate it: python3 dataset.py <file.xlsx> <out.jsonl>")
    if not rows:
        return _fail("dataset", f"no labeled rows in {dataset_path}",
                     "for .xlsx: the labeled sheet was not recognized — headers must include "
                     "'agent answer' and 'Is agents answer correct?' (Да/Частично/Нет); "
                     "for .jsonl: human_label must be yes/partial/no")

    # ── smoke: judge the first N dialogues — the only step that touches the API ──
    print(f"judge: {calibrate.judge_banner()}")
    print(f"smoke: {min(n, len(rows))} of {len(rows)} labeled dialogues")

    records, elapsed = asyncio.run(run_smoke(rows, n))
    for r in records:
        flag = "" if r["failure_reason"] == "none" else f"·{r['failure_reason']}"
        print(f"  {r['id']:>10}  {r['verdict']}{flag}")

    # ── diagnose the judged records: any finding = broken scheme → exit 1 with WHERE/WHAT ──
    findings = SchemeDiagnostician().diagnose(records)
    print(f"elapsed: {elapsed:.0f}s for {len(records)} dialogues | "
          f"full run over {len(rows)} rows: {eta_text(elapsed, len(records), len(rows))}")
    if findings:
        n_problems = sum(len(f.rows) for f in findings)
        print(f"SCHEME FAILED ({n_problems} problem(s) in {len(records)} dialogues)")
        SchemeDiagnostician.print_findings(findings, len(records))
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
