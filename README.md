# Agency Stack

Корпоративная платформа ИИ-агентов для туристической компании.

## Текущий статус

Рабочая ветка `stage-3.3b-full-read-sync` развивает read-only интеграцию Bitrix24 и MVP ИИ-РОПа.

Текущая версия: **0.4.12**.

Ключевые возможности текущего MVP:

- локальный full/incremental read-only sync Bitrix24 в SQLite;
- аналитика воронок, stage-specific SLA, cycle time, manager scorecards и focus-list;
- локальный справочник сотрудников/отделов без сохранения email/телефонов;
- `/rop_daily` — ежедневная управленческая сводка;
- `/rop_deal <ID>` — детальный разбор конкретной сделки;
- `/rop_deal_activity <ID> <days>` — точный rolling-срез активностей за 1–365 дней;
- `/rop_leads <days>` — Lead Intelligence по лидам за rolling-окно 1–365 дней;
- evidence-based диагностика активности после входа на текущую стадию;
- activity-aware risk model: отдельно stage risk, история коммуникаций и next action;
- deal vitality: консервативная проверка актуальности активного pipeline;
- Lead Intelligence: новые лиды, текущие статусы, финализации S/F, aging, источники,
  CRM-активности и менеджеры;
- точечное read-only чтение timeline-комментариев для `/rop_deal` без сохранения их в SQLite;
- AI-tool получает агрегаты и сигналы риска без сырых текстов писем, комментариев и контактов клиента.

`ALLOW_CRM_WRITE=false` остаётся обязательным режимом MVP: запись в Bitrix24 не выполняется.

Коммуникационная пауза пока не имеет отдельного бизнес-SLA. Количество дней с последней коммуникации показывается как факт и не окрашивается в критичность, пока отдельный норматив не утверждён.

Deal vitality не является вероятностью продажи. Если актуальность карточки требует подтверждения, её `OPPORTUNITY` остаётся фактом CRM, но помечается как неподтверждённый pipeline для управленческой трактовки до ручной проверки.

Lead Intelligence не считает `new_deals / new_leads` конверсией lead→deal. Успешные и неуспешные финализации лидов считаются по `lead_stage_history` и semantic S/F. First-response SLA по лидам пока не измеряется.

## Пройденные этапы

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
- чтение воронок, стадий и сделок;
- локальная сводка без передачи сырых карточек в OpenAI;
- жёсткая блокировка методов записи.

### Stage 3.2 — пройдено

- локальный поиск зависших сделок;
- локальный поиск сделок и лидов без ответственного;
- базовая аналитика CRM.

### Stage 3.3B/3.3C/3.3D — текущая рабочая ветка

- полный и incremental sync Bitrix24 в SQLite;
- Analytics Core для ИИ-РОПа;
- stage-specific SLA и focus-list;
- manager ranking и Daily Brief;
- локальный справочник сотрудников и подразделений;
- Deal Drilldown;
- evidence-based диагностика сделки;
- activity-aware risk model 0.4.10;
- recent activity + deal vitality 0.4.11;
- Lead Intelligence 0.4.12.

Подробности ранних этапов:

- [`docs/stage-2.1-status.md`](docs/stage-2.1-status.md)
- [`docs/stage-3.1-status.md`](docs/stage-3.1-status.md)
- [`docs/stage-3.2-status.md`](docs/stage-3.2-status.md)

## Локальная настройка на macOS

```bash
git switch stage-3.3b-full-read-sync
git pull --ff-only origin stage-3.3b-full-read-sync
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

Основные настройки `.env`:

```env
APP_VERSION=0.4.12
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_MANAGER_USER_IDS=

DATABASE_PATH=data/agency_stack.db
CONVERSATION_HISTORY_LIMIT=12

BITRIX24_WEBHOOK_URL=
BITRIX24_TIMEOUT_SECONDS=15
BITRIX24_VERIFY_SSL=true
BITRIX24_MAX_PAGES=20
BITRIX24_SYNC_MAX_PAGES=20000
BITRIX24_SYNC_MAX_ITEMS_PER_ENTITY=0
BITRIX24_SYNC_OVERLAP_MINUTES=5

ROP_TIMEZONE=Europe/Moscow
ROP_FOCUS_LIMIT=20
ROP_MANAGER_MIN_CLOSED_SAMPLE=5

