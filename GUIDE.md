# Agent Evaluator — How It Works (Step by Step)

An offline evaluation harness for the Alfa-Bank support agent. It runs the agent on a set of
cases, captures what the agent did (its answer + tool calls), grades each case with
deterministic checks and two LLM judges, and produces a report. It can also **calibrate** the
judges against a set of human-labeled answers.

The shipped `agents.toml` points all three roles at the **Alfa internal Sandbox** (an
OpenAI-compatible endpoint serving Qwen), so out of the box it runs against real models. A fully
**keyless offline path** (deterministic offline agent + stub judges) is also built in — force it
with `*_MODE=offline` to see the whole pipeline work with no endpoint (step 2).

---

## Architecture at a glance

```
        agents.toml + .env ──► config   (per role: provider · endpoint · model · prompt · mode)
                                  │
  data/golden_mini.jsonl         ▼
     (cases) ────────► agent.py ────► TRACE ────► evaluations (run in parallel)
                        │  tools:    {answer,       ├─ checks.py           (deterministic)
                        │  MCPClear   tool_calls,   ├─ groundedness judge  (LLM)
                        │  getInstr.  chunks}       └─ resolution judge    (LLM · yes/partial/no)
                        ▼                                   │
               fixtures / live MCP                         ▼
                                         aggregate.py ──► runs/<ts>/report.json
                                         policy: solved = resolution yes ∧ grounded ∧ tools_ok
                                         + printed summary + diff vs previous run

  Calibration (separate entry point):
    current_agent_answers.xlsx ─► dataset.py ─► data/labeled.jsonl ─► calibrate.py ─► resolution judge
                                                       └─► agreement % + 3×3 confusion + disagreements.csv
```

---

## 1. Install

```bash
pip install -r requirements.txt          # anthropic (mcp is optional, for live MCP)
```

Python 3.11+ (uses stdlib `tomllib`). The default `provider = "openai"` roles talk to an
OpenAI-compatible endpoint over **stdlib HTTP** (`oai.py`) — no SDK needed; `anthropic` is only
required if you point a role back at `provider = "anthropic"`. The keyless offline run needs
nothing beyond the stdlib.

## 2. Run the eval

```bash
python3 runner.py --dataset data/golden_mini.jsonl
```

With the shipped config this runs against the **Alfa Sandbox** (needs `SANDBOX_API_KEY` + network
to the endpoint — steps 5–6). To run **fully offline / keyless** (deterministic agent + stub
judges), force offline mode:

```bash
AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline \
  python3 runner.py --dataset data/golden_mini.jsonl
```

Either way you'll see a summary and a per-run folder under `runs/<timestamp>/`:

```
Run runs/20260721T140727
Solved: 8/8 (100.0%)
  must_facts   100.0%
  tools_ok     100.0%
  grounded     100.0%
  instruction  100.0% (4 applicable)
  planted_op   100.0% (6 applicable)
  resolution   yes=8 partial=0 no=0
Cases:
  SOLVED subscription_299   yes
  ...
```

> In offline mode, `grounded` and `resolution` come from **stub judges** (always pass) — so
> `Solved` is inflated. The deterministic parts (`tools_ok`, `must_facts`, `instruction`,
> `planted_op`) are real. For real judge verdicts, run against a live endpoint (steps 5–6).

Run one case: `--case-id subscription_299`.

## 3. What happens inside the pipeline

For each case in the dataset:

1. **Agent runs** (`agent.py`). It reads the client's question and calls two tools:
   - `MCPClear` — transaction history (→ recorded in the trace as `tool_calls`)
   - `getInstruction` — verified help-center explanations (→ recorded as `chunks`)
   It then writes an answer (Russian, prefixed `final_answer:` or `no_comments:`).
2. **Trace is captured** and saved to `runs/<ts>/traces/<case_id>.json`:
   `{case_id, answer, tool_calls[], chunks[]}`.
3. **Three evaluations run in parallel** on that trace:
   - `checks.py` (deterministic, no LLM): `tools_ok`, `must_facts`, `instruction_ok`,
     `planted_operation_ok`.
   - **Groundedness judge**: does the answer claim anything not supported by the tool data?
   - **Resolution judge**: does the answer resolve the question? 3-way verdict `yes/partial/no`.
