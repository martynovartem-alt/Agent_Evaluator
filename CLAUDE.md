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
```

**Agent mode** (`AGENT_MODE`): `auto` (default — real Claude agent if `ANTHROPIC_API_KEY`
+ `anthropic` SDK are present, else offline baseline), `llm` (force real agent), `offline`
(force deterministic baseline). `AGENT_MODEL` overrides the agent model (default `claude-opus-4-8`).
The offline baseline drives the same real tool layer, so the full pipeline runs and is
verifiable with no API key.

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
3. `aggregate.py` applies policy → writes `runs/<ts>/report.json` + diff vs previous run

## Schemas

**golden_set.jsonl** (one JSON object per line):
`{id, query, intent, current_date, operator_answer, must_facts[], needs_history, fixture_user, planted_operation_id, expected_instruction}`

**trace** (written per case to `runs/<ts>/traces/`):
`{case_id, answer, tool_calls[{name, args, result}], chunks[{doc_id, text}]}`

## Agent (agent.py + tools.py)

`run_agent(case)` dispatches to `run_llm_agent` (Claude, manual tool-use loop, system prompt
`prompts/agent.md`) or `run_offline_agent` (deterministic baseline). Both drive the same tools:
- `MCPClear(user, fromDate, toDate, operationAmount?)` — the MCP-served history tool. `tools.py`
  holds the offline fixture adapter; in production it is served over MCP. Swap behind that seam
  without touching the runner/judges. Calls land in `trace.tool_calls[]` (RAW MCP data).
- `getInstruction(names[])` — verified topic explanations (alfaSmart, alfaCheck, …). Results land
  in `trace.chunks[]` (grounding).

Tools read only from `fixtures/` (`operations.json` in the real MCP shape, `instructions.json`)
— no network, so runs are reproducible. **Amount encoding**: operation `amount = value / minorUnits`
rubles (value is in kopecks — 299 ₽ ⇒ `value: 29900, minorUnits: 100`). Fixture data backs the
golden cases: `fixture_user`/`planted_operation_id` in `operations.json`, `expected_instruction`
key in `instructions.json`. Real MCP format reference: `json_answer_history_operations.md`.

## Judges (implemented in Phase 4)

Prompts live in `prompts/groundedness.md` and `prompts/resolution.md`.
Both judges call Claude API and return structured JSON.
Judge stubs return fixed passing values so Phase 1–3 pipeline runs end-to-end.

Note: the spec calls for temperature=0, but `claude-opus-4-8` rejects `temperature` (400).
Get judge determinism via `output_config.effort` + structured outputs instead — not `temperature`.
