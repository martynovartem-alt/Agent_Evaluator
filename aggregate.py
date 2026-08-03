"""
Aggregates per-case eval results, applies policy, writes report + run diff.
Policy: solved = resolution_yes AND NOT has_unsupported_critical_claim AND tools_ok
"""
import json
from collections import Counter
from pathlib import Path

# metrics carried in the run-to-run diff
_DIFF_KEYS = ["pct_must_facts", "pct_tools_ok", "pct_grounded",
              "pct_instruction_ok", "pct_planted_operation_ok"]


def _pct(num: int, den: int) -> float | None:
    """Percentage, or None when nothing is applicable (den == 0)."""
    return round(num / den * 100, 1) if den else None


def is_solved(result: dict) -> bool:
    checks, grounded, resolution = result["checks"], result["groundedness"], result["resolution"]
    return (
        resolution.get("resolution_yes", False)
        and not grounded.get("has_unsupported_critical_claim", True)
        and checks.get("tools_ok", False)
    )


def summarize_metrics(results: list[dict]) -> dict:
    """Headline rates. instruction/planted are gated to cases where they apply (value not None)."""
    n = len(results)
    must = sum(1 for r in results if r["checks"].get("all_must_facts_present"))
    tools = sum(1 for r in results if r["checks"].get("tools_ok"))
    grounded = sum(1 for r in results if not r["groundedness"].get("has_unsupported_critical_claim", True))
    instr = [r["checks"]["instruction_ok"] for r in results if r["checks"].get("instruction_ok") is not None]
    planted = [r["checks"]["planted_operation_ok"] for r in results if r["checks"].get("planted_operation_ok") is not None]
    verdicts = Counter(r["resolution"].get("verdict") for r in results)
    return {
        "pct_must_facts": _pct(must, n),
        "pct_tools_ok": _pct(tools, n),
        "pct_grounded": _pct(grounded, n),
        "pct_instruction_ok": _pct(sum(instr), len(instr)),
        "instruction_applicable": len(instr),
        "pct_planted_operation_ok": _pct(sum(planted), len(planted)),
        "planted_applicable": len(planted),
        "resolution_verdicts": {v: verdicts.get(v, 0) for v in ("yes", "partial", "no")},
    }


def _case_row(result: dict) -> dict:
    checks = result["checks"]
    return {
        "case_id": result["case_id"],
        "solved": is_solved(result),
        "resolution_verdict": result["resolution"].get("verdict"),
        "resolution_failure": result["resolution"].get("failure_reason"),
        "resolution_votes": [v["verdict"] for v in result["resolution"].get("votes", [])] or None,
        "has_unsupported_claim": result["groundedness"].get("has_unsupported_critical_claim"),
        "tools_ok": checks.get("tools_ok"),
        "all_must_facts": checks.get("all_must_facts_present"),
        "instruction_ok": checks.get("instruction_ok"),
        "planted_operation_ok": checks.get("planted_operation_ok"),
    }


def aggregate_results(results: list[dict], run_dir: Path) -> dict:
    total = len(results)
    solved = sum(1 for r in results if is_solved(r))
    report = {
        "run_dir": str(run_dir),
        "total": total,
        "solved": solved,
        "pct_solved": _pct(solved, total),
        "metrics": summarize_metrics(results),
        "cases": [_case_row(r) for r in results],
    }
    diff = _diff_vs_previous(report, run_dir)
    if diff:
        report["diff"] = diff
    return report


def _diff_vs_previous(current: dict, current_run_dir: Path) -> dict | None:
    runs_root = current_run_dir.parent
    prev_runs = sorted(d for d in runs_root.iterdir() if d.is_dir() and d != current_run_dir)
    for prev_dir in reversed(prev_runs):
        prev_path = prev_dir / "report.json"
        if prev_path.exists():
            prev = json.loads(prev_path.read_text())
            break
    else:
        return None

    diff = {"vs_run": prev["run_dir"]}
    cur_vals = {"pct_solved": current.get("pct_solved"), **current.get("metrics", {})}
    prev_vals = {"pct_solved": prev.get("pct_solved"), **prev.get("metrics", {})}
    for key in ["pct_solved", *_DIFF_KEYS]:
        cur, old = cur_vals.get(key), prev_vals.get(key)
        if isinstance(cur, (int, float)) and isinstance(old, (int, float)):
            diff[f"{key}_delta"] = round(cur - old, 1)
    return diff


def format_report(report: dict) -> str:
    m = report["metrics"]
    pc = lambda v: "n/a" if v is None else f"{v}%"
    v = m["resolution_verdicts"]
    lines = [
        f"Run {report['run_dir']}",
        f"Solved: {report['solved']}/{report['total']} ({pc(report['pct_solved'])})",
        f"  must_facts   {pc(m['pct_must_facts'])}",
        f"  tools_ok     {pc(m['pct_tools_ok'])}",
        f"  grounded     {pc(m['pct_grounded'])}",
        f"  instruction  {pc(m['pct_instruction_ok'])} ({m['instruction_applicable']} applicable)",
        f"  planted_op   {pc(m['pct_planted_operation_ok'])} ({m['planted_applicable']} applicable)",
        f"  resolution   yes={v['yes']} partial={v['partial']} no={v['no']}",
    ]
    if "diff" in report:
        d = report["diff"]
        deltas = " ".join(
            f"{k[:-6]} {d[k]:+g}" for k in d if k.endswith("_delta")
        )
        lines.append(f"vs {d['vs_run']}: {deltas}")
    # one line per case: verdict[panel votes]·failure_reason + every check that failed,
    # e.g. "failed subscription_299 no[nny]·incomplete facts✗"
    lines.append("Cases:")
    for c in report["cases"]:
        flags = [c["resolution_verdict"] or "?"]
        if c.get("resolution_votes"):
            flags[0] += "[" + "".join(v[0] for v in c["resolution_votes"]) + "]"  # e.g. yes[yyn]
        if c.get("resolution_failure") not in (None, "none"):
            flags[0] += f"·{c['resolution_failure']}"  # e.g. no·incomplete
        if not c["tools_ok"]:
            flags.append("tools✗")
        if not c["all_must_facts"]:
            flags.append("facts✗")
        if c["instruction_ok"] is False:
            flags.append("instr✗")
        if c["planted_operation_ok"] is False:
            flags.append("planted✗")
        if c["has_unsupported_claim"]:
            flags.append("ungrounded")
        status = "SOLVED" if c["solved"] else "failed"
        lines.append(f"  {status:6} {c['case_id']:24} {' '.join(flags)}")
    return "\n".join(lines)
