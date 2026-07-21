"""
Central configuration for the eval — API and MCP integrations in one place.

Reads environment variables, optionally from a local `.env` file (copy `.env.example` → `.env`;
`.env` is gitignored). Real environment variables take precedence over `.env`.

    import config
    config.api_available()      # is the Anthropic API usable?
    config.MCP_MODE             # "fixture" | "live"
"""
import os
from pathlib import Path


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
    path = Path(__file__).parent / ".env"
    if path.exists():
        for key, value in parse_dotenv(path.read_text()).items():
            os.environ.setdefault(key, value)  # real env wins over .env


_load_dotenv()

# ── Anthropic API (judges + LLM agent) ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ── Agent under test ──
AGENT_MODE = os.getenv("AGENT_MODE", "auto")            # auto | llm | offline
AGENT_MODEL = os.getenv("AGENT_MODEL", "claude-opus-4-8")

# ── Judges ──
JUDGE_MODE = os.getenv("JUDGE_MODE", "auto")            # auto | llm | offline
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-opus-4-8")
JUDGE_EFFORT = os.getenv("JUDGE_EFFORT", "medium")      # low | medium | high
JUDGE_CONCURRENCY = int(os.getenv("JUDGE_CONCURRENCY", "6"))

# ── MCP integration (backs MCPClear / get_transactions) ──
MCP_MODE = os.getenv("MCP_MODE", "fixture")             # fixture | live
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")     # stdio | http
MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "")        # http transport endpoint
MCP_COMMAND = os.getenv("MCP_COMMAND", "")              # stdio transport, e.g. "python3 mcp_server.py"
MCP_TOOL_NAME = os.getenv("MCP_TOOL_NAME", "get_transactions")  # tool name on the MCP server
MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")        # optional bearer for http transport


def api_available() -> bool:
    """True iff an API key is set and the anthropic SDK is importable."""
    if not ANTHROPIC_API_KEY:
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False
