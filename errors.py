"""
Typed error hierarchy for the eval pipeline. Every error knows WHERE it happened
(`where` ∈ dataset | config | api | llm_output) and WHAT TO DO about it (`what_to_do`),
so scripts like eval_fast can print an actionable diagnosis instead of a raw traceback.

The `api` remediations encode the Sandbox error table from "4. Sandbox API.pdf"
(406 → systemid, 400 → messageid/params, 429 → the 0.2 RPS limit, timeouts → VPN).
"""


class EvalError(Exception):
    """Base: an error with a location and a remediation."""
    where = "unknown"
    default_fix = "see the logs above"

    def __init__(self, message: str, what_to_do: str | None = None):
        super().__init__(message)
        self.what_to_do = what_to_do or self.default_fix

    def info(self) -> dict:
        """Structured form carried through judge results into diagnoses."""
        return {"where": self.where, "what_to_do": self.what_to_do, "detail": str(self)}


class DatasetError(EvalError):
    """The input data is wrong: file missing, no labeled sheet, unexpected columns."""
    where = "dataset"
    default_fix = ("check the input file: it must be dataset.py output (.jsonl) or the "
                   "assessors' .xlsx with the labeled sheet (headers like 'agent answer', "
                   "'Is agents answer correct?')")


class ConfigError(EvalError):
    """The pipeline setup is wrong: role unavailable, key missing, offline mode."""
    where = "config"
    default_fix = ("set SANDBOX_API_KEY in .env, check the role's section in agents.toml, "
                   "and make sure *_MODE is not 'offline'")


class ApiError(EvalError):
    """The endpoint answered with an error, or could not be reached."""
    where = "api"
    default_fix = "check the endpoint and your access; see '4. Sandbox API.pdf' error table"

    def __init__(self, message: str, what_to_do: str | None = None, status: int | None = None):
        super().__init__(message, what_to_do)
        self.status = status

    _FIXES = {
        400: "bad request — often a repeated messageid or an oversized message; check params",
        401: "the key is invalid or expired (Sandbox keys live 3 months) — request a new one",
        403: "the key is invalid/forbidden for this model — check SANDBOX_API_KEY and the model name",
        404: "wrong endpoint path — base_url must end with /internal/llm/v1 (Sandbox)",
        406: "the systemid header is missing/wrong — set system_id = \"sanduser\" in agents.toml",
        413: "message too large for the model — shorten the dialogue or pick a larger-context model",
        422: "invalid body param or unsupported model — check the model name against the Sandbox list",
        429: "rate limit exceeded — keep rps = 0.2 in agents.toml (one request per 5 s)",
        500: "Sandbox internal error — retry later or ask in the AlfaGen Club chat",
        503: "the model is not answering — retry later or pick another model from the list",
        523: "the model returned an error — retry; if it persists, ask in the AlfaGen Club chat",
    }

    @classmethod
    def from_http(cls, status: int, body: str, model: str) -> "ApiError":
        return cls(f"{model} HTTP {status}: {body[:300]}",
                   cls._FIXES.get(status, cls.default_fix), status=status)


class NetworkError(ApiError):
    """The endpoint is unreachable — for the Sandbox that almost always means no VPN."""
    default_fix = ("connect to the bank VPN/VDI — the Sandbox is intranet-only; "
                   "off-network use *_MODE=offline for the stub path")


class LlmOutputError(EvalError):
    """The model answered, but not in the required format (JSON/schema violation)."""
    where = "llm_output"
    default_fix = ("the model ignores the output schema — check the role's prompt, try a "
                   "stricter format instruction, or switch the model in agents.toml")


def error_info(e: Exception) -> dict:
    """Structured {where, what_to_do, detail} for any exception (typed or foreign)."""
    if isinstance(e, EvalError):
        return e.info()
    import json
    if isinstance(e, (json.JSONDecodeError, KeyError, TypeError)):
        return LlmOutputError(str(e) or type(e).__name__).info()
    return {"where": "api", "what_to_do": ApiError.default_fix, "detail": f"{type(e).__name__}: {e}"}
