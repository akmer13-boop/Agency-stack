# Agency Stack

Корпоративная платформа ИИ-агентов для туристической компании.

## Текущий статус

### Stage 1

Безопасный FastAPI-фундамент, OpenAI Agents SDK, Bearer-авторизация, логи, correlation ID, тесты и Docker.

### Stage 2

Рабочий Telegram-бот через long polling с allowlist по Telegram user ID.

### Stage 2.1 — пройдено

Локальная приёмка на macOS подтверждена 2026-08-06.

Реализованы и проверены:

- роли: администратор, руководитель, сотрудник, наблюдатель;
- специализированные агенты: ИИ-РОП, аналитик сделок, база знаний, техадминистратор;
- автоматическая маршрутизация запроса;
- SQLite-память отдельно для каждого Telegram-пользователя;
- команды `/help`, `/status`, `/reset`;
- локальный аудит технических событий;
- постоянный Docker volume для SQLite;
- запрет попадания локальной базы и WAL/SHM-файлов в Git.

Подробный результат: [`docs/stage-2.1-status.md`](docs/stage-2.1-status.md).

### Stage 3 — начат

Создана ветка `stage-3-bitrix-readonly` для подключения коробочного Bitrix24 строго в режиме чтения.

Запись в CRM остаётся запрещённой.

## Локальная настройка на macOS

```bash
git switch stage-2-telegram
git pull --ff-only origin stage-2-telegram
./.venv/bin/python -m pip install -e '.[dev]'
nano .env
```

Основные настройки `.env`:

```env
APP_VERSION=0.2.1
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=

# Пользователи без отдельной роли получают роль «Сотрудник»
TELEGRAM_ALLOWED_USER_IDS=123456789

# Ролевые списки одновременно дают доступ к боту
TELEGRAM_ADMIN_USER_IDS=
TELEGRAM_MANAGER_USER_IDS=
TELEGRAM_OBSERVER_USER_IDS=

DATABASE_PATH=data/agency_stack.db
CONVERSATION_HISTORY_LIMIT=12
OPENAI_TRACING_ENABLED=false
ALLOW_CRM_WRITE=false
```

Для назначения владельца бота руководителем:

```env
TELEGRAM_MANAGER_USER_IDS=123456789
```

## Запуск Telegram-бота

```bash
bash scripts/run-telegram.sh
```

Команды:

```text
/start   — главное меню
/help    — помощь
/status  — версия, роль и размер памяти
/reset   — очистить память текущего пользователя
/id      — показать Telegram ID
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

API:

```bash
docker compose up --build
```

API и Telegram worker:

```bash
docker compose --profile telegram up --build
```

## План поставки

- Stage 0 — архитектура и ограничения безопасности.
- Stage 1 — API-фундамент и первый агент.
- Stage 2 — Telegram и авторизация.
- Stage 2.1 — роли, память и специализированные агенты — **пройдено**.
- Stage 3 — Bitrix24 только чтение — **начат**.
- Stage 4 — полноценный ИИ-РОП и аналитика продаж.

## Безопасность

Секреты, реальные Telegram ID, клиентские данные, `.env` и локальная SQLite-база не коммитятся в Git. Все чувствительные значения задаются только через переменные окружения или секрет-хранилище.