4. **Policy + report** (`aggregate.py`):
   `solved = resolution is "yes" AND no unsupported critical claim AND tools_ok`.
   Writes `runs/<ts>/report.json`, prints the summary, and diffs against the previous run.

**Key rule:** judges read **only the captured trace** (plus the operator's reference answer for
resolution) — never live tools. Every run writes a fresh `runs/<ts>/`; nothing is overwritten.

## 4. Reading the report

| Metric | Meaning | Source |
|---|---|---|
| `tools_ok` | `MCPClear` was called iff the case needs history | deterministic |
| `must_facts` | every required fact appears in the answer | deterministic |
| `instruction` | the expected help-center topic was retrieved (of applicable cases) | deterministic |
| `planted_op` | the planted operation was surfaced by the tool (of applicable cases) | deterministic |
| `grounded` | answer has no unsupported critical claim | LLM judge |
| `resolution` | yes / partial / no distribution | LLM judge |
| `Solved` | policy above | combined |

Per-case flags show what failed (e.g. `facts✗`, `instr✗`, `ungrounded`). `n/a` means the check
didn't apply to any case.

## 5. Configure each agent (agents.toml)

The pipeline has three LLM roles, each configured **independently** in `agents.toml`. Each role
picks a `provider` — `"anthropic"` or `"openai"` (any OpenAI-compatible endpoint) — so you can
mix providers across roles. The shipped config points every role at the Alfa Sandbox (Qwen):

```toml
[agent]                                    # the support agent under test
provider = "openai"                        # "anthropic" | "openai" (OpenAI-compatible)
base_url = "https://agenapisandbox.moscow.alfaintra.net/internal/llm/v1"
system_id = "sanduser"                     # Sandbox `systemid` header (406 without it)
rps      = 0.2                             # Sandbox hard limit — one request per 5 s, shared
insecure = true                            # Sandbox cert is self-signed — skip TLS verification (curl -k)
model    = "gpt-oss-120b"                  # the agent calls tools; Sandbox documents tools
                                           # only for QwQ-32B / llama-3.3 / gpt-oss-120b
prompt   = "agent_prompt_v2.md"            # its system prompt (swap the file to change it)
effort   = "medium"
mode     = "auto"                          # auto | llm | offline
api_key_env = "SANDBOX_API_KEY"            # env var holding its key (UUID issued by email)

[groundedness]   # ... own provider / endpoint / model / prompt ...
[resolution]     # ... own provider / endpoint / model / prompt ...
[mcp]            # tool backend (see step 7)
[pipeline]       # concurrency
```

To run the agent under test on one deployment and the judges on another, give each section a
different `provider` / `base_url` / `api_key_env`. To change an agent's behavior, edit its
`prompt` file or point `prompt` at a different file. For `provider = "anthropic"` leave
`base_url = ""` for the default (it must be Anthropic-compatible); for `provider = "openai"` set
`base_url` to any OpenAI-compatible `/v1` endpoint.

**Panel voting ("trial").** The resolution verdict is decided by a 3-judge panel declared as
`[[resolution.panel]]` entries (shipped bench: neutral **judge**, **prosecutor**, **defender** —
same verdict definitions, different scrutiny). Majority wins (any 2 of 3); a full
yes/partial/no split resolves to `partial`. Each entry inherits `[resolution]` and can override
`prompt`, `model`, even `base_url` — e.g. a mixed-model bench. `RESOLUTION_PANEL=off` reverts to
the single judge; note the panel triples resolution calls per case.

## 6. Go live (real agent + real judges)

Copy the env template and fill in the key each role's `api_key_env` names. The shipped config
uses **`SANDBOX_API_KEY`** (Alfa Sandbox) for all three roles:

```bash
cp .env.example .env
# .env:
#   SANDBOX_API_KEY=...           (Alfa Sandbox — what the shipped agents.toml points at)
#   ANTHROPIC_API_KEY=sk-...      (only if a role uses provider = "anthropic")
#   AGENT_MODE=llm                (optional; else agents.toml mode=auto uses the LLM when reachable)
```

