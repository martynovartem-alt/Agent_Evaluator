"""
Dialogue scope: is this a query the transaction-history agent is supposed to handle
(operations history / commissions / transfer status / subscriptions / disputes), or
another bank topic where the correct agent behavior is to step aside (no_comments)?

Three sources, cheapest first:
1. `scope_from_intent` — deterministic rule on the backend intent label when the export
   carries one (72% of the current labeled set). All named intents are matched against
   domain markers; an unexpected intent without markers → out_of_scope.
2. `classify_scope` — LLM classifier (config role [scope], prompt prompts/scope.md) for
   rows without an exported intent. Same provider seam and Sandbox throttle as the judges.
3. Results are cached in data/scope_cache.json (gitignored), keyed by a dialogue hash —
   repeated calibration runs pay zero extra API calls.

Scope values: "in_scope" | "out_of_scope" | "unknown" (blank intent, classifier off/failed).
"""
import hashlib
import json
import re
from pathlib import Path

import config
from judges import _llm

CACHE_PATH = Path(__file__).parent.parent / "data" / "scope_cache.json"

# Domain markers for the agent's remit (matched against the *intent name*, lowercased).
IN_SCOPE_MARKERS = ("истори", "операц", "комисси", "перевод", "списан", "платеж", "платёж",
                    "подписк", "снят", "стикер", "баланс", "счёт", "счет", "статус")

_SCHEMA = {
    "type": "object",
    "properties": {
        "in_scope": {"type": "boolean"},
        "topic": {"type": "string"},
        "reasoning": {"type": "string"},
    },
    "required": ["in_scope", "topic", "reasoning"],
    "additionalProperties": False,
}


def norm_intent(raw: str) -> str:
    """Backend intent export → readable name(s). The export wraps names in a set literal
    (`{'Возврат комиссии обращение*'}`); blank and «Интент определен на бэке» (the label
    exists upstream but was not exported) both normalize to ""."""
    raw = (raw or "").strip()
    if not raw or "бэке" in raw.lower():
        return ""
    parts = re.findall(r"'([^']+)'", raw)
    if parts:
        return ", ".join(p.rstrip("*").strip() for p in parts)
    return raw.rstrip("*").strip()


def scope_from_intent(intent_norm: str) -> str:
    """Deterministic scope from a normalized intent name; "" when there is no intent."""
    if not intent_norm:
        return ""
    low = intent_norm.lower()
    return "in_scope" if any(m in low for m in IN_SCOPE_MARKERS) else "out_of_scope"


def cache_key(dialogue: str) -> str:
    return hashlib.sha1((dialogue or "").encode("utf-8")).hexdigest()[:16]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1))


async def classify_scope(dialogue: str) -> dict:
    """LLM scope call → {"scope", "topic", "reasoning"}. Unknown (not a guess) when the
    role is unavailable or errors — segmentation must not invent labels."""
    spec = config.get("scope")
    if not spec.available():
        return {"scope": "unknown", "topic": "", "reasoning": "[stub — no scope LLM]"}
    try:
        out = await _llm.judge_json(spec, spec.prompt_text(), {"dialogue": dialogue}, _SCHEMA)
        return {"scope": "in_scope" if out["in_scope"] else "out_of_scope",
                "topic": out.get("topic", ""), "reasoning": out.get("reasoning", "")}
    except Exception as e:
        return {"scope": "unknown", "topic": "", "reasoning": f"[scope error: {e}]"}
