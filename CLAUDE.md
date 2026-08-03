# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Step-by-step usage guide: `GUIDE.md` (EN) / `GUIDE.ru.md` (RU).

DO NOT ADD in code or commits Co-Authored-By: Claude noreply@anthropic.com 

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

# Fast scheme preflight (10 dialogues, exit 0/1) and full eval (preflight → all rows).
# Both accept the .xlsx directly (auto-converted to a temp jsonl OUTSIDE the repo).
python3 eval_fast.py --dataset data/labeled.jsonl
python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"
```

## Configuration — per-agent (agents.toml + config.py)

Each LLM role in the pipeline is configured **independently** in `agents.toml`: its own
`base_url` (endpoint), `model`, `prompt`, `effort`, and `mode` — plus, for the Alfa
Sandbox/AlfaGen API (`4. Sandbox API.pdf`): `system_id` (sent as the `systemid` header, with
a fresh unique `messageid` per request) and `rps` (endpoint rate limit; Sandbox = 0.2 RPS,
enforced by a shared per-endpoint throttle in `oai.py`). TLS: the Sandbox cert is self-signed —
per-role `insecure = true` (curl `-k`, shipped on for the Sandbox sections; provider="openai"
roles only); the proper fix needs no code: `SSL_CERT_FILE=<bank CA PEM>` in `.env`.
DLP: the Sandbox rejects requests with personal data (400 HAS_PERSONAL_DATA) — per-role
`sanitize = true` (shipped on) masks names/cards/emails via `privacy.py` before send, with one
automatic strict retry; `system` messages are never masked (verbatim agent prompt).
Sandbox is VPN/VDI-only; tools
(function calling) are documented only for QwQ-32B / llama-3.3 / gpt-oss-120b, so `[agent]`
must use one of those. Three roles:
`[agent]` (system under test), `[groundedness]`, `[resolution]`. Plus `[mcp]` (tool backend)
and `[pipeline]`. Secrets stay in env — each role's `api_key_env` names its key var (falls back
to `ANTHROPIC_API_KEY`); a local `.env` (from `.env.example`, gitignored) is loaded for convenience.

```python
import config
spec = config.get("resolution")   # AgentSpec(base_url, api_key, model, effort, prompt, mode)
spec.available()                  # LLM usable for this role?
spec.prompt_text()                # this role's system prompt
anthropic.Anthropic(**config.client_kwargs(spec))   # client at this role's endpoint/key
```

Point a role at a different endpoint/model/prompt by editing its section — e.g. run the agent
under test on one deployment and the judges on another. `base_url` must be Anthropic-compatible.
Env overrides: `EVAL_CONFIG` (config path), `{ROLE}_MODE` (`AGENT_MODE`/`GROUNDEDNESS_MODE`/
`RESOLUTION_MODE`), `MCP_*`, `JUDGE_CONCURRENCY`. `mode=auto` → LLM iff the role is usable: a key
is present, or (provider=openai) any `base_url` is set — so the shipped Sandbox config counts as
usable even keyless, and off the bank network you must force `*_MODE=offline` for all three roles
to run fully offline. `MCP_MODE=live` routes `MCPClear` through
`mcp_client.py` (needs `pip install mcp`); adapt `_tool_args` to your server's tool schema.

## Architecture

```
runner → trace per case → [groundedness + resolution + checks] (parallel) → aggregate → report + run diff
```

Domain: Alfa-Bank transaction-history support agent (Russian; replies prefixed
`final_answer:` / `no_comments:`). Real agent prompt: `agent_prompt_v2.md` (used verbatim by `[agent]`; `prompts/agent.md` is a derived alt).

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
`agent_prompt_v2.md` verbatim via `build_system`; `prompts/agent.md` is a derived alt) or
`run_offline_agent` (deterministic baseline). Both drive the same tools:
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

OO structure: `judges/base.py` `LlmJudge` — template method (spec → availability → LLM call →
`shape`/`stub`/`on_error`) shared by `GroundednessJudge`, `ResolutionJudge` (overrides `judge()`
for panel voting), `ScopeClassifier`. Failure results carry `error = {where, what_to_do, detail}`
(typed hierarchy in `errors.py`: Dataset/Config/Api/Network/LlmOutput; `ApiError.from_http`
encodes the Sandbox error table). Module-level `judge_groundedness`/`judge_resolution`/
`classify_scope` are the stable seam — thin wrappers over singleton instances; tests patch
the shared `config`/`_llm` module attributes, which the base class sees. `eval_fast.py`'s
`SchemeDiagnostician` turns these structured errors into a where/what-to-do diagnosis.

`judge_groundedness` and `judge_resolution` call the LLM via `judges/_llm.py` (AsyncAnthropic,
structured outputs `output_config.format` json_schema, determinism via `output_config.effort`;
or the OpenAI-compatible path through `oai.py` — `OaiClient` + `RateLimiter` classes).
Prompts: `prompts/groundedness.md`, `prompts/resolution.md`.
- Groundedness reads trace only (`query`, `answer`, `tool_calls`, `chunks`) — never `operator_answer`.
- Resolution reads `query`, `answer`, `operator_answer`; **3-way** `verdict` ∈ yes/partial/no
  (matches the human labels); `resolution_yes` (= verdict=="yes") is derived in code and feeds the
  policy. It also emits `failure_reason` (none | wrong_operation | hallucination | incomplete |
  no_answer | missed_data | other) — normalized in code (`yes` → none, not-yes without a
  recognized reason → other); shown per case in the report as e.g. `no·incomplete`.
- Resolution supports **panel voting** ("trial"): `[[resolution.panel]]` entries in `agents.toml`
  (default bench: neutral judge / prosecutor / defender — `prompts/resolution*.md`) vote
  concurrently; majority wins, a full 3-way split → `partial` (lower median on no<partial<yes,
  `majority_verdict`). Per-vote details ({judge, model, verdict, reasoning}) land in the
  result's `votes[]`; the report's `resolution_votes` carries the verdicts. `RESOLUTION_PANEL=off`
  → single judge (A/B in calibrate.py). A panelist error counts as a `no` vote (transient
  failures no longer decide a case alone); a panelist whose spec is unusable is skipped.

Env: `JUDGE_MODE` (auto|llm|offline), `JUDGE_MODEL` (default `claude-opus-4-8`), `JUDGE_EFFORT`
(low|medium|high, default medium), `JUDGE_CONCURRENCY` (calibrate fan-out, default 6). With no key
(or `JUDGE_MODE=offline`) the judges fall back to stubs so the pipeline runs keyless.

Note: the spec calls for temperature=0, but `claude-opus-4-8` rejects `temperature` (400).
Determinism comes from `effort` + structured outputs — not `temperature`.

## Calibration (calibrate.py)

The resolution judge's target is the human `Is agents answer correct?` label. `calibrate.py`
scores each labeled row's existing agent answer and reports exact-match agreement + a 3×3
confusion matrix (human × judge), per-class precision/recall/F1 + macro, a binary collapse
(**correct = Да only; Частично counts as incorrect** — matches the solved policy; derived from
the same records, no extra judge calls — `binary_collapse` in calibration.json), and a
failure-reason breakdown (`failure_reasons`: judge-flagged incorrect rows by cause —
wrong_operation / hallucination / incomplete / no_answer / missed_data / other — with how many
the human also graded incorrect). This validates the judge — the "Ground Truth" arm of the
architecture. Baseline with the always-`yes` stub is ~8% (the `Да` share); the real judge
must beat it.

Calibration is **scope-segmented**: each row gets `scope` ∈ in_scope/out_of_scope/unknown —
deterministic rule on the backend `Intent` label (`judges/scope.py` markers) when exported,
`--classify-scope` → LLM classifier (`[scope]` role, `prompts/scope.md`, cached in
`data/scope_cache.json`, gitignored) for rows without one. The report/console carry
`by_scope` (n, human-correct %, judge agreement/kappa per segment) and `by_intent`
(top intents, n≥10); scope+intent land in disagreements.csv. Don't drop out-of-scope rows —
they measure correct-refusal (`no_comments`) behavior.

The resolution prompts include few-shot examples distilled — anonymized and paraphrased, real
data never verbatim — from labeled rows (clarifier-as-right-move vs cop-out, hedged reading vs
confident misidentification, mechanism-without-cause, wrong-direction search); agreement on the
source rows is slightly optimistic, so judge calibration by overall kappa.

It also writes `runs/<ts>/disagreements.csv` (id, human_label, verdict, agree, judge reasoning,
agent/operator answers, dialogue, assessor_comment) — judge reasoning next to the human comment,
for prompt-tuning. `--all-rows` writes every row; `--csv PATH` overrides the location.
UTF-8-BOM so Excel renders the Cyrillic.
