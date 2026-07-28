# Architecture (implemented)

How the system actually works today, mapped to the real code. `runner.py` orchestrates an
agent eval; `eval_fast.py`/`eval_full.py`/`calibrate.py` run the judge-calibration arm;
per-role endpoints/models/prompts live in `agents.toml` (resolved by `config.py`).
`Architecture.png` is the original design sketch — the mapping table below ties its labels
to the implementation.

## 1. Agent eval (runner.py)

```
                         ┌──────────────────────────┐
                         │  Client question (query) │   data/golden_mini.jsonl
                         │  «Запрос пользователя»   │   or data/eval_prompt.jsonl
                         └────────────┬─────────────┘
                                      ▼
   tune prompt/config ▶  ┌──────────────────────────┐   getInstruction    ╭───────────────────╮
   agent_prompt_v2.md    │  Agent X  (gpt-oss-120b) │──────────────▶──────│ instructions.json │ FAQ
   prompts/*.md          │  agent.py · run_llm_agent│                     ╰───────────────────╯
   agents.toml           │  prompt agent_prompt_v2  │   MCPClear          ╭───────────────────╮
   «Правим промпт»       │  manual tool-use loop    │──────────────▶──────│ operations.json + │ MCP
                         └───────┬──────────┬───────┘                     │ mcp_fake.json  /  │
                                 │          │                             │ live MCP server   │
                        Chunks ◀─┘          └─▶ RAW MCP data              ╰───────────────────╯
                     (trace.chunks)          (trace.tool_calls)
                                 │          │
     ┌── Eval (asyncio.gather) ──┼──────────┼───────────────────────────────┐
     │  TRACE {answer, tool_calls, chunks}  │                               │
     │                           ▼          ▼                               │   ╭──────────────╮
     │  checks.py (deterministic, no LLM): tools_ok · must_facts ·          │   │ Ground Truth │
     │                             instruction_ok · planted_operation_ok    │   │  operator_   │
     │                                                                      │◀──│  answer /    │
     │  Knowledge Assessor «Оценщик знаний» → judges/groundedness.py        │   │  human       │
     │      claims grounded in tool data?  (no hallucinated amounts/dates)  │   │  labels      │
     │                                                                      │◀──│              │
     │  Examiner «Экзаменатор» → judges/resolution.py                       │   ╰──────────────╯
     │      verdict yes/partial/no + failure_reason + reasoning             │
     │      optional panel: judge + prosecutor + defender → majority        │
     └───────────────────────────────────┬──────────────────────────────────┘
                                          ▼
                    ┌─────────────────────────────────────────────────────┐
                    │  Report «Отчёт»  aggregate.py → runs/<ts>/report.json│
                    │  policy: solved = resolution "yes" ∧ grounded        │
                    │          ∧ tools_ok   («Частично» is NOT solved)     │
                    │  % solved · % facts · % tools_ok · % grounded ·      │
                    │  verdicts · per-case failure reasons · diff vs prev  │
                    └─────────────────────────────────────────────────────┘
```

Per case: the (blocking) agent runs in a worker thread, then its three evaluations run
concurrently; cases themselves run concurrently bounded by `[pipeline].concurrency`.
Traces are persisted to `runs/<ts>/traces/<case_id>.json` before judging — judges read
**only the trace**, never live tools.

## 2. Judge calibration (the "Ground Truth" arm)

Validates the resolution judge against human assessor labels — real dialogues where each
agent answer was graded `Да / Частично / Нет`.

```
  Agents-new-answers(...).xlsx ──▶ dataset.py ──▶ labeled .jsonl   (gitignored — real data)
        (multi-sheet: the labeled table is auto-detected by header match)
                                        │
              ┌─────────────────────────┴──────────────────────────┐
              │ eval_fast.py — preflight (~1 min, exit 0/1)        │
              │   10 dialogues → scheme validation: verdicts and   │
              │   failure reasons in-enum + consistent, no judge   │
              │   errors, live LLM (stub = fail), full-run ETA     │
              └─────────────────────────┬──────────────────────────┘
                                        ▼ (only if scheme OK)
              ┌────────────────────────────────────────────────────┐
              │ eval_full.py / calibrate.py — every labeled row    │
              │   3-way: agreement · kappa · within-1 · confusion  │
              │   per-class precision/recall/F1 + macro            │
              │   BINARY (headline): correct = «Да» only;          │
              │     «Частично» → incorrect (matches solved policy) │
              │   failure reasons w/ human-confirmation counts     │
              │   scope segments (in/out/unknown) + top intents:   │
              │     backend Intent → rule (judges/scope.py);       │
              │     --classify-scope → LLM [scope] role, cached    │
              │     (out-of-scope rows reported, never dropped —   │
              │      they measure correct-refusal behavior)        │
              │   runs/<ts>/disagreements.csv (UTF-8-BOM, Excel)   │
              └────────────────────────────────────────────────────┘
```

The agent answers already exist in the xlsx — calibration scores them with the judge and
never runs the agent. Measured (judge = `deepseek-chat`, two independent sets, n=325 and
n=1145; stable across both): binary agreement ≈ 87%, κ ≈ 0.29; flagging *incorrect*
answers P≈93%/R≈93%; certifying *correct* answers is the weak spot (P ≈ 30–35%). Agent
failure profile: ≈65% `no_answer`, ≈28% `wrong_operation`.

