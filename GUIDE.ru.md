# Agent Evaluator — как это работает (пошагово)

Офлайн-система оценки для агента поддержки Альфа-Банка. Она прогоняет агента по набору кейсов,
фиксирует, что агент сделал (его ответ и вызовы инструментов), оценивает каждый кейс
детерминированными проверками и двумя LLM-судьями и формирует отчёт. Также она умеет
**калибровать** судей по набору ответов с человеческой разметкой.

Поставляемый `agents.toml` направляет все три роли во **внутренний Sandbox Альфы**
(OpenAI-совместимый эндпоинт с моделью Qwen), поэтому «из коробки» конвейер работает на реальных
моделях. При этом встроен и полностью **офлайн-путь без ключа** (детерминированный офлайн-агент +
judge-заглушки) — включите его через `*_MODE=offline`, чтобы увидеть работу всего конвейера без
эндпоинта (шаг 2).

---

## Архитектура вкратце

```
        agents.toml + .env ──► config   (по роли: провайдер · эндпоинт · модель · промпт · режим)
                                  │
  data/golden_mini.jsonl         ▼
     (кейсы) ────────► agent.py ────► TRACE ────► оценки (параллельно)
                        │  инструм.: {answer,       ├─ checks.py           (детерминированно)
                        │  MCPClear   tool_calls,   ├─ groundedness judge  (LLM)
                        │  getInstr.  chunks}       └─ resolution judge    (LLM · yes/partial/no)
                        ▼                                   │
             фикстуры / live MCP                           ▼
                                         aggregate.py ──► runs/<ts>/report.json
                                         политика: solved = resolution yes ∧ grounded ∧ tools_ok
                                         + печать сводки + сравнение с прошлым прогоном

  Калибровка (отдельная точка входа):
    current_agent_answers.xlsx ─► dataset.py ─► data/labeled.jsonl ─► calibrate.py ─► resolution judge
                                                       └─► процент согласия + матрица 3×3 + disagreements.csv
```

---

## 1. Установка

```bash
pip install -r requirements.txt          # anthropic (mcp — опционально, для live-MCP)
```

Python 3.11+ (используется стандартный `tomllib`). Роли с `provider = "openai"` (по умолчанию)
обращаются к OpenAI-совместимому эндпоинту по **HTTP из стандартной библиотеки** (`oai.py`) — SDK
не нужен; `anthropic` требуется, только если роль использует `provider = "anthropic"`. Для
офлайн-запуска не нужно ничего, кроме стандартной библиотеки.

## 2. Запуск оценки

```bash
python3 runner.py --dataset data/golden_mini.jsonl
```

С поставляемой конфигурацией это обращается к **Sandbox Альфы** (нужны `SANDBOX_API_KEY` и доступ
к эндпоинту по сети — шаги 5–6). Чтобы запустить **полностью офлайн / без ключа**
(детерминированный агент + judge-заглушки), принудительно включите офлайн-режим:

```bash
AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline \
  python3 runner.py --dataset data/golden_mini.jsonl
```

В любом случае вы увидите сводку, а результаты запуска попадут в папку `runs/<timestamp>/`:

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

> В офлайн-режиме `grounded` и `resolution` берутся из **judge-заглушек** (всегда «проходит»),
> поэтому `Solved` завышен. Детерминированные части (`tools_ok`, `must_facts`, `instruction`,
> `planted_op`) — настоящие. Чтобы получить реальные вердикты судей, запустите на живом эндпоинте
> (шаги 5–6).

Запуск одного кейса: `--case-id subscription_299`.

## 3. Что происходит внутри конвейера

Для каждого кейса из датасета:

1. **Запускается агент** (`agent.py`). Он читает вопрос клиента и вызывает два инструмента:
   - `MCPClear` — история операций (→ пишется в трейс как `tool_calls`)
   - `getInstruction` — проверенные объяснения из базы знаний (→ пишется как `chunks`)
   Затем он пишет ответ (на русском, с префиксом `final_answer:` или `no_comments:`).
2. **Фиксируется трейс** и сохраняется в `runs/<ts>/traces/<case_id>.json`:
   `{case_id, answer, tool_calls[], chunks[]}`.
3. **Три оценки выполняются параллельно** по этому трейсу:
   - `checks.py` (детерминированно, без LLM): `tools_ok`, `must_facts`, `instruction_ok`,
     `planted_operation_ok`.
   - **Судья groundedness**: есть ли в ответе утверждения, не подтверждённые данными инструментов?
   - **Судья resolution**: решает ли ответ вопрос? Трёхуровневый вердикт `yes/partial/no`.
4. **Политика + отчёт** (`aggregate.py`):
   `solved = resolution == "yes" И нет неподтверждённого критичного утверждения И tools_ok`.
   Пишет `runs/<ts>/report.json`, печатает сводку и сравнивает с предыдущим запуском.

