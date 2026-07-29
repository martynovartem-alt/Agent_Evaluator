=====================================================================
AGENT EVALUATOR — ПОШАГОВАЯ ИНСТРУКЦИЯ (RU)          [English below]
=====================================================================

Что это: пайплайн оценки агента поддержки по истории операций.
Считает % решённых кейсов и калибрует LLM-судью по разметке асессоров
(Да/Частично/Нет). Работает ТОЛЬКО с моделями банковского Sandbox API
и ТОЛЬКО из сети банка (VPN/VDI).

ШАГ 0. Получить доступ к Sandbox (один раз)
   - Письмо на EYanovskaya@alfabank.ru, тема строго:
     «Получение доступа к Sandbox (ФИО)», приложить анкету
     (см. "4. Sandbox API.pdf" в корне проекта).
   - В ответ придёт ключ UUID. Срок действия — 3 месяца,
     продление тем же письмом.

ШАГ 1. Подключиться к VPN/VDI банка
   Без сети банка Sandbox недоступен (запросы упадут по таймауту).

ШАГ 2. Установить и настроить проект
   pip install -r requirements.txt
   cp .env.example .env
   В .env вписать:  SANDBOX_API_KEY=<ваш UUID-ключ>

ШАГ 3. Быстрая проверка схемы (~1 минута, 10 диалогов)
   python3 eval_fast.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"
   - Печатает вердикты, причины ошибок и ETA полного прогона.
   - «SCHEME OK» (exit 0)  -> можно запускать полный прогон.
   - «SCHEME FAILED» (exit 1) -> чинить по подсказкам (ключ/VPN/конфиг),
     полный прогон НЕ запускать.

ШАГ 4. Полный прогон оценки
   python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx" --classify-scope
   - Сначала сам повторит проверку схемы, при ошибке остановится.
   - Лимит Sandbox 0.2 RPS (1 запрос в 5 с) -> полный прогон идёт часы,
     ETA показывается заранее. --classify-scope при первом запуске
     классифицирует диалоги без интента (результат кэшируется,
     повторные прогоны бесплатны).
   - .xlsx подхватывается напрямую (конвертация во временный файл
     вне репозитория — реальные данные не попадут в git).

ШАГ 5. Смотреть результаты
   - Консоль: agreement, каппа, precision/recall по классам,
     бинарная метрика (correct = только «Да», «Частично» = incorrect),
     причины ошибок (no_answer, wrong_operation, ...), разрезы
     by_scope и by_intent.
   - Файлы: runs/<дата-время>/calibration.json
            runs/<дата-время>/disagreements.csv  (открывается в Excel;
            рассуждения судьи рядом с комментарием асессора)

ШАГ 6 (опционально). Прогон агента на золотом наборе
   python3 runner.py --dataset data/golden_mini.jsonl
   Отчёт: runs/<дата-время>/report.json + diff с прошлым прогоном.

СМЕНА МОДЕЛИ
   Править секцию роли в agents.toml. Разрешены ТОЛЬКО модели из
   списка Sandbox ("Alfa_LLM_endpoints.png" / "4. Sandbox API.pdf"):
     Qwen/Qwen3.6-35B-A3B-FP8 (судьи), Qwen/QwQ-32B,
     gpt-oss-120b, /models/llama-3.3, Qwen/Qwen3-Coder-Next
   ВАЖНО: [agent] вызывает инструменты (tools), а tools поддерживают
   только QwQ-32B, /models/llama-3.3 и gpt-oss-120b.

ЕСЛИ ЧТО-ТО НЕ ТАК
   - Ошибка 406 ............ не передан systemid (проверьте agents.toml)
   - Ошибка 400 messageId .. повторный messageid (не должно случаться —
                             клиент генерирует уникальный сам)
   - Таймауты .............. вы не в VPN/VDI
   - «stub (no LLM)» ....... нет ключа в .env или *_MODE=offline
   - Ошибка 429 ............ превышен лимит 0.2 RPS (проверьте rps=0.2
                             в agents.toml)
   Проверка без ключа и сети (заглушки, для разработки):
   AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline \
     python3 runner.py --dataset data/golden_mini.jsonl

