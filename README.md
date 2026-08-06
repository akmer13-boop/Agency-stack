# Agency Stack

Корпоративная платформа ИИ-агентов для туристической компании.

## Цель

Единый backend на Python/FastAPI с OpenAI Agents SDK, Telegram как пользовательским интерфейсом и контролируемой интеграцией с коробочным Bitrix24.

## Текущий статус

Stage 1: безопасный технический фундамент.

Stage 2: локальный Telegram-канал в режиме long polling:

- `/start` и `/id`;
- закрытый allowlist по неизменяемому Telegram user ID;
- безопасный запрет доступа при пустом allowlist;
- текстовый запрос из Telegram в оркестратор;
- возврат ответа частями до лимита Telegram;
- главное меню с демонстрационными сценариями;
- in-memory rate limit на пользователя;
- безопасная обработка ошибок OpenAI;
- отдельные тесты доступа, handlers и форматирования сообщений.

Подтверждён рабочий сквозной сценарий:

```text
Telegram
  → проверка Telegram user ID
  → Agency Stack handler
  → OpenAI Agents SDK
  → Agency Stack Orchestrator
  → ответ обратно в Telegram
```

Подробный статус: [`docs/stage-2-status.md`](docs/stage-2-status.md).

## Локальная настройка на macOS

```bash
git switch stage-2-telegram
git pull --ff-only origin stage-2-telegram
bash scripts/setup-local.sh
./.venv/bin/python -m pip install -e '.[dev]'
nano .env
```

Обязательные настройки `.env`:

```env
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=123456789
TELEGRAM_REQUEST_COOLDOWN_SECONDS=2
OPENAI_TRACING_ENABLED=false
ALLOW_CRM_WRITE=false
```

Несколько Telegram ID задаются через запятую:

```env
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
```

## Запуск API

```bash
./.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

## Запуск Telegram-бота

Во втором Terminal:

```bash
bash scripts/run-telegram.sh
```

Либо напрямую:

```bash
./.venv/bin/python -m app.telegram.bot
```

## Проверки

```bash
./.venv/bin/ruff check .
./.venv/bin/pytest -q
```

## Docker

Только API:

```bash
docker compose up --build
```

API и Telegram:

```bash
docker compose --profile telegram up --build
```

После запуска API:

- health-check: `http://127.0.0.1:8000/health`;
- Swagger: `http://127.0.0.1:8000/docs`.

## План поставки

- Stage 0 — архитектура, роли, безопасность и ограничения данных.
- Stage 1 — технический каркас API и первый агент.
- Stage 2 — Telegram-бот и авторизация сотрудников.
- Stage 3 — Bitrix24 в режиме только чтения.
- Stage 4 — ИИ-РОП и проверяемые аналитические ответы.
- Stage 5+ — база знаний, подтверждаемые действия, QA звонков и автоматические отчёты.

## Правило безопасности

Секреты, токены, персональные данные и реальные клиентские выгрузки не коммитятся в Git. Все чувствительные значения задаются только через переменные окружения или секрет-хранилище. `.env` исключён из Git и Docker-контекста.
