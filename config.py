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
}


def _sdk_present(provider: str) -> bool:
    return importlib_util.find_spec("openai" if provider == "openai" else "anthropic") is not None


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

    def prompt_text(self) -> str:
        return (ROOT / self.prompt).read_text()

    def available(self) -> bool:
        if self.mode == "offline":
            return False
        return bool(self.api_key) and _sdk_present(self.provider)


def get(role: str) -> AgentSpec:
    """Resolve the LLM spec for a pipeline role (agent | groundedness | resolution)."""
    cfg = {**_DEFAULTS[role], **_RAW.get(role, {})}
    api_key = os.getenv(cfg.get("api_key_env") or "") or os.getenv("ANTHROPIC_API_KEY", "")
    mode = os.getenv(f"{role.upper()}_MODE") or cfg.get("mode", "auto")
    return AgentSpec(
        role=role, provider=cfg.get("provider", "anthropic"), mode=mode,
        base_url=cfg.get("base_url", ""), api_key=api_key,
        model=cfg["model"], effort=cfg.get("effort", "medium"), prompt=cfg["prompt"],
    )


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