Then run the pipeline — the real agent answers and the real judges grade:

```bash
python3 runner.py --dataset data/golden_mini.jsonl
```

`mode = auto` uses the LLM when the role is usable (a key is present, or an OpenAI-compatible
`base_url` is set); `offline` forces the deterministic baseline; `llm` forces the real model.
Any key left unset falls back to `ANTHROPIC_API_KEY`.

## 7. Connect a real MCP server (optional)

By default `MCPClear` reads local fixtures. To pull live transaction data, set in `agents.toml`
`[mcp]` (or via env):

```toml
[mcp]
mode      = "live"
transport = "stdio"                 # or "http"
command   = "python3 mcp_server.py" # stdio; or server_url for http
tool_name = "get_transactions"
```

Live mode needs `pip install mcp`. Adapt `_tool_args` in `mcp_client.py` to your server's tool
parameter names. (The transport code follows the MCP SDK but should be validated against your
server.)

## 8. Calibrate the judges against human labels

The ground truth is a spreadsheet of agent answers a human graded `Да / Частично / Нет`.

```bash
# 1) convert the xlsx to jsonl (real file is gitignored; kept local)
python3 dataset.py current_agent_answers.xlsx data/labeled.jsonl

# 2) score each answer with the resolution judge and compare to the human label
python3 calibrate.py --dataset data/labeled.jsonl
```

At the Sandbox 0.2 RPS limit a full run takes hours, so use the two-step flow: **preflight,
then full**. `eval_fast.py` judges 20 random dialogues and validates the *scheme* (valid
verdicts/failure reasons, no judge errors, live LLM — not the stub), prints an ETA for the
full set, and exits 0/1. `eval_full.py` runs the same preflight first and aborts if it
fails. Both accept the `.xlsx` directly (converted to a temp jsonl outside the repo):

```bash
python3 eval_fast.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"   # ~1 min
python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"   # preflight + all rows
```

Output: **agreement %** + a 3×3 confusion matrix (human × judge), per-class
**precision/recall/F1**, the headline **binary scale** (correct = Да only; **Частично counts as
incorrect**, matching the solved policy), and a **failure-reason breakdown** for judge-flagged
incorrect rows (wrong_operation / hallucination / incomplete / no_answer / missed_data / other,
each with how many the human also graded incorrect). Plus a `runs/<ts>/disagreements.csv`
listing every row where the judge and human differ — with the judge's failure reason and
reasoning next to the human's comment. Use that CSV to tune `prompts/resolution.md`, then
re-run. (`--all-rows` writes every row; `--csv PATH` overrides the location.)

To measure whether the panel actually beats the single judge, A/B the same labeled set:

```bash
python3 calibrate.py --dataset data/labeled.jsonl --repeat 3                       # panel
RESOLUTION_PANEL=off python3 calibrate.py --dataset data/labeled.jsonl --repeat 3  # single judge
```

Compare **kappa** (chance-corrected) — keep the panel only if it wins; with panel voting the
`reasoning` column in `disagreements.csv` carries every panelist's vote and argument.

Note: the resolution prompts carry few-shot examples **distilled (anonymized, paraphrased) from
labeled rows** — agreement on the handful of source rows is slightly optimistic, so judge the
calibration by the overall kappa, not by any single row.

## 9. Generate fake MCP data

`gen_mcp.py` builds synthetic transactions in the real MCP shape, using operation names mined
from the ground-truth data (all amounts/dates/accounts are fabricated):

```bash
# the committed dataset: 200 clients × 200 operations over the last 90 days
python3 gen_mcp.py --users 200 --ops-per-user 200 --days 90 --seed 7 --compact --out data/mcp_fake.json
```

`MCPClear` merges this with the hand-authored fixtures, so golden cases can be backed by these
users (`user_0001` …).

## 10. Add your own cases

Append a line to `data/golden_mini.jsonl`:

```json
{"id": "my_case", "query": "...", "current_date": "2026-07-20", "operator_answer": "final_answer: ...",
 "must_facts": ["..."], "needs_history": true, "fixture_user": "user_0001",
 "planted_operation_id": "G260602MOCOIPFBT00010", "expected_instruction": "alfaSmart"}
```