## 3. The resolution judge in detail (judges/resolution.py)

- Reads `query`, `answer`, `operator_answer`. Structured output: `verdict` ∈
  `yes/partial/no`, `failure_reason`, `reasoning`.
- `resolution_yes` (= verdict == "yes") is derived **in code**, not trusted from the model,
  and feeds the solved policy.
- `failure_reason` taxonomy — why an answer is not correct: `wrong_operation ·
  hallucination · incomplete · no_answer · missed_data · other` (`none` when correct;
  normalized in code: `yes` → `none`, not-yes without a recognized reason → `other`).
  Tool misuse is not text-observable here — in the pipeline it is caught by `checks.py`
  (`tools_ok`) and hallucination-vs-trace by the groundedness judge.
- **Panel voting** («суд»): `[[resolution.panel]]` entries (neutral judge / prosecutor /
  defender personas — same verdict definitions, different scrutiny) vote concurrently;
  majority wins, a full 3-way split resolves to `partial` (lower median on
  no < partial < yes). A panelist error counts as a `no` vote; an unusable panelist is
  skipped. `RESOLUTION_PANEL=off` → single judge. Measured on two datasets: the single
  judge ≥ panel on kappa at 1/3 the cost — prefer the single judge; the panel's one edge
  is wrong-answer recall (95% vs 93%).
- Prompts (`prompts/resolution*.md`) carry few-shot examples **distilled (anonymized,
  paraphrased) from real graded dialogues**: clarifier-as-right-move vs template cop-out,
  hedged reading of tool data vs confident misidentification, mechanism-without-cause,
  wrong-direction search.

## 4. LLM transport (agents.toml → config.py → oai.py / judges/_llm.py)

Every role is an independent `AgentSpec`: `provider` (`openai` | `anthropic`), `base_url`,
`api_key_env`, `model`, `prompt`, `effort`, `mode` (`auto|llm|offline`), plus the Sandbox
contract fields `system_id` and `rps`. Default deployment — the bank's **Alfa Sandbox API**
(AlfaGen, `.../internal/llm/v1`, see `"4. Sandbox API.pdf"`):

- Bearer **UUID key** (`SANDBOX_API_KEY`, issued by email, 3-month lifetime); VPN/VDI only.
- `systemid: sanduser` header on every request (406 without it); a **fresh unique
  `messageid`** per request (a repeat is a documented 400).
- **0.2 RPS hard limit** → `oai.py` reserves send slots 5 s apart per normalized endpoint,
  shared across all roles, panel votes and threads (`rps = 0.2` in `agents.toml`).
- Tools (function calling) are documented only for `Qwen/QwQ-32B`, `/models/llama-3.3`,
  `gpt-oss-120b` — hence `[agent]` (which must call MCPClear/getInstruction) runs
  `gpt-oss-120b`, while the judges (no tools) run `Qwen/Qwen3.6-35B-A3B-FP8`.

`mode=auto` uses the LLM when the role is *usable* (a key, or for `provider=openai` any
`base_url`) — so off-network force `*_MODE=offline` for the deterministic keyless path
(offline agent + stub judges), which keeps the whole pipeline runnable with no key.

## PNG label → implementation

| `Architecture.png` | Implemented as |
|---|---|
| Запрос пользователя | `query` in `data/golden_mini.jsonl` / `data/eval_prompt.jsonl` |
| Агент X | `agent.py` `run_llm_agent` running `agent_prompt_v2.md` on the `[agent]` endpoint |
| Chunks ← База знаний FAQ | `getInstruction` results → `trace.chunks`; source `fixtures/instructions.json` |
| RAW MCP data ← MCP | `MCPClear` results → `trace.tool_calls`; source `fixtures/operations.json` + `data/mcp_fake.json` (200×200), or a live MCP server (`mcp_client.py`) |
| Агент Оценщик Знаний | `judges/groundedness.py` (grounded vs tool data) |
| Агент экзаменатор | `judges/resolution.py` (yes/partial/no + failure_reason; optional 3-judge panel — `[[resolution.panel]]`, `RESOLUTION_PANEL=off` → single judge) |
| Ground Truth | `operator_answer` (golden) / human `Да·Частично·Нет` labels (calibration) |
| Eval (dashed box) | `runner.evaluate_case` → `checks` + 2 judges concurrently |
| Отчёт | `aggregate.py` → `runs/<ts>/report.json` + `format_report` summary + diff vs previous run |
| Правим промпт / интеграции | edit `agent_prompt_v2.md` / `prompts/*.md` / `agents.toml` |

Beyond the PNG: deterministic `checks.py`; the offline/keyless path; per-run `runs/<ts>/`
output; the per-role provider seam (Sandbox, DeepSeek, Anthropic, any OpenAI-compatible
endpoint — swap by editing one section); the Sandbox rate limiter + header contract in
`oai.py`; panel voting; the binary correct/incorrect metric with failure-reason analysis;
and the `eval_fast` → `eval_full` preflight flow that protects hours-long 0.2 RPS runs.
