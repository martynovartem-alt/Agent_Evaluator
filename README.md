# Agent_Evaluator

Evaluation pipeline for **Alfa-Bank's transaction-history support agent** (Russian-language;
the agent proposes reply hints for human operators). It answers two questions:

1. **How good is the agent?** Run golden cases through the agent, judge every answer, and
   report a `% solved` with per-case diagnostics (`runner.py`).
2. **Can we trust the judge?** Calibrate the LLM judge against human assessor labels from
   real graded dialogues, with precision/recall and a failure-reason analysis
   (`calibrate.py`, `eval_fast.py`, `eval_full.py`).

## How it works

```
golden case ──▶ Agent (LLM + tools) ──▶ trace {answer, tool_calls, chunks}
                                             │
              ┌──────────────────────────────┼─────────────────────────────┐
              │ checks.py (deterministic)    │ groundedness judge   resolution judge
              │ tools_ok · must_facts · ...  │ (answer vs tool data)  (answer vs operator answer)
              └──────────────────────────────┴─────────────────────────────┘
                                             │
                     solved = resolution "yes" ∧ grounded ∧ tools_ok
                                             ▼
                        runs/<timestamp>/report.json  (+ diff vs previous run)
```

- The **agent under test** answers a client question using two tools: `MCPClear`
  (transaction history — local fixtures, or a live MCP server) and `getInstruction`
  (verified topic explanations). Everything it does is captured as a **trace**.
- Three evaluations run per case, concurrently: deterministic **checks** (no LLM), a
  **groundedness judge** (does the answer claim anything the tool data doesn't support?),
  and a **resolution judge** (does the answer resolve the question? verdict
  `yes/partial/no` + a **failure reason**: `wrong_operation / hallucination / incomplete /
  no_answer / missed_data / other`).
- Judges read **only the captured trace** — never live tools. Every run writes a fresh
  `runs/<timestamp>/`; nothing is overwritten.
- The headline metric is **binary**: an answer is *correct* only when the verdict is a
  strict `yes` — «Частично» counts as *incorrect*, matching the solved policy.

All LLM roles (`[agent]`, `[groundedness]`, `[resolution]`) are configured independently in
`agents.toml` and run on the bank's **Alfa Sandbox API** (AlfaGen) by default — the client
(`oai.py`) implements the Sandbox contract: `systemid` header, a unique `messageid` per
request, and a shared **0.2 RPS** throttle. Any role can be pointed at another
OpenAI-compatible or Anthropic endpoint by editing its section.

## Quickstart

```bash
pip install -r requirements.txt
cp .env.example .env         # put your SANDBOX_API_KEY (UUID from email) here

# no key / off-VPN? — the deterministic offline path runs end-to-end:
AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline \
  python3 runner.py --dataset data/golden_mini.jsonl

# agent eval (live, on the bank VPN/VDI):
python3 runner.py --dataset data/golden_mini.jsonl
```

## Judge calibration against human labels

The ground truth is an xlsx of real dialogues where assessors graded each agent answer
`Да / Частично / Нет`. Because the Sandbox allows one request per 5 seconds, a full run
takes hours — so the flow is **preflight first, then full**:

```bash
# ~1 min: judge 10 dialogues, validate the scheme (verdicts, failure reasons,
# no judge errors, live LLM — not the stub), print the full-run ETA; exit 0/1
python3 eval_fast.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"

# preflight + every labeled row: agreement, kappa, per-class precision/recall,
# binary correct/incorrect, failure-reason breakdown, scope/intent segmentation,
# disagreements.csv. --classify-scope adds an LLM scope label (cached) for rows
# without a backend Intent — out-of-scope rows are reported, not dropped.
python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx" --classify-scope
```

Both accept the `.xlsx` directly (converted to a temp jsonl **outside** the repo — the raw
rows are real customer data and stay out of git, see `.gitignore`).

Reference numbers (measured with a `deepseek-chat` judge on 325 + 1145 labeled rows; the
judge is stable across both sets): binary agreement ≈ 87%, κ ≈ 0.29; catching *incorrect*
answers P/R ≈ 93%/93%; the judge's weak spot is certifying *correct* answers (P ≈ 30–35%).
The agent's failure profile: ≈ 65% `no_answer` (template clarifier instead of looking up
the operation), ≈ 28% `wrong_operation`.

## Repository map

| Path | What it is |
|---|---|
| `runner.py` | agent eval: cases → traces → 3 evaluations → `runs/<ts>/report.json` |
| `agent.py` / `tools.py` | the agent under test (LLM tool loop or offline baseline) + its tools |
| `checks.py` | deterministic checks, no LLM (`docs/checks_spec.md`) |
| `judges/` | groundedness + resolution judges (`_llm.py` — provider seam) |
| `aggregate.py` | policy, report, run-to-run diff |
| `calibrate.py` | judge vs human labels: kappa, P/R, binary, failure reasons |
| `eval_fast.py` / `eval_full.py` | scheme preflight (10 dialogues, exit 0/1) / preflight + full eval |
| `dataset.py` / `gen_mcp.py` | xlsx → jsonl ingestion / synthetic MCP data generator |
| `agents.toml` / `config.py` / `oai.py` | per-role LLM config / spec resolution / Sandbox-aware HTTP client |
| `prompts/` | judge prompts incl. the panel personas (`resolution*.md`, `groundedness.md`) |
| `agent_prompt_v2.md` | production agent prompt, used verbatim by `[agent]` (repo root) |
| `data/` · `fixtures/` | golden sets, generated MCP data · hand-authored operations/instructions |
| `tests/` | 114 unit tests; run keyless: `python3 -m unittest discover -s tests -t .` |

Docs: step-by-step usage — `GUIDE.md` (EN) / `GUIDE.ru.md` (RU) · deep structure —
`ARCHITECTURE.md` · agent-facing dev notes — `CLAUDE.md` · Sandbox API contract —
`"4. Sandbox API.pdf"` + `Alfa_LLM_endpoints.png` (internal, kept untracked).
