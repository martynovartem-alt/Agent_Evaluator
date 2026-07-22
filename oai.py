"""
Minimal OpenAI-compatible chat client over stdlib HTTP — no `openai` SDK required.
Used for provider="openai" roles (e.g. DeepSeek) in agents.toml.

Returns the assistant `message` dict: {role, content, tool_calls?}.
"""
import json
import urllib.error
import urllib.request


def chat(spec, messages: list, tools: list | None = None, response_format: dict | None = None,
         temperature: float = 0.0, max_tokens: int = 1024) -> dict:
    body = {"model": spec.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
    if response_format:
        body["response_format"] = response_format
    url = (spec.base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if spec.api_key:  # internal endpoints may need no key
        headers["Authorization"] = f"Bearer {spec.api_key}"
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{spec.model} HTTP {e.code}: {e.read().decode('utf-8')[:300]}") from e
    return data["choices"][0]["message"]
