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
import statistics
from datetime import datetime
from pathlib import Path

import config
import dataset
import progress
from judges import scope as scope_mod
from judges.resolution import FAILURE_REASONS, judge_resolution

LABELS = ["yes", "partial", "no"]
LABELS_BINARY = ["correct", "incorrect"]
SCOPES = ["in_scope", "out_of_scope", "unknown"]
CSV_FIELDS = ["id", "human_label", "verdict", "failure_reason", "scope", "intent", "agree",
              "reasoning", "agent_answer", "operator_answer", "dialogue", "assessor_comment"]


def to_binary(label: str) -> str:
    """Collapse the 3-way label to the binary scale: only Да is correct — Частично counts
    as incorrect (matches the pipeline policy, where solved requires a strict yes)."""
    return "correct" if label == "yes" else "incorrect"


def load(path: str) -> list[dict]:
    return dataset.load_jsonl(path)


def load_labeled(path: str) -> list[dict]:
    """Rows carrying a usable 3-way human label (shared with eval_fast)."""
    return [r for r in load(path) if r.get("human_label") in LABELS]


_ORDER = {"no": 0, "partial": 1, "yes": 2}          # ordinal scale for within-1
_ORDER_BINARY = {"incorrect": 0, "correct": 1}


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
    # Per-class precision (judge said l → human agreed) / recall (human said l → judge found it).
    per_class = {}
    for l in labels:
        tp = confusion[l][l]
        prec = tp / col[l] if col[l] else 0.0
        rec = tp / row[l] if row[l] else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[l] = {"precision": round(prec * 100, 1), "recall": round(rec * 100, 1),
                        "f1": round(f1 * 100, 1), "support": row[l]}
    k = len(labels)
    return {
        "n": n,
        "agreement": round(po * 100, 1) if n else 0.0,
        "kappa": kappa,
        "within1": round(within1 / n * 100, 1) if n else 0.0,
        "per_class": per_class,
        "macro_precision": round(sum(c["precision"] for c in per_class.values()) / k, 1),
        "macro_recall": round(sum(c["recall"] for c in per_class.values()) / k, 1),
        "macro_f1": round(sum(c["f1"] for c in per_class.values()) / k, 1),
        "confusion": confusion,
    }


async def score_row(row: dict, binary: bool = False) -> dict:
    case = {"id": row["id"], "query": row.get("dialogue", ""), "operator_answer": row.get("operator_answer", "")}
    trace = {"case_id": row["id"], "answer": row.get("agent_answer", ""), "tool_calls": [], "chunks": []}
    # Always run the 3-way judge and collapse in code (finer reasoning beats a native binary
    # judge); the binary scale is correct vs incorrect, with the judge's partial → incorrect.
    result = await judge_resolution(case, trace)
    verdict = to_binary(result["verdict"]) if binary else result["verdict"]
    human = to_binary(row["human_label"]) if binary else row["human_label"]
    return {
        "id": row.get("id", ""),
        "human_label": human,
        "verdict": verdict,
        "failure_reason": result.get("failure_reason", "none"),
        "scope": row.get("scope", "unknown"),
        "intent": row.get("intent_norm", scope_mod.norm_intent(row.get("intent", ""))),
        "error": result.get("error"),   # {where, what_to_do, detail} when the judge call failed
        "reasoning": result.get("reasoning", ""),
        "agent_answer": row.get("agent_answer", ""),
        "operator_answer": row.get("operator_answer", ""),
        "dialogue": row.get("dialogue", ""),
        "assessor_comment": row.get("assessor_comment", ""),
    }


def agg_metrics(reports: list[dict]) -> dict:
    """mean ± std of the headline metrics across repeated runs (beats per-run non-determinism)."""
    out = {}
    for key in ("agreement", "kappa", "within1"):
        vals = [r[key] for r in reports]
        out[f"{key}_mean"] = round(statistics.mean(vals), 3)
        out[f"{key}_std"] = round(statistics.stdev(vals), 3) if len(vals) > 1 else 0.0
    return out


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


def _print_confusion(confusion: dict, labels: list) -> None:
    print("            " + "".join(f"{j:>11}" for j in labels))
    for h in labels:
        print(f"  {h:>10}  " + "".join(f"{confusion[h][j]:>11}" for j in labels))


def _print_pr(report: dict, labels: list) -> None:
    print("precision/recall (per class):")
    for l in labels:
        c = report["per_class"][l]
        print(f"  {l:>10}  P {c['precision']:5}%  R {c['recall']:5}%  F1 {c['f1']:5}%  (n={c['support']})")
    print(f"  {'macro':>10}  P {report['macro_precision']:5}%  R {report['macro_recall']:5}%  "
          f"F1 {report['macro_f1']:5}%")


def binary_collapse(records: list[dict]) -> dict:
    """Binary (correct vs incorrect, Частично → incorrect) summary derived from
    already-scored 3-way records — no extra judge calls."""
    pairs = [(to_binary(r["human_label"]), to_binary(r["verdict"])) for r in records]
    return summarize(pairs, LABELS_BINARY, _ORDER_BINARY)