**Ключевое правило:** судьи читают **только зафиксированный трейс** (плюс эталонный ответ
оператора для resolution) — никогда не обращаются к живым инструментам. Каждый запуск пишет
новую папку `runs/<ts>/`; ничего не перезаписывается.

## 4. Как читать отчёт

| Метрика | Смысл | Источник |
|---|---|---|
| `tools_ok` | `MCPClear` вызван тогда и только тогда, когда кейсу нужна история | детерминированно |
| `must_facts` | каждый обязательный факт присутствует в ответе | детерминированно |
| `instruction` | нужная тема из базы знаний получена (среди применимых кейсов) | детерминированно |
| `planted_op` | заложенная операция была найдена инструментом (среди применимых кейсов) | детерминированно |
| `grounded` | в ответе нет неподтверждённых критичных утверждений | LLM-судья |
| `resolution` | распределение yes / partial / no | LLM-судья |
| `Solved` | политика выше | комбинированно |

Флаги по каждому кейсу показывают, что не прошло (например, `facts✗`, `instr✗`, `ungrounded`).
`n/a` означает, что проверка не применялась ни к одному кейсу.

## 5. Настройка каждого агента (agents.toml)

В конвейере три LLM-роли, каждая настраивается **независимо** в `agents.toml`. Каждая роль
выбирает `provider` — `"anthropic"` или `"openai"` (любой OpenAI-совместимый эндпоинт), — так что
провайдеров можно смешивать по ролям. Поставляемая конфигурация направляет все роли в Sandbox
Альфы (Qwen):

```toml
[agent]                                    # проверяемый агент поддержки
provider = "openai"                        # "anthropic" | "openai" (OpenAI-совместимый)
base_url = "https://agenapisandbox.moscow.alfaintra.net/internal/llm/v1"
system_id = "sanduser"                     # заголовок `systemid` Sandbox (без него — 406)
rps      = 0.2                             # жёсткий лимит Sandbox — один запрос в 5 с, общий
insecure = true                            # сертификат Sandbox самоподписанный — отключить
                                           # проверку TLS (аналог curl -k); либо ca_bundle = "путь к CA"
model    = "gpt-oss-120b"                  # агент вызывает инструменты; tools в Sandbox
                                           # документированы только для QwQ-32B / llama-3.3 / gpt-oss-120b
prompt   = "agent_prompt_v2.md"            # его системный промпт (замените файл, чтобы изменить)
effort   = "medium"
mode     = "auto"                          # auto | llm | offline
api_key_env = "SANDBOX_API_KEY"            # переменная окружения с его ключом (UUID из письма)

[groundedness]   # ... свой provider / эндпоинт / модель / промпт ...
[resolution]     # ... свой provider / эндпоинт / модель / промпт ...
[mcp]            # бэкенд инструментов (см. шаг 7)
[pipeline]       # конкурентность
```

Чтобы запускать проверяемого агента на одном развёртывании, а судей — на другом, задайте каждой
секции свои `provider` / `base_url` / `api_key_env`. Чтобы изменить поведение агента,
отредактируйте его файл `prompt` или укажите другой файл. Для `provider = "anthropic"` оставьте
`base_url = ""` (эндпоинт по умолчанию, должен быть Anthropic-совместимым); для
`provider = "openai"` укажите в `base_url` любой OpenAI-совместимый эндпоинт `/v1`.

**Панельное голосование («суд»).** Вердикт resolution выносит панель из трёх судей, объявленных
записями `[[resolution.panel]]` (поставляемый состав: нейтральный **judge**, **prosecutor**
(обвинитель), **defender** (защитник) — одни и те же определения вердиктов, разный фокус
проверки). Побеждает большинство (любые 2 из 3); полный разброс yes/partial/no даёт `partial`.
Каждая запись наследует `[resolution]` и может переопределять `prompt`, `model` и даже
`base_url` — например, панель из разных моделей. `RESOLUTION_PANEL=off` возвращает одиночного
судью; учтите, что панель втрое увеличивает число вызовов resolution на кейс.

## 6. Перейти в «боевой» режим (реальный агент + реальные судьи)

Скопируйте шаблон окружения и впишите ключ, который называет `api_key_env` каждой роли.
Поставляемая конфигурация использует **`SANDBOX_API_KEY`** (Sandbox Альфы) для всех трёх ролей:

```bash
cp .env.example .env
# .env:
#   SANDBOX_API_KEY=...           (Sandbox Альфы — куда указывает поставляемый agents.toml)
#   ANTHROPIC_API_KEY=sk-...      (только если роль использует provider = "anthropic")
#   AGENT_MODE=llm                (опционально; иначе mode=auto из agents.toml включит LLM, когда роль доступна)
```

