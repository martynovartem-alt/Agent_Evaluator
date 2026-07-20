# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -r requirements.txt

# Full eval pipeline
python3 runner.py --dataset data/golden_mini.jsonl

# Single case
python3 runner.py --dataset data/golden_mini.jsonl --case-id txn_001
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

**Fixed constraints — do not redesign:**
- Judges read ONLY the captured trace, never live MCP/FAQ
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
`{id, query, intent, operator_answer, must_facts[], needs_transactions, fixture_user, planted_txn_id, expected_faq_doc}`

**trace** (written per case to `runs/<ts>/traces/`):
`{case_id, answer, tool_calls[{name, args, result}], chunks[{doc_id, text}]}`

## Agent (agent.py + tools.py)

`run_agent(case)` dispatches to `run_llm_agent` (Claude, manual tool-use loop) or
`run_offline_agent` (deterministic baseline). Both drive the same tools in `tools.py`:
- `get_transactions` — the MCP-served transactions tool. `tools.py` holds the offline
  fixture adapter; in production it is served over MCP. Swap behind that seam without
  touching the runner/judges. Calls land in `trace.tool_calls[]`.
- `retrieve_faq` (agent tool `search_faq`) — FAQ retrieval over `fixtures/faq.json`,
  deterministic token-overlap scorer. Results land in `trace.chunks[]`.

Tools read only from `fixtures/` (`transactions.json`, `faq.json`) — no network, so runs
are reproducible. Fixture data must back the golden cases: `fixture_user`/`planted_txn_id`
in `transactions.json`, `expected_faq_doc` in `faq.json`.

## Judges (implemented in Phase 4)

Prompts live in `prompts/groundedness.md` and `prompts/resolution.md`.
Both judges call Claude API and return structured JSON.
Judge stubs return fixed passing values so Phase 1–3 pipeline runs end-to-end.

Note: the spec calls for temperature=0, but `claude-opus-4-8` rejects `temperature` (400).
Get judge determinism via `output_config.effort` + structured outputs instead — not `temperature`.
