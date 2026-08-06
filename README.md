# Agency Stack

Корпоративная платформа ИИ-агентов для туристической компании.

## Цель

Единый backend на Python/FastAPI с OpenAI Agents SDK, Telegram как пользовательским интерфейсом и контролируемой интеграцией с коробочным Bitrix24.

## Текущий статус

Stage 1 реализует безопасный технический фундамент:

- FastAPI backend;
- OpenAI Agents SDK;
- первый агент-оркестратор;
- Bearer-авторизация внутреннего API;
- запрет записи в CRM по умолчанию;
- correlation ID для каждого запроса;
- структурированные JSON-логи;
- безопасная обработка ошибок OpenAI;
- автотесты, Ruff и GitHub Actions;
- Dockerfile, Docker Compose и health-check контейнера.

## Локальный запуск на macOS

```bash
git switch stage-1-foundation
git pull origin stage-1-foundation
chmod +x scripts/setup-local.sh scripts/test-agent.sh
./scripts/setup-local.sh
nano .env
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

В другом Terminal:

```bash
./scripts/test-agent.sh
```

## Локальный запуск на Windows

```powershell
git switch stage-1-foundation
git pull origin stage-1-foundation
powershell -ExecutionPolicy Bypass -File .\scripts\setup-local.ps1
notepad .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

## Проверки

```bash
ruff check .
pytest -q
```

## Docker

Сборка и запуск одной командой:

```bash
docker compose up --build
```

Либо вручную:

```bash
docker build -t agency-stack:local .
docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  agency-stack:local
```

После запуска:

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
