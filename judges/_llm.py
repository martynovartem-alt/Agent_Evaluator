"""
Shared Claude client for the judges.

- Structured JSON output via output_config.format (json_schema) — guarantees a parseable object.
- Determinism/cost via output_config.effort (opus-4-8 rejects `temperature`, so we don't send it).
- Async client so a case's two judges run concurrently.

Env: JUDGE_MODEL (default claude-opus-4-8), JUDGE_EFFORT (low|medium|high, default medium),
JUDGE_MODE (auto|llm|offline — offline forces the stub fallback in the judges).
"""
import json
import os
from pathlib import Path

MODEL = os.getenv("JUDGE_MODEL", "claude-opus-4-8")
EFFORT = os.getenv("JUDGE_EFFORT", "medium")
PROMPTS = Path(__file__).parent.parent / "prompts"

_client = None


def available() -> bool:
    """True iff the real judge LLM can be used (mode not offline, key set, SDK importable)."""
    if os.getenv("JUDGE_MODE") == "offline":
        return False
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text()


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.AsyncAnthropic()
    return _client


async def judge_json(system: str, payload: dict, schema: dict) -> dict:
    """Send payload (as JSON) under `system`, constrain output to `schema`, return the parsed object."""
    response = await _get_client().messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system,
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
