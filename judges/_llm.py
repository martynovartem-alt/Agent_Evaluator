"""
Shared Claude caller for the judges — one client per (endpoint, key) so each agent/role can
point at a different LLM endpoint.

- Structured JSON output via output_config.format (json_schema) — guaranteed-parseable object.
- Determinism/cost via output_config.effort (opus-4-8 rejects `temperature`).
The system prompt, model, effort, endpoint, and key all come from the role's config.AgentSpec.
"""
import json

import config

_clients: dict[tuple, object] = {}


def _client_for(spec: "config.AgentSpec"):
    key = (spec.base_url, spec.api_key)
    if key not in _clients:
        import anthropic
        _clients[key] = anthropic.AsyncAnthropic(**config.client_kwargs(spec))
    return _clients[key]


async def judge_json(spec: "config.AgentSpec", system: str, payload: dict, schema: dict) -> dict:
    """Send payload (as JSON) under `system` to the role's endpoint; constrain output to `schema`."""
    response = await _client_for(spec).messages.create(
        model=spec.model,
        max_tokens=1024,
        system=system,
        output_config={"effort": spec.effort, "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)