- `needs_history` → whether `MCPClear` must be called.
- `planted_operation_id` → an operation id that must appear in the tool result.
- `expected_instruction` → a `getInstruction` topic key (see `fixtures/instructions.json`).
- `must_facts` → strings that must appear in the answer.

## 11. Run the tests

```bash
python3 -m unittest discover -s tests -t .
```

Deterministic; no API key needed.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Banner `stub (no LLM)`; `resolution` all `yes` | Role not usable → judges run as stubs (only when no key **and** no `base_url`, or `*_MODE=offline`) | Set the role's key (e.g. `SANDBOX_API_KEY`) or a `base_url` in `agents.toml`; check `config.get("resolution").available()` |
| Agent answers look canned; `mode=llm` seems ignored | Running the offline baseline (no key) | Set a key and `[agent].mode = "llm"` (or `auto`) |
| `ModuleNotFoundError: anthropic` | `mode=llm` forced but SDK missing | `pip install anthropic` |
| `RuntimeError: MCP_MODE=live requires the MCP SDK — pip install mcp` | Live MCP without the SDK | `pip install mcp`, or set `[mcp].mode = "fixture"` |
| `RuntimeError: … requires MCP_COMMAND` / `MCP_SERVER_URL` | Live MCP without connection details | Set `command` (stdio) or `server_url` (http) in `agents.toml [mcp]` |
| Connection / 401 / 404 from the API | Wrong `base_url`, provider↔endpoint mismatch, or key↔endpoint mismatch | Match `provider` to the endpoint (`openai` for OpenAI-compatible, `anthropic` for Anthropic); for Anthropic leave `base_url = ""`; ensure the key matches the endpoint |
| `URLError` / DNS failure for `…alfaintra.net`; run crashes | Shipped config targets the Alfa Sandbox, reachable only on the Alfa network | Run on-network, or force the offline path: `AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline` (or blank `base_url` / set `mode = "offline"`) |
| `[SSL: CERTIFICATE_VERIFY_FAILED] … self-signed certificate` (on VPN) | The Sandbox presents a self-signed TLS cert; Python verifies by default | `insecure = true` in the role's `agents.toml` section (= `curl -k`; shipped on for Sandbox roles); proper fix: get the bank CA cert and set `SSL_CERT_FILE=/path/to/alfa_ca.pem` in `.env` |
| `HTTP 400 … "HAS_PERSONAL_DATA"` | The Sandbox DLP scans requests and rejects personal data (names, cards, emails in real dialogues) | `sanitize = true` in the role's section (shipped on) masks the text via `privacy.py` and retries once with strict masking; if a row still fails, find the trigger in its dialogue and extend the patterns in `privacy.py` |
| `agents.toml` ignored (defaults used) | Python < 3.11 (no `tomllib`) | Use Python 3.11+ |
| Live MCP: `KeyError: 'amount'` / wrong amounts | Server tool schema/shape differs | Adapt `_tool_args` in `mcp_client.py`; server must return `{operations:[{…,"amount":{"value","minorUnits"}}]}` (rubles = value/minorUnits) |

---

## File map

| Path | Role |
|---|---|
| `runner.py` | orchestrates a run: agent → trace → evaluations → report |
| `agent.py` | the support agent (LLM tool-use loop + offline baseline) |
| `tools.py` / `mcp_client.py` | `MCPClear` + `getInstruction` (fixtures or live MCP) |
| `checks.py` | deterministic checks |
| `judges/` | groundedness + resolution judges (`_llm.py` = shared client) |
| `oai.py` | OpenAI-compatible chat client (stdlib HTTP; used by `provider = "openai"` roles) |
| `aggregate.py` | policy + report + run diff |
| `calibrate.py` / `dataset.py` | judge calibration vs the labeled ground truth |
| `gen_mcp.py` | synthetic MCP dataset generator |
| `config.py` / `agents.toml` / `.env` | per-agent endpoint / model / prompt config |
| `prompts/` | one system prompt per agent |
| `fixtures/` / `data/` | tool fixtures + datasets |
| `runs/` | per-run output (traces, report, calibration) — gitignored |
