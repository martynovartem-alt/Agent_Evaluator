"""
Central configuration — one LLM spec per agent/role in the pipeline.

Endpoints, models, and prompts live in `agents.toml` (one [section] per role: agent,
groundedness, resolution, plus [mcp]/[pipeline]). Secrets stay in the environment
(each role's `api_key_env`, falling back to ANTHROPIC_API_KEY); a local `.env` is loaded
for convenience. Real env vars override `.env`. Point elsewhere with EVAL_CONFIG=path.

    import config
    spec = config.get("resolution")      # AgentSpec: base_url, api_key, model, effort, prompt
    spec.available()                     # is this role's LLM usable?
    spec.prompt_text()                   # the role's system prompt
"""
import functools
import os
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None

ROOT = Path(__file__).parent


def parse_dotenv(text: str) -> dict[str, str]:
    """KEY=value lines → dict. Ignores blanks/`#` comments; strips quotes and inline ` # ...`."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        out[key.strip()] = value
    return out


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if path.exists():
        for key, value in parse_dotenv(path.read_text()).items():
            os.environ.setdefault(key, value)  # real env wins over .env


_load_dotenv()

_CONFIG_PATH = Path(os.getenv("EVAL_CONFIG", ROOT / "agents.toml"))
_RAW = tomllib.loads(_CONFIG_PATH.read_text()) if (tomllib and _CONFIG_PATH.exists()) else {}

_DEFAULTS = {
    "agent": {"model": "claude-opus-4-8", "prompt": "prompts/agent.md", "effort": "medium"},
    "groundedness": {"model": "claude-opus-4-8", "prompt": "prompts/groundedness.md", "effort": "medium"},
    "resolution": {"model": "claude-opus-4-8", "prompt": "prompts/resolution.md", "effort": "medium"},
    "scope": {"model": "claude-opus-4-8", "prompt": "prompts/scope.md", "effort": "medium"},
}


def _anthropic_present() -> bool:
    return importlib_util.find_spec("anthropic") is not None


@functools.lru_cache(maxsize=None)
def _prompt_text(path: str) -> str:
    """Prompts are read once per process — every LLM call re-reads them otherwise."""
    return (ROOT / path).read_text()


@dataclass(frozen=True)
class AgentSpec:
    role: str
    provider: str      # anthropic | openai (openai = OpenAI-compatible, e.g. DeepSeek)
    mode: str          # auto | llm | offline
    base_url: str      # "" → provider default endpoint
    api_key: str       # resolved from api_key_env / ANTHROPIC_API_KEY
    model: str
    effort: str        # low | medium | high (Anthropic only)
    prompt: str        # path, relative to repo root
    name: str = ""     # panelist name in panel voting; defaults to the role name
    system_id: str = ""  # `systemid` header (Alfa Sandbox/AlfaGen API); "" → header not sent
    rps: float = 0.0     # endpoint rate limit, requests/sec (Sandbox: 0.2); 0 → unlimited
    insecure: bool = False  # skip TLS verification (curl -k) — the Sandbox cert is self-signed;
                            # proper fix, no code: SSL_CERT_FILE=<bank CA PEM> in .env

    def prompt_text(self) -> str:
        return _prompt_text(self.prompt)

    def available(self) -> bool:
        if self.mode == "offline":
            return False
        if self.provider == "openai":
            return bool(self.api_key or self.base_url)  # stdlib client; internal endpoints may need no key
        return bool(self.api_key) and _anthropic_present()


def _base_cfg(role: str) -> dict:
    """Role section merged over defaults, with {ROLE}_MODE/{ROLE}_MODEL env overrides applied.
    Env overrides land here — on the base a panel entry inherits — so an explicit per-entry
    `model =`/`mode =` in agents.toml still wins over the env var."""
    cfg = {**_DEFAULTS[role], **_RAW.get(role, {})}
    if os.getenv(f"{role.upper()}_MODE"):
        cfg["mode"] = os.getenv(f"{role.upper()}_MODE")
    if os.getenv(f"{role.upper()}_MODEL"):                       # e.g. RESOLUTION_MODEL for A/B
        cfg["model"] = os.getenv(f"{role.upper()}_MODEL")
    return cfg


def _resolve(role: str, cfg: dict, name: str = "") -> AgentSpec:
    api_key = os.getenv(cfg.get("api_key_env") or "") or os.getenv("ANTHROPIC_API_KEY", "")
    return AgentSpec(
        role=role, provider=cfg.get("provider", "anthropic"), mode=cfg.get("mode", "auto"),
        base_url=cfg.get("base_url", ""), api_key=api_key,
        model=cfg["model"], effort=cfg.get("effort", "medium"), prompt=cfg["prompt"],
        name=name or role,
        system_id=cfg.get("system_id", ""), rps=float(cfg.get("rps", 0) or 0),
        insecure=bool(cfg.get("insecure", False)),
    )


def get(role: str) -> AgentSpec:
    """Resolve the LLM spec for a pipeline role (agent | groundedness | resolution)."""
    return _resolve(role, _base_cfg(role))


def panel(role: str) -> list[AgentSpec]:
    """Panel of judge specs for a role — [[role.panel]] entries in agents.toml.
    Each entry inherits the role's base section (with env overrides applied) and overrides
    fields (usually prompt/model); `name` defaults to the entry's prompt filename stem.
    Empty list = no panel (single judge). {ROLE}_PANEL=off disables the panel for A/B."""
    if (os.getenv(f"{role.upper()}_PANEL") or "").lower() in ("off", "0", "false"):
        return []
    base = _base_cfg(role)
    entries = base.pop("panel", None) or []
    return [
        _resolve(role, {**base, **entry},
                 entry.get("name") or Path(entry.get("prompt", base["prompt"])).stem)
        for entry in entries
    ]


def client_kwargs(spec: AgentSpec) -> dict:
    """kwargs for anthropic.(Async)Anthropic(...) — per-agent endpoint + key."""
    kwargs = {}
    if spec.base_url:
        kwargs["base_url"] = spec.base_url
    if spec.api_key:
        kwargs["api_key"] = spec.api_key
    return kwargs


# ── MCP tool backend + pipeline knobs (env overrides agents.toml) ──
_MCP = _RAW.get("mcp", {})
MCP_MODE = os.getenv("MCP_MODE") or _MCP.get("mode", "fixture")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT") or _MCP.get("transport", "stdio")
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL") or _MCP.get("server_url", "")
MCP_COMMAND = os.getenv("MCP_COMMAND") or _MCP.get("command", "")
MCP_TOOL_NAME = os.getenv("MCP_TOOL_NAME") or _MCP.get("tool_name", "get_transactions")
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN") or _MCP.get("auth_token", "")
JUDGE_CONCURRENCY = int(os.getenv("JUDGE_CONCURRENCY") or _RAW.get("pipeline", {}).get("concurrency", 6))
