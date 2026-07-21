# Agent Evaluator — How It Works (Step by Step)

An offline evaluation harness for the Alfa-Bank support agent. It runs the agent on a set of
cases, captures what the agent did (its answer + tool calls), grades each case with
deterministic checks and two LLM judges, and produces a report. It can also **calibrate** the
judges against a set of human-labeled answers.

Everything runs with **no API key** by default (a deterministic offline agent + stub judges),
so you can see the whole pipeline work before plugging in real models.

---

## Architecture at a glance

```
        agents.toml + .env ──► config   (per role: endpoint · model · prompt · mode)
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

Python 3.11+ (uses stdlib `tomllib`). No key needed for the offline run.

## 2. Run the eval (offline)

```bash
python3 runner.py --dataset data/golden_mini.jsonl
```

You'll see a summary and a per-run folder under `runs/<timestamp>/`:

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

> Offline, `grounded` and `resolution` come from **stub judges** (always pass) — so `Solved`
> is inflated. The deterministic parts (`tools_ok`, `must_facts`, `instruction`, `planted_op`)
> are real. To get real judge verdicts, add a key (step 6).

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

The pipeline has three LLM roles, each configured **independently** in `agents.toml`:

```toml
[agent]                       # the support agent under test
base_url = ""                 # its LLM endpoint ("" = Anthropic default)
model    = "claude-opus-4-8"
prompt   = "prompts/agent.md" # its system prompt (swap the file to change it)
effort   = "medium"
mode     = "auto"             # auto | llm | offline
api_key_env = "AGENT_API_KEY" # env var holding its key

[groundedness]   # ... own endpoint / model / prompt ...
[resolution]     # ... own endpoint / model / prompt ...
[mcp]            # tool backend (see step 7)
[pipeline]       # concurrency
```

To run the agent under test on one deployment and the judges on another, give each section a
different `base_url` / `api_key_env`. To change an agent's behavior, edit its `prompt` file or
point `prompt` at a different file. `base_url` must be an Anthropic-compatible endpoint.

## 6. Go live (real agent + real judges)

Copy the env template and fill in keys:

```bash
cp .env.example .env
# .env:
#   ANTHROPIC_API_KEY=sk-...      (or per-role AGENT_API_KEY / JUDGE_API_KEY)
#   AGENT_MODE=llm                (optional; else agents.toml mode=auto uses the LLM when a key exists)
```

Then run the pipeline again — the real agent answers and the real judges grade:

```bash
python3 runner.py --dataset data/golden_mini.jsonl
```

`mode = auto` uses the LLM automatically when a key is available; `offline` forces the
deterministic baseline; `llm` forces the real model.

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

Output: **agreement %** + a 3×3 confusion matrix (human × judge), plus a
`runs/<ts>/disagreements.csv` listing every row where the judge and human differ — with the
judge's reasoning next to the human's comment. Use that CSV to tune `prompts/resolution.md`, then
re-run. (`--all-rows` writes every row; `--csv PATH` overrides the location.)

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
| Banner `stub (no LLM)`; `resolution` all `yes` | No API key → judges run as stubs | Set `ANTHROPIC_API_KEY` (or `JUDGE_API_KEY`) in `.env`; check `config.get("resolution").available()` |
| Agent answers look canned; `mode=llm` seems ignored | Running the offline baseline (no key) | Set a key and `[agent].mode = "llm"` (or `auto`) |
| `ModuleNotFoundError: anthropic` | `mode=llm` forced but SDK missing | `pip install anthropic` |
| `RuntimeError: MCP_MODE=live requires the MCP SDK — pip install mcp` | Live MCP without the SDK | `pip install mcp`, or set `[mcp].mode = "fixture"` |
| `RuntimeError: … requires MCP_COMMAND` / `MCP_SERVER_URL` | Live MCP without connection details | Set `command` (stdio) or `server_url` (http) in `agents.toml [mcp]` |
| Connection / 401 / 404 from the API | Wrong or non-Anthropic `base_url`, or key↔endpoint mismatch | Leave `base_url = ""` for the default; it must be Anthropic-compatible; ensure the key matches the endpoint |
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
| `aggregate.py` | policy + report + run diff |
| `calibrate.py` / `dataset.py` | judge calibration vs the labeled ground truth |
| `gen_mcp.py` | synthetic MCP dataset generator |
| `config.py` / `agents.toml` / `.env` | per-agent endpoint / model / prompt config |
| `prompts/` | one system prompt per agent |
| `fixtures/` / `data/` | tool fixtures + datasets |
| `runs/` | per-run output (traces, report, calibration) — gitignored |
