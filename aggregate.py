"""
Aggregates per-case eval results, applies policy, writes report + run diff.
Policy: solved = resolution_yes AND NOT has_unsupported_critical_claim AND tools_ok
"""
import json
from pathlib import Path


def is_solved(result: dict) -> bool:
    checks = result["checks"]
    groundedness = result["groundedness"]
    resolution = result["resolution"]
    return (
        resolution.get("resolution_yes", False)
        and not groundedness.get("has_unsupported_critical_claim", True)
        and checks.get("tools_ok", False)
    )


def aggregate_results(results: list[dict], run_dir: Path) -> dict:
    total = len(results)
    solved = sum(1 for r in results if is_solved(r))
    must_facts_ok = sum(1 for r in results if r["checks"].get("all_must_facts_present", False))
    tools_ok = sum(1 for r in results if r["checks"].get("tools_ok", False))

    report = {
        "run_dir": str(run_dir),
        "total": total,
        "solved": solved,
        "pct_solved": round(solved / total * 100, 1) if total else 0,
        "pct_must_facts": round(must_facts_ok / total * 100, 1) if total else 0,
        "pct_tools_ok": round(tools_ok / total * 100, 1) if total else 0,
        "cases": [
            {
                "case_id": r["case_id"],
                "solved": is_solved(r),
                "tools_ok": r["checks"]["tools_ok"],
                "all_must_facts": r["checks"]["all_must_facts_present"],
                "resolution_yes": r["resolution"].get("resolution_yes"),
                "has_unsupported_claim": r["groundedness"].get("has_unsupported_critical_claim"),
            }
            for r in results
        ],
    }

    diff = _diff_vs_previous(report, run_dir)
    if diff:
        report["diff"] = diff

    return report


def _diff_vs_previous(current: dict, current_run_dir: Path) -> dict | None:
    runs_root = current_run_dir.parent
    prev_runs = sorted(d for d in runs_root.iterdir() if d.is_dir() and d != current_run_dir)
    if not prev_runs:
        return None
    prev_report_path = prev_runs[-1] / "report.json"
    if not prev_report_path.exists():
        return None
    prev = json.loads(prev_report_path.read_text())
    return {
        "vs_run": prev["run_dir"],
        "pct_solved_delta": round(current["pct_solved"] - prev["pct_solved"], 1),
        "pct_must_facts_delta": round(current["pct_must_facts"] - prev["pct_must_facts"], 1),
        "pct_tools_ok_delta": round(current["pct_tools_ok"] - prev["pct_tools_ok"], 1),
    }
