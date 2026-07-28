"""
Minimal OpenAI-compatible chat client over stdlib HTTP — no `openai` SDK required.
Used for provider="openai" roles in agents.toml: the Alfa Sandbox / AlfaGen API
(see "4. Sandbox API.pdf") and any other OpenAI-compatible endpoint (DeepSeek, etc.).

Alfa Sandbox specifics (all driven by the role's AgentSpec, no code changes needed):
- `systemid` header (spec.system_id, e.g. "sanduser") — required; missing/wrong → 406.
- `messageid` header — generated fresh (UUID) per request; reusing one is a documented 400.
- Rate limit (spec.rps, Sandbox allows 0.2 RPS): a thread-safe per-endpoint throttle
  reserves send slots, so concurrent runner/calibrate threads queue instead of tripping 429s.

Returns the assistant `message` dict: {role, content, tool_calls?}.

Env: OAI_TIMEOUT — request timeout in seconds (default 180). At 0.2 RPS queued threads
sleep before sending, so the timeout only covers the HTTP call itself.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

_TIMEOUT = float(os.getenv("OAI_TIMEOUT") or 180)

_slot_lock = threading.Lock()
_next_slot: dict[str, float] = {}   # base_url -> next free send time (monotonic)


def _reserve_slot(endpoint: str, rps: float, now: float) -> float:
    """Reserve the next send slot for `endpoint` (normalized base_url) and return how long
    to sleep. Slots are spaced 1/rps apart, shared across all roles/threads on the endpoint."""
    if not rps:
        return 0.0
    with _slot_lock:
        slot = max(now, _next_slot.get(endpoint, 0.0))
        _next_slot[endpoint] = slot + 1.0 / rps
    return slot - now


def chat(spec, messages: list, tools: list | None = None, response_format: dict | None = None,
         temperature: float = 0.0, max_tokens: int = 1024) -> dict:
    body = {"model": spec.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    if response_format:
        body["response_format"] = response_format
    endpoint = (spec.base_url or "https://api.openai.com/v1").rstrip("/")
    url = endpoint + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if spec.api_key:  # hosted endpoints need a key; some internal ones accept none
        headers["Authorization"] = f"Bearer {spec.api_key}"
    if getattr(spec, "system_id", ""):
        headers["systemid"] = spec.system_id
        headers["messageid"] = str(uuid.uuid4())  # must be unique per request (Sandbox 400)
    # throttle on the same normalized endpoint as the URL, so base_url spelling variants
    # (trailing slash) cannot split the schedule and double the effective RPS
    wait = _reserve_slot(endpoint, getattr(spec, "rps", 0.0), time.monotonic())
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{spec.model} HTTP {e.code}: {e.read().decode('utf-8')[:300]}") from e
    except urllib.error.URLError as e:  # Sandbox is VPN/VDI-only — surface a clear hint
        raise RuntimeError(f"{spec.model} cannot reach {url}: {e.reason} "
                           f"(Sandbox API requires the bank VPN/VDI network)") from e
    return data["choices"][0]["message"]