def failure_breakdown(records: list[dict]) -> list[dict]:
    """Judge-flagged failures by reason: count + how many of those rows the human also
    graded incorrect (i.e. the flag is confirmed by ground truth)."""
    flagged = [r for r in records if r.get("failure_reason", "none") != "none"]
    out = []
    for reason in FAILURE_REASONS:
        sub = [r for r in flagged if r["failure_reason"] == reason]
        if sub:
            confirmed = sum(1 for r in sub if r["human_label"] not in ("yes", "correct"))
            out.append({"reason": reason, "count": len(sub), "human_also_incorrect": confirmed})
    return sorted(out, key=lambda x: -x["count"])


async def assign_scopes(rows: list[dict], classify: bool = False) -> None:
    """Attach `scope` + `intent_norm` to every row. Backend intent → deterministic rule;
    no intent → LLM classifier (when `classify`, cache-first) else "unknown"."""
    cache = scope_mod.load_cache() if classify else {}
    changed = False
    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)
    bar = progress.ProgressBar(len(rows) if classify else 0, label="scope")

    async def one(row):
        nonlocal changed
        intent = scope_mod.norm_intent(row.get("intent", ""))
        row["intent_norm"] = intent
        scope = scope_mod.scope_from_intent(intent)
        if not scope and classify:
            key = scope_mod.cache_key(row.get("dialogue", ""))
            if key in cache:
                scope = cache[key]["scope"]
            else:
                async with sem:
                    out = await scope_mod.classify_scope(row.get("dialogue", ""))
                scope = out["scope"]
                if scope != "unknown":   # don't cache failures — retry them next run
                    cache[key] = {"scope": scope, "topic": out.get("topic", "")}
                    changed = True
        row["scope"] = scope or "unknown"
        bar.advance(note=row.get("id", ""))

    try:
        await asyncio.gather(*(one(r) for r in rows))
    finally:
        bar.close()
    if changed:
        scope_mod.save_cache(cache)


def _binlabel(v: str) -> str:
    return v if v in LABELS_BINARY else to_binary(v)


def scope_summary(records: list[dict]) -> dict:
    """Per-segment view: how often the agent is correct there (human base rate) and how
    well the judge tracks the humans (binary agreement/kappa)."""
    out = {}
    for seg in SCOPES:
        sub = [r for r in records if r.get("scope") == seg]
        if not sub:
            continue
        s = summarize([(_binlabel(r["human_label"]), _binlabel(r["verdict"])) for r in sub],
                      LABELS_BINARY, _ORDER_BINARY)
        human_correct = sum(1 for r in sub if _binlabel(r["human_label"]) == "correct")
        out[seg] = {"n": len(sub), "human_correct_pct": round(human_correct / len(sub) * 100, 1),
                    "agreement": s["agreement"], "kappa": s["kappa"]}
    return out