Подробнее: GUIDE.ru.md (полный гайд), ARCHITECTURE.md (устройство),
README.md (обзор).

=====================================================================
AGENT EVALUATOR — STEP-BY-STEP INSTRUCTIONS (EN)
=====================================================================

What it is: an evaluation pipeline for the transaction-history support
agent. It measures % of solved cases and calibrates the LLM judge
against assessor labels (Да/Частично/Нет). Works ONLY with the bank's
Sandbox API models and ONLY from the bank network (VPN/VDI).

STEP 0. Get Sandbox access (once)
   - Email EYanovskaya@alfabank.ru, subject exactly:
     «Получение доступа к Sandbox (Full Name)», attach the request
     form (see "4. Sandbox API.pdf" in the project root).
   - You receive a UUID key by email. Valid for 3 months; renew by
     the same email process.

STEP 1. Connect to the bank VPN/VDI
   Without the bank network the Sandbox is unreachable (requests
   time out).

STEP 2. Install and configure
   pip install -r requirements.txt
   cp .env.example .env
   In .env set:  SANDBOX_API_KEY=<your UUID key>

STEP 3. Fast scheme check (~1 minute, 10 dialogues)
   python3 eval_fast.py --dataset "Agents-new-answers(after_20_07_2026).xlsx"
   - Prints verdicts, failure reasons and the full-run ETA.
   - "SCHEME OK" (exit 0)  -> safe to launch the full run.
   - "SCHEME FAILED" (exit 1) -> fix what it points at (key/VPN/config);
     do NOT start the full run.

STEP 4. Full evaluation run
   python3 eval_full.py --dataset "Agents-new-answers(after_20_07_2026).xlsx" --classify-scope
   - Re-runs the scheme check first and aborts on failure.
   - The Sandbox limit is 0.2 RPS (1 request per 5 s) -> a full run
     takes hours; the ETA is shown upfront. --classify-scope
     classifies dialogues without a backend intent on the first run
     (results are cached; reruns are free).
   - An .xlsx is accepted directly (converted to a temp file outside
     the repo — real data never lands in git).

STEP 5. Read the results
   - Console: agreement, kappa, per-class precision/recall, the
     binary metric (correct = «Да» only; «Частично» counts as
     incorrect), failure reasons (no_answer, wrong_operation, ...),
     by_scope and by_intent breakdowns.
   - Files: runs/<timestamp>/calibration.json
            runs/<timestamp>/disagreements.csv  (opens in Excel;
            judge reasoning next to the assessor's comment)

STEP 6 (optional). Agent run on the golden set
   python3 runner.py --dataset data/golden_mini.jsonl
   Report: runs/<timestamp>/report.json + diff vs the previous run.

CHANGING THE MODEL
   Edit the role's section in agents.toml. ONLY models from the
   Sandbox list are allowed ("Alfa_LLM_endpoints.png" /
   "4. Sandbox API.pdf"):
     Qwen/Qwen3.6-35B-A3B-FP8 (judges), Qwen/QwQ-32B,
     gpt-oss-120b, /models/llama-3.3, Qwen/Qwen3-Coder-Next
   IMPORTANT: [agent] calls tools, and tools are supported only by
   QwQ-32B, /models/llama-3.3 and gpt-oss-120b.

TROUBLESHOOTING
   - Error 406 ............. systemid header missing (check agents.toml)
   - Error 400 messageId ... repeated messageid (should not happen —
                             the client generates a unique one)
   - Timeouts .............. you are not on VPN/VDI
   - "stub (no LLM)" ....... no key in .env, or *_MODE=offline
   - Error 429 ............. 0.2 RPS limit exceeded (check rps=0.2
                             in agents.toml)
   Keyless/offline check (stubs, for development):
   AGENT_MODE=offline GROUNDEDNESS_MODE=offline RESOLUTION_MODE=offline \
     python3 runner.py --dataset data/golden_mini.jsonl

More: GUIDE.md (full guide), ARCHITECTURE.md (internals),
README.md (overview).