Затем запустите конвейер — ответит реальный агент, а оценят реальные судьи:

```bash
python3 runner.py --dataset data/golden_mini.jsonl
```

`mode = auto` использует LLM, когда роль пригодна (есть ключ или задан OpenAI-совместимый
`base_url`); `offline` принудительно включает детерминированный базовый вариант; `llm`
принудительно включает реальную модель. Любой незаданный ключ откатывается к `ANTHROPIC_API_KEY`.

## 7. Подключить реальный MCP-сервер (опционально)

По умолчанию `MCPClear` читает локальные фикстуры. Чтобы получать живые данные об операциях,
задайте в `agents.toml` секцию `[mcp]` (или через переменные окружения):

```toml
[mcp]
mode      = "live"
transport = "stdio"                 # или "http"
command   = "python3 mcp_server.py" # для stdio; или server_url для http
tool_name = "get_transactions"
```

Для live-режима нужен `pip install mcp`. Подгоните `_tool_args` в `mcp_client.py` под имена
параметров инструмента вашего сервера. (Код транспорта следует MCP SDK, но его стоит проверить на
вашем сервере.)

## 8. Калибровка судей по человеческой разметке

Эталон — таблица ответов агента, которые человек оценил как `Да / Частично / Нет`.

```bash
# 1) конвертируем xlsx в jsonl (реальный файл в .gitignore, хранится локально)
python3 dataset.py current_agent_answers.xlsx data/labeled.jsonl

# 2) оцениваем каждый ответ судьёй resolution и сравниваем с меткой человека
python3 calibrate.py --dataset data/labeled.jsonl
```

При лимите Sandbox 0,2 RPS полный прогон занимает часы, поэтому используйте двухшаговый
поток: **быстрая проверка, потом полный прогон**. `eval_fast.py` оценивает 10 диалогов и
проверяет *схему* (валидные вердикты и причины ошибок, отсутствие judge error, живая LLM,
а не заглушка), печатает оценку времени полного прогона и завершается с кодом 0/1.
`eval_full.py` сначала выполняет ту же проверку и прерывается, если она не прошла. Оба
принимают `.xlsx` напрямую (конвертация во временный jsonl вне репозитория):

```bash
python3 eval_fast.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"   # ~1 мин
python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"   # проверка + все строки
```

Вывод: **процент согласия** + матрица ошибок 3×3 (человек × судья), **precision/recall/F1 по
классам**, главная **бинарная шкала** (correct = только «Да»; **«Частично» считается
incorrect** — как в политике solved) и **разбор причин ошибок** для строк, которые судья счёл
некорректными (wrong_operation / hallucination / incomplete / no_answer / missed_data / other,
с числом строк, где человек тоже поставил «не корректно»). Плюс `runs/<ts>/disagreements.csv`
со всеми строками, где судья и человек разошлись, — с причиной и обоснованием судьи рядом с
комментарием человека. Используйте этот CSV, чтобы улучшать
`prompts/resolution.md`, и запускайте заново. (`--all-rows` пишет все строки; `--csv PATH`
меняет путь.)

Чтобы проверить, действительно ли панель лучше одиночного судьи, прогоните A/B на одном и том же
размеченном наборе:

```bash
python3 calibrate.py --dataset data/labeled.jsonl --repeat 3                       # панель
RESOLUTION_PANEL=off python3 calibrate.py --dataset data/labeled.jsonl --repeat 3  # одиночный судья
```

Сравнивайте **каппу** (скорректирована на случайные совпадения) — оставляйте панель, только если
она выигрывает; при панельном голосовании колонка `reasoning` в `disagreements.csv` содержит
голос и аргументы каждого судьи.

Примечание: в промптах resolution есть few-shot-примеры, **выжатые (анонимизированные,
перефразированные) из размеченных строк** — согласие на нескольких строках-источниках слегка
завышено, поэтому оценивайте калибровку по общей каппе, а не по отдельной строке.

## 9. Генерация фейковых MCP-данных

`gen_mcp.py` создаёт синтетические операции в реальном формате MCP, используя названия операций,
собранные из эталонных данных (все суммы/даты/счета — вымышленные):

```bash
# зафиксированный датасет: 200 клиентов × 200 операций за последние 90 дней
python3 gen_mcp.py --users 200 --ops-per-user 200 --days 90 --seed 7 --compact --out data/mcp_fake.json
```

`MCPClear` объединяет их с фикстурами, написанными вручную, поэтому golden-кейсы могут опираться
на этих пользователей (`user_0001` …).

## 10. Добавить свои кейсы

Допишите строку в `data/golden_mini.jsonl`:

