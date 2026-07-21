# Architecture (implemented)

Markdown sketch of `Architecture.png`, mapped to the real code. `runner.py` orchestrates a run;
per-role endpoints/models/prompts live in `agents.toml` (`config.py`).

```
                         ┌──────────────────────────┐
                         │  Client question (query) │   data/golden_mini.jsonl
                         │  «Запрос пользователя»   │   or data/eval_prompt.jsonl
                         └────────────┬─────────────┘
                                      ▼
   tune prompt/config ▶  ┌──────────────────────────┐   getInstruction    ╭───────────────────╮
   agent_prompt_v2.md    │  Agent X                 │──────────────▶──────│ instructions.json │ FAQ
   prompts/*.md          │  agent.py · run_llm_agent│                     ╰───────────────────╯
   agents.toml           │  prompt agent_prompt_v2  │   MCPClear          ╭───────────────────╮
   «Правим промпт»       │  endpoint per agents.toml│──────────────▶──────│ mcp_fake.json  /  │ MCP
                         └───────┬──────────┬───────┘                     │ live MCP server   │
                                 │          │                             ╰───────────────────╯
                        Chunks ◀─┘          └─▶ RAW MCP data
                     (trace.chunks)          (trace.tool_calls)
                                 │          │
     ┌── Eval ───────────────────┼──────────┼───────────────────────────────┐
     │  TRACE {answer, tool_calls, chunks}  │                               │
     │                           ▼          ▼                               │   ╭──────────────╮
     │  checks.py (deterministic): tools_ok · must_facts ·                  │   │ Ground Truth │
     │                             instruction_ok · planted_operation_ok    │   │  operator_   │
     │                                                                      │◀──│  answer /    │
     │  Knowledge Assessor «Оценщик знаний» → judges/groundedness.py        │   │  human       │
     │      claims grounded in tool data?  (no hallucinated amounts/dates)  │   │  labels      │
     │                                                                      │◀──│              │
     │  Examiner «Экзаменатор» → judges/resolution.py                       │   ╰──────────────╯
     │      solved? verdict yes / partial / no  +  reasoning («да\нет. почему»)  │
     └───────────────────────────────────┬──────────────────────────────────┘
                                          ▼
                    ┌─────────────────────────────────────────────┐
                    │  Report  «Отчёт»   aggregate.py → report.json│
                    │  policy: solved = resolution yes ∧ grounded  │
                    │          ∧ tools_ok                          │
                    │  % solved · % facts · % tools_ok ·           │
                    │  grounded · resolution yes/partial/no        │
                    └─────────────────────────────────────────────┘

  Calibration (separate): current_agent_answers.xlsx → dataset.py → labeled.jsonl
                          → calibrate.py → resolution judge vs human labels
                          → agreement % + confusion + disagreements.csv
```

## PNG label → implementation

| `Architecture.png` | Implemented as |
|---|---|
| Запрос пользователя | `query` in `data/golden_mini.jsonl` / `data/eval_prompt.jsonl` |
| Агент X | `agent.py` `run_llm_agent` running `agent_prompt_v2.md` on the `[agent]` endpoint |
| Chunks ← База знаний FAQ | `getInstruction` results → `trace.chunks`; source `fixtures/instructions.json` |
| RAW MCP data ← MCP | `MCPClear` results → `trace.tool_calls`; source `data/mcp_fake.json` (200×200) or a live MCP server |
| Агент Оценщик Знаний | `judges/groundedness.py` (grounded vs tool data) |
| Агент экзаменатор | `judges/resolution.py` (3-way yes/partial/no + reasoning) |
| Ground Truth | `operator_answer` (golden) / human `Да·Частично·Нет` labels (calibration) |
| Eval (dashed box) | `runner.evaluate_case` → `checks` + 2 judges in parallel |
| Отчёт | `aggregate.py` → `runs/<ts>/report.json` + `format_report` summary |
| Правим промпт / интеграции | edit `agent_prompt_v2.md` / `prompts/*.md` / `agents.toml` |

Not in the PNG but in the implementation: `checks.py` (deterministic checks), the offline
baseline agent + stub judges (keyless runs), per-run `runs/<ts>/` output, and the per-agent
provider seam (Anthropic or OpenAI-compatible endpoints — Claude, DeepSeek, etc.).
