"""
Minimal OpenAI-compatible chat client over stdlib HTTP — no `openai` SDK required.
Used for provider="openai" roles in agents.toml — the Alfa Sandbox / AlfaGen API
(see "4. Sandbox API.pdf").

Object model:
- RateLimiter — thread-safe reserved send slots per endpoint (the Sandbox allows 0.2 RPS;
  slots are spaced 1/rps apart and shared across all roles, panel votes and threads).
- OaiClient  — one chat() call: Sandbox headers (`systemid` from spec.system_id; a fresh
  unique `messageid` per request — a repeat is a documented 400), throttling, and typed
  errors (errors.ApiError / errors.NetworkError) that carry a remediation.

Module-level `chat()` / `_reserve_slot()` delegate to a default client so existing call
sites (agent.py, judges/_llm.py) and tests keep working unchanged.

Env: OAI_TIMEOUT — request timeout in seconds (default 180); the throttle sleeps before
sending, so the timeout only covers the HTTP call itself.

TLS: `insecure = true` on a role skips certificate verification (curl -k — the Sandbox
cert is self-signed). The proper fix needs no code: put the bank CA in SSL_CERT_FILE.
"""
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid

import errors
import privacy

DEFAULT_BASE_URL = "https://api.openai.com/v1"

_INSECURE_CTX = ssl._create_unverified_context()   # curl -k (PEP 476 escape hatch)


class RateLimiter:
    """Reserved send slots per endpoint, safe under concurrent threads."""

    def __init__(self):
        self._lock = threading.Lock()
        self._next_slot: dict[str, float] = {}   # endpoint -> next free send time (monotonic)

    def reserve(self, endpoint: str, rps: float, now: float) -> float:
        """Reserve the next slot for `endpoint` (normalized base_url); return seconds to sleep.
        rps == 0 → no limit, send immediately."""
        if not rps:
            return 0.0
        with self._lock:
            slot = max(now, self._next_slot.get(endpoint, 0.0))
            self._next_slot[endpoint] = slot + 1.0 / rps
        return slot - now


class OaiClient:
    """OpenAI-compatible chat over stdlib urllib, implementing the Sandbox contract."""

    def __init__(self, limiter: RateLimiter | None = None, timeout: float | None = None):
        self.limiter = limiter or RateLimiter()
        self.timeout = timeout if timeout is not None else float(os.getenv("OAI_TIMEOUT") or 180)

    def _headers(self, spec) -> dict:
        headers = {"Content-Type": "application/json"}
        if spec.api_key:  # hosted endpoints need a key; some internal ones accept none
            headers["Authorization"] = f"Bearer {spec.api_key}"
        if getattr(spec, "system_id", ""):
            headers["systemid"] = spec.system_id
            headers["messageid"] = str(uuid.uuid4())  # unique per request (Sandbox 400)
        return headers

    def chat(self, spec, messages: list, tools: list | None = None,
             response_format: dict | None = None, temperature: float = 0.0,
             max_tokens: int = 1024) -> dict:
        """POST /chat/completions for the role `spec`; returns the assistant message dict."""
        endpoint = (spec.base_url or DEFAULT_BASE_URL).rstrip("/")
        url = endpoint + "/chat/completions"
        # sanitize on → mask personal data first (the Sandbox DLP rejects it with
        # 400 HAS_PERSONAL_DATA); if standard masking is still rejected, retry ONCE strict
        attempts = [False, True] if getattr(spec, "sanitize", False) else [None]

        for strict in attempts:
            # 1. build the OpenAI-compatible request body (freshly masked per attempt)
            msgs = messages if strict is None else privacy.mask_messages(messages, strict)
            body = {"model": spec.model, "messages": msgs,
                    "temperature": temperature, "max_tokens": max_tokens}
            if tools:
                body["tools"] = tools
            if response_format:
                body["response_format"] = response_format
            # 2. wait for this endpoint's rate slot (Sandbox: one request per 5 s, shared by
            #    every role/panelist/thread; a retry queues like any other request).
            #    Throttle on the normalized endpoint so base_url spelling variants
            #    (trailing slash) cannot split the schedule and double the RPS.
            wait = self.limiter.reserve(endpoint, getattr(spec, "rps", 0.0), time.monotonic())
            if wait > 0:
                time.sleep(wait)
            # 3. send; every failure becomes a typed error carrying a what-to-do remediation
            req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                         headers=self._headers(spec))  # fresh messageid per attempt
            try:
                ctx = _INSECURE_CTX if getattr(spec, "insecure", False) else None
                with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                body_text = e.read().decode("utf-8")
                if strict is False and privacy.is_personal_data_error(body_text):
                    continue    # DLP still triggered → one more attempt with strict masking
                raise errors.ApiError.from_http(e.code, body_text, spec.model) from e
            except urllib.error.URLError as e:  # no VPN — or a cert failure, which needs its own fix
                fix = errors.NetworkError.cert_fix if "CERTIFICATE_VERIFY_FAILED" in str(e.reason) else None
                raise errors.NetworkError(f"{spec.model} cannot reach {url}: {e.reason}", fix) from e
        # 4. the assistant's reply is choices[0].message — anything else is a shape error
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise errors.LlmOutputError(f"{spec.model}: unexpected response shape: "
                                        f"{str(data)[:200]}") from e


_CLIENT = OaiClient()


def chat(spec, messages: list, tools: list | None = None, response_format: dict | None = None,
         temperature: float = 0.0, max_tokens: int = 1024) -> dict:
    return _CLIENT.chat(spec, messages, tools, response_format, temperature, max_tokens)


def _reserve_slot(endpoint: str, rps: float, now: float) -> float:
    return _CLIENT.limiter.reserve(endpoint, rps, now)