```json
{"id": "my_case", "query": "...", "current_date": "2026-07-20", "operator_answer": "final_answer: ...",
 "must_facts": ["..."], "needs_history": true, "fixture_user": "user_0001",
 "planted_operation_id": "G260602MOCOIPFBT00010", "expected_instruction": "alfaSmart"}
```

- `needs_history` → нужно ли вызывать `MCPClear`.
- `planted_operation_id` → id операции, который должен появиться в результате инструмента.
- `expected_instruction` → ключ темы `getInstruction` (см. `fixtures/instructions.json`).
- `must_facts` → строки, которые должны присутствовать в ответе.

## 11. Запуск тестов

```bash
python3 -m unittest discover -s tests -t .
```

Детерминированно; API-ключ не нужен.

---

## Устранение неполадок

| Симптом | Причина | Решение |
|---|---|---|
| Баннер `stub (no LLM)`; `resolution` везде `yes` | Роль непригодна → судьи работают как заглушки (только если нет ключа **и** нет `base_url`, либо `*_MODE=offline`) | Задайте ключ роли (например, `SANDBOX_API_KEY`) или `base_url` в `agents.toml`; проверьте `config.get("resolution").available()` |
| Ответы агента шаблонные; `mode=llm` будто игнорируется | Работает офлайн-базлайн (нет ключа) | Задайте ключ и `[agent].mode = "llm"` (или `auto`) |
| `ModuleNotFoundError: anthropic` | Принудительный `mode=llm`, но SDK не установлен | `pip install anthropic` |
| `RuntimeError: MCP_MODE=live requires the MCP SDK — pip install mcp` | Live-MCP без SDK | `pip install mcp` или `[mcp].mode = "fixture"` |
| `RuntimeError: … requires MCP_COMMAND` / `MCP_SERVER_URL` | Live-MCP без данных подключения | Задайте `command` (stdio) или `server_url` (http) в `agents.toml [mcp]` |
| Ошибка соединения / 401 / 404 от API | Неверный `base_url`, несоответствие `provider`↔эндпоинт или ключ↔эндпоинт | Сопоставьте `provider` с эндпоинтом (`openai` для OpenAI-совместимых, `anthropic` для Anthropic); для Anthropic оставьте `base_url = ""`; ключ должен подходить к эндпоинту |
| `URLError` / ошибка DNS для `…alfaintra.net`; прогон падает | Поставляемая конфигурация указывает на Sandbox Альфы, доступный только из сети Альфы | Запускайте из сети Альфы или включите офлайн-путь: `AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline` (или очистите `base_url` / задайте `mode = "offline"`) |
| `[SSL: CERTIFICATE_VERIFY_FAILED] … self-signed certificate` (при этом VPN подключён) | У Sandbox самоподписанный TLS-сертификат; Python по умолчанию проверяет сертификаты | `insecure = true` в секции роли в `agents.toml` (аналог `curl -k`; в поставке уже включено для Sandbox-ролей), либо установить сертификат банка: `ca_bundle = "путь/к/alfa_ca.pem"`; env: `{ROLE}_INSECURE=1` / `{ROLE}_CA_BUNDLE=путь` |
| `agents.toml` игнорируется (берутся значения по умолчанию) | Python < 3.11 (нет `tomllib`) | Используйте Python 3.11+ |
| Live-MCP: `KeyError: 'amount'` / неверные суммы | Схема/формат инструмента сервера отличается | Подгоните `_tool_args` в `mcp_client.py`; сервер должен возвращать `{operations:[{…,"amount":{"value","minorUnits"}}]}` (рубли = value/minorUnits) |

---

## Карта файлов

| Путь | Роль |
|---|---|
| `runner.py` | оркестрирует прогон: агент → трейс → оценки → отчёт |
| `agent.py` | агент поддержки (цикл tool-use у LLM + офлайн-базлайн) |
| `tools.py` / `mcp_client.py` | `MCPClear` + `getInstruction` (фикстуры или live-MCP) |
| `checks.py` | детерминированные проверки |
| `judges/` | судьи groundedness + resolution (`_llm.py` = общий клиент) |
| `oai.py` | OpenAI-совместимый чат-клиент (HTTP из stdlib; для ролей `provider = "openai"`) |
| `aggregate.py` | политика + отчёт + сравнение прогонов |
| `calibrate.py` / `dataset.py` | калибровка судей по размеченному эталону |
| `gen_mcp.py` | генератор синтетического MCP-датасета |
| `config.py` / `agents.toml` / `.env` | по-агентная конфигурация эндпоинт / модель / промпт |
| `prompts/` | по одному системному промпту на агента |
| `fixtures/` / `data/` | фикстуры инструментов + датасеты |
| `runs/` | вывод по каждому прогону (трейсы, отчёт, калибровка) — в .gitignore |
