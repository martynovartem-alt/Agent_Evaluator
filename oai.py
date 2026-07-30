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

TLS: per-role `insecure` (skip verification — curl -k, for the Sandbox's self-signed cert)
and `ca_bundle` (verify against the bank CA PEM) flow in on the spec; see _ssl_context().
"""
import functools
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid

import errors

DEFAULT_BASE_URL = "https://api.openai.com/v1"


@functools.lru_cache(maxsize=None)
def _ssl_context(insecure: bool, ca_bundle: str) -> ssl.SSLContext | None:
    """TLS settings for a spec: None → default verification; insecure → skip it entirely
    (curl -k; wins over ca_bundle); ca_bundle → verify against that CA PEM instead.
    Cached — SSLContext is shareable across connections and threads."""
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False          # must precede verify_mode = CERT_NONE
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if ca_bundle:
        return ssl.create_default_context(cafile=ca_bundle)
    return None


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
        body = {"model": spec.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        # throttle on the same normalized endpoint as the URL, so base_url spelling
        # variants (trailing slash) cannot split the schedule and double the RPS
        endpoint = (spec.base_url or DEFAULT_BASE_URL).rstrip("/")
        url = endpoint + "/chat/completions"
        wait = self.limiter.reserve(endpoint, getattr(spec, "rps", 0.0), time.monotonic())
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers=self._headers(spec))
        try:
            ctx = _ssl_context(getattr(spec, "insecure", False), getattr(spec, "ca_bundle", ""))
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except FileNotFoundError as e:      # ca_bundle points at a missing PEM
            raise errors.ConfigError(f"ca_bundle not found: {getattr(spec, 'ca_bundle', '')}",
                                     "fix the ca_bundle path in agents.toml (or use insecure = true)") from e
        except urllib.error.HTTPError as e:
            raise errors.ApiError.from_http(e.code, e.read().decode("utf-8"), spec.model) from e
        except urllib.error.URLError as e:  # no VPN, or a cert failure — remediation differs
            raise errors.NetworkError.from_url_error(e.reason, spec.model, url) from e
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
