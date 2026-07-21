# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt

# Full eval pipeline
python3 runner.py --dataset data/golden_mini.jsonl

# Single case
python3 runner.py --dataset data/golden_mini.jsonl --case-id subscription_299

# Tests (deterministic checks + tools; no API key needed) — run one: -k pattern via pytest, or:
python3 -m unittest discover -s tests -t .
python3 -m unittest tests.test_checks.TestToolsOk   # single class

# Ground-truth calibration (resolution judge vs human labels)
python3 dataset.py current_agent_answers.xlsx data/labeled.jsonl   # real data → jsonl (both gitignored)
python3 calibrate.py --dataset data/labeled.jsonl                  # or data/labeled_sample.jsonl
```

## Configuration (config.py / .env)

All API + MCP integration settings live in `config.py`, read from env or a local `.env`
(copy `.env.example` → `.env`; gitignored; real env vars win). Everything defaults to the
offline path, so the pipeline runs with no key.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the LLM agent + real judges |
| `AGENT_MODE` / `AGENT_MODEL` | `auto` / `claude-opus-4-8` | Agent: auto (LLM iff key) \| llm \| offline |
| `JUDGE_MODE` / `JUDGE_MODEL` / `JUDGE_EFFORT` | `auto` / `claude-opus-4-8` / `medium` | Judges (auto \| llm \| offline) |
| `JUDGE_CONCURRENCY` | `6` | calibrate fan-out |
| `MCP_MODE` | `fixture` | `fixture` (local data) \| `live` (real MCP server) |
| `MCP_TRANSPORT` / `MCP_SERVER_URL` / `MCP_COMMAND` / `MCP_TOOL_NAME` / `MCP_AUTH_TOKEN` | `stdio` / … | Live MCP connection (see `mcp_client.py`) |

`MCP_MODE=live` routes `MCPClear` through `mcp_client.py` (needs `pip install mcp`); adapt
`_tool_args` there to your server's tool schema. `config.api_available()` gates the LLM paths.

## Architecture

```
runner → trace per case → [groundedness + resolution + checks] (parallel) → aggregate → report + run diff
```

Domain: Alfa-Bank transaction-history support agent (Russian; replies prefixed
`final_answer:` / `no_comments:`). Real agent prompt: `agent_prompt_v2.md` → `prompts/agent.md`.

**Fixed constraints — do not redesign:**
- Judges read ONLY the captured trace, never live tools
- `checks.py` is deterministic, no LLM (spec: `docs/checks_spec.md`)
- Policy: `solved = resolution_yes AND no_critical_unsupported_claim AND tools_ok`
- Every run writes to `runs/<timestamp>/`, nothing is overwritten
- After each phase: run pipeline on `data/golden_mini.jsonl`, then commit

## Data flow

1. `runner.py` reads golden_set.jsonl → calls `run_agent()` (in `agent.py`) per case → writes `runs/<ts>/traces/<id>.json`
2. Per case, 3 evaluations run concurrently via `asyncio.gather`: `checks.run_checks()`, `judges/groundedness.py`, `judges/resolution.py`
3. `aggregate.py` applies policy → `runs/<ts>/report.json` (`{pct_solved, metrics{pct_must_facts,
   pct_tools_ok, pct_grounded, pct_instruction_ok, pct_planted_operation_ok, resolution_verdicts},
   cases[], diff}`; instruction/planted are gated to applicable cases) → prints a readable summary
   via `format_report`; diff vs the previous run's report

## Schemas

**golden_set.jsonl** (one JSON object per line):
`{id, query, intent, current_date, operator_answer, must_facts[], needs_history, fixture_user, planted_operation_id, expected_instruction}`

**trace** (written per case to `runs/<ts>/traces/`):
`{case_id, answer, tool_calls[{name, args, result}], chunks[{doc_id, text}]}`

**labeled.jsonl** (ground truth from `current_agent_answers.xlsx`, via `dataset.py`):
`{id, date, dialogue, agent_answer, operator_answer, human_label, assessor_comment}`
`human_label` ∈ `yes`/`partial`/`no` (Да/Частично/Нет; blank = unlabeled, skipped).
Agent answers already exist here — used to calibrate the judge, not to run the agent.

## Agent (agent.py + tools.py)

`run_agent(case)` dispatches to `run_llm_agent` (Claude, manual tool-use loop, system prompt
`prompts/agent.md`) or `run_offline_agent` (deterministic baseline). Both drive the same tools:
- `MCPClear(user, fromDate, toDate, operationAmount?)` — the MCP-served history tool. `tools.py`
  holds the offline fixture adapter; in production it is served over MCP. Swap behind that seam
  without touching the runner/judges. Calls land in `trace.tool_calls[]` (RAW MCP data).
- `getInstruction(names[])` — verified topic explanations (alfaSmart, alfaCheck, …). Results land
  in `trace.chunks[]` (grounding).

MCPClear reads `fixtures/operations.json` (hand-authored: `user_alfa`) **merged** with the
generated `data/mcp_fake.json` (`user_000N`, from `gen_mcp.py`) — disjoint users, non-destructive.
getInstruction reads `fixtures/instructions.json`. No network, so runs are reproducible.
**Amount encoding**: operation `amount = value / minorUnits`
rubles (value is in kopecks — 299 ₽ ⇒ `value: 29900, minorUnits: 100`). Fixture data backs the
golden cases: `fixture_user`/`planted_operation_id` in `operations.json`, `expected_instruction`
key in `instructions.json`. Real MCP format reference: `json_answer_history_operations.md`.

## Judges (judges/)

`judge_groundedness` and `judge_resolution` call Claude via `judges/_llm.py` (AsyncAnthropic,
structured outputs `output_config.format` json_schema, determinism via `output_config.effort`).
Prompts: `prompts/groundedness.md`, `prompts/resolution.md`.
- Groundedness reads trace only (`query`, `answer`, `tool_calls`, `chunks`) — never `operator_answer`.
- Resolution reads `query`, `answer`, `operator_answer`; **3-way** `verdict` ∈ yes/partial/no
  (matches the human labels); `resolution_yes` (= verdict=="yes") is derived in code and feeds the policy.

Env: `JUDGE_MODE` (auto|llm|offline), `JUDGE_MODEL` (default `claude-opus-4-8`), `JUDGE_EFFORT`
(low|medium|high, default medium), `JUDGE_CONCURRENCY` (calibrate fan-out, default 6). With no key
(or `JUDGE_MODE=offline`) the judges fall back to stubs so the pipeline runs keyless.

Note: the spec calls for temperature=0, but `claude-opus-4-8` rejects `temperature` (400).
Determinism comes from `effort` + structured outputs — not `temperature`.

## Calibration (calibrate.py)

The resolution judge's target is the human `Is agents answer correct?` label. `calibrate.py`
scores each labeled row's existing agent answer and reports exact-match agreement + a 3×3
confusion matrix (human × judge). This validates the judge — the "Ground Truth" arm of the
architecture. Baseline with the always-`yes` stub is ~8% (the `Да` share); the real judge
must beat it.

It also writes `runs/<ts>/disagreements.csv` (id, human_label, verdict, agree, judge reasoning,
agent/operator answers, dialogue, assessor_comment) — judge reasoning next to the human comment,
for prompt-tuning. `--all-rows` writes every row; `--csv PATH` overrides the location.
UTF-8-BOM so Excel renders the Cyrillic.
