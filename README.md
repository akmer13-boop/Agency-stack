# Agency Stack

Корпоративная платформа ИИ-агентов для туристической компании.

## Текущий статус

### Stage 1

Безопасный FastAPI-фундамент, OpenAI Agents SDK, Bearer-авторизация, логи, correlation ID, тесты и Docker.

### Stage 2 и 2.1 — пройдено

- Telegram-бот через long polling;
- allowlist и роли пользователей;
- специализированные агенты;
- SQLite-память по Telegram user ID;
- команды `/help`, `/status`, `/reset`.

### Stage 3.0 и 3.1 — пройдено

- безопасный read-only клиент коробочного Bitrix24;
- подтверждённое соединение через входящий вебхук;
- чтение воронок, стадий и тестовых сделок;
- локальная сводка без передачи карточек в OpenAI;
- жёсткая блокировка любых методов записи.

### Stage 3.2 — реализовано

- тестовые лиды только для игрушечного Bitrix24;
- имена ответственных сотрудников;
- локальный поиск зависших сделок;
- локальный поиск сделок и лидов без ответственного;
- версия проекта `0.3.2`.

Подробности:

- [`docs/stage-2.1-status.md`](docs/stage-2.1-status.md)
- [`docs/stage-3.1-status.md`](docs/stage-3.1-status.md)
- [`docs/stage-3.2-status.md`](docs/stage-3.2-status.md)

## Локальная настройка на macOS

```bash
git switch stage-3-bitrix-readonly
git pull --ff-only origin stage-3-bitrix-readonly
./.venv/bin/python -m pip install -e '.[dev]'
nano .env
```

Основные настройки `.env`:

```env
APP_VERSION=0.3.2
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_MANAGER_USER_IDS=123456789

DATABASE_PATH=data/agency_stack.db
CONVERSATION_HISTORY_LIMIT=12

BITRIX24_WEBHOOK_URL=
BITRIX24_TIMEOUT_SECONDS=15
BITRIX24_VERIFY_SSL=true
BITRIX24_MAX_PAGES=20
BITRIX24_DEAL_PREVIEW_LIMIT=20
BITRIX24_SUMMARY_LIMIT=500

# Только для игрушечного Bitrix24 с вымышленными людьми
BITRIX24_DEMO_MODE=true
BITRIX24_ALLOW_LEADS=true
BITRIX24_LEAD_PREVIEW_LIMIT=20
BITRIX24_STALE_DAYS=3
BITRIX24_STALE_LIMIT=100

OPENAI_TRACING_ENABLED=false
ALLOW_CRM_WRITE=false
```

## Запуск Telegram-бота

```bash
bash scripts/run-telegram.sh
```

Основные команды:

```text
/start
/help
/status
/reset
/id
/bitrix_status
/bitrix_pipelines
/bitrix_stages
/bitrix_deals
/bitrix_summary
/bitrix_leads
/bitrix_stuck
/bitrix_unassigned
```

## Запуск API

```bash
./.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

## Проверки

```bash
./.venv/bin/ruff check .
./.venv/bin/pytest -q
```

## Docker

```bash
docker compose --profile telegram up --build
```

## План поставки

- Stage 0 — архитектура и ограничения безопасности.
- Stage 1 — API-фундамент и первый агент.
- Stage 2 — Telegram и авторизация.
- Stage 2.1 — роли, память и специализированные агенты — **пройдено**.
- Stage 3.0 — read-only подключение Bitrix24 — **пройдено**.
- Stage 3.1 — воронки, стадии и сделки — **пройдено**.
- Stage 3.2 — лиды и локальный контроль зависаний — **реализовано**.
- Stage 3.3 — безопасный аналитический контекст для ИИ-РОПа.
- Stage 4 — полноценный ИИ-РОП и аналитика продаж.

## Безопасность

Секреты, реальные Telegram ID, `.env` и локальная SQLite-база не коммитятся в Git. Методы создания, обновления и удаления данных Bitrix24 не входят в разрешённый список. `ALLOW_CRM_WRITE=false` остаётся обязательным.
