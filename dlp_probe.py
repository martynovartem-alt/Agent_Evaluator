"""
Find WHAT the Sandbox DLP flags in a failing row — empirically, by bisection.

The DLP's documented rules (bank account + card numbers) do not explain every
HAS_PERSONAL_DATA rejection we see, and guessing patterns costs a VPN round-trip per guess.
This probe runs ON the bank machine and asks the Sandbox directly: it sends progressively
smaller pieces of the row's text (minimal prompt, max_tokens=1 — each request is tiny) and
binary-searches the smallest contiguous fragment that still triggers the rejection.

    python3 dlp_probe.py --row row_132
    python3 dlp_probe.py --row row_132 --dataset "Agents-new-answers(after_20_07_2026).xlsx"

Sequence (~25 requests ≈ 2 min at the 0.2 RPS limit, progress printed per step):
  1. full text, standard mask   → does the normal first attempt pass?
  2. full text, strict mask     → does the retry pass?
  3. full text, no mask         → baseline trigger check
  4. line bisection             → minimal line window that triggers
  5. word bisection in-window   → minimal word window
The result is printed and appended to runs/dlp_probe.log. Send that fragment back and the
masking gets a pattern for exactly it — no more guessing.
"""
import argparse
import dataclasses
import sys

import config
import dataset
import errors
import oai
import privacy


def _probe_spec():
    """The resolution role's endpoint with masking/retry OFF — the probe must observe the
    raw DLP behavior for each fragment (the shared throttle still applies)."""
    return dataclasses.replace(config.get("resolution"), sanitize=False)


def triggers(spec, text: str) -> bool:
    """True iff the Sandbox rejects `text` with HAS_PERSONAL_DATA. Minimal request:
    the fragment is the whole user message; the reply itself is irrelevant."""
    try:
        oai.chat(spec, [{"role": "user", "content": text}], max_tokens=1)
        return False
    except errors.ApiError as e:
        if privacy.is_personal_data_error(str(e)):
            return True
        raise   # some other API problem — surface it, don't misreport as "no trigger"


def bisect_units(spec, units: list[str], joiner: str, label: str) -> list[str]:
    """Smallest contiguous window of `units` that still triggers, by prefix/suffix bisection."""
    def test(i: int, j: int) -> bool:
        print(f"  {label} [{i}:{j}] of {len(units)} ...", end=" ", flush=True)
        hit = triggers(spec, joiner.join(units[i:j]))
        print("TRIGGERS" if hit else "clean")
        return hit

    lo, hi = 1, len(units)          # minimal prefix end j: [0:j] triggers
    while lo < hi:
        mid = (lo + hi) // 2
        if test(0, mid):
            hi = mid
        else:
            lo = mid + 1
    end = lo
    lo, hi = 0, end - 1             # maximal window start i: [i:end] still triggers
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if test(mid, end):
            lo = mid
        else:
            hi = mid - 1
    return units[lo:end]


def main() -> int:
    p = argparse.ArgumentParser(description="Bisect a row's text to the fragment the DLP flags")
    p.add_argument("--row", required=True, help="row id from the smoke_errors log, e.g. row_132")
    p.add_argument("--dataset", default="Agents-new-answers(after_20_07_2026).xlsx")
    args = p.parse_args()

    recs = {r["id"]: r for r in dataset.parse_xlsx(args.dataset)}
    if args.row not in recs:
        sys.exit(f"{args.row} not found in {args.dataset}")
    r = recs[args.row]
    text = "\n".join((r["dialogue"], r["agent_answer"], r["operator_answer"]))
    spec = _probe_spec()
    print(f"probing {args.row} against {spec.base_url} as {spec.model} "
          f"(~25 requests, one per 5 s — a couple of minutes)")

    print("1) full text, standard mask:", end=" ", flush=True)
    std = not triggers(spec, privacy.mask(text))
    print("passes" if std else "TRIGGERS")
    print("2) full text, strict mask:  ", end=" ", flush=True)
    str_ok = not triggers(spec, privacy.mask(text, strict=True))
    print("passes" if str_ok else "TRIGGERS")
    if std:
        print("the standard mask already passes — rerun eval_fast; if it still fails there, "
              "the trigger is in the judge prompt or panel wiring, not this row")
        return 0
    print("3) full text, no mask:      ", end=" ", flush=True)
    print("TRIGGERS" if triggers(spec, text) else "passes (?!)")

    lines = [l for l in text.splitlines() if l.strip()]
    window = bisect_units(spec, lines, "\n", "lines")
    words = window[0].split() if len(window) == 1 else None
    if words and len(words) > 6:
        words = bisect_units(spec, words, " ", "words")
    fragment = " ".join(words) if words else "\n".join(window)

    print("\n═══ minimal triggering fragment ═══")
    print(fragment)
    from pathlib import Path
    log = Path("runs") / "dlp_probe.log"
    log.parent.mkdir(exist_ok=True)
    with open(log, "a") as f:
        f.write(f"=== {args.row} ===\n{fragment}\n\n")
    print(f"(appended to {log} — send this fragment back to get a mask pattern for it)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