def intent_summary(records: list[dict], min_n: int = 10) -> list[dict]:
    """Per-intent view for intents with enough rows: agent correctness (human) + judge
    binary agreement. Rows without an exported intent are excluded."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        if r.get("intent"):
            groups.setdefault(r["intent"], []).append(r)
    out = []
    for intent, sub in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(sub) < min_n:
            continue
        human_correct = sum(1 for r in sub if _binlabel(r["human_label"]) == "correct")
        agree = sum(1 for r in sub if _binlabel(r["human_label"]) == _binlabel(r["verdict"]))
        out.append({"intent": intent, "n": len(sub),
                    "human_correct_pct": round(human_correct / len(sub) * 100, 1),
                    "judge_agreement_pct": round(agree / len(sub) * 100, 1)})
    return out


def _print_scope(by_scope: dict) -> None:
    if not by_scope:
        return
    print("scope segments (binary):")
    for seg, s in by_scope.items():
        print(f"  {seg:>12}  n={s['n']:<5} human correct {s['human_correct_pct']:5}% | "
              f"judge agreement {s['agreement']}% | kappa {s['kappa']}")


def _print_intents(by_intent: list[dict]) -> None:
    if not by_intent:
        return
    print(f"top intents (n≥{10}):")
    for i in by_intent:
        print(f"  {i['intent'][:46]:<48} n={i['n']:<5} human correct {i['human_correct_pct']:5}% | "
              f"judge agreement {i['judge_agreement_pct']}%")


def _print_failures(breakdown: list[dict]) -> None:
    if not breakdown:
        return
    total = sum(b["count"] for b in breakdown)
    print(f"failure reasons (judge says incorrect, n={total}; confirmed = human also incorrect):")
    for b in breakdown:
        print(f"  {b['reason']:>15}  {b['count']:4}  (confirmed: {b['human_also_incorrect']})")


def judge_banner() -> str:
    """Human-readable description of the active resolution judge (shared with eval_fast)."""
    spec = config.get("resolution")
    panel = config.panel("resolution") if spec.available() else []
    if panel:
        models = "/".join(sorted({p.model for p in panel}))
        return f"panel of {len(panel)}: {', '.join(p.name for p in panel)} ({models}, effort {spec.effort})"
    if spec.available():
        return f"LLM ({spec.model}, effort {spec.effort})"
    return "stub (no LLM)"


async def main(dataset_path: str, csv_path: str | None = None, all_rows: bool = False,
               limit: int | None = None, binary: bool = False, repeat: int = 1,
               classify_scope: bool = False) -> None:
    rows = load_labeled(dataset_path)
    if limit:
        rows = rows[:limit]
    labels, order = (LABELS_BINARY, _ORDER_BINARY) if binary else (LABELS, _ORDER)
    print(f"judge: {judge_banner()}{'  [binary: correct/incorrect]' if binary else ''}"
          f"{f'  × {repeat} runs' if repeat > 1 else ''}")
    await assign_scopes(rows, classify_scope)

    sem = asyncio.Semaphore(config.JUDGE_CONCURRENCY)

    reports, last_records = [], None
    for i in range(repeat):
        label = f"scoring {i + 1}/{repeat}" if repeat > 1 else "scoring"
        bar = progress.ProgressBar(len(rows), label=label)

        async def scored(row, bar=bar):
            async with sem:
                rec = await score_row(row, binary)
            bar.advance(note=rec.get("id", ""))
            return rec

        ticker = asyncio.ensure_future(bar.tick())
        try:
            records = list(await asyncio.gather(*(scored(r) for r in rows)))
        finally:
            ticker.cancel()
            bar.close()
        report = summarize([(r["human_label"], r["verdict"]) for r in records], labels, order)
        reports.append(report)
        last_records = records
        if repeat > 1:
            print(f"  run {i + 1}/{repeat}: agreement {report['agreement']}% | "
                  f"kappa {report['kappa']} | within-1 {report['within1']}%")

    run_dir = Path("runs") / datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    out_csv = Path(csv_path) if csv_path else run_dir / ("rows.csv" if all_rows else "disagreements.csv")
    n_csv = write_csv(last_records, out_csv, all_rows)

    binary_summary = None if binary else binary_collapse(last_records)
    failures = failure_breakdown(last_records)
    by_scope = scope_summary(last_records)
    by_intent = intent_summary(last_records)
    # One assembly path for both modes; they differ only in the headline metrics
    # (mean ± std over runs vs the single run) and the confusion source.
    if repeat > 1:
        agg = agg_metrics(reports)
        conf = {h: {j: round(statistics.mean(r["confusion"][h][j] for r in reports), 1)
                    for j in labels} for h in labels}
        result = {"n": reports[0]["n"], "repeat": repeat, **agg, "runs": reports, "confusion_avg": conf}
        headline = (f"labeled rows: {result['n']} | agreement: {agg['agreement_mean']}%±{agg['agreement_std']} | "
                    f"kappa: {agg['kappa_mean']}±{agg['kappa_std']} | within-1: {agg['within1_mean']}%±{agg['within1_std']}")
        conf_title = "avg confusion (rows = human, cols = judge):"
    else:
        result = reports[0]
        conf = result["confusion"]
        headline = (f"labeled rows: {result['n']} | agreement: {result['agreement']}% | "
                    f"kappa: {result['kappa']} | within-1: {result['within1']}%")
        conf_title = "confusion (rows = human, cols = judge):"
    result.update({"failure_reasons": failures, "by_scope": by_scope, "by_intent": by_intent})
    if binary_summary:
        result["binary_collapse"] = binary_summary  # from the last run's records
    (run_dir / "calibration.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(headline)
    _print_pr(reports[-1], labels)
    print(conf_title)
    _print_confusion(conf, labels)
    if binary_summary:
        print(f"binary (correct = Да; Частично counts as incorrect): "
              f"agreement {binary_summary['agreement']}% | kappa {binary_summary['kappa']}")
        _print_pr(binary_summary, LABELS_BINARY)
    _print_failures(failures)
    _print_scope(by_scope)
    _print_intents(by_intent)
    print(f"{'rows' if all_rows else 'disagreements'}: {n_csv} → {out_csv}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resolution-judge calibration vs human labels")
    parser.add_argument("--dataset", default="data/labeled_sample.jsonl")
    parser.add_argument("--csv", default=None, help="CSV path (default: runs/<ts>/disagreements.csv)")
    parser.add_argument("--all-rows", action="store_true", help="write every row, not only disagreements")
    parser.add_argument("--limit", type=int, help="score only the first N labeled rows")
    parser.add_argument("--binary", action="store_true",
                        help="score on the binary scale: correct vs incorrect (Частично → incorrect)")
    parser.add_argument("--repeat", type=int, default=1, help="run N times → mean ± std (beats per-run noise)")
    parser.add_argument("--classify-scope", action="store_true",
                        help="LLM-classify scope for rows without a backend intent (cached)")
    args = parser.parse_args()
    asyncio.run(main(args.dataset, args.csv, args.all_rows, args.limit, args.binary, args.repeat,
                     args.classify_scope))
