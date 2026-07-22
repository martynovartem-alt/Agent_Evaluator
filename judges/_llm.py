"""
Shared LLM caller for the judges — one client per (endpoint, key), per provider, so each
role can point at a different endpoint/model (Anthropic or OpenAI-compatible, e.g. DeepSeek).

- Anthropic: structured outputs (json_schema) + effort determinism.
- OpenAI-compatible: JSON mode (response_format json_object); the schema is enforced via the
  judge prompt (which already specifies the exact object).
Model, effort, endpoint, key, and provider come from the role's config.AgentSpec.
"""
import asyncio
import json
import re

import config


def _extract_json(text: str) -> dict:
    """Parse a JSON object out of a model reply — tolerant of reasoning <think> blocks,
    markdown fences, and surrounding prose (portable across OpenAI-compatible models)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise

_anthropic_clients: dict[tuple, object] = {}


def _anthropic_client(spec):
    key = (spec.base_url, spec.api_key)
    if key not in _anthropic_clients:
        import anthropic
        _anthropic_clients[key] = anthropic.AsyncAnthropic(**config.client_kwargs(spec))
    return _anthropic_clients[key]


async def _judge_anthropic(spec, system: str, payload: dict, schema: dict) -> dict:
    response = await _anthropic_client(spec).messages.create(
        model=spec.model,
        max_tokens=1024,
        system=system,
        output_config={"effort": spec.effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


async def _judge_openai(spec, system: str, payload: dict, schema: dict) -> dict:
    import oai
    messages = [
        {"role": "system", "content": system + "\n\nReturn ONLY one JSON object matching the schema. Respond in json."},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    # temperature 0 for deterministic verdicts; no response_format (not all endpoints support it);
    # generous max_tokens so reasoning models can think before emitting the JSON.
    msg = await asyncio.to_thread(oai.chat, spec, messages, None, None, 0.0, 8192)
    return _extract_json(msg["content"])


async def judge_json(spec, system: str, payload: dict, schema: dict) -> dict:
    """Send payload (as JSON) under `system` to the role's endpoint; return the parsed object."""
    if spec.provider == "openai":
        return await _judge_openai(spec, system, payload, schema)
    return await _judge_anthropic(spec, system, payload, schema)
