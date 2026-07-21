"""
Shared LLM caller for the judges — one client per (endpoint, key), per provider, so each
role can point at a different endpoint/model (Anthropic or OpenAI-compatible, e.g. DeepSeek).

- Anthropic: structured outputs (json_schema) + effort determinism.
- OpenAI-compatible: JSON mode (response_format json_object); the schema is enforced via the
  judge prompt (which already specifies the exact object).
Model, effort, endpoint, key, and provider come from the role's config.AgentSpec.
"""
import json

import config

_anthropic_clients: dict[tuple, object] = {}
_openai_clients: dict[tuple, object] = {}


def _anthropic_client(spec):
    key = (spec.base_url, spec.api_key)
    if key not in _anthropic_clients:
        import anthropic
        _anthropic_clients[key] = anthropic.AsyncAnthropic(**config.client_kwargs(spec))
    return _anthropic_clients[key]


def _openai_client(spec):
    key = (spec.base_url, spec.api_key)
    if key not in _openai_clients:
        from openai import AsyncOpenAI
        _openai_clients[key] = AsyncOpenAI(api_key=spec.api_key, base_url=spec.base_url or None)
    return _openai_clients[key]


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
    response = await _openai_client(spec).chat.completions.create(
        model=spec.model,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": system + "\n\nReturn ONLY one JSON object matching the schema. Respond in json."},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


async def judge_json(spec, system: str, payload: dict, schema: dict) -> dict:
    """Send payload (as JSON) under `system` to the role's endpoint; return the parsed object."""
    if spec.provider == "openai":
        return await _judge_openai(spec, system, payload, schema)
    return await _judge_anthropic(spec, system, payload, schema)