BITRIX24_DEMO_MODE=false
OPENAI_TRACING_ENABLED=false
ALLOW_CRM_WRITE=false
```

Секреты и реальные ID в репозиторий не коммитятся.

## Запуск Telegram-бота

```bash
python -m app.telegram.bot
```

Основные команды платформы:

```text
/start
/help
/status
/reset
/id
```

Синхронизация Bitrix24:

```text
/bitrix_sync
/bitrix_sync_incremental
/bitrix_sync_status
/bitrix_directory_sync
```

Основные команды ИИ-РОПа:

```text
/rop_today
/rop_week
/rop_month
/rop_pipeline
/rop_funnel
/rop_risks
/rop_losses
/rop_stage_aging
/rop_managers
/rop_sla
/rop_cycle_time
/rop_focus
/rop_daily
/rop_deal 7040
/rop_deal_activity 7040 7
/rop_leads 7
```

## Activity-aware risk model

Начиная с 0.4.10, риск конкретной сделки не сводится к одному общему статусу. Для `/rop_deal <ID>` оцениваются три независимых сигнала:

1. **Stage risk** — критичность только по утверждённому stage-specific SLA.
2. **Communication evidence** — факт наличия завершённых коммуникаций после входа на текущую стадию и давность последней коммуникации.
3. **Next action** — наличие или отсутствие незавершённого следующего действия в CRM.

Давность последней коммуникации сама по себе не считается SLA, пока бизнес отдельно не утвердил норматив допустимой паузы между контактами.

## Recent activity и Deal vitality

Начиная с 0.4.11, ИИ-РОП умеет считать точные rolling-срезы по одной сделке за произвольные 1–365 дней. Например:

```text
/rop_deal_activity 7040 7
/rop_deal_activity 7040 14
/rop_deal_activity 7040 30
```

AI-tool `get_rop_deal_activity(deal_id, days)` позволяет задавать те же вопросы обычным языком: «что было по сделке за последнюю неделю?» или «сколько активностей было за последние 30 дней?». Неизвестные типы активности учитываются в общем количестве, но не считаются коммуникациями без явной классификации.

Deal vitality использует уже подтверждённые сигналы stage risk, communication evidence и next action. Он может пометить карточку как имеющую признаки текущего ведения, требующую подтверждения актуальности, кандидата на проверку закрытия или как недостаточно определённую. Модель не закрывает сделки автоматически и не считает наличие писем доказательством текущего намерения клиента купить.

## Lead Intelligence

Начиная с 0.4.12, `/rop_leads <days>` и AI-tool `get_rop_leads(days)` дают отдельную аналитику именно по лидам, не смешивая её с метриками сделок.

Отчёт включает:

- новые лиды за rolling-окно;
- текущие активные, успешные и неуспешные финальные статусы;
- события `lead_stage_history` за окно;
- успешные и неуспешные финализации S/F;
- долю успешных среди финализированных переходов, без выдачи её за lead→deal conversion;
- общий aging активных лидов 3+/5+;
- источники новых лидов;
- CRM-активности, привязанные к лидам, и отдельно подтверждённые коммуникации;
- текущую операционную картину по менеджерам с локальными ФИО и подразделениями.

Названия статусов и источников читаются точечно через read-only `crm.status.list`. Если справочник временно недоступен, отчёт продолжает работать и показывает CRM ID. Тексты лидов, телефоны и e-mail клиентов в AI-tool не передаются.

## Запуск API

```bash
./.venv/bin/python -m uvicorn app.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

## Проверки

```bash
python -m ruff check .
python -m pytest -q
```

## Docker

```bash
docker compose --profile telegram up --build
```

## План поставки

- Stage 0 — архитектура и ограничения безопасности.
- Stage 1 — API-фундамент и первый агент — **пройдено**.
- Stage 2 — Telegram и авторизация — **пройдено**.
- Stage 2.1 — роли, память и специализированные агенты — **пройдено**.
- Stage 3.0 — read-only подключение Bitrix24 — **пройдено**.
- Stage 3.1 — воронки, стадии и сделки — **пройдено**.
- Stage 3.2 — локальный контроль CRM — **пройдено**.
- Stage 3.3 — безопасный аналитический контекст для ИИ-РОПа — **в работе, MVP уже функционирует**.
- Stage 4 — расширение полноценного ИИ-РОПа и аналитики продаж.

## Безопасность

Секреты, реальные Telegram ID, `.env` и локальная SQLite-база не коммитятся в Git. Методы создания, обновления и удаления данных Bitrix24 не входят в разрешённый read-only контур MVP. `ALLOW_CRM_WRITE=false` остаётся обязательным.

Сырые тексты timeline-комментариев могут отображаться в прямом локальном `/rop_deal`, но не передаются в AI-tool. В LLM уходят только необходимые агрегированные факты и сигналы риска.